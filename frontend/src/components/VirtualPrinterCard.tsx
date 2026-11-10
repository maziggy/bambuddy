import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import {
  Loader2, Check, AlertTriangle, Eye, EyeOff, Info,
  ChevronDown, ChevronRight, ArrowRightLeft, Trash2, ShieldCheck, Copy, Stethoscope,
} from 'lucide-react';
import { api, multiVirtualPrinterApi } from '../api/client';
import type { LibraryFolderTree, VirtualPrinterConfig } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { VirtualPrinterDiagnosticModal } from './VirtualPrinterDiagnosticModal';
import { useToast } from '../contexts/ToastContext';
import { copyTextToClipboard } from '../utils/clipboard';

type LocalMode = 'library' | 'review' | 'queue' | 'proxy';

const MODE_LABELS: Record<string, string> = {
  library: 'library',
  review: 'review',
  queue: 'queue',
  proxy: 'proxy',
};

// Legacy wire values (`immediate` and `archive` → `library`, `print_queue` →
// `queue`). Backend migration flips existing rows but the function tolerates
// either form so a stale fetch doesn't show an unselected mode (#1429 follow-up).
function normalizeMode(value: string | undefined): LocalMode {
  if (value === 'immediate' || value === 'archive') return 'library';
  if (value === 'print_queue' || value === 'queue') return 'queue';
  if (value === 'library' || value === 'review' || value === 'proxy') return value;
  return 'library';
}

// The folders a virtual printer can store uploads in, as a flat list with
// their depth: internal ones the user may add files to (an external mount
// refuses a second file of the same name, and slicer names repeat).
function uploadFolderOptions(tree: LibraryFolderTree[] | undefined, depth = 0): { id: number; label: string }[] {
  return (tree ?? [])
    .filter((folder) => !folder.is_external)
    .flatMap((folder) => [
      ...(folder.can_write ? [{ id: folder.id, label: `${'\u00a0\u00a0'.repeat(depth)}${folder.name}` }] : []),
      ...uploadFolderOptions(folder.children, depth + 1),
    ]);
}

interface VirtualPrinterCardProps {
  printer: VirtualPrinterConfig;
  models: Record<string, string>;
  // Serial family per model code, shown for codes that share a display name (H2C: O1C / O1C2).
  modelSerialPrefixes?: Record<string, string>;
  // Collapsed by default: with several virtual printers the page stays a list.
  defaultExpanded?: boolean;
}

/** Whether the codes called ``name`` are told apart by their serial (H2C: O1C 094, O1C2 31B). */
function serialTellsApart(models: Record<string, string>, prefixes: Record<string, string>, name: string | undefined): boolean {
  if (!name) return false;
  const families = new Set<string | undefined>(Object.entries(models).filter(([, n]) => n === name).map(([code]) => prefixes[code]));
  return families.size > 1 && !families.has(undefined);
}

