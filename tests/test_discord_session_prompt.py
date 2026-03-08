from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bub.channels.discord import DiscordChannel

ONE_BY_ONE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+kL9sAAAAASUVORK5CYII="
)


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


def _build_message(
    *,
    content: str = "",
    attachments: list[object] | None = None,
    stickers: list[object] | None = None,
) -> SimpleNamespace:
    author = SimpleNamespace(id=42, name="tester", display_name="Test User", bot=False, global_name=None)
    channel = SimpleNamespace(id=123)
    guild = SimpleNamespace(id=999)
    return SimpleNamespace(
        content=content,
        attachments=attachments or [],
        stickers=stickers or [],
        author=author,
        channel=channel,
        guild=guild,
        id=10,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        reference=None,
        mentions=[],
    )


def _attachment(
    *,
    name: str,
    content_type: str,
    url: str = "https://cdn.example/file",
    width: int | None = None,
    height: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        filename=name,
        content_type=content_type,
        size=123,
        url=url,
        width=width,
        height=height,
    )


class _ReadableAttachment(SimpleNamespace):
    async def read(self, *, use_cached: bool = False) -> bytes:
        _ = use_cached
        return self.payload


@pytest.mark.asyncio
async def test_discord_get_session_prompt_keeps_text_and_image_attachment() -> None:
    channel = _build_channel()
    message = _build_message(
        content="请描述这张图",
        attachments=[_attachment(name="test.png", content_type="image/png", width=64, height=32)],
    )

    session_id, inbound = await channel.get_session_prompt(message)  # type: ignore[arg-type]

    assert session_id == "discord:123"
    assert inbound.raw_text == "请描述这张图"
    assert inbound.display_text == "请描述这张图\n[Attachment: test.png]"
    assert inbound.immediate is True
    assert inbound.has_image_attachments is True
    data = json.loads(inbound.model_prompt)
    assert data["message"] == "请描述这张图"
    assert data["media"]["attachments"][0]["filename"] == "test.png"
    assert data["media"]["attachments"][0]["width"] == 64
    assert data["media"]["attachments"][0]["height"] == 32


@pytest.mark.asyncio
async def test_discord_get_session_prompt_supports_image_only_message() -> None:
    channel = _build_channel()
    message = _build_message(
        attachments=[_attachment(name="diagram.png", content_type="image/png", url="https://cdn.example/diagram.png")],
    )

    _session_id, inbound = await channel.get_session_prompt(message)  # type: ignore[arg-type]

    assert inbound.raw_text == ""
    assert inbound.display_text == "[Attachment: diagram.png]"
    assert inbound.immediate is True
    assert inbound.has_image_attachments is True
    data = json.loads(inbound.model_prompt)
    assert data["message"] == ""
    assert data["media"]["attachments"][0]["url"] == "https://cdn.example/diagram.png"


@pytest.mark.asyncio
async def test_discord_get_session_prompt_keeps_non_image_attachment_as_metadata_only() -> None:
    channel = _build_channel()
    message = _build_message(
        content="请看附件",
        attachments=[_attachment(name="report.pdf", content_type="application/pdf")],
    )

    _session_id, inbound = await channel.get_session_prompt(message)  # type: ignore[arg-type]

    assert inbound.raw_text == "请看附件"
    assert inbound.immediate is False
    assert inbound.has_image_attachments is False
    data = json.loads(inbound.model_prompt)
    assert data["media"]["attachments"][0]["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_discord_get_session_prompt_inlines_image_attachment_for_model() -> None:
    channel = _build_channel()
    message = _build_message(
        content="这张图里有什么?",
        attachments=[
            _ReadableAttachment(
                id=1,
                filename="tiny.png",
                content_type="image/png",
                size=len(ONE_BY_ONE_PNG),
                url="https://cdn.example/tiny.png",
                width=1,
                height=1,
                payload=ONE_BY_ONE_PNG,
            )
        ],
    )

    _session_id, inbound = await channel.get_session_prompt(message)  # type: ignore[arg-type]

    assert inbound.has_image_attachments is True
    attachment = inbound.image_attachments[0]
    assert attachment.model_url is not None
    assert attachment.model_url.startswith("data:image/png;base64,")
