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

    def fake_vision(prompt, image_b64, model=""):
        box["vision_prompt"] = prompt
        box["vision_image_b64"] = image_b64
        box["vision_model"] = model
        return '{"activity":"hosted vision"}'

    monkeypatch.setattr(appmod, "do_search", fake_search)
    monkeypatch.setattr(appmod, "do_cloud", fake_cloud)
    monkeypatch.setattr(appmod, "do_vision", fake_vision)
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


def test_transcriptions_proxies_scribe_and_scrubs(monkeypatch):
    cap = {}

    def fake_stt(audio, filename, model=""):
        cap["audio"] = audio
        cap["filename"] = filename
        cap["model"] = model
        return "my email is bob@example.com"

    monkeypatch.setattr(appmod, "do_stt", fake_stt)
    r = client(tokens={"t"}).post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", b"RIFFfake", "audio/wav")},
        data={"model": "scribe_v1"},
        headers={"Authorization": "Bearer t"},
    )
    assert r.status_code == 200
    assert cap["audio"] == b"RIFFfake"
    assert cap["filename"] == "clip.wav"
    assert cap["model"] == "scribe_v1"
    text = r.json()["text"]
    assert "bob@example.com" not in text and "[EMAIL]" in text  # server backstop


def test_transcriptions_serves_elevenlabs_native_path(monkeypatch):
    # Hermes appends ElevenLabs' own path to the base URL and sends model_id, not model.
    cap = {}

    def fake_stt(audio, filename, model=""):
        cap["model"] = model
        return "native path ok"

    monkeypatch.setattr(appmod, "do_stt", fake_stt)
    r = client(tokens={"t"}).post(
        "/v1/speech-to-text",
        files={"file": ("clip.wav", b"RIFFfake", "audio/wav")},
        data={"model_id": "scribe_v1"},
        headers={"xi-api-key": "t"},
    )
    assert r.status_code == 200
    assert r.json()["text"] == "native path ok"
    assert cap["model"] == "scribe_v1"


def test_transcriptions_accepts_xi_api_key_header(monkeypatch):
    # Hermes' ElevenLabs STT client sends the device token as xi-api-key, not a bearer token.
    monkeypatch.setattr(appmod, "do_stt", lambda audio, filename, model="": "hello there")
    r = client(tokens={"t"}).post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", b"RIFFfake", "audio/wav")},
        headers={"xi-api-key": "t"},
    )
    assert r.status_code == 200
    assert r.json()["text"] == "hello there"


def test_transcriptions_requires_auth():
    r = client(tokens={"t"}).post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", b"RIFFfake", "audio/wav")},
    )
    assert r.status_code == 401


def test_transcriptions_rejects_empty_upload(monkeypatch):
    monkeypatch.setattr(appmod, "do_stt", lambda *a, **k: pytest.fail("should not call provider"))
    r = client(tokens={"t"}).post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", b"", "audio/wav")},
        headers={"Authorization": "Bearer t"},
    )
    assert r.status_code == 400


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


def test_vision_proxies_and_scrubs(captured):
    c = client(tokens={"t"})
    r = c.post(
        "/vision",
        json={"prompt": "contact me at bob@example.com", "image_b64": "QUJD", "model": "m1"},
        headers={"Authorization": "Bearer t"},
    )
    assert r.status_code == 200
    assert r.json()["content"] == '{"activity":"hosted vision"}'
    assert "bob@example.com" not in captured["vision_prompt"]
    assert "[EMAIL]" in captured["vision_prompt"]
    assert captured["vision_image_b64"] == "QUJD"
    assert captured["vision_model"] == "m1"


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


def test_forgot_password_issues_token_and_never_leaks_existence(monkeypatch, captured):
    store = AuthStore(":memory:")
    c = client(tokens=set(), store=store)
    c.post("/signup", json={"email": "a@b.com", "password": "correcthorse"})

    sent = {}
    monkeypatch.setattr(
        appmod, "send_reset_email", lambda email, link: sent.update(email=email, link=link)
    )

    known = c.post("/forgot-password", json={"email": "a@b.com"})
    unknown = c.post("/forgot-password", json={"email": "nope@nowhere.com"})

    assert known.status_code == 200 and unknown.status_code == 200
    assert known.json()["message"] == unknown.json()["message"]
    assert sent["email"] == "a@b.com"
    assert "reset_token=" in sent["link"]


def test_reset_password_flow_rotates_credentials_and_is_single_use(monkeypatch, captured):
    store = AuthStore(":memory:")
    c = client(tokens=set(), store=store)
    signup = c.post("/signup", json={"email": "a@b.com", "password": "correcthorse"})
    old_token = signup.json()["token"]

    sent = {}
    monkeypatch.setattr(
        appmod, "send_reset_email", lambda email, link: sent.update(email=email, link=link)
    )
    c.post("/forgot-password", json={"email": "a@b.com"})
    reset_token = sent["link"].split("reset_token=")[1]

    r = c.post("/reset-password", json={"reset_token": reset_token, "new_password": "newpassword2"})
    assert r.status_code == 200
    new_token = r.json()["token"]
    assert new_token != old_token

    # old password no longer works, new one does
    assert c.post("/login", json={"email": "a@b.com", "password": "correcthorse"}).status_code == 401
    assert c.post("/login", json={"email": "a@b.com", "password": "newpassword2"}).status_code == 200

    # old device token is invalidated
    assert c.post(
        "/search", json={"query": "hi"}, headers={"Authorization": f"Bearer {old_token}"}
    ).status_code == 401

    # reusing the same reset token fails
    again = c.post("/reset-password", json={"reset_token": reset_token, "new_password": "yetanother3"})
    assert again.status_code == 400


def test_reset_password_rejects_bad_or_short_password(captured):
    store = AuthStore(":memory:")
    c = client(tokens=set(), store=store)
    assert c.post(
        "/reset-password", json={"reset_token": "garbage", "new_password": "longenough"}
    ).status_code == 400

    c.post("/signup", json={"email": "a@b.com", "password": "correcthorse"})
    real_token = store.create_reset_token("a@b.com")
    assert c.post(
        "/reset-password", json={"reset_token": real_token, "new_password": "short"}
    ).status_code == 400


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
