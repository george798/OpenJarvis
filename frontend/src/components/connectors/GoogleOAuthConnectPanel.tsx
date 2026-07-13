import { useEffect, useRef, useState } from 'react';
import { ExternalLink, Loader2 } from 'lucide-react';
import { getApiKey, getBase } from '../../lib/api';
import { connectSource, getConnector, triggerSync } from '../../lib/connectors-api';
import type { ConnectRequest } from '../../types/connectors';

export const GOOGLE_OAUTH_CONNECTORS = new Set([
  'gcalendar',
  'gdrive',
  'gcontacts',
  'google_tasks',
]);

/** Full callback URL(s) Google Cloud must allow for Web UI OAuth. */
function oauthRedirectUris(connectorId: string): string[] {
  const path = `/v1/connectors/${encodeURIComponent(connectorId)}/oauth/callback`;
  const base = getBase() || (typeof window !== 'undefined' ? window.location.origin : '');
  if (!base) return [path];
  const primary = `${base.replace(/\/+$/, '')}${path}`;
  try {
    const url = new URL(primary);
    const altHost = url.hostname === 'localhost' ? '127.0.0.1' : 'localhost';
    const alt = `${url.protocol}//${altHost}${url.port ? `:${url.port}` : ''}${path}`;
    return alt === primary ? [primary] : [primary, alt];
  } catch {
    return [primary];
  }
}

function buildCredentialRequest(
  fields: Array<{ name: string; placeholder: string; type?: string }>,
  inputs: Record<string, string>,
): ConnectRequest {
  const req: ConnectRequest = {};
  for (const f of fields) {
    if (f.name === 'email') req.email = inputs.email;
    else if (f.name === 'password') req.password = inputs.password;
    else if (f.name === 'token') req.token = inputs.token;
  }
  if (req.email && req.password) {
    req.token = `${req.email}:${req.password}`;
    req.code = req.token;
  }
  if (req.token && !req.code) req.code = req.token;
  return req;
}

export function GoogleOAuthConnectPanel({
  connectorId,
  displayName,
  fields,
  onDone,
}: {
  connectorId: string;
  displayName: string;
  fields: Array<{ name: string; placeholder: string; type?: string }>;
  onDone: () => void;
}) {
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [stage, setStage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [credentialsSaved, setCredentialsSaved] = useState(false);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    getConnector(connectorId)
      .then((info) => {
        if (info.oauth_setup?.has_credentials) setCredentialsSaved(true);
      })
      .catch(() => {});
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [connectorId]);

  const allFilled = fields.every((f) => inputs[f.name]?.trim());

  const saveCredentials = async () => {
    if (!allFilled) {
      setError('Enter your Google OAuth Client ID and Client Secret.');
      return;
    }
    setBusy(true);
    setError('');
    setStage('Saving credentials…');
    try {
      await connectSource(connectorId, buildCredentialRequest(fields, inputs));
      setCredentialsSaved(true);
      setStage('Credentials saved. Click “Sign in with Google” next.');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save credentials');
      setStage('');
    } finally {
      setBusy(false);
    }
  };

  const startOAuth = async () => {
    setBusy(true);
    setError('');
    setStage('Opening Google sign-in…');
    try {
      if (!credentialsSaved && allFilled) {
        await connectSource(connectorId, buildCredentialRequest(fields, inputs));
        setCredentialsSaved(true);
      } else if (!credentialsSaved) {
        setError('Save your Client ID and Client Secret first.');
        setStage('');
        setBusy(false);
        return;
      }

      const apiKey = getApiKey();
      const params = new URLSearchParams();
      if (apiKey) params.set('token', apiKey);
      const oauthUrl = `${getBase()}/v1/connectors/${encodeURIComponent(connectorId)}/oauth/start${params.toString() ? `?${params}` : ''}`;
      window.open(oauthUrl, '_blank', 'width=600,height=700');
      setStage('Waiting for Google authorization… Complete sign-in in the popup.');

      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(async () => {
        try {
          const info = await getConnector(connectorId);
          if (info.connected) {
            if (pollRef.current) window.clearInterval(pollRef.current);
            pollRef.current = null;
            setStage('Connected! Starting sync…');
            try {
              await triggerSync(connectorId);
            } catch {
              /* sync may already be running */
            }
            setBusy(false);
            onDone();
          }
        } catch {
          /* keep polling */
        }
      }, 2000);

      window.setTimeout(() => {
        if (pollRef.current) {
          window.clearInterval(pollRef.current);
          pollRef.current = null;
          setBusy(false);
          setStage('');
          setError('Authorization timed out. Try “Sign in with Google” again.');
        }
      }, 180000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'OAuth failed');
      setStage('');
      setBusy(false);
    }
  };

  const inputStyle = {
    width: '100%',
    padding: '7px 10px',
    background: 'var(--color-bg)',
    border: '1px solid var(--color-border)',
    borderRadius: 4,
    color: 'var(--color-text)',
    fontSize: 12,
    marginBottom: 6,
    boxSizing: 'border-box' as const,
  };

  return (
    <div>
      {fields.map((f) => (
        <input
          key={f.name}
          value={inputs[f.name] || ''}
          onChange={(e) => setInputs((p) => ({ ...p, [f.name]: e.target.value }))}
          placeholder={f.placeholder}
          type={f.type || 'text'}
          style={inputStyle}
        />
      ))}

      <div
        style={{
          fontSize: 11,
          color: 'var(--color-text-tertiary)',
          marginBottom: 8,
          lineHeight: 1.4,
        }}
      >
        In Google Cloud Console → Credentials → OAuth client (use <strong>Web application</strong>{' '}
        for Docker/Web UI), add every redirect URI below:
        {oauthRedirectUris(connectorId).map((uri) => (
          <code
            key={uri}
            style={{
              display: 'block',
              marginTop: 4,
              padding: '4px 6px',
              background: 'var(--color-bg-tertiary)',
              borderRadius: 4,
              wordBreak: 'break-all',
            }}
          >
            {uri}
          </code>
        ))}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <button
          type="button"
          onClick={saveCredentials}
          disabled={busy || !allFilled}
          style={{
            width: '100%',
            padding: 8,
            background: busy || !allFilled ? 'var(--color-disabled-bg)' : 'var(--color-bg-tertiary)',
            color: 'var(--color-text)',
            border: '1px solid var(--color-border)',
            borderRadius: 6,
            fontSize: 12,
            cursor: 'pointer',
          }}
        >
          Save credentials
        </button>
        <button
          type="button"
          onClick={startOAuth}
          disabled={busy}
          style={{
            width: '100%',
            padding: 8,
            background: busy ? 'var(--color-disabled-bg)' : 'var(--color-accent-purple)',
            color: 'var(--color-on-accent)',
            border: 'none',
            borderRadius: 6,
            fontSize: 12,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
          }}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <ExternalLink size={14} />}
          Sign in with Google
        </button>
      </div>

      {stage && (
        <p style={{ fontSize: 11, color: 'var(--color-warning)', marginTop: 8 }}>{stage}</p>
      )}
      {error && (
        <p style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 8 }}>{error}</p>
      )}
    </div>
  );
}
