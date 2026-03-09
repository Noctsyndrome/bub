"""Discord channel adapter."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, cast

import discord
from discord.ext import commands
from loguru import logger

from bub.app.runtime import AppRuntime
from bub.channels.base import BaseChannel, exclude_none
from bub.channels.image_payloads import attachment_to_data_url
from bub.channels.utils import resolve_proxy
from bub.core.agent_loop import LoopResult
from bub.core.inbound import InboundPayload, MediaAttachment
from bub.core.progress import ProgressCallback, ProgressEvent


def _message_type(message: discord.Message) -> str:
    if message.content:
        return "text"
    if message.attachments:
        return "attachment"
    if message.stickers:
        return "sticker"
    return "unknown"


@dataclass(frozen=True)
class DiscordConfig:
    """Discord adapter config."""

    token: str
    allow_from: set[str]
    allow_channels: set[str]
    command_prefix: str = "!"
    proxy: str | None = None


@dataclass
class _ProgressState:
    status_message: discord.Message | None = None
    suppress_final_error: bool = False


class DiscordChannel(BaseChannel[discord.Message]):
    """Discord adapter based on discord.py."""

    name = "discord"

    def __init__(self, runtime: AppRuntime) -> None:
        super().__init__(runtime)
        settings = runtime.settings
        self._config = DiscordConfig(
            token=settings.discord_token or "",
            allow_from=set(settings.discord_allow_from),
            allow_channels=set(settings.discord_allow_channels),
            command_prefix=settings.discord_command_prefix,
            proxy=settings.discord_proxy,
        )
        self._bot: commands.Bot | None = None
        self._on_receive: Callable[[discord.Message], Awaitable[None]] | None = None
        self._latest_message_by_session: dict[str, discord.Message] = {}
        self._progress_by_session: dict[str, _ProgressState] = {}

    async def start(self, on_receive: Callable[[discord.Message], Awaitable[None]]) -> None:
        if not self._config.token:
            raise RuntimeError("discord token is empty")

        self._on_receive = on_receive
        intents = discord.Intents.default()
        intents.messages = True
        intents.message_content = True

        proxy, _ = resolve_proxy(self._config.proxy)
        bot = commands.Bot(command_prefix=self._config.command_prefix, intents=intents, help_command=None, proxy=proxy)
        self._bot = bot

        @bot.event
        async def on_ready() -> None:
            logger.info("discord.ready user={} id={}", str(bot.user), bot.user.id if bot.user else "<unknown>")

        @bot.event
        async def on_message(message: discord.Message) -> None:
            await bot.process_commands(message)
            await self._on_message(message)

        logger.info(
            "discord.start allow_from_count={} allow_channels_count={} proxy_enabled={}",
            len(self._config.allow_from),
            len(self._config.allow_channels),
            bool(proxy),
        )
        try:
            async with bot:
                await bot.start(self._config.token)
        finally:
            self._bot = None
            logger.info("discord.stopped")

    async def get_session_prompt(self, message: discord.Message) -> tuple[str, InboundPayload]:
        channel_id = str(message.channel.id)
        session_id = f"{self.name}:{channel_id}"
        raw_text, media, extra_metadata = self._parse_message(message)
        media = await self._prepare_media(message, media)

        prefix = f"{self._config.command_prefix}bub "
        if raw_text.startswith(prefix):
            raw_text = raw_text[len(prefix) :]

        if raw_text.strip().startswith(","):
            self._latest_message_by_session[session_id] = message
            return session_id, InboundPayload(
                raw_text=raw_text,
                model_prompt=raw_text,
                display_text=raw_text,
                metadata={"channel_id": channel_id},
                media=media,
                is_command=True,
                immediate=True,
            )

        metadata: dict[str, Any] = {
            "message_id": message.id,
            "type": _message_type(message),
            "username": message.author.name,
            "full_name": getattr(message.author, "display_name", message.author.name),
            "sender_id": str(message.author.id),
            "date": message.created_at.timestamp() if message.created_at else None,
            "channel_id": str(message.channel.id),
            "guild_id": str(message.guild.id) if message.guild else None,
        }

        if extra_metadata:
            metadata.update(extra_metadata)
        if media:
            metadata["media"] = {
                "attachments": [
                    exclude_none(
                        {
                            "id": attachment.id,
                            "filename": attachment.filename,
                            "content_type": attachment.content_type,
                            "size": attachment.size,
                            "url": attachment.url,
                            "width": attachment.width,
                            "height": attachment.height,
                        }
                    )
                    for attachment in media
                ]
            }

        reply_meta = self._extract_reply_metadata(message)
        if reply_meta:
            metadata["reply_to_message"] = reply_meta

        metadata_json = json.dumps(
            {"message": raw_text, "channel_id": channel_id, **exclude_none(metadata)},
            ensure_ascii=False,
        )
        self._latest_message_by_session[session_id] = message
        display_parts = [raw_text] if raw_text else []
        for attachment in media:
            display_parts.append(f"[Attachment: {attachment.filename}]")
        display_text = "\n".join(part for part in display_parts if part).strip() or "[Discord attachment]"
        return session_id, InboundPayload(
            raw_text=raw_text,
            model_prompt=metadata_json,
            display_text=display_text,
            metadata=metadata,
            media=media,
            is_command=False,
            immediate=any(attachment.is_image for attachment in media),
        )

    async def process_output(self, session_id: str, output: LoopResult) -> None:
        progress_state = self._progress_by_session.pop(session_id, _ProgressState())
        parts = [part for part in (output.immediate_output, output.assistant_output) if part]
        if output.error and not progress_state.suppress_final_error:
            parts.append(f"Error: {output.error}")
        content = "\n\n".join(parts).strip()
        if content:
            print(content, flush=True)

        if self.runtime.settings.proactive_response:
            send_parts = [part for part in (output.immediate_output,) if part]
            if output.error and not progress_state.suppress_final_error:
                send_parts.append(f"Error: {output.error}")
            send_content = "\n\n".join(send_parts).strip()
        else:
            send_content = content
        if not send_content:
            return

        channel = await self._resolve_channel(session_id)
        if channel is None:
            logger.warning("discord.outbound unresolved channel session_id={}", session_id)
            return

        source = self._latest_message_by_session.get(session_id)
        reference = source.to_reference(fail_if_not_exists=False) if source is not None else None
        for chunk in self._chunk_message(send_content):
            kwargs: dict[str, Any] = {"content": chunk}
            if reference is not None:
                kwargs["reference"] = reference
                kwargs["mention_author"] = False
            await channel.send(**kwargs)

    async def _on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if self._on_receive is None:
            logger.warning("discord.inbound no handler for received messages")
            return

        content, media, _ = self._parse_message(message)
        logger.info(
            "discord.inbound channel_id={} sender_id={} username={} content={}",
            message.channel.id,
            message.author.id,
            message.author.name,
            content[:100],
        )
        if media:
            logger.info(
                "discord.inbound.media channel_id={} images={} multimodal={} inline_images={} compressed_images={}",
                message.channel.id,
                sum(1 for attachment in media if attachment.is_image),
                any(attachment.is_image for attachment in media),
                sum(1 for attachment in media if attachment.model_url is not None),
                sum(1 for attachment in media if attachment.compressed),
            )

        async with message.channel.typing():
            await self._on_receive(message)

    def get_progress_callback(self, session_id: str) -> ProgressCallback | None:
        async def _callback(event: ProgressEvent) -> None:
            await self._handle_progress_event(session_id, event)

        return _callback

    async def _resolve_channel(self, session_id: str) -> discord.abc.Messageable | None:
        if self._bot is None:
            return None
        channel_id = int(session_id.split(":", 1)[1])
        channel = self._bot.get_channel(channel_id)
        if channel is not None:
            return channel  # type: ignore[return-value]
        with contextlib.suppress(Exception):
            fetched = await self._bot.fetch_channel(channel_id)
            if isinstance(fetched, discord.abc.Messageable):
                return fetched
        return None

    def is_mentioned(self, message: discord.Message) -> bool:
        channel_id = str(message.channel.id)
        if self._config.allow_channels and channel_id not in self._config.allow_channels:
            return False

        has_visible_content = bool(
            message.content.strip() or getattr(message, "attachments", None) or getattr(message, "stickers", None)
        )
        if not has_visible_content:
            return False

        sender_tokens = {str(message.author.id), message.author.name}
        if getattr(message.author, "global_name", None):
            sender_tokens.add(cast(str, message.author.global_name))
        if self._config.allow_from and sender_tokens.isdisjoint(self._config.allow_from):
            logger.warning(
                "discord.inbound.denied channel_id={} sender_id={} reason=allow_from",
                message.channel.id,
                message.author.id,
            )
            return False

        if (
            isinstance(message.channel, discord.DMChannel)
            or self._is_bub_scoped_thread(message)
            or message.content.startswith(f"{self._config.command_prefix}bub")
        ):
            return True

        bot_user = self._bot.user if self._bot is not None else None
        if bot_user is None:
            return False
        if bot_user in message.mentions:
            return True

        ref = message.reference
        if ref is None:
            return False
        resolved = ref.resolved
        return bool(isinstance(resolved, discord.Message) and resolved.author and resolved.author.id == bot_user.id)

    async def _prepare_media(
        self,
        message: discord.Message,
        media: tuple[MediaAttachment, ...],
    ) -> tuple[MediaAttachment, ...]:
        if not media:
            return media

        source_by_id = {str(attachment.id): attachment for attachment in message.attachments}
        prepared: list[MediaAttachment] = []
        for attachment in media:
            if not attachment.is_image:
                prepared.append(attachment)
                continue

            source = source_by_id.get(attachment.id)
            if source is None or not hasattr(source, "read"):
                prepared.append(attachment)
                continue

            logger.info(
                "discord.image.inline_start attachment_id={} filename={} size={} url={}",
                attachment.id,
                attachment.filename,
                attachment.size,
                attachment.url,
            )
            try:
                prepared_image = await attachment_to_data_url(
                    source,
                    fallback_content_type=attachment.content_type,
                    fallback_filename=attachment.filename,
                )
            except Exception as exc:
                logger.warning(
                    "discord.image.inline_failed attachment_id={} filename={} error={}",
                    attachment.id,
                    attachment.filename,
                    exc,
                )
                prepared.append(attachment)
                continue

            logger.info(
                "discord.image.inline_ready attachment_id={} filename={} source_bytes={} output_bytes={} compressed={}",
                attachment.id,
                attachment.filename,
                prepared_image.source_bytes,
                prepared_image.output_bytes,
                prepared_image.compressed,
            )
            prepared.append(
                replace(
                    attachment,
                    model_url=prepared_image.data_url,
                    model_bytes=prepared_image.output_bytes,
                    compressed=prepared_image.compressed,
                )
            )
        return tuple(prepared)

    @staticmethod
    def _is_bub_scoped_thread(message: discord.Message) -> bool:
        channel = message.channel
        thread_name = getattr(channel, "name", None)
        if not isinstance(thread_name, str):
            return False
        is_thread = isinstance(channel, discord.Thread) or getattr(channel, "parent", None) is not None
        return is_thread and thread_name.lower().startswith("bub")

    @staticmethod
    def _parse_message(
        message: discord.Message,
    ) -> tuple[str, tuple[MediaAttachment, ...], dict[str, Any] | None]:
        raw_text = message.content or ""
        attachments = tuple(
            MediaAttachment(
                kind="attachment",
                id=str(att.id),
                filename=att.filename,
                content_type=att.content_type,
                size=att.size,
                url=att.url,
                width=getattr(att, "width", None),
                height=getattr(att, "height", None),
            )
            for att in message.attachments
        )

        metadata: dict[str, Any] = {}
        if message.stickers:
            metadata["stickers"] = [{"id": str(sticker.id), "name": sticker.name} for sticker in message.stickers]

        if raw_text or attachments:
            return raw_text, attachments, metadata or None

        if message.stickers:
            lines = [f"[Sticker: {sticker.name}]" for sticker in message.stickers]
            return "\n".join(lines), (), metadata

        return "[Unknown message type]", (), None

    @staticmethod
    def _extract_reply_metadata(message: discord.Message) -> dict[str, Any] | None:
        ref = message.reference
        if ref is None:
            return None
        resolved = ref.resolved
        if not isinstance(resolved, discord.Message):
            return None
        return exclude_none({
            "message_id": str(resolved.id),
            "from_user_id": str(resolved.author.id),
            "from_username": resolved.author.name,
            "from_is_bot": resolved.author.bot,
            "text": (resolved.content or "")[:100],
        })

    @staticmethod
    def _chunk_message(text: str, *, limit: int = 2000) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break
            split_at = remaining.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip("\n")
        return [chunk for chunk in chunks if chunk]

    async def _handle_progress_event(self, session_id: str, event: ProgressEvent) -> None:
        if event.kind == "started":
            self._progress_by_session[session_id] = _ProgressState()
            return

        state = self._progress_by_session.setdefault(session_id, _ProgressState())
        if event.kind in {"soft_timeout_reached", "progress_update"}:
            content = self._render_progress_message(event)
            await self._upsert_progress_message(session_id, state, content)
            return

        if event.kind == "completed":
            await self._delete_progress_message(state)
            return

        if event.kind in {"failed", "hard_timeout"}:
            content = self._render_failure_message(event)
            state.suppress_final_error = await self._upsert_progress_message(session_id, state, content)

    async def _upsert_progress_message(self, session_id: str, state: _ProgressState, content: str) -> bool:
        channel = await self._resolve_channel(session_id)
        if channel is None:
            logger.warning("discord.progress unresolved channel session_id={}", session_id)
            return False

        if state.status_message is not None:
            await state.status_message.edit(content=content)
            return True

        source = self._latest_message_by_session.get(session_id)
        reference = source.to_reference(fail_if_not_exists=False) if source is not None else None
        kwargs: dict[str, Any] = {"content": content}
        if reference is not None:
            kwargs["reference"] = reference
            kwargs["mention_author"] = False
        sent = await channel.send(**kwargs)
        state.status_message = cast(discord.Message, sent) if sent is not None else None
        return state.status_message is not None

    @staticmethod
    async def _delete_progress_message(state: _ProgressState) -> None:
        if state.status_message is None:
            return
        with contextlib.suppress(Exception):
            await state.status_message.delete()
        state.status_message = None
        state.suppress_final_error = False

    @staticmethod
    def _render_progress_message(event: ProgressEvent) -> str:
        return (
            "还在处理中, 正在继续分析.\n"
            f"已等待约 {event.elapsed_seconds} 秒, 完成后我会回复最终结果."
        )

    @staticmethod
    def _render_failure_message(event: ProgressEvent) -> str:
        if event.kind == "hard_timeout":
            return (
                "处理超时, 任务已停止.\n"
                f"已等待约 {event.elapsed_seconds} 秒, 超过上限 {event.hard_timeout_seconds} 秒."
            )
        return f"处理失败: {event.message or '未知错误'}"
