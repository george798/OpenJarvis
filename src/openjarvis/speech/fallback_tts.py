"""Fallback TTS wrapper — cloud primary with automatic local fallback.

Wraps a primary (usually cloud) TTS backend and transparently falls back to
a local backend (Kokoro) when the primary raises, so voice never breaks even
if an API key is missing/invalid or the account is out of credits.
"""

from __future__ import annotations

import logging
import time
from typing import List

from openjarvis.speech.tts import TTSBackend, TTSResult

logger = logging.getLogger(__name__)

# After a primary failure, skip the primary for this long so a dead cloud
# API doesn't add a failing round-trip to every synthesis call.
_FAILURE_COOLDOWN_SECONDS = 300.0


class FallbackTTSBackend(TTSBackend):
    """Primary TTS backend with a local fallback on any synthesis failure."""

    def __init__(
        self,
        primary: TTSBackend,
        fallback: TTSBackend,
        *,
        fallback_voice_id: str = "",
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        # Primary voice ids (e.g. fish.audio reference ids) mean nothing to
        # the fallback backend, so it uses its own voice.
        self._fallback_voice_id = fallback_voice_id
        self._primary_failed_at = 0.0
        self.backend_id = primary.backend_id

    def _primary_usable(self) -> bool:
        if self._primary_failed_at == 0.0:
            return True
        return time.time() - self._primary_failed_at >= _FAILURE_COOLDOWN_SECONDS

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "",
    ) -> TTSResult:
        extra: dict = {}
        if output_format:
            extra["output_format"] = output_format

        if self._primary_usable():
            try:
                result = self._primary.synthesize(
                    text, voice_id=voice_id, speed=speed, **extra
                )
                self._primary_failed_at = 0.0
                return result
            except Exception as exc:
                self._primary_failed_at = time.time()
                logger.warning(
                    "TTS backend '%s' failed (%s) — falling back to '%s' "
                    "for the next %.0f s",
                    self._primary.backend_id,
                    exc,
                    self._fallback.backend_id,
                    _FAILURE_COOLDOWN_SECONDS,
                )
        else:
            logger.debug(
                "TTS backend '%s' in failure cooldown — using '%s'",
                self._primary.backend_id,
                self._fallback.backend_id,
            )

        if self._fallback_voice_id:
            extra["voice_id"] = self._fallback_voice_id
        result = self._fallback.synthesize(text, speed=speed, **extra)
        result.metadata.setdefault("fallback_from", self._primary.backend_id)
        return result

    def available_voices(self) -> List[str]:
        try:
            voices = self._primary.available_voices()
            if voices:
                return voices
        except Exception:
            pass
        return self._fallback.available_voices()

    def health(self) -> bool:
        try:
            if self._primary.health():
                return True
        except Exception:
            pass
        return self._fallback.health()


__all__ = ["FallbackTTSBackend"]
