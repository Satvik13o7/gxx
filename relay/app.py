"""contour relay — FastAPI proxy for web search + opt-in cloud LLM.

Zero-key client story: the client authenticates with a per-device token and never
holds a provider key. The relay holds project-owned keys in env vars, applies a
server-side PII backstop, and rate-limits per token.

Endpoints:
    GET  /health            -> liveness + which providers are configured
    POST /search  {query,num_results}  -> {results:[{title,url,snippet}]}
    POST /cloud   {prompt,system}       -> {answer}   (opt-in on the client)
    POST /tts     {text,voice_id}        -> raw PCM16 audio (ElevenLabs)
    POST /v1/audio/transcriptions        -> {text}  (OpenAI-shaped; proxies ElevenLabs Scribe)

Provider calls (`do_search`, `do_cloud`) are module-level so tests can monkeypatch
them without real keys or network.
"""

from __future__ import annotations

import logging
import os
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

# ElevenLabs PCM output format the client knows how to play (mono 16-bit LE).
TTS_SAMPLE_RATE = 16000
TTS_OUTPUT_FORMAT = "pcm_16000"

from relay.auth import AuthError, AuthStore
from relay.mailer import send_reset_email
from relay.pii import scrub
from relay.ratelimit import RateLimiter

log = logging.getLogger("contour.relay.app")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WEBSITE = _PROJECT_ROOT / "website" / "index.html"
_INSTALLER_PS1 = _PROJECT_ROOT / "install" / "install.ps1"
_INSTALLER_SH = _PROJECT_ROOT / "install" / "install.sh"

_BUNDLE_PATHS = [
    "datastore",
    "mcp_server",
    "watcher",
    "skill",
    "install",
    "pyproject.toml",
    "README.md",
]


def _iter_bundle_files() -> list[Path]:
    files: list[Path] = []
    for rel in _BUNDLE_PATHS:
        p = _PROJECT_ROOT / rel
        if not p.exists():
            continue
        if p.is_file():
            files.append(p)
            continue
        for child in p.rglob("*"):
            if child.is_dir():
                continue
            if "__pycache__" in child.parts:
                continue
            files.append(child)
    return files


def _bundle_zip_bytes() -> bytes:
    files = _iter_bundle_files()
    if not files:
        raise HTTPException(status_code=503, detail="client bundle unavailable")
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src in files:
            arc = src.relative_to(_PROJECT_ROOT)
            zf.write(src, arcname=str(arc))
    return buf.getvalue()


def _bundle_targz_bytes() -> bytes:
    files = _iter_bundle_files()
    if not files:
        raise HTTPException(status_code=503, detail="client bundle unavailable")
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for src in files:
            arc = src.relative_to(_PROJECT_ROOT)
            tf.add(src, arcname=str(arc), recursive=False)
    return buf.getvalue()


class SearchReq(BaseModel):
    query: str
    num_results: int = 5


class CloudReq(BaseModel):
    prompt: str
    system: str = ""


class TTSReq(BaseModel):
    text: str
    voice_id: str = ""


class VisionReq(BaseModel):
    prompt: str
    image_b64: str
    model: str = ""


class Credentials(BaseModel):
    email: str
    password: str


class ForgotPasswordReq(BaseModel):
    email: str


class ResetPasswordReq(BaseModel):
    reset_token: str
    new_password: str


def _allowed_tokens() -> set[str]:
    raw = os.environ.get("CONTOUR_RELAY_TOKENS", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


# -- provider adapters (monkeypatched in tests) ------------------------------
def do_search(query: str, num_results: int) -> list[dict]:
    """Call the configured search provider (Linkup preferred, then Tavily, then Serper)."""
    import httpx

    linkup = os.environ.get("LINKUP_API_KEY")
    if linkup:
        r = httpx.post(
            "https://api.linkup.so/v1/search",
            headers={"Authorization": f"Bearer {linkup}"},
            json={
                "q": query,
                "depth": os.environ.get("LINKUP_DEPTH", "standard"),  # fast|standard|deep
                "outputType": "searchResults",
            },
            timeout=30,
        )
        r.raise_for_status()
        results = [x for x in r.json().get("results", []) if x.get("type") != "image"]
        return [
            {"title": x.get("name", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
            for x in results[:num_results]
        ]

    tavily = os.environ.get("TAVILY_API_KEY")
    if tavily:
        r = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": tavily, "query": query, "max_results": num_results},
            timeout=20,
        )
        r.raise_for_status()
        return [
            {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
            for x in r.json().get("results", [])
        ]

    serper = os.environ.get("SERPER_API_KEY")
    if serper:
        r = httpx.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": serper},
            json={"q": query, "num": num_results},
            timeout=20,
        )
        r.raise_for_status()
        return [
            {"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
            for x in r.json().get("organic", [])
        ]

    raise HTTPException(status_code=503, detail="no search provider configured")


def do_cloud(prompt: str, system: str) -> str:
    """Call the configured cloud LLM (OpenAI)."""
    import httpx

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="no cloud provider configured")
    model = os.environ.get("CONTOUR_CLOUD_MODEL", "gpt-4o-mini")
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": messages},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def do_tts(text: str, voice_id: str) -> bytes:
    """Synthesize speech via ElevenLabs; returns raw PCM16 mono @ 16 kHz."""
    import httpx

    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="no TTS provider configured")
    vid = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    model = os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
        params={"output_format": TTS_OUTPUT_FORMAT},
        headers={"xi-api-key": key, "content-type": "application/json"},
        json={"text": text, "model_id": model},
        timeout=60,
    )
    r.raise_for_status()
    return r.content


