"""Fish Audio text-to-speech backend.

Uses the Fish Audio REST API (s2-pro) for high-quality voice synthesis.
Requires FISH_API_KEY and a voice ``reference_id`` from fish.audio.
"""

from __future__ import annotations

import os
from typing import List

import httpx

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

_FISH_API_BASE = "https://api.fish.audio"


def _fish_synthesize(
    api_key: str,
    text: str,
    reference_id: str,
    *,
    model: str = "s2-pro",
    output_format: str = "mp3",
    speed: float = 1.0,
) -> bytes:
    """Call Fish Audio TTS and return raw audio bytes."""
    payload: dict = {
        "text": text,
        "format": output_format,
        "latency": "normal",
    }
    if reference_id:
        payload["reference_id"] = reference_id
    if speed != 1.0:
        payload["prosody"] = {"speed": speed, "volume": 0, "normalize_loudness": True}

    resp = httpx.post(
        f"{_FISH_API_BASE}/v1/tts",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "model": model,
        },
        json=payload,
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.content


@TTSRegistry.register("fish_audio")
class FishAudioTTSBackend(TTSBackend):
    """Fish Audio TTS backend — expressive cloned voices via s2-pro."""

    backend_id = "fish_audio"

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = "s2-pro",
        default_voice_id: str = "",
    ) -> None:
        self._api_key = api_key or os.environ.get("FISH_API_KEY", "")
        self._model = model or os.environ.get("FISH_TTS_MODEL", "s2-pro")
        self._default_voice_id = default_voice_id or os.environ.get(
            "FISH_VOICE_ID", ""
        )

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "mp3",
    ) -> TTSResult:
        if not self._api_key:
            raise RuntimeError("FISH_API_KEY not set")

        ref = voice_id or self._default_voice_id
        if not ref:
            raise RuntimeError(
                "Fish Audio voice not configured — set speech.tts_voice_id "
                "to your fish.audio model reference_id"
            )

        audio = _fish_synthesize(
            self._api_key,
            text,
            ref,
            model=self._model,
            output_format=output_format,
            speed=speed,
        )

        return TTSResult(
            audio=audio,
            format=output_format,
            voice_id=ref,
            metadata={"backend": "fish_audio", "model": self._model},
        )

    def available_voices(self) -> List[str]:
        if not self._api_key:
            return []
        try:
            resp = httpx.get(
                f"{_FISH_API_BASE}/model",
                headers={"Authorization": f"Bearer {self._api_key}"},
                params={"page_size": 50, "self": True},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items") or data.get("models") or []
            ids = [item.get("_id") or item.get("id") for item in items]
            voices = [v for v in ids if v]
            if voices:
                return voices
        except Exception:
            pass
        if self._default_voice_id:
            return [self._default_voice_id]
        return []

    def health(self) -> bool:
        return bool(self._api_key and self._default_voice_id)
