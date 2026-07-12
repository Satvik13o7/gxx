from datastore.concepts import ConceptStore, context_key
from datastore.store import Observation


def test_context_key_normalizes_app_names():
    assert context_key("VS Code") == "vs_code"
    assert context_key("Terminal.app") == "terminal_app"
    assert context_key("") == "unknown"


def test_concept_store_writes_transcription_and_concept(tmp_path):
    cs = ConceptStore(tmp_path, refresh_secs=1)
    obs = Observation(
        summary="editing project files",
        app="Code",
        transcription="python app.py --debug",
        trigger="AppSwitch",
        source="uia",
        ts=123,
    )

    cs.update(obs)
    # force a refresh on next update to materialize concept.md
    cs._last_refresh["code"] = 0
    cs.update(obs)

    folder = tmp_path / "concepts" / "code"
    trans = (folder / "transcription.md").read_text(encoding="utf-8")
    concept = (folder / "concept.md").read_text(encoding="utf-8")

    assert "python app.py --debug" in trans
    assert "Recent picture" in concept
    assert "editing project files" in concept


def test_concept_store_search_returns_hits(tmp_path):
    cs = ConceptStore(tmp_path, refresh_secs=1)
    obs = Observation(
        summary="debugging terminal command",
        app="Terminal",
        transcription="kubectl get pods",
        trigger="TypingPause",
        source="uia",
        ts=200,
    )
    cs.update(obs)
    cs._last_refresh["terminal"] = 0
    cs.update(obs)

    hits = cs.search("kubectl", limit_contexts=2, hits_per_context=3)
    assert hits
    assert hits[0]["context"] == "terminal"
    assert any("kubectl" in h for h in hits[0]["transcription_hits"])
