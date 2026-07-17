import { useCallback, useEffect, useState, type ComponentType, type CSSProperties, type ReactNode } from 'react';
import {
  Activity,
  Bot,
  Cable,
  Database,
  HardDrive,
  Network,
  RefreshCw,
  Server,
  Wrench,
  BookOpen,
} from 'lucide-react';
import { fetchCapabilities, fetchConfig, setConfigKey, type CapabilitiesIndex } from '../lib/api';
import { toast } from 'sonner';

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div
      className="rounded-lg px-4 py-3"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      <div className="text-[10px] uppercase tracking-wide" style={{ color: 'var(--color-text-tertiary)' }}>
        {label}
      </div>
      <div className="text-xl font-semibold mt-1" style={{ color: 'var(--color-text)' }}>
        {value}
      </div>
      {hint && (
        <div className="text-xs mt-1" style={{ color: 'var(--color-text-secondary)' }}>
          {hint}
        </div>
      )}
    </div>
  );
}

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: ComponentType<{ size?: number; style?: CSSProperties }>;
  title: string;
  children: ReactNode;
}) {
  return (
    <section
      className="rounded-lg p-4"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
    >
      <h2 className="text-sm font-semibold flex items-center gap-2 mb-3" style={{ color: 'var(--color-text)' }}>
        <Icon size={14} style={{ color: 'var(--color-accent)' }} />
        {title}
      </h2>
      {children}
    </section>
  );
}