export function VirtualPrinterCard({ printer, models, modelSerialPrefixes = {}, defaultExpanded = false }: VirtualPrinterCardProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [expanded, setExpanded] = useState(defaultExpanded);
  const [localEnabled, setLocalEnabled] = useState(printer.enabled);
  const [localName, setLocalName] = useState(printer.name);
  const [localAccessCode, setLocalAccessCode] = useState('');
  const [localMode, setLocalMode] = useState<LocalMode>(normalizeMode(printer.mode));
  const [localTargetPrinterId, setLocalTargetPrinterId] = useState<number | null>(printer.target_printer_id);
  const [localBindIp, setLocalBindIp] = useState(printer.bind_ip || '');
  const [localRemoteInterfaceIp, setLocalRemoteInterfaceIp] = useState(printer.remote_interface_ip || '');
  const [localModel, setLocalModel] = useState(printer.model || '');
  const [localAutoDispatch, setLocalAutoDispatch] = useState(printer.auto_dispatch ?? true);
  const [localAutoDispatchFirstOnly, setLocalAutoDispatchFirstOnly] = useState(
    printer.auto_dispatch_first_only ?? false,
  );
  const [localQueueForceColorMatch, setLocalQueueForceColorMatch] = useState(printer.queue_force_color_match ?? false);
  const [localSaveAmsMapping, setLocalSaveAmsMapping] = useState(printer.save_ams_mapping ?? false);
  const [localGcodeInjection, setLocalGcodeInjection] = useState(printer.gcode_injection ?? false);
  const [localFolderId, setLocalFolderId] = useState<number | null>(printer.library_folder_id ?? null);
  const [localRemoveAfterPrint, setLocalRemoveAfterPrint] = useState(printer.queue_remove_after_print ?? false);
  const [localTailscaleDisabled, setLocalTailscaleDisabled] = useState(printer.tailscale_disabled ?? true);
  const [showAccessCode, setShowAccessCode] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showDiagnostic, setShowDiagnostic] = useState(false);
  const [fqdnCopied, setFqdnCopied] = useState(false);

  // Host-level Tailscale identity (same for every VP) — shown inline on the card when
  // the user has marked this VP as "exposed over Tailscale". Cert handling does NOT
  // depend on this toggle; the slicer trusts the bambuddy CA the user imports once.
  const { data: tailscaleStatus } = useQuery({
    queryKey: ['tailscale-status'],
    queryFn: multiVirtualPrinterApi.getTailscaleStatus,
    enabled: !localTailscaleDisabled,
    staleTime: 60_000,
  });
  const tailscaleFqdn = tailscaleStatus?.available ? tailscaleStatus.fqdn : '';
  const tailscaleIp = tailscaleStatus?.available ? tailscaleStatus.tailscale_ips?.[0] ?? '' : '';

  // Cleared on unmount: a timer left running sets state on a card that is gone.
  const fqdnCopiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (fqdnCopiedTimer.current) clearTimeout(fqdnCopiedTimer.current);
  }, []);

  const handleCopyFqdn = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const fqdn = tailscaleFqdn;
    if (!fqdn) return;
    const ok = await copyTextToClipboard(fqdn);
    if (ok) {
      setFqdnCopied(true);
      showToast(t('printers.copied'));
      if (fqdnCopiedTimer.current) clearTimeout(fqdnCopiedTimer.current);
      fqdnCopiedTimer.current = setTimeout(() => setFqdnCopied(false), 2000);
    } else {
      showToast(t('virtualPrinter.toast.copyFailed'), 'error');
    }
  };

  // Sync local state when props change (e.g., after backend auto-disable)
  useEffect(() => {
    if (!pendingAction) {
      setLocalEnabled(printer.enabled);
      setLocalMode(normalizeMode(printer.mode));
      setLocalName(printer.name);
      setLocalTargetPrinterId(printer.target_printer_id);
      setLocalBindIp(printer.bind_ip || '');
      setLocalRemoteInterfaceIp(printer.remote_interface_ip || '');
      setLocalModel(printer.model || '');
      setLocalAutoDispatch(printer.auto_dispatch ?? true);
      setLocalAutoDispatchFirstOnly(printer.auto_dispatch_first_only ?? false);
      setLocalQueueForceColorMatch(printer.queue_force_color_match ?? false);
      setLocalSaveAmsMapping(printer.save_ams_mapping ?? false);
      setLocalGcodeInjection(printer.gcode_injection ?? false);
      setLocalFolderId(printer.library_folder_id ?? null);
      setLocalRemoveAfterPrint(printer.queue_remove_after_print ?? false);
      setLocalTailscaleDisabled(printer.tailscale_disabled ?? true);
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

  const { data: libraryFolders } = useQuery({
    queryKey: ['library-folders'],
    queryFn: api.getLibraryFolders,
    enabled: localMode !== 'proxy',
  });
  const folderOptions = uploadFolderOptions(libraryFolders);

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
      setLocalMode(normalizeMode(printer.mode));
      setLocalTargetPrinterId(printer.target_printer_id);
      setLocalBindIp(printer.bind_ip || '');
      setLocalTailscaleDisabled(printer.tailscale_disabled ?? true);
      // Queue-mode behaviour toggles. Without these the switch stays visually
      // flipped after a failed save, so the card claims a setting the server
      // never accepted.
      setLocalQueueForceColorMatch(printer.queue_force_color_match ?? false);
      setLocalSaveAmsMapping(printer.save_ams_mapping ?? false);
      setLocalGcodeInjection(printer.gcode_injection ?? false);
      setLocalFolderId(printer.library_folder_id ?? null);
      setLocalRemoveAfterPrint(printer.queue_remove_after_print ?? false);
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

  const handleTargetPrinterChange = (printerId: number | null) => {
    // The new target's access code becomes this VP's access code on the
    // backend write. If the slicer was already bound with the old code,
    // it has to rebind; flag this so the user doesn't sit there confused.
    const previousCode = targetPrinter?.access_code;
    const nextCode = printerId === null ? undefined : printers?.find(p => p.id === printerId)?.access_code;
    setLocalTargetPrinterId(printerId);
    setPendingAction('targetPrinter');
    updateMutation.mutate(
      { target_printer_id: printerId },
      {
        onSuccess: () => {
          if (previousCode && nextCode && previousCode !== nextCode) {
            showToast(t('virtualPrinter.toast.targetCodeChangedRebind'), 'info');
          }
        },
      },
    );
  };

  const handleRemoteInterfaceChange = (ip: string) => {
    setLocalRemoteInterfaceIp(ip);
    setPendingAction('remoteInterface');
    updateMutation.mutate({ remote_interface_ip: ip });
  };

  const isRunning = printer.status?.running || false;
  const modeLabel = t(`virtualPrinter.mode.${MODE_LABELS[localMode] || 'library'}`);
  const targetPrinter = printers?.find(p => p.id === localTargetPrinterId);
  const targetPrinterName = targetPrinter?.name;
  // The bridge in non-proxy modes (and the transparent relay in proxy mode)
  // forwards the slicer's auth bytes to the real printer, so the VP's access
  // code is always the target's. When a target is set, the card surfaces the
  // target's code read-only — the user types it into the slicer, but can't
  // diverge it from the printer.
  const inheritsAccessCodeFromTarget = !!localTargetPrinterId;
  const inheritedAccessCode = inheritsAccessCodeFromTarget ? (targetPrinter?.access_code ?? '') : '';

  return (
    <>
      {/*
        Clip at the border as a backstop, so a value longer than anything
        anticipated above lands inside the card instead of on the page
        background (#2808). Scoped here rather than added to `Card` itself:
        half the cards in the app render dropdowns and menus that deliberately
        paint outside their bounds, and this one has none.
      */}
      <Card className="overflow-hidden">
        {/* Collapsed header - always visible, clickable to expand */}
        <div
          className="px-4 py-3 flex items-center gap-3 cursor-pointer select-none"
          onClick={() => setExpanded(!expanded)}
        >
          <button
            className="text-bambu-gray flex-shrink-0"
            aria-expanded={expanded}
            aria-label={expanded ? t('common.collapse') : t('common.expand')}
          >
            {expanded
              ? <ChevronDown className="w-4 h-4" />
              : <ChevronRight className="w-4 h-4" />
            }
          </button>
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${isRunning ? 'bg-green-400 animate-pulse' : 'bg-gray-500'}`} />
          {/*
            Metadata wraps rather than overflowing (#2808). Every item in this
            row used to be flex-shrink-0, so nothing could give and the row was
            as wide as its contents -- which the Card doesn't clip, so the last
            IP and the toggle were painted outside the card's border. The name's
            `truncate` didn't save it either: a flex item defaults to
            min-width:auto, so it couldn't shrink below its text and the
            ellipsis never engaged (`flex-shrink-0 truncate` on the target name
            was self-cancelling for the same reason).

            It needs three things at once to overflow, which is why it took a
            two-VLAN setup to surface: bind_ip and remote_interface_ip are both
            set only when Bambuddy and the printer are on different subnets, a
            printer adopted without a name is called "Printer at <ip>" (25
            characters of unbreakable text), and the cards sit in a 3-column
            grid. Wrapping keeps every value readable -- these are the addresses
            an operator came to this page to check, so truncating them away
            would trade one bug for a quieter one.
          */}
          <div className="flex-1 min-w-0 flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="text-white font-medium truncate max-w-full">{printer.name}</span>
            <span className="text-xs text-bambu-gray flex-shrink-0">{modeLabel}</span>
            {printer.model_name && (
              <span className="text-xs text-bambu-gray flex-shrink-0">{printer.model_name}</span>
            )}
            {targetPrinterName && (
              <span className="text-xs text-bambu-gray truncate max-w-full">
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
          </div>
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
              <button
                onClick={() => setShowDiagnostic(true)}
                className="p-1.5 text-bambu-gray hover:text-bambu-green transition-colors flex-shrink-0"
                title={t('vpDiagnostic.runButton')}
              >
                <Stethoscope className="w-4 h-4" />
              </button>
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className="p-1.5 text-bambu-gray hover:text-red-600 dark:hover:text-red-400 transition-colors flex-shrink-0"
                title={t('common.delete')}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>

            {/* Tailscale identity (host-level) + serial — compact info row.
                Shown only when this VP is marked Tailscale-exposed AND the daemon is up. */}
            <div className="flex items-center gap-2 -mt-2">
              {tailscaleFqdn && (
                <span className="flex items-center gap-1 text-green-700/80 dark:text-green-400/70 min-w-0">
                  <ShieldCheck className="w-3.5 h-3.5 flex-shrink-0" />
                  <span className="font-mono text-xs truncate">
                    {tailscaleIp ? `${tailscaleIp} (${tailscaleFqdn})` : tailscaleFqdn}
                  </span>
                  <button
                    onClick={handleCopyFqdn}
                    className="p-0.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors flex-shrink-0"
                    title={fqdnCopied ? t('printers.copied') : t('printers.copyToClipboard')}
                  >
                    {fqdnCopied ? (
                      <Check className="w-3.5 h-3.5 text-bambu-green" />
                    ) : (
                      <Copy className="w-3.5 h-3.5" />
                    )}
                  </button>
                </span>
              )}
              <span className="text-xs text-bambu-gray font-mono ml-auto flex-shrink-0">{printer.serial}</span>
            </div>

            {/* Mode */}
            <div>
              <div className="text-white text-sm font-medium mb-2">{t('virtualPrinter.mode.title')}</div>
              <div className="grid grid-cols-2 gap-2">
                {(['library', 'review', 'queue', 'proxy'] as const).map((mode) => (
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

            {/* File Manager folder - every mode that receives files */}
            {localMode !== 'proxy' && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="text-white text-sm font-medium mb-1">{t('virtualPrinter.uploadFolder.title')}</div>
                <div className="relative">
                  <select
                    aria-label={t('virtualPrinter.uploadFolder.title')}
                    value={localFolderId ?? ''}
                    onChange={(e) => {
                      const id = e.target.value === '' ? null : parseInt(e.target.value, 10);
                      setLocalFolderId(id);
                      setPendingAction('uploadFolder');
                      updateMutation.mutate({ library_folder_id: id });
                    }}
                    disabled={pendingAction === 'uploadFolder'}
                    className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-1.5 text-white text-sm appearance-none cursor-pointer disabled:opacity-50 pr-10"
                  >
                    <option value="">{t('virtualPrinter.uploadFolder.default')}</option>
                    {folderOptions.map((folder) => (
                      <option key={folder.id} value={folder.id}>{folder.label}</option>
                    ))}
                    {/* A folder the user can't add to still shows as what it is. */}
                    {localFolderId !== null && !folderOptions.some((f) => f.id === localFolderId) && (
                      <option value={localFolderId}>{t('virtualPrinter.uploadFolder.other')}</option>
                    )}
                  </select>
                  <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                </div>
                <p className="text-xs text-bambu-gray mt-1">{t(`virtualPrinter.uploadFolder.hint.${localMode}`)}</p>
              </div>
            )}

            {/* Remove-after-print toggle - only for queue mode */}
            {localMode === 'queue' && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-white text-sm font-medium">{t('virtualPrinter.removeAfterPrint.title')}</div>
                    <div className="text-[10px] text-bambu-gray">{t('virtualPrinter.removeAfterPrint.description')}</div>
                  </div>
                  <button
                    role="switch"
                    aria-checked={localRemoveAfterPrint}
                    aria-label={t('virtualPrinter.removeAfterPrint.title')}
                    onClick={() => {
                      const newVal = !localRemoveAfterPrint;
                      setLocalRemoveAfterPrint(newVal);
                      setPendingAction('removeAfterPrint');
                      updateMutation.mutate({ queue_remove_after_print: newVal });
                    }}
                    disabled={pendingAction === 'removeAfterPrint'}
                    className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${
                      localRemoveAfterPrint ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                    } ${pendingAction === 'removeAfterPrint' ? 'opacity-50' : ''}`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                        localRemoveAfterPrint ? 'translate-x-5' : ''
                      }`}
                    />
                  </button>
                </div>
              </div>
            )}

            {/* Auto-dispatch toggle - only for queue mode */}
            {localMode === 'queue' && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-white text-sm font-medium">{t('virtualPrinter.autoDispatch.title')}</div>
                    <div className="text-[10px] text-bambu-gray">{t('virtualPrinter.autoDispatch.description')}</div>
                  </div>
                  <button
                    onClick={() => {
                      const newVal = !localAutoDispatch;
                      setLocalAutoDispatch(newVal);
                      setPendingAction('autoDispatch');
                      updateMutation.mutate({ auto_dispatch: newVal });
                    }}
                    disabled={pendingAction === 'autoDispatch'}
                    className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${
                      localAutoDispatch ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                    } ${pendingAction === 'autoDispatch' ? 'opacity-50' : ''}`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                        localAutoDispatch ? 'translate-x-5' : ''
                      }`}
                    />
                  </button>
                </div>
              </div>
            )}

            {/* Only the first job starts by itself - queue mode with auto-dispatch on (#3299) */}
            {localMode === 'queue' && localAutoDispatch && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-white text-sm font-medium">
                      {t('virtualPrinter.autoDispatchFirstOnly.title')}
                    </div>
                    <div className="text-[10px] text-bambu-gray">
                      {t('virtualPrinter.autoDispatchFirstOnly.description')}
                    </div>
                  </div>
                  <button
                    role="switch"
                    aria-checked={localAutoDispatchFirstOnly}
                    aria-label={t('virtualPrinter.autoDispatchFirstOnly.title')}
                    onClick={() => {
                      const newVal = !localAutoDispatchFirstOnly;
                      setLocalAutoDispatchFirstOnly(newVal);
                      setPendingAction('autoDispatchFirstOnly');
                      updateMutation.mutate({ auto_dispatch_first_only: newVal });
                    }}
                    disabled={pendingAction === 'autoDispatchFirstOnly'}
                    className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${
                      localAutoDispatchFirstOnly ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                    } ${pendingAction === 'autoDispatchFirstOnly' ? 'opacity-50' : ''}`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                        localAutoDispatchFirstOnly ? 'translate-x-5' : ''
                      }`}
                    />
                  </button>
                </div>
              </div>
            )}

            {/* Force-color-match toggle - only for queue mode (#1188) */}
            {localMode === 'queue' && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-white text-sm font-medium">{t('virtualPrinter.queueForceColorMatch.title')}</div>
                    <div className="text-[10px] text-bambu-gray">{t('virtualPrinter.queueForceColorMatch.description')}</div>
                  </div>
                  <button
                    onClick={() => {
                      const newVal = !localQueueForceColorMatch;
                      setLocalQueueForceColorMatch(newVal);
                      setPendingAction('queueForceColorMatch');
                      updateMutation.mutate({ queue_force_color_match: newVal });
                    }}
                    disabled={pendingAction === 'queueForceColorMatch'}
                    className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${
                      localQueueForceColorMatch ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                    } ${pendingAction === 'queueForceColorMatch' ? 'opacity-50' : ''}`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                        localQueueForceColorMatch ? 'translate-x-5' : ''
                      }`}
                    />
                  </button>
                </div>
              </div>
            )}

            {/* Save-AMS-mapping toggle - only for queue mode */}
            {localMode === 'queue' && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-white text-sm font-medium">{t('virtualPrinter.saveAmsMapping.title')}</div>
                    <div className="text-[10px] text-bambu-gray">{t('virtualPrinter.saveAmsMapping.description')}</div>
                  </div>
                  <button
                    onClick={() => {
                      const newVal = !localSaveAmsMapping;
                      setLocalSaveAmsMapping(newVal);
                      setPendingAction('saveAmsMapping');
                      updateMutation.mutate({ save_ams_mapping: newVal });
                    }}
                    disabled={pendingAction === 'saveAmsMapping'}
                    className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${
                      localSaveAmsMapping ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                    } ${pendingAction === 'saveAmsMapping' ? 'opacity-50' : ''}`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                        localSaveAmsMapping ? 'translate-x-5' : ''
                      }`}
                    />
                  </button>
                </div>
              </div>
            )}

            {/* G-code injection toggle - only for queue mode (#1516) */}
            {localMode === 'queue' && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-white text-sm font-medium">{t('virtualPrinter.gcodeInjection.title')}</div>
                    <div className="text-[10px] text-bambu-gray">{t('virtualPrinter.gcodeInjection.description')}</div>
                  </div>
                  <button
                    onClick={() => {
                      const newVal = !localGcodeInjection;
                      setLocalGcodeInjection(newVal);
                      setPendingAction('gcodeInjection');
                      updateMutation.mutate({ gcode_injection: newVal });
                    }}
                    disabled={pendingAction === 'gcodeInjection'}
                    className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${
                      localGcodeInjection ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                    } ${pendingAction === 'gcodeInjection' ? 'opacity-50' : ''}`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                        localGcodeInjection ? 'translate-x-5' : ''
                      }`}
                    />
                  </button>
                </div>
              </div>
            )}

            {/* Tailscale toggle */}
            <div className="pt-2 border-t border-bambu-dark-tertiary">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-white text-sm font-medium">{t('virtualPrinter.tailscaleDisabled.title')}</div>
                  <div className="text-[10px] text-bambu-gray">{t('virtualPrinter.tailscaleDisabled.description')}</div>
                </div>
                <button
                  onClick={() => {
                    const newVal = !localTailscaleDisabled;
                    setLocalTailscaleDisabled(newVal);
                    setPendingAction('tailscaleDisabled');
                    updateMutation.mutate({ tailscale_disabled: newVal });
                  }}
                  disabled={pendingAction === 'tailscaleDisabled'}
                  className={`relative w-10 h-5 rounded-full transition-colors shrink-0 ${
                    !localTailscaleDisabled ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                  } ${pendingAction === 'tailscaleDisabled' ? 'opacity-50' : ''}`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                      !localTailscaleDisabled ? 'translate-x-5' : ''
                    }`}
                  />
                </button>
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
                      <option key={code} value={code}>
                        {serialTellsApart(models, modelSerialPrefixes, name)
                          ? t('virtualPrinter.model.optionSerial', { name, code, prefix: modelSerialPrefixes[code] })
                          : `${name} (${code})`}
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                </div>
                {serialTellsApart(models, modelSerialPrefixes, models[localModel]) && (
                  <p className="text-xs text-bambu-gray mt-1" data-testid="vp-model-same-name-hint">
                    {t('virtualPrinter.model.sameNameHint', { menu: t('printers.printerInformation') })}
                  </p>
                )}
              </div>
            )}

            {/* Proxy mode: hint about using target printer's access code */}
            {localMode === 'proxy' && (
              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <div className="flex items-start gap-2 p-2 rounded bg-blue-50 border border-blue-300 dark:bg-blue-500/10 dark:border-blue-500/30">
                  <Info className="w-4 h-4 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" />
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
                  {inheritsAccessCodeFromTarget ? (
                    <span className="flex items-center gap-1 text-xs text-blue-700 dark:text-blue-400">
                      <Info className="w-3 h-3" />
                      {t('virtualPrinter.accessCode.inheritedFromTarget')}
                    </span>
                  ) : printer.access_code_set ? (
                    <span className="flex items-center gap-1 text-xs text-green-700 dark:text-green-400">
                      <Check className="w-3 h-3" />
                      {t('virtualPrinter.accessCode.isSet')}
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-xs text-yellow-700 dark:text-yellow-400">
                      <AlertTriangle className="w-3 h-3" />
                      {t('virtualPrinter.accessCode.notSet')}
                    </span>
                  )}
                </div>
                {inheritsAccessCodeFromTarget ? (
                  <>
                    <div className="relative">
                      <input
                        type={showAccessCode ? 'text' : 'password'}
                        value={inheritedAccessCode}
                        readOnly
                        aria-label={t('virtualPrinter.accessCode.title')}
                        className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-1.5 text-white text-sm pr-10 font-mono opacity-90 cursor-default"
                      />
                      <button
                        onClick={() => setShowAccessCode(!showAccessCode)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
                        aria-label={showAccessCode ? t('virtualPrinter.accessCode.hide') : t('virtualPrinter.accessCode.reveal')}
                      >
                        {showAccessCode ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                      </button>
                    </div>
                    <p className="text-xs text-bambu-gray mt-1">
                      {t('virtualPrinter.accessCode.derivedFromTargetHint')}
                    </p>
                  </>
                ) : (
                  <>
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
                        <span className={localAccessCode.length === 8 ? 'text-green-700 dark:text-green-400' : 'text-yellow-700 dark:text-yellow-400'}>
                          {t('virtualPrinter.accessCode.charCount', { count: localAccessCode.length })}
                        </span>
                      </p>
                    )}
                  </>
                )}
                {/* Personal access codes (#1430): users can sign in with their own. */}
                <p className="text-xs text-bambu-gray mt-1">{t('virtualPrinter.accessCode.personalHint')}</p>
              </div>
            )}

            {/* Target Printer */}
            <div className="pt-2 border-t border-bambu-dark-tertiary">
              <div className="text-white text-sm font-medium mb-2">{t('virtualPrinter.targetPrinter.title')}</div>
              <div className="relative">
                <select
                  value={localTargetPrinterId ?? ''}
                  onChange={(e) => {
                    if (e.target.value === '') {
                      // Proxy mode needs a printer, so its empty entry is only a prompt.
                      if (localMode !== 'proxy' && localTargetPrinterId !== null) handleTargetPrinterChange(null);
                      return;
                    }
                    const id = parseInt(e.target.value, 10);
                    if (!isNaN(id)) handleTargetPrinterChange(id);
                  }}
                  disabled={pendingAction === 'targetPrinter'}
                  className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-1.5 text-white text-sm appearance-none cursor-pointer disabled:opacity-50 pr-10"
                >
                  <option value="">
                    {localMode === 'proxy'
                      ? t('virtualPrinter.targetPrinter.placeholder')
                      : localMode === 'queue'
                        ? t('virtualPrinter.targetPrinter.anyOfModel', { model: models[localModel] || localModel })
                        : t('virtualPrinter.targetPrinter.none')}
                  </option>
                  {printers?.map((p) => (
                    <option key={p.id} value={p.id}>{p.name} ({p.ip_address})</option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
              </div>
              {localMode === 'queue' && (
                <p className="text-xs text-bambu-gray mt-1">
                  {localTargetPrinterId !== null
                    ? t('virtualPrinter.targetPrinter.queueFixedHint')
                    : t('virtualPrinter.targetPrinter.queueAnyHint', { model: models[localModel] || localModel })}
                </p>
              )}
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
                  <span className="flex items-center gap-1 text-xs text-green-700 dark:text-green-400"><Check className="w-3 h-3" /></span>
                ) : (
                  <span className="flex items-center gap-1 text-xs text-bambu-gray" title={t('virtualPrinter.remoteInterface.optional')}><Info className="w-3 h-3" /></span>
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

      {showDiagnostic && (
        <VirtualPrinterDiagnosticModal
          vpId={printer.id}
          vpName={printer.name}
          onClose={() => setShowDiagnostic(false)}
        />
      )}

    </>
  );
}
