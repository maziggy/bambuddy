import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../contexts/AuthContext';
import {
  X,
  Square,
  Pause,
  Play,
  ChevronDown,
  BellOff,
  Eraser,
} from 'lucide-react';
import { Button } from './Button';
import { filterKnownHMSErrors } from './HMSErrorModal';
import type { Printer, HMSError } from '../api/client';

export type BulkAction = 'stop' | 'pause' | 'resume' | 'clearPlate' | 'clearHMS';
export type PrinterState = 'printing' | 'paused' | 'finished' | 'idle' | 'error' | 'offline';

interface PrinterStatus {
  connected: boolean;
  state: string | null;
  hms_errors?: HMSError[];
  awaiting_plate_clear?: boolean;
}

interface BulkPrinterToolbarProps {
  selectedIds: Set<number>;
  printers: Printer[];
  onClose: () => void;
  onSelectAll: () => void;
  onSelectByLocation: (location: string) => void;
  onSelectByState: (state: PrinterState) => void;
  onAction: (action: BulkAction) => void;
  actionPending: boolean;
}

const STATE_OPTIONS: { key: PrinterState; dot: string }[] = [
  { key: 'printing', dot: 'bg-bambu-green' },
  { key: 'paused', dot: 'bg-status-warning' },
  { key: 'finished', dot: 'bg-blue-400' },
  { key: 'idle', dot: 'bg-bambu-green' },
  { key: 'error', dot: 'bg-status-error' },
  { key: 'offline', dot: 'bg-gray-400' },
];

