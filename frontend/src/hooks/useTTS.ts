import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchSpeechHealth, synthesizeSpeech } from '../lib/api';

function stripForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' code block ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

function speakBrowser(text: string): void {
  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1;
  utterance.pitch = 1;
  window.speechSynthesis.speak(utterance);
}

export function useTTS(voiceId?: string) {
  const [available, setAvailable] = useState(false);
  const [serverTts, setServerTts] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);
  const lastSpokenRef = useRef<string>('');
  const lastSpokenAtRef = useRef<number>(0);

  useEffect(() => {
    const browserOk = typeof window !== 'undefined' && 'speechSynthesis' in window;
    fetchSpeechHealth()
      .then((h) => {
        setServerTts(Boolean(h.tts_available));
        setAvailable(Boolean(h.tts_available) || browserOk);
      })
      .catch(() => setAvailable(browserOk));
  }, []);

  const cancel = useCallback(() => {
    window.speechSynthesis?.cancel();
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
    setSpeaking(false);
  }, []);

  const speak = useCallback(
    async (text: string) => {
      const cleaned = stripForSpeech(text);
      if (!cleaned) return;

      const now = Date.now();
      if (cleaned === lastSpokenRef.current && now - lastSpokenAtRef.current < 3000) {
        return;
      }
      lastSpokenRef.current = cleaned;
      lastSpokenAtRef.current = now;

      cancel();

      if (serverTts) {
        try {
          setSpeaking(true);
          const blob = await synthesizeSpeech(cleaned, voiceId);
          const url = URL.createObjectURL(blob);
          blobUrlRef.current = url;
          const audio = new Audio(url);
          audioRef.current = audio;
          audio.onended = () => {
            setSpeaking(false);
            if (blobUrlRef.current) {
              URL.revokeObjectURL(blobUrlRef.current);
              blobUrlRef.current = null;
            }
            audioRef.current = null;
          };
          audio.onerror = () => {
            setSpeaking(false);
            // Do not fall back to browser TTS — that caused double playback when
            // server audio partially loaded or autoplay glitched.
          };
          await audio.play();
          return;
        } catch {
          setSpeaking(false);
          // Fall through to browser TTS
        }
      }

      if (window.speechSynthesis) {
        setSpeaking(true);
        const utterance = new SpeechSynthesisUtterance(cleaned);
        utterance.onend = () => setSpeaking(false);
        utterance.onerror = () => setSpeaking(false);
        window.speechSynthesis.speak(utterance);
      }
    },
    [cancel, serverTts, voiceId],
  );

  return { speak, cancel, available, speaking, serverTts };
}
