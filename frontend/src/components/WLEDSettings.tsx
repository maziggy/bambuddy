import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Circle, Lightbulb, Loader2, Play, RefreshCw, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../api/client';
import type { WLEDConfig, WLEDPreset, WLEDPresets } from '../api/client';
import { Button } from './Button';
import { Card, CardContent, CardHeader } from './Card';
import { useToast } from '../contexts/ToastContext';
import { registerSettingsSearch } from '../lib/settingsSearch';

registerSettingsSearch({
  labelKey: 'wled.title',
  tab: 'wled',
  keywords: 'wled led light preset printer status',
  anchor: 'card-wled',
});

const emptyPresets: WLEDPresets = {
  idle: null,
  prepare: null,
  printing: null,
  paused: null,
  finished: null,
  error: null,
  queue_waiting: null,
  filament_problem: null,
  hms_error: null,
  offline: null,
};

const emptyConfig = (): WLEDConfig => ({
  enabled: false,
  base_url: null,
  presets: { ...emptyPresets },
  finished_timeout_seconds: 0,
});

const mappingKeys: (keyof WLEDPresets)[] = [
  'idle',
  'prepare',
  'printing',
  'paused',
  'finished',
  'error',
  'queue_waiting',
  'filament_problem',
  'hms_error',
  'offline',
];

type RequestKind = 'save' | 'presets' | 'connection' | 'presetTest';
type RequestTicket = { printerId: number; url: string; context: number; sequence: number };

