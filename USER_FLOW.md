# USER FLOW — Contour

How an end user (a technical hackathon tester) goes from the website to a working,
private, proactive assistant. Written from the user's side of the screen.

---

## 1. Onboarding (website → installed)

```
Landing/dashboard (single page)
      │
      ├─ Create account  ──▶ POST /signup ──▶ device token minted
      │                                         (or Sign in ──▶ POST /login ──▶ same token)
      ▼
Dashboard state (same page, no reload)
      │  shows: device token · install one-liner (copy) · Download installer for your OS · next steps
      ▼
User copies the OS-specific one-liner and runs it
      │  Windows: irm https://RELAY/download/install.ps1 -OutFile install.ps1
      │           ./install.ps1 -RelayUrl "https://RELAY" -DeviceToken "contour_..."
      │  macOS:   curl -fsSL https://RELAY/download/install.sh -o install.sh
      │           chmod +x install.sh && ./install.sh --relay-url "https://RELAY" --device-token "contour_..."
      ▼
Installer: pulls Gemma 4 + embeddings · installs deps · registers MCP + skill in Hermes ·
            wires the token · starts the watcher
           (if no repo checkout exists, installer auto-downloads a client bundle)
      ▼
User grants microphone permission · says "what was I just doing?"
```

- **Zero personal keys**: the user never enters an API key. Their device token (issued at
  signup) is the only credential, and it authorizes the shared relay for search / opt-in
  cloud. Provider keys live only on the relay.
- **Returning user**: the site remembers the token in the browser; visiting again lands
  straight on the dashboard. Signing in on a new machine returns the same token.

## 2. Passive capture (always-on, invisible, private)

```
User works normally
   │
   ▼
Watcher reacts to events (app switch, window focus, typing pause, idle, on-screen change)
   │
   ├─ reads the active window's accessibility (UIA) TEXT  ── primary, cheap
   │        │
   │        ├─ unchanged text on a soft event? → SKIP (nothing stored, nothing computed)
   │        └─ new/meaningful text?            → summarize + embed → store locally
   │
   └─ text thin/absent (canvas app, terminal, image)? → Gemma 4 vision describes the frame
                                                          (runs on a minority of events)
   ▼
Everything is written to a private on-device store (turbovec + SQLite). Raw pixels/audio
never leave the machine.
```

The user does nothing here. The design goal is that most activity is captured as compact
text and near-duplicates are dropped, so the store stays small and private.

## 3. Reactive voice Q&A (user asks)

```
User (voice): "what error did I hit earlier?" / "which docs was I reading about X?"
   │
   ▼
Hermes (native Whisper STT) → invokes the contour skill
   │
   ├─ mcp_contour_query_datastore(question)  → semantic search over local activity
   └─ (if it needs live info) Hermes native web_search → relay → Linkup
   ▼
Hermes composes a short answer, grounded in what the user actually saw
   │
   ▼
mcp_contour_speak(answer) → relay (/tts) → ElevenLabs → PCM played locally
   (Hermes' own TTS is disabled; our ElevenLabs voice is the only output)
```

If nothing relevant is stored, the assistant says so rather than inventing an answer.

## 4. Proactive interjection (assistant speaks first — gated & rare)

```
Watcher stores an observation flagged is_actionable (e.g. "build failed", "payment overdue")
   │
   ▼
Proactive gate (conservative, hardcoded rules):
   fires only if  actionable  AND  a context-defining event (app switch / focus / idle)
                  AND  an explicit keyword (error/deadline/failed/…)
                  AND  cooldown elapsed  AND  not in quiet mode
   │
   ├─ passes → ONE short offer of help spoken in the ElevenLabs voice (relay /tts)
   └─ fails  → stays silent   (the "boring" control scenario)
```

The bar is intentionally high so the assistant is helpful, not noisy.

## 5. Opt-in cloud escalation (off by default)

```
Only if the user enabled -AskCloud at install:
User asks something the local model + store can't answer
   │
   ▼
mcp_contour_ask_cloud(question)
   │  client-side PII scrub  →  relay (/cloud)  →  server-side PII scrub  →  OpenAI
   ▼
Text answer returned (text only — never screen/audio). If not opted in, the tool politely
refuses and the assistant answers with what it has locally.
```

## Privacy summary (what the user is promised)
- Raw screen/audio **never** leave the device.
- Only distilled text can be sent, only for search / opt-in cloud, only after a PII scrub
  (re-checked on the relay).
- `ask_cloud` is off unless explicitly enabled.
- The device token identifies the install, not the person. PII scrubbing is best-effort and
  disclosed — not marketed as guaranteed.
```
