import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Bot, Check, X, AlertTriangle, Printer as PrinterIcon, Activity } from 'lucide-react';
import { api, type BedcheckAiHealthEntry } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { formatRelativeTime } from '../utils/date';

type TestResult = { ok: boolean; message: string; requestMode?: string } | null;

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

/**
 * One line describing a printer's last AI bed-check outcome, for the Status
 * card. Returns both the text and a color class so callers don't have to
 * duplicate the outcome switch.
 */
function healthLine(
  entry: BedcheckAiHealthEntry | undefined,
  t: (key: string, options?: Record<string, unknown>) => string,
): { text: string; className: string } {
  if (!entry) {
    return { text: t('bedcheckAi.health.none'), className: 'text-bambu-gray/60' };
  }
  const time = formatRelativeTime(entry.at, 'system', t);
  switch (entry.outcome) {
    case 'ok':
      return { text: t('bedcheckAi.health.ok', { time }), className: 'text-green-700 dark:text-green-400' };
    case 'unavailable':
      return {
        text: t('bedcheckAi.health.unavailable', {
          time,
          reason: entry.reason || t('bedcheckAi.health.noReason'),
        }),
        className: 'text-amber-700 dark:text-amber-400',
      };
    case 'degraded':
      return { text: t('bedcheckAi.health.degraded', { time }), className: 'text-bambu-gray' };
    default:
      return { text: t('bedcheckAi.health.none'), className: 'text-bambu-gray/60' };
  }
}

export function BedCheckAiSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();

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

  // Last-outcome health snapshot for the Status card. Fetched once on mount
  // (and again after a successful Test connection below) — deliberately not
  // polled.
  const { data: health, refetch: refetchHealth } = useQuery({
    queryKey: ['bedcheckAiHealth'],
    queryFn: api.getBedcheckAiHealth,
    refetchInterval: false,
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
    onError: (error: Error) => showToast(error.message, 'error'),
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
          // Absent on failure paths (see BedcheckAiTestConnection) -- only
          // ever set here on the success branch.
          requestMode: res.request_mode,
        });
        // A successful probe is the one non-mount moment worth refreshing the
        // Status card's health badges for — the backend may have just healed.
        refetchHealth();
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

  // Theme-aware input/select styling, matching EmailSettings/LDAPSettings/
  // OIDCProviderSettings — bg-bambu-dark-secondary/border-bambu-dark-tertiary
  // resolve through CSS variables (see index.css) so they render correctly in
  // both themes, unlike the literal bg-gray-800/border-gray-700 this card
  // used to hardcode (dark-only, unreadable against a light card).
  const fieldClasses =
    'px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray text-sm focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors';
  const inputClasses = `w-full ${fieldClasses}`;
  const compactSelectClasses =
    'bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-2 py-1 text-white text-sm focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors';

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
            className={inputClasses}
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
                  className={`flex-1 ${fieldClasses}`}
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
                className={inputClasses}
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
                className={inputClasses}
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
                <div>
                  <div>{testResult.message}</div>
                  {testResult.ok && testResult.requestMode && (
                    <div className="text-xs text-bambu-gray mt-0.5">
                      {t('bedcheckAi.testRequestMode', { mode: testResult.requestMode })}
                      {testResult.requestMode === 'json_object' &&
                        ` ${t('printers.plateDetection.decision.degradedNote')}`}
                    </div>
                  )}
                </div>
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
                    disabled={printerUpdateMutation.isPending || !hasPermission('printers:update')}
                    onChange={(e) =>
                      printerUpdateMutation.mutate({ id: p.id, patch: { plate_detection_enabled: e.target.checked } })
                    }
                  />
                  <span className="text-white truncate">{p.name}</span>
                </label>
                <select
                  value={p.bedcheck_backend_override ?? ''}
                  disabled={printerUpdateMutation.isPending || !hasPermission('printers:update')}
                  onChange={(e) =>
                    printerUpdateMutation.mutate({
                      id: p.id,
                      patch: {
                        bedcheck_backend_override: e.target.value === '' ? null : (e.target.value as 'opencv' | 'ai'),
                      },
                    })
                  }
                  className={compactSelectClasses}
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
            <div className="pt-2 border-t border-bambu-dark-tertiary space-y-1.5">
              {printers.map((p) => {
                // The effective backend for this printer: its own override if
                // set, otherwise the global one.
                const usesAi =
                  p.bedcheck_backend_override === 'ai' || (!p.bedcheck_backend_override && backend === 'ai');
                // Health is an AI-backend concept only. An OpenCV-monitored
                // printer has no AI outcome to report, so rendering the line
                // for it would show a permanent "No checks yet" that never
                // resolves — reads as a fault where there is none.
                const line = p.plate_detection_enabled && usesAi
                  ? healthLine(health?.printers?.[String(p.id)], t)
                  : null;
                return (
                  <div key={p.id} className="space-y-0.5">
                    <div className="flex justify-between gap-4">
                      <span className="text-bambu-gray truncate">{p.name}</span>
                      <span className={p.plate_detection_enabled ? 'text-green-700 dark:text-green-400' : 'text-bambu-gray/60'}>
                        {p.plate_detection_enabled
                          ? (usesAi ? t('bedcheckAi.backendAi') : t('bedcheckAi.backendOpencv'))
                          : t('bedcheckAi.notMonitored')}
                      </span>
                    </div>
                    {line && <div className={`text-xs ${line.className}`}>{line.text}</div>}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
    </div>
    </div>
  );
}
