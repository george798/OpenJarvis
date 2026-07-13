"""Tests for TTS backend infrastructure."""

from __future__ import annotations

from unittest.mock import patch

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSResult

# ---------------------------------------------------------------------------
# TTSResult tests
# ---------------------------------------------------------------------------


def test_tts_result_dataclass():
    result = TTSResult(
        audio=b"fake-audio-bytes",
        format="mp3",
        duration_seconds=3.5,
        voice_id="jarvis-v1",
    )
    assert result.audio == b"fake-audio-bytes"
    assert result.format == "mp3"
    assert result.duration_seconds == 3.5


def test_tts_result_save(tmp_path):
    result = TTSResult(audio=b"fake-mp3-data", format="mp3")
    out = result.save(tmp_path / "test.mp3")
    assert out.exists()
    assert out.read_bytes() == b"fake-mp3-data"


# ---------------------------------------------------------------------------
# Cartesia backend tests
# ---------------------------------------------------------------------------


def test_cartesia_registered():
    from openjarvis.speech.cartesia_tts import CartesiaTTSBackend

    TTSRegistry.register_value("cartesia", CartesiaTTSBackend)
    assert TTSRegistry.contains("cartesia")


def test_cartesia_synthesize():
    from openjarvis.speech.cartesia_tts import CartesiaTTSBackend

    backend = CartesiaTTSBackend(api_key="fake-key")

    with patch(
        "openjarvis.speech.cartesia_tts._cartesia_synthesize",
        return_value=b"fake-audio-mp3-bytes",
    ):
        result = backend.synthesize("Hello world", voice_id="test-voice")

    assert result.audio == b"fake-audio-mp3-bytes"
    assert result.format == "mp3"
    assert result.voice_id == "test-voice"


# ---------------------------------------------------------------------------
# Kokoro backend tests
# ---------------------------------------------------------------------------


def test_kokoro_registered():
    from openjarvis.speech.kokoro_tts import KokoroTTSBackend

    TTSRegistry.register_value("kokoro", KokoroTTSBackend)
    assert TTSRegistry.contains("kokoro")


def test_kokoro_health_false_without_package():
    from openjarvis.speech.kokoro_tts import KokoroTTSBackend

    backend = KokoroTTSBackend()
    # Without kokoro installed, health returns False
    assert backend.health() is False


# ---------------------------------------------------------------------------
# OpenAI TTS backend tests
# ---------------------------------------------------------------------------


def test_openai_tts_registered():
    from openjarvis.speech.openai_tts import OpenAITTSBackend

    TTSRegistry.register_value("openai_tts", OpenAITTSBackend)
    assert TTSRegistry.contains("openai_tts")


def test_openai_tts_synthesize():
    from openjarvis.speech.openai_tts import OpenAITTSBackend

    backend = OpenAITTSBackend(api_key="fake-key")

    with patch(
        "openjarvis.speech.openai_tts._openai_tts_request",
        return_value=b"fake-openai-audio",
    ):
        result = backend.synthesize("Hello", voice_id="nova")

    assert result.audio == b"fake-openai-audio"
    assert result.voice_id == "nova"


# ---------------------------------------------------------------------------
# Fish Audio backend tests
# ---------------------------------------------------------------------------


def test_fish_audio_registered():
    from openjarvis.speech.fish_audio_tts import FishAudioTTSBackend

    TTSRegistry.register_value("fish_audio", FishAudioTTSBackend)
    assert TTSRegistry.contains("fish_audio")


# ---------------------------------------------------------------------------
# Fallback wrapper tests
# ---------------------------------------------------------------------------


class _FakeTTS:
    def __init__(self, backend_id, *, fail=False, healthy=True):
        self.backend_id = backend_id
        self._fail = fail
        self._healthy = healthy
        self.calls = []

    def synthesize(self, text, *, voice_id="", speed=1.0, output_format="mp3"):
        self.calls.append(voice_id)
        if self._fail:
            raise RuntimeError("boom")
        return TTSResult(
            audio=b"audio-" + self.backend_id.encode(),
            format=output_format,
            voice_id=voice_id,
            metadata={"backend": self.backend_id},
        )

    def available_voices(self):
        return [f"{self.backend_id}-voice"]

    def health(self):
        return self._healthy


def test_fallback_uses_primary_when_healthy():
    from openjarvis.speech.fallback_tts import FallbackTTSBackend

    primary = _FakeTTS("fish_audio")
    fallback = _FakeTTS("kokoro")
    backend = FallbackTTSBackend(primary, fallback, fallback_voice_id="am_michael")

    result = backend.synthesize("Hello", voice_id="ref-123")
    assert result.audio == b"audio-fish_audio"
    assert not fallback.calls


def test_fallback_on_primary_failure():
    from openjarvis.speech.fallback_tts import FallbackTTSBackend

    primary = _FakeTTS("fish_audio", fail=True)
    fallback = _FakeTTS("kokoro")
    backend = FallbackTTSBackend(primary, fallback, fallback_voice_id="am_michael")

    result = backend.synthesize("Hello", voice_id="ref-123")
    assert result.audio == b"audio-kokoro"
    assert result.metadata["fallback_from"] == "fish_audio"
    # Fallback must use its own voice, not the cloud reference id
    assert fallback.calls == ["am_michael"]


def test_fallback_cooldown_skips_primary():
    from openjarvis.speech.fallback_tts import FallbackTTSBackend

    primary = _FakeTTS("fish_audio", fail=True)
    fallback = _FakeTTS("kokoro")
    backend = FallbackTTSBackend(primary, fallback, fallback_voice_id="am_michael")

    backend.synthesize("one")
    backend.synthesize("two")
    # Primary tried once, then skipped during cooldown
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 2


def test_fallback_health_uses_either():
    from openjarvis.speech.fallback_tts import FallbackTTSBackend

    primary = _FakeTTS("fish_audio", healthy=False)
    fallback = _FakeTTS("kokoro", healthy=True)
    backend = FallbackTTSBackend(primary, fallback)
    assert backend.health() is True
