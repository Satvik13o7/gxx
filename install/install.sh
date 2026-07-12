#!/usr/bin/env bash
# contour installer (macOS path)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
HERMES_DIR="${HOME}/.hermes"
HERMES_CONFIG="${HERMES_DIR}/config.yaml"
DATA_DIR="${HERMES_DIR}/contour"
LAUNCH_AGENT_LABEL="com.contour.watcher"
LAUNCH_AGENT_PLIST="${HOME}/Library/LaunchAgents/${LAUNCH_AGENT_LABEL}.plist"
LOG_OUT="/tmp/contour-watcher.log"
LOG_ERR="/tmp/contour-watcher.err"
CLIENT_HOME="${HOME}/.contour/client"

RELAY_URL="${CONTOUR_RELAY_URL:-}"
DEVICE_TOKEN="${CONTOUR_DEVICE_TOKEN:-}"
ASK_CLOUD="${CONTOUR_ASK_CLOUD:-false}"
VISION_MODEL="${CONTOUR_VISION_MODEL:-gemma4:e4b}"
EMBED_MODEL="${CONTOUR_EMBED_MODEL:-nomic-embed-text}"
INFERENCE_MODE="${CONTOUR_INFERENCE_MODE:-local}"
HOSTED_PROVIDER="${CONTOUR_HOSTED_PROVIDER:-relay}"
HOSTED_INFERENCE_URL="${CONTOUR_HOSTED_INFERENCE_URL:-}"
HOSTED_INFERENCE_KEY="${CONTOUR_HOSTED_INFERENCE_KEY:-}"
DEEPINFRA_API_KEY="${DEEPINFRA_API_KEY:-}"

step() { printf "\n[%s] %s\n" "$1" "$2"; }
warn() { printf "  ! %s\n" "$1"; }
ok() { printf "  + %s\n" "$1"; }

is_project_root() {
  [[ -f "$1/pyproject.toml" && -d "$1/watcher" && -f "$1/install/register_hermes.py" ]]
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi
  warn "uv not found; installing uv"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh || true
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
}

ensure_project_root() {
  if is_project_root "$PROJECT_ROOT"; then
    return
  fi
  if [[ -z "$RELAY_URL" ]]; then
    warn "Installer downloaded without project files and no --relay-url provided."
    warn "Pass --relay-url so the client bundle can be downloaded."
    exit 1
  fi
  step 0 "Downloading contour client bundle"
  mkdir -p "$CLIENT_HOME"
  local archive
  archive="$(mktemp /tmp/contour-client.XXXXXX.tar.gz)"
  curl -fsSL "${RELAY_URL}/download/client.tar.gz" -o "$archive"
  rm -rf "$CLIENT_HOME"
  mkdir -p "$CLIENT_HOME"
  tar -xzf "$archive" -C "$CLIENT_HOME"
  rm -f "$archive"
  PROJECT_ROOT="$CLIENT_HOME"
  ok "Client bundle extracted to ${PROJECT_ROOT}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --relay-url) RELAY_URL="${2:-}"; shift 2 ;;
    --device-token) DEVICE_TOKEN="${2:-}"; shift 2 ;;
    --ask-cloud) ASK_CLOUD="true"; shift 1 ;;
    --vision-model) VISION_MODEL="${2:-}"; shift 2 ;;
    --embed-model) EMBED_MODEL="${2:-}"; shift 2 ;;
    --inference-mode) INFERENCE_MODE="${2:-}"; shift 2 ;;
    --hosted-provider) HOSTED_PROVIDER="${2:-}"; shift 2 ;;
    --hosted-inference-url) HOSTED_INFERENCE_URL="${2:-}"; shift 2 ;;
    --hosted-inference-key) HOSTED_INFERENCE_KEY="${2:-}"; shift 2 ;;
    --deepinfra-api-key) DEEPINFRA_API_KEY="${2:-}"; shift 2 ;;
    --help|-h)
      cat <<'USAGE'
