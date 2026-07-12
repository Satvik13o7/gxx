import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import relay.app as appmod  # noqa: E402
from relay.app import create_app  # noqa: E402
from relay.auth import AuthStore  # noqa: E402
from relay.ratelimit import RateLimiter  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    box = {}

    def fake_search(query, num_results):
        box["search_query"] = query
        return [{"title": "t", "url": "u", "snippet": "s"}]

    def fake_cloud(prompt, system):
        box["cloud_prompt"] = prompt
        return "cloud answer"

    monkeypatch.setattr(appmod, "do_search", fake_search)
    monkeypatch.setattr(appmod, "do_cloud", fake_cloud)
    return box


def client(tokens=None, limiter=None, store=None):
    store = store or AuthStore(":memory:")
    return TestClient(create_app(tokens=tokens, limiter=limiter, auth_store=store))


def test_health_no_auth_required_in_open_mode(captured):
    r = client(tokens=set()).get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_auth_enforced_when_tokens_configured(captured):
    c = client(tokens={"good"})
    assert c.post("/search", json={"query": "hi"}).status_code == 401  # no token
    assert c.post("/search", json={"query": "hi"}, headers={"Authorization": "Bearer bad"}).status_code == 401
    ok = c.post("/search", json={"query": "hi"}, headers={"Authorization": "Bearer good"})
    assert ok.status_code == 200


def test_search_scrubs_query_server_side(captured):
    c = client(tokens={"t"})
    c.post(
        "/search",
        json={"query": "contact bob@example.com now", "num_results": 2},
        headers={"Authorization": "Bearer t"},
    )
    assert "bob@example.com" not in captured["search_query"]
    assert "[EMAIL]" in captured["search_query"]


def test_cloud_scrubs_prompt_server_side(captured):
    c = client(tokens={"t"})
    r = c.post(
        "/cloud",
        json={"prompt": "my ssn-like 4111111111111111 card"},
        headers={"Authorization": "Bearer t"},
    )
    assert r.json()["answer"] == "cloud answer"
    assert "4111111111111111" not in captured["cloud_prompt"]


def test_rate_limit_returns_429(captured):
    limiter = RateLimiter(max_requests=2, window_secs=60.0)
    c = client(tokens={"t"}, limiter=limiter)
    h = {"Authorization": "Bearer t"}
    assert c.post("/search", json={"query": "a"}, headers=h).status_code == 200
    assert c.post("/search", json={"query": "b"}, headers=h).status_code == 200
    assert c.post("/search", json={"query": "c"}, headers=h).status_code == 429


def test_tts_proxies_elevenlabs_and_scrubs(monkeypatch):
    cap = {}

    def fake_tts(text, voice_id):
        cap["text"] = text
        cap["voice"] = voice_id
        return b"\x01\x02\x03\x04"

    monkeypatch.setattr(appmod, "do_tts", fake_tts)
    r = client(tokens={"t"}).post(
        "/tts",
        json={"text": "call me at bob@example.com", "voice_id": "v9"},
        headers={"Authorization": "Bearer t"},
    )
    assert r.status_code == 200
    assert r.headers["X-Sample-Rate"] == "16000"
    assert r.content == b"\x01\x02\x03\x04"
    assert "bob@example.com" not in cap["text"] and "[EMAIL]" in cap["text"]  # server backstop
    assert cap["voice"] == "v9"


# -- account flow ------------------------------------------------------------
def test_signup_login_and_token_authorizes_search(captured):
    store = AuthStore(":memory:")
    c = client(tokens=set(), store=store)

    r = c.post("/signup", json={"email": "a@b.com", "password": "supersecret"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token.startswith("contour_")

    # the freshly-minted token authorizes an API call
    ok = c.post("/search", json={"query": "hi"}, headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200

    # login returns the same token
    r2 = c.post("/login", json={"email": "a@b.com", "password": "supersecret"})
    assert r2.status_code == 200 and r2.json()["token"] == token


def test_signup_rejects_dupes_and_weak_passwords(captured):
    c = client(tokens=set(), store=AuthStore(":memory:"))
    assert c.post("/signup", json={"email": "x@y.com", "password": "short"}).status_code == 400
    assert c.post("/signup", json={"email": "bad", "password": "longenough"}).status_code == 400
    assert c.post("/signup", json={"email": "x@y.com", "password": "longenough"}).status_code == 200
    dupe = c.post("/signup", json={"email": "x@y.com", "password": "longenough"})
    assert dupe.status_code == 400


def test_login_wrong_password_rejected(captured):
    store = AuthStore(":memory:")
    c = client(tokens=set(), store=store)
    c.post("/signup", json={"email": "a@b.com", "password": "correcthorse"})
    assert c.post("/login", json={"email": "a@b.com", "password": "wrongwrong"}).status_code == 401


def test_dashboard_and_installer_served(captured):
    c = client(tokens={"t"})
    home = c.get("/")
    assert home.status_code == 200 and "contour" in home.text.lower()
    dl = c.get("/download/install.ps1")
    assert dl.status_code == 200
    assert "contour installer" in dl.text.lower() or "param(" in dl.text.lower()
    dl_sh = c.get("/download/install.sh")
    assert dl_sh.status_code == 200
    assert dl_sh.text.startswith("#!/usr/bin/env bash")
    bundle_zip = c.get("/download/client.zip")
    assert bundle_zip.status_code == 200
    assert len(bundle_zip.content) > 1024
    bundle_tgz = c.get("/download/client.tar.gz")
    assert bundle_tgz.status_code == 200
    assert len(bundle_tgz.content) > 1024