export function BulkPrinterToolbar({
  selectedIds,
  printers,
  onClose,
  onSelectAll,
  onSelectByLocation,
  onSelectByState,
  onAction,
  actionPending,
}: BulkPrinterToolbarProps) {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const [showLocationDropdown, setShowLocationDropdown] = useState(false);
  const [showStateDropdown, setShowStateDropdown] = useState(false);

  // Read cached statuses for selected printers
  const selectedStatuses = Array.from(selectedIds).map(id => ({
    id,
    status: queryClient.getQueryData<PrinterStatus>(['printerStatus', id]),
  }));

  // Smart enablement: check if any selected printer is in the right state
  const anyRunning = selectedStatuses.some(
    ({ status }) => status?.connected && status.state === 'RUNNING',
  );
  const anyPaused = selectedStatuses.some(
    ({ status }) => status?.connected && status.state === 'PAUSE',
  );
  const anyStoppable = anyRunning || anyPaused;
  const anyNeedsClearPlate = selectedStatuses.some(
    ({ status }) => !!(status?.connected && status.awaiting_plate_clear),
  );
  const anyWithHMS = selectedStatuses.some(({ status }) => {
    if (!status?.connected || !status.hms_errors) return false;
    return filterKnownHMSErrors(status.hms_errors).length > 0;
  });

  const canControl = hasPermission('printers:control');
  const canClearPlate = hasPermission('printers:clear_plate');

  // Unique locations from all printers (not just selected)
  const locations = [...new Set(printers.map(p => p.location).filter((l): l is string => !!l))].sort();

  // Count printers per state for the state dropdown
  const stateCounts: Record<PrinterState, number> = { printing: 0, paused: 0, finished: 0, idle: 0, error: 0, offline: 0 };
  printers.forEach(p => {
    const status = queryClient.getQueryData<PrinterStatus>(['printerStatus', p.id]);
    if (!status || !status.connected) { stateCounts.offline++; return; }
    if (status.hms_errors && filterKnownHMSErrors(status.hms_errors).length > 0) stateCounts.error++;
    switch (status.state) {
      case 'RUNNING': stateCounts.printing++; break;
      case 'PAUSE': stateCounts.paused++; break;
      case 'FINISH': stateCounts.finished++; break;
      case 'FAILED': stateCounts.error++; break;
      default: stateCounts.idle++; break;
    }
  });

  const stateLabels: Record<PrinterState, string> = {
    printing: t('printers.status.printing'),
    paused: t('printers.status.paused', 'Paused'),
    finished: t('printers.status.finished', 'Finished'),
    idle: t('printers.status.idle'),
    error: t('printers.status.problem'),
    offline: t('printers.status.offline'),
  };

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl px-4 py-3 flex items-center gap-3 flex-wrap">
      {/* Close */}
      <Button variant="secondary" size="sm" onClick={onClose}>
        <X className="w-4 h-4" />
      </Button>

      <div className="w-px h-6 bg-bambu-dark-tertiary" />

      {/* Selection count */}
      <span className="text-white font-medium text-sm">
        {t('printers.bulk.selected', { count: selectedIds.size })}
      </span>

      <div className="w-px h-6 bg-bambu-dark-tertiary" />

      {/* Select All */}
      <Button variant="secondary" size="sm" onClick={onSelectAll}>
        {t('printers.bulk.selectAll')}
      </Button>

      {/* Select by State */}
      <div className="relative">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => { setShowStateDropdown(!showStateDropdown); setShowLocationDropdown(false); }}
        >
          {t('printers.bulk.selectByState')}
          <ChevronDown className={`w-3 h-3 transition-transform ${showStateDropdown ? 'rotate-180' : ''}`} />
        </Button>
        {showStateDropdown && (
          <>
            <div
              className="fixed inset-0 z-10"
              onClick={() => setShowStateDropdown(false)}
            />
            <div className="absolute bottom-full mb-2 left-0 w-48 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg z-20 py-1">
              {STATE_OPTIONS.filter(({ key }) => stateCounts[key] > 0).map(({ key, dot }) => (
                <button
                  key={key}
                  onClick={() => {
                    onSelectByState(key);
                    setShowStateDropdown(false);
                  }}
                  className="w-full text-left px-3 py-2 text-sm text-bambu-gray-light hover:bg-bambu-dark-tertiary hover:text-white transition-colors flex items-center gap-2"
                >
                  <div className={`w-2 h-2 rounded-full ${dot}`} />
                  {stateLabels[key]}
                  <span className="ml-auto text-bambu-gray text-xs">{stateCounts[key]}</span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Select by Location */}
      {locations.length > 0 && (
        <div className="relative">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => { setShowLocationDropdown(!showLocationDropdown); setShowStateDropdown(false); }}
          >
            {t('printers.bulk.selectByLocation')}
            <ChevronDown className={`w-3 h-3 transition-transform ${showLocationDropdown ? 'rotate-180' : ''}`} />
          </Button>
          {showLocationDropdown && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setShowLocationDropdown(false)}
              />
              <div className="absolute bottom-full mb-2 left-0 w-48 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg z-20 py-1">
                {locations.map(location => (
                  <button
                    key={location}
                    onClick={() => {
                      onSelectByLocation(location);
                      setShowLocationDropdown(false);
                    }}
                    className="w-full text-left px-3 py-2 text-sm text-bambu-gray-light hover:bg-bambu-dark-tertiary hover:text-white transition-colors"
                  >
                    {location}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      <div className="w-px h-6 bg-bambu-dark-tertiary" />

      {/* Action buttons */}
      <Button
        size="sm"
        className="bg-red-500 hover:bg-red-600"
        onClick={() => onAction('stop')}
        disabled={actionPending || !canControl || !anyStoppable}
        title={!canControl ? t('printers.permission.noControl') : !anyStoppable ? t('printers.bulk.noneApplicable') : undefined}
      >
        <Square className="w-3.5 h-3.5" />
        {t('printers.bulk.actions.stop')}
      </Button>

      <Button
        variant="secondary"
        size="sm"
        onClick={() => onAction('pause')}
        disabled={actionPending || !canControl || !anyRunning}
        title={!canControl ? t('printers.permission.noControl') : !anyRunning ? t('printers.bulk.noneApplicable') : undefined}
      >
        <Pause className="w-3.5 h-3.5" />
        {t('printers.bulk.actions.pause')}
      </Button>

      <Button
        variant="secondary"
        size="sm"
        onClick={() => onAction('resume')}
        disabled={actionPending || !canControl || !anyPaused}
        title={!canControl ? t('printers.permission.noControl') : !anyPaused ? t('printers.bulk.noneApplicable') : undefined}
      >
        <Play className="w-3.5 h-3.5" />
        {t('printers.bulk.actions.resume')}
      </Button>

      <Button
        variant="secondary"
        size="sm"
        onClick={() => onAction('clearHMS')}
        disabled={actionPending || !canControl || !anyWithHMS}
        title={!canControl ? t('printers.permission.noControl') : !anyWithHMS ? t('printers.bulk.noneApplicable') : undefined}
      >
        <BellOff className="w-3.5 h-3.5" />
        {t('printers.bulk.actions.clearHMS')}
      </Button>

      <Button
        variant="secondary"
        size="sm"
        onClick={() => onAction('clearPlate')}
        disabled={actionPending || !canClearPlate || !anyNeedsClearPlate}
        title={!canClearPlate ? t('printers.permission.noControl') : !anyNeedsClearPlate ? t('printers.bulk.noneApplicable') : undefined}
      >
        <Eraser className="w-3.5 h-3.5" />
        {t('printers.bulk.actions.clearPlate')}
      </Button>
    </div>
  );
}
