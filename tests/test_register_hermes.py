import pytest

yaml = pytest.importorskip("yaml")

from install.register_hermes import build_block, register


def _seed(path):
    path.write_text(
        "model: hermes-4\nvoice: on\nmcp_servers:\n  other:\n    command: foo\n",
        encoding="utf-8",
    )


def test_register_preserves_user_settings_and_is_idempotent(tmp_path):
    cfg = tmp_path / "config.yaml"
    _seed(cfg)
    block = build_block(
        python="C:/py/python.exe",
        data_dir=str(tmp_path / "data"),
        relay_url="https://relay.example",
        token="dev-abc",
        ask_cloud=True,
    )
    register(cfg, block, ask_cloud=True)
    register(cfg, block, ask_cloud=True)  # again => must not duplicate

    d = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert d["model"] == "hermes-4"            # user setting preserved
    assert d["voice"] is True                  # 'on' -> YAML bool (equivalent)
    assert "other" in d["mcp_servers"]         # other server untouched
    g = d["mcp_servers"]["contour"]
    assert g["args"][0].endswith("server.py")
    assert g["env"]["CONTOUR_ASK_CLOUD"] == "true"
    assert g["tools"]["include"] == [
        "capture_and_store",
        "query_datastore",
        "query_memory_hybrid",
        "optimize_datastore",
        "web_search",
        "speak",
        "ask_cloud",
    ]
    assert d["skills"]["config"]["ask_cloud"] is True


def test_configure_voice_keeps_stt_disables_hermes_tts(tmp_path):
    cfg = tmp_path / "config.yaml"
    block = build_block("py", str(tmp_path), "", "", False)
    register(cfg, block, ask_cloud=False, enable_voice=True)
    d = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert d["voice"] == "on"
    assert d["stt"]["provider"] == "elevenlabs"
    assert d["stt"]["model"] == "scribe_v1"
    # voice OUTPUT is our ElevenLabs speak tool => Hermes' own TTS disabled
    assert d["tts"]["provider"] == "none"
    assert d["tts"]["enabled"] is False
    assert "speak" in d["mcp_servers"]["contour"]["tools"]["include"]


def test_register_from_empty_config(tmp_path):
    cfg = tmp_path / "config.yaml"  # does not exist yet
    block = build_block("py", str(tmp_path), "", "", False)
    register(cfg, block, ask_cloud=False)
    d = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert "contour" in d["mcp_servers"]
    assert d["skills"]["config"]["ask_cloud"] is False
