import { useState, type ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Edit2, Trash2, Globe, Check, X, RefreshCw, ExternalLink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import type { OIDCProvider, OIDCProviderCreate } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { Toggle } from './Toggle';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';

const EMPTY_FORM: OIDCProviderCreate = {
  name: '',
  issuer_url: '',
  client_id: '',
  client_secret: '',
  scopes: 'openid email profile',
  is_enabled: true,
  auto_create_users: false,
  auto_link_existing_accounts: false,
  email_claim: 'email',
  require_email_verified: true,
  icon_url: undefined,
};

// ─── Provider form (create / edit) ───────────────────────────────────────────
function ProviderForm({
  initial,
  isEdit = false,
  onSave,
  onCancel,
  isPending,
}: {
  initial: OIDCProviderCreate;
  isEdit?: boolean;
  onSave: (data: OIDCProviderCreate) => void;
  onCancel: () => void;
  isPending: boolean;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<OIDCProviderCreate>(initial);
  const [secretChanged, setSecretChanged] = useState(false);
  const set = (key: keyof OIDCProviderCreate, value: unknown) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const inputCls =
    'w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors text-sm';
  const labelCls = 'block text-sm font-medium text-white mb-1';

  const handleSave = () => {
    const payload = { ...form };
    if (isEdit && !secretChanged) {
      delete (payload as Partial<OIDCProviderCreate>).client_secret;
    }
    onSave(payload);
  };

  const autoLinkOn = form.auto_link_existing_accounts === true;
  const emailVerifiedOn = form.require_email_verified ?? true;
  let requireEmailVerifiedDesc: ReactNode;
  if (autoLinkOn) {
    requireEmailVerifiedDesc = t('settings.oidc.form.requireEmailVerifiedAutoLink');
  } else if (emailVerifiedOn) {
    requireEmailVerifiedDesc = t('settings.oidc.form.requireEmailVerifiedDesc');
  } else {
    requireEmailVerifiedDesc = (
      <span className="text-red-400">{t('settings.oidc.form.requireEmailVerifiedWarning')}</span>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className={labelCls}>{t('settings.oidc.form.name')} <span className="text-red-400">*</span></label>
          <input className={inputCls} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Google" />
        </div>
        <div>
          <label className={labelCls}>{t('settings.oidc.form.issuerUrl')} <span className="text-red-400">*</span></label>
          <input className={inputCls} value={form.issuer_url} onChange={(e) => set('issuer_url', e.target.value)} placeholder="https://accounts.google.com" />
        </div>
        <div>
          <label className={labelCls}>{t('settings.oidc.form.clientId')} <span className="text-red-400">*</span></label>
          <input className={inputCls} value={form.client_id} onChange={(e) => set('client_id', e.target.value)} placeholder="your-client-id" />
        </div>
        <div>
          <label className={labelCls}>
            {t('settings.oidc.form.clientSecret')}
            {!isEdit && <span className="text-red-400"> *</span>}
            {isEdit && <span className="text-bambu-gray text-xs ml-1">({t('settings.oidc.form.secretHint')})</span>}
          </label>
          <input
            className={inputCls}
            type="password"
            value={secretChanged ? form.client_secret : ''}
            placeholder={isEdit && !secretChanged ? '••••••••' : t('settings.oidc.form.secretPlaceholder')}
            onChange={(e) => {
              setSecretChanged(true);
              set('client_secret', e.target.value);
            }}
          />
        </div>
        <div>
          <label className={labelCls}>{t('settings.oidc.form.scopes')}</label>
          <input className={inputCls} value={form.scopes} onChange={(e) => set('scopes', e.target.value)} placeholder="openid email profile" />
        </div>
        <div>
          <label className={labelCls}>{t('settings.oidc.form.iconUrl')}</label>
          <input className={inputCls} value={form.icon_url ?? ''} onChange={(e) => set('icon_url', e.target.value || undefined)} placeholder="https://..." />
        </div>
      </div>

      <div className="flex flex-wrap gap-6 pt-2">
        <label className="flex items-center gap-3 cursor-pointer">
          <Toggle checked={form.is_enabled ?? true} onChange={(v) => set('is_enabled', v)} />
          <span className="text-white text-sm">{t('settings.oidc.form.enabled')}</span>
        </label>
        <label className="flex items-center gap-3 cursor-pointer">
          <Toggle checked={form.auto_create_users ?? false} onChange={(v) => set('auto_create_users', v)} />
          <div>
            <p className="text-white text-sm">{t('settings.oidc.form.autoCreate')}</p>
            <p className="text-bambu-gray text-xs">{t('settings.oidc.form.autoCreateDesc')}</p>
          </div>
        </label>
        <label className="flex items-center gap-3 cursor-pointer w-full">
          <Toggle checked={form.auto_link_existing_accounts ?? false} onChange={(v) => set('auto_link_existing_accounts', v)} />
          <div>
            <p className="text-white text-sm">{t('settings.oidc.form.autoLink')}</p>
            <p className="text-bambu-gray text-xs">{t('settings.oidc.form.autoLinkDesc')}</p>
          </div>
        </label>
        <label className="flex items-center gap-3 cursor-pointer w-full">
          <Toggle
            checked={emailVerifiedOn}
            onChange={(v) => set('require_email_verified', v)}
            disabled={autoLinkOn}
          />
          <div>
            <p className="text-white text-sm">{t('settings.oidc.form.requireEmailVerified')}</p>
            <p className="text-bambu-gray text-xs">{requireEmailVerifiedDesc}</p>
          </div>
        </label>
      </div>

      <div>
        <label className={labelCls}>{t('settings.oidc.form.emailClaim')}</label>
        <input
          className={inputCls}
          value={form.email_claim}
          onChange={(e) => set('email_claim', e.target.value || 'email')}
          placeholder={t('settings.oidc.form.emailClaimPlaceholder')}
        />
        <p className="text-bambu-gray text-xs mt-1">{t('settings.oidc.form.emailClaimDesc')}</p>
      </div>

      <div className="flex gap-3 pt-2">
        <Button variant="secondary" onClick={onCancel} className="flex-1">
          {t('common.cancel')}
        </Button>
        <Button
          variant="primary"
          className="flex-1"
          disabled={!form.name || !form.issuer_url || !form.client_id || (!isEdit && !form.client_secret) || (isEdit && secretChanged && !form.client_secret) || isPending}
          onClick={handleSave}
        >
          {isPending ? t('common.saving') : t('common.save')}
        </Button>
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export function OIDCProviderSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [showCreate, setShowCreate] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<OIDCProvider | null>(null);

  const { data: providers, isLoading } = useQuery({
    queryKey: ['oidc-providers-all'],
    queryFn: () => api.getOIDCProvidersAll(),
  });

  const createMutation = useMutation({
    mutationFn: (data: OIDCProviderCreate) => api.createOIDCProvider(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['oidc-providers-all'] });
      setShowCreate(false);
      showToast(t('settings.oidc.created'), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<OIDCProviderCreate> }) =>
      api.updateOIDCProvider(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['oidc-providers-all'] });
      setEditingId(null);
      showToast(t('settings.oidc.updated'), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteOIDCProvider(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['oidc-providers-all'] });
      setDeleteTarget(null);
      showToast(t('settings.oidc.deleted'), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const toggleEnabled = (provider: OIDCProvider) =>
    updateMutation.mutate({ id: provider.id, data: { is_enabled: !provider.is_enabled } });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <RefreshCw className="w-6 h-6 animate-spin text-bambu-green" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <Card id="card-oidc">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-white font-semibold">{t('settings.oidc.title')}</h3>
              <p className="text-bambu-gray text-sm">{t('settings.oidc.desc')}</p>
            </div>
            {!showCreate && (
              <Button variant="primary" size="sm" onClick={() => setShowCreate(true)} className="flex items-center gap-2">
                <Plus className="w-4 h-4" />
                {t('settings.oidc.addProvider')}
              </Button>
            )}
          </div>
        </CardHeader>

        {showCreate && (
          <CardContent>
            <div className="border-t border-bambu-dark-tertiary pt-4">
              <h4 className="text-white font-medium mb-4">{t('settings.oidc.newProvider')}</h4>
              <ProviderForm
                initial={EMPTY_FORM}
                onSave={(data) => createMutation.mutate(data)}
                onCancel={() => setShowCreate(false)}
                isPending={createMutation.isPending}
              />
            </div>
          </CardContent>
        )}
      </Card>

      {/* Provider list */}
      {providers && providers.length === 0 && !showCreate && (
        <Card id="card-oidc-empty">
          <CardContent>
            <div className="text-center py-8 space-y-3">
              <Globe className="w-12 h-12 text-bambu-gray mx-auto" />
              <p className="text-bambu-gray">{t('settings.oidc.empty')}</p>
              <Button variant="primary" size="sm" onClick={() => setShowCreate(true)} className="inline-flex items-center gap-2">
                <Plus className="w-4 h-4" />
                {t('settings.oidc.addProvider')}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {providers?.map((provider) => (
        <Card key={provider.id}>
          <CardHeader>
            <div className="flex items-center gap-3">
              {provider.icon_url ? (
                <img src={provider.icon_url} alt={provider.name} className="w-8 h-8 rounded object-contain" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
              ) : (
                <div className="w-8 h-8 rounded-full bg-bambu-dark-tertiary flex items-center justify-center">
                  <Globe className="w-4 h-4 text-bambu-gray" />
                </div>
              )}
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h4 className="text-white font-medium">{provider.name}</h4>
                  {provider.is_enabled ? (
                    <span className="flex items-center gap-1 text-xs text-green-400 bg-green-400/10 px-2 py-0.5 rounded-full">
                      <Check className="w-3 h-3" /> {t('common.enabled')}
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-xs text-bambu-gray bg-bambu-dark-tertiary px-2 py-0.5 rounded-full">
                      <X className="w-3 h-3" /> {t('common.disabled')}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 text-bambu-gray text-xs mt-0.5">
                  <ExternalLink className="w-3 h-3" />
                  <span>{provider.issuer_url}</span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Toggle
                  checked={provider.is_enabled}
                  onChange={() => toggleEnabled(provider)}
                  disabled={updateMutation.isPending}
                />
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setEditingId(editingId === provider.id ? null : provider.id)}
                >
                  <Edit2 className="w-4 h-4" />
                </Button>
                <Button variant="danger" size="sm" onClick={() => setDeleteTarget(provider)}>
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            </div>
          </CardHeader>

          {editingId === provider.id && (
            <CardContent>
              <div className="border-t border-bambu-dark-tertiary pt-4">
                <ProviderForm
                  isEdit={true}
                  initial={{
                    name: provider.name,
                    issuer_url: provider.issuer_url,
                    client_id: provider.client_id,
                    client_secret: '',
                    scopes: provider.scopes,
                    is_enabled: provider.is_enabled,
                    auto_create_users: provider.auto_create_users,
                    auto_link_existing_accounts: provider.auto_link_existing_accounts,
                    email_claim: provider.email_claim,
                    require_email_verified: provider.require_email_verified,
                    icon_url: provider.icon_url ?? undefined,
                  }}
                  onSave={(data) => updateMutation.mutate({ id: provider.id, data })}
                  onCancel={() => setEditingId(null)}
                  isPending={updateMutation.isPending}
                />
              </div>
            </CardContent>
          )}

          {editingId !== provider.id && (
            <CardContent>
              <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-2 text-sm">
                <div>
                  <dt className="text-bambu-gray">{t('settings.oidc.form.clientId')}</dt>
                  <dd className="text-white font-mono truncate">{provider.client_id}</dd>
                </div>
                <div>
                  <dt className="text-bambu-gray">{t('settings.oidc.form.scopes')}</dt>
                  <dd className="text-white">{provider.scopes}</dd>
                </div>
                <div>
                  <dt className="text-bambu-gray">{t('settings.oidc.form.autoCreate')}</dt>
                  <dd className={provider.auto_create_users ? 'text-green-400' : 'text-bambu-gray'}>
                    {provider.auto_create_users ? t('common.yes') : t('common.no')}
                  </dd>
                </div>
                <div>
                  <dt className="text-bambu-gray">{t('settings.oidc.form.autoLink')}</dt>
                  <dd className={provider.auto_link_existing_accounts ? 'text-green-400' : 'text-bambu-gray'}>
                    {provider.auto_link_existing_accounts ? t('common.yes') : t('common.no')}
                  </dd>
                </div>
                <div>
                  <dt className="text-bambu-gray">{t('settings.oidc.form.emailClaim')}</dt>
                  <dd className="text-white font-mono">{provider.email_claim}</dd>
                </div>
                <div>
                  <dt className="text-bambu-gray">{t('settings.oidc.form.requireEmailVerified')}</dt>
                  <dd className={provider.require_email_verified ? 'text-green-400' : 'text-red-400'}>
                    {provider.require_email_verified ? t('common.yes') : t('common.no')}
                  </dd>
                </div>
              </dl>
            </CardContent>
          )}
        </Card>
      ))}

      {/* Delete confirm */}
      {deleteTarget && (
        <ConfirmModal
          title={t('settings.oidc.deleteTitle')}
          message={t('settings.oidc.deleteMessage', { name: deleteTarget.name })}
          confirmText={t('common.delete')}
          variant="danger"
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
