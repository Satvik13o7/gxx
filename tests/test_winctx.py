import watcher.winctx as winctx
from watcher.winctx import content_ratio, is_thin


def test_content_ratio_high_for_prose_low_for_symbols():
    assert content_ratio("the quick brown fox jumps over") > 0.8
    assert content_ratio("|-+-|=====|>>><<<|") < 0.2
    assert content_ratio("") == 0.0


def test_is_thin_short_text():
    assert is_thin("too short", app="notepad") is True  # < 100 chars


def test_is_thin_prefer_vision_app():
    long_text = "word " * 60  # > 100 chars, high content ratio
    assert is_thin(long_text, app="WindowsTerminal") is True
    assert is_thin(long_text, app="notepad") is False


def test_is_thin_low_content_ratio():
    # long, but mostly box-drawing / symbols => thin
    symbols = "─│┌┐└┘├┤┬┴┼ " * 20
    assert len(symbols) > 100
    assert is_thin(symbols, app="someapp") is True


def test_foreground_window_info_mac_fallback(monkeypatch):
    monkeypatch.setattr(winctx.sys, "platform", "darwin")
    monkeypatch.setattr(winctx.subprocess, "check_output", lambda *a, **k: "Safari\tDocs\n")
    hwnd, title, app = winctx._foreground_window_info()
    assert app == "Safari"
    assert title == "Docs"
    assert isinstance(hwnd, int)
