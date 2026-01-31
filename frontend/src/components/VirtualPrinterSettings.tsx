import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Check, AlertTriangle, Printer, Eye, EyeOff, Info, ChevronDown, ExternalLink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { virtualPrinterApi } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

export function VirtualPrinterSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [localEnabled, setLocalEnabled] = useState(false);
  const [localAccessCode, setLocalAccessCode] = useState('');
  const [localMode, setLocalMode] = useState<'immediate' | 'review' | 'print_queue'>('immediate');
  const [localModel, setLocalModel] = useState('3DPrinter-X1-Carbon');
  const [showAccessCode, setShowAccessCode] = useState(false);
  const [pendingAction, setPendingAction] = useState<'toggle' | 'accessCode' | 'mode' | 'model' | null>(null);

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

  // Initialize local state from settings
  useEffect(() => {
    if (settings) {
      setLocalEnabled(settings.enabled);
      // Map legacy 'queue' mode to 'review'
      let mode: 'immediate' | 'review' | 'print_queue' = settings.mode === 'queue' ? 'review' : settings.mode;
      if (mode !== 'immediate' && mode !== 'review' && mode !== 'print_queue') {
        mode = 'immediate'; // fallback
      }
      setLocalMode(mode);
      setLocalModel(settings.model);
    }
  }, [settings]);

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: (data: { enabled?: boolean; access_code?: string; mode?: 'immediate' | 'review' | 'print_queue'; model?: string }) =>
      virtualPrinterApi.updateSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['virtual-printer-settings'] });
      showToast(t('settings.virtualPrinterUpdated'));
      setPendingAction(null);
    },
    onError: (error: Error) => {
      showToast(error.message || t('settings.saveFailed'), 'error');
      // Revert local state on error
      if (settings) {
        setLocalEnabled(settings.enabled);
        // Map legacy 'queue' mode to 'review'
        const mode = settings.mode === 'queue' ? 'review' : settings.mode;
        setLocalMode(mode === 'print_queue' || mode === 'review' ? mode : 'immediate');
        setLocalModel(settings.model);
      }
      setPendingAction(null);
    },
  });

  const handleToggleEnabled = () => {
    const newEnabled = !localEnabled;

    // If enabling, must have access code
    if (newEnabled && !localAccessCode && !settings?.access_code_set) {
      showToast(t('settings.vp.accessCodeRequired'), 'error');
      return;
    }

    setLocalEnabled(newEnabled);
    setPendingAction('toggle');
    updateMutation.mutate({
      enabled: newEnabled,
      access_code: localAccessCode || undefined,
      mode: localMode,
    });
  };

  const handleAccessCodeChange = () => {
    if (!localAccessCode) {
      showToast(t('settings.vp.accessCodeEmpty'), 'error');
      return;
    }

    if (localAccessCode.length !== 8) {
      showToast(t('settings.vp.accessCodeLength'), 'error');
      return;
    }

    setPendingAction('accessCode');
    updateMutation.mutate({
      access_code: localAccessCode,
    });
    setLocalAccessCode(''); // Clear after saving
  };

  const handleModeChange = (mode: 'immediate' | 'review' | 'print_queue') => {
    setLocalMode(mode);
    setPendingAction('mode');
    updateMutation.mutate({ mode });
  };

  const handleModelChange = (model: string) => {
    setLocalModel(model);
    setPendingAction('model');
    updateMutation.mutate({ model });
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
              <h2 className="text-lg font-semibold text-white">{t('settings.vp.title')}</h2>
            </div>
            {status && (
              <div className={`flex items-center gap-2 text-sm ${isRunning ? 'text-green-400' : 'text-bambu-gray'}`}>
                <span className={`w-2 h-2 rounded-full ${isRunning ? 'bg-green-400 animate-pulse' : 'bg-gray-500'}`} />
                {isRunning ? t('settings.vp.running') : t('settings.vp.stopped')}
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-bambu-gray">
            {t('settings.vp.description')}
          </p>

          {/* Enable/Disable Toggle */}
          <div className="flex items-center justify-between py-3 border-t border-bambu-dark-tertiary">
            <div>
              <div className="text-white font-medium">{t('settings.vp.enable')}</div>
              <div className="text-sm text-bambu-gray">
                {isRunning ? t('settings.vp.visibleInSlicer') : t('settings.vp.notVisible')}
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

          {/* Printer Model */}
          <div className="py-3 border-t border-bambu-dark-tertiary">
            <div className="text-white font-medium mb-2">{t('settings.vp.printerModel')}</div>
            <div className="text-sm text-bambu-gray mb-3">
              {t('settings.vp.printerModelDescription')}
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
                {t('settings.vp.modelRestartWarning')}
              </p>
            )}
          </div>

          {/* Access Code */}
          <div className="py-3 border-t border-bambu-dark-tertiary">
            <div className="text-white font-medium mb-2">{t('settings.vp.accessCode')}</div>
            <div className="text-sm text-bambu-gray mb-3">
              {settings?.access_code_set ? (
                <span className="flex items-center gap-1 text-green-400">
                  <Check className="w-4 h-4" />
                  {t('settings.vp.accessCodeSet')}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-yellow-400">
                  <AlertTriangle className="w-4 h-4" />
                  {t('settings.vp.noAccessCode')}
                </span>
              )}
            </div>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <input
                  type={showAccessCode ? 'text' : 'password'}
                  value={localAccessCode}
                  onChange={(e) => setLocalAccessCode(e.target.value)}
                  placeholder={settings?.access_code_set ? t('settings.enterNewCodeToChange') : t('settings.enter8CharCode')}
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
              {t('settings.vp.accessCodeHint')}
              {localAccessCode && (
                <span className={localAccessCode.length === 8 ? 'text-green-400' : 'text-yellow-400'}>
                  {' '}({localAccessCode.length}/8)
                </span>
              )}
            </p>
          </div>

          {/* Mode */}
          <div className="py-3 border-t border-bambu-dark-tertiary">
            <div className="text-white font-medium mb-2">{t('settings.vp.mode')}</div>
            <div className="grid grid-cols-3 gap-3">
              <button
                onClick={() => handleModeChange('immediate')}
                disabled={pendingAction === 'mode'}
                className={`p-3 rounded-lg border text-left transition-colors ${
                  localMode === 'immediate'
                    ? 'border-bambu-green bg-bambu-green/10'
                    : 'border-bambu-dark-tertiary hover:border-bambu-gray'
                }`}
              >
                <div className="text-white font-medium">{t('settings.vp.modeArchive')}</div>
                <div className="text-xs text-bambu-gray">{t('settings.vp.modeArchiveDesc')}</div>
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
                <div className="text-white font-medium">{t('settings.vp.modeReview')}</div>
                <div className="text-xs text-bambu-gray">{t('settings.vp.modeReviewDesc')}</div>
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
                <div className="text-white font-medium">{t('settings.vp.modeQueue')}</div>
                <div className="text-xs text-bambu-gray">{t('settings.vp.modeQueueDesc')}</div>
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
                  {t('settings.vp.setupRequired')}
                </p>
                <p className="text-bambu-gray mb-3">
                  {t('settings.vp.setupDescription')}
                </p>
                <a
                  href="https://wiki.bambuddy.cool/features/virtual-printer/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-4 py-2 bg-yellow-500/20 border border-yellow-500/50 rounded-md text-yellow-400 hover:bg-yellow-500/30 transition-colors"
                >
                  <ExternalLink className="w-4 h-4" />
                  {t('settings.vp.readSetupGuide')}
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
                  <strong className="text-white">{t('settings.vp.howItWorks')}</strong>
                </p>
                <ol className="list-decimal list-inside space-y-1">
                  <li>{t('settings.vp.step1')}</li>
                  <li>{t('settings.vp.step2')}</li>
                  <li>{t('settings.vp.step3')}</li>
                  <li>{t('settings.vp.step4')}</li>
                  <li>{t('settings.vp.step5')}</li>
                  <li>{t('settings.vp.step6')}</li>
                </ol>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Status Details (when running) */}
        {status && isRunning && (
          <Card>
            <CardHeader>
              <h3 className="text-md font-semibold text-white">{t('settings.vp.statusDetails')}</h3>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <div className="text-bambu-gray">{t('settings.vp.printerName')}</div>
                  <div className="text-white">{status.name}</div>
                </div>
                <div>
                  <div className="text-bambu-gray">{t('settings.vp.model')}</div>
                  <div className="text-white">{status.model_name || status.model}</div>
                </div>
                <div>
                  <div className="text-bambu-gray">{t('settings.vp.serialNumber')}</div>
                  <div className="text-white font-mono">{status.serial}</div>
                </div>
                <div>
                  <div className="text-bambu-gray">{t('settings.vp.mode')}</div>
                  <div className="text-white capitalize">{status.mode}</div>
                </div>
                <div>
                  <div className="text-bambu-gray">{t('settings.vp.pendingFiles')}</div>
                  <div className="text-white">{status.pending_files}</div>
                </div>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
