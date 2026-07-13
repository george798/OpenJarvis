import { useCallback, useEffect, useState } from 'react';
import { ExternalLink, Mail, RefreshCw, Search } from 'lucide-react';
import { useAppStore } from '../../lib/store';
import { fetchModels } from '../../lib/api';
import type { ModelInfo } from '../../types';
import {
  connectSource,
  disconnectSource,
  getSyncStatus,
  listConnectors,
  triggerSync,
} from '../../lib/connectors-api';
import type { ConnectRequest, ConnectorInfo, SyncStatus } from '../../types/connectors';

interface MailProviderConfig {
  connectorId: string;
  label: string;
  emailStorageKey: string;
  helpUrl: string;
  helpLabel: string;
  steps: string[];
}

const MAIL_PROVIDERS: MailProviderConfig[] = [
  {
    connectorId: 'gmail_imap',
    label: 'Gmail',
    emailStorageKey: 'openjarvis-giorgos-gmail-email',
    helpUrl: 'https://myaccount.google.com/apppasswords',
    helpLabel: 'Create Gmail app password',
    steps: [
      'Enable 2-Step Verification on your Google account.',
      'Generate a 16-character App Password (Mail → Other → "OpenJarvis").',
      'Use the app password below — not your regular Gmail password.',
    ],
  },
  {
    connectorId: 'outlook',
    label: 'Outlook / Microsoft 365',
    emailStorageKey: 'openjarvis-giorgos-outlook-email',
    helpUrl: 'https://account.microsoft.com/security',
    helpLabel: 'Create Outlook app password',
    steps: [
      'Enable two-step verification on your Microsoft account.',
      'Under Security → Advanced security options, create an app password.',
      'Use that app password below — not your regular Microsoft password.',
    ],
  },
];

function readStoredEmail(key: string): string {
  try {
    return localStorage.getItem(key) || '';
  } catch {
    return '';
  }
}

function storeEmail(key: string, value: string) {
  try {
    if (value.trim()) localStorage.setItem(key, value.trim());
    else localStorage.removeItem(key);
  } catch {}
}

function buildConnectRequest(email: string, password: string): ConnectRequest {
  const normalizedPassword = password.replace(/\s/g, '');
  const token = `${email.trim()}:${normalizedPassword}`;
  return { email: email.trim(), password: normalizedPassword, token, code: token };
}

function syncLabel(status: SyncStatus | null): string {
  if (!status) return 'Unknown';
  if (status.state === 'syncing') {
    const total = status.items_total > 0 ? ` / ${status.items_total}` : '';
    return `Syncing (${status.items_synced}${total})`;
  }
  if (status.state === 'error') return status.error || 'Sync error';
  if (status.items_synced > 0) return `${status.items_synced} emails indexed`;
  return 'Idle';
}

