/**
 * Streaming-overlay URL builder (#1422).
 *
 * The overlay at /overlay/{printerId} has been configurable by query string
 * since #2613, but only for people who found the parameters in the wiki. The
 * issue asked for the field set to be selectable "through the web UI"; this is
 * that surface. Appearance is configured in the URL. Tokens entered, imported, or created
 * here remain in component memory; existing credentials cannot be recovered.
 * The URL is the configuration so an OBS browser source needs no saved server-side profile.
 * The choices that build it are remembered in this browser so the builder
 * reopens as it was left; the token never is.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Copy, ExternalLink, Eye, EyeOff } from 'lucide-react';
import { api, type Printer } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { OverlayBrandingControls } from './OverlayBrandingControls';
import { DEFAULT_BRANDING, overlayGradient } from '../utils/overlayBranding';
import { useAuth } from '../contexts/AuthContext';
import { CreateTokenForm } from '../pages/CameraTokensPage';
import { NumberInput } from './NumberInput';
import { OverlayFrame } from './OverlayFrame';
import { OVERLAY_DIMENSIONS, type OverlayLayout } from '../utils/overlayLayout';

type OverlaySize = 'small' | 'medium' | 'large';

// Order matters: it is the order the fields appear in the overlay, so the
// checkbox list reads as a preview of the result.
const FIELDS = [
  { key: 'printer', labelKey: 'streamOverlay.builder.fieldPrinter', fallback: 'Printer name' },
  { key: 'model', labelKey: 'streamOverlay.builder.fieldModel', fallback: 'Printer model' },
  { key: 'filename', labelKey: 'streamOverlay.builder.fieldFilename', fallback: 'File name' },
  { key: 'status', labelKey: 'streamOverlay.builder.fieldStatus', fallback: 'Status' },
  { key: 'progress', labelKey: 'streamOverlay.builder.fieldProgress', fallback: 'Progress bar' },
  { key: 'layers', labelKey: 'streamOverlay.builder.fieldLayers', fallback: 'Layer count' },
  { key: 'eta', labelKey: 'streamOverlay.builder.fieldEta', fallback: 'Time remaining and ETA' },
  { key: 'nozzle', labelKey: 'printers.heaterHistory.nozzle', fallback: 'Nozzle' },
  { key: 'bed', labelKey: 'printers.heaterHistory.bed', fallback: 'Bed' },
  { key: 'chamber', labelKey: 'printers.heaterHistory.chamber', fallback: 'Chamber' },
] as const;

// Matches parseConfig() in StreamOverlayPage: the fields an overlay shows when
// the URL carries no ?show= at all.
const DEFAULT_FIELDS = ['progress', 'layers', 'eta', 'filename', 'status'];

const DEFAULT_FPS = 15;

// Everything that shapes the URL except the token, which stays in memory only.
const STORAGE_KEY = 'bambuddy.streamOverlayBuilder';

interface SavedChoices {
  printerId: number | null;
  fields: string[];
  size: OverlaySize;
  fps: number;
  artwork: '1' | '2';
  backgroundTransparency: number;
  showCamera: boolean;
  layout: OverlayLayout | 'both';
  logo: boolean;
  from: string;
  to: string;
}

const DEFAULT_CHOICES: SavedChoices = {
  printerId: null,
  fields: DEFAULT_FIELDS,
  size: 'medium',
  fps: DEFAULT_FPS,
  artwork: '1',
  backgroundTransparency: 0,
  showCamera: true,
  layout: 'landscape',
  logo: false,
  from: '',
  to: '',
};

/** The stored choices, each checked the way an imported URL is; anything off falls back to its default. */
function loadChoices(): SavedChoices {
  let raw: unknown;
  try {
    raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? 'null');
  } catch {
    return DEFAULT_CHOICES;
  }
  if (!raw || typeof raw !== 'object') return DEFAULT_CHOICES;
  const v = raw as Record<string, unknown>;
  const num = (value: unknown, min: number, max: number, fallback: number) =>
    typeof value === 'number' && Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;
  const gradient = typeof v.from === 'string' && typeof v.to === 'string' && overlayGradient(v.from, v.to);
  return {
    printerId: typeof v.printerId === 'number' && Number.isSafeInteger(v.printerId) && v.printerId > 0 ? v.printerId : null,
    fields: Array.isArray(v.fields)
      ? v.fields.filter((field): field is string => FIELDS.some((f) => f.key === field))
      : DEFAULT_FIELDS,
    size: v.size === 'small' || v.size === 'large' ? v.size : 'medium',
    fps: Math.round(num(v.fps, 1, 30, DEFAULT_FPS)),
    artwork: v.artwork === '2' ? '2' : '1',
    backgroundTransparency: num(v.backgroundTransparency, 0, 100, 0),
    showCamera: v.showCamera !== false,
    layout: v.layout === 'portrait' || v.layout === 'both' ? v.layout : 'landscape',
    logo: v.logo === true,
    from: gradient ? (v.from as string) : '',
    to: gradient ? (v.to as string) : '',
  };
}

