from watcher.triggers import TriggerEngine
import watcher.triggers as tmod


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def kinds(trigs):
    return [t.kind for t in trigs]


def test_app_switch_vs_window_focus():
    clock = Clock()
    fg = {"v": ("code", "a.py")}
    eng = TriggerEngine(fg_key_fn=lambda: fg["v"], idle_fn=lambda: 0.0, clock=clock)

    assert "AppSwitch" in kinds(eng.poll())      # first foreground => app switch
    clock.t += 1
    assert eng.poll() == []                        # unchanged => nothing
    fg["v"] = ("code", "b.py")                     # same app, new title
    clock.t += 1
    assert "WindowFocus" in kinds(eng.poll())
    fg["v"] = ("chrome", "news")                    # different app
    clock.t += 1
    assert "AppSwitch" in kinds(eng.poll())


def test_typing_pause_and_idle_are_one_shot():
    clock = Clock()
    idle = {"v": 0.0}
    eng = TriggerEngine(
        fg_key_fn=lambda: ("code", "a.py"), idle_fn=lambda: idle["v"], clock=clock
    )
    eng.poll()  # consume the initial AppSwitch

    idle["v"] = 5.0  # crossed typing-pause (default 2s), below idle (30s)
    clock.t += 1
    k = kinds(eng.poll())
    assert "TypingPause" in k and "Idle" not in k
    clock.t += 1
    assert "TypingPause" not in kinds(eng.poll())  # one-shot until re-active

    idle["v"] = 40.0  # now fully idle
    clock.t += 1
    assert "Idle" in kinds(eng.poll())

    idle["v"] = 0.0   # back to active resets the flags
    clock.t += 1
    eng.poll()
    idle["v"] = 5.0
    clock.t += 1
    assert "TypingPause" in kinds(eng.poll())


def test_visual_change_only_when_idle_and_over_threshold():
    clock = Clock()
    score = {"v": 0.5}
    eng = TriggerEngine(
        fg_key_fn=lambda: ("vlc", "movie"),
        idle_fn=lambda: 10.0,  # idle => visual checks allowed
        visual_probe=lambda: score["v"],
        clock=clock,
    )
    eng.poll()  # initial app switch; also seeds visual check timer
    clock.t += 5  # past the visual-check interval (3s)
    assert "VisualChange" in kinds(eng.poll())

    score["v"] = 0.01  # below threshold
    clock.t += 5
    assert "VisualChange" not in kinds(eng.poll())


def test_get_idle_seconds_darwin_fallback(monkeypatch):
    sample = '    "HIDIdleTime" = 4000000000\n'
    monkeypatch.setattr(tmod.sys, "platform", "darwin")
    monkeypatch.setattr(tmod.subprocess, "check_output", lambda *a, **k: sample)
    assert tmod.get_idle_seconds() == 4.0
