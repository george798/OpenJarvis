"""Auto-discover available text-to-speech backends."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openjarvis.core.config import JarvisConfig
    from openjarvis.speech.tts import TTSBackend


def _cloud_api_key(name: str) -> str:
    """Resolve an API key from process env or cloud-keys.env."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        from openjarvis.server.cloud_router import _load_keys

        return (_load_keys().get(name) or "").strip()
    except Exception:
        return ""

_TTS_ALIASES = {
    "openai": "openai_tts",
    "cartesia": "cartesia",
    "fish": "fish_audio",
    "fish_audio": "fish_audio",
    "kokoro": "kokoro",
}

_TTS_DISCOVERY_ORDER = ["fish_audio", "openai_tts", "cartesia", "kokoro"]


def _resolve_tts_key(config: "JarvisConfig") -> str:
    raw = getattr(config.speech, "tts_backend", "") or config.digest.tts_backend or "auto"
    return _TTS_ALIASES.get(raw, raw)


def _create_tts_backend(key: str, config: "JarvisConfig") -> Optional["TTSBackend"]:
    from openjarvis.core.registry import TTSRegistry

    if not TTSRegistry.contains(key):
        return None
    try:
        backend_cls = TTSRegistry.get(key)
        if key == "openai_tts":
            api_key = _cloud_api_key("OPENAI_API_KEY")
            if not api_key:
                return None
            model = getattr(config.speech, "tts_model", "") or "tts-1"
            return backend_cls(api_key=api_key, model=model)
        if key == "cartesia":
            api_key = _cloud_api_key("CARTESIA_API_KEY")
            if not api_key:
                return None
            return backend_cls(api_key=api_key)
        if key == "fish_audio":
            api_key = _cloud_api_key("FISH_API_KEY")
            voice_id = getattr(config.speech, "tts_voice_id", "") or ""
            if not api_key or not voice_id:
                return None
            model = getattr(config.speech, "tts_model", "") or "s2-pro"
            return backend_cls(
                api_key=api_key,
                model=model,
                default_voice_id=voice_id,
            )
        if key == "kokoro":
            return backend_cls()
        return backend_cls()
    except Exception:
        return None


_CLOUD_TTS_KEYS = ("fish_audio", "openai_tts", "cartesia")

# Local voice used when a cloud backend fails and we fall back to Kokoro
# (cloud voice/reference ids mean nothing to Kokoro).
_KOKORO_FALLBACK_VOICE = "am_michael"


def _with_local_fallback(
    backend: Optional["TTSBackend"], config: "JarvisConfig"
) -> Optional["TTSBackend"]:
    """Wrap a cloud backend so failures fall back to local Kokoro."""
    if backend is None or backend.backend_id not in _CLOUD_TTS_KEYS:
        return backend
    kokoro = _create_tts_backend("kokoro", config)
    if kokoro is None or not kokoro.health():
        return backend
    from openjarvis.speech.fallback_tts import FallbackTTSBackend

    return FallbackTTSBackend(
        backend, kokoro, fallback_voice_id=_KOKORO_FALLBACK_VOICE
    )


def get_tts_backend(config: "JarvisConfig") -> Optional["TTSBackend"]:
    """Resolve TTS backend from config or auto-discovery.

    Cloud backends (fish_audio, openai_tts, cartesia) are wrapped with a
    local Kokoro fallback so voice keeps working when the cloud API is
    unreachable, the key is missing/invalid, or the account is out of
    credits. If an explicitly selected cloud backend can't even be
    constructed (e.g. no API key at all), Kokoro is used directly.
    """
    import openjarvis.speech  # noqa: F401 — register backends

    key = _resolve_tts_key(config)
    if key != "auto":
        backend = _create_tts_backend(key, config)
        if backend is None and key in _CLOUD_TTS_KEYS:
            return _create_tts_backend("kokoro", config)
        return _with_local_fallback(backend, config)

    for candidate in _TTS_DISCOVERY_ORDER:
        backend = _create_tts_backend(candidate, config)
        if backend is not None and backend.health():
            return _with_local_fallback(backend, config)
    return None


def default_voice_id(config: "JarvisConfig") -> str:
    voice = getattr(config.speech, "tts_voice_id", "") or config.digest.voice_id
    if voice:
        return voice
    key = _resolve_tts_key(config)
    if key in ("openai_tts", "openai"):
        return "onyx"
    if key in ("fish_audio", "fish"):
        return getattr(config.speech, "tts_voice_id", "") or ""
    if key == "kokoro":
        return "af_heart"
    return "nova"
