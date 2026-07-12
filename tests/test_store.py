import numpy as np
import pytest

from datastore import ActivityStore, Observation

DIM = 768


def _vec(seed: int) -> np.ndarray:
    """Deterministic pseudo-embedding."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal(DIM).astype(np.float32)


@pytest.fixture
def store(tmp_path):
    s = ActivityStore(tmp_path, dim=DIM, backend="numpy")
    yield s
    s.close()


def test_add_and_semantic_query_orders_by_similarity(store):
    v_code = _vec(1)
    v_video = _vec(2)
    id_code = store.add(
        Observation(summary="editing main.py in VS Code", trigger="AppSwitch"),
        embedding=v_code,
    )
    id_video = store.add(
        Observation(summary="watching a youtube video", trigger="AppSwitch"),
        embedding=v_video,
    )
    # query close to the code vector => code row ranks first
    results = store.query(v_code + 0.01 * _vec(3), limit=2)
    assert results[0]["id"] == id_code
    assert {r["id"] for r in results} == {id_code, id_video}
    assert results[0]["score"] >= results[1]["score"]


def test_soft_trigger_dedups_identical_content(store):
    obs = Observation(summary="same screen", trigger="TypingPause")
    id1 = store.add(obs, embedding=_vec(1))
    id2 = store.add(Observation(summary="same screen", trigger="KeyPress"), embedding=_vec(1))
    assert id1 == id2  # deduped, no new row
    assert len(store.recent()) == 1


def test_hard_trigger_bypasses_dedup(store):
    store.add(Observation(summary="same screen", trigger="TypingPause"), embedding=_vec(1))
    store.add(Observation(summary="same screen", trigger="AppSwitch"), embedding=_vec(1))
    assert len(store.recent()) == 2  # hard checkpoint always inserts


def test_heartbeat_forces_write_after_floor(store):
    store.add(Observation(summary="idle screen", trigger="TypingPause", ts=1000), embedding=_vec(1))
    # 40s later, same content, soft trigger, heartbeat=30 => must insert
    store.add(
        Observation(summary="idle screen", trigger="TypingPause", ts=1040),
        embedding=_vec(1),
        heartbeat_secs=30,
    )
    assert len(store.recent()) == 2


def test_optimize_collapses_duplicates_and_prunes_old(store):
    now = 2_000_000_000
    day = 86400
    # two consecutive identical (hard trigger so both inserted), plus an old one
    store.add(Observation(summary="dup", trigger="AppSwitch", ts=now), embedding=_vec(1))
    store.add(Observation(summary="dup", trigger="AppSwitch", ts=now), embedding=_vec(1))
    store.add(Observation(summary="ancient", trigger="AppSwitch", ts=now - 40 * day), embedding=_vec(2))
    assert len(store.recent(limit=99)) == 3

    report = store.optimize(retention_days=30, evict_after_days=3, now=now)
    ids = {r["summary"] for r in store.recent(limit=99)}
    assert report["deduped"] >= 1
    assert report["hard_deleted"] >= 1
    assert "ancient" not in ids  # pruned past retention
    assert "dup" in ids  # one anchor kept


def test_persistence_round_trip(tmp_path):
    v = _vec(7)
    with ActivityStore(tmp_path, dim=DIM, backend="numpy") as s:
        rid = s.add(Observation(summary="persist me", trigger="Manual"), embedding=v)
    # reopen
    with ActivityStore(tmp_path, dim=DIM, backend="numpy") as s2:
        results = s2.query(v, limit=1)
        assert results and results[0]["id"] == rid
        assert results[0]["summary"] == "persist me"


def test_exact_query_matches_transcription(store):
    store.add(
        Observation(
            summary="terminal session",
            salient_text="",
            transcription="kubectl get pods --all-namespaces",
            trigger="AppSwitch",
        ),
        embedding=_vec(11),
    )
    out = store.query_exact("kubectl pods", limit=5)
    assert out
    assert "kubectl" in out[0]["transcription"]


def test_hybrid_query_merges_exact_and_semantic(store):
    v_code = _vec(21)
    v_misc = _vec(22)
    code_id = store.add(
        Observation(summary="editing app.py", transcription="python app.py", trigger="AppSwitch"),
        embedding=v_code,
    )
    grep_id = store.add(
        Observation(summary="terminal", transcription="docker compose up", trigger="AppSwitch"),
        embedding=v_misc,
    )

    results = store.query_hybrid("docker", v_code, limit=5)
    ids = [r["id"] for r in results]
    assert code_id in ids
    assert grep_id in ids
