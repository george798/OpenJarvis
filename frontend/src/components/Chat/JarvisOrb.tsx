import { useEffect, useRef } from 'react';
import type { VoiceOrbState } from '../../lib/store';

interface JarvisOrbProps {
  state: VoiceOrbState;
  onClick: () => void;
  disabled?: boolean;
  size?: 'hero' | 'compact';
  label?: string;
  hint?: string;
}

function readAccent(): string {
  return (
    getComputedStyle(document.documentElement).getPropertyValue('--color-accent').trim() ||
    '#22d3ee'
  );
}

function readPurple(): string {
  return (
    getComputedStyle(document.documentElement).getPropertyValue('--color-accent-purple').trim() ||
    '#b794ff'
  );
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const n = parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function JarvisOrb({
  state,
  onClick,
  disabled = false,
  size = 'hero',
  label,
  hint,
}: JarvisOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef(0);
  const stateRef = useRef(state);
  stateRef.current = state;

  const sizePx = size === 'hero' ? 260 : 80;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = sizePx * dpr;
    canvas.height = sizePx * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    let raf = 0;
    const draw = () => {
      frameRef.current += 1;
      const frame = frameRef.current;
      const orbState = stateRef.current;
      const accent = readAccent();
      const purple = readPurple();
      const [ar, ag, ab] = hexToRgb(accent.startsWith('#') ? accent : '#22d3ee');

      const cx = sizePx / 2;
      const cy = sizePx / 2;
      const baseR = sizePx * (size === 'hero' ? 0.36 : 0.34);

      ctx.clearRect(0, 0, sizePx, sizePx);

      const pulse =
        orbState === 'recording'
          ? 1 + Math.sin(frame * 0.14) * 0.06
          : orbState === 'speaking'
            ? 1 + Math.sin(frame * 0.1) * 0.04
            : 1 + Math.sin(frame * 0.04) * 0.025;
      const r = baseR * pulse;

      // Outer ambient glow
      const glow = ctx.createRadialGradient(cx, cy, r * 0.2, cx, cy, r * 1.85);
      const glowAlpha =
        orbState === 'recording' ? 0.55 : orbState === 'thinking' ? 0.45 : 0.35;
      glow.addColorStop(0, `rgba(${ar}, ${ag}, ${ab}, ${glowAlpha})`);
      glow.addColorStop(0.45, `rgba(${ar}, ${ag}, ${ab}, 0.12)`);
      glow.addColorStop(1, 'rgba(0, 0, 0, 0)');
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(cx, cy, r * 1.85, 0, Math.PI * 2);
      ctx.fill();

      // Speaking ripples
      if (orbState === 'speaking') {
        for (let i = 0; i < 3; i += 1) {
          const phase = ((frame * 0.04 + i * 0.33) % 1) * r * 0.9;
          ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, ${0.35 * (1 - phase / (r * 0.9))})`;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.arc(cx, cy, r + phase, 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      // Core sphere
      const core = ctx.createRadialGradient(cx - r * 0.25, cy - r * 0.3, r * 0.05, cx, cy, r);
      if (orbState === 'recording') {
        core.addColorStop(0, 'rgba(255, 120, 80, 0.95)');
        core.addColorStop(0.35, `rgba(${ar}, ${ag}, ${ab}, 0.75)`);
        core.addColorStop(1, `rgba(${ar}, ${ag}, ${ab}, 0.15)`);
      } else if (orbState === 'thinking') {
        const [pr, pg, pb] = hexToRgb(purple.startsWith('#') ? purple : '#b794ff');
        core.addColorStop(0, `rgba(${pr}, ${pg}, ${pb}, 0.85)`);
        core.addColorStop(0.4, `rgba(${ar}, ${ag}, ${ab}, 0.55)`);
        core.addColorStop(1, `rgba(${ar}, ${ag}, ${ab}, 0.12)`);
      } else {
        core.addColorStop(0, 'rgba(255, 255, 255, 0.92)');
        core.addColorStop(0.18, `rgba(${ar}, ${ag}, ${ab}, 0.85)`);
        core.addColorStop(0.65, `rgba(${ar}, ${ag}, ${ab}, 0.35)`);
        core.addColorStop(1, `rgba(${ar}, ${ag}, ${ab}, 0.08)`);
      }
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();

      // Wireframe latitude / longitude
      const rotSpeed =
        orbState === 'recording' ? 0.022 : orbState === 'thinking' ? 0.018 : 0.008;
      const rot = frame * rotSpeed;

      ctx.save();
      ctx.translate(cx, cy);
      ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, ${orbState === 'idle' ? 0.35 : 0.55})`;
      ctx.lineWidth = size === 'hero' ? 1 : 0.75;

      for (let i = 0; i < 5; i += 1) {
        const tilt = rot + (i * Math.PI) / 5;
        ctx.save();
        ctx.rotate(tilt);
        ctx.scale(1, 0.28 + (i % 2) * 0.12);
        ctx.beginPath();
        ctx.arc(0, 0, r * 0.98, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

      for (let i = 0; i < 8; i += 1) {
        const angle = rot * 1.4 + (i * Math.PI) / 4;
        ctx.beginPath();
        ctx.ellipse(0, 0, r * 0.98, r * 0.98, angle, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();

      // Scan highlight
      const scanY = cy + Math.sin(frame * 0.06) * r * 0.55;
      const scan = ctx.createLinearGradient(cx, scanY - r * 0.15, cx, scanY + r * 0.15);
      scan.addColorStop(0, 'rgba(255, 255, 255, 0)');
      scan.addColorStop(0.5, `rgba(${ar}, ${ag}, ${ab}, 0.35)`);
      scan.addColorStop(1, 'rgba(255, 255, 255, 0)');
      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.clip();
      ctx.fillStyle = scan;
      ctx.fillRect(cx - r, scanY - r * 0.2, r * 2, r * 0.4);
      ctx.restore();

      // Transcribing ring
      if (orbState === 'transcribing') {
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(frame * 0.08);
        ctx.setLineDash([8, 10]);
        ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, 0.85)`;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(0, 0, r * 1.18, 0, Math.PI * 1.35);
        ctx.stroke();
        ctx.restore();
      }

      // Recording reticle
      if (orbState === 'recording') {
        ctx.strokeStyle = 'rgba(255, 100, 80, 0.75)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(cx, cy, r * 1.22, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Specular highlight
      const spec = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.4, 0, cx - r * 0.2, cy - r * 0.25, r * 0.5);
      spec.addColorStop(0, 'rgba(255, 255, 255, 0.75)');
      spec.addColorStop(1, 'rgba(255, 255, 255, 0)');
      ctx.fillStyle = spec;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();

      raf = requestAnimationFrame(draw);
    };

    draw();
    return () => cancelAnimationFrame(raf);
  }, [size, sizePx]);

  const stateLabel =
    state === 'recording'
      ? 'Listening…'
      : state === 'transcribing'
        ? 'Transcribing…'
        : state === 'speaking'
          ? 'Speaking…'
          : state === 'thinking'
            ? 'Thinking…'
            : label ?? 'Talk to Jarvis';

  return (
    <div className={`jarvis-orb-wrap jarvis-orb-wrap--${size}`}>
      <button
        type="button"
        className={`jarvis-orb-btn jarvis-orb-btn--${state}${disabled ? ' jarvis-orb-btn--disabled' : ''}`}
        onClick={onClick}
        disabled={disabled}
        aria-label={stateLabel}
        aria-pressed={state === 'recording'}
        title={`${stateLabel}${hint ? ` · ${hint}` : ''}`}
      >
        <canvas ref={canvasRef} className="jarvis-orb-canvas" aria-hidden />
        <span className="jarvis-orb-ring jarvis-orb-ring--outer" aria-hidden />
        <span className="jarvis-orb-ring jarvis-orb-ring--inner" aria-hidden />
      </button>
      {size === 'hero' && (
        <div className="jarvis-orb-caption">
          <span className="jarvis-orb-caption__title">{stateLabel}</span>
          {hint && <span className="jarvis-orb-caption__hint">{hint}</span>}
        </div>
      )}
    </div>
  );
}
