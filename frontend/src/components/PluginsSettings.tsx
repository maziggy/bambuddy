import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Code,
  ExternalLink,
  Loader2,
  Package,
  RotateCcw,
  Upload,
  X,
} from 'lucide-react';
import { pluginsApi } from '../api/client';
import type { PluginInfo, PluginUploadPreview } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Toggle } from './Toggle';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

// ---------------------------------------------------------------------------
// Plugin settings editor
// ---------------------------------------------------------------------------

function PluginSettingsEditor({ pluginKey }: { pluginKey: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const { data: settings, isLoading } = useQuery({
    queryKey: ['plugin-settings', pluginKey],
    queryFn: () => pluginsApi.getSettings(pluginKey),
  });

  const [localSettings, setLocalSettings] = useState<Record<string, unknown> | null>(null);
  const displaySettings = localSettings ?? (settings ?? {});

  const saveMutation = useMutation({
    mutationFn: (s: Record<string, unknown>) => pluginsApi.updateSettings(pluginKey, s),
    onSuccess: () => {
      showToast(t('plugins.toast.settingsSaved'), 'success');
      queryClient.invalidateQueries({ queryKey: ['plugin-settings', pluginKey] });
      setLocalSettings(null);
    },
    onError: (err: Error) => {
      showToast(err.message || t('plugins.toast.settingsFailed'), 'error');
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-bambu-gray text-sm mt-3">
        <Loader2 className="w-4 h-4 animate-spin" />
        {t('plugins.loadingSettings')}
      </div>
    );
  }

  const entries = Object.entries(displaySettings);
  if (entries.length === 0) {
    return <p className="text-bambu-gray text-sm mt-3">{t('plugins.noSettings')}</p>;
  }

  return (
    <div className="mt-3 space-y-3">
      <div className="grid gap-3">
        {entries.map(([key, value]) => (
          <div key={key} className="flex flex-col gap-1">
            <label className="text-xs font-medium text-bambu-gray uppercase tracking-wide">
              {key}
            </label>
            {typeof value === 'boolean' ? (
              <Toggle
                checked={value}
                onChange={(checked) =>
                  setLocalSettings({ ...(localSettings ?? settings ?? {}), [key]: checked })
                }
              />
            ) : (
              <input
                type={typeof value === 'number' ? 'number' : 'text'}
                value={String(value ?? '')}
                onChange={(e) =>
                  setLocalSettings({
                    ...(localSettings ?? settings ?? {}),
                    [key]: typeof value === 'number' ? Number(e.target.value) : e.target.value,
                  })
                }
                className="w-full bg-bambu-dark-tertiary border border-bambu-dark-border rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-bambu-green"
              />
            )}
          </div>
        ))}
      </div>
      {localSettings !== null && (
        <Button
          size="sm"
          onClick={() => saveMutation.mutate(localSettings)}
          disabled={saveMutation.isPending}
        >
          {saveMutation.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
          {t('plugins.saveSettings')}
        </Button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Plugin card
// ---------------------------------------------------------------------------

function PluginCard({ plugin }: { plugin: PluginInfo }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [expanded, setExpanded] = useState(false);

  const toggleMutation = useMutation({
    mutationFn: (enable: boolean) =>
      enable ? pluginsApi.enable(plugin.plugin_key) : pluginsApi.disable(plugin.plugin_key),
    onSuccess: (data, enable) => {
      showToast(enable ? t('plugins.toast.enabled') : t('plugins.toast.disabled'), 'success');
      if (data.restart_required) showToast(t('plugins.toast.restartRequired'), 'warning');
      queryClient.invalidateQueries({ queryKey: ['plugins'] });
    },
    onError: (err: Error) => {
      showToast(err.message || t('plugins.toast.toggleFailed'), 'error');
    },
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3 min-w-0">
            <Package className="w-5 h-5 text-bambu-green shrink-0 mt-0.5" />
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="text-white font-medium">{plugin.name}</h3>
                <span className="text-xs text-bambu-gray bg-bambu-dark-tertiary px-1.5 py-0.5 rounded">
                  v{plugin.version}
                </span>
                {plugin.loaded ? (
                  <span className="text-xs text-green-400 bg-green-900/30 px-1.5 py-0.5 rounded">
                    {t('plugins.loaded')}
                  </span>
                ) : plugin.enabled ? (
                  <span className="text-xs text-yellow-400 bg-yellow-900/30 px-1.5 py-0.5 rounded flex items-center gap-1">
                    <RotateCcw className="w-3 h-3" />
                    {t('plugins.restartNeeded')}
                  </span>
                ) : (
                  <span className="text-xs text-bambu-gray bg-bambu-dark-tertiary px-1.5 py-0.5 rounded">
                    {t('plugins.disabled')}
                  </span>
                )}
              </div>
              {plugin.author && (
                <p className="text-xs text-bambu-gray mt-0.5">
                  {t('plugins.by')} {plugin.author}
                </p>
              )}
              {plugin.description && (
                <p className="text-sm text-bambu-gray mt-1">{plugin.description}</p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {plugin.has_viewer && plugin.loaded && (
              <a
                href={`/api/v1/plugins/${plugin.plugin_key}/assets/index.html`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-xs text-bambu-green hover:text-white border border-bambu-green/40 hover:border-bambu-green rounded px-2 py-1 transition-colors"
                title={t('plugins.openViewer')}
              >
                <ExternalLink className="w-3 h-3" />
                {t('plugins.openViewer')}
              </a>
            )}
            <Toggle
              checked={plugin.enabled}
              onChange={(checked) => toggleMutation.mutate(checked)}
              disabled={toggleMutation.isPending}
            />
            <button
              onClick={() => setExpanded((v) => !v)}
              className="text-bambu-gray hover:text-white transition-colors"
              title={expanded ? t('plugins.hideSettings') : t('plugins.showSettings')}
            >
              {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </CardHeader>
      {expanded && (
        <CardContent>
          {plugin.loaded ? (
            <PluginSettingsEditor pluginKey={plugin.plugin_key} />
          ) : (
            <div className="flex items-center gap-2 text-yellow-400 text-sm">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              {plugin.enabled ? t('plugins.notLoadedEnabled') : t('plugins.notLoadedDisabled')}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Upload modal
// ---------------------------------------------------------------------------

type UploadStep = 'idle' | 'uploading' | 'preview' | 'installing';

function UploadModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [step, setStep] = useState<UploadStep>('idle');
  const [dragOver, setDragOver] = useState(false);
  const [preview, setPreview] = useState<PluginUploadPreview | null>(null);
  const [showCode, setShowCode] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFile = async (file: File) => {
    setError(null);
    setStep('uploading');
    try {
      const result = await pluginsApi.upload(file);
      setPreview(result);
      setStep('preview');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Upload failed');
      setStep('idle');
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  const handleInstall = async () => {
    if (!preview) return;
    setStep('installing');
    try {
      await pluginsApi.install(preview.upload_id);
      showToast(t('plugins.toast.installed'), 'success');
      queryClient.invalidateQueries({ queryKey: ['plugins'] });
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Install failed');
      showToast(t('plugins.toast.installFailed'), 'error');
      setStep('preview');
    }
  };

  const typeLabel = () => {
    if (!preview) return '';
    if (preview.plugin_type === 'bambuddy') return t('plugins.upload.bambuddyDetected');
    if (preview.plugin_type === 'octoprint') return t('plugins.upload.octoprintDetected');
    return t('plugins.upload.unknownType');
  };

  const typeColor = () => {
    if (!preview) return '';
    if (preview.plugin_type === 'bambuddy') return 'text-green-400';
    if (preview.plugin_type === 'octoprint') return 'text-yellow-400';
    return 'text-red-400';
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary border border-bambu-dark-border rounded-xl w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-bambu-dark-border shrink-0">
          <div className="flex items-center gap-2">
            <Package className="w-5 h-5 text-bambu-green" />
            <h2 className="text-lg font-semibold text-white">{t('plugins.upload.title')}</h2>
          </div>
          <button onClick={onClose} className="text-bambu-gray hover:text-white transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 p-5 space-y-4">
          {/* Error */}
          {error && (
            <div className="flex items-center gap-2 text-red-400 bg-red-900/20 border border-red-900/40 rounded-lg p-3 text-sm">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              {error}
            </div>
          )}

          {/* Drop zone — shown in idle / uploading */}
          {(step === 'idle' || step === 'uploading') && (
            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`border-2 border-dashed rounded-xl p-10 flex flex-col items-center justify-center gap-3 cursor-pointer transition-colors ${
                dragOver
                  ? 'border-bambu-green bg-bambu-green/10'
                  : 'border-bambu-dark-border hover:border-bambu-green/50'
              }`}
            >
              {step === 'uploading' ? (
                <>
                  <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
                  <p className="text-bambu-gray text-sm">{t('plugins.upload.uploading')}</p>
                </>
              ) : (
                <>
                  <Upload className="w-8 h-8 text-bambu-gray" />
                  <p className="text-bambu-gray text-sm text-center">{t('plugins.upload.dropzone')}</p>
                </>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip"
                className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
              />
            </div>
          )}

          {/* Preview */}
          {step === 'preview' && preview && (
            <div className="space-y-4">
              {/* Type badge + name */}
              <div className="flex items-start gap-3">
                <Package className="w-6 h-6 text-bambu-green shrink-0 mt-0.5" />
                <div>
                  <p className={`text-sm font-medium ${typeColor()}`}>{typeLabel()}</p>
                  <h3 className="text-white font-semibold text-lg">{preview.name}</h3>
                  <p className="text-bambu-gray text-sm">
                    v{preview.version}{preview.author ? ` · ${preview.author}` : ''}
                  </p>
                  {preview.description && (
                    <p className="text-bambu-gray text-sm mt-1">{preview.description}</p>
                  )}
                </div>
              </div>

              {/* Already installed warning */}
              {preview.already_installed && (
                <div className="flex items-center gap-2 text-yellow-400 bg-yellow-900/20 border border-yellow-900/40 rounded-lg p-3 text-sm">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  {t('plugins.upload.alreadyInstalled')}
                </div>
              )}

              {/* Unknown type — can't install */}
              {preview.plugin_type === 'unknown' && (
                <div className="flex items-center gap-2 text-red-400 bg-red-900/20 border border-red-900/40 rounded-lg p-3 text-sm">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  {t('plugins.upload.unknownHint')}
                </div>
              )}

              {/* Supported / unsupported mixins (OctoPrint only) */}
              {preview.plugin_type === 'octoprint' && (
                <div className="grid grid-cols-2 gap-3">
                  {preview.supported_mixins.length > 0 && (
                    <div className="bg-bambu-dark-tertiary rounded-lg p-3">
                      <p className="text-xs font-medium text-bambu-gray uppercase tracking-wide mb-2">
                        {t('plugins.upload.supported')}
                      </p>
                      <ul className="space-y-1">
                        {preview.supported_mixins.map((m) => (
                          <li key={m} className="flex items-center gap-1.5 text-sm text-green-400">
                            <Check className="w-3 h-3 shrink-0" />
                            {m}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {preview.unsupported_mixins.length > 0 && (
                    <div className="bg-bambu-dark-tertiary rounded-lg p-3">
                      <p className="text-xs font-medium text-bambu-gray uppercase tracking-wide mb-2">
                        {t('plugins.upload.unsupported')}
                      </p>
                      <ul className="space-y-1">
                        {preview.unsupported_mixins.map((m) => (
                          <li key={m} className="flex items-center gap-1.5 text-sm text-red-400">
                            <X className="w-3 h-3 shrink-0" />
                            {m}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {/* Conversion notes */}
              {preview.conversion_notes.length > 0 && (
                <div className="bg-bambu-dark-tertiary rounded-lg p-3">
                  <p className="text-xs font-medium text-bambu-gray uppercase tracking-wide mb-2">
                    {t('plugins.upload.notes')}
                  </p>
                  <ul className="space-y-1">
                    {preview.conversion_notes.map((note, i) => (
                      <li key={i} className="text-sm text-bambu-gray leading-relaxed">
                        {note}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Generated code toggle */}
              {preview.converted_code && (
                <div>
                  <button
                    onClick={() => setShowCode((v) => !v)}
                    className="flex items-center gap-1.5 text-sm text-bambu-gray hover:text-white transition-colors"
                  >
                    <Code className="w-4 h-4" />
                    {showCode ? t('plugins.upload.hideCode') : t('plugins.upload.showCode')}
                    {showCode ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                  </button>
                  {showCode && (
                    <pre className="mt-2 bg-black/40 border border-bambu-dark-border rounded-lg p-3 text-xs text-green-300 overflow-auto max-h-64 font-mono whitespace-pre">
                      {preview.converted_code}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}

          {step === 'installing' && (
            <div className="flex items-center justify-center py-8 gap-2 text-bambu-gray">
              <Loader2 className="w-5 h-5 animate-spin" />
              {t('plugins.upload.installing')}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 p-5 border-t border-bambu-dark-border shrink-0">
          <Button variant="outline" onClick={onClose} disabled={step === 'installing'}>
            {t('plugins.upload.cancel')}
          </Button>
          {step === 'preview' && preview && preview.plugin_type !== 'unknown' && (
            <Button onClick={handleInstall} disabled={step === 'installing'}>
              {t('plugins.upload.install')}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function PluginsSettings() {
  const { t } = useTranslation();
  const [showUpload, setShowUpload] = useState(false);

  const { data: plugins, isLoading, isError, refetch } = useQuery({
    queryKey: ['plugins'],
    queryFn: pluginsApi.list,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16 text-bambu-gray">
        <Loader2 className="w-6 h-6 animate-spin mr-2" />
        {t('plugins.loading')}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-3 text-bambu-gray">
        <AlertTriangle className="w-8 h-8 text-red-400" />
        <p>{t('plugins.loadError')}</p>
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          {t('plugins.retry')}
        </Button>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-white">{t('plugins.title')}</h2>
            <p className="text-bambu-gray text-sm mt-1">{t('plugins.description')}</p>
          </div>
          <Button size="sm" onClick={() => setShowUpload(true)}>
            <Upload className="w-4 h-4" />
            {t('plugins.installPlugin')}
          </Button>
        </div>

        {!plugins || plugins.length === 0 ? (
          <Card>
            <CardContent>
              <div className="flex flex-col items-center justify-center py-8 gap-2 text-bambu-gray">
                <Package className="w-10 h-10 opacity-30" />
                <p className="font-medium">{t('plugins.none')}</p>
                <p className="text-sm text-center">{t('plugins.noneHint')}</p>
              </div>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {plugins.map((plugin) => (
              <PluginCard key={plugin.plugin_key} plugin={plugin} />
            ))}
          </div>
        )}
      </div>

      {showUpload && <UploadModal onClose={() => setShowUpload(false)} />}
    </>
  );
}
