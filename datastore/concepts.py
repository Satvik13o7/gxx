"""Context concept files for fast human-readable memory summaries.

Each context key (usually app-derived: terminal, code, chrome, etc.) keeps:
- ``transcription.md``: append-only event log with exact-ish text.
- ``concept.md``: periodically refreshed compact summary for quick grounding.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path

from datastore.store import Observation


def context_key(app: str) -> str:
    raw = (app or "unknown").strip().lower()
    key = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return key or "unknown"


class ConceptStore:
    def __init__(self, data_dir: str | Path, refresh_secs: int = 120):
        self.base = Path(data_dir) / "concepts"
        self.base.mkdir(parents=True, exist_ok=True)
        self.refresh_secs = max(30, int(refresh_secs))
        self._last_refresh: dict[str, float] = {}

    def update(self, obs: Observation) -> None:
        key = context_key(obs.app)
        folder = self.base / key
        folder.mkdir(parents=True, exist_ok=True)

        ts = int(obs.ts if obs.ts is not None else time.time())
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        trans = (obs.transcription or obs.salient_text or "").strip()
        summary = (obs.summary or "").strip()

        with (folder / "transcription.md").open("a", encoding="utf-8") as f:
            f.write(f"\n## {stamp}\n")
            f.write(f"- source: {obs.source}\n")
            f.write(f"- trigger: {obs.trigger}\n")
            if summary:
                f.write(f"- summary: {summary}\n")
            if trans:
                clean = " ".join(trans.split())
                f.write(f"- transcription: {clean}\n")

        now = time.time()
        if now - self._last_refresh.get(key, 0.0) >= self.refresh_secs:
            self._refresh_concept(folder, key)
            self._last_refresh[key] = now

    def _refresh_concept(self, folder: Path, key: str) -> None:
        path = folder / "transcription.md"
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        summaries = [ln.replace("- summary:", "", 1).strip() for ln in lines if ln.startswith("- summary:")]
        transcripts = [
            ln.replace("- transcription:", "", 1).strip()
            for ln in lines
            if ln.startswith("- transcription:")
        ]
        top_terms = self._top_terms(" ".join(transcripts[-200:]))

        concept = folder / "concept.md"
        with concept.open("w", encoding="utf-8") as f:
            f.write(f"# {key}\n\n")
            f.write(f"- updated_at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"- entries: {len([ln for ln in lines if ln.startswith('## ')])}\n")
            if summaries:
                f.write("\n## Recent picture\n")
                for s in summaries[-5:]:
                    f.write(f"- {s}\n")
            if top_terms:
                f.write("\n## Top keywords\n")
                for t, n in top_terms:
                    f.write(f"- {t} ({n})\n")

    def search(self, query: str, limit_contexts: int = 3, hits_per_context: int = 5) -> list[dict]:
        q = (query or "").strip().lower()
        if not q:
            return []

        matches: list[dict] = []
        for folder in sorted(self.base.iterdir()) if self.base.exists() else []:
            if not folder.is_dir():
                continue

            concept_snippet = ""
            concept_path = folder / "concept.md"
            if concept_path.exists():
                for line in concept_path.read_text(encoding="utf-8").splitlines():
                    if q in line.lower() and line.strip():
                        concept_snippet = line.strip()
                        break

            trans_hits: list[str] = []
            trans_path = folder / "transcription.md"
            if trans_path.exists():
                for line in trans_path.read_text(encoding="utf-8").splitlines():
                    if q in line.lower() and line.strip().startswith("- transcription:"):
                        trans_hits.append(line.replace("- transcription:", "", 1).strip())
                        if len(trans_hits) >= hits_per_context:
                            break

            if concept_snippet or trans_hits:
                matches.append(
                    {
                        "context": folder.name,
                        "concept_snippet": concept_snippet,
                        "transcription_hits": trans_hits,
                        "_score": (2 if concept_snippet else 0) + len(trans_hits),
                    }
                )

        matches.sort(key=lambda m: m.get("_score", 0), reverse=True)
        for m in matches:
            m.pop("_score", None)
        return matches[: max(1, int(limit_contexts))]

    @staticmethod
    def _top_terms(text: str) -> list[tuple[str, int]]:
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text.lower())
        stop = {
            "the",
            "and",
            "with",
            "that",
            "this",
            "from",
            "you",
            "for",
            "are",
            "was",
            "have",
            "your",
            "screen",
            "summary",
            "transcription",
        }
        c = Counter(w for w in words if w not in stop)
        return c.most_common(10)
