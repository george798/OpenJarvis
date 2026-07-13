import { useState, useRef, useEffect } from 'react';
import { Search, X, Sparkles, Check } from 'lucide-react';
import { toast } from 'sonner';
import { useAppStore } from '../../lib/store';
import { fetchSkills } from '../../lib/api';
import type { SkillInfo } from '../../types';

export function SkillsPicker() {
  const [query, setQuery] = useState('');
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const activeSkill = useAppStore((s) => s.activeSkill);
  const setActiveSkill = useAppStore((s) => s.setActiveSkill);
  const setSkillsPickerOpen = useAppStore((s) => s.setSkillsPickerOpen);

  useEffect(() => {
    inputRef.current?.focus();
    let cancelled = false;
    (async () => {
      try {
        const list = await fetchSkills();
        if (!cancelled) setSkills(list);
      } catch {
        if (!cancelled) {
          toast.error('Could not load skills');
          setSkills([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = query
    ? skills.filter(
        (s) =>
          s.name.toLowerCase().includes(query.toLowerCase()) ||
          (s.description || '').toLowerCase().includes(query.toLowerCase()),
      )
    : skills;

  useEffect(() => {
    setSelectedIdx(0);
  }, [query]);

  const handleSelect = (skill: SkillInfo | null) => {
    setActiveSkill(skill?.name ?? null);
    setSkillsPickerOpen(false);
    if (skill) {
      toast.message(`Skill active: ${skill.name}`);
      useAppStore.getState().addLogEntry({
        timestamp: Date.now(),
        level: 'info',
        category: 'chat',
        message: `Loaded skill: ${skill.name}`,
      });
    } else {
      toast.message('Skill cleared');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setSkillsPickerOpen(false);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIdx((i) => Math.min(i + 1, filtered.length));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (selectedIdx === 0) {
        handleSelect(null);
      } else if (filtered[selectedIdx - 1]) {
        handleSelect(filtered[selectedIdx - 1]);
      }
    }
  };

  const rows = [{ name: '', description: 'No skill — general assistant' }, ...filtered];

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]"
      onClick={() => setSkillsPickerOpen(false)}
    >
      <div className="fixed inset-0" style={{ background: 'rgba(0,0,0,0.5)' }} />

      <div
        className="relative w-full max-w-lg rounded-xl overflow-hidden"
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          boxShadow: 'var(--shadow-lg)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center gap-3 px-4 py-3"
          style={{ borderBottom: '1px solid var(--color-border)' }}
        >
          <Sparkles size={18} style={{ color: 'var(--color-accent)' }} />
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium" style={{ color: 'var(--color-text)' }}>
              Skills
            </div>
            <div className="text-[11px] truncate" style={{ color: 'var(--color-text-tertiary)' }}>
              Choose a skill to guide this chat (like Cursor /skills)
            </div>
          </div>
          <button
            onClick={() => setSkillsPickerOpen(false)}
            className="p-1 rounded cursor-pointer"
            style={{ color: 'var(--color-text-tertiary)' }}
          >
            <X size={16} />
          </button>
        </div>

        <div
          className="flex items-center gap-3 px-4 py-3"
          style={{ borderBottom: '1px solid var(--color-border)' }}
        >
          <Search size={18} style={{ color: 'var(--color-text-tertiary)' }} />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search skills..."
            className="flex-1 bg-transparent outline-none text-sm"
            style={{ color: 'var(--color-text)' }}
          />
        </div>

        <div className="max-h-[360px] overflow-y-auto py-2">
          {loading ? (
            <div className="px-4 py-6 text-center text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
              Loading skills...
            </div>
          ) : rows.length === 1 ? (
            <div className="px-4 py-6 text-center text-sm" style={{ color: 'var(--color-text-tertiary)' }}>
              No skills installed. Add skills under config/skills/ or run jarvis skill sync hermes.
            </div>
          ) : (
            rows.map((skill, idx) => {
              const isNone = idx === 0;
              const isActive = isNone ? !activeSkill : skill.name === activeSkill;
              const isSelected = idx === selectedIdx;
              return (
                <button
                  key={isNone ? '__none__' : skill.name}
                  onClick={() => handleSelect(isNone ? null : skill)}
                  onMouseEnter={() => setSelectedIdx(idx)}
                  className="flex items-start gap-3 w-full px-4 py-2.5 text-left cursor-pointer transition-colors"
                  style={{
                    background: isSelected ? 'var(--color-bg-secondary)' : 'transparent',
                    border: 'none',
                  }}
                >
                  <Sparkles
                    size={16}
                    className="mt-0.5 shrink-0"
                    style={{ color: isActive ? 'var(--color-accent)' : 'var(--color-text-tertiary)' }}
                  />
                  <div className="flex-1 min-w-0">
                    <div
                      className="text-sm truncate"
                      style={{
                        color: isActive ? 'var(--color-accent)' : 'var(--color-text)',
                        fontWeight: isActive ? 500 : 400,
                      }}
                    >
                      {isNone ? 'None' : skill.name}
                    </div>
                    {skill.description && (
                      <div
                        className="text-[11px] leading-snug mt-0.5 line-clamp-2"
                        style={{ color: 'var(--color-text-tertiary)' }}
                      >
                        {skill.description}
                      </div>
                    )}
                  </div>
                  {isActive && (
                    <span
                      className="text-[10px] px-2 py-0.5 rounded-full shrink-0 flex items-center gap-1"
                      style={{ background: 'var(--color-accent-subtle)', color: 'var(--color-accent)' }}
                    >
                      <Check size={10} /> Active
                    </span>
                  )}
                </button>
              );
            })
          )}
        </div>

        <div
          className="flex items-center gap-4 px-4 py-2 text-[11px]"
          style={{ borderTop: '1px solid var(--color-border)', color: 'var(--color-text-tertiary)' }}
        >
          <span><kbd className="font-mono">↑↓</kbd> Navigate</span>
          <span><kbd className="font-mono">Enter</kbd> Select</span>
          <span><kbd className="font-mono">Esc</kbd> Close</span>
        </div>
      </div>
    </div>
  );
}