export function SystemMapPage() {
  const [data, setData] = useState<CapabilitiesIndex | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [configKey, setKey] = useState('agent.context_from_knowledge');
  const [configValue, setValue] = useState('true');
  const [configSections, setConfigSections] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [caps, cfg] = await Promise.all([fetchCapabilities(), fetchConfig()]);
      setData(caps);
      setConfigSections(cfg.sections || []);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load system map');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const applyConfig = async () => {
    try {
      const msg = await setConfigKey(configKey, configValue);
      toast.success(msg);
      load();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Config update failed');
    }
  };

  const s = data?.summary;

  return (
    <div className="flex-1 overflow-y-auto px-6 py-10">
      <div className="max-w-6xl mx-auto">
        <header className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold" style={{ color: 'var(--color-text)' }}>
              System Map
            </h1>
            <p className="text-sm mt-2 max-w-2xl" style={{ color: 'var(--color-text-secondary)' }}>
              Live view of what Jarvis can do and what is configured — tools, MCP, connectors,
              knowledge corpus, managed agents, memory layers, and vault.
            </p>
          </div>
          <button
            type="button"
            onClick={load}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs cursor-pointer"
            style={{
              background: 'var(--color-bg-tertiary)',
              color: 'var(--color-text)',
              border: '1px solid var(--color-border)',
            }}
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </header>

        {error && (
          <div className="mb-4 text-sm" style={{ color: 'var(--color-error)' }}>
            {error}
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          <Stat label="Tools enabled" value={s?.tools_enabled ?? '—'} hint={`${s?.tools_registry ?? 0} in registry`} />
          <Stat label="MCP servers" value={s?.mcp_servers ?? '—'} />
          <Stat
            label="Connectors"
            value={`${s?.connectors_connected ?? 0}/${s?.connectors_total ?? 0}`}
            hint="connected"
          />
          <Stat
            label="Knowledge"
            value={s?.knowledge_chunks ?? '—'}
            hint={`${s?.knowledge_sources ?? 0} sources`}
          />
          <Stat label="Agents" value={s?.managed_agents ?? '—'} />
          <Stat label="Vault" value={s?.vault_configured ? 'on' : 'off'} />
          <Stat label="Model" value={s?.model || '—'} />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
          <Section icon={Wrench} title="Tools">
            <p className="text-xs mb-2" style={{ color: 'var(--color-text-secondary)' }}>
              Enabled allowlist ({data?.tools?.enabled_count ?? 0})
              {data?.tools?.mcp_wildcard ? ' · mcp:*' : ''}
            </p>
            <div className="flex flex-wrap gap-1 max-h-40 overflow-y-auto">
              {(data?.tools?.enabled || []).map((t) => (
                <span
                  key={t}
                  className="px-2 py-0.5 rounded text-[10px] font-mono"
                  style={{ background: 'var(--color-bg-tertiary)', color: 'var(--color-text)' }}
                >
                  {t}
                </span>
              ))}
            </div>
          </Section>

          <Section icon={Network} title="MCP servers">
            {(data?.mcp_servers || []).length === 0 ? (
              <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                None configured — use mcp_manage or Settings below.
              </p>
            ) : (
              <ul className="space-y-1">
                {(data?.mcp_servers || []).map((m) => (
                  <li key={m.name} className="text-xs flex justify-between" style={{ color: 'var(--color-text)' }}>
                    <span className="font-mono">{m.name}</span>
                    <span style={{ color: 'var(--color-text-tertiary)' }}>{m.kind}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section icon={Cable} title="Connectors">
            <ul className="space-y-1 max-h-48 overflow-y-auto">
              {(data?.connectors || []).map((c) => (
                <li key={c.id} className="text-xs flex items-center justify-between" style={{ color: 'var(--color-text)' }}>
                  <span className="font-mono">{c.id}</span>
                  <span
                    style={{
                      color: c.connected ? 'var(--color-success)' : 'var(--color-text-tertiary)',
                    }}
                  >
                    {c.connected ? 'connected' : 'idle'}
                  </span>
                </li>
              ))}
            </ul>
          </Section>

          <Section icon={Database} title="Knowledge corpus">
            <ul className="space-y-1 max-h-48 overflow-y-auto">
              {Object.entries(data?.knowledge?.sources || {})
                .sort((a, b) => b[1] - a[1])
                .map(([src, n]) => (
                  <li key={src} className="text-xs flex justify-between" style={{ color: 'var(--color-text)' }}>
                    <span className="font-mono">{src}</span>
                    <span style={{ color: 'var(--color-text-tertiary)' }}>{n} chunks</span>
                  </li>
                ))}
              {!Object.keys(data?.knowledge?.sources || {}).length && (
                <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                  No indexed sources yet.
                </p>
              )}
            </ul>
          </Section>

          <Section icon={Bot} title="Managed agents">
            <ul className="space-y-2 max-h-56 overflow-y-auto">
              {(data?.managed_agents || []).map((a) => (
                <li key={a.id} className="text-xs" style={{ color: 'var(--color-text)' }}>
                  <div className="flex justify-between gap-2">
                    <span className="font-medium">{a.name}</span>
                    <span style={{ color: 'var(--color-text-tertiary)' }}>{a.status}</span>
                  </div>
                  <div style={{ color: 'var(--color-text-tertiary)' }}>
                    {a.schedule_type}
                    {a.schedule_value ? ` · ${a.schedule_value}` : ''}
                    {a.agent_type ? ` · ${a.agent_type}` : ''}
                  </div>
                </li>
              ))}
              {!data?.managed_agents?.length && (
                <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                  No managed agents.
                </p>
              )}
            </ul>
          </Section>

          <Section icon={BookOpen} title="Vault & memory">
            {data?.vault?.configured ? (
              <div className="text-xs space-y-1" style={{ color: 'var(--color-text-secondary)' }}>
                <div>
                  Path: <span className="font-mono" style={{ color: 'var(--color-text)' }}>{data.vault.path}</span>
                </div>
                <div>
                  Notes: {data.vault.note_count ?? 0} · Journals: {data.vault.journal_count ?? 0}
                </div>
                <div>Writeback: {data.vault.writeback ? 'on' : 'off'} every {data.vault.writeback_interval}s</div>
              </div>
            ) : (
              <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                Vault not configured (set memory_files.vault_path).
              </p>
            )}
            <div className="mt-3 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
              Layers: fact→memory.db · preference→USER.md · rule→MEMORY.md · note→vault
            </div>
            <div className="text-xs mt-1" style={{ color: 'var(--color-text-tertiary)' }}>
              context_from_memory: {String(data?.memory?.context_from_memory)} · context_from_knowledge:{' '}
              {String(data?.memory?.context_from_knowledge)}
            </div>
          </Section>
        </div>

        <Section icon={Server} title="Quick config (dotted key)">
          <p className="text-xs mb-3" style={{ color: 'var(--color-text-secondary)' }}>
            Writes to config.toml via the same path as config_manage. Most changes need a container restart.
            Sections: {configSections.slice(0, 12).join(', ')}
            {configSections.length > 12 ? '…' : ''}
          </p>
          <div className="flex flex-wrap gap-2 items-center">
            <input
              value={configKey}
              onChange={(e) => setKey(e.target.value)}
              placeholder="agent.tools"
              className="px-2 py-1.5 rounded text-xs font-mono flex-1 min-w-[200px]"
              style={{
                background: 'var(--color-bg)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text)',
              }}
            />
            <input
              value={configValue}
              onChange={(e) => setValue(e.target.value)}
              placeholder="value"
              className="px-2 py-1.5 rounded text-xs font-mono w-48"
              style={{
                background: 'var(--color-bg)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text)',
              }}
            />
            <button
              type="button"
              onClick={applyConfig}
              className="px-3 py-1.5 rounded text-xs cursor-pointer"
              style={{
                background: 'var(--color-accent)',
                color: 'var(--color-bg)',
                border: 'none',
              }}
            >
              Apply
            </button>
          </div>
        </Section>

        <div className="mt-4 flex items-center gap-2 text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
          <Activity size={12} />
          <HardDrive size={12} />
          Data from GET /v1/capabilities — same map the brain sees via self_inspect.
        </div>
      </div>
    </div>
  );
}
