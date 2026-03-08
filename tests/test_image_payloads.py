from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from bub.channels import image_payloads


class _ReadableAttachment(SimpleNamespace):
    async def read(self, *, use_cached: bool = False) -> bytes:
        _ = use_cached
        return self.payload


def _build_large_jpeg_bytes() -> bytes:
    image = Image.effect_noise((3200, 2400), 120).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=98)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_attachment_to_data_url_compresses_large_images(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_bytes = _build_large_jpeg_bytes()
    monkeypatch.setattr(image_payloads, "MAX_INLINE_IMAGE_BYTES", 80_000)
    monkeypatch.setattr(image_payloads, "MAX_IMAGE_DIMENSION", 1200)
    monkeypatch.setattr(image_payloads, "MIN_IMAGE_DIMENSION", 256)
    attachment = _ReadableAttachment(payload=raw_bytes)

    prepared = await image_payloads.attachment_to_data_url(
        attachment,
        fallback_content_type="image/jpeg",
        fallback_filename="large.jpg",
    )

    assert prepared.compressed is True
    assert prepared.output_bytes < prepared.source_bytes
    assert prepared.output_bytes <= 80_000
    assert prepared.data_url.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_attachment_to_data_url_times_out() -> None:
    class _SlowAttachment:
        async def read(self, *, use_cached: bool = False) -> bytes:
            _ = use_cached
            await asyncio.sleep(0.05)
            return b"late"

    original = image_payloads.ATTACHMENT_READ_TIMEOUT_SECONDS
    image_payloads.ATTACHMENT_READ_TIMEOUT_SECONDS = 0.01
    try:
        with pytest.raises(TimeoutError):
            await image_payloads.attachment_to_data_url(
                _SlowAttachment(),
                fallback_content_type="image/png",
                fallback_filename="slow.png",
            )
    finally:
        image_payloads.ATTACHMENT_READ_TIMEOUT_SECONDS = original
