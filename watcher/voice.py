"""Speak text aloud via our ElevenLabs voice (proxied through the relay).

Replaces Hermes' native TTS: both the MCP `speak` tool and the proactive gate call
``speak()`` here, so all spoken output uses our ElevenLabs voice while the API key
stays on the relay. The text is PII-scrubbed before it leaves the device (again in
RelayClient.tts, and once more server-side at the relay).

Playback and the relay client are injectable so this unit-tests without audio
hardware or a network.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger("contour.voice")


def play_pcm16(data: bytes, samplerate: int = 16000) -> bool:
    """Play raw mono 16-bit little-endian PCM. Returns False if audio is unavailable."""
    if not data:
        return False
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001
        log.info("audio playback unavailable (%s); cannot speak", e)
        return False
    try:
        samples = np.frombuffer(data, dtype="<i2")
        sd.play(samples, samplerate=samplerate)
        sd.wait()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("playback failed: %s", e)
        return False


def speak(text: str, relay=None, player=play_pcm16, voice_id: str = "") -> bool:
    """Synthesize ``text`` via the relay and play it. Best-effort; never raises."""
    text = (text or "").strip()
    if not text:
        return False
    if relay is None:
        from mcp_server.relay_client import RelayClient

        relay = RelayClient()
    audio = b""
    rate = 16000
    for attempt in range(3):
        try:
            audio, rate = relay.tts(text, voice_id=voice_id)
            break
        except Exception as e:  # noqa: BLE001 - speaking must never crash the caller
            if attempt == 2:
                log.warning("tts request failed after retries: %s", e)
                return False
            time.sleep(0.25 * (attempt + 1))
    return player(audio, rate)
