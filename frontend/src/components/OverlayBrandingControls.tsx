import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useOverlayLogo } from '../hooks/useOverlayLogo';
import { isOverlayColour, type OverlayBranding } from '../utils/overlayBranding';

export function OverlayBrandingControls({ value, onChange, onBusyChange }: {
  value: OverlayBranding;
  onChange: (value: OverlayBranding) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { authEnabled, hasPermission } = useAuth();
  const canEdit = !authEnabled || hasPermission('settings:update');
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({ from: value.from || '#00ae42', to: value.to || '#00ae42' });
  const logo = useOverlayLogo(true, null, value.logoRevision);

  const saveLogo = async (file: File | null) => {
    setBusy(true);
    onBusyChange?.(true);
    try {
      if (file) await api.uploadOverlayLogo(file);
      else await api.deleteOverlayLogo();
      onChange({ ...value, logo: file !== null, logoRevision: value.logoRevision + 1 });
    } catch (error) {
      showToast(error instanceof Error ? error.message : t('streamOverlay.branding.failed'), 'error');
    } finally {
      setBusy(false);
      onBusyChange?.(false);
    }
  };
  const setColour = (key: 'from' | 'to', colour: string) => {
    setDraft((current) => ({ ...current, [key]: colour }));
    if (isOverlayColour(colour)) {
      onChange({ ...value, from: value.from || '#00ae42', to: value.to || '#00ae42', [key]: colour });
    }
  };

  return <fieldset disabled={busy} className="my-4 space-y-3 rounded-lg border border-bambu-dark-tertiary p-4">
    <legend className="px-2 text-sm font-medium text-white">{t('streamOverlay.branding.title')}</legend>
    <p className="text-xs text-bambu-gray">{t('streamOverlay.branding.hint')}</p>
    <label className="block text-sm text-bambu-gray">
      {t('streamOverlay.branding.upload')}
      <input type="file" accept="image/png,image/webp" disabled={!canEdit}
        className="mt-1 block w-full text-sm"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) void saveLogo(file);
        }} />
    </label>
    {logo && <div className="flex flex-wrap items-center gap-3">
      <img src={logo} alt={t('streamOverlay.branding.logo')} className="h-16 max-w-40 object-contain" />
      <label className="flex items-center gap-2 text-sm text-bambu-gray">
        <input type="checkbox" checked={value.logo} onChange={(event) => onChange({ ...value, logo: event.target.checked })} />
        {t('streamOverlay.branding.logo')}
      </label>
      <button type="button" disabled={!canEdit} onClick={() => void saveLogo(null)} className="text-sm text-red-400 disabled:opacity-50">{t('common.remove')}</button>
    </div>}
    <div className="grid gap-3 sm:grid-cols-2">
      {(['from', 'to'] as const).map((key) => <div key={key}>
        <label htmlFor={`overlay-colour-${key}`} className="block text-sm text-bambu-gray mb-1">{t(`streamOverlay.branding.${key}`)}</label>
        <div className="flex items-center gap-2">
          <input id={`overlay-colour-${key}`} type="color" value={value[key] || '#00ae42'} onChange={(event) => setColour(key, event.target.value)} className="h-9 w-12 bg-transparent" />
          <input aria-label={`${t(`streamOverlay.branding.${key}`)} (hex)`} value={draft[key]} onChange={(event) => setColour(key, event.target.value)}
            aria-invalid={!isOverlayColour(draft[key])} maxLength={7} spellCheck={false}
            className="w-28 rounded border border-bambu-dark-tertiary bg-bambu-dark px-2 py-1 text-white" />
        </div>
      </div>)}
    </div>
    <button type="button" className="text-sm text-bambu-green" onClick={() => {
      setDraft({ from: '#00ae42', to: '#00ae42' });
      onChange({ ...value, from: '', to: '' });
    }}>{t('streamOverlay.branding.reset')}</button>
  </fieldset>;
}
