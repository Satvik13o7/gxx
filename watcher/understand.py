"""Local multimodal understanding + embeddings via Ollama.

Two responsibilities:
- ``describe`` — turn a screenshot (+ any UIA text) into a compact structured
  summary using the local vision model (Gemma 4). Runs only on the fallback path
  (thin/absent accessibility text or a hard checkpoint needing enrichment).
- ``embed`` — produce a local text embedding (nomic-embed-text) for the store.

The Ollama client is created lazily and can be injected for tests. All network
calls stay on-device (localhost Ollama); nothing here touches the relay.
"""

from __future__ import annotations

import json
import logging
import re
import base64
import os

import numpy as np

from . import config

log = logging.getLogger("contour.understand")

_DESCRIBE_PROMPT = """You observe a user's screen to build a private activity log.
Given the screenshot (and any extracted on-screen text), reply with ONE compact JSON
object and nothing else:
{"app_or_context": str, "activity": str, "salient_text": str,
 "entities": [str], "is_actionable": bool}
- "activity": one concise sentence describing what the user is doing.
- "salient_text": the few most important on-screen strings (errors, titles, names).
- "is_actionable": true only if something clearly needs attention (an error, a
  deadline, a question awaiting an answer). Be conservative.
Keep it short."""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class Understanding:
    def __init__(self, client=None):
        self._client = client  # ollama.Client-like; created lazily if None

    @property
    def client(self):
        if self._client is None:
            import ollama  # imported lazily so the package loads without ollama

            self._client = ollama.Client(host=config.OLLAMA_HOST)
        return self._client

    # -- vision ---------------------------------------------------------------
    def describe(self, image: bytes | str, uia_text: str = "") -> dict:
        """Return a structured summary dict. ``image`` = PNG bytes or a file path."""
        if config.INFERENCE_MODE == "hosted" and config.HOSTED_INFERENCE_URL:
            raw = self._describe_hosted(image, uia_text)
            return self._parse(raw)
        content = _DESCRIBE_PROMPT
        if uia_text:
            content += f"\n\nExtracted on-screen text (may be partial):\n{uia_text[:4000]}"
        resp = self.client.chat(
            model=config.VISION_MODEL,
            messages=[{"role": "user", "content": content, "images": [image]}],
            options={"temperature": 0.2},
        )
        raw = resp["message"]["content"]
        return self._parse(raw)

    def _describe_hosted(self, image: bytes | str, uia_text: str = "") -> str:
        import httpx
        from mcp_server.relay_client import RelayClient, RelayError

        prompt = _DESCRIBE_PROMPT
        if uia_text:
            prompt += f"\n\nExtracted on-screen text (may be partial):\n{uia_text[:4000]}"

        if isinstance(image, bytes):
            img_bytes = image
        else:
            with open(image, "rb") as f:
                img_bytes = f.read()

        use_relay = config.HOSTED_PROVIDER == "relay" or not config.HOSTED_INFERENCE_URL
        if use_relay and os.environ.get("CONTOUR_RELAY_URL"):
            try:
                out = RelayClient().vision(prompt=prompt, image=img_bytes, model=config.HOSTED_VISION_MODEL)
                return out.get("content", "")
            except RelayError as e:
                log.warning("relay vision failed (%s), falling back to direct hosted/local", e)

        data_url = "data:image/png;base64," + base64.b64encode(img_bytes).decode("ascii")

        base = config.HOSTED_INFERENCE_URL.rstrip("/")
        if base.endswith("/v1/openai"):
            url = f"{base}/chat/completions"
        elif base.endswith("/v1"):
            url = f"{base}/chat/completions"
        else:
            url = f"{base}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        api_key = config.HOSTED_INFERENCE_KEY
        if not api_key and config.HOSTED_PROVIDER == "deepinfra":
            api_key = config.DEEPINFRA_API_KEY
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": config.HOSTED_VISION_MODEL,
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
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=45)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            log.warning("hosted describe failed (%s), falling back to local Ollama", e)
            content = _DESCRIBE_PROMPT
            if uia_text:
                content += f"\n\nExtracted on-screen text (may be partial):\n{uia_text[:4000]}"
            resp = self.client.chat(
                model=config.VISION_MODEL,
                messages=[{"role": "user", "content": content, "images": [image]}],
                options={"temperature": 0.2},
            )
            return resp["message"]["content"]

    @staticmethod
    def _parse(raw: str) -> dict:
        out = {
            "app_or_context": "",
            "activity": "",
            "salient_text": "",
            "entities": [],
            "is_actionable": False,
        }
        m = _JSON_RE.search(raw or "")
        if m:
            try:
                data = json.loads(m.group(0))
                for k in out:
                    if k in data:
                        out[k] = data[k]
            except json.JSONDecodeError:
                log.warning("describe: model did not return valid JSON; using raw text")
                out["activity"] = (raw or "").strip()[:400]
        else:
            out["activity"] = (raw or "").strip()[:400]
        # coerce types defensively
        out["entities"] = [str(e) for e in (out.get("entities") or [])][:20]
        out["is_actionable"] = bool(out.get("is_actionable"))
        return out

    # -- embeddings -----------------------------------------------------------
    def embed(self, text: str, is_query: bool = False) -> np.ndarray:
        """Embed text locally. Uses nomic's task prefixes for better retrieval."""
        prefix = "search_query: " if is_query else "search_document: "
        resp = self.client.embed(
            model=config.EMBED_MODEL,
            input=prefix + (text or ""),
            options={"num_ctx": 8192},
        )
        vecs = resp.get("embeddings") or resp.get("embedding")
        vec = vecs[0] if isinstance(vecs[0], (list, tuple)) else vecs
        return np.asarray(vec, dtype=np.float32)
