"""Token-authed client for the shared relay (web search + opt-in cloud escalation).

Every payload is PII-scrubbed client-side before it leaves the device (the relay
re-scrubs as a backstop). The relay is addressed with a per-device token, never a
personal API key. Raw screen/audio never passes through here — text only.
"""

from __future__ import annotations

import base64
import logging
import os

from datastore.pii import scrub

log = logging.getLogger("contour.relay")


class RelayError(RuntimeError):
    pass


class RelayClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 20.0):
        self.base_url = (base_url or os.environ.get("CONTOUR_RELAY_URL", "")).rstrip("/")
        self.token = token or os.environ.get("CONTOUR_DEVICE_TOKEN", "")
        self.timeout = timeout

    def _request(self, path: str, payload: dict):
        if not self.base_url:
            raise RelayError("relay not configured (set CONTOUR_RELAY_URL)")
        import httpx  # lazy

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            resp = httpx.post(
                f"{self.base_url}{path}", json=payload, headers=headers, timeout=self.timeout
            )
        except httpx.HTTPError as e:
            raise RelayError(f"relay request failed: {e}") from e
        if resp.status_code == 429:
            raise RelayError("relay rate limit hit; try again shortly")
        if resp.status_code == 401:
            raise RelayError("relay rejected the device token")
        if resp.status_code >= 400:
            raise RelayError(f"relay error {resp.status_code}: {resp.text[:200]}")
        return resp

    def _post(self, path: str, payload: dict) -> dict:
        return self._request(path, payload).json()

    def search(self, query: str, num_results: int = 5) -> dict:
        return self._post("/search", {"query": scrub(query), "num_results": num_results})

    def cloud(self, prompt: str, system: str = "") -> dict:
        return self._post(
            "/cloud", {"prompt": scrub(prompt), "system": scrub(system)}
        )

    def tts(self, text: str, voice_id: str = "") -> tuple[bytes, int]:
        """Synthesize speech via the relay (ElevenLabs). Returns (pcm16_bytes, sample_rate)."""
        resp = self._request("/tts", {"text": scrub(text), "voice_id": voice_id})
        rate = int(resp.headers.get("X-Sample-Rate", "16000"))
        return resp.content, rate

    def vision(self, prompt: str, image: bytes, model: str = "") -> dict:
        return self._post(
            "/vision",
            {
                "prompt": scrub(prompt),
                "image_b64": base64.b64encode(image).decode("ascii"),
                "model": model,
            },
        )
