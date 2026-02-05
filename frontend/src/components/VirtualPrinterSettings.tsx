import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Check, AlertTriangle, Printer, Eye, EyeOff, Info, ChevronDown, ExternalLink, ArrowRightLeft } from 'lucide-react';
import { api, virtualPrinterApi } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

type LocalMode = 'immediate' | 'review' | 'print_queue' | 'proxy';

export function VirtualPrinterSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [localEnabled, setLocalEnabled] = useState(false);
  const [localAccessCode, setLocalAccessCode] = useState('');
  const [localMode, setLocalMode] = useState<LocalMode>('immediate');
  const [localModel, setLocalModel] = useState('3DPrinter-X1-Carbon');
  const [localTargetPrinterId, setLocalTargetPrinterId] = useState<number | null>(null);
  const [localRemoteInterfaceIp, setLocalRemoteInterfaceIp] = useState('');
  const [showAccessCode, setShowAccessCode] = useState(false);
  const [pendingAction, setPendingAction] = useState<'toggle' | 'accessCode' | 'mode' | 'model' | 'targetPrinter' | 'remoteInterface' | null>(null);

  // Fetch current settings
  const { data: settings, isLoading } = useQuery({
    queryKey: ['virtual-printer-settings'],
    queryFn: virtualPrinterApi.getSettings,
    refetchInterval: 10000, // Refresh every 10 seconds for status updates
  });

  // Fetch available models
  const { data: modelsData } = useQuery({
    queryKey: ['virtual-printer-models'],
    queryFn: virtualPrinterApi.getModels,
  });

  // Fetch printers for proxy mode dropdown
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Fetch network interfaces for SSDP proxy (only in proxy mode)
  const { data: networkInterfaces } = useQuery({
    queryKey: ['network-interfaces'],
    queryFn: () => api.getNetworkInterfaces().then(res => res.interfaces),
    enabled: localMode === 'proxy',
  });

  // Initialize local state from settings
  useEffect(() => {
    if (settings) {
      setLocalEnabled(settings.enabled);
      // Map legacy 'queue' mode to 'review'
      let mode: LocalMode = settings.mode === 'queue' ? 'review' : settings.mode as LocalMode;
      if (mode !== 'immediate' && mode !== 'review' && mode !== 'print_queue' && mode !== 'proxy') {
        mode = 'immediate'; // fallback
      }
      setLocalMode(mode);
      setLocalModel(settings.model);
      setLocalTargetPrinterId(settings.target_printer_id);
      setLocalRemoteInterfaceIp(settings.remote_interface_ip || '');
    }
  }, [settings]);

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: (data: { enabled?: boolean; access_code?: string; mode?: LocalMode; model?: string; target_printer_id?: number; remote_interface_ip?: string }) =>
      virtualPrinterApi.updateSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['virtual-printer-settings'] });
      showToast(t('virtualPrinter.toast.updated'));
      setPendingAction(null);
    },
    onError: (error: Error) => {
      showToast(error.message || t('virtualPrinter.toast.failedToUpdate'), 'error');
      // Revert local state on error
      if (settings) {
        setLocalEnabled(settings.enabled);
        // Map legacy 'queue' mode to 'review'
        const mode = settings.mode === 'queue' ? 'review' : settings.mode;
        setLocalMode(['immediate', 'review', 'print_queue', 'proxy'].includes(mode) ? mode as LocalMode : 'immediate');
        setLocalModel(settings.model);
        setLocalTargetPrinterId(settings.target_printer_id);
      }
      setPendingAction(null);
    },
  });

  const handleToggleEnabled = () => {
    const newEnabled = !localEnabled;

    // Validation depends on mode
    if (newEnabled) {
      if (localMode === 'proxy') {
        // Proxy mode requires target printer
        if (!localTargetPrinterId) {
          showToast(t('virtualPrinter.toast.targetPrinterRequired'), 'error');
          return;
        }
      } else {
        // Other modes require access code
        if (!localAccessCode && !settings?.access_code_set) {
          showToast(t('virtualPrinter.toast.accessCodeRequired'), 'error');
          return;
        }
      }
    }

    setLocalEnabled(newEnabled);
    setPendingAction('toggle');
    updateMutation.mutate({
      enabled: newEnabled,
      access_code: localMode !== 'proxy' ? (localAccessCode || undefined) : undefined,
      mode: localMode,
      target_printer_id: localMode === 'proxy' ? (localTargetPrinterId ?? undefined) : undefined,
    });
  };

  const handleAccessCodeChange = () => {
    if (!localAccessCode) {
      showToast(t('virtualPrinter.toast.accessCodeEmpty'), 'error');
      return;
    }

    if (localAccessCode.length !== 8) {
      showToast(t('virtualPrinter.toast.accessCodeLength'), 'error');
      return;
    }

    setPendingAction('accessCode');
    updateMutation.mutate({
      access_code: localAccessCode,
    });
    setLocalAccessCode(''); // Clear after saving
  };

  const handleModeChange = (mode: LocalMode) => {
    setLocalMode(mode);
    setPendingAction('mode');
    updateMutation.mutate({ mode });
  };

  const handleTargetPrinterChange = (printerId: number) => {
    setLocalTargetPrinterId(printerId);
    setPendingAction('targetPrinter');
    updateMutation.mutate({
      target_printer_id: printerId,
    });
  };

  const handleModelChange = (model: string) => {
    setLocalModel(model);
    setPendingAction('model');
    updateMutation.mutate({ model });
  };

  const handleRemoteInterfaceChange = (ip: string) => {
    setLocalRemoteInterfaceIp(ip);
    setPendingAction('remoteInterface');
    updateMutation.mutate({ remote_interface_ip: ip });
  };

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-8 flex justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
        </CardContent>
      </Card>
    );
  }

  const status = settings?.status;
  const isRunning = status?.running || false;

  return (
    <div className="flex flex-col lg:flex-row gap-6 lg:gap-8">
      {/* Left Column - Settings */}
      <div className="space-y-6 lg:w-[480px] lg:flex-shrink-0">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Printer className="w-5 h-5 text-bambu-green" />
              <h2 className="text-lg font-semibold text-white">{t('virtualPrinter.title')}</h2>
            </div>
            {status && (
              <div className={`flex items-center gap-2 text-sm ${isRunning ? 'text-green-400' : 'text-bambu-gray'}`}>
                <span className={`w-2 h-2 rounded-full ${isRunning ? 'bg-green-400 animate-pulse' : 'bg-gray-500'}`} />
                {isRunning ? t('virtualPrinter.running') : t('virtualPrinter.stopped')}
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-bambu-gray">
            {localMode === 'proxy'
              ? t('virtualPrinter.description.proxy')
              : t('virtualPrinter.description.default')}
          </p>

          {/* Enable/Disable Toggle */}
          <div className="flex items-center justify-between py-3 border-t border-bambu-dark-tertiary">
            <div>
              <div className="text-white font-medium">{t('virtualPrinter.enable.title')}</div>
              <div className="text-sm text-bambu-gray">
                {isRunning ? (
                  localMode === 'proxy'
                    ? t('virtualPrinter.enable.proxyingTo', { name: printers?.find(p => p.id === localTargetPrinterId)?.name || 'printer' })
                    : t('virtualPrinter.enable.visibleInSlicer')
                ) : t('virtualPrinter.enable.notActive')}
              </div>
            </div>
            <button
              onClick={handleToggleEnabled}
              disabled={pendingAction === 'toggle'}
              className={`relative w-12 h-6 rounded-full transition-colors ${
                localEnabled ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
              } ${pendingAction === 'toggle' ? 'opacity-50' : ''}`}
            >
              <span
                className={`absolute top-1 left-1 w-4 h-4 bg-white rounded-full transition-transform ${
                  localEnabled ? 'translate-x-6' : ''
                }`}
              />
            </button>
          </div>

          {/* Printer Model - only for non-proxy modes */}
          {localMode !== 'proxy' && (
          <div className="py-3 border-t border-bambu-dark-tertiary">
            <div className="text-white font-medium mb-2">{t('virtualPrinter.model.title')}</div>
            <div className="text-sm text-bambu-gray mb-3">
              {t('virtualPrinter.model.description')}
            </div>
            <div className="relative">
              <select
                value={localModel}
                onChange={(e) => handleModelChange(e.target.value)}
                disabled={pendingAction === 'model'}
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white appearance-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed pr-10"
              >
                {modelsData?.models && Object.entries(modelsData.models)
                  .sort(([, a], [, b]) => (a as string).localeCompare(b as string))
                  .map(([code, name]) => (
                  <option key={code} value={code}>
                    {name}
                  </option>
                ))}
              </select>
              <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
            </div>
            {localEnabled && isRunning && (
              <p className="text-xs text-bambu-gray mt-2">
                <Info className="w-3 h-3 inline mr-1" />
                {t('virtualPrinter.model.restartWarning')}
              </p>
            )}
          </div>
          )}

          {/* Access Code - only for non-proxy modes */}
          {localMode !== 'proxy' && (
            <div className="py-3 border-t border-bambu-dark-tertiary">
              <div className="text-white font-medium mb-2">{t('virtualPrinter.accessCode.title')}</div>
              <div className="text-sm text-bambu-gray mb-3">
                {settings?.access_code_set ? (
                  <span className="flex items-center gap-1 text-green-400">
                    <Check className="w-4 h-4" />
                    {t('virtualPrinter.accessCode.isSet')}
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-yellow-400">
                    <AlertTriangle className="w-4 h-4" />
                    {t('virtualPrinter.accessCode.notSet')}
                  </span>
                )}
              </div>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <input
                    type={showAccessCode ? 'text' : 'password'}
                    value={localAccessCode}
                    onChange={(e) => setLocalAccessCode(e.target.value)}
                    placeholder={settings?.access_code_set ? t('virtualPrinter.accessCode.placeholderChange') : t('virtualPrinter.accessCode.placeholder')}
                    maxLength={8}
                    className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white placeholder-bambu-gray pr-10 font-mono"
                  />
                  <button
                    onClick={() => setShowAccessCode(!showAccessCode)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
                  >
                    {showAccessCode ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
                <Button
                  onClick={handleAccessCodeChange}
                  disabled={!localAccessCode || pendingAction === 'accessCode'}
                  variant="primary"
                >
                  {pendingAction === 'accessCode' ? <Loader2 className="w-4 h-4 animate-spin" /> : t('common.save')}
                </Button>
              </div>
              <p className="text-xs text-bambu-gray mt-2">
                {t('virtualPrinter.accessCode.hint')}
                {localAccessCode && (
                  <span className={localAccessCode.length === 8 ? 'text-green-400' : 'text-yellow-400'}>
                    {' '}{t('virtualPrinter.accessCode.charCount', { count: localAccessCode.length })}
                  </span>
                )}
              </p>
            </div>
          )}

          {/* Target Printer - only for proxy mode */}
          {localMode === 'proxy' && (
            <div className="py-3 border-t border-bambu-dark-tertiary">
              <div className="text-white font-medium mb-2">{t('virtualPrinter.targetPrinter.title')}</div>
              <div className="text-sm text-bambu-gray mb-3">
                {localTargetPrinterId ? (
                  <span className="flex items-center gap-1 text-green-400">
                    <Check className="w-4 h-4" />
                    {t('virtualPrinter.targetPrinter.configured')}
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-yellow-400">
                    <AlertTriangle className="w-4 h-4" />
                    {t('virtualPrinter.targetPrinter.notConfigured')}
                  </span>
                )}
              </div>
              <div className="relative">
                <select
                  value={localTargetPrinterId ?? ''}
                  onChange={(e) => {
                    const id = parseInt(e.target.value, 10);
                    if (!isNaN(id)) {
                      handleTargetPrinterChange(id);
                    }
                  }}
                  disabled={pendingAction === 'targetPrinter'}
                  className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white appearance-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed pr-10"
                >
                  <option value="">{t('virtualPrinter.targetPrinter.placeholder')}</option>
                  {printers?.map((printer) => (
                    <option key={printer.id} value={printer.id}>
                      {printer.name} ({printer.ip_address})
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
              </div>
              <p className="text-xs text-bambu-gray mt-2">
                {t('virtualPrinter.targetPrinter.hint')}
              </p>
              {!printers?.length && (
                <p className="text-xs text-yellow-400 mt-2">
                  <AlertTriangle className="w-3 h-3 inline mr-1" />
                  {t('virtualPrinter.targetPrinter.noPrinters')}
                </p>
              )}
            </div>
          )}

          {/* Remote Interface - only for proxy mode (SSDP proxy) */}
          {localMode === 'proxy' && (
            <div className="py-3 border-t border-bambu-dark-tertiary">
              <div className="text-white font-medium mb-2">{t('virtualPrinter.remoteInterface.title')}</div>
              <div className="text-sm text-bambu-gray mb-3">
                {localRemoteInterfaceIp ? (
                  <span className="flex items-center gap-1 text-green-400">
                    <Check className="w-4 h-4" />
                    {t('virtualPrinter.remoteInterface.configured')}
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-bambu-gray">
                    <Info className="w-4 h-4" />
                    {t('virtualPrinter.remoteInterface.optional')}
                  </span>
                )}
              </div>
              <div className="relative">
                <select
                  value={localRemoteInterfaceIp}
                  onChange={(e) => handleRemoteInterfaceChange(e.target.value)}
                  disabled={pendingAction === 'remoteInterface'}
                  className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white appearance-none cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed pr-10"
                >
                  <option value="">{t('virtualPrinter.remoteInterface.placeholder')}</option>
                  {networkInterfaces?.map((iface) => (
                    <option key={iface.ip} value={iface.ip}>
                      {iface.name} ({iface.ip}) - {iface.subnet}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
              </div>
              <p className="text-xs text-bambu-gray mt-2">
                {t('virtualPrinter.remoteInterface.hint')}
              </p>
            </div>
          )}

          {/* Mode */}
          <div className="py-3 border-t border-bambu-dark-tertiary">
            <div className="text-white font-medium mb-2">{t('virtualPrinter.mode.title')}</div>
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={() => handleModeChange('immediate')}
                disabled={pendingAction === 'mode'}
                className={`p-3 rounded-lg border text-left transition-colors ${
                  localMode === 'immediate'
                    ? 'border-bambu-green bg-bambu-green/10'
                    : 'border-bambu-dark-tertiary hover:border-bambu-gray'
                }`}
              >
                <div className="text-white font-medium">{t('virtualPrinter.mode.archive')}</div>
                <div className="text-xs text-bambu-gray">{t('virtualPrinter.mode.archiveDesc')}</div>
              </button>
              <button
                onClick={() => handleModeChange('review')}
                disabled={pendingAction === 'mode'}
                className={`p-3 rounded-lg border text-left transition-colors ${
                  localMode === 'review'
                    ? 'border-bambu-green bg-bambu-green/10'
                    : 'border-bambu-dark-tertiary hover:border-bambu-gray'
                }`}
              >
                <div className="text-white font-medium">{t('virtualPrinter.mode.review')}</div>
                <div className="text-xs text-bambu-gray">{t('virtualPrinter.mode.reviewDesc')}</div>
              </button>
              <button
                onClick={() => handleModeChange('print_queue')}
                disabled={pendingAction === 'mode'}
                className={`p-3 rounded-lg border text-left transition-colors ${
                  localMode === 'print_queue'
                    ? 'border-bambu-green bg-bambu-green/10'
                    : 'border-bambu-dark-tertiary hover:border-bambu-gray'
                }`}
              >
                <div className="text-white font-medium">{t('virtualPrinter.mode.queue')}</div>
                <div className="text-xs text-bambu-gray">{t('virtualPrinter.mode.queueDesc')}</div>
              </button>
              <button
                onClick={() => handleModeChange('proxy')}
                disabled={pendingAction === 'mode'}
                className={`p-3 rounded-lg border text-left transition-colors ${
                  localMode === 'proxy'
                    ? 'border-blue-500 bg-blue-500/10'
                    : 'border-bambu-dark-tertiary hover:border-bambu-gray'
                }`}
              >
                <div className="flex items-center gap-1.5 text-white font-medium">
                  <ArrowRightLeft className="w-4 h-4" />
                  {t('virtualPrinter.mode.proxy')}
                </div>
                <div className="text-xs text-bambu-gray">{t('virtualPrinter.mode.proxyDesc')}</div>
              </button>
            </div>
          </div>
        </CardContent>
      </Card>
      </div>

      {/* Right Column - Info & Status */}
      <div className="space-y-6 lg:w-[480px] lg:flex-shrink-0">
        {/* Setup Required Warning */}
        <Card className="border-l-4 border-l-yellow-500">
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-yellow-500 flex-shrink-0 mt-0.5" />
              <div className="text-sm">
                <p className="text-white font-medium mb-2">
                  {t('virtualPrinter.setupRequired.title')}
                </p>
                <p className="text-bambu-gray mb-3">
                  {t('virtualPrinter.setupRequired.description')}
                </p>
                <a
                  href="https://wiki.bambuddy.cool/features/virtual-printer/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-4 py-2 bg-yellow-500/20 border border-yellow-500/50 rounded-md text-yellow-400 hover:bg-yellow-500/30 transition-colors"
                >
                  <ExternalLink className="w-4 h-4" />
                  {t('virtualPrinter.setupRequired.readGuide')}
                </a>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* How it works */}
        <Card>
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <Info className="w-5 h-5 text-blue-400 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-bambu-gray">
                <p className="mb-2">
                  <strong className="text-white">{localMode === 'proxy' ? t('virtualPrinter.howItWorks.titleProxy') : t('virtualPrinter.howItWorks.title')}:</strong>
                </p>
                {localMode === 'proxy' ? (
                  <ol className="list-decimal list-inside space-y-1">
                    <li>{t('virtualPrinter.howItWorks.proxyStep1')}</li>
                    <li>{t('virtualPrinter.howItWorks.proxyStep2')}</li>
                    <li>{t('virtualPrinter.howItWorks.proxyStep3')}</li>
                    <li>{t('virtualPrinter.howItWorks.proxyStep4')}</li>
                    <li>{t('virtualPrinter.howItWorks.proxyStep5')}</li>
                  </ol>
                ) : (
                  <ol className="list-decimal list-inside space-y-1">
                    <li>{t('virtualPrinter.howItWorks.step1')}</li>
                    <li>{t('virtualPrinter.howItWorks.step2')}</li>
                    <li>{t('virtualPrinter.howItWorks.step3')}</li>
                    <li>{t('virtualPrinter.howItWorks.step4')}</li>
                    <li>{t('virtualPrinter.howItWorks.step5')}</li>
                    <li>{t('virtualPrinter.howItWorks.step6')}</li>
                  </ol>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Status Details (when running) */}
        {status && isRunning && (
          <Card>
            <CardHeader>
              <h3 className="text-md font-semibold text-white">{t('virtualPrinter.status.title')}</h3>
            </CardHeader>
            <CardContent>
              {status.mode === 'proxy' && status.proxy ? (
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.targetPrinter')}</div>
                    <div className="text-white">
                      {printers?.find(p => p.id === localTargetPrinterId)?.name || status.proxy.target_host}
                    </div>
                    <div className="text-xs text-bambu-gray font-mono">{status.proxy.target_host}</div>
                  </div>
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.mode')}</div>
                    <div className="text-white flex items-center gap-1.5">
                      <ArrowRightLeft className="w-4 h-4" />
                      {t('virtualPrinter.mode.proxy')}
                    </div>
                  </div>
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.ftpPort')}</div>
                    <div className="text-white font-mono">{status.proxy.ftp_port}</div>
                  </div>
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.mqttPort')}</div>
                    <div className="text-white font-mono">{status.proxy.mqtt_port}</div>
                  </div>
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.ftpConnections')}</div>
                    <div className="text-white">{status.proxy.ftp_connections ?? 0}</div>
                  </div>
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.mqttConnections')}</div>
                    <div className="text-white">{status.proxy.mqtt_connections ?? 0}</div>
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.printerName')}</div>
                    <div className="text-white">{status.name}</div>
                  </div>
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.model')}</div>
                    <div className="text-white">{status.model_name || status.model}</div>
                  </div>
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.serialNumber')}</div>
                    <div className="text-white font-mono">{status.serial}</div>
                  </div>
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.mode')}</div>
                    <div className="text-white capitalize">{status.mode}</div>
                  </div>
                  <div>
                    <div className="text-bambu-gray">{t('virtualPrinter.status.pendingFiles')}</div>
                    <div className="text-white">{status.pending_files}</div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
