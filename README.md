# Contour — Local-First Proactive Multimodal Assistant

> *Building the contour for your brain.*

A local-first desktop assistant that watches your screen + audio, builds a **private,
on-device** activity store, and rides on top of an existing **Hermes Agent** install to
answer voice questions and proactively surface useful context — with **zero personal API
keys** at setup.

See [prd.md](prd.md) for the product spec and `.claude/plans/` for the implementation plan.

## How it works

```
Screen/Audio ─▶ Watcher (event-driven, text-first)
                  │  triggers: app-switch / window-focus / typing-pause / idle / visual-change
                  │  UIA accessibility text = primary source + content-hash dedup key
                  │  Gemma 4 vision = fallback only when text is thin/absent
                  ▼
             Datastore (turbovec embeddings + SQLite metadata)
                  ▲
                  │ MCP tools: capture_and_store / query_datastore / optimize_datastore / speak / ask_cloud
             Hermes Agent ──▶ voice input via native Whisper STT + native web_search
                  │             (Hermes' own TTS is disabled)
                  ▼
             Relay (FastAPI): ElevenLabs voice (TTS) + Linkup web search + opt-in cloud,
                              per-token rate limit, server-side PII backstop.
                              Project-owned keys — never on the client.
```

**Data minimization** (adapted from screenpipe): capture is event-driven, not a fixed
loop; the dedup key is *text*, not pixels; the expensive vision model runs only on a
minority of events (thin/absent accessibility text). See the watcher stats
(`triggers / skipped / uia / vision`) to confirm vision stays a minority.

## Layout
- `watcher/` — triggers, window/UIA context, capture, frame-diff, understanding, proactive gate, daemon
- `datastore/` — turbovec + SQLite store, content-hash/simhash dedup, PII scrub
- `mcp_server/` — FastMCP stdio server exposing the store to Hermes
- `skill/SKILL.md` — Hermes skill for activity Q&A + proactivity
- `relay/` — deployable FastAPI proxy (search + opt-in cloud)
- `install/` — `install.ps1` (Windows), `install.sh` (macOS), `register_hermes.py`

## Install (Windows)
```powershell
# after installing Hermes Agent, Ollama, and Python 3.10+
./install/install.ps1 -RelayUrl "https://<your-relay>" -DeviceToken "<token>"   # add -AskCloud to opt in
```
Then say to Hermes: **"what was I just doing?"** (`/reload-mcp` if Hermes is already running).

## Install (macOS)
```bash
# after installing Hermes Agent, Ollama, and Python 3.10+
chmod +x ./install/install.sh
./install/install.sh --relay-url "https://<your-relay>" --device-token "<token>"   # add --ask-cloud to opt in
```
Then run `/reload-mcp` in Hermes and ask: **"what was I just doing?"**.

## Deploy the relay
```bash
docker build -f relay/Dockerfile -t contour-relay .
docker run -p 8080:8080 \
  -e LINKUP_API_KEY="..." \        # web search (preferred provider)
  -e ELEVENLABS_API_KEY="..." \    # voice output (TTS)
  -e OPENAI_API_KEY="..." \        # optional: opt-in cloud escalation
  -e CONTOUR_RELAY_DB="/data/relay.db" \
  contour-relay
```
The relay also serves the signup/login/download dashboard at `/` and installers at
`/download/install.ps1` (Windows) and `/download/install.sh` (macOS). Installers
auto-bootstrap the client bundle from `/download/client.zip` or
`/download/client.tar.gz` when run outside a repo checkout.

## Develop / test
```bash
python -m pytest -q          # 69 tests, no Ollama/Hermes/turbovec required
```
The datastore falls back to an exact-cosine numpy index when `turbovec` isn't installed,
and every model/OS call is dependency-guarded, so the suite runs anywhere.

## Privacy
- Raw screen/audio **never** leave the device.
- Only distilled text summaries can be sent, only for web search / opt-in cloud escalation,
  and only after a client-side PII scrub (re-checked server-side at the relay).
- `ask_cloud` is off by default; enable explicitly at install with `-AskCloud`.
- The device token identifies the install, not the person. Best-effort PII scrubbing —
  disclosed, not guaranteed (see PRD non-goals).
