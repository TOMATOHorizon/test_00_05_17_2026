from __future__ import annotations

from typing import Protocol

from window_frame_monitor.models import BackendName, CapturedFrame, WindowInfo


class CaptureBackend(Protocol):
    name: BackendName

    def is_available(self) -> tuple[bool, str | None]:
        """Return availability and an optional human-readable unavailable reason."""

    def start(self, window: WindowInfo) -> None:
        """Start capturing the selected window."""

    def get_frame(self) -> CapturedFrame:
        """Return the next captured frame."""

    def stop(self) -> None:
        """Stop capturing and release resources."""
