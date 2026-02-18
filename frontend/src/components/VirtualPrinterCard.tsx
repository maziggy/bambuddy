import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import {
  Loader2, Check, AlertTriangle, Eye, EyeOff, Info,
  ChevronDown, ChevronRight, ArrowRightLeft, Trash2,
} from 'lucide-react';
import { api, multiVirtualPrinterApi } from '../api/client';
import type { VirtualPrinterConfig } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';

type LocalMode = 'immediate' | 'review' | 'print_queue' | 'proxy';

const MODE_LABELS: Record<string, string> = {
  immediate: 'archive',
  review: 'review',
  print_queue: 'queue',
  proxy: 'proxy',
};

interface VirtualPrinterCardProps {
  printer: VirtualPrinterConfig;
  models: Record<string, string>;
}

export function VirtualPrinterCard({ printer, models }: VirtualPrinterCardProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [expanded, setExpanded] = useState(false);
  const [localEnabled, setLocalEnabled] = useState(printer.enabled);
  const [localName, setLocalName] = useState(printer.name);
  const [localAccessCode, setLocalAccessCode] = useState('');
  const [localMode, setLocalMode] = useState<LocalMode>(
    (printer.mode === 'queue' ? 'review' : printer.mode) as LocalMode
  );
  const [localTargetPrinterId, setLocalTargetPrinterId] = useState<number | null>(printer.target_printer_id);
  const [localBindIp, setLocalBindIp] = useState(printer.bind_ip || '');
  const [localRemoteInterfaceIp, setLocalRemoteInterfaceIp] = useState(printer.remote_interface_ip || '');
  const [localModel, setLocalModel] = useState(printer.model || '');
  const [showAccessCode, setShowAccessCode] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  // Sync local state when props change (e.g., after backend auto-disable)
  useEffect(() => {
    if (!pendingAction) {
      setLocalEnabled(printer.enabled);
      setLocalMode((printer.mode === 'queue' ? 'review' : printer.mode) as LocalMode);
      setLocalName(printer.name);
      setLocalTargetPrinterId(printer.target_printer_id);
      setLocalBindIp(printer.bind_ip || '');
      setLocalRemoteInterfaceIp(printer.remote_interface_ip || '');
      setLocalModel(printer.model || '');
    }
  }, [printer, pendingAction]);

  // Fetch printers for dropdown
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Fetch network interfaces
  const { data: networkInterfaces } = useQuery({
    queryKey: ['network-interfaces'],
    queryFn: () => api.getNetworkInterfaces().then(res => res.interfaces),
  });

  const updateMutation = useMutation({
    mutationFn: (data: Parameters<typeof multiVirtualPrinterApi.update>[1]) =>
      multiVirtualPrinterApi.update(printer.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['virtual-printers'] });
      showToast(t('virtualPrinter.toast.updated'));
      setPendingAction(null);
    },
    onError: (error: Error) => {
      showToast(error.message || t('virtualPrinter.toast.failedToUpdate'), 'error');
      setLocalEnabled(printer.enabled);
      setLocalMode((printer.mode === 'queue' ? 'review' : printer.mode) as LocalMode);
      setLocalTargetPrinterId(printer.target_printer_id);
      setLocalBindIp(printer.bind_ip || '');
      setPendingAction(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => multiVirtualPrinterApi.remove(printer.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['virtual-printers'] });
      showToast(t('virtualPrinter.toast.deleted'));
      setShowDeleteConfirm(false);
    },
    onError: (error: Error) => {
      showToast(error.message || t('virtualPrinter.toast.failedToDelete'), 'error');
      setShowDeleteConfirm(false);
    },
  });

  const handleToggleEnabled = (e: React.MouseEvent) => {
    e.stopPropagation();
    const newEnabled = !localEnabled;
    if (newEnabled) {
      if (!localBindIp) {
        showToast(t('virtualPrinter.toast.bindIpRequired'), 'error');
        return;
      }
      if (localMode === 'proxy') {
        if (!localTargetPrinterId) {
          showToast(t('virtualPrinter.toast.targetPrinterRequired'), 'error');
          return;
        }
      } else {
        if (!localAccessCode && !printer.access_code_set) {
          showToast(t('virtualPrinter.toast.accessCodeRequired'), 'error');
          return;
        }
      }
    }
    setLocalEnabled(newEnabled);
    setPendingAction('toggle');
    updateMutation.mutate({ enabled: newEnabled });
  };

  const handleNameChange = () => {
    if (!localName.trim()) return;
    setPendingAction('name');
    updateMutation.mutate({ name: localName.trim() });
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
    updateMutation.mutate({ access_code: localAccessCode });
    setLocalAccessCode('');
  };

  const handleModeChange = (mode: LocalMode) => {
    setLocalMode(mode);
    setPendingAction('mode');
    updateMutation.mutate({ mode });
  };

  const handleModelChange = (model: string) => {
    setLocalModel(model);
    setPendingAction('model');
    updateMutation.mutate({ model });
  };

  const handleTargetPrinterChange = (printerId: number) => {
    setLocalTargetPrinterId(printerId);
    setPendingAction('targetPrinter');
    updateMutation.mutate({ target_printer_id: printerId });
  };

  const handleRemoteInterfaceChange = (ip: string) => {
    setLocalRemoteInterfaceIp(ip);
    setPendingAction('remoteInterface');
    updateMutation.mutate({ remote_interface_ip: ip });
  };

  const isRunning = printer.status?.running || false;
  const modeLabel = t(`virtualPrinter.mode.${MODE_LABELS[localMode] || 'archive'}`);
  const targetPrinterName = printers?.find(p => p.id === localTargetPrinterId)?.name;

  return (
    <>
      <Card>
        {/* Collapsed header - always visible, clickable to expand */}
        <div
          className="px-4 py-3 flex items-center gap-3 cursor-pointer select-none"
          onClick={() => setExpanded(!expanded)}
        >
          <button className="text-bambu-gray flex-shrink-0">
            {expanded
              ? <ChevronDown className="w-4 h-4" />
              : <ChevronRight className="w-4 h-4" />
            }
          </button>
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${isRunning ? 'bg-green-400 animate-pulse' : 'bg-gray-500'}`} />
          <span className="text-white font-medium truncate">{printer.name}</span>
          <span className="text-xs text-bambu-gray flex-shrink-0">{modeLabel}</span>
          {printer.model_name && (
            <span className="text-xs text-bambu-gray flex-shrink-0">{printer.model_name}</span>
          )}
          {targetPrinterName && (
            <span className="text-xs text-bambu-gray flex-shrink-0 truncate">
              {localMode === 'proxy' && <ArrowRightLeft className="w-3 h-3 inline mr-1" />}
              {targetPrinterName}
            </span>
          )}
          {localBindIp && (
            <span className="text-[10px] text-bambu-gray flex-shrink-0 font-mono">{localBindIp}</span>
          )}
          {localRemoteInterfaceIp && (
            <span className="text-[10px] text-bambu-gray flex-shrink-0 font-mono">{localRemoteInterfaceIp}</span>
          )}
          <div className="ml-auto flex items-center gap-2 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
            <button
              onClick={handleToggleEnabled}
              disabled={pendingAction === 'toggle'}
              className={`relative w-10 h-5 rounded-full transition-colors ${
                localEnabled ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
              } ${pendingAction === 'toggle' ? 'opacity-50' : ''}`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                  localEnabled ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>
        </div>

        {/* Expanded content */}
        {expanded && (
          <CardContent className="pt-0 space-y-4">
            <div className="border-t border-bambu-dark-tertiary" />

            {/* Name + delete */}
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={localName}
                onChange={(e) => setLocalName(e.target.value)}
                onBlur={handleNameChange}
                onKeyDown={(e) => e.key === 'Enter' && handleNameChange()}
                className="flex-1 text-sm text-white bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-1.5 focus:border-bambu-green focus:outline-none"
              />
              <span className="text-xs text-bambu-gray font-mono">{printer.serial}</span>
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className="p-1.5 text-bambu-gray hover:text-red-400 transition-colors"
                title={t('common.delete')}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>

            {/* Mode */}
            <div>
              <div className="text-white text-sm font-medium mb-2">{t('virtualPrinter.mode.title')}</div>
              <div className="grid grid-cols-2 gap-2">
                {(['immediate', 'review', 'print_queue', 'proxy'] as const).map((mode) => (
                  <button
                    key={mode}
                    onClick={() => handleModeChange(mode)}
                    disabled={pendingAction === 'mode'}
                    className={`p-2 rounded-lg border text-left transition-colors ${
                      localMode === mode
                        ? mode === 'proxy'
                          ? 'border-blue-500 bg-blue-500/10'
                          : 'border-bambu-green bg-bambu-green/10'
                        : 'border-bambu-dark-tertiary hover:border-bambu-gray'
                    }`}
                  >
                    <div className="flex items-center gap-1.5 text-white text-xs font-medium">
                      {mode === 'proxy' && <ArrowRightLeft className="w-3 h-3" />}
                      {t(`virtualPrinter.mode.${MODE_LABELS[mode]}`)}
                    </div>
                    <div className="text-[10px] text-bambu-gray">
                      {t(`virtualPrinter.mode.${MODE_LABELS[mode]}Desc`)}
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* Printer Model - for non-proxy modes */}
            {localMode !== 'proxy' && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="text-white text-sm font-medium mb-1">{t('virtualPrinter.model.title')}</div>
                <p className="text-xs text-bambu-gray mb-2">{t('virtualPrinter.model.description')}</p>
                <div className="relative">
                  <select
                    value={localModel}
                    onChange={(e) => handleModelChange(e.target.value)}
                    disabled={pendingAction === 'model'}
                    className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-1.5 text-white text-sm appearance-none cursor-pointer disabled:opacity-50 pr-10"
                  >
                    {Object.entries(models).map(([code, name]) => (
                      <option key={code} value={code}>{name} ({code})</option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                </div>
              </div>
            )}

            {/* Proxy mode: hint about using target printer's access code */}
            {localMode === 'proxy' && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="flex items-start gap-2 p-2 rounded bg-blue-500/10 border border-blue-500/30">
                  <Info className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" />
                  <p className="text-xs text-bambu-gray">
                    {t('virtualPrinter.proxy.accessCodeHint')}
                  </p>
                </div>
              </div>
            )}

            {/* Access Code - only for non-proxy modes */}
            {localMode !== 'proxy' && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="flex items-center gap-2 mb-2">
                  <div className="text-white text-sm font-medium">{t('virtualPrinter.accessCode.title')}</div>
                  {printer.access_code_set ? (
                    <span className="flex items-center gap-1 text-xs text-green-400">
                      <Check className="w-3 h-3" />
                      {t('virtualPrinter.accessCode.isSet')}
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-xs text-yellow-400">
                      <AlertTriangle className="w-3 h-3" />
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
                      placeholder={printer.access_code_set ? t('virtualPrinter.accessCode.placeholderChange') : t('virtualPrinter.accessCode.placeholder')}
                      maxLength={8}
                      className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-1.5 text-white text-sm placeholder-bambu-gray pr-10 font-mono"
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
                {localAccessCode && (
                  <p className="text-xs text-bambu-gray mt-1">
                    <span className={localAccessCode.length === 8 ? 'text-green-400' : 'text-yellow-400'}>
                      {t('virtualPrinter.accessCode.charCount', { count: localAccessCode.length })}
                    </span>
                  </p>
                )}
              </div>
            )}

            {/* Target Printer */}
            <div className="pt-2 border-t border-bambu-dark-tertiary">
              <div className="text-white text-sm font-medium mb-2">{t('virtualPrinter.targetPrinter.title')}</div>
              <div className="relative">
                <select
                  value={localTargetPrinterId ?? ''}
                  onChange={(e) => {
                    const id = parseInt(e.target.value, 10);
                    if (!isNaN(id)) handleTargetPrinterChange(id);
                  }}
                  disabled={pendingAction === 'targetPrinter'}
                  className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-1.5 text-white text-sm appearance-none cursor-pointer disabled:opacity-50 pr-10"
                >
                  <option value="">{t('virtualPrinter.targetPrinter.placeholder')}</option>
                  {printers?.map((p) => (
                    <option key={p.id} value={p.id}>{p.name} ({p.ip_address})</option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
              </div>
            </div>

            {/* Bind Interface */}
            <div className="pt-2 border-t border-bambu-dark-tertiary">
              <div className="text-white text-sm font-medium mb-1">{t('virtualPrinter.bindIp.title')}</div>
              <div className="relative">
                <select
                  value={localBindIp}
                  onChange={(e) => {
                    setLocalBindIp(e.target.value);
                    setPendingAction('bindIp');
                    updateMutation.mutate({ bind_ip: e.target.value });
                  }}
                  disabled={pendingAction === 'bindIp'}
                  className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-1.5 text-white text-sm appearance-none cursor-pointer disabled:opacity-50 pr-10"
                >
                  <option value="">{t('virtualPrinter.bindIp.placeholder')}</option>
                  {networkInterfaces?.map((iface) => (
                    <option key={iface.ip} value={iface.ip}>
                      {iface.name} ({iface.ip}){iface.is_alias ? ' [alias]' : ''} - {iface.subnet}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
              </div>
              <p className="text-xs text-bambu-gray mt-1">{t('virtualPrinter.bindIp.hint')}</p>
            </div>

            {/* Remote Interface - always visible for configuration */}
            <div className="pt-2 border-t border-bambu-dark-tertiary">
              <div className="flex items-center gap-2 mb-1">
                <div className="text-white text-sm font-medium">{t('virtualPrinter.remoteInterface.title')}</div>
                {localRemoteInterfaceIp ? (
                  <span className="flex items-center gap-1 text-xs text-green-400"><Check className="w-3 h-3" /></span>
                ) : (
                  <span className="flex items-center gap-1 text-xs text-bambu-gray"><Info className="w-3 h-3" /></span>
                )}
              </div>
              <div className="relative">
                <select
                  value={localRemoteInterfaceIp}
                  onChange={(e) => handleRemoteInterfaceChange(e.target.value)}
                  disabled={pendingAction === 'remoteInterface'}
                  className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-1.5 text-white text-sm appearance-none cursor-pointer disabled:opacity-50 pr-10"
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
            </div>
          </CardContent>
        )}
      </Card>

      {showDeleteConfirm && (
        <ConfirmModal
          title={t('virtualPrinter.deleteConfirm.title')}
          message={t('virtualPrinter.deleteConfirm.message', { name: printer.name })}
          variant="danger"
          confirmText={t('common.delete')}
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}

    </>
  );
}
