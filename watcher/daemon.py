"""Watcher daemon: turns triggers into stored observations, text-first.

The ordering here is the whole data-minimization story:
1. Get (TTL-cached) window context + UIA text — cheap.
2. Cheap dedup pre-check BEFORE any embed/vision call: on a soft trigger with
   unchanged content (and within the heartbeat floor), skip entirely.
3. Only escalate to the Gemma vision path when UIA text is thin/absent — and even
   then skip re-running vision on the same static window on a soft trigger.
4. Embed + store; run the proactive gate.

``process()`` is pure orchestration over injected components so it unit-tests
without a screen, Ollama, or Windows.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import time
import hashlib
from pathlib import Path

from datastore import ActivityStore, ConceptStore, Observation
from datastore.texthash import content_hash

from . import config
from .capture import ScreenCapturer
from .triggers import TriggerEngine, get_idle_seconds
from .understand import Understanding
from .winctx import ContextProvider, is_thin

log = logging.getLogger("contour.daemon")

_ACTIONABLE = re.compile(
    r"\b(error|exception|failed|failure|traceback|denied|deadline|due|overdue|"
    r"warning|todo|fixme|urgent|blocked)\b",
    re.IGNORECASE,
)


def quick_actionable(text: str) -> bool:
    return bool(_ACTIONABLE.search(text or ""))


def summarize_uia(app: str, title: str, uia_text: str, limit: int = 400) -> tuple[str, str]:
    """Cheap (summary, salient_text) from accessibility text — no model call."""
    where = " ".join(p for p in (app, f"— {title}" if title else "") if p).strip(" —")
    snippet = " ".join((uia_text or "").split())[:limit]
    summary = f"In {where}: {snippet}" if where else snippet
    return summary or where or "(no text)", snippet


class WatcherDaemon:
    def __init__(
        self,
        store: ActivityStore,
        understanding: Understanding,
        ctx_provider: ContextProvider,
        screen: ScreenCapturer | None = None,
        gate=None,
        heartbeat_secs: int = config.HEARTBEAT_SECS,
    ):
        self.store = store
        self.understanding = understanding
        self.ctx = ctx_provider
        self.screen = screen
        self.gate = gate
        self.heartbeat = heartbeat_secs

        self._last_hash: int | None = None
        self._last_ts: float = -1e9
        self._last_vision_key: tuple[str, str, int] | None = None
        self._last_vision_ts: float = -1e9
        self._last_frame_hash: str | None = None
        self._last_save_ts: float = -1e9
        self._last_optimize_ts: float = -1e9
        self.concepts = (
            ConceptStore(self.store.data_dir, refresh_secs=config.CONCEPT_REFRESH_SECS)
            if config.CONCEPTS_ENABLED
            else None
        )
        # counters for the "fraction of triggers that reach vision" metric
        self.stats = {"triggers": 0, "skipped": 0, "uia": 0, "vision": 0}

    def process(self, trigger) -> int | None:
        """Handle one trigger. Returns the stored row id, or None if skipped."""
        self.stats["triggers"] += 1
        now = trigger.ts
        hard = trigger.kind in config.HARD_TRIGGERS
        ctx = self.ctx.get()

        thin = is_thin(ctx.uia_text, ctx.app, ctx.content_ratio)

        if not thin:
            summary, salient = summarize_uia(ctx.app, ctx.title, ctx.uia_text)
            obs = Observation(
                summary=summary,
                app=ctx.app,
                window=ctx.title,
                salient_text=salient,
                transcription=(ctx.uia_text or "")[:20000],
                trigger=trigger.kind,
                source="uia",
                is_actionable=quick_actionable(ctx.uia_text),
                ts=int(now),
            )
            chash = content_hash(obs.hash_text())
            if not hard and self._dup(chash, now):
                self.stats["skipped"] += 1
                log.debug("skip (uia dedup): %s", trigger.kind)
                return None
            self.stats["uia"] += 1
        else:
            # vision fallback: avoid re-running it on the same static window (soft)
            ui_hash = content_hash((ctx.uia_text or "")[:4000])
            key = (ctx.app, ctx.title, ui_hash)
            min_gap = max(self.heartbeat, float(config.VISION_MIN_INTERVAL_SECS))
            if (
                not hard
                and key == self._last_vision_key
                and (now - self._last_vision_ts) < min_gap
            ):
                self.stats["skipped"] += 1
                log.debug("skip (vision cooldown): %s", trigger.kind)
                return None
            if not hard and trigger.kind == "VisualChange":
                score = float((trigger.meta or {}).get("score", 0.0))
                if score < float(config.VISION_VISUAL_SCORE_MIN):
                    self.stats["skipped"] += 1
                    log.debug("skip (vision low-score %.4f): %s", score, trigger.kind)
                    return None
            if self.screen is None:
                log.debug("thin text but no screen capturer; skipping")
                self.stats["skipped"] += 1
                return None
            frame = self.screen.grab_png()
            frame_hash = hashlib.blake2b(frame, digest_size=12).hexdigest()
            last_key = self._last_vision_key
            app_switched = bool(last_key is not None and (ctx.app, ctx.title) != (last_key[0], last_key[1]))
            if (
                app_switched
                and self._last_frame_hash is not None
                and frame_hash == self._last_frame_hash
            ):
                # Likely stale capture (same pixels while foreground app/window changed).
                # Avoid repeating generic wallpaper-like vision summaries.
                fallback = f"In {ctx.app or 'current app'}: active window changed (no fresh visual delta)"
                obs = Observation(
                    summary=fallback,
                    app=ctx.app,
                    window=ctx.title,
                    salient_text="",
                    transcription=(ctx.uia_text or "")[:20000],
                    trigger=trigger.kind,
                    source="uia",
                    is_actionable=quick_actionable(ctx.uia_text),
                    ts=int(now),
                )
                self.stats["uia"] += 1
                self._last_vision_key = key
                self._last_vision_ts = now
                self._last_frame_hash = frame_hash
            else:
                desc = self.understanding.describe(frame, ctx.uia_text)
                summary = desc.get("activity") or ""
                obs = Observation(
                    summary=f"In {ctx.app or desc.get('app_or_context','')}: {summary}".strip(": "),
                    app=ctx.app or desc.get("app_or_context", ""),
                    window=ctx.title,
                    salient_text=desc.get("salient_text", ""),
                    transcription=(ctx.uia_text or desc.get("salient_text", "") or "")[:20000],
                    entities=desc.get("entities", []),
                    trigger=trigger.kind,
                    source="vision",
                    is_actionable=bool(desc.get("is_actionable")),
                    ts=int(now),
                )
                self._last_vision_key = key
                self._last_vision_ts = now
                self._last_frame_hash = frame_hash
                self.stats["vision"] += 1

        emb = self.understanding.embed(obs.hash_text())
        rid = self.store.add(obs, embedding=emb, dedup=not hard, heartbeat_secs=self.heartbeat)
        self._last_hash = content_hash(obs.hash_text())
        self._last_ts = now

        if config.INDEX_SAVE_SECS > 0 and (now - self._last_save_ts) >= config.INDEX_SAVE_SECS:
            try:
                self.store.save()
                self._last_save_ts = now
            except Exception as e:  # noqa: BLE001 - persistence must never crash capture
                log.warning("index save failed: %s", e)

        if self.concepts is not None:
            try:
                self.concepts.update(obs)
            except Exception as e:  # noqa: BLE001
                log.warning("concept update failed: %s", e)

        if config.OPTIMIZE_INTERVAL_SECS > 0 and (now - self._last_optimize_ts) >= config.OPTIMIZE_INTERVAL_SECS:
            try:
                if self._should_optimize():
                    self.store.optimize(now=int(now))
                self._last_optimize_ts = now
            except Exception as e:  # noqa: BLE001
                log.warning("background optimize failed: %s", e)

        if self.gate is not None:
            try:
                self.gate.evaluate(obs, trigger, now=now)
            except Exception as e:  # noqa: BLE001 - proactivity must never crash capture
                log.warning("gate error: %s", e)
        return rid

    def _dup(self, chash: int, now: float) -> bool:
        return (
            self._last_hash is not None
            and chash == self._last_hash
            and (now - self._last_ts) < self.heartbeat
        )

    def _should_optimize(self) -> bool:
        max_bytes = max(1, config.MAX_STORE_MB) * 1024 * 1024
        total = 0
        for root, _, files in os.walk(self.store.data_dir):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
        if total >= max_bytes:
            return True

        try:
            usage = shutil.disk_usage(self.store.data_dir)
            free_mb = usage.free // (1024 * 1024)
            return free_mb <= max(1, config.MIN_FREE_MB)
        except OSError:
            return False

    # -- run loop -------------------------------------------------------------
    def run(self, poll_interval: float = config.FOREGROUND_POLL_INTERVAL) -> None:
        comparer_probe = None
        if self.screen is not None:
            from .diff import FrameComparer

            comparer = FrameComparer(downscale_factor=config.DOWNSCALE_FACTOR)

            def comparer_probe():  # noqa: ANN202
                try:
                    return comparer.compare(self.screen.grab_array())
                except Exception as e:  # noqa: BLE001
                    log.debug("visual probe failed: %s", e)
                    return None

        engine = TriggerEngine(
            fg_key_fn=self.ctx.foreground_key,
            idle_fn=get_idle_seconds,
            visual_probe=comparer_probe,
        )
        log.info("watcher started (backend=%s)", self.store.backend)
        while True:
            try:
                for trig in engine.poll():
                    self.process(trig)
            except Exception as e:  # noqa: BLE001 - never die on a single bad poll
                log.warning("poll error: %s", e)
            time.sleep(poll_interval)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    store = ActivityStore(config.data_dir(), dim=config.EMBED_DIM)
    understanding = Understanding()
    ctx = ContextProvider(ttl=1.0)
    screen = ScreenCapturer(monitor=config.CAPTURE_MONITOR)
    try:
        from .gate import ProactiveGate

        gate = ProactiveGate()
    except Exception:  # noqa: BLE001
        gate = None
    daemon = WatcherDaemon(store, understanding, ctx, screen=screen, gate=gate)
    try:
        daemon.run()
    except KeyboardInterrupt:
        log.info("watcher stopping; stats=%s", daemon.stats)
    finally:
        store.close()
        screen.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