// One toast once a burst of edits settles: typing a colour or dragging the
// transparency slider changes the choices many times in a row.
const SAVED_TOAST_DELAY_MS = 600;

function saveChoices(choices: SavedChoices): boolean {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(choices));
    return true;
  } catch {
    // Private windows and full or blocked storage: the builder still works,
    // it just starts from the defaults next time.
    return false;
  }
}

export function StreamOverlayBuilder({ onTokenCreated }: { onTokenCreated?: () => void }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { user, hasPermission } = useAuth();
  const [creatingToken, setCreatingToken] = useState(false);
  const [submittingToken, setSubmittingToken] = useState(false);
  const [revealToken, setRevealToken] = useState(false);
  const [manualToken, setManualToken] = useState('');
  const token = manualToken;
  const [importUrl, setImportUrl] = useState('');
  const [importError, setImportError] = useState(false);

  const [saved] = useState(loadChoices);
  const lastSavedRef = useRef<string | null>(null);
  // Set when the builder itself replaces a remembered printer that no longer
  // exists: that save is not the user's, so it gets no toast.
  const quietSaveRef = useRef(false);
  const savedToastRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [printers, setPrinters] = useState<Printer[]>([]);
  const [printerId, setPrinterId] = useState<number | null>(saved.printerId);
  const [fields, setFields] = useState<string[]>(saved.fields);
  const [size, setSize] = useState<OverlaySize>(saved.size);
  const [fps, setFps] = useState(saved.fps);
  // '1' is the original overlay; the renderer is picked by version, not by a
  // name like "updated" that stops being true once there's a newer one.
  const [artwork, setArtwork] = useState<'1' | '2'>(saved.artwork);
  const [backgroundTransparency, setBackgroundTransparency] = useState(saved.backgroundTransparency);
  const [showCamera, setShowCamera] = useState(saved.showCamera);
  const [branding, setBranding] = useState({ ...DEFAULT_BRANDING, logo: saved.logo, from: saved.from, to: saved.to });
  const [layout, setLayout] = useState<OverlayLayout | 'both'>(saved.layout);
  const [brandingImportRevision, setBrandingImportRevision] = useState(0);
  const [brandingBusy, setBrandingBusy] = useState(false);
  const [preview, setPreview] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await api.getPrinters();
        if (cancelled) return;
        setPrinters(list);
        // A remembered printer that has since been deleted falls back to the
        // first one. An imported URL may name a printer of another server, so
        // only the remembered id is checked.
        if (list.length > 0) {
          setPrinterId((current) => {
            const next = current === null || (current === saved.printerId && !list.some((p) => p.id === current))
              ? list[0].id : current;
            if (next !== current) quietSaveRef.current = true;
            return next;
          });
        }
      } catch {
        // A failed printer list only costs the picker its options — the builder
        // still works if the user types a printer number into the URL by hand,
        // so this is not worth a toast on a settings page they may just be
        // scrolling past.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [saved.printerId]);

  useEffect(() => {
    const choices: SavedChoices = {
      printerId, fields, size, fps, artwork, backgroundTransparency, showCamera, layout,
      logo: branding.logo, from: branding.from, to: branding.to,
    };
    const serialized = JSON.stringify(choices);
    if (serialized === lastSavedRef.current) return;
    // The first pass only writes back what was just loaded.
    const loading = lastSavedRef.current === null || quietSaveRef.current;
    lastSavedRef.current = serialized;
    quietSaveRef.current = false;
    if (!saveChoices(choices) || loading) return;
    if (savedToastRef.current) clearTimeout(savedToastRef.current);
    savedToastRef.current = setTimeout(() => {
      savedToastRef.current = null;
      showToast(t('settings.toast.settingsSaved'));
    }, SAVED_TOAST_DELAY_MS);
  }, [printerId, fields, size, fps, artwork, backgroundTransparency, showCamera, layout, branding.logo, branding.from, branding.to, showToast, t]);

  useEffect(() => () => {
    if (savedToastRef.current) clearTimeout(savedToastRef.current);
  }, []);

  const resetChoices = () => {
    setPrinterId(printers[0]?.id ?? null);
    setFields(DEFAULT_CHOICES.fields);
    setSize(DEFAULT_CHOICES.size);
    setFps(DEFAULT_CHOICES.fps);
    setArtwork(DEFAULT_CHOICES.artwork);
    setBackgroundTransparency(DEFAULT_CHOICES.backgroundTransparency);
    setShowCamera(DEFAULT_CHOICES.showCamera);
    setLayout(DEFAULT_CHOICES.layout);
    // The uploaded logo stays on the server; only whether this URL uses it resets.
    setBranding((current) => ({ ...current, logo: false, from: '', to: '' }));
    setBrandingImportRevision((revision) => revision + 1);
    setPreview(false);
  };

  const outputs = useMemo(() => {
    const id = printerId ?? 1;
    const params = new URLSearchParams();
    // Emit ?show= in the canonical field order rather than click order, so the
    // same selection always produces the same URL.
    const selected = FIELDS.filter((f) => fields.includes(f.key)).map((f) => f.key);
    params.set('show', selected.join(','));
    if (size !== 'medium') params.set('size', size);
    if (fps !== DEFAULT_FPS) params.set('fps', String(fps));
    if (artwork !== '1') params.set('artwork', artwork);
    if (artwork === '2' && backgroundTransparency > 0) {
      params.set('backgroundTransparency', String(backgroundTransparency));
    }
    if (!showCamera) params.set('camera', 'false');
    if (branding.logo) params.set('logo', '1');
    if (branding.from && branding.to) {
      params.set('progressFrom', branding.from);
      params.set('progressTo', branding.to);
    }
    if (token.trim()) params.set('token', token.trim());
    const layouts: OverlayLayout[] = layout === 'both' ? ['landscape', 'portrait'] : [layout];
    return layouts.map((orientation) => {
      if (orientation === 'portrait') params.set('layout', orientation);
      else params.delete('layout');
      return { layout: orientation, url: `${window.location.origin}/overlay/${id}?${params.toString()}` };
    });
  }, [printerId, fields, size, fps, showCamera, token, artwork, layout, branding, backgroundTransparency]);

  const displayedUrl = (url: string) => {
    const masked = new URL(url);
    if (masked.searchParams.has('token') && !revealToken) masked.searchParams.set('token', '****');
    return masked.toString();
  };

  const importExistingUrl = () => {
    try {
      const parsed = new URL(importUrl.trim());
      const match = /^\/overlay\/([1-9]\d*)\/?$/.exec(parsed.pathname);
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || !match) throw new Error();
      const id = Number(match[1]);
      if (!Number.isSafeInteger(id)) throw new Error();
      const params = parsed.searchParams;
      if (params.getAll('token').length > 1) throw new Error();
      const importedFields = params.has('show') ? params.get('show')!.split(',').filter(
        (field) => FIELDS.some((supportedField) => supportedField.key === field),
      ) : DEFAULT_FIELDS;
      const sizeParam = params.get('size');
      const importedSize = sizeParam === 'small' || sizeParam === 'large' ? sizeParam : 'medium';
      const fpsParam = parseInt(params.get('fps') || '15', 10);
      const importedFps = Math.min(Math.max(Number.isNaN(fpsParam) ? DEFAULT_FPS : fpsParam, 1), 30);
      const transparencyParam = Number(params.get('backgroundTransparency'));
      const importedTransparency = Number.isFinite(transparencyParam)
        ? Math.min(100, Math.max(0, transparencyParam)) : 0;
      const from = params.get('progressFrom');
      const to = params.get('progressTo');
      const validGradient = overlayGradient(from, to);
      // Validate everything before updating any state. Never open the imported origin.
      setPrinterId(id);
      setLayout(params.get('layout') === 'portrait' ? 'portrait' : 'landscape');
      setFields(importedFields);
      setSize(importedSize as OverlaySize);
      setFps(importedFps);
      setArtwork(params.get('artwork') === '2' ? '2' : '1');
      setShowCamera(!['false', '0'].includes(params.get('camera') ?? ''));
      setBackgroundTransparency(importedTransparency);
      setBranding((current) => ({
        ...current, logo: params.get('logo') === '1',
        from: validGradient ? from! : '', to: validGradient ? to! : '',
      }));
      setBrandingImportRevision((revision) => revision + 1);
      setManualToken(params.get('token') ?? '');
      setPreview(false);
      setRevealToken(false);
      setImportUrl('');
      setImportError(false);
    } catch {
      setImportError(true);
    }
  };

  const toggleField = (key: string) => {
    setFields((prev) => (prev.includes(key) ? prev.filter((f) => f !== key) : [...prev, key]));
  };

  const copyUrl = async (url: string) => {
    try {
      // Same fallback as the token dialog: the clipboard API needs a secure
      // context, and plenty of Bambuddy installs are plain HTTP on a LAN.
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(url);
      } else {
        const ta = document.createElement('textarea');
        ta.value = url;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        try {
          ta.select();
          if (!document.execCommand('copy')) throw new Error();
        } finally {
          document.body.removeChild(ta);
        }
      }
      showToast(t('cameraTokens.toast.copied', 'Copied to clipboard'));
    } catch {
      showToast(t('cameraTokens.toast.copyFailed', 'Copy failed — select and copy manually'), 'error');
    }
  };

  return (
    <div className="@container/overlay min-w-0">
      <p className="text-sm text-bambu-gray mb-4">
        {t(
          'streamOverlay.builder.description',
          'Build the URL for a streaming overlay — a full-screen camera view with live print data drawn over it, for OBS, a wall display, or any browser source. Pick the fields you want and copy the URL.',
        )}
      </p>
      <div className="-mt-2 mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-bambu-gray">
        <span>{t('streamOverlay.builder.rememberedHint')}</span>
        <button type="button" onClick={resetChoices} disabled={submittingToken || brandingBusy}
          className="text-bambu-green hover:underline disabled:opacity-50">
          {t('streamOverlay.builder.resetChoices')}
        </button>
      </div>

      <div className="mb-4 space-y-2">
        <label htmlFor="overlay-builder-import" className="block text-sm font-medium text-white">{t('streamOverlay.builder.importUrl')}</label>
        <input id="overlay-builder-import" type="password" autoComplete="new-password" spellCheck={false} value={importUrl} disabled={submittingToken || brandingBusy}
          onChange={(event) => { setImportUrl(event.target.value); setImportError(false); }}
          className="w-full min-w-0 px-3 py-2 bg-bambu-dark rounded-md text-white border border-bambu-dark-tertiary" />
        <button type="button" disabled={submittingToken || brandingBusy || !importUrl.trim()} onClick={importExistingUrl} className="px-3 py-2 bg-bambu-dark-tertiary text-white rounded-md disabled:opacity-50">{t('streamOverlay.builder.importAction')}</button>
        <p className="text-xs text-bambu-gray">{t('streamOverlay.builder.importHint')}</p>
        {importError && <p role="alert" className="text-sm text-red-400">{t('streamOverlay.builder.importError')}</p>}
      </div>

      <div className="mb-4 space-y-2">
        {user && hasPermission('camera:view') && (
          <button type="button" disabled={submittingToken} onClick={() => setCreatingToken((current) => !current)} className="px-3 py-2 bg-bambu-dark-tertiary text-white rounded-md">
            {t(creatingToken ? 'common.cancel' : 'streamOverlay.builder.createToken')}
          </button>
        )}
        {creatingToken && user && hasPermission('camera:view') && <CreateTokenForm fixedScope="overlay" onSubmittingChange={setSubmittingToken} onCreated={(created) => {
          onTokenCreated?.();
          if (!created.token) return;
          setPreview(false);
          setManualToken(created.token);
          setRevealToken(false);
          setCreatingToken(false);
        }} />}
      </div>
      <div className="grid grid-cols-1 gap-4 @min-[28rem]/overlay:grid-cols-2">
        <div>
          <label
            htmlFor="overlay-builder-printer"
            className="block text-sm font-medium text-white mb-1"
          >
            {t('streamOverlay.builder.printer', 'Printer')}
          </label>
          <select
            id="overlay-builder-printer"
            value={printerId ?? ''}
            onChange={(e) => setPrinterId(Number(e.target.value))}
            className="w-full px-3 py-2 bg-bambu-dark rounded-md text-white border border-bambu-dark-tertiary focus:border-bambu-green focus:outline-none"
          >
            {printers.length === 0 && <option value="">{t('common.loading', 'Loading…')}</option>}
            {printerId !== null && !printers.some((printer) => printer.id === printerId) && <option value={printerId}>{printerId}</option>}
            {printers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="overlay-builder-size" className="block text-sm font-medium text-white mb-1">
            {t('streamOverlay.builder.size', 'Text size')}
          </label>
          <select
            id="overlay-builder-size"
            value={size}
            onChange={(e) => setSize(e.target.value as OverlaySize)}
            className="w-full px-3 py-2 bg-bambu-dark rounded-md text-white border border-bambu-dark-tertiary focus:border-bambu-green focus:outline-none"
          >
            <option value="small">{t('streamOverlay.builder.sizeSmall', 'Small')}</option>
            <option value="medium">{t('streamOverlay.builder.sizeMedium', 'Medium')}</option>
            <option value="large">{t('streamOverlay.builder.sizeLarge', 'Large')}</option>
          </select>
        </div>

        <div>
          <label htmlFor="overlay-builder-layout" className="block text-sm font-medium text-white mb-1">
            {t('streamOverlay.builder.layout')}
          </label>
          <select id="overlay-builder-layout" value={layout}
            onChange={(e) => {
              const value = e.target.value;
              if (value === 'landscape' || value === 'portrait' || value === 'both') setLayout(value);
            }}
            className="w-full px-3 py-2 bg-bambu-dark rounded-md text-white border border-bambu-dark-tertiary focus:border-bambu-green focus:outline-none">
            <option value="landscape">{t('streamOverlay.builder.landscape')}</option>
            <option value="portrait">{t('streamOverlay.builder.portrait')}</option>
            <option value="both">{t('streamOverlay.builder.both')}</option>
          </select>
        </div>

        <div>
          <label htmlFor="overlay-builder-artwork" className="block text-sm font-medium text-white mb-1">
            {t('streamOverlay.builder.artwork', 'Artwork')}
          </label>
          <select
            id="overlay-builder-artwork"
            value={artwork}
            onChange={(e) => setArtwork(e.target.value as '1' | '2')}
            className="w-full px-3 py-2 bg-bambu-dark rounded-md text-white border border-bambu-dark-tertiary focus:border-bambu-green focus:outline-none"
          >
            <option value="1">{t('streamOverlay.builder.artworkClassic', 'Classic')}</option>
            <option value="2">{t('streamOverlay.builder.artworkV2', 'Version 2')}</option>
          </select>
        </div>

        {artwork === '2' && (
          <div>
            <label htmlFor="overlay-builder-background-transparency" className="flex justify-between gap-2 text-sm font-medium text-white mb-1">
              <span>{t('streamOverlay.builder.backgroundTransparency')}</span>
              <span aria-hidden="true">{backgroundTransparency}%</span>
            </label>
            <input
              id="overlay-builder-background-transparency"
              type="range"
              min={0}
              max={100}
              step={1}
              value={backgroundTransparency}
              aria-valuetext={`${backgroundTransparency}%`}
              onChange={(event) => setBackgroundTransparency(Number(event.target.value))}
              className="w-full accent-bambu-green"
            />
            <p className="text-xs text-bambu-gray mt-1">{t('streamOverlay.builder.backgroundTransparencyHint')}</p>
          </div>
        )}

        <div>
          <label htmlFor="overlay-builder-fps" className="block text-sm font-medium text-white mb-1">
            {t('streamOverlay.builder.fps', 'Frame rate')}
          </label>
          <NumberInput
            id="overlay-builder-fps"
            min={1}
            max={30}
            value={fps}
            onChange={setFps}
            fallback={1}
            className="w-full px-3 py-2 bg-bambu-dark rounded-md text-white border border-bambu-dark-tertiary focus:border-bambu-green focus:outline-none"
          />
          <p className="text-xs text-bambu-gray mt-1">
            {t(
              'streamOverlay.builder.fpsHint',
              'A1 and P1 cameras top out around 5 fps whatever you ask for.',
            )}
          </p>
        </div>

        <div>
          <label htmlFor="overlay-builder-manual-token" className="block text-sm font-medium text-white mt-3 mb-1">
            {t('streamOverlay.builder.manualToken')}
          </label>
          <input
            id="overlay-builder-manual-token"
            type={revealToken ? 'text' : 'password'}
            value={manualToken}
            disabled={submittingToken}
            autoComplete="new-password"
            spellCheck={false}
            onChange={(event) => {
              setPreview(false);
              setRevealToken(false);
              setManualToken(event.target.value);
            }}
            placeholder="bblt_…"
            className="w-full min-w-0 px-3 py-2 bg-bambu-dark rounded-md text-white border border-bambu-dark-tertiary"
          />
          <button type="button" disabled={!token} onClick={() => setRevealToken((current) => !current)} className="mt-2 text-sm text-bambu-gray disabled:opacity-50">
            {t(revealToken ? 'streamOverlay.builder.hideToken' : 'streamOverlay.builder.showToken')}
          </button>
          <p className="text-xs text-bambu-gray mt-1">
            {t(
              'streamOverlay.builder.credentialHint',
              'Create a token above, enter an existing token, or import an overlay URL. Copy the URL before leaving: tokens are held only in memory and cannot be recovered from their stored hash.',
            )}
          </p>
        </div>
      </div>

      <fieldset className="mt-4">
        <legend className="text-sm font-medium text-white mb-2">
          {t('streamOverlay.builder.fields', 'Fields to show')}
        </legend>
        <div className="grid grid-cols-1 gap-2 @min-[24rem]/overlay:grid-cols-2 @min-[36rem]/overlay:grid-cols-3">
          {FIELDS.map((field) => (
            <label key={field.key} className="flex items-center gap-2 text-sm text-bambu-gray">
              <input
                type="checkbox"
                checked={fields.includes(field.key)}
                onChange={() => toggleField(field.key)}
                className="accent-bambu-green"
              />
              {t(field.labelKey, field.fallback)}
            </label>
          ))}
          <label className="flex items-center gap-2 text-sm text-bambu-gray">
            <input
              type="checkbox"
              checked={showCamera}
              onChange={(e) => setShowCamera(e.target.checked)}
              className="accent-bambu-green"
            />
            {t('streamOverlay.builder.fieldCamera', 'Camera feed')}
          </label>
        </div>
        <p className="text-xs text-bambu-gray mt-2">
          {t(
            'streamOverlay.builder.chamberHint',
            'Chamber temperature only appears on models with a real chamber sensor — P1 and A1 printers report a meaningless value, so it is left out there.',
          )}
        </p>
      </fieldset>

      <OverlayBrandingControls key={brandingImportRevision} value={branding} onChange={setBranding} onBusyChange={setBrandingBusy} />

      <div className="mt-4">
        {outputs.map(({ layout: orientation, url }) => (
          <fieldset key={orientation} className="min-w-0 mb-3">
            <legend className="text-sm font-medium text-white mb-1">
              {t('streamOverlay.builder.orientationUrl', { orientation: t(`streamOverlay.builder.${orientation}`) })}
            </legend>
            <p className="text-xs text-bambu-gray mb-2">
              {t('streamOverlay.builder.sourceDimensions', OVERLAY_DIMENSIONS[orientation])}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <code className="w-full px-3 py-2 bg-bambu-dark rounded-md text-bambu-green text-xs break-all font-mono">
                {displayedUrl(url)}
              </code>
              <button type="button" onClick={() => void copyUrl(url)}
                className="flex items-center gap-2 px-3 py-2 bg-bambu-green text-white rounded-md hover:bg-bambu-green/90">
                <Copy className="w-4 h-4" />
                {t('cameraTokens.created.copy', 'Copy')}
              </button>
              <a href={url} target="_blank" rel="noopener noreferrer"
                className="flex items-center gap-2 px-3 py-2 bg-bambu-dark-tertiary text-white rounded-md hover:bg-bambu-dark-tertiary/80">
                <ExternalLink className="w-4 h-4" />
                {t('streamOverlay.builder.open', 'Open')}
              </a>
            </div>
          </fieldset>
        ))}
        {token.trim() && (
          <p className="text-xs text-bambu-gray mt-2">
            {t(
              'streamOverlay.builder.tokenWarning',
              'This URL contains a token — anyone who can read it can watch the stream and see the file name. Revoke the token to cut it off.',
            )}
          </p>
        )}
      </div>

      {/* The preview opens a real camera stream, so it stays off until asked
          for. Leaving one running behind a settings tab would hold a subscriber
          on the printer's single camera connection for as long as the tab is
          open. */}
      <div className="mt-4">
        <button
          type="button"
          onClick={() => setPreview((p) => !p)}
          className="flex items-center gap-2 px-3 py-2 bg-bambu-dark-tertiary text-white rounded-md hover:bg-bambu-dark-tertiary/80 text-sm"
        >
          {preview ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          {preview
            ? t('streamOverlay.builder.hidePreview', 'Hide preview')
            : t('streamOverlay.builder.showPreview', 'Show preview')}
        </button>
        {preview && (
          <div className="mt-3 flex flex-wrap items-start gap-4">
            {outputs.map(({ layout: orientation, url }) => (
              <div key={orientation} className="min-w-0 flex-1 basis-64"
                style={orientation === 'portrait' ? { maxWidth: 360 } : undefined}>
                <p className="text-sm text-white mb-2">{t(`streamOverlay.builder.${orientation}`)}</p>
                <div className="overflow-hidden rounded-md border border-bambu-dark-tertiary">
                  <OverlayFrame layout={orientation} preview>
                    <OverlayPreview url={url} logoRevision={branding.logoRevision}
                      title={t('streamOverlay.builder.orientationPreview', { orientation: t(`streamOverlay.builder.${orientation}`) })} />
                  </OverlayFrame>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function OverlayPreview({ url, logoRevision, title }: { url: string; logoRevision: number; title: string }) {
  const [source, setSource] = useState({ url, logoRevision });

  useEffect(() => {
    // Colour and transparency controls emit continuously while dragging.
    // Wait for them to settle before opening another camera stream.
    const timeout = window.setTimeout(() => setSource({ url, logoRevision }), 300);
    return () => window.clearTimeout(timeout);
  }, [url, logoRevision]);

  return <iframe
    key={`${source.url}:${source.logoRevision}`}
    src={source.url}
    title={title}
    className="w-full h-full border-0"
  />;
}
