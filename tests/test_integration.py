"""End-to-end: real watcher daemon -> real store -> real MCP query_datastore.

Only the OS/model boundaries (window context, screen, Ollama) are faked. This
exercises the full data path the demo relies on, including the dedup gate keeping
vision to a minority, and a semantic query returning the right activity.
"""

import asyncio
import contextlib
import json

import numpy as np
import pytest

from datastore import ActivityStore
from watcher import config
from watcher.daemon import WatcherDaemon
from watcher.gate import ProactiveGate
from watcher.triggers import Trigger
from watcher.winctx import WindowContext, content_ratio

pytest.importorskip("mcp")


class FakeCtx:
    def __init__(self):
        self.win = WindowContext()

    def set(self, app, title, uia):
        self.win = WindowContext(app=app, title=title, uia_text=uia, content_ratio=content_ratio(uia))

    def get(self, force=False):
        return self.win

    def foreground_key(self):
        return (self.win.app, self.win.title)


class FakeUnderstanding:
    """Topic-separated embeddings so semantic ranking is meaningful."""

    def describe(self, image, uia_text=""):
        return {"activity": "unknown", "salient_text": "", "entities": [], "is_actionable": False}

    def embed(self, text, is_query=False):
        v = np.zeros(768, dtype=np.float32)
        t = text.lower()
        v[0] = 1.0 if ("python" in t or "main.py" in t or "code" in t) else 0.0
        v[1] = 1.0 if ("invoice" in t or "payment" in t or "overdue" in t) else 0.0
        if v.sum() == 0:
            v[2] = 1.0
        return v


def test_full_pipeline_capture_dedup_and_query(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTOUR_DATA_DIR", str(tmp_path))
    store = ActivityStore(tmp_path, dim=768, backend="numpy")
    u = FakeUnderstanding()
    ctx = FakeCtx()
    spoke = []
    gate = ProactiveGate(cooldown=60, speaker=lambda p: spoke.append(p))
    daemon = WatcherDaemon(store, u, ctx, screen=None, gate=gate, heartbeat_secs=30)

    # scene 1: coding (rich UIA text, hard trigger) -> stored via UIA path
    ctx.set("Code", "main.py", "def main(): print('hello') " * 8 + "editing python code here")
    daemon.process(Trigger("AppSwitch", ts=100.0))

    # ...hammered by soft triggers on the same screen -> all deduped
    for i in range(5):
        daemon.process(Trigger("TypingPause", ts=101.0 + i))

    # scene 2: an overdue invoice appears (actionable) -> stored + proactive interjection
    ctx.set("Mail", "Invoice", "Your payment is OVERDUE. Invoice 42 is due. " * 4)
    daemon.process(Trigger("AppSwitch", ts=200.0))

    store.close()

    # only 2 rows despite 7 triggers; zero vision calls; one interjection fired
    assert daemon.stats["triggers"] == 7
    assert daemon.stats["vision"] == 0
    assert daemon.stats["skipped"] >= 5
    assert len(spoke) == 1 and "OVERDUE" in spoke[0].upper() or "overdue" in spoke[0].lower()

    # now query through the real MCP server against the same data dir
    import importlib

    import mcp_server.server as s
    importlib.reload(s)
    monkeypatch.setattr(s._understanding, "embed", u.embed)
    # Match the daemon's explicit numpy backend so both sides read/write the
    # same index file regardless of whether turbovec is installed on this box.
    monkeypatch.setattr(
        s, "open_store", lambda: contextlib.closing(ActivityStore(config.data_dir(), dim=768, backend="numpy"))
    )

    res = json.loads(s.query_datastore("what python code was I editing", limit=2))
    assert res["count"] == 2
    assert "main.py" in res["results"][0]["window"] or "python" in res["results"][0]["summary"].lower()

    res2 = json.loads(s.query_datastore("anything about an overdue payment", limit=2))
    assert "Invoice" in res2["results"][0]["window"] or "overdue" in res2["results"][0]["summary"].lower()
