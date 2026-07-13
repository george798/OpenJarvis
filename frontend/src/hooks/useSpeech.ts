import { useState, useCallback, useRef, useEffect } from 'react';
import { transcribeAudio, fetchSpeechHealth } from '../lib/api';

export type SpeechState = 'idle' | 'recording' | 'transcribing';
export type MicPermissionState = 'unknown' | 'granted' | 'denied' | 'prompt' | 'unsupported';

const MIN_RECORD_MS = 800;
const MIN_BLOB_BYTES = 200;

function micErrorMessage(err: unknown): string {
  if (!(err instanceof DOMException)) {
    return err instanceof Error ? err.message : 'Could not start microphone';
  }
  switch (err.name) {
    case 'NotAllowedError':
      return 'Microphone blocked — reset site permissions (lock icon → Site settings), hard-refresh, use Chrome/Edge at http://127.0.0.1:8000';
    case 'NotFoundError':
      return 'No microphone found — plug in a mic or pick an input device in Windows Sound settings';
    case 'NotReadableError':
      return 'Microphone is in use by another app — close Teams/Discord/OBS and try again';
    case 'OverconstrainedError':
      return 'Microphone constraints not supported — try a different input device in Windows Sound settings';
    case 'SecurityError':
      return 'Microphone blocked by browser — open http://127.0.0.1:8000 in Chrome or Edge (not Cursor preview)';
    case 'AbortError':
      return 'Microphone request was cancelled';
    default:
      return `Microphone error (${err.name})`;
  }
}

export async function probeMicPermission(): Promise<MicPermissionState> {
  if (!navigator.mediaDevices?.getUserMedia) return 'unsupported';
  try {
    const status = await navigator.permissions.query({ name: 'microphone' as PermissionName });
    return status.state as MicPermissionState;
  } catch {
    return 'unknown';
  }
}

async function openMicStream(): Promise<MediaStream> {
  const preferred: MediaTrackConstraints = {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: preferred });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'OverconstrainedError') {
      return navigator.mediaDevices.getUserMedia({ audio: true });
    }
    throw err;
  }
}

function createMediaRecorder(stream: MediaStream): MediaRecorder {
  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : MediaRecorder.isTypeSupported('audio/webm')
      ? 'audio/webm'
      : '';

  if (!mimeType) return new MediaRecorder(stream);

  try {
    return new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 128_000 });
  } catch {
    return new MediaRecorder(stream, { mimeType });
  }
}

export function useSpeech() {
  const [state, setState] = useState<SpeechState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const recordingStartedAtRef = useRef(0);

  const refreshHealth = useCallback(() => {
    fetchSpeechHealth()
      .then((health) => setAvailable(health.available))
      .catch(() => setAvailable(false));
  }, []);

  useEffect(() => {
    refreshHealth();
    const onFocus = () => refreshHealth();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [refreshHealth]);

  useEffect(() => {
    if (!navigator.permissions?.query) return;
    let status: PermissionStatus | null = null;
    navigator.permissions
      .query({ name: 'microphone' as PermissionName })
      .then((s) => {
        status = s;
        s.onchange = () => refreshHealth();
      })
      .catch(() => {});
    return () => {
      if (status) status.onchange = null;
    };
  }, [refreshHealth]);

  const startRecording = useCallback(async (): Promise<void> => {
    setError(null);

    if (!window.isSecureContext) {
      setError('Microphone requires a secure context — use http://127.0.0.1:8000 in Chrome or Edge');
      return;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone not supported — use Chrome or Edge, not Cursor\'s built-in browser');
      return;
    }

    try {
      const stream = await openMicStream();
      streamRef.current = stream;

      const recorder = createMediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      // No timeslice — one clean blob on stop (avoids empty clips on quick taps).
      recorder.start();
      recordingStartedAtRef.current = Date.now();
      mediaRecorderRef.current = recorder;
      setState('recording');
    } catch (err) {
      setError(micErrorMessage(err));
      setState('idle');
    }
  }, []);

  const stopRecording = useCallback(async (): Promise<string> => {
    return new Promise((resolve, reject) => {
      const recorder = mediaRecorderRef.current;
      if (!recorder || recorder.state !== 'recording') {
        reject(new Error('Not recording'));
        return;
      }

      const elapsed = Date.now() - recordingStartedAtRef.current;
      if (elapsed < MIN_RECORD_MS) {
        setError(`Keep speaking… (${Math.ceil((MIN_RECORD_MS - elapsed) / 1000)}s more)`);
        reject(new Error('Recording too short'));
        return;
      }

      recorder.onstop = async () => {
        setState('transcribing');

        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;

        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        chunksRef.current = [];

        if (blob.size < MIN_BLOB_BYTES) {
          setState('idle');
          setError('No audio captured — check Windows mic input level and try again');
          reject(new Error('Empty recording'));
          return;
        }

        try {
          const result = await transcribeAudio(blob);
          setState('idle');
          refreshHealth();
          resolve(result.text);
        } catch (err) {
          setState('idle');
          const msg =
            err instanceof Error && err.message.includes('401')
              ? 'Speech API unauthorized — hard-refresh the page (Ctrl+Shift+R) or set API key in Settings → Connection'
              : err instanceof Error
                ? err.message
                : 'Transcription failed';
          setError(msg);
          reject(err);
        }
      };

      if (recorder.state === 'recording') {
        recorder.requestData();
      }
      recorder.stop();
    });
  }, [refreshHealth]);

  return {
    state,
    error,
    available,
    startRecording,
    stopRecording,
    isRecording: state === 'recording',
    isTranscribing: state === 'transcribing',
    refreshHealth,
  };
}
