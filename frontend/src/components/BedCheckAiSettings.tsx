import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Bot, Check, X, AlertTriangle, Printer as PrinterIcon, Activity } from 'lucide-react';
import { api } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

type TestResult = { ok: boolean; message: string } | null;

// Informational only -- not a security boundary. Real enforcement of the
// LAN-vs-remote distinction lives server-side via LAN_SERVICE_URL_SETTINGS /
// assert_safe_lan_service_url; this just decides whether to show a heads-up
// that snapshots will leave the local network.
function isLikelyLanUrl(url: string): boolean {
  try {
    const host = new URL(url).hostname;
    if (host === 'localhost' || host === '::1' || host.endsWith('.local')) return true;
    const m = host.match(/^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/);
    if (!m) return false;
    const a = Number(m[1]);
    const b = Number(m[2]);
    if (a === 127) return true; // loopback
    if (a === 10) return true; // 10.0.0.0/8
    if (a === 172 && b >= 16 && b <= 31) return true; // 172.16.0.0/12
    if (a === 192 && b === 168) return true; // 192.168.0.0/16
    return false;
  } catch {
    // Not a parseable URL (empty, mid-typing) -- don't warn on garbage input.
    return true;
  }
}

export function BedCheckAiSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [backend, setBackend] = useState<'opencv' | 'ai'>('opencv');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [testResult, setTestResult] = useState<TestResult>(null);
  const [testing, setTesting] = useState(false);
  const [initialized, setInitialized] = useState(false);

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Per-printer rows: plate-check enabled toggle + backend override select.
  const printerUpdateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { plate_detection_enabled?: boolean; bedcheck_backend_override?: 'opencv' | 'ai' | null } }) =>
      api.updatePrinter(id, patch),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('bedcheckAi.printerUpdated'));
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  useEffect(() => {
    if (!settings) return;
    setBackend(settings.bedcheck_backend ?? 'opencv');
    setBaseUrl(settings.bedcheck_ai_base_url ?? '');
    setModel(settings.bedcheck_ai_model ?? '');
    setApiKey(settings.bedcheck_ai_api_key ?? '');
    setInitialized(true);
  }, [settings]);

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateSettings({
        bedcheck_backend: backend,
        bedcheck_ai_base_url: baseUrl,
        bedcheck_ai_model: model,
        bedcheck_ai_api_key: apiKey,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      showToast(t('settings.toast.settingsSaved'));
    },
  });

  // Auto-save on change (debounced) -- same pattern as FailureDetectionSettings.
  useEffect(() => {
    if (!initialized || !settings) return;
    const changed =
      (settings.bedcheck_backend ?? 'opencv') !== backend ||
      (settings.bedcheck_ai_base_url ?? '') !== baseUrl ||
      (settings.bedcheck_ai_model ?? '') !== model ||
      (settings.bedcheck_ai_api_key ?? '') !== apiKey;
    if (!changed) return;
    const id = setTimeout(() => saveMutation.mutate(), 500);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backend, baseUrl, model, apiKey, initialized]);

  const handleTest = async () => {
    setTestResult(null);
    setTesting(true);
    try {
      const res = await api.testBedcheckAiConnection(baseUrl, model, apiKey);
      if (res.ok) {
        setTestResult({
          ok: true,
          message: t('bedcheckAi.testSuccess', { ms: res.latency_ms ?? '?' }),
        });
      } else {
        setTestResult({ ok: false, message: res.error || t('bedcheckAi.testFailed') });
      }
    } catch (e: unknown) {
      setTestResult({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setTesting(false);
    }
  };

  const showPrivacyWarning = backend === 'ai' && baseUrl.trim() !== '' && !isLikelyLanUrl(baseUrl);

  return (
    // Two-column responsive layout, matching FailureDetectionSettings: config +
    // monitored printers on the left, Status on the right once the viewport is
    // wide enough (lg breakpoint); stacked vertically below it.
    <div className="flex flex-col lg:flex-row gap-4 lg:gap-6">
    <div className="space-y-3 flex-1 lg:max-w-xl">
    <Card id="card-bedcheck-ai-inner">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Bot className="w-5 h-5 text-bambu-green" />
          <h2 className="text-lg font-semibold text-white">{t('bedcheckAi.title')}</h2>
        </div>
        <p className="text-sm text-bambu-gray mt-2">{t('bedcheckAi.description')}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <label className="block text-sm text-bambu-gray mb-1">{t('bedcheckAi.backendLabel')}</label>
          <select
            value={backend}
            onChange={(e) => setBackend(e.target.value as 'opencv' | 'ai')}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm"
          >
            <option value="opencv">{t('bedcheckAi.backendOpencv')}</option>
            <option value="ai">{t('bedcheckAi.backendAi')}</option>
          </select>
          <p className="text-xs text-bambu-gray mt-1">{t('bedcheckAi.backendHint')}</p>
        </div>

        {backend === 'ai' && (
          <>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('bedcheckAi.baseUrlLabel')}</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="http://192.168.1.20:11434/v1"
                  className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm"
                />
                <Button
                  onClick={handleTest}
                  disabled={!baseUrl || !model || testing || saveMutation.isPending}
                  variant="secondary"
                >
                  {t('bedcheckAi.testButton')}
                </Button>
              </div>
              <p className="text-xs text-bambu-gray mt-1">{t('bedcheckAi.baseUrlHint')}</p>
            </div>

            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('bedcheckAi.modelLabel')}</label>
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="qwen2.5vl:7b"
                className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm"
              />
              <p className="text-xs text-bambu-gray mt-1">{t('bedcheckAi.modelHint')}</p>
            </div>

            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('bedcheckAi.apiKeyLabel')}</label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="off"
                placeholder={t('bedcheckAi.apiKeyPlaceholder')}
                className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm"
              />
              <p className="text-xs text-bambu-gray mt-1">{t('bedcheckAi.apiKeyHint')}</p>
            </div>

            {testResult && (
              <div
                className={`flex items-start gap-2 text-sm ${
                  testResult.ok ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'
                }`}
              >
                {testResult.ok ? <Check className="w-4 h-4 mt-0.5" /> : <X className="w-4 h-4 mt-0.5" />}
                <span>{testResult.message}</span>
              </div>
            )}

            {showPrivacyWarning && (
              <div className="flex items-start gap-2 p-3 bg-amber-50 dark:bg-amber-900/30 border border-amber-300 dark:border-amber-700 rounded text-sm text-amber-800 dark:text-amber-200">
                <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span>{t('bedcheckAi.privacyWarning')}</span>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>

    {/* Monitored printers — which printers run the pre-print check, and with
        which backend (per-printer override of the global selector above). */}
    <Card id="card-bedcheck-printers">
      <CardHeader>
        <div className="flex items-center gap-2">
          <PrinterIcon className="w-5 h-5 text-bambu-green" />
          <h2 className="text-lg font-semibold text-white">{t('bedcheckAi.printersTitle')}</h2>
        </div>
        <p className="text-sm text-bambu-gray mt-2">{t('bedcheckAi.printersHint')}</p>
      </CardHeader>
      <CardContent>
        {!printers || printers.length === 0 ? (
          <p className="text-sm text-bambu-gray italic">{t('bedcheckAi.noPrinters')}</p>
        ) : (
          <div className="space-y-2">
            {printers.map((p) => (
              <div key={p.id} className="flex items-center justify-between gap-3 py-1">
                <label className="flex items-center gap-2 text-sm min-w-0">
                  <input
                    type="checkbox"
                    checked={p.plate_detection_enabled}
                    disabled={printerUpdateMutation.isPending}
                    onChange={(e) =>
                      printerUpdateMutation.mutate({ id: p.id, patch: { plate_detection_enabled: e.target.checked } })
                    }
                  />
                  <span className="text-white truncate">{p.name}</span>
                </label>
                <select
                  value={p.bedcheck_backend_override ?? ''}
                  disabled={printerUpdateMutation.isPending}
                  onChange={(e) =>
                    printerUpdateMutation.mutate({
                      id: p.id,
                      patch: {
                        bedcheck_backend_override: e.target.value === '' ? null : (e.target.value as 'opencv' | 'ai'),
                      },
                    })
                  }
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white text-sm"
                >
                  <option value="">{t('bedcheckAi.useGlobal', { backend: backend === 'ai' ? t('bedcheckAi.backendAi') : t('bedcheckAi.backendOpencv') })}</option>
                  <option value="opencv">{t('bedcheckAi.backendOpencv')}</option>
                  <option value="ai">{t('bedcheckAi.backendAi')}</option>
                </select>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>

    </div>

    <div className="space-y-3 flex-1 lg:max-w-xl">
    {/* Status — the effective configuration at a glance. */}
    <Card id="card-bedcheck-status">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-bambu-green" />
          <h2 className="text-lg font-semibold text-white">{t('bedcheckAi.statusTitle')}</h2>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-bambu-gray">{t('bedcheckAi.globalBackend')}</span>
            <span className="text-white">{backend === 'ai' ? t('bedcheckAi.backendAi') : t('bedcheckAi.backendOpencv')}</span>
          </div>
          {backend === 'ai' && (
            <div className="flex justify-between gap-4">
              <span className="text-bambu-gray">{t('bedcheckAi.baseUrlLabel')}</span>
              <span className="text-white font-mono truncate">{baseUrl || '—'}{model ? ` · ${model}` : ''}</span>
            </div>
          )}
          {printers && printers.length > 0 && (
            <div className="pt-2 border-t border-bambu-dark-tertiary space-y-1">
              {printers.map((p) => (
                <div key={p.id} className="flex justify-between gap-4">
                  <span className="text-bambu-gray truncate">{p.name}</span>
                  <span className={p.plate_detection_enabled ? 'text-green-700 dark:text-green-400' : 'text-bambu-gray/60'}>
                    {p.plate_detection_enabled
                      ? (p.bedcheck_backend_override === 'ai' || (!p.bedcheck_backend_override && backend === 'ai')
                          ? t('bedcheckAi.backendAi')
                          : t('bedcheckAi.backendOpencv'))
                      : t('bedcheckAi.notMonitored')}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
    </div>
    </div>
  );
}
