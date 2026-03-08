"""Helpers for preparing channel image attachments for multimodal model input."""

from __future__ import annotations

import asyncio
import base64
import io
import mimetypes
from dataclasses import dataclass
from typing import Any

from loguru import logger

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:  # pragma: no cover - exercised only when Pillow is missing at runtime.
    Image = None
    ImageOps = None
    UnidentifiedImageError = Exception

MAX_INLINE_IMAGE_BYTES = 1_500_000
MAX_IMAGE_DIMENSION = 1600
MIN_IMAGE_DIMENSION = 768
INITIAL_JPEG_QUALITY = 85
MIN_JPEG_QUALITY = 55
JPEG_QUALITY_STEP = 10
ATTACHMENT_READ_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class PreparedImage:
    data_url: str
    mime_type: str
    source_bytes: int
    output_bytes: int
    compressed: bool


async def attachment_to_data_url(
    attachment: Any,
    *,
    fallback_content_type: str | None,
    fallback_filename: str,
) -> PreparedImage:
    raw_bytes = await _read_attachment_bytes(attachment)
    mime_type = _guess_mime_type(fallback_content_type, fallback_filename)
    payload_bytes, output_mime_type, compressed = _prepare_image_bytes(raw_bytes, mime_type)
    return PreparedImage(
        data_url=_to_data_url(payload_bytes, output_mime_type),
        mime_type=output_mime_type,
        source_bytes=len(raw_bytes),
        output_bytes=len(payload_bytes),
        compressed=compressed,
    )


async def _read_attachment_bytes(attachment: Any) -> bytes:
    async with asyncio.timeout(ATTACHMENT_READ_TIMEOUT_SECONDS):
        try:
            return await attachment.read(use_cached=True)
        except TypeError:
            return await attachment.read()


def _prepare_image_bytes(raw_bytes: bytes, mime_type: str) -> tuple[bytes, str, bool]:
    if len(raw_bytes) <= MAX_INLINE_IMAGE_BYTES:
        return raw_bytes, mime_type, False

    if Image is None:
        logger.warning(
            "image.inline.no_pillow source_bytes={} mime_type={} limit={}",
            len(raw_bytes),
            mime_type,
            MAX_INLINE_IMAGE_BYTES,
        )
        return raw_bytes, mime_type, False

    try:
        with Image.open(io.BytesIO(raw_bytes)) as image:
            working = _normalize_image(image)
            output_bytes, output_mime_type = _compress_image(working)
    except (UnidentifiedImageError, OSError):
        logger.warning("image.inline.decode_failed source_bytes={} mime_type={}", len(raw_bytes), mime_type)
        return raw_bytes, mime_type, False

    if len(output_bytes) >= len(raw_bytes):
        return raw_bytes, mime_type, False
    return output_bytes, output_mime_type, True


def _normalize_image(image: Any) -> Any:
    normalized = image.copy()
    if ImageOps is not None:
        normalized = ImageOps.exif_transpose(normalized)
    if max(normalized.size) <= MAX_IMAGE_DIMENSION:
        return normalized

    width, height = normalized.size
    scale = MAX_IMAGE_DIMENSION / max(width, height)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return normalized.resize(new_size, Image.Resampling.LANCZOS)


def _compress_image(image: Any) -> tuple[bytes, str]:
    working = image
    quality = INITIAL_JPEG_QUALITY
    best_bytes = _save_as_jpeg(working, quality)

    while len(best_bytes) > MAX_INLINE_IMAGE_BYTES and quality > MIN_JPEG_QUALITY:
        quality = max(MIN_JPEG_QUALITY, quality - JPEG_QUALITY_STEP)
        best_bytes = _save_as_jpeg(working, quality)

    while len(best_bytes) > MAX_INLINE_IMAGE_BYTES and min(working.size) > MIN_IMAGE_DIMENSION:
        width, height = working.size
        resized = working.resize((max(1, int(width * 0.85)), max(1, int(height * 0.85))), Image.Resampling.LANCZOS)
        working.close()
        working = resized
        best_bytes = _save_as_jpeg(working, quality)

    if len(best_bytes) > MAX_INLINE_IMAGE_BYTES:
        logger.warning(
            "image.inline.still_large output_bytes={} limit={} size={}",
            len(best_bytes),
            MAX_INLINE_IMAGE_BYTES,
            working.size,
        )
    return best_bytes, "image/jpeg"


def _save_as_jpeg(image: Any, quality: int) -> bytes:
    normalized = image.convert("RGB")
    buffer = io.BytesIO()
    normalized.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _to_data_url(payload: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _guess_mime_type(content_type: str | None, filename: str) -> str:
    if content_type and content_type.startswith("image/"):
        return content_type
    guessed, _ = mimetypes.guess_type(filename)
    if guessed and guessed.startswith("image/"):
        return guessed
    return "image/jpeg"
