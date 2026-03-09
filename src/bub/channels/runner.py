import asyncio
from dataclasses import replace
from typing import Any

from loguru import logger

from bub.channels.base import BaseChannel
from bub.core.inbound import InboundPayload


class SessionRunner:
    def __init__(
        self, session_id: str, debounce_seconds: int, message_delay_seconds: int, active_time_window_seconds: int
    ) -> None:
        self.session_id = session_id
        self.debounce_seconds = debounce_seconds
        self.message_delay_seconds = message_delay_seconds
        self.active_time_window_seconds = active_time_window_seconds
        self._prompts: list[str] = []
        self._event = asyncio.Event()
        self._timer: asyncio.TimerHandle | None = None
        self._last_mentioned_at: float | None = None
        self._running_task: asyncio.Task[None] | None = None
        self._loop = asyncio.get_running_loop()

    async def _run(self, channel: BaseChannel) -> None:
        await self._event.wait()
        prompt = channel.format_prompt("\n".join(self._prompts))
        self._prompts.clear()
        self._running_task = None
        try:
            result = await channel.run_prompt(
                self.session_id,
                prompt,
                progress_callback=channel.get_progress_callback(self.session_id),
            )
            await channel.process_output(self.session_id, result)
        except Exception:
            if not channel.debounce_enabled:
                raise
            logger.exception("session.run.error session_id={}", self.session_id)

    def reset_timer(self, timeout: int) -> None:
        self._event.clear()
        if self._timer:
            self._timer.cancel()
        self._timer = self._loop.call_later(timeout, self._event.set)

    async def process_message(self, channel: BaseChannel, message: Any) -> None:
        is_mentioned = channel.is_mentioned(message)
        _, inbound = await channel.get_session_prompt(message)
        now = self._loop.time()
        if self._should_ignore_message(is_mentioned, now):
            self._last_mentioned_at = None
            logger.info("session.receive ignored session_id={} message={}", self.session_id, inbound.display_text)
            return
        if inbound.is_command:
            await self._run_direct(channel, inbound, log_event="session.receive.command")
            return
        if inbound.immediate or not channel.debounce_enabled:
            await self._run_direct(channel, inbound, log_event="session.receive.immediate")
            return

        self._prompts.append(inbound.model_prompt)
        if is_mentioned:
            # Debounce mentioned messages before responding.
            self._last_mentioned_at = now
            logger.info("session.receive.mentioned session_id={} message={}", self.session_id, inbound.display_text)
            self.reset_timer(self.debounce_seconds)
            if self._running_task is None:
                self._running_task = asyncio.create_task(self._run(channel))
            return await self._running_task
        elif self._last_mentioned_at is not None and self._running_task is None:
            # Otherwise if bot is mentioned before, we will keep reading messages for at most 60s.
            logger.info("session.receive followup session_id={} message={}", self.session_id, inbound.display_text)
            self.reset_timer(self.message_delay_seconds)
            self._running_task = asyncio.create_task(self._run(channel))
            return await self._running_task

    def _should_ignore_message(self, is_mentioned: bool, now: float) -> bool:
        return not is_mentioned and (
            self._last_mentioned_at is None or now - self._last_mentioned_at > self.active_time_window_seconds
        )

    async def _run_direct(self, channel: BaseChannel, inbound: InboundPayload, *, log_event: str) -> None:
        logger.info("{} session_id={} message={}", log_event, self.session_id, inbound.display_text)
        try:
            result = await channel.run_prompt(
                self.session_id,
                self._prepare_inbound(channel, inbound),
                progress_callback=channel.get_progress_callback(self.session_id),
            )
            await channel.process_output(self.session_id, result)
        except Exception:
            if not channel.debounce_enabled:
                raise
            logger.exception("session.run.error session_id={}", self.session_id)

    @staticmethod
    def _prepare_inbound(channel: BaseChannel, inbound: InboundPayload) -> InboundPayload:
        if inbound.is_command:
            return inbound
        return replace(inbound, model_prompt=channel.format_prompt(inbound.model_prompt))
