import numpy as np
import pytest

from datastore import ActivityStore
from watcher.daemon import WatcherDaemon
from watcher import config
from watcher.triggers import Trigger
from watcher.winctx import WindowContext, content_ratio

DIM = 8


class FakeCtx:
    def __init__(self, app="", title="", uia_text=""):
        self.win = WindowContext(app=app, title=title, uia_text=uia_text,
                                 content_ratio=content_ratio(uia_text))

    def set(self, app, title, uia_text):
        self.win = WindowContext(app=app, title=title, uia_text=uia_text,
                                 content_ratio=content_ratio(uia_text))

    def get(self, force=False):
        return self.win

    def foreground_key(self):
        return (self.win.app, self.win.title)


class FakeUnderstanding:
    def __init__(self):
        self.describe_calls = 0
        self.embed_calls = 0

    def describe(self, image, uia_text=""):
        self.describe_calls += 1
        return {
            "app_or_context": "SomeApp",
            "activity": "doing a thing",
            "salient_text": "important line",
            "entities": ["thing"],
            "is_actionable": False,
        }

    def embed(self, text, is_query=False):
        self.embed_calls += 1
        return np.ones(DIM, dtype=np.float32)


class FakeScreen:
    def __init__(self):
        self.grabs = 0

    def grab_png(self, downscale_width=1920):
        self.grabs += 1
        return b"PNG"


LONG_TEXT = "the user is reading a long document about distributed systems " * 3


@pytest.fixture
def parts(tmp_path):
    store = ActivityStore(tmp_path, dim=DIM, backend="numpy")
    u = FakeUnderstanding()
    ctx = FakeCtx(app="notepad", title="doc.txt", uia_text=LONG_TEXT)
    screen = FakeScreen()
    daemon = WatcherDaemon(store, u, ctx, screen=screen, heartbeat_secs=30)
    yield daemon, store, u, ctx, screen
    store.close()


def test_uia_path_does_not_call_vision(parts):
    daemon, store, u, ctx, screen = parts
    rid = daemon.process(Trigger("AppSwitch", ts=100.0))
    assert rid is not None
    assert u.describe_calls == 0   # not thin => no vision
    assert screen.grabs == 0
    assert daemon.stats["uia"] == 1 and daemon.stats["vision"] == 0


def test_soft_trigger_dedups_before_embedding(parts):
    daemon, store, u, ctx, screen = parts
    daemon.process(Trigger("AppSwitch", ts=100.0))   # hard, stores
    embeds_after_first = u.embed_calls
    # identical content, soft trigger, within heartbeat => skipped, no embed
    skipped = daemon.process(Trigger("TypingPause", ts=105.0))
    assert skipped is None
    assert u.embed_calls == embeds_after_first        # embed avoided
    assert daemon.stats["skipped"] == 1
    assert len(store.recent(limit=99)) == 1


def test_hard_trigger_bypasses_dedup(parts):
    daemon, store, u, ctx, screen = parts
    daemon.process(Trigger("AppSwitch", ts=100.0))
    daemon.process(Trigger("AppSwitch", ts=105.0))    # hard again => stores
    assert len(store.recent(limit=99)) == 2


def test_thin_text_takes_vision_path(parts):
    daemon, store, u, ctx, screen = parts
    ctx.set(app="notepad", title="empty", uia_text="hi")  # thin (<100 chars)
    rid = daemon.process(Trigger("AppSwitch", ts=100.0))
    assert rid is not None
    assert u.describe_calls == 1 and screen.grabs == 1
    assert daemon.stats["vision"] == 1


def test_vision_cooldown_skips_repeat_soft_on_same_window(parts):
    daemon, store, u, ctx, screen = parts
    ctx.set(app="figma", title="board", uia_text="")   # thin (prefer-vision app)
    daemon.process(Trigger("AppSwitch", ts=100.0))      # hard: runs vision
    assert u.describe_calls == 1
    # soft trigger, same window, within heartbeat => vision NOT re-run
    skipped = daemon.process(Trigger("VisualChange", ts=110.0))
    assert skipped is None
    assert u.describe_calls == 1
    assert daemon.stats["skipped"] == 1


def test_vision_fraction_metric_is_minority_on_static_screen(parts):
    daemon, store, u, ctx, screen = parts
    # simulate a static (non-thin) screen hammered by soft triggers
    daemon.process(Trigger("AppSwitch", ts=0.0))
    for i in range(1, 10):
        daemon.process(Trigger("TypingPause", ts=float(i)))
    # only the first (hard) stored; the rest deduped; zero vision calls
    assert daemon.stats["vision"] == 0
    assert daemon.stats["skipped"] >= 8
    assert len(store.recent(limit=99)) == 1


def test_visualchange_low_score_skips_vision(parts, monkeypatch):
    daemon, store, u, ctx, screen = parts
    monkeypatch.setattr(config, "VISION_VISUAL_SCORE_MIN", 0.5)
    ctx.set(app="figma", title="board", uia_text="")  # thin -> vision path candidate
    skipped = daemon.process(Trigger("VisualChange", ts=100.0, meta={"score": 0.1}))
    assert skipped is None
    assert u.describe_calls == 0
    assert daemon.stats["skipped"] >= 1


def test_stale_frame_switch_avoids_repeated_wallpaper_like_vision(parts):
    daemon, store, u, ctx, screen = parts
    ctx.set(app="figma", title="board", uia_text="")
    daemon.process(Trigger("AppSwitch", ts=100.0))
    assert u.describe_calls == 1

    # Same captured frame bytes but a new foreground app/title: avoid another
    # vision call and emit a generic app-grounded fallback instead.
    ctx.set(app="Code", title="main.py", uia_text="")
    daemon.process(Trigger("AppSwitch", ts=110.0))

    assert u.describe_calls == 1
    latest = store.recent(limit=1)[0]
    assert latest["source"] == "uia"
    assert "no fresh visual delta" in latest["summary"].lower()


def test_writes_concept_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONCEPTS_ENABLED", True)
    monkeypatch.setattr(config, "CONCEPT_REFRESH_SECS", 1)
    store = ActivityStore(tmp_path, dim=DIM, backend="numpy")
    u = FakeUnderstanding()
    ctx = FakeCtx(app="Code", title="main.py", uia_text=LONG_TEXT)
    daemon = WatcherDaemon(store, u, ctx, screen=FakeScreen(), heartbeat_secs=30)

    daemon.process(Trigger("AppSwitch", ts=100.0))
    daemon.process(Trigger("AppSwitch", ts=102.0))

    folder = tmp_path / "concepts" / "code"
    assert (folder / "transcription.md").exists()
    assert (folder / "concept.md").exists()
    store.close()
