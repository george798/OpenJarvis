import { useState } from 'react';
import type { SpeechState } from '../../hooks/useSpeech';

interface MicButtonProps {
  state: SpeechState;
  onClick: () => void;
  voiceAssistant?: boolean;
}

export function MicButton({ state, onClick, voiceAssistant }: MicButtonProps) {
  const [showTooltip, setShowTooltip] = useState(false);

  const tooltipText =
    state === 'recording'
      ? voiceAssistant ? 'Stop — send to Jarvis' : 'Stop recording'
      : state === 'transcribing'
        ? 'Transcribing...'
        : voiceAssistant ? 'Talk to Jarvis' : 'Voice input';

  const isBusy = state === 'transcribing';

  return (
    <div
      className="relative"
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <button
        type="button"
        onClick={onClick}
        aria-label={tooltipText}
        aria-pressed={state === 'recording'}
        className="p-2 rounded-xl transition-all shrink-0"
        style={{
          background: state === 'recording'
            ? 'var(--color-error)'
            : 'transparent',
          color: state === 'recording'
            ? 'white'
            : isBusy
              ? 'var(--color-text-tertiary)'
              : 'var(--color-text-secondary)',
          cursor: isBusy ? 'wait' : 'pointer',
          opacity: isBusy ? 0.6 : 1,
          animation: state === 'recording' ? 'pulse 1.5s ease-in-out infinite' : 'none',
        }}
      >
        {state === 'transcribing' ? (
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="28" strokeDashoffset="10">
              <animateTransform attributeName="transform" type="rotate" from="0 8 8" to="360 8 8" dur="1s" repeatCount="indefinite" />
            </circle>
          </svg>
        ) : (
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
            <path d="M5 3a3 3 0 0 1 6 0v5a3 3 0 0 1-6 0V3z" />
            <path d="M3.5 6.5A.5.5 0 0 1 4 7v1a4 4 0 0 0 8 0V7a.5.5 0 0 1 1 0v1a5 5 0 0 1-4.5 4.975V15h3a.5.5 0 0 1 0 1h-7a.5.5 0 0 1 0-1h3v-2.025A5 5 0 0 1 3 8V7a.5.5 0 0 1 .5-.5z" />
          </svg>
        )}
      </button>
      {showTooltip && (
        <div
          className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2.5 py-1.5 rounded-lg text-xs whitespace-nowrap pointer-events-none z-50"
          style={{
            background: 'var(--color-text)',
            color: 'var(--color-bg)',
            boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
          }}
        >
          {tooltipText}
        </div>
      )}
    </div>
  );
}