function MailProviderCard({
  config,
  connector,
  syncStatus,
  onRefresh,
}: {
  config: MailProviderConfig;
  connector: ConnectorInfo | undefined;
  syncStatus: SyncStatus | null;
  onRefresh: () => void;
}) {
  const connected = connector?.connected ?? false;
  const [email, setEmail] = useState(() => readStoredEmail(config.emailStorageKey));
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const inputStyle = {
    background: 'var(--color-bg-secondary)',
    color: 'var(--color-text)',
    border: '1px solid var(--color-border)',
  };

  const handleConnect = async () => {
    if (!email.trim() || !password.trim()) {
      setError('Enter your email and app password.');
      return;
    }
    setBusy(true);
    setError('');
    setMessage('Connecting…');
    try {
      await connectSource(config.connectorId, buildConnectRequest(email, password));
      storeEmail(config.emailStorageKey, email);
      setPassword('');
      setMessage('Connected — initial sync started.');
      onRefresh();
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : 'Connection failed';
      const friendly =
        config.connectorId === 'gmail_imap' &&
        /auth|credentials|LOGIN/i.test(detail)
          ? 'Invalid credentials — use a Gmail App Password, not your regular password.'
          : detail;
      setError(friendly);
      setMessage('');
    } finally {
      setBusy(false);
    }
  };

  const handleDisconnect = async () => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await disconnectSource(config.connectorId);
      setMessage('Disconnected.');
      onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Disconnect failed');
    } finally {
      setBusy(false);
    }
  };

  const handleSync = async () => {
    setBusy(true);
    setError('');
    setMessage('Starting sync…');
    try {
      await triggerSync(config.connectorId);
      setMessage('Sync started.');
      onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Sync failed');
      setMessage('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border-subtle)' }}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Mail size={16} style={{ color: 'var(--color-accent)' }} />
          <span className="text-sm font-medium" style={{ color: 'var(--color-text)' }}>
            {config.label}
          </span>
          <span
            className="w-2 h-2 rounded-full shrink-0"
            style={{
              background: connected ? 'var(--color-success)' : 'var(--color-text-tertiary)',
            }}
          />
        </div>
        <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
          {syncLabel(syncStatus)}
        </span>
      </div>

      <ul className="text-xs mb-3 space-y-1 list-disc pl-4" style={{ color: 'var(--color-text-tertiary)' }}>
        {config.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ul>

      <a
        href={config.helpUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-xs mb-3"
        style={{ color: 'var(--color-accent)' }}
      >
        {config.helpLabel}
        <ExternalLink size={11} />
      </a>

      <div className="flex flex-col gap-2 mb-3">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          disabled={busy}
          className="text-sm px-3 py-1.5 rounded-lg outline-none w-full"
          style={inputStyle}
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="App password"
          disabled={busy}
          autoComplete="off"
          className="text-sm px-3 py-1.5 rounded-lg outline-none w-full"
          style={inputStyle}
        />
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={handleConnect}
          disabled={busy}
          className="text-xs px-3 py-1.5 rounded-lg cursor-pointer disabled:opacity-50"
          style={{
            background: 'var(--color-accent, var(--color-bg-tertiary))',
            color: 'var(--color-text)',
            border: '1px solid var(--color-border)',
          }}
        >
          {connected ? 'Update credentials' : 'Connect'}
        </button>
        {connected && (
          <>
            <button
              type="button"
              onClick={handleSync}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded-lg cursor-pointer disabled:opacity-50"
              style={{
                background: 'var(--color-bg-tertiary)',
                color: 'var(--color-text-secondary)',
                border: '1px solid var(--color-border)',
              }}
            >
              Sync now
            </button>
            <button
              type="button"
              onClick={handleDisconnect}
              disabled={busy}
              className="text-xs px-3 py-1.5 rounded-lg cursor-pointer disabled:opacity-50"
              style={{
                color: 'var(--color-error)',
                border: '1px solid var(--color-error)',
                background: 'transparent',
              }}
            >
              Disconnect
            </button>
          </>
        )}
      </div>

      {message && (
        <p className="text-xs mt-2" style={{ color: 'var(--color-success)' }}>
          {message}
        </p>
      )}
      {error && (
        <p className="text-xs mt-2" style={{ color: 'var(--color-error)' }}>
          {error}
        </p>
      )}
    </div>
  );
}

const DEEP_RESEARCH_DEFAULT = 'qwen2.5-coder:7b';

function DeepResearchModelCard() {
  const deepResearchModel = useAppStore((s) => s.settings.deepResearchModel);
  const updateSettings = useAppStore((s) => s.updateSettings);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [customModel, setCustomModel] = useState('');

  const refreshModels = useCallback(() => {
    setLoading(true);
    setLoadError('');
    fetchModels()
      .then(setModels)
      .catch((err: unknown) => {
        setLoadError(err instanceof Error ? err.message : 'Could not load models');
        setModels([]);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refreshModels();
  }, [refreshModels]);

  useEffect(() => {
    if (deepResearchModel && !models.some((m) => m.id === deepResearchModel)) {
      setCustomModel(deepResearchModel);
    }
  }, [deepResearchModel, models]);

  const inputStyle = {
    background: 'var(--color-bg-secondary)',
    color: 'var(--color-text)',
    border: '1px solid var(--color-border)',
  };

  const effectiveLabel = deepResearchModel.trim() || `Server default (${DEEP_RESEARCH_DEFAULT})`;

  return (
    <div
      className="rounded-lg p-4"
      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border-subtle)' }}
    >
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <Search size={16} style={{ color: 'var(--color-accent)' }} />
          <span className="text-sm font-medium" style={{ color: 'var(--color-text)' }}>
            Deep Research model
          </span>
        </div>
        <button
          type="button"
          onClick={refreshModels}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded cursor-pointer"
          style={{
            background: 'var(--color-bg-tertiary)',
            color: 'var(--color-text-secondary)',
            border: '1px solid var(--color-border)',
          }}
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
          Models
        </button>
      </div>

      <p className="text-xs mb-3" style={{ color: 'var(--color-text-tertiary)' }}>
        Planner model when <strong>Deep Research</strong> is toggled in chat. Uses Ollama on the
        host — pick a model with tool-calling support (e.g. qwen2.5, llama3.1, gemma). Leave empty
        for the server default from{' '}
        <code className="px-1 py-0.5 rounded text-[10px]" style={{ background: 'var(--color-bg-tertiary)' }}>
          config.toml
        </code>
        .
      </p>

      <p className="text-xs mb-2" style={{ color: 'var(--color-text-secondary)' }}>
        Active: <span style={{ color: 'var(--color-accent)' }}>{effectiveLabel}</span>
      </p>

      {loadError && (
        <p className="text-xs mb-2" style={{ color: 'var(--color-error)' }}>
          {loadError}
        </p>
      )}

      <select
        value={deepResearchModel}
        onChange={(e) => {
          updateSettings({ deepResearchModel: e.target.value });
          setCustomModel('');
        }}
        disabled={loading}
        className="text-sm px-3 py-1.5 rounded-lg outline-none w-full mb-2 cursor-pointer"
        style={inputStyle}
      >
        <option value="">Server default ({DEEP_RESEARCH_DEFAULT})</option>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.id}
          </option>
        ))}
      </select>

      <div className="flex gap-2">
        <input
          type="text"
          value={customModel}
          onChange={(e) => setCustomModel(e.target.value)}
          placeholder="Or type Ollama tag, e.g. qwen2.5:14b"
          className="text-sm px-3 py-1.5 rounded-lg outline-none flex-1"
          style={inputStyle}
        />
        <button
          type="button"
          onClick={() => {
            const tag = customModel.trim();
            if (tag) updateSettings({ deepResearchModel: tag });
          }}
          disabled={!customModel.trim()}
          className="text-xs px-3 py-1.5 rounded-lg cursor-pointer disabled:opacity-50 shrink-0"
          style={{
            background: 'var(--color-accent, var(--color-bg-tertiary))',
            color: 'var(--color-text)',
            border: '1px solid var(--color-border)',
          }}
        >
          Apply
        </button>
      </div>
    </div>
  );
}

