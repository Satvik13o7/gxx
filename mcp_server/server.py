"""contour MCP server (stdio) — exposes the local activity store to Hermes Agent.

Tools:
- capture_and_store   : push an observation into the store (Hermes/skill can add notes)
- query_datastore     : semantic search over past activity (the core Q&A path)
- optimize_datastore  : dedup + retention maintenance (PRD feature 4)
- web_search          : relay-backed web search (keeps search keys server-side)
- ask_cloud           : opt-in, text-only cloud escalation via the relay (off by default)

Voice output is intentionally NOT here — Hermes provides it natively when enabled.
CRITICAL: an stdio MCP server must never write to stdout
(it corrupts JSON-RPC); all logging goes to stderr.

The store is opened per-request so this process always sees the daemon's latest
writes and never holds a long-lived lock. The watcher daemon is the primary
high-frequency writer; capture_and_store here is an occasional path.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("contour.mcp")

# Ensure the package root is importable when launched via `uv run server.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datastore import ActivityStore, Observation  # noqa: E402
from datastore.pii import scrub  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from watcher import config  # noqa: E402
from watcher.understand import Understanding  # noqa: E402

from mcp_server.relay_client import RelayClient, RelayError  # noqa: E402

mcp = FastMCP("contour")
_understanding = Understanding()


@contextmanager
def open_store():
    store = ActivityStore(config.data_dir(), dim=config.EMBED_DIM)
    try:
        yield store
    finally:
        store.close()


def _ask_cloud_enabled() -> bool:
    return os.environ.get("CONTOUR_ASK_CLOUD", "").strip().lower() in {"1", "true", "yes", "on"}


@mcp.tool()
def capture_and_store(
    summary: str,
    app: str = "",
    window: str = "",
    salient_text: str = "",
    tags: str = "",
) -> str:
    """Store an observation about the user's activity in the local datastore.

    Use this to record a note or fact worth remembering. Returns the stored row id.
    """
    obs = Observation(
        summary=summary,
        app=app,
        window=window,
        salient_text=salient_text,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        trigger="Manual",
        source="manual",
    )
    with open_store() as store:
        try:
            emb = _understanding.embed(obs.hash_text())
        except Exception as e:  # noqa: BLE001 - store without vector if Ollama down
            log.warning("embed failed, storing without vector: %s", e)
            emb = None
        rid = store.add(obs, embedding=emb)
    return json.dumps({"ok": True, "id": rid})


@mcp.tool()
def query_datastore(query: str, limit: int = 10, since_minutes: int | None = None) -> str:
    """Search the user's recorded on-screen/audio activity by meaning.

    Returns a JSON list of matches (summary, app, window, time, score), most
    relevant first. Use this to answer questions about what the user has been doing.
    """
    since_ts = int(time.time()) - since_minutes * 60 if since_minutes else None
    with open_store() as store:
        try:
            qvec = _understanding.embed(query, is_query=True)
            results = store.query(qvec, limit=limit, since_ts=since_ts)
        except Exception as e:  # noqa: BLE001 - fall back to recency if Ollama down
            log.warning("semantic query failed (%s); returning recent rows", e)
            results = store.recent(limit=limit, since_ts=since_ts)
    slim = [
        {
            "id": r["id"],
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])),
            "app": r.get("app", ""),
            "window": r.get("window", ""),
            "summary": r.get("summary", ""),
            "salient_text": r.get("salient_text", ""),
            "score": round(r["score"], 4) if "score" in r else None,
        }
        for r in results
    ]
    return json.dumps({"count": len(slim), "results": slim}, ensure_ascii=False)


@mcp.tool()
def optimize_datastore(retention_days: int = 30, evict_after_days: int = 3) -> str:
    """Run datastore maintenance: collapse near-duplicates and apply retention.

    Safe to call periodically. Returns a report of how many rows were deduped,
    media-evicted, and hard-deleted.
    """
    with open_store() as store:
        report = store.optimize(retention_days=retention_days, evict_after_days=evict_after_days)
    return json.dumps({"ok": True, **report})


@mcp.tool()
def speak(text: str) -> str:
    """Speak text aloud in the user's ElevenLabs voice (via the relay).

    Call this with your final spoken answer so the user hears it. Keep it to 1-3
    short, natural sentences. Text is PII-scrubbed before it leaves the device.
    """
    from watcher.voice import speak as _speak

    ok = _speak(text)
    return json.dumps({"ok": ok, "spoken": text if ok else ""})


@mcp.tool()
def ask_cloud(question: str) -> str:
    """Escalate a text-only question to a cloud LLM via the relay (OPT-IN).

    Disabled unless enabled at install. Never sends raw screen/audio — the question
    is PII-scrubbed before leaving the device. Use only when the local model and the
    activity store cannot answer.
    """
    if not _ask_cloud_enabled():
        return json.dumps({"ok": False, "error": "ask_cloud is disabled (opt-in at install)"})
    try:
        resp = RelayClient().cloud(scrub(question))
    except RelayError as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps({"ok": True, "answer": resp.get("answer", resp)})


@mcp.tool()
def web_search(query: str, limit: int = 5) -> str:
    """Search the web through the relay (server-side search key).

    Use this for fresh or external information. The query is PII-scrubbed before
    being sent to the relay and authenticated with the device token.
    """
    try:
        lim = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        lim = 5
    try:
        resp = RelayClient().search(scrub(query), num_results=lim)
    except RelayError as e:
        return json.dumps({"ok": False, "error": str(e)})
    return json.dumps({"ok": True, "count": len(resp.get("results", [])), **resp}, ensure_ascii=False)


def main() -> None:
    log.info("contour MCP server starting (data_dir=%s)", config.data_dir())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
