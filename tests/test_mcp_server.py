import asyncio
import json

import numpy as np
import pytest

pytest.importorskip("mcp")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTOUR_DATA_DIR", str(tmp_path))
    import importlib

    import mcp_server.server as s
    importlib.reload(s)  # pick up CONTOUR_DATA_DIR

    # deterministic, well-separated embeddings keyed on a topic word
    def fake_embed(text, is_query=False):
        v = np.zeros(768, dtype=np.float32)
        v[0] = 1.0 if "code" in text or "main.py" in text else 0.0
        v[1] = 1.0 if "video" in text or "youtube" in text else 0.0
        if v.sum() == 0:
            v[2] = 1.0
        return v

    monkeypatch.setattr(s._understanding, "embed", fake_embed)
    return s


def test_all_tools_registered(server):
    tools = asyncio.run(server.mcp.list_tools())
    assert sorted(t.name for t in tools) == [
        "ask_cloud",
        "capture_and_store",
        "optimize_datastore",
        "query_datastore",
        "speak",
        "speak_proactive",
        "web_search",
    ]


def test_speak_tool_uses_voice(server, monkeypatch):
    import json as _json

    spoken = {}
    import watcher.voice as voice

    def fake_speak(text, **kw):
        spoken["t"] = text
        return True

    monkeypatch.setattr(voice, "speak", fake_speak)
    out = _json.loads(server.speak("build finished successfully"))
    assert out["ok"] is True
    assert spoken["t"] == "build finished successfully"


def test_capture_then_semantic_query(server):
    server.capture_and_store("editing main.py in VS Code", app="Code", window="main.py")
    server.capture_and_store("watching a youtube video", app="chrome", window="YouTube")

    res = json.loads(server.query_datastore("what code was I writing", limit=2))
    assert res["count"] == 2
    # the code row should rank first given the fake embedding space
    assert "main.py" in res["results"][0]["summary"]


def test_ask_cloud_gated_by_optin(server, monkeypatch):
    assert json.loads(server.ask_cloud("hi"))["ok"] is False  # disabled by default
    monkeypatch.setenv("CONTOUR_ASK_CLOUD", "true")
    # enabled but relay unconfigured => graceful error, not a crash
    out = json.loads(server.ask_cloud("hi"))
    assert out["ok"] is False and "relay" in out["error"].lower()


def test_optimize_returns_report(server):
    server.capture_and_store("a note", app="x")
    out = json.loads(server.optimize_datastore())
    assert out["ok"] is True
    assert {"deduped", "evicted", "hard_deleted"} <= set(out)


def test_web_search_uses_relay(server, monkeypatch):
    class DummyRelay:
        def search(self, query, num_results=5):
            assert query == "latest python release"
            assert num_results == 3
            return {"results": [{"title": "Python", "url": "https://python.org"}]}

    monkeypatch.setattr(server, "RelayClient", lambda: DummyRelay())
    out = json.loads(server.web_search("latest python release", limit=3))
    assert out["ok"] is True
    assert out["count"] == 1


def test_web_search_handles_relay_error(server, monkeypatch):
    class FailingRelay:
        def search(self, query, num_results=5):
            raise server.RelayError("boom")

    monkeypatch.setattr(server, "RelayClient", lambda: FailingRelay())
    out = json.loads(server.web_search("anything", limit=5))
    assert out["ok"] is False
    assert "boom" in out["error"]
