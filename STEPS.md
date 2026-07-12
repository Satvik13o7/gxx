# STEPS — what we need to do to ship & demo Contour

A step-by-step runbook for **us** (the builders). Ordered so a working demo exists as
early as possible. Checkboxes track remaining work; most code is already built and tested
(`python -m pytest -q` → 63 passing, no external services required).

---

## A. One-time: deploy the relay (project-owned keys live here only)

1. **Get provider keys** (kept on the relay, never on clients):
   - [ ] `LINKUP_API_KEY` — web search (we have the paid key). Preferred provider.
   - [ ] `ELEVENLABS_API_KEY` — voice output (TTS). Required now that we use ElevenLabs
     instead of Hermes' native voice.
   - [ ] `OPENAI_API_KEY` — only if enabling opt-in `ask_cloud`.
2. **Deploy** `relay/` to Railway or Fly.io (Dockerfile included):
   ```bash
   docker build -f relay/Dockerfile -t contour-relay .
   # set as deploy secrets / env:
   #   LINKUP_API_KEY=...          (search)
   #   ELEVENLABS_API_KEY=...      (voice / TTS)
   #   ELEVENLABS_VOICE_ID=...     (optional; default Rachel 21m00Tcm4TlvDq8ikWAM)
   #   ELEVENLABS_MODEL=eleven_turbo_v2_5  (optional)
   #   OPENAI_API_KEY=...          (optional, cloud escalation)
   #   CONTOUR_RELAY_RATE=120          (per-token req/min)
   #   CONTOUR_RELAY_DB=/data/relay.db (persist accounts on a volume)
   #   LINKUP_DEPTH=standard       (fast|standard|deep)
   ```
   - [ ] Confirm `GET /health` returns `{"ok":true,"providers":{"search":true,...}}`.
   - [ ] Note the public URL → this is `RELAY_URL` everywhere below.
3. **Accounts**: signup/login are built in (`/signup`, `/login`) and issue device tokens
   automatically. No static token list needed. (Env `CONTOUR_RELAY_TOKENS` still works for
   pre-issued tokens if we want demo shortcuts.)

## B. One-time per demo machine: prerequisites

4. [ ] Install **Hermes Agent**: `iex (irm https://hermes-agent.nousresearch.com/install.ps1)`
5. [ ] Install **Ollama** (https://ollama.com/download) and start it.
6. [ ] Install **Python 3.10+**. (`uv` optional but nice.)
7. [ ] Confirm Hermes voice INPUT works: `stt.provider = faster-whisper` (no key). Install
   Hermes voice extras + ffmpeg/portaudio. Voice OUTPUT is ours (ElevenLabs via the `speak`
   tool) — the installer disables Hermes' native TTS, so no manual TTS setup is needed.

## C. Install contour (the 5-minute path)

8. [ ] From the site (or repo), run the platform installer:
   - Windows (PowerShell):
     ```powershell
     irm https://RELAY_URL/download/install.ps1 -OutFile install.ps1
     ./install.ps1 -RelayUrl "https://RELAY_URL" -DeviceToken "contour_..."   # -AskCloud to opt in
     ```
   - macOS (Terminal):
     ```bash
     curl -fsSL https://RELAY_URL/download/install.sh -o install.sh
     chmod +x install.sh
     ./install.sh --relay-url "https://RELAY_URL" --device-token "contour_..."   # --ask-cloud to opt in
     ```
    The installer: pulls `gemma4:e4b` + `nomic-embed-text`, installs contour deps, registers the
    MCP server + skill into Hermes, wires the token, and starts the watcher at login
    (Scheduled Task on Windows, LaunchAgent on macOS).
9. [ ] Grant **microphone** permission (OS Settings → Privacy / Microphone).
10. [ ] In a running Hermes session, run `/reload-mcp` so it picks up the contour tools.

## D. Point Hermes' web search at the relay (zero-key search)

11. [ ] Configure Hermes' `web_search` provider base URL → `https://RELAY_URL/search`
    (so the Linkup key stays on the relay).
    - [ ] **If Hermes doesn't accept a custom search endpoint**: flip on the fallback thin
      `web_search` MCP tool (add it to `mcp_server/server.py` + `tools.include`) that calls
      `RelayClient().search(...)`. Decide this once by inspecting `hermes mcp` / config.

## E. Verify the demo (both success metrics)

12. [ ] **Reactive Q&A**: do a scripted activity (open a file, read a page), then say
    “what was I just doing?” → Hermes calls `query_datastore` → correct spoken answer.
13. [ ] **Proactive**: run the scripted trigger scenario (e.g. a build error on screen) →
    one timely spoken interjection. Then a “boring” control (reading a calm article) →
    silence.
14. [ ] **Data minimization**: check watcher stats (`triggers / skipped / uia / vision`) —
    vision should be a small minority.
15. [ ] **Zero-key**: confirm no personal API key was requested anywhere in setup.

---

## Remaining code TODOs (small, mostly confirmations)

- [ ] **Confirm Hermes' "disable native TTS" key.** We set `tts.provider: none` +
  `tts.enabled: false`; verify these actually silence Hermes' own voice (so only our
  ElevenLabs `speak` plays). Adjust `configure_voice()` in `register_hermes.py` if the real
  key differs.
- [ ] **Pick the ElevenLabs voice** (`ELEVENLABS_VOICE_ID` on the relay) and confirm the
  PCM playback sounds right on the demo machine (`sounddevice` must be installed/working).
- [ ] **Confirm Hermes `web_search` custom endpoint** support (step 11) → else enable the
  fallback tool.
- [ ] **Install `turbovec` on the target machine** (`pip install turbovec`); the store
  auto-falls back to a numpy index if absent, but turbovec is the intended path.
- [ ] **System-audio loopback** (`pyaudiowpatch`) is a stretch goal; mic-only works today.
- [ ] Optional: serve `STEPS.md` / `USER_FLOW.md` from the relay if we want the dashboard
  doc links live (currently resolve only when the folder is hosted statically).

## Owner cheat-sheet (env vars)
| Where | Var | Purpose |
|---|---|---|
| Relay | `LINKUP_API_KEY` | web search (preferred) |
| Relay | `ELEVENLABS_API_KEY` | voice output / TTS |
| Relay | `ELEVENLABS_VOICE_ID` | which ElevenLabs voice (optional) |
| Relay | `OPENAI_API_KEY` | opt-in cloud escalation |
| Relay | `CONTOUR_RELAY_DB` | account/token persistence |
| Client (MCP env) | `CONTOUR_RELAY_URL`, `CONTOUR_DEVICE_TOKEN` | reach the relay |
| Client | `CONTOUR_ASK_CLOUD` | enable ask_cloud (opt-in) |
| Client | `CONTOUR_VISION_MODEL` | default `gemma4:e4b` (`gemma4:12b` = upgrade) |