Usage: ./install.sh [options]
  --relay-url URL         Relay base URL
  --device-token TOKEN    Device token minted by /signup or /login
  --ask-cloud             Enable opt-in ask_cloud path
  --vision-model MODEL    Vision model tag (default gemma4:e4b)
  --embed-model MODEL     Embedding model tag (default nomic-embed-text)
  --inference-mode MODE   local|hosted (default local)
  --hosted-provider NAME  relay|hf|deepinfra|openai_compat (default relay)
  --hosted-inference-url  Hosted inference base URL
  --hosted-inference-key  Optional bearer token for hosted inference
  --deepinfra-api-key     Optional DeepInfra API key (used in hosted provider=deepinfra)
USAGE
      exit 0
      ;;
    *)
      warn "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  warn "This installer targets macOS. For Windows, use install.ps1."
fi

if [[ "$INFERENCE_MODE" != "local" && "$INFERENCE_MODE" != "hosted" ]]; then
  warn "Invalid --inference-mode '$INFERENCE_MODE' (expected local|hosted)"
  exit 1
fi

ensure_project_root

step 1 "Checking Python 3.10+"
command -v python3 >/dev/null || { warn "Python 3.10+ required"; exit 1; }
PYTHON="$(command -v python3)"
RUNTIME_PYTHON="$PYTHON"
ok "Python available at ${PYTHON}"

step 2 "Ensuring Ollama + local models"
if ! command -v ollama >/dev/null; then
  if command -v brew >/dev/null; then
    warn "Ollama not found; installing with Homebrew cask"
    brew install --cask ollama || warn "Ollama install failed; install manually from https://ollama.com/download"
  else
    warn "Ollama not found; install from https://ollama.com/download"
  fi
fi
if command -v ollama >/dev/null; then
  if ! pgrep -f "ollama serve" >/dev/null 2>&1; then
    nohup ollama serve >/tmp/contour-ollama.log 2>&1 &
    sleep 2
  fi
  if [[ "$INFERENCE_MODE" == "local" ]]; then
    ollama pull "$VISION_MODEL" || warn "Failed to pull $VISION_MODEL"
  else
    warn "Hosted inference mode selected; skipping local vision model pull"
  fi
  ollama pull "$EMBED_MODEL" || warn "Failed to pull $EMBED_MODEL"
  ok "Models requested (vision=${INFERENCE_MODE}, embed=${EMBED_MODEL})"
fi

step 3 "Installing contour Python dependencies"
ensure_uv
if command -v uv >/dev/null; then
  (cd "$PROJECT_ROOT" && uv sync)
  if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    RUNTIME_PYTHON="$PROJECT_ROOT/.venv/bin/python"
  fi
else
  warn "uv not found; using pip"
  "$PYTHON" -m pip install --user -e "$PROJECT_ROOT"
fi
ok "Dependencies installed"

step 4 "Verifying Hermes"
if command -v hermes >/dev/null; then
  hermes doctor >/dev/null 2>&1 || warn "hermes doctor reported issues"
  ok "Hermes available"
else
  warn "Hermes not found. Install/repair Hermes first."
fi

step 5 "Registering contour MCP server + skill"
mkdir -p "$DATA_DIR"
if command -v uv >/dev/null; then
  (cd "$PROJECT_ROOT" && uv run --with pyyaml python install/register_hermes.py \
    --config "$HERMES_CONFIG" --python "$RUNTIME_PYTHON" \
    --relay-url "$RELAY_URL" --token "$DEVICE_TOKEN" \
    --data-dir "$DATA_DIR" --ask-cloud "$ASK_CLOUD" --enable-voice true \
    --inference-mode "$INFERENCE_MODE" \
    --hosted-provider "$HOSTED_PROVIDER" \
    --hosted-inference-url "$HOSTED_INFERENCE_URL" \
    --hosted-inference-key "$HOSTED_INFERENCE_KEY" \
    --deepinfra-api-key "$DEEPINFRA_API_KEY")
