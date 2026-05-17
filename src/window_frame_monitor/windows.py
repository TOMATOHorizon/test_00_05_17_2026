from __future__ import annotations

import sys

import psutil

from window_frame_monitor.models import WindowInfo


def list_windows() -> list[WindowInfo]:
    if sys.platform != "win32":
        return [WindowInfo(hwnd=0, title="Test Pattern", process_name="test-pattern")]

    import win32gui
    import win32process

    windows: list[WindowInfo] = []

    def visit(hwnd: int, _extra: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return
        _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = None
        try:
            process_name = psutil.Process(pid).name()
        except psutil.Error:
            process_name = None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        windows.append(
            WindowInfo(
                hwnd=hwnd,
                title=title,
                pid=pid,
                process_name=process_name,
                x=left,
                y=top,
                width=max(0, right - left),
                height=max(0, bottom - top),
                visible=True,
                minimized=bool(win32gui.IsIconic(hwnd)),
            )
        )

    win32gui.EnumWindows(visit, None)
    return windows


def resolve_window(*, hwnd: int | None = None, title: str | None = None, process_name: str | None = None) -> WindowInfo:
    candidates = list_windows()
    if hwnd is not None:
        matches = [window for window in candidates if window.hwnd == hwnd]
    elif title:
        lowered = title.lower()
        matches = [window for window in candidates if lowered in window.title.lower()]
    elif process_name:
        lowered = process_name.lower()
        matches = [window for window in candidates if window.process_name and lowered in window.process_name.lower()]
    else:
        raise ValueError("Provide hwnd, title, or process_name.")

    if not matches:
        raise ValueError("No matching window found.")
    if len(matches) > 1:
        names = ", ".join(f"{window.hwnd}:{window.title}" for window in matches[:5])
        raise ValueError(f"Multiple matching windows found: {names}")
    return matches[0]
