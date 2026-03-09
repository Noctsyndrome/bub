from __future__ import annotations

import builtins
from types import SimpleNamespace

import pytest

from bub.channels.discord import DiscordChannel
from bub.core.agent_loop import LoopResult
from bub.core.progress import ProgressEvent


class DummySentMessage:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.edits: list[dict[str, object]] = []
        self.deleted = False

    async def edit(self, **kwargs: object) -> None:
        self.edits.append(kwargs)
        self.payload.update(kwargs)

    async def delete(self) -> None:
        self.deleted = True


class DummyMessageable:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.messages: list[DummySentMessage] = []

    async def send(self, **kwargs: object) -> DummySentMessage:
        self.sent.append(kwargs)
        message = DummySentMessage(dict(kwargs))
        self.messages.append(message)
        return message


class DummySourceMessage:
    def to_reference(self, *, fail_if_not_exists: bool = False) -> str:
        return "ref"


def _build_channel() -> DiscordChannel:
    settings = SimpleNamespace(
        discord_token="token",  # noqa: S106
        discord_allow_from=[],
        discord_allow_channels=[],
        discord_command_prefix="!",
        discord_proxy=None,
        proactive_response=False,
    )
    runtime = SimpleNamespace(settings=settings)
    return DiscordChannel(runtime)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_process_output_sends_full_content_and_prints_full_when_not_proactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = _build_channel()
    sink = DummyMessageable()
    printed: list[str] = []

    def _capture_print(*args: object, **kwargs: object) -> None:
        printed.append(" ".join(str(arg) for arg in args))

    async def _resolve_channel(_session_id: str) -> DummyMessageable:
        return sink

    monkeypatch.setattr(builtins, "print", _capture_print)
    channel._bot = object()  # type: ignore[assignment]
    channel._resolve_channel = _resolve_channel  # type: ignore[method-assign]

    output = LoopResult(
        immediate_output="immediate reply",
        assistant_output="assistant details",
        exit_requested=False,
        steps=1,
        error="boom",
    )
    await channel.process_output("discord:1", output)

    joined = "\n".join(printed)
    assert "immediate reply" in joined
    assert "assistant details" in joined
    assert "Error: boom" in joined
    assert sink.sent == [{"content": "immediate reply\n\nassistant details\n\nError: boom"}]


@pytest.mark.asyncio
async def test_process_output_no_immediate_still_sends_assistant_text_when_not_proactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = _build_channel()
    sink = DummyMessageable()
    printed: list[str] = []

    def _capture_print(*args: object, **kwargs: object) -> None:
        printed.append(" ".join(str(arg) for arg in args))

    async def _resolve_channel(_session_id: str) -> DummyMessageable:
        return sink

    monkeypatch.setattr(builtins, "print", _capture_print)
    channel._bot = object()  # type: ignore[assignment]
    channel._resolve_channel = _resolve_channel  # type: ignore[method-assign]

    output = LoopResult(
        immediate_output="",
        assistant_output="assistant only",
        exit_requested=False,
        steps=1,
        error=None,
    )
    await channel.process_output("discord:1", output)

    joined = "\n".join(printed)
    assert "assistant only" in joined
    assert sink.sent == [{"content": "assistant only"}]


@pytest.mark.asyncio
async def test_process_output_sends_only_immediate_when_proactive(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = _build_channel()
    channel.runtime.settings.proactive_response = True
    sink = DummyMessageable()
    printed: list[str] = []

    def _capture_print(*args: object, **kwargs: object) -> None:
        printed.append(" ".join(str(arg) for arg in args))

    async def _resolve_channel(_session_id: str) -> DummyMessageable:
        return sink

    monkeypatch.setattr(builtins, "print", _capture_print)
    channel._bot = object()  # type: ignore[assignment]
    channel._resolve_channel = _resolve_channel  # type: ignore[method-assign]

    output = LoopResult(
        immediate_output="immediate reply",
        assistant_output="assistant details",
        exit_requested=False,
        steps=1,
        error=None,
    )
    await channel.process_output("discord:1", output)

    joined = "\n".join(printed)
    assert "immediate reply" in joined
    assert "assistant details" in joined
    assert sink.sent == [{"content": "immediate reply"}]


@pytest.mark.asyncio
async def test_progress_callback_edits_single_status_message_and_deletes_on_completion() -> None:
    channel = _build_channel()
    sink = DummyMessageable()

    async def _resolve_channel(_session_id: str) -> DummyMessageable:
        return sink

    channel._bot = object()  # type: ignore[assignment]
    channel._resolve_channel = _resolve_channel  # type: ignore[method-assign]
    channel._latest_message_by_session["discord:1"] = DummySourceMessage()  # type: ignore[assignment]
    callback = channel.get_progress_callback("discord:1")
    assert callback is not None

    await callback(ProgressEvent("started", 1, 0, 90, 600))
    await callback(ProgressEvent("soft_timeout_reached", 1, 90, 90, 600))
    await callback(ProgressEvent("progress_update", 1, 120, 90, 600))
    await callback(ProgressEvent("completed", 1, 121, 90, 600))

    assert sink.sent[0]["reference"] == "ref"
    assert sink.sent[0]["mention_author"] is False
    assert "已等待约 90 秒" in str(sink.sent[0]["content"])
    assert sink.messages[0].edits == [{"content": "还在处理中, 正在继续分析.\n已等待约 120 秒, 完成后我会回复最终结果."}]
    assert sink.messages[0].deleted is True


@pytest.mark.asyncio
async def test_process_output_suppresses_duplicate_error_after_progress_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = _build_channel()
    sink = DummyMessageable()
    printed: list[str] = []

    def _capture_print(*args: object, **kwargs: object) -> None:
        printed.append(" ".join(str(arg) for arg in args))

    async def _resolve_channel(_session_id: str) -> DummyMessageable:
        return sink

    monkeypatch.setattr(builtins, "print", _capture_print)
    channel._bot = object()  # type: ignore[assignment]
    channel._resolve_channel = _resolve_channel  # type: ignore[method-assign]
    channel._latest_message_by_session["discord:1"] = DummySourceMessage()  # type: ignore[assignment]
    callback = channel.get_progress_callback("discord:1")
    assert callback is not None

    await callback(ProgressEvent("started", 1, 0, 90, 600))
    await callback(ProgressEvent("soft_timeout_reached", 1, 90, 90, 600))
    await callback(
        ProgressEvent(
            "hard_timeout",
            1,
            600,
            90,
            600,
            message="model_hard_timeout: no response within 600s",
        )
    )

    output = LoopResult(
        immediate_output="",
        assistant_output="",
        exit_requested=False,
        steps=1,
        error="model_hard_timeout: no response within 600s",
    )
    await channel.process_output("discord:1", output)

    assert printed == []
    assert len(sink.sent) == 1
    assert "处理超时" in str(sink.messages[0].payload["content"])
