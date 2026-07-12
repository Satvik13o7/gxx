---
name: contour-activity
description: >-
  Answer questions about what the user has recently been doing on their PC and
  proactively surface useful context, grounded in the local contour activity store
  (private, on-device) plus live web search when needed.
version: 0.1.0
author: contour
license: MIT
metadata:
  hermes:
    tags: [productivity, memory, screen, voice]
    requires_tools:
      - mcp_contour_query_datastore
      - mcp_contour_query_memory_hybrid
      - mcp_contour_optimize_datastore
      - mcp_contour_speak
    config:
      # non-secret toggles live in ~/.hermes/config.yaml under skills.config.*
      ask_cloud: false
---

# contour Activity Assistant

## When to Use
Use this skill whenever the user asks about **their own recent activity** — e.g.
"what was I just doing?", "what error did I see earlier?", "which docs was I
reading about X?", "summarize my last hour" — or when the user asks a question
that recent on-screen context would help answer. Also use it when deciding whether
to proactively surface something the watcher flagged.

## Quick Reference
- `mcp_contour_query_memory_hybrid(query, limit=10, since_minutes=None, include_concepts=True)`
  — preferred recall path: combines exact transcription matches, semantic retrieval,
  and concept-folder context snippets.
- `mcp_contour_query_datastore(query, limit=10, since_minutes=None)` — semantic +
  recent fallback over the local activity store. Use when you need a lighter query
  without concept-file lookups.
- `mcp_contour_capture_and_store(summary, app, window, salient_text, tags)` — record a
  new note/observation worth remembering.
- `mcp_contour_optimize_datastore(retention_days=30, evict_after_days=3)` — housekeeping
  (dedup + retention). Safe to run periodically or when asked to "clean up".
- `mcp_contour_speak(text)` — speak your final answer aloud in the user's ElevenLabs voice
  (via the relay). This is how the user HEARS responses — Hermes' native TTS is disabled.
- `mcp_contour_ask_cloud(question)` — OPT-IN, text-only cloud escalation. Off unless the
  user enabled it at install. Never carries raw screen/audio.

## Procedure
1. **Reactive Q&A**: call `mcp_contour_query_memory_hybrid` with the user's question as
   the query. If the question is time-scoped ("in the last 20 minutes"), pass
   `since_minutes`. Ground your answer in both datastore rows and concept snippets;
   cite app/window/time briefly. Fall back to `mcp_contour_query_datastore` only when
   concept lookup isn't needed.
2. **Needs live info too?** If the answer depends on current web information (docs,
   prices, news), combine the store results with Hermes' native `web_search` tool.
   Do not build a separate search path — use the built-in one.
3. **Answer for voice**: keep replies to 1–3 short, natural sentences, then call
   `mcp_contour_speak(answer)` so the user hears it in the ElevenLabs voice. Avoid lists,
   markdown, and long quotes — write for the ear.
4. **Can't answer locally?** Only if the user opted into cloud escalation, use
   `mcp_contour_ask_cloud`. Otherwise say what you found and what you couldn't.
5. **Proactive interjection**: when handed a flagged observation, phrase ONE short,
   friendly spoken sentence: name what you noticed and offer help. If it isn't clearly
   useful, stay silent.

## Pitfalls
- Don't fabricate activity. If `query_datastore` returns nothing relevant, say so.
- Don't send screen/audio anywhere. Only distilled text leaves the device, and only
  via `ask_cloud`/web search, which the user opted into.
- Don't be chatty when proactive — one sentence, no preamble, respect that the user
  didn't ask.
- The store is local and may be sparse right after install; prefer recency when
  similarity scores are low.

## Verification
- Ask "what was I just doing?" after some scripted activity → the answer should name
  the right app/window from `query_datastore`.
- With nothing recorded, the skill should admit it has no relevant activity rather
  than invent one.
