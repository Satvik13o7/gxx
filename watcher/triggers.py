"""Event-driven capture triggers (replaces a fixed-FPS loop).

The engine turns cheap OS signals into ``Trigger`` events that drive capture:
- foreground-window change  -> AppSwitch / WindowFocus  (hard checkpoints)
- input idle transitions    -> TypingPause (soft) / Idle (hard)
- input-less visual change  -> VisualChange (soft)  [catches video / auto-scroll]

Primitives are injected (``fg_key_fn``, ``idle_fn``, ``visual_probe``) so the
transition logic is unit-testable without Windows APIs or a real screen. The
daemon wires them to ``winctx``/``GetLastInputInfo``/``FrameComparer``.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

from . import config

log = logging.getLogger("contour.triggers")


@dataclass
class Trigger:
    kind: str
    ts: float
    meta: dict = field(default_factory=dict)


def get_idle_seconds() -> float:
    """Seconds since last keyboard/mouse input (0.0 if unavailable)."""
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(["ioreg", "-c", "IOHIDSystem"], text=True, timeout=2)
            m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
            if not m:
                return 0.0
            nanos = int(m.group(1))
            return max(0.0, nanos / 1_000_000_000.0)
        except Exception:  # noqa: BLE001
            return 0.0
    try:
        import win32api

        millis = win32api.GetTickCount() - win32api.GetLastInputInfo()
        return max(0.0, millis / 1000.0)
    except Exception:  # noqa: BLE001
        return 0.0


class TriggerEngine:
    def __init__(
        self,
        fg_key_fn,
        idle_fn=get_idle_seconds,
        visual_probe=None,
        clock=time.monotonic,
    ):
        self._fg_key = fg_key_fn        # () -> (app, title)
        self._idle = idle_fn            # () -> float seconds
        self._visual = visual_probe     # () -> float score in [0,1], or None
        self._clock = clock

        self._last_fg: tuple[str, str] | None = None
        self._was_active = True         # were we active on the previous poll?
        self._typing_pause_emitted = False
        self._idle_emitted = False
        self._last_visual_check = -1e9

    def poll(self) -> list[Trigger]:
        now = self._clock()
        out: list[Trigger] = []

        # 1. foreground window / app switch
        fg = self._fg_key()
        if fg != self._last_fg and fg is not None:
            app, title = fg
            if self._last_fg is None or fg[0] != self._last_fg[0]:
                out.append(Trigger("AppSwitch", now, {"app": app, "title": title}))
            else:
                out.append(Trigger("WindowFocus", now, {"app": app, "title": title}))
            self._last_fg = fg

        # 2. idle-state transitions
        idle = self._idle()
        active = idle < config.TYPING_PAUSE_SECS
        if active:
            # returned to activity: reset one-shot flags
            self._was_active = True
            self._typing_pause_emitted = False
            self._idle_emitted = False
        else:
            if self._was_active and not self._typing_pause_emitted:
                out.append(Trigger("TypingPause", now, {"idle": idle}))
                self._typing_pause_emitted = True
            if idle >= config.IDLE_SECS and not self._idle_emitted:
                out.append(Trigger("Idle", now, {"idle": idle}))
                self._idle_emitted = True
            self._was_active = False

        # 3. input-less visual change (only while not actively typing)
        if (
            self._visual is not None
            and not active
            and (now - self._last_visual_check) >= config.VISUAL_CHECK_INTERVAL
        ):
            self._last_visual_check = now
            score = self._visual()
            if score is not None and score >= config.VISUAL_CHANGE_THRESHOLD:
                out.append(Trigger("VisualChange", now, {"score": round(score, 4)}))

        return out