def do_stt(audio: bytes, filename: str, model: str = "") -> str:
    """Transcribe audio via ElevenLabs Scribe; returns the recognized text."""
    import httpx

    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="no STT provider configured")
    mdl = model or os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v1")
    r = httpx.post(
        "https://api.elevenlabs.io/v1/speech-to-text",
        headers={"xi-api-key": key},
        files={"file": (filename or "audio.wav", audio, "application/octet-stream")},
        data={"model_id": mdl},
        timeout=120,
    )
    r.raise_for_status()
    return r.json().get("text", "")


def do_vision(prompt: str, image_b64: str, model: str = "") -> str:
    """Run hosted vision inference via backend-managed provider keys."""
    import httpx

    key = os.environ.get("DEEPINFRA_API_KEY") or os.environ.get("CONTOUR_HOSTED_INFERENCE_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="no hosted inference key configured")
    base = (
        os.environ.get("DEEPINFRA_BASE_URL")
        or os.environ.get("CONTOUR_HOSTED_INFERENCE_URL")
        or "https://api.deepinfra.com/v1/openai"
    ).rstrip("/")
    url = f"{base}/chat/completions"
    mdl = model or os.environ.get("CONTOUR_HOSTED_VISION_MODEL", "google/gemma-4-26B-A4B-it")
    data_url = "data:image/png;base64," + image_b64
    payload = {
        "model": mdl,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.2,
    }
    r = httpx.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def create_app(
    tokens: set[str] | None = None,
    limiter: RateLimiter | None = None,
    auth_store: AuthStore | None = None,
) -> FastAPI:
    app = FastAPI(title="contour relay", version="0.1.0")
    allowed = tokens if tokens is not None else _allowed_tokens()
    store = auth_store if auth_store is not None else AuthStore()
    rl = limiter or RateLimiter(
        max_requests=int(os.environ.get("CONTOUR_RELAY_RATE", "120")), window_secs=60.0
    )
    # auth is enforced when either a static allowlist or accounts exist
    require_auth = bool(allowed) or store is not None

    def _check(token: str) -> str:
        if require_auth:
            ok = (token in allowed) or (store is not None and store.valid_token(token))
            if not ok:
                raise HTTPException(status_code=401, detail="invalid device token")
        if not rl.allow(token or "anon"):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        return token

    def auth(authorization: str = Header(default="")) -> str:
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        return _check(token)

    def auth_compat(
        authorization: str = Header(default=""),
        xi_api_key: str = Header(default="", alias="xi-api-key"),
    ) -> str:
        """Auth for the OpenAI/ElevenLabs-shaped routes.

        Third-party STT clients send the device token in the provider's own key header
        (``xi-api-key``) rather than as a bearer token, so accept either form.
        """
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        return _check(token or xi_api_key.strip())

    @app.post("/signup")
    def signup(creds: Credentials) -> dict:
        try:
            token = store.signup(creds.email, creds.password)
        except AuthError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"ok": True, "token": token}

    @app.post("/login")
    def login(creds: Credentials) -> dict:
        try:
            token = store.login(creds.email, creds.password)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        return {"ok": True, "token": token}

    @app.post("/forgot-password")
    def forgot_password(req: ForgotPasswordReq, request: Request) -> dict:
        reset_token = store.create_reset_token(req.email)
        if reset_token:
            base = str(request.base_url).rstrip("/")
            reset_link = f"{base}/?reset_token={reset_token}"
            try:
                send_reset_email(req.email, reset_link)
            except Exception:
                log.exception("failed to send reset email to %s", req.email)
        # Always return the same response, whether or not the email exists,
        # so this endpoint can't be used to enumerate registered accounts.
        return {"ok": True, "message": "If that email has an account, a reset link has been sent."}

    @app.post("/reset-password")
    def reset_password(req: ResetPasswordReq) -> dict:
        try:
            token = store.reset_password(req.reset_token, req.new_password)
        except AuthError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"ok": True, "token": token}

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        if _WEBSITE.exists():
            return _WEBSITE.read_text(encoding="utf-8")
        return "<h1>contour relay</h1><p>dashboard not found</p>"

    @app.get("/docs/{name}", response_class=HTMLResponse)
    def docs(name: str) -> str:
        allowed = {"steps": "STEPS.md", "user-flow": "USER_FLOW.md", "readme": "README.md"}
        rel = allowed.get(name)
        if rel is None:
            raise HTTPException(status_code=404, detail="doc not found")
        path = _PROJECT_ROOT / rel
        if not path.exists():
            raise HTTPException(status_code=404, detail="doc not found")
        text = path.read_text(encoding="utf-8")
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<!doctype html><meta charset='utf-8'><title>{rel}</title><pre style='white-space:pre-wrap;font-family:ui-monospace,monospace;max-width:860px;margin:40px auto;padding:0 20px;line-height:1.6'>{escaped}</pre>"

    @app.get("/download/install.ps1")
    def download_installer():
        if not _INSTALLER_PS1.exists():
            raise HTTPException(status_code=404, detail="installer not found")
        return FileResponse(
            _INSTALLER_PS1, media_type="text/plain", filename="install.ps1"
        )

    @app.get("/download/install.sh")
    def download_installer_sh():
        if not _INSTALLER_SH.exists():
            raise HTTPException(status_code=404, detail="installer not found")
        return FileResponse(
            _INSTALLER_SH, media_type="text/plain", filename="install.sh"
        )

    @app.get("/download/client.zip")
    def download_client_zip() -> Response:
        payload = _bundle_zip_bytes()
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="contour-client.zip"'},
        )

    @app.get("/download/client.tar.gz")
    def download_client_targz() -> Response:
        payload = _bundle_targz_bytes()
        return Response(
            content=payload,
            media_type="application/gzip",
            headers={"Content-Disposition": 'attachment; filename="contour-client.tar.gz"'},
        )

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "providers": {
                "search": bool(
                    os.environ.get("LINKUP_API_KEY")
                    or os.environ.get("TAVILY_API_KEY")
                    or os.environ.get("SERPER_API_KEY")
                ),
                "cloud": bool(os.environ.get("OPENAI_API_KEY")),
                "tts": bool(os.environ.get("ELEVENLABS_API_KEY")),
                "stt": bool(os.environ.get("ELEVENLABS_API_KEY")),
            },
            "auth_required": bool(allowed),
        }

    @app.post("/search")
    def search(req: SearchReq, token: str = Depends(auth)) -> dict:
        results = do_search(scrub(req.query), req.num_results)  # server-side backstop scrub
        return {"results": results}

    @app.post("/cloud")
    def cloud(req: CloudReq, token: str = Depends(auth)) -> dict:
        answer = do_cloud(scrub(req.prompt), scrub(req.system))
        return {"answer": answer}

    @app.post("/tts")
    def tts(req: TTSReq, token: str = Depends(auth)) -> Response:
        audio = do_tts(scrub(req.text), req.voice_id)  # server-side scrub backstop
        return Response(
            content=audio,
            media_type="application/octet-stream",
            headers={"X-Sample-Rate": str(TTS_SAMPLE_RATE)},
        )

    @app.post("/vision")
    def vision(req: VisionReq, token: str = Depends(auth)) -> dict:
        content = do_vision(scrub(req.prompt), req.image_b64, req.model)
        return {"content": content}

    # Drop-in transcription endpoint: clients are pointed at <relay>/v1 in place of the
    # provider's own base URL, so we answer on both the ElevenLabs native path
    # (/v1/speech-to-text, what Hermes calls) and the OpenAI-compatible one
    # (/v1/audio/transcriptions). Audio is proxied to Scribe; the key stays on the relay.
    @app.post("/v1/speech-to-text")
    @app.post("/v1/audio/transcriptions")
    async def transcriptions(
        file: UploadFile = File(...),
        model: str = Form(default=""),  # OpenAI field name
        model_id: str = Form(default=""),  # ElevenLabs field name
        token: str = Depends(auth_compat),
    ) -> dict:
        audio = await file.read()
        if not audio:
            raise HTTPException(status_code=400, detail="empty audio upload")
        text = do_stt(audio, file.filename or "audio.wav", model or model_id)
        return {"text": scrub(text)}  # server-side backstop before it goes back to the device

    return app


app = create_app()
