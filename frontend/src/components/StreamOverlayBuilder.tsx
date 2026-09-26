/**
 * Streaming-overlay URL builder (#1422).
 *
 * The overlay at /overlay/{printerId} has been configurable by query string
 * since #2613, but only for people who found the parameters in the wiki. The
 * issue asked for the field set to be selectable "through the web UI"; this is
 * that surface. It composes a URL, it does not persist anything — the URL *is*
 * the configuration, which keeps a scene in OBS reproducible by copy-paste and
 * means two displays can show different fields off one token.
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Copy, ExternalLink, Eye, EyeOff } from 'lucide-react';
import { api, type Printer } from '../api/client';
import { useToast } from '../contexts/ToastContext';

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

export function StreamOverlayBuilder() {
  const { t } = useTranslation();
  const { showToast } = useToast();

  const [printers, setPrinters] = useState<Printer[]>([]);
  const [printerId, setPrinterId] = useState<number | null>(null);
  const [fields, setFields] = useState<string[]>(DEFAULT_FIELDS);
  const [size, setSize] = useState<OverlaySize>('medium');
  const [fps, setFps] = useState(DEFAULT_FPS);
  const [showCamera, setShowCamera] = useState(true);
  const [token, setToken] = useState('');
  const [preview, setPreview] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await api.getPrinters();
        if (cancelled) return;
        setPrinters(list);
        if (list.length > 0) setPrinterId(list[0].id);
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
  }, []);

  const url = useMemo(() => {
    const id = printerId ?? 1;
    const params = new URLSearchParams();
    // Emit ?show= in the canonical field order rather than click order, so the
    // same selection always produces the same URL.
    const selected = FIELDS.filter((f) => fields.includes(f.key)).map((f) => f.key);
    params.set('show', selected.join(','));
    if (size !== 'medium') params.set('size', size);
    if (fps !== DEFAULT_FPS) params.set('fps', String(fps));
    if (!showCamera) params.set('camera', 'false');
    if (token.trim()) params.set('token', token.trim());
    return `${window.location.origin}/overlay/${id}?${params.toString()}`;
  }, [printerId, fields, size, fps, showCamera, token]);

  const toggleField = (key: string) => {
    setFields((prev) => (prev.includes(key) ? prev.filter((f) => f !== key) : [...prev, key]));
  };

  const copyUrl = async () => {
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
          document.execCommand('copy');
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
    <div>
      <p className="text-sm text-bambu-gray mb-4">
        {t(
          'streamOverlay.builder.description',
          'Build the URL for a streaming overlay — a full-screen camera view with live print data drawn over it, for OBS, a wall display, or any browser source. Pick the fields you want and copy the URL.',
        )}
      </p>

      <div className="grid gap-4 md:grid-cols-2">
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
          <label htmlFor="overlay-builder-fps" className="block text-sm font-medium text-white mb-1">
            {t('streamOverlay.builder.fps', 'Frame rate')}
          </label>
          <input
            id="overlay-builder-fps"
            type="number"
            min={1}
            max={30}
            value={fps}
            onChange={(e) => setFps(Math.min(Math.max(Number(e.target.value) || 1, 1), 30))}
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
          <label htmlFor="overlay-builder-token" className="block text-sm font-medium text-white mb-1">
            {t('streamOverlay.builder.token', 'Streaming Overlay token (optional)')}
          </label>
          <input
            id="overlay-builder-token"
            type="text"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="bblt_…"
            className="w-full px-3 py-2 bg-bambu-dark rounded-md text-white border border-bambu-dark-tertiary focus:border-bambu-green focus:outline-none font-mono text-xs"
          />
          <p className="text-xs text-bambu-gray mt-1">
            {t(
              'streamOverlay.builder.tokenHint',
              'Only needed when login is enabled: OBS has no session of its own. Create one above with the Streaming Overlay scope.',
            )}
          </p>
        </div>
      </div>

      <fieldset className="mt-4">
        <legend className="text-sm font-medium text-white mb-2">
          {t('streamOverlay.builder.fields', 'Fields to show')}
        </legend>
        <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-3">
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

      <div className="mt-4">
        <p className="text-sm font-medium text-white mb-1">
          {t('streamOverlay.builder.urlTitle', 'Overlay URL')}
        </p>
        <div className="flex items-center gap-2">
          <code className="flex-1 px-3 py-2 bg-bambu-dark rounded-md text-bambu-green text-xs break-all font-mono select-all">
            {url}
          </code>
          <button
            type="button"
            onClick={() => void copyUrl()}
            className="flex items-center gap-2 px-3 py-2 bg-bambu-green text-white rounded-md hover:bg-bambu-green/90"
          >
            <Copy className="w-4 h-4" />
            {t('cameraTokens.created.copy', 'Copy')}
          </button>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-3 py-2 bg-bambu-dark-tertiary text-white rounded-md hover:bg-bambu-dark-tertiary/80"
          >
            <ExternalLink className="w-4 h-4" />
            {t('streamOverlay.builder.open', 'Open')}
          </a>
        </div>
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
          <iframe
            key={url}
            src={url}
            title={t('streamOverlay.builder.previewTitle', 'Overlay preview')}
            className="mt-3 w-full aspect-video rounded-md border border-bambu-dark-tertiary bg-black"
          />
        )}
      </div>
    </div>
  );
}
