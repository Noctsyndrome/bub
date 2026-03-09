"""Progress notifications for long-running model calls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

ProgressKind = Literal["started", "soft_timeout_reached", "progress_update", "completed", "failed", "hard_timeout"]


@dataclass(frozen=True)
class ProgressEvent:
    """Status update emitted while one model step is running."""

    kind: ProgressKind
    step: int
    elapsed_seconds: int
    soft_timeout_seconds: int | None
    hard_timeout_seconds: int | None
    message: str | None = None


ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]
