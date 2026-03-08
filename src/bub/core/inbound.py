"""Structured inbound payloads for channel messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MediaAttachment:
    """One inbound media attachment."""

    kind: str
    id: str
    filename: str
    content_type: str | None
    size: int | None
    url: str
    width: int | None = None
    height: int | None = None
    model_url: str | None = None
    model_bytes: int | None = None
    compressed: bool = False

    @property
    def is_image(self) -> bool:
        return bool(self.content_type and self.content_type.startswith("image/"))


@dataclass(frozen=True)
class InboundPayload:
    """Structured input passed from channels into the runtime."""

    raw_text: str
    model_prompt: str
    display_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    media: tuple[MediaAttachment, ...] = ()
    is_command: bool = False
    immediate: bool = False

    @property
    def has_media(self) -> bool:
        return bool(self.media)

    @property
    def image_attachments(self) -> tuple[MediaAttachment, ...]:
        return tuple(item for item in self.media if item.is_image)

    @property
    def has_image_attachments(self) -> bool:
        return bool(self.image_attachments)
