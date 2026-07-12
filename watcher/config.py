"""Central configuration for the watcher + understanding layer.

Values are overridable via environment variables so the installer and the Hermes
MCP `env` block can tune them without code changes.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# -- paths --------------------------------------------------------------------
def data_dir() -> Path:
    """Where the store lives. Defaults under Hermes' config dir by OS."""
    explicit = os.environ.get("CONTOUR_DATA_DIR")
    if explicit:
        return Path(os.path.expandvars(explicit)).expanduser()
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "hermes" / "contour"
    return Path.home() / ".hermes" / "contour"


# -- models -------------------------------------------------------------------
INFERENCE_MODE = _env("CONTOUR_INFERENCE_MODE", "local").strip().lower()  # local|hosted
HOSTED_PROVIDER = _env("CONTOUR_HOSTED_PROVIDER", "relay").strip().lower()  # relay|hf|deepinfra|openai_compat
HOSTED_INFERENCE_URL = _env("CONTOUR_HOSTED_INFERENCE_URL", "").strip()
HOSTED_INFERENCE_KEY = _env("CONTOUR_HOSTED_INFERENCE_KEY", "").strip()
DEEPINFRA_API_KEY = _env("DEEPINFRA_API_KEY", "").strip()
HOSTED_VISION_MODEL = _env("CONTOUR_HOSTED_VISION_MODEL", "gemma4")
VISION_MODEL = _env("CONTOUR_VISION_MODEL", "gemma4:e4b")   # doc gemma4:12b as upgrade
EMBED_MODEL = _env("CONTOUR_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = _env_int("CONTOUR_EMBED_DIM", 768)
OLLAMA_HOST = _env("OLLAMA_HOST", "http://127.0.0.1:11434")

# -- capture / trigger tuning -------------------------------------------------
VISUAL_CHECK_INTERVAL = _env_float("CONTOUR_VISUAL_CHECK_INTERVAL", 3.0)   # seconds
VISUAL_CHANGE_THRESHOLD = _env_float("CONTOUR_VISUAL_CHANGE_THRESHOLD", 0.05)
FOREGROUND_POLL_INTERVAL = _env_float("CONTOUR_FOREGROUND_POLL", 0.7)      # seconds
TYPING_PAUSE_SECS = _env_float("CONTOUR_TYPING_PAUSE", 2.0)
IDLE_SECS = _env_float("CONTOUR_IDLE_SECS", 30.0)
HEARTBEAT_SECS = _env_int("CONTOUR_HEARTBEAT_SECS", 30)
DOWNSCALE_FACTOR = _env_int("CONTOUR_DOWNSCALE", 4)

# "thin" accessibility-text heuristic (screenpipe): escalate to vision below these.
THIN_MIN_CHARS = _env_int("CONTOUR_THIN_MIN_CHARS", 100)
THIN_CONTENT_RATIO = _env_float("CONTOUR_THIN_CONTENT_RATIO", 0.3)

# Apps whose UIA text is empty/noisy => always take the vision path.
PREFER_VISION_APPS = {
    a.strip().lower()
    for a in _env(
        "CONTOUR_PREFER_VISION_APPS",
        "windowsterminal,cmd,powershell,wt,alacritty,figma,photoshop,mspaint",
    ).split(",")
    if a.strip()
}

# Trigger classification (hard = always store; soft = dedup-eligible).
HARD_TRIGGERS = {"AppSwitch", "WindowFocus", "Idle", "Manual"}
SOFT_TRIGGERS = {"TypingPause", "KeyPress", "Clipboard", "VisualChange", "ScrollStop"}

# -- proactivity --------------------------------------------------------------
INTERJECTION_COOLDOWN_SECS = _env_int("CONTOUR_INTERJECTION_COOLDOWN", 60)

# How often the in-memory vector index is flushed to disk, so other processes
# (e.g. the Hermes MCP server) can see fresh data without waiting for a clean
# shutdown. 0 disables periodic saving (save on close() only).
INDEX_SAVE_SECS = _env_int("CONTOUR_INDEX_SAVE_SECS", 20)

# -- memory maintenance / concept files ---------------------------------------
CONCEPTS_ENABLED = _env("CONTOUR_CONCEPTS_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CONCEPT_REFRESH_SECS = _env_int("CONTOUR_CONCEPT_REFRESH_SECS", 120)
OPTIMIZE_INTERVAL_SECS = _env_int("CONTOUR_OPTIMIZE_INTERVAL_SECS", 300)
MAX_STORE_MB = _env_int("CONTOUR_MAX_STORE_MB", 1024)
MIN_FREE_MB = _env_int("CONTOUR_MIN_FREE_MB", 1024)

# Additional Gemma-call dampening when vision is needed.
VISION_MIN_INTERVAL_SECS = _env_float("CONTOUR_VISION_MIN_INTERVAL_SECS", 8.0)
VISION_VISUAL_SCORE_MIN = _env_float("CONTOUR_VISION_VISUAL_SCORE_MIN", 0.12)
