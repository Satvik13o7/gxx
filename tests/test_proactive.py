import json

import pytest

from watcher import proactive

LINE = "Heads up: the build failed."


# -- the guard, in isolation ---------------------------------------------------


def test_speaks_when_caller_says_to():
    g = proactive.gate(speak=True, text=LINE, now=1000, last_fire=0)
    assert g["speak"] is True
    assert g["text"] == LINE


def test_silent_when_caller_says_not_to():
    g = proactive.gate(speak=False, text="", now=1000, last_fire=0)
    assert g["speak"] is False
    assert "caller decided not to speak" in g["reason"]


def test_a_line_supplied_with_speak_false_is_still_not_spoken():
    """The speak flag is authoritative; stray text must not leak out."""
    g = proactive.gate(speak=False, text=LINE, now=1000, last_fire=0)
    assert g["speak"] is False
    assert g["text"] == ""


def test_speak_true_with_no_text_is_reported_as_a_caller_bug():
    g = proactive.gate(speak=True, text="   ", now=1000, last_fire=0)
    assert g["speak"] is False
    assert "no text" in g["reason"]


def test_cooldown_suppresses_a_chatty_caller():
    assert proactive.gate(speak=True, text=LINE, now=1000, last_fire=980, cooldown=60)["speak"] is False
    # ...and lets it through once the gap has elapsed
    assert proactive.gate(speak=True, text=LINE, now=1100, last_fire=980, cooldown=60)["speak"] is True


def test_cooldown_reports_remaining_time():
    g = proactive.gate(speak=True, text=LINE, now=1000, last_fire=970, cooldown=60)
    assert "30s remaining" in g["reason"]


def test_quiet_mode_overrides_the_caller():
    g = proactive.gate(speak=True, text=LINE, now=1000, last_fire=0, quiet=True)
    assert g["speak"] is False
    assert "quiet" in g["reason"]


def test_text_is_stripped_before_speaking():
    g = proactive.gate(speak=True, text=f"  {LINE}\n", now=1000, last_fire=0)
    assert g["text"] == LINE


# -- cross-process cooldown persistence ---------------------------------------


def test_last_fire_roundtrips(tmp_path):
    p = tmp_path / "state.json"
    assert proactive.read_last_fire(p) == 0.0  # never fired
    proactive.write_last_fire(1234.5, p)
    assert proactive.read_last_fire(p) == 1234.5


def test_corrupt_state_is_treated_as_never_fired(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json{", encoding="utf-8")
    assert proactive.read_last_fire(p) == 0.0


# -- the MCP tool end to end ---------------------------------------------------


@pytest.fixture
def server(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv("CONTOUR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CONTOUR_QUIET", raising=False)
    import importlib

    import mcp_server.server as s
    importlib.reload(s)
    return s


@pytest.fixture
def spoken(monkeypatch):
    """Capture what would have gone to ElevenLabs."""
    said = []
    import watcher.voice as voice

    monkeypatch.setattr(voice, "speak", lambda text, **kw: (said.append(text), True)[1])
    return said


def test_tool_registered(server):
    import asyncio

    tools = asyncio.run(server.mcp.list_tools())
    assert "speak_proactive" in {t.name for t in tools}


def test_tool_speaks_what_it_is_given(server, spoken):
    out = json.loads(server.speak_proactive(speak=True, text=LINE))
    assert out["spoke"] is True
    assert spoken == [LINE]


def test_tool_says_nothing_when_told_not_to(server, spoken):
    out = json.loads(server.speak_proactive(speak=False))
    assert out["spoke"] is False
    assert out["text"] == ""
    assert spoken == []


def test_tool_never_touches_the_datastore(server, spoken, monkeypatch):
    """The sink must not fetch — the caller already did that work."""
    def boom(*a, **kw):
        raise AssertionError("speak_proactive must not open the store")

    monkeypatch.setattr(server, "open_store", boom)
    assert json.loads(server.speak_proactive(speak=True, text=LINE))["spoke"] is True
    assert spoken == [LINE]


def test_tool_cooldown_holds_across_calls(server, spoken):
    assert json.loads(server.speak_proactive(speak=True, text=LINE))["spoke"] is True
    out = json.loads(server.speak_proactive(speak=True, text="and another thing"))
    assert out["spoke"] is False
    assert "cooldown" in out["reason"]
    assert spoken == [LINE]  # the second line never reached the user


def test_quiet_env_mutes_the_tool(server, spoken, monkeypatch):
    monkeypatch.setenv("CONTOUR_QUIET", "true")
    assert json.loads(server.speak_proactive(speak=True, text=LINE))["spoke"] is False
    assert spoken == []


def test_failed_tts_does_not_start_cooldown(server, monkeypatch):
    """A failed speak must not silence the next genuinely-actionable moment."""
    import watcher.voice as voice

    monkeypatch.setattr(voice, "speak", lambda text, **kw: False)
    out = json.loads(server.speak_proactive(speak=True, text=LINE))
    assert out["spoke"] is False
    assert out["reason"] == "tts failed"
    assert proactive.read_last_fire() == 0.0  # cooldown never started
