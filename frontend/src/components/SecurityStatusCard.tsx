import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Shield, ShieldCheck, ShieldOff, AlertTriangle, XCircle, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import type { EncryptionStatus } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { registerSettingsSearch } from '../lib/settingsSearch';

// Cross-tab search registration so this card surfaces in
// Settings → Search results under the users → security sub-tab.
registerSettingsSearch({
  labelKey: 'settings.encryption.title',
  labelFallback: 'MFA Encryption Status',
  tab: 'users',
  subTab: 'security',
  keywords: 'mfa encryption status security backup totp oidc fernet',
  anchor: 'card-mfa-encryption',
});

/**
 * Read-only status card showing the at-rest encryption state for
 * OIDC client_secret and TOTP secret rows. Five severity levels:
 *
 *   - Green: key configured, no legacy rows, no decryption-broken state.
 *   - Yellow: key configured but plaintext rows still need re-encryption.
 *   - Orange: key was auto-generated → operator must back up the key file
 *     (or set MFA_ENCRYPTION_KEY explicitly).
 *   - Red: encrypted rows exist but no key is loadable → recovery required.
 *   - Grey: encryption is not configured at all and no encrypted rows exist
 *     yet — a plain "not configured" disabled state.
 */
export function SecurityStatusCard() {
  const { t } = useTranslation();

  const { data, isLoading, isError, refetch } = useQuery<EncryptionStatus>({
    queryKey: ['encryptionStatus'],
    queryFn: () => api.getEncryptionStatus(),
    // S5: bounded auto-recovery via refetchInterval backoff + manual recovery
    // via the "Retry" button rendered in the error branch below. Previously
    // a single 5xx blip killed the live status indicator until a full page
    // reload. The queryClient-level `retry` setting is left untouched so
    // operators (production) get the default 3 internal retries while tests
    // (which set retry:false) don't have to wait for them.
    refetchInterval: (query) => {
      if (!query.state.error) return 30_000;
      // After the first error, back off: 5s, 10s, 15s, then stop until the
      // user clicks Retry or the page reloads.
      const failures = query.state.fetchFailureCount ?? 0;
      if (failures <= 3) return Math.min(5_000 * Math.max(1, failures), 30_000);
      return false;
    },
  });

  if (isLoading) {
    return (
      <Card id="card-mfa-encryption" data-testid="encryption-status-card">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Shield className="text-bambu-gray" size={20} />
            <h2 className="text-lg font-semibold">{t('settings.encryption.title')}</h2>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-bambu-gray" data-testid="encryption-loading">
            <Loader2 className="animate-spin" size={16} />
            <span>{t('common.loading')}</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (isError || !data) {
    return (
      <Card id="card-mfa-encryption" data-testid="encryption-status-card">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Shield className="text-bambu-gray" size={20} />
            <h2 className="text-lg font-semibold">{t('settings.encryption.title')}</h2>
          </div>
        </CardHeader>
        <CardContent>
          <div className="text-red-400" data-testid="encryption-error">{t('common.errorLoading')}</div>
          {/* S5: manual recovery button — the bounded auto-retry above stops
              after 3 consecutive failures so the operator needs an explicit
              way to reset polling without reloading the whole page. */}
          <button
            type="button"
            onClick={() => refetch()}
            className="mt-2 text-sm text-blue-400 underline hover:text-blue-300"
            data-testid="encryption-retry-button"
          >
            {t('common.retry')}
          </button>
        </CardContent>
      </Card>
    );
  }

  const totalLegacy = data.legacy_plaintext_rows.oidc_providers + data.legacy_plaintext_rows.user_totp;
  const totalEncrypted = data.encrypted_rows.oidc_providers + data.encrypted_rows.user_totp;

  // Severity selection — order matters: red first (recovery), then orange
  // (backup hint for auto-generated key), then yellow (legacy rows), green
  // (all good), grey (not configured at all and no encrypted rows).
  let severityClasses: string;
  let icon;
  let statusLabel: string;
  let statusBody: string;

  if (data.decryption_broken) {
    severityClasses = 'bg-red-500/20 border-red-500/50 text-red-400';
    icon = <XCircle className="text-red-400" size={20} />;
    statusLabel = t('settings.encryption.decryptionBrokenTitle');
    statusBody = t('settings.encryption.decryptionBrokenError', { count: totalEncrypted });
  } else if (data.key_source === 'generated') {
    severityClasses = 'bg-amber-500/10 border-amber-500/30 text-amber-400';
    icon = <ShieldCheck className="text-amber-400" size={20} />;
    statusLabel = t('settings.encryption.enabledGenerated');
    statusBody = t('settings.encryption.backupHint');
  } else if (totalLegacy > 0) {
    severityClasses = 'bg-amber-500/10 border-amber-500/30 text-amber-400';
    icon = <AlertTriangle className="text-amber-400" size={20} />;
    statusLabel = data.key_source === 'env' ? t('settings.encryption.enabledFromEnv') : t('settings.encryption.enabledFromFile');
    statusBody = t('settings.encryption.legacyRowsWarning', { count: totalLegacy });
  } else if (data.key_configured) {
    severityClasses = 'bg-green-500/20 border-green-500/30 text-green-400';
    icon = <ShieldCheck className="text-green-400" size={20} />;
    statusLabel = data.key_source === 'env' ? t('settings.encryption.enabledFromEnv') : t('settings.encryption.enabledFromFile');
    statusBody = t('settings.encryption.allEncrypted');
  } else {
    severityClasses = 'bg-gray-500/20 border-gray-500/30 text-gray-400';
    icon = <ShieldOff className="text-gray-400" size={20} />;
    statusLabel = t('settings.encryption.notConfigured');
    statusBody = t('settings.encryption.notConfiguredDesc');
  }

  // E4: show legacy-rows warning as a secondary alert when key is auto-generated
  // AND there are still unencrypted rows (both conditions can be true simultaneously).
  const showConcurrentLegacyWarning = data.key_source === 'generated' && totalLegacy > 0;

  return (
    <Card id="card-mfa-encryption" data-testid="encryption-status-card">
      <CardHeader>
        <div className="flex items-center gap-2">
          {icon}
          <h2 className="text-lg font-semibold">{t('settings.encryption.title')}</h2>
        </div>
      </CardHeader>
      <CardContent>
        <div
          className={`p-3 border rounded-lg ${severityClasses}`}
          data-testid="encryption-status"
        >
          <p className="font-medium mb-1">{statusLabel}</p>
          <p className="text-sm">{statusBody}</p>
        </div>
        {showConcurrentLegacyWarning && (
          <div
            className="mt-2 p-3 border rounded-lg bg-amber-500/10 border-amber-500/30 text-amber-400"
            data-testid="encryption-legacy-warning"
          >
            <p className="text-sm">{t('settings.encryption.legacyRowsWarning', { count: totalLegacy })}</p>
          </div>
        )}
        {data.migration_error_count > 0 && (
          <div
            className="mt-2 p-3 border rounded-lg bg-amber-500/10 border-amber-500/30 text-amber-400"
            data-testid="encryption-migration-warning"
          >
            <p className="text-sm">
              {t('settings.encryption.migrationErrorWarning', { count: data.migration_error_count })}
            </p>
          </div>
        )}
        <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-bambu-gray">{t('settings.encryption.encryptedRowsLabel')}</p>
            <p className="font-medium">
              OIDC: {data.encrypted_rows.oidc_providers} · TOTP: {data.encrypted_rows.user_totp}
            </p>
          </div>
          <div>
            <p className="text-bambu-gray">{t('settings.encryption.legacyRowsLabel')}</p>
            <p className="font-medium">
              OIDC: {data.legacy_plaintext_rows.oidc_providers} · TOTP: {data.legacy_plaintext_rows.user_totp}
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
