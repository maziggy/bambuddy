/**
 * Settings → API Keys → Connected apps.
 *
 * Registers external applications that sign users in with their Bambuddy
 * account ("Sign in with Bambuddy"). The client secret is shown exactly once,
 * at creation or rotation; the list only ever shows the client ID.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Copy, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { api, type ConnectedApp } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { parseUTCDate } from '../utils/date';

const QUERY_KEY = ['connected-apps'];

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = parseUTCDate(iso);
  return d ? d.toLocaleString() : '—';
}

async function copyToClipboard(value: string): Promise<void> {
  // navigator.clipboard needs a secure context; LAN installs are often plain
  // http, so fall back to a hidden textarea.
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const ta = document.createElement('textarea');
  ta.value = value;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  try {
    ta.select();
    document.execCommand('copy');
  } finally {
    document.body.removeChild(ta);
  }
}

function CopyField({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  return (
    <div>
      <p className="text-xs text-bambu-gray mb-1">{label}</p>
      <div className="flex items-center gap-2">
        <code className="flex-1 min-w-0 px-3 py-2 bg-bambu-dark rounded-md text-bambu-green text-xs break-all font-mono select-all">
          {value}
        </code>
        <button
          type="button"
          onClick={() =>
            copyToClipboard(value).then(
              () => showToast(t('connectedApps.toast.copied')),
              () => showToast(t('connectedApps.toast.copyFailed'), 'error'),
            )
          }
          className="flex items-center gap-2 px-3 py-2 bg-bambu-green text-white rounded-md hover:bg-bambu-green/90"
        >
          <Copy className="w-4 h-4" />
          {t('connectedApps.copy')}
        </button>
      </div>
    </div>
  );
}

function SecretModal({ app, onClose }: { app: ConnectedApp; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" role="dialog" aria-modal="true">
      <div className="bg-bambu-dark-secondary rounded-lg p-6 max-w-2xl w-full border border-bambu-green/40 space-y-4">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-6 h-6 text-yellow-600 dark:text-yellow-400 flex-shrink-0 mt-0.5" />
          <div>
            <h2 className="text-lg font-semibold text-white">{t('connectedApps.secret.title', { name: app.name })}</h2>
            <p className="text-sm text-bambu-gray mt-1">{t('connectedApps.secret.warning')}</p>
          </div>
        </div>
        <CopyField label={t('connectedApps.clientId')} value={app.client_id} />
        <CopyField label={t('connectedApps.clientSecret')} value={app.client_secret ?? ''} />
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 bg-bambu-dark-tertiary text-white rounded-md hover:bg-bambu-dark-tertiary/80"
          >
            {t('connectedApps.secret.dismiss')}
          </button>
        </div>
      </div>
    </div>
  );
}

function ConfirmModal({
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" role="dialog" aria-modal="true">
      <div className="bg-bambu-dark-secondary rounded-lg p-6 max-w-md w-full border border-red-500/40">
        <div className="flex items-start gap-3 mb-4">
          <AlertTriangle className="w-6 h-6 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <h2 className="text-lg font-semibold text-white">{title}</h2>
            <p className="text-sm text-bambu-gray mt-1">{body}</p>
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 bg-bambu-dark-tertiary text-white rounded-md hover:bg-bambu-dark-tertiary/80"
          >
            {t('connectedApps.cancel')}
          </button>
          <button type="button" onClick={onConfirm} className="px-4 py-2 bg-red-500 text-white rounded-md hover:bg-red-600">
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ConnectedAppsSection() {
  const { t } = useTranslation();
  const { authEnabled } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [redirectUri, setRedirectUri] = useState('');
  const [shownSecret, setShownSecret] = useState<ConnectedApp | null>(null);
  const [confirm, setConfirm] = useState<{ app: ConnectedApp; action: 'rotate' | 'delete' } | null>(null);

  const { data: apps = [], isLoading } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: api.listConnectedApps,
    enabled: authEnabled,
  });

  const onError = (err: unknown) =>
    showToast(err instanceof Error ? err.message : t('connectedApps.toast.failed'), 'error');
  const refresh = () => queryClient.invalidateQueries({ queryKey: QUERY_KEY });

  const create = useMutation({
    mutationFn: () => api.createConnectedApp({ name: name.trim(), redirect_uri: redirectUri.trim() }),
    onSuccess: (app) => {
      setName('');
      setRedirectUri('');
      setShownSecret(app);
      showToast(t('connectedApps.toast.created'));
      refresh();
    },
    onError,
  });

  const toggle = useMutation({
    mutationFn: (app: ConnectedApp) => api.updateConnectedApp(app.id, { enabled: !app.enabled }),
    onSuccess: (app) => {
      showToast(app.enabled ? t('connectedApps.toast.enabled') : t('connectedApps.toast.disabled'));
      refresh();
    },
    onError,
  });

  const rotate = useMutation({
    mutationFn: (app: ConnectedApp) => api.rotateConnectedAppSecret(app.id),
    onSuccess: (app) => {
      setShownSecret(app);
      showToast(t('connectedApps.toast.rotated'));
      refresh();
    },
    onError,
  });

  const remove = useMutation({
    mutationFn: (app: ConnectedApp) => api.deleteConnectedApp(app.id),
    onSuccess: () => {
      showToast(t('connectedApps.toast.deleted'));
      refresh();
    },
    onError,
  });

  if (!authEnabled) {
    return <p className="text-sm text-bambu-gray">{t('connectedApps.requiresAuth')}</p>;
  }

  const inputClass =
    'px-3 py-2 bg-bambu-dark rounded-md text-white border border-bambu-dark-tertiary focus:border-bambu-green focus:outline-none';

  return (
    <div className="space-y-4">
      <p className="text-sm text-bambu-gray">{t('connectedApps.description')}</p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim() && redirectUri.trim()) create.mutate();
        }}
        className="grid gap-3 md:grid-cols-[1fr_1.5fr_auto]"
      >
        <input
          type="text"
          maxLength={100}
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('connectedApps.namePlaceholder')}
          aria-label={t('connectedApps.name')}
          className={inputClass}
        />
        <input
          type="url"
          maxLength={500}
          required
          value={redirectUri}
          onChange={(e) => setRedirectUri(e.target.value)}
          placeholder="http://orders.local:8090/auth/callback"
          aria-label={t('connectedApps.callbackUrl')}
          className={inputClass}
        />
        <button
          type="submit"
          disabled={create.isPending || !name.trim() || !redirectUri.trim()}
          className="flex items-center justify-center gap-2 px-4 py-2 bg-bambu-green text-white rounded-md hover:bg-bambu-green/90 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Plus className="w-4 h-4" />
          {t('connectedApps.add')}
        </button>
      </form>
      <p className="text-xs text-bambu-gray">{t('connectedApps.callbackHint')}</p>

      {isLoading ? null : apps.length === 0 ? (
        <p className="text-sm text-bambu-gray">{t('connectedApps.empty')}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-bambu-gray border-b border-bambu-dark-tertiary">
                <th className="py-2 px-3 font-medium">{t('connectedApps.name')}</th>
                <th className="py-2 px-3 font-medium">{t('connectedApps.clientId')}</th>
                <th className="py-2 px-3 font-medium">{t('connectedApps.callbackUrl')}</th>
                <th className="py-2 px-3 font-medium">{t('connectedApps.lastUsed')}</th>
                <th className="py-2 px-3" />
              </tr>
            </thead>
            <tbody>
              {apps.map((app) => (
                <tr key={app.id} className="border-b border-bambu-dark-tertiary last:border-b-0">
                  <td className="py-3 px-3 text-white">
                    {app.name}
                    {!app.enabled && (
                      <span className="ml-2 px-2 py-0.5 text-xs rounded bg-bambu-dark-tertiary text-bambu-gray">
                        {t('connectedApps.disabledBadge')}
                      </span>
                    )}
                  </td>
                  <td className="py-3 px-3 text-bambu-gray font-mono text-xs">{app.client_id}</td>
                  <td className="py-3 px-3 text-bambu-gray text-xs break-all">{app.redirect_uri}</td>
                  <td className="py-3 px-3 text-bambu-gray">{formatDate(app.last_used_at)}</td>
                  <td className="py-3 px-3">
                    <div className="flex justify-end gap-3 whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => toggle.mutate(app)}
                        className="text-sm text-bambu-gray hover:text-white"
                      >
                        {app.enabled ? t('connectedApps.disable') : t('connectedApps.enable')}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirm({ app, action: 'rotate' })}
                        className="inline-flex items-center gap-1 text-sm text-bambu-gray hover:text-white"
                      >
                        <RefreshCw className="w-4 h-4" />
                        {t('connectedApps.rotate')}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirm({ app, action: 'delete' })}
                        className="inline-flex items-center gap-1 text-sm text-red-700 dark:text-red-400 hover:text-red-900 dark:hover:text-red-300"
                      >
                        <Trash2 className="w-4 h-4" />
                        {t('connectedApps.delete')}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {shownSecret && <SecretModal app={shownSecret} onClose={() => setShownSecret(null)} />}
      {confirm && (
        <ConfirmModal
          title={
            confirm.action === 'rotate'
              ? t('connectedApps.confirmRotate.title')
              : t('connectedApps.confirmDelete.title')
          }
          body={
            confirm.action === 'rotate'
              ? t('connectedApps.confirmRotate.body', { name: confirm.app.name })
              : t('connectedApps.confirmDelete.body', { name: confirm.app.name })
          }
          confirmLabel={confirm.action === 'rotate' ? t('connectedApps.rotate') : t('connectedApps.delete')}
          onCancel={() => setConfirm(null)}
          onConfirm={() => {
            if (confirm.action === 'rotate') rotate.mutate(confirm.app);
            else remove.mutate(confirm.app);
            setConfirm(null);
          }}
        />
      )}
    </div>
  );
}