else
  "$PYTHON" -m pip install --user --quiet pyyaml
  "$PYTHON" "${PROJECT_ROOT}/install/register_hermes.py" \
    --config "$HERMES_CONFIG" --python "$RUNTIME_PYTHON" \
    --relay-url "$RELAY_URL" --token "$DEVICE_TOKEN" \
    --data-dir "$DATA_DIR" --ask-cloud "$ASK_CLOUD" --enable-voice true \
    --inference-mode "$INFERENCE_MODE" \
    --hosted-provider "$HOSTED_PROVIDER" \
    --hosted-inference-url "$HOSTED_INFERENCE_URL" \
    --hosted-inference-key "$HOSTED_INFERENCE_KEY" \
    --deepinfra-api-key "$DEEPINFRA_API_KEY"
fi
mkdir -p "${HERMES_DIR}/skills/contour-activity"
cp "${PROJECT_ROOT}/skill/SKILL.md" "${HERMES_DIR}/skills/contour-activity/"
ok "MCP server registered and skill copied"

step 6 "Zero-key web search"
if [[ -n "$RELAY_URL" ]]; then
  warn "Point Hermes web_search base URL to ${RELAY_URL}/search"
else
  warn "No relay URL provided; pass --relay-url and --device-token"
fi
if [[ "$INFERENCE_MODE" == "hosted" ]]; then
  [[ -n "$HOSTED_INFERENCE_URL" ]] && ok "Hosted Gemma inference enabled" || warn "Hosted mode selected but no hosted inference URL provided"
else
  ok "Local Gemma inference enabled"
fi
ok "STT provider configured via Hermes: ElevenLabs"

step 7 "Device token + cloud opt-in"
[[ -n "$DEVICE_TOKEN" ]] && ok "Device token wired" || warn "No device token provided"
[[ "$ASK_CLOUD" == "true" ]] && ok "ask_cloud enabled" || ok "ask_cloud disabled (default)"

step 8 "Permissions"
warn "Grant microphone and screen recording permissions in macOS Settings if prompted."

step 9 "Starting watcher at login via LaunchAgent"
mkdir -p "${HOME}/Library/LaunchAgents"
cat > "$LAUNCH_AGENT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCH_AGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${RUNTIME_PYTHON}</string>
    <string>-m</string>
    <string>watcher.daemon</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CONTOUR_RELAY_URL</key>
    <string>${RELAY_URL}</string>
    <key>CONTOUR_DEVICE_TOKEN</key>
    <string>${DEVICE_TOKEN}</string>
    <key>CONTOUR_INFERENCE_MODE</key>
    <string>${INFERENCE_MODE}</string>
    <key>CONTOUR_HOSTED_PROVIDER</key>
    <string>${HOSTED_PROVIDER}</string>
    <key>CONTOUR_HOSTED_INFERENCE_URL</key>
    <string>${HOSTED_INFERENCE_URL}</string>
    <key>CONTOUR_HOSTED_INFERENCE_KEY</key>
    <string>${HOSTED_INFERENCE_KEY}</string>
    <key>DEEPINFRA_API_KEY</key>
    <string>${DEEPINFRA_API_KEY}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_OUT}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_ERR}</string>
</dict>
</plist>
PLIST

: > "$LOG_OUT"
: > "$LOG_ERR"
launchctl bootout "gui/$(id -u)/${LAUNCH_AGENT_LABEL}" >/dev/null 2>&1 || true
if launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENT_PLIST" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${LAUNCH_AGENT_LABEL}" >/dev/null 2>&1 || true
  ok "Watcher launch agent installed and started"
elif launchctl load -w "$LAUNCH_AGENT_PLIST" >/dev/null 2>&1; then
  ok "Watcher launch agent loaded"
else
  warn "Could not bootstrap launch agent automatically"
  warn "Manual start: cd \"${PROJECT_ROOT}\" && \"${RUNTIME_PYTHON}\" -m watcher.daemon"
fi

echo
echo "Done. In Hermes run /reload-mcp, then ask: \"what was I just doing?\""
