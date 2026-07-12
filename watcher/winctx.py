"""Active-window context + accessibility text extraction.

The UIA text is our *primary* content source and dedup key; the vision model is
only the fallback when this text is absent or "thin". Windows-only libraries
(``win32gui``/``win32process``, ``uiautomation``) are imported lazily and guarded
so the module still imports (and the pure helpers still test) on any platform.

A short TTL cache avoids re-walking the UIA tree on every rapid trigger
(screenpipe uses ~1s); pass a ``clock`` for deterministic tests.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

from . import config

log = logging.getLogger("contour.winctx")

_WORD = re.compile(r"[A-Za-z0-9]{2,}")
# UIA control types whose text is real content (vs. menu/toolbar chrome).
_CONTENT_CONTROL_TYPES = {
    "TextControl",
    "EditControl",
    "DocumentControl",
    "ListItemControl",
    "DataItemControl",
    "HyperlinkControl",
    "TreeItemControl",
}


@dataclass
class WindowContext:
    app: str = ""
    title: str = ""
    uia_text: str = ""
    content_ratio: float = 0.0
    extras: dict = field(default_factory=dict)


# -- pure heuristics (unit-tested) -------------------------------------------
def content_ratio(text: str) -> float:
    """Proxy for screenpipe's content-role ratio: fraction of chars in word-like
    tokens (>=2 alphanumerics). Chrome (menus, box-drawing, symbols) scores low."""
    if not text:
        return 0.0
    word_chars = sum(len(m.group(0)) for m in _WORD.finditer(text))
    return word_chars / len(text)


def is_thin(text: str, app: str = "", ratio: float | None = None) -> bool:
    """Decide whether to escalate to the vision path instead of trusting UIA text."""
    if app and app.lower() in config.PREFER_VISION_APPS:
        return True
    if len((text or "").strip()) < config.THIN_MIN_CHARS:
        return True
    r = content_ratio(text) if ratio is None else ratio
    return r < config.THIN_CONTENT_RATIO


# -- Windows extraction (guarded) --------------------------------------------
def _mac_foreground_window_info() -> tuple[int, str, str]:
    """Best-effort (pseudo_hwnd, window_title, app_name) on macOS via AppleScript."""
    script = (
        'tell application "System Events"\n'
        'set p to first process whose frontmost is true\n'
        'set appName to name of p\n'
        'set winTitle to ""\n'
        'try\n'
        'set winTitle to name of front window of p\n'
        'end try\n'
        'return appName & "\\t" & winTitle\n'
        'end tell'
    )
    try:
        out = subprocess.check_output(["osascript", "-e", script], text=True, timeout=2).strip()
        if not out:
            return 0, "", ""
        app, _, title = out.partition("\t")
        pseudo = abs(hash((app, title))) % (2**31)
        return pseudo, title.strip(), app.strip()
    except Exception:  # noqa: BLE001
        return 0, "", ""


def _foreground_window_info() -> tuple[int, str, str]:
    """(window_id, window_title, app_name). Returns (0, '', '') if unavailable."""
    if sys.platform == "darwin":
        return _mac_foreground_window_info()
    try:
        import win32gui
        import win32process
    except Exception:  # noqa: BLE001
        return 0, "", ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return 0, "", ""
        title = win32gui.GetWindowText(hwnd) or ""
        app = ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            import win32api
            import win32con

            h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            try:
                path = win32process.GetModuleFileNameEx(h, 0)
                app = path.rsplit("\\", 1)[-1].removesuffix(".exe")
            finally:
                win32api.CloseHandle(h)
        except Exception:  # noqa: BLE001 - process name is best-effort
            pass
        return hwnd, title, app
    except Exception as e:  # noqa: BLE001
        log.debug("foreground window lookup failed: %s", e)
        return 0, "", ""


def _uia_text_for_hwnd(hwnd: int, max_chars: int = 6000) -> str:
    """Concatenate content-bearing UIA node text under a window handle."""
    if not hwnd:
        return ""
    try:
        import uiautomation as auto
    except Exception:  # noqa: BLE001
        return ""
    parts: list[str] = []
    total = 0
    try:
        root = auto.ControlFromHandle(hwnd)
        if root is None:
            return ""
        # bounded breadth-first walk to keep latency in check
        for ctrl, _depth in auto.WalkControl(root, includeTop=True, maxDepth=12):
            ct = ctrl.ControlTypeName
            if ct not in _CONTENT_CONTROL_TYPES:
                continue
            txt = (ctrl.Name or "").strip()
            if not txt:
                try:
                    vp = ctrl.GetValuePattern()
                    txt = (vp.Value or "").strip()
                except Exception:  # noqa: BLE001
                    txt = ""
            if txt:
                parts.append(txt)
                total += len(txt)
                if total >= max_chars:
                    break
    except Exception as e:  # noqa: BLE001
        log.debug("UIA walk failed: %s", e)
    return "\n".join(parts)[:max_chars]


class ContextProvider:
    """Foreground-window context with a short TTL cache."""

    def __init__(self, ttl: float = 1.0, clock=time.monotonic):
        self.ttl = ttl
        self._clock = clock
        self._cached: WindowContext | None = None
        self._cached_at = -1e9
        self._cached_hwnd = 0

    def get(self, force: bool = False) -> WindowContext:
        now = self._clock()
        if not force and self._cached is not None and (now - self._cached_at) < self.ttl:
            return self._cached
        hwnd, title, app = _foreground_window_info()
        text = _uia_text_for_hwnd(hwnd)
        ctx = WindowContext(
            app=app,
            title=title,
            uia_text=text,
            content_ratio=content_ratio(text),
        )
        self._cached, self._cached_at, self._cached_hwnd = ctx, now, hwnd
        return ctx

    def foreground_key(self) -> tuple[str, str]:
        """Cheap (app, title) probe for detecting app/window switches."""
        _, title, app = _foreground_window_info()
        return app, title