export function WLEDSettings() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const { data: printers = [], isLoading } = useQuery({ queryKey: ['printers'], queryFn: api.getPrinters });
  const [printerId, setPrinterId] = useState<number | null>(null);
  const selectedPrinterId = useRef(printerId);
  selectedPrinterId.current = printerId;
  const contextGeneration = useRef(0);
  const currentUrl = useRef('');
  const requestSequence = useRef<Record<RequestKind, number>>({ save: 0, presets: 0, connection: 0, presetTest: 0 });
  const nextRequest = useCallback((kind: RequestKind, url: string): RequestTicket => ({
    printerId: selectedPrinterId.current!, url, context: contextGeneration.current,
    sequence: ++requestSequence.current[kind],
  }), []);
  const isCurrentRequest = useCallback((kind: RequestKind, request: RequestTicket) =>
    request.printerId === selectedPrinterId.current
    && request.url === currentUrl.current
    && request.context === contextGeneration.current
    && request.sequence === requestSequence.current[kind], []);
  useEffect(() => () => { contextGeneration.current += 1; }, []);
  const initializedPrinterId = useRef<number | null>(null);
  const [draftsByPrinterId, setDraftsByPrinterId] = useState<Record<number, WLEDConfig>>({});
  const printer = useMemo(() => printers.find((item) => item.id === printerId), [printers, printerId]);
  const config = printerId === null ? emptyConfig() : draftsByPrinterId[printerId] ?? printer?.wled_config ?? emptyConfig();
  const currentConfig = useRef(config);
  currentConfig.current = config;
  if (printer && draftsByPrinterId[printer.id] === undefined) currentUrl.current = config.base_url?.trim() ?? '';
  const setConfig = (change: (current: WLEDConfig) => WLEDConfig) => {
    if (printerId === null) return;
    setDraftsByPrinterId((drafts) => ({
      ...drafts,
      [printerId]: change(drafts[printerId] ?? config),
    }));
  };
  const [presets, setPresets] = useState<WLEDPreset[]>([]);
  const [presetState, setPresetState] = useState<'idle' | 'online' | 'offline'>('idle');
  const [connection, setConnection] = useState<{ state: 'idle' | 'online' | 'offline'; name?: string | null; version?: string | null }>({ state: 'idle' });

  useEffect(() => {
    if (printerId === null ? !printers.length : printers.some((item) => item.id === printerId)) return;
    contextGeneration.current += 1;
    selectedPrinterId.current = printers[0]?.id ?? null;
    setPrinterId(selectedPrinterId.current);
  }, [printerId, printers]);

  useEffect(() => {
    if (!printer || initializedPrinterId.current === printer.id) return;
    initializedPrinterId.current = printer.id;
    currentUrl.current = config.base_url?.trim() ?? '';
    setPresets([]);
    setPresetState('idle');
    setConnection({ state: 'idle' });
  }, [printer, config.base_url]);

  const saveMutation = useMutation({
    mutationFn: (request: RequestTicket & { config: WLEDConfig }) =>
      api.updatePrinter(request.printerId, { wled_config: request.config }),
    onSuccess: (updated, request) => {
      const isCurrent = isCurrentRequest('save', request);
      const draftUnchanged = currentConfig.current === request.config;
      if (request.sequence === requestSequence.current.save) {
        queryClient.setQueryData<typeof printers>(['printers'], (old = []) =>
          old.map((item) => (item.id === request.printerId ? updated : item)),
        );
        setDraftsByPrinterId((drafts) => {
          if (drafts[request.printerId] !== request.config) return drafts;
          const next = { ...drafts };
          delete next[request.printerId];
          return next;
        });
      }
      if (isCurrent && draftUnchanged) showToast(t('wled.saved'), 'success');
    },
    onError: (error, request) => {
      if (!isCurrentRequest('save', request) || currentConfig.current !== request.config) return;
      showToast(error instanceof ApiError && error.status === 422 ? error.message : t('wled.saveError'), 'error');
    },
  });

  const presetsMutation = useMutation({
    mutationFn: (request: RequestTicket) => api.getWledPresets(request.printerId, request.url),
    onSuccess: (loaded, request) => {
      if (!isCurrentRequest('presets', request)) return;
      setPresets(loaded);
      setPresetState('online');
    },
    onError: (error, request) => {
      if (!isCurrentRequest('presets', request)) return;
      if (error instanceof ApiError && error.status === 422) {
        showToast(error.message, 'error');
        return;
      }
      setPresetState('offline');
      showToast(t('wled.offlineHint'), 'error');
    },
  });

  const loadPresets = presetsMutation.mutate;

  useEffect(() => {
    const savedConfig = printer?.wled_config;
    if (savedConfig?.enabled && savedConfig.base_url && savedConfig.base_url === currentUrl.current) {
      loadPresets(nextRequest('presets', savedConfig.base_url));
    }
  }, [printer, loadPresets, nextRequest]);

  const connectionMutation = useMutation({
    mutationFn: (request: RequestTicket) => api.testWledConnection(request.printerId, request.url),
    onSuccess: (info, request) => {
      if (isCurrentRequest('connection', request)) setConnection({ state: 'online', ...info });
    },
    onError: (error, request) => {
      if (!isCurrentRequest('connection', request)) return;
      if (error instanceof ApiError && error.status === 422) showToast(error.message, 'error');
      else setConnection({ state: 'offline' });
    },
  });

  const presetTestMutation = useMutation({
    mutationFn: (request: RequestTicket & { presetId: number }) =>
      api.testWledPreset(request.printerId, request.url, request.presetId),
    onSuccess: (_result, request) => {
      if (isCurrentRequest('presetTest', request)) showToast(t('wled.presetTested'), 'success');
    },
    onError: (error, request) => {
      if (!isCurrentRequest('presetTest', request)) return;
      showToast(error instanceof ApiError && error.status === 422 ? error.message : t('wled.presetTestFailed'), 'error');
    },
  });

  const setPreset = (key: keyof WLEDPresets, value: string) => {
    setConfig((current) => ({
      ...current,
      presets: { ...current.presets, [key]: value ? Number(value) : null },
    }));
  };

  if (isLoading) return <p className="text-bambu-gray">{t('common.loading')}</p>;

  const configuredCount = mappingKeys.filter((key) => config.presets[key] !== null).length;

  return (
    <div id="card-wled" className="max-w-7xl space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <Lightbulb className="w-5 h-5 text-yellow-400" />
          {t('wled.title')}
        </h2>
        <p className="mt-2 text-sm text-bambu-gray">{t('wled.description')}</p>
      </div>

      {!printers.length ? (
        <Card>
          <CardContent className="space-y-4">
            <label className="flex items-center gap-3 text-white opacity-60">
              <input type="checkbox" checked={false} disabled className="h-5 w-5 accent-bambu-green" />
              {t('wled.enabled')}
            </label>
            <p className="text-sm text-bambu-gray">{t('wled.noPrinters')}</p>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-5 items-start">
            <Card>
              <CardHeader>
                <h3 className="font-semibold text-white">{t('wled.connectionSection')}</h3>
              </CardHeader>
              <CardContent className="space-y-4">
                <label className="block">
                  <span className="block text-sm text-bambu-gray mb-1">{t('wled.printer')}</span>
                  <select
                    aria-label={t('wled.printer')}
                    value={printerId ?? ''}
                    onChange={(event) => {
                      contextGeneration.current += 1;
                      selectedPrinterId.current = Number(event.target.value);
                      setPrinterId(selectedPrinterId.current);
                    }}
                    className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-white"
                  >
                    {printers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </select>
                </label>

                <label className="flex items-center gap-3 text-white">
                  <input
                    type="checkbox"
                    checked={config.enabled}
                    onChange={(event) => setConfig((current) => ({ ...current, enabled: event.target.checked }))}
                    className="h-5 w-5 accent-bambu-green"
                  />
                  {t('wled.enabled')}
                </label>

                <fieldset
                  disabled={!config.enabled}
                  className={`space-y-3 transition-opacity ${config.enabled ? '' : 'opacity-60'}`}
                >
                  <label className="block">
                    <span className="block text-sm text-bambu-gray mb-1">{t('wled.baseUrl')}</span>
                    <input
                      type="url"
                      value={config.base_url ?? ''}
                      onChange={(event) => {
                        contextGeneration.current += 1;
                        currentUrl.current = event.target.value.trim();
                        setConfig((current) => ({ ...current, base_url: event.target.value }));
                        setConnection({ state: 'idle' });
                      }}
                      placeholder="http://wled.local"
                      className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-white"
                    />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      onClick={() => connectionMutation.mutate(nextRequest('connection', config.base_url?.trim() ?? ''))}
                      disabled={!config.base_url?.trim() || connectionMutation.isPending}
                    >
                      {connectionMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                      {t('wled.testConnection')}
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => presetsMutation.mutate(nextRequest('presets', config.base_url?.trim() ?? ''))}
                      disabled={!config.base_url?.trim() || presetsMutation.isPending}
                    >
                      {presetsMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                      {t('wled.loadPresets')}
                    </Button>
                  </div>

                  <div aria-live="polite" className="flex items-center gap-2 text-sm">
                    {connection.state === 'idle' && <><Circle className="w-3 h-3 text-bambu-gray" /><span className="text-bambu-gray">{t('wled.notTested')}</span></>}
                    {connection.state === 'online' && <><CheckCircle2 className="w-4 h-4 text-green-400" /><span className="text-green-400">{t('wled.connected', { name: connection.name ? ` (${connection.name})` : '', version: connection.version ? ` · v${connection.version}` : '' })}</span></>}
                    {connection.state === 'offline' && <><XCircle className="w-4 h-4 text-red-400" /><span className="text-red-400">{t('wled.notReachable')}</span></>}
                  </div>

                  <div aria-live="polite" className="text-sm">
                    {presetState === 'online' && <span className="text-green-400">{t('wled.online', { count: presets.length })}</span>}
                    {presetState === 'offline' && <span className="text-red-400">{t('wled.offlineHint')}</span>}
                  </div>
                </fieldset>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                  <h3 className="font-semibold text-white">{t('wled.mappingSection')}</h3>
                  <p className="text-xs text-bambu-gray">
                    {t('wled.mappingSummary', { configured: configuredCount, disabled: mappingKeys.length - configuredCount })}
                  </p>
                </div>
              </CardHeader>
              <CardContent>
                <p className="mb-4 text-xs text-bambu-gray">{t('wled.presetSegmentHint')}</p>
                <fieldset
                  disabled={!config.enabled}
                  className={`space-y-5 transition-opacity ${config.enabled ? '' : 'opacity-60'}`}
                >
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {mappingKeys.map((key) => {
                      const selected = config.presets[key];
                      const missing = selected !== null && !presets.some((preset) => preset.id === selected);
                      return (
                        <div key={key}>
                          <span className="block text-sm text-bambu-gray mb-1">{t(`wled.status.${key}`)}</span>
                          <div className="flex gap-2">
                            <select
                              aria-label={t(`wled.status.${key}`)}
                              value={selected ?? ''}
                              onChange={(event) => setPreset(key, event.target.value)}
                              className="min-w-0 flex-1 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-white"
                            >
                              <option value="">{t('wled.noPreset')}</option>
                              {missing && <option value={selected!}>{t('wled.savedUnavailable', { id: selected })}</option>}
                              {presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name} ({preset.id})</option>)}
                            </select>
                            <Button
                              type="button"
                              size="sm"
                              variant="secondary"
                              aria-label={`${t('wled.testPreset')} ${t(`wled.status.${key}`)}`}
                              onClick={() => selected !== null && presetTestMutation.mutate({
                                ...nextRequest('presetTest', config.base_url?.trim() ?? ''), presetId: selected,
                              })}
                              disabled={selected === null || presetTestMutation.isPending}
                            >
                              <Play className="w-3 h-3" />
                              {t('wled.testPreset')}
                            </Button>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <label className="block max-w-xs">
                    <span className="block text-sm text-bambu-gray mb-1">{t('wled.finishedTimeout')}</span>
                    <input
                      type="number"
                      aria-label={t('wled.finishedTimeout')}
                      min="0"
                      max="86400"
                      placeholder="0"
                      value={config.finished_timeout_seconds ?? 0}
                      onChange={(event) => setConfig((current) => ({
                        ...current,
                        finished_timeout_seconds: Number(event.target.value),
                      }))}
                      className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-white"
                    />
                    <span className="block text-xs text-bambu-gray mt-1">{t('wled.finishedTimeoutHint')}</span>
                  </label>
                </fieldset>
              </CardContent>
            </Card>
          </div>

          <div className="flex justify-end">
            <Button
              onClick={() => saveMutation.mutate({
                ...nextRequest('save', config.base_url?.trim() ?? ''), config,
              })}
              disabled={saveMutation.isPending || (config.enabled && !config.base_url?.trim())}
            >
              {saveMutation.isPending ? t('common.saving') : t('common.save')}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
