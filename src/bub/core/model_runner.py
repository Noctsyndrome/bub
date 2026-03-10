"""Model turn runner."""

from __future__ import annotations

import asyncio
import re
import textwrap
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, ClassVar

from loguru import logger
from republic import Tool, ToolAutoResult

from bub.core.progress import ProgressCallback, ProgressEvent
from bub.core.router import AssistantRouteResult, InputRouter
from bub.skills.loader import SkillMetadata
from bub.skills.view import render_compact_skills
from bub.tape.service import TapeService
from bub.tools.progressive import ProgressiveToolView
from bub.tools.view import render_tool_prompt_block

HINT_RE = re.compile(r"\$([A-Za-z0-9_.-]+)")
TOOL_CONTINUE_PROMPT = "Continue the task."


@dataclass(frozen=True)
class ModelTurnResult:
    """Result of one model turn loop."""

    visible_text: str
    exit_requested: bool
    steps: int
    error: str | None = None
    command_followups: int = 0


@dataclass
class _PromptState:
    prompt: str
    multimodal_messages: list[dict[str, Any]] | None = None
    step: int = 0
    followups: int = 0
    visible_parts: list[str] = field(default_factory=list)
    error: str | None = None
    exit_requested: bool = False


class ModelRunner:
    """Runs assistant loop over tape with command-aware follow-up handling."""

    DEFAULT_HEADERS: ClassVar[dict[str, str]] = {"HTTP-Referer": "https://bub.build/", "X-Title": "Bub"}

    def __init__(
        self,
        *,
        tape: TapeService,
        router: InputRouter,
        tool_view: ProgressiveToolView,
        tools: list[Tool],
        list_skills: Callable[[], list[SkillMetadata]],
        model: str,
        max_steps: int,
        max_tokens: int,
        model_timeout_seconds: int | None,
        model_hard_timeout_seconds: int | None = None,
        model_progress_update_seconds: int = 30,
        base_system_prompt: str,
        get_workspace_system_prompt: Callable[[], str],
        proactive_response: bool = False,
    ) -> None:
        self._tape = tape
        self._router = router
        self._tool_view = tool_view
        self._tools = tools
        self._list_skills = list_skills
        self._model = model
        self._max_steps = max_steps
        self._max_tokens = max_tokens
        self._model_timeout_seconds = model_timeout_seconds
        self._model_hard_timeout_seconds = model_hard_timeout_seconds if model_hard_timeout_seconds is not None else model_timeout_seconds
        self._model_progress_update_seconds = model_progress_update_seconds
        self._base_system_prompt = base_system_prompt.strip()
        self._get_workspace_system_prompt = get_workspace_system_prompt
        self._proactive_response = proactive_response
        self._expanded_skills: set[str] = set()

    def reset_context(self) -> None:
        """Clear volatile model-side context caches within one session."""
        self._expanded_skills.clear()

    async def run(
        self,
        prompt: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ModelTurnResult:
        state = _PromptState(prompt=prompt, multimodal_messages=messages)
        self._activate_hints(prompt)
        if messages is not None:
            self._activate_hints(_extract_message_text(messages))

        while state.step < self._max_steps and not state.exit_requested:
            state.step += 1
            logger.info("model.runner.step step={} model={}", state.step, self._model)
            await self._tape.append_event(
                "loop.step.start",
                {
                    "step": state.step,
                    "model": self._model,
                },
            )
            request_messages: list[dict[str, Any]] | None = None
            if state.multimodal_messages is not None:
                request_messages = [*(await self._tape.read_messages()), *state.multimodal_messages]
            response = await self._chat(state.prompt, messages=request_messages, step=state.step, progress_callback=progress_callback)
            if response.error is not None:
                state.error = response.error
                await self._tape.append_event(
                    "loop.step.error",
                    {
                        "step": state.step,
                        "error": response.error,
                    },
                )
                break

            if state.multimodal_messages is not None:
                await self._record_multimodal_turn(state.multimodal_messages, response)
                state.multimodal_messages = None

            if response.followup_prompt:
                await self._tape.append_event(
                    "loop.step.finish",
                    {
                        "step": state.step,
                        "visible_text": False,
                        "followup": True,
                        "exit_requested": False,
                    },
                )
                state.prompt = response.followup_prompt
                state.followups += 1
                continue

            assistant_text = response.text
            if not assistant_text.strip():
                await self._tape.append_event("loop.step.empty", {"step": state.step})
                break

            self._activate_hints(assistant_text)
            route = await self._router.route_assistant(assistant_text)
            await self._consume_route(state, route)
            if not route.next_prompt:
                break
            state.prompt = route.next_prompt
            state.followups += 1

        if state.step >= self._max_steps and not state.error:
            state.error = f"max_steps_reached={self._max_steps}"
            await self._tape.append_event("loop.max_steps", {"max_steps": self._max_steps})

        return ModelTurnResult(
            visible_text="\n\n".join(part for part in state.visible_parts if part).strip(),
            exit_requested=state.exit_requested,
            steps=state.step,
            error=state.error,
            command_followups=state.followups,
        )

    async def _consume_route(self, state: _PromptState, route: AssistantRouteResult) -> None:
        if route.visible_text:
            state.visible_parts.append(route.visible_text)
        if route.exit_requested:
            state.exit_requested = True
        await self._tape.append_event(
            "loop.step.finish",
            {
                "step": state.step,
                "visible_text": bool(route.visible_text),
                "followup": bool(route.next_prompt),
                "exit_requested": route.exit_requested,
            },
        )

    async def _chat(  # noqa: C901
        self,
        prompt: str,
        *,
        messages: list[dict[str, Any]] | None = None,
        step: int,
        progress_callback: ProgressCallback | None = None,
    ) -> _ChatResult:
        system_prompt = self._render_system_prompt()
        provider, _, _ = self._model.partition(":")
        if messages is not None and provider.casefold() != "openai":
            return _ChatResult(
                text="",
                error=(
                    "image_input_unsupported: current model configuration does not support Discord image understanding "
                    "for this provider. Switch to an openai:* multimodal model."
                ),
            )
        start = asyncio.get_running_loop().time()
        logger.info(
            "model.call.start step={} model={} soft_timeout_seconds={} hard_timeout_seconds={}",
            step,
            self._model,
            self._model_timeout_seconds,
            self._model_hard_timeout_seconds,
        )
        await self._emit_progress(
            progress_callback,
            ProgressEvent(
                kind="started",
                step=step,
                elapsed_seconds=0,
                soft_timeout_seconds=self._model_timeout_seconds,
                hard_timeout_seconds=self._model_hard_timeout_seconds,
            ),
        )

        task = asyncio.create_task(self._run_model_call(prompt, system_prompt, provider, messages))
        soft_reported = False
        last_progress_second = 0

        try:
            while True:
                if task.done():
                    result = await task
                    elapsed = max(0, int(asyncio.get_running_loop().time() - start))
                    logger.info("model.call.finish step={} elapsed_seconds={}", step, elapsed)
                    await self._emit_progress(
                        progress_callback,
                        ProgressEvent(
                            kind="completed",
                            step=step,
                            elapsed_seconds=elapsed,
                            soft_timeout_seconds=self._model_timeout_seconds,
                            hard_timeout_seconds=self._model_hard_timeout_seconds,
                        ),
                    )
                    return result

                elapsed = max(0, int(asyncio.get_running_loop().time() - start))
                if self._model_hard_timeout_seconds is not None and elapsed >= self._model_hard_timeout_seconds:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                    logger.warning(
                        "model.call.hard_timeout step={} elapsed_seconds={} hard_timeout_seconds={}",
                        step,
                        elapsed,
                        self._model_hard_timeout_seconds,
                    )
                    await self._emit_progress(
                        progress_callback,
                        ProgressEvent(
                            kind="hard_timeout",
                            step=step,
                            elapsed_seconds=elapsed,
                            soft_timeout_seconds=self._model_timeout_seconds,
                            hard_timeout_seconds=self._model_hard_timeout_seconds,
                            message=f"model_hard_timeout: no response within {self._model_hard_timeout_seconds}s",
                        ),
                    )
                    return _ChatResult(
                        text="",
                        error=f"model_hard_timeout: no response within {self._model_hard_timeout_seconds}s",
                    )

                should_report = False
                event_kind: str | None = None
                if (
                    self._model_timeout_seconds is not None
                    and not soft_reported
                    and elapsed >= self._model_timeout_seconds
                ):
                    soft_reported = True
                    should_report = True
                    event_kind = "soft_timeout_reached"
                elif (
                    soft_reported
                    and elapsed - last_progress_second >= self._model_progress_update_seconds
                ):
                    should_report = True
                    event_kind = "progress_update"

                if should_report and event_kind is not None:
                    last_progress_second = elapsed
                    logger.info("model.call.waiting step={} elapsed_seconds={}", step, elapsed)
                    await self._emit_progress(
                        progress_callback,
                        ProgressEvent(
                            kind=event_kind,  # type: ignore[arg-type]
                            step=step,
                            elapsed_seconds=elapsed,
                            soft_timeout_seconds=self._model_timeout_seconds,
                            hard_timeout_seconds=self._model_hard_timeout_seconds,
                        ),
                    )

                await asyncio.sleep(1)
        except Exception as exc:
            elapsed = max(0, int(asyncio.get_running_loop().time() - start))
            logger.exception("model.call.error step={} elapsed_seconds={}", step, elapsed)
            await self._emit_progress(
                progress_callback,
                ProgressEvent(
                    kind="failed",
                    step=step,
                    elapsed_seconds=elapsed,
                    soft_timeout_seconds=self._model_timeout_seconds,
                    hard_timeout_seconds=self._model_hard_timeout_seconds,
                    message=str(exc),
                ),
            )
            if messages is not None and _looks_like_multimodal_unsupported(str(exc)):
                return _ChatResult(
                    text="",
                    error=(
                        "image_input_unsupported: current model configuration rejected Discord image input. "
                        "Switch to a multimodal openai-compatible model or continue with text-only messages."
                    ),
                )
            return _ChatResult(text="", error=f"model_call_error: {exc!s}")
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def _run_model_call(
        self,
        prompt: str,
        system_prompt: str,
        provider: str,
        messages: list[dict[str, Any]] | None,
    ) -> _ChatResult:
        if provider.casefold() == "vertexai":
            output = await self._tape.run_tools_async(
                prompt=prompt if messages is None else None,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=self._max_tokens,
                tools=self._tools,
                http_options={"headers": self.DEFAULT_HEADERS},
            )
        else:
            output = await self._tape.run_tools_async(
                prompt=prompt if messages is None else None,
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=self._max_tokens,
                tools=self._tools,
                extra_headers=self.DEFAULT_HEADERS,
            )
        return _ChatResult.from_tool_auto(output)

    @staticmethod
    async def _emit_progress(progress_callback: ProgressCallback | None, event: ProgressEvent) -> None:
        if progress_callback is None:
            return
        try:
            await progress_callback(event)
        except Exception:
            logger.exception("model.progress_callback.error kind={} step={}", event.kind, event.step)

    def _render_system_prompt(self) -> str:
        blocks: list[str] = []
        if self._base_system_prompt:
            blocks.append(self._base_system_prompt)
        if workspace_system_prompt := self._get_workspace_system_prompt():
            blocks.append(workspace_system_prompt)
        blocks.append(render_tool_prompt_block(self._tool_view))
        compact_skills = render_compact_skills(self._list_skills(), self._expanded_skills)
        if compact_skills:
            blocks.append(compact_skills)
        blocks.append(_runtime_contract(self._proactive_response))
        return "\n\n".join(block for block in blocks if block.strip())

    def _activate_hints(self, text: str) -> None:
        skill_index = self._build_skill_index()
        for match in HINT_RE.finditer(text):
            hint = match.group(1)
            self._tool_view.note_hint(hint)

            skill = skill_index.get(hint.casefold())
            if skill is None:
                continue
            self._expanded_skills.add(skill.name)

    def _build_skill_index(self) -> dict[str, SkillMetadata]:
        return {skill.name.casefold(): skill for skill in self._list_skills()}

    async def _record_multimodal_turn(self, messages: list[dict[str, Any]], response: _ChatResult) -> None:
        for message in messages:
            await self._tape.append_message(_sanitize_multimodal_message_for_tape(message))
        await self._tape.append_event(
            "model.input.multimodal",
            {
                "provider": self._model.partition(":")[0],
                "images": _count_message_images(messages),
            },
        )
        if response.tool_calls:
            await self._tape.append_tool_call(response.tool_calls)
        if response.tool_results:
            await self._tape.append_tool_result(response.tool_results)
        if response.text:
            await self._tape.append_message({"role": "assistant", "content": response.text})


@dataclass(frozen=True)
class _ChatResult:
    text: str
    error: str | None = None
    followup_prompt: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[Any] = field(default_factory=list)

    @classmethod
    def from_tool_auto(cls, output: ToolAutoResult) -> _ChatResult:
        if output.kind == "text":
            return cls(text=output.text or "")
        if output.kind == "tools":
            return cls(
                text="",
                followup_prompt=TOOL_CONTINUE_PROMPT,
                tool_calls=list(output.tool_calls),
                tool_results=list(output.tool_results),
            )

        if output.tool_calls or output.tool_results:
            return cls(
                text="",
                followup_prompt=TOOL_CONTINUE_PROMPT,
                tool_calls=list(output.tool_calls),
                tool_results=list(output.tool_results),
            )

        if output.error is None:
            return cls(text="", error="tool_auto_error: unknown")
        return cls(text="", error=f"{output.error.kind.value}: {output.error.message}")


def _extract_message_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
    return "\n".join(parts)


def _count_message_images(messages: list[dict[str, Any]]) -> int:
    count = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                count += 1
    return count


def _sanitize_multimodal_message_for_tape(message: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(message)
    content = sanitized.get("content")
    if not isinstance(content, list):
        return sanitized

    sanitized_blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            sanitized_blocks.append(block)
            continue

        if block.get("type") != "image_url":
            sanitized_blocks.append(dict(block))
            continue

        image_url = block.get("image_url")
        if not isinstance(image_url, dict):
            sanitized_blocks.append(dict(block))
            continue

        url = image_url.get("url")
        if isinstance(url, str) and url.startswith("data:"):
            sanitized_blocks.append(
                {
                    "type": "text",
                    "text": "[inline image omitted from tape history]",
                }
            )
            continue

        sanitized_blocks.append(
            {
                "type": "image_url",
                "image_url": dict(image_url),
            }
        )

    sanitized["content"] = sanitized_blocks
    return sanitized


def _looks_like_multimodal_unsupported(text: str) -> bool:
    normalized = text.casefold()
    patterns = (
        "image_url",
        "image input",
        "vision",
        "multimodal",
        "unsupported",
        "invalid image",
        "invalid type",
    )
    return any(pattern in normalized for pattern in patterns)


def _runtime_contract(proactive_response: bool) -> str:
    if not proactive_response:
        response_instruct = """\
        <response_instruct>
        If this turn comes from a channel message, return the exact final reply text as plain natural language.
        Use normal line breaks in the returned text; do not emit escaped '\\n' sequences unless the user literally asks for them.
        Do not call the channel skill just to send a normal reply, because the channel adapter will deliver your final text.
        Only use a channel skill when the user explicitly asks for a channel-side action beyond replying.
        </response_instruct>"""
    else:
        response_instruct = """\
        <response_instruct>
        You MUST send message to the corresponding channel before finish when you want to respond.
        Route your response to the same channel the message came from.
        There is a skill named `{channel}` for each channel that you need to figure out how to send a response to that channel.
        ## Before finishing ANY response to a channel message:
        1. Identify the source channel from the user message metadata
        2. Prepare your response text
        3. Call the corresponding channel skill to deliver the message
        4. ONLY THEN end your turn
        </response_instruct>"""

    return textwrap.dedent(f"""\
        <runtime_contract>
        1. Use tool calls for all actions (file ops, shell, web, tape, skills).
        2. Do not emit comma-prefixed commands in normal flow; use tool calls instead.
        3. If a compatibility fallback is required, runtime can still parse comma commands.
        4. Never emit '<command ...>' blocks yourself; those are runtime-generated.
        5. When enough evidence is collected, return plain natural language answer.
        6. Use '$name' hints to request detail expansion for tools/skills when needed.
        </runtime_contract>
        <context_contract>
        Excessively long context may cause model call failures. In this case, you SHOULD first use tape.handoff tool to shorten the length of the retrieved history.
        </context_contract>
        {response_instruct}""")
