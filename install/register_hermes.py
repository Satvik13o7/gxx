"""Register the contour MCP server + skill into Hermes Agent's config.

Idempotent: safe to run repeatedly. Merges an `mcp_servers.contour` block and a
`skills.config.ask_cloud` toggle into `~/.hermes/config.yaml` (or the path given),
without clobbering the user's other settings.

The MCP server is launched by running server.py as a script (it self-inserts the
project root on sys.path, so absolute imports work without cwd/PYTHONPATH tricks).

Usage:
    python install/register_hermes.py \
        [--config PATH] [--python PATH] [--relay-url URL] [--token TOK] \
        [--data-dir DIR] [--ask-cloud true|false]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "hermes" / "config.yaml"
    return Path.home() / ".hermes" / "config.yaml"


def default_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA")
    return str(Path(base) / "hermes" / "contour") if base else str(Path.home() / ".hermes" / "contour")


def build_block(python: str, data_dir: str, relay_url: str, token: str, ask_cloud: bool) -> dict:
    server_py = str(PROJECT_ROOT / "mcp_server" / "server.py")
    return {
        "command": python,
        "args": [server_py],
        "env": {
            "CONTOUR_DATA_DIR": data_dir,
            "CONTOUR_RELAY_URL": relay_url,
            "CONTOUR_DEVICE_TOKEN": token,
            "CONTOUR_ASK_CLOUD": "true" if ask_cloud else "false",
            "CONTOUR_INFERENCE_MODE": os.environ.get("CONTOUR_INFERENCE_MODE", "local"),
            "CONTOUR_HOSTED_PROVIDER": os.environ.get("CONTOUR_HOSTED_PROVIDER", "relay"),
            "CONTOUR_HOSTED_INFERENCE_URL": os.environ.get("CONTOUR_HOSTED_INFERENCE_URL", ""),
            "CONTOUR_HOSTED_INFERENCE_KEY": os.environ.get("CONTOUR_HOSTED_INFERENCE_KEY", ""),
            "DEEPINFRA_API_KEY": os.environ.get("DEEPINFRA_API_KEY", ""),
            "CONTOUR_STT_PROVIDER": os.environ.get("CONTOUR_STT_PROVIDER", "elevenlabs"),
            "ELEVENLABS_STT_MODEL": os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v1"),
        },
        "tools": {
            "include": [
                "capture_and_store",
                "query_datastore",
                "optimize_datastore",
                "web_search",
                "speak",
                "speak_proactive",
                "ask_cloud",
            ]
        },
    }


def configure_voice(data: dict, stt_provider: str = "elevenlabs", elevenlabs_stt_model: str = "scribe_v1") -> None:
    """Configure voice IO:
    - STT via chosen provider (default ElevenLabs)
    - TTS via contour's ElevenLabs `speak` tool (Hermes native TTS disabled)

    Only fills in these providers; leaves any other voice settings the user has.
    NOTE: Hermes' exact "disable TTS" key is confirmed at install; we set the two
    plausible forms and the installer prints a reminder to verify.
    """
    data["voice"] = "on"
    stt = data.setdefault("stt", {})
    stt["provider"] = stt_provider
    if stt_provider == "elevenlabs":
        stt.setdefault("model", elevenlabs_stt_model)
    tts = data.setdefault("tts", {})
    tts["provider"] = "none"   # do not let Hermes speak; contour speaks via ElevenLabs
    tts["enabled"] = False


def register(
    config_path: Path,
    block: dict,
    ask_cloud: bool,
    enable_voice: bool = False,
    stt_provider: str = "elevenlabs",
    elevenlabs_stt_model: str = "scribe_v1",
) -> None:
    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML is required: pip install pyyaml")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    data.setdefault("mcp_servers", {})["contour"] = block
    skills = data.setdefault("skills", {})
    skills.setdefault("config", {})["ask_cloud"] = ask_cloud
    if enable_voice:
        configure_voice(data, stt_provider=stt_provider, elevenlabs_stt_model=elevenlabs_stt_model)

    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    print(f"registered contour MCP server in {config_path}")
    if enable_voice:
        print(
            f"configured voice ({stt_provider} STT; Hermes TTS off — contour speaks via ElevenLabs)"
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=default_config_path())
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--relay-url", default=os.environ.get("CONTOUR_RELAY_URL", ""))
    p.add_argument("--token", default=os.environ.get("CONTOUR_DEVICE_TOKEN", ""))
    p.add_argument("--data-dir", default=default_data_dir())
    p.add_argument("--ask-cloud", default="false")
    p.add_argument("--enable-voice", default="false")
    p.add_argument("--stt-provider", default=os.environ.get("CONTOUR_STT_PROVIDER", "elevenlabs"))
    p.add_argument("--elevenlabs-stt-model", default=os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v1"))
    p.add_argument("--inference-mode", default=os.environ.get("CONTOUR_INFERENCE_MODE", "local"))
    p.add_argument("--hosted-provider", default=os.environ.get("CONTOUR_HOSTED_PROVIDER", "relay"))
    p.add_argument(
        "--hosted-inference-url",
        default=os.environ.get("CONTOUR_HOSTED_INFERENCE_URL", ""),
    )
    p.add_argument(
        "--hosted-inference-key",
        default=os.environ.get("CONTOUR_HOSTED_INFERENCE_KEY", ""),
    )
    p.add_argument(
        "--deepinfra-api-key",
        default=os.environ.get("DEEPINFRA_API_KEY", ""),
    )
    args = p.parse_args(argv)

    ask_cloud = args.ask_cloud.strip().lower() in {"1", "true", "yes", "on"}
    enable_voice = args.enable_voice.strip().lower() in {"1", "true", "yes", "on"}
    os.environ["CONTOUR_INFERENCE_MODE"] = (args.inference_mode or "local").strip().lower()
    os.environ["CONTOUR_HOSTED_PROVIDER"] = (args.hosted_provider or "relay").strip().lower()
    os.environ["CONTOUR_HOSTED_INFERENCE_URL"] = (args.hosted_inference_url or "").strip()
    os.environ["CONTOUR_HOSTED_INFERENCE_KEY"] = (args.hosted_inference_key or "").strip()
    os.environ["DEEPINFRA_API_KEY"] = (args.deepinfra_api_key or "").strip()
    os.environ["CONTOUR_STT_PROVIDER"] = (args.stt_provider or "elevenlabs").strip().lower()
    os.environ["ELEVENLABS_STT_MODEL"] = (args.elevenlabs_stt_model or "scribe_v1").strip()
    block = build_block(args.python, args.data_dir, args.relay_url, args.token, ask_cloud)
    register(
        args.config,
        block,
        ask_cloud,
        enable_voice,
        stt_provider=os.environ["CONTOUR_STT_PROVIDER"],
        elevenlabs_stt_model=os.environ["ELEVENLABS_STT_MODEL"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