export function GiorgosSettingsSection() {
  const [connectors, setConnectors] = useState<ConnectorInfo[]>([]);
  const [syncMap, setSyncMap] = useState<Record<string, SyncStatus>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  const refresh = useCallback(async () => {
    setLoadError('');
    try {
      const list = await listConnectors();
      setConnectors(list);
      const statuses: Record<string, SyncStatus> = {};
      await Promise.all(
        MAIL_PROVIDERS.map(async (provider) => {
          const match = list.find((c) => c.connector_id === provider.connectorId);
          if (match?.connected) {
            try {
              statuses[provider.connectorId] = await getSyncStatus(provider.connectorId);
            } catch {
              /* sync endpoint may be unavailable while connecting */
            }
          }
        }),
      );
      setSyncMap(statuses);
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : 'Could not load mail connectors');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 8000);
    return () => clearInterval(interval);
  }, [refresh]);

  return (
    <div
      className="rounded-xl p-5"
      style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
    >
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
          Giorgo&apos;s Settings
        </h3>
        <button
          type="button"
          onClick={() => {
            setLoading(true);
            refresh();
          }}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded cursor-pointer"
          style={{
            background: 'var(--color-bg-secondary)',
            color: 'var(--color-text-secondary)',
            border: '1px solid var(--color-border)',
          }}
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>
      <p className="text-xs mb-4" style={{ color: 'var(--color-text-tertiary)' }}>
        Custom integrations not covered by the standard Web UI. Configure Gmail and Outlook here
        with IMAP app passwords — credentials are stored on the server in{' '}
        <code className="px-1 py-0.5 rounded text-[10px]" style={{ background: 'var(--color-bg-tertiary)' }}>
          config/connectors/
        </code>
        . Set your API key in Connection above first.
      </p>

      {loadError && (
        <p className="text-xs mb-3 px-3 py-2 rounded-lg" style={{ color: 'var(--color-error)', background: 'rgba(220,38,38,0.08)' }}>
          {loadError}
        </p>
      )}

      <div className="flex flex-col gap-3">
        <DeepResearchModelCard />
        {MAIL_PROVIDERS.map((provider) => (
          <MailProviderCard
            key={provider.connectorId}
            config={provider}
            connector={connectors.find((c) => c.connector_id === provider.connectorId)}
            syncStatus={syncMap[provider.connectorId] ?? null}
            onRefresh={refresh}
          />
        ))}
      </div>

      <p className="text-xs mt-4" style={{ color: 'var(--color-text-tertiary)' }}>
        Both inboxes feed the morning digest when enabled in{' '}
        <code className="px-1 py-0.5 rounded text-[10px]" style={{ background: 'var(--color-bg-tertiary)' }}>
          config.toml
        </code>
        . After connecting, try &quot;Summarize my unread Gmail&quot; or say &quot;Good morning&quot; for the spoken digest.
      </p>
    </div>
  );
}
