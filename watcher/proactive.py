"""Safety guards for proactive speech.

The decision of *whether* to speak and *what* to say is made upstream: the deciding
tool reads the activity store and hands us its verdict. This module does not fetch,
rank, or judge content. It is the last checkpoint before a sound reaches the user,
and it exists to answer one question: given that someone wants to speak right now,
is it acceptable to?

Two guards, both aimed at the PRD's top-listed risk (a proactive gate that becomes
noisy and annoying):

- *quiet mode* — a global mute the user controls; nothing gets through it.
- *cooldown* — a minimum gap between interjections, so a caller that loops or turns
  chatty cannot talk over the user.

The cooldown has to hold across processes: the MCP server is stateless (it opens the
store per request) and the caller may reach us from a fresh process each time, so an
in-memory timestamp would let every call through. It is persisted next to the store,
in wall-clock time.

``gate()`` is pure given a clock, so it unit-tests without audio or a network.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from . import config

log = logging.getLogger("contour.proactive")

_STATE_FILE = "proactive_state.json"


def _state_path() -> Path:
    return config.data_dir() / _STATE_FILE


def read_last_fire(path: Path | None = None) -> float:
    """Wall-clock time of the last interjection, or 0.0 if we've never spoken."""
    path = path or _state_path()
    try:
        with open(path, encoding="utf-8") as f:
            return float(json.load(f).get("last_fire", 0.0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0.0  # missing or corrupt state => treat as "never fired"


def write_last_fire(ts: float, path: Path | None = None) -> None:
    path = path or _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"last_fire": ts}, f)
    except OSError as e:  # noqa: BLE001 - a lost cooldown must not break the tool
        log.warning("could not persist proactive state: %s", e)


def gate(
    speak: bool,
    text: str,
    now: float,
    last_fire: float,
    cooldown: float = config.INTERJECTION_COOLDOWN_SECS,
    quiet: bool = False,
) -> dict:
    """Vet a caller's decision to speak. Returns {"speak": bool, "text": str, "reason": str}.

    ``reason`` always explains the outcome, so a suppressed line is distinguishable
    from a broken tool — the caller can see it was heard and deliberately held back.
    """
    def no(reason: str) -> dict:
        return {"speak": False, "text": "", "reason": reason}

    if not speak:
        return no("caller decided not to speak")

    text = (text or "").strip()
    if not text:
        # speak=true with no line is a caller bug; say so rather than silently passing.
        return no("caller asked to speak but gave no text")
    if quiet:
        return no("quiet mode is on")

    remaining = cooldown - (now - last_fire)
    if last_fire and remaining > 0:
        return no(f"cooldown active ({int(remaining)}s remaining)")

    return {"speak": True, "text": text, "reason": "speaking"}
