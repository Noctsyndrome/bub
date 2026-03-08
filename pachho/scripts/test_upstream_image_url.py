"""Direct upstream image-understanding probe for the current Bub model config."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from republic import LLM

from bub.config.settings import Settings

DEFAULT_IMAGE_URL = (
    "https://images.unsplash.com/photo-1772955543023-a6e99819e161"
    "?ixlib=rb-4.1.0&q=85&fm=jpg&crop=entropy&cs=srgb"
    "&dl=gadiel-lazcano-Yqtaoythv2g-unsplash.jpg&w=640"
)


def _build_llm(settings: Settings) -> LLM:
    client_args: dict[str, Any] | None = None
    if "azure" in settings.model:
        client_args = {"api_version": "2025-01-01-preview"}
    return LLM(
        settings.model,
        api_key=settings.resolved_api_key,
        api_base=settings.api_base,
        client_args=client_args,
    )


def _build_messages(image_url: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": "You are testing whether the model can understand image input. Respond in Chinese, concisely.",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请直接描述这张图片里有什么，用两三句话回答。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]


async def _run(image_url: str) -> int:
    settings = Settings()
    llm = _build_llm(settings)
    messages = _build_messages(image_url)

    print(f"model={settings.model}")
    print(f"api_base={settings.api_base or '<default>'}")
    print(f"image_url={image_url}")
    print("request=messages+image_url")

    try:
        response = await llm.chat_async(messages=messages, max_tokens=300)
    except Exception as exc:
        print("status=error")
        print(f"error_type={type(exc).__name__}")
        print(f"error={exc}")
        return 1

    print("status=ok")
    if isinstance(response, str):
        print("response_text:")
        print(response)
        return 0

    print("response_raw:")
    print(json.dumps(response, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-url", default=DEFAULT_IMAGE_URL)
    args = parser.parse_args()
    return asyncio.run(_run(args.image_url))


if __name__ == "__main__":
    raise SystemExit(main())
