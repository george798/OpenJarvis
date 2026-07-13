"""Tests for Fish Audio TTS backend."""

from __future__ import annotations

from unittest.mock import patch

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.fish_audio_tts import FishAudioTTSBackend


def test_fish_audio_registered():
    TTSRegistry.register_value("fish_audio", FishAudioTTSBackend)
    assert TTSRegistry.contains("fish_audio")


def test_fish_audio_synthesize():
    backend = FishAudioTTSBackend(
        api_key="fake-key",
        default_voice_id="voice-123",
    )

    with patch(
        "openjarvis.speech.fish_audio_tts._fish_synthesize",
        return_value=b"fake-fish-audio",
    ) as mock_syn:
        result = backend.synthesize("Hello sir", voice_id="voice-123")

    assert result.audio == b"fake-fish-audio"
    assert result.voice_id == "voice-123"
    mock_syn.assert_called_once()


def test_fish_audio_health_requires_key_and_voice():
    assert FishAudioTTSBackend(api_key="", default_voice_id="v").health() is False
    assert FishAudioTTSBackend(api_key="k", default_voice_id="").health() is False
    assert FishAudioTTSBackend(api_key="k", default_voice_id="v").health() is True
