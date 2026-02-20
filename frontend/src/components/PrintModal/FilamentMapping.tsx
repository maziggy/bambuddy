import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Circle, Check, AlertTriangle, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react';
import { api } from '../../api/client';
import { useFilamentMapping } from '../../hooks/useFilamentMapping';
import { getGlobalTrayId } from '../../utils/amsHelpers';
import { getColorName } from '../../utils/colors';
import type { FilamentMappingProps } from './types';

/**
 * Filament mapping UI for comparing required filaments with loaded AMS slots.
 * Shows auto-matched and manually overridden slot assignments.
 */
export function FilamentMapping({
  printerId,
  filamentReqs,
  manualMappings,
  onManualMappingChange,
  currencySymbol,
  defaultCostPerKg,
  defaultExpanded = false,
}: FilamentMappingProps & { defaultExpanded?: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);

  // Fetch printer status
  const { data: printerStatus } = useQuery({
    queryKey: ['printer-status', printerId],
    queryFn: () => api.getPrinterStatus(printerId),
    enabled: !!printerId,
  });

  const { data: assignments } = useQuery({
    queryKey: ['spool-assignments', printerId],
    queryFn: () => api.getAssignments(printerId),
    enabled: !!printerId,
  });

  const { loadedFilaments, filamentComparison, hasTypeMismatch, hasColorMismatch } =
    useFilamentMapping(filamentReqs, printerStatus, manualMappings);

  const trayCostMap = useMemo(() => {
    const map = new Map<number, number | null>();
    for (const assignment of assignments || []) {
      const isExternal = assignment.ams_id === 255;
      const globalTrayId = getGlobalTrayId(assignment.ams_id, assignment.tray_id, isExternal);
      map.set(globalTrayId, assignment.spool?.cost_per_kg ?? null);
    }
    return map;
  }, [assignments]);

  const totalCost = useMemo(() => {
    let total = 0;
    for (const item of filamentComparison) {
      const trayId = item.loaded?.globalTrayId;
      if (trayId == null) continue;
      const assignedCost = trayCostMap.get(trayId) ?? null;
      const costPerKg = assignedCost ?? defaultCostPerKg;
      if (costPerKg > 0) {
        total += (item.used_grams / 1000) * costPerKg;
      }
    }
    return total;
  }, [filamentComparison, trayCostMap, defaultCostPerKg]);

  const hasFilamentReqs = filamentReqs?.filaments && filamentReqs.filaments.length > 0;
  const isDualNozzle = filamentReqs?.filaments?.some((f) => f.nozzle_id != null) ?? false;

  // Don't render if no filament requirements
  if (!hasFilamentReqs) {
    return null;
  }

  // Don't render until we have printer status to do the comparison
  if (!printerStatus) {
    return null;
  }

  // Determine status indicator color
  const statusColor = hasTypeMismatch
    ? '#f97316' // orange
    : hasColorMismatch
    ? '#facc15' // yellow
    : '#00ae42'; // green

  const handleSlotChange = (slotId: number, value: string) => {
    if (slotId > 0) {
      if (value === '') {
        // Clear manual override
        const next = { ...manualMappings };
        delete next[slotId];
        onManualMappingChange(next);
      } else {
        onManualMappingChange({
          ...manualMappings,
          [slotId]: parseInt(value, 10),
        });
      }
    }
  };

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      // Request fresh data from printer via MQTT pushall command
      await api.refreshPrinterStatus(printerId);
      // Wait a moment for printer to respond, then refetch
      await new Promise((r) => setTimeout(r, 500));
      await queryClient.refetchQueries({ queryKey: ['printer-status', printerId] });
    } finally {
      setIsRefreshing(false);
    }
  };

  // Extract cost display logic from JSX IIFE to a variable
  const hasAnyCost = Array.from(trayCostMap.values()).some((v) => v != null && v > 0);
  const costDisplay = (totalCost > 0 || hasAnyCost) ? (
    <div className="text-xs text-bambu-gray">
      {t('printModal.totalCost')} <span className="text-white">{currencySymbol}{totalCost.toFixed(2)}</span>
    </div>
  ) : (
    <div className="text-xs text-bambu-gray">
      {t('printModal.totalCost')} <span className="text-white">N/A</span>
    </div>
  );

  return (
    <div className="mb-4">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-2 text-sm text-bambu-gray hover:text-white transition-colors w-full"
      >
        <Circle className="w-4 h-4" fill={statusColor} stroke="none" />
        <span>{t('printModal.filamentMapping')}</span>
        {hasTypeMismatch ? (
          <span className="text-xs text-orange-400">(Type not found)</span>
        ) : hasColorMismatch ? (
          <span className="text-xs text-yellow-400">(Color mismatch)</span>
        ) : (
          <span className="text-xs text-bambu-green">(Ready)</span>
        )}
        {isExpanded ? (
          <ChevronUp className="w-4 h-4 ml-auto" />
        ) : (
          <ChevronDown className="w-4 h-4 ml-auto" />
        )}
      </button>

      {isExpanded && (
        <div className="mt-2 bg-bambu-dark rounded-lg p-3 space-y-2">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-bambu-gray">Click to change slot assignment</span>
            <button
              type="button"
              onClick={handleRefresh}
              className="flex items-center gap-1 px-2 py-0.5 text-xs rounded border border-bambu-gray/30 hover:border-bambu-gray hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray hover:text-white"
              disabled={isRefreshing}
            >
              <RefreshCw className={`w-3 h-3 ${isRefreshing ? 'animate-spin' : ''}`} />
              <span>Re-read</span>
            </button>
          </div>
          {filamentComparison.map((item, idx) => (
            <div
              key={idx}
              className="grid items-center gap-2 text-xs"
              style={{ gridTemplateColumns: '16px minmax(70px, 1fr) auto 2fr 16px' }}
            >
              {/* Required color */}
              <span title={`Required: ${item.type} - ${getColorName(item.color)}`}>
                <Circle className="w-3 h-3" fill={item.color} stroke={item.color} />
              </span>
              {/* Required type + grams + nozzle badge */}
              <span className="text-white truncate flex items-center gap-1">
                {isDualNozzle && item.nozzle_id != null && (
                  <span
                    className="inline-flex items-center justify-center w-3.5 h-3.5 rounded text-[9px] font-bold leading-none bg-bambu-gray/20 text-bambu-gray shrink-0"
                    title={item.nozzle_id === 1 ? t('printModal.leftNozzleTooltip') : t('printModal.rightNozzleTooltip')}
                  >
                    {item.nozzle_id === 1 ? t('printModal.leftNozzle') : t('printModal.rightNozzle')}
                  </span>
                )}
                {item.type} <span className="text-bambu-gray">({item.used_grams}g)</span>
              </span>
              {/* Arrow */}
              <span className="text-bambu-gray">→</span>
              {/* Slot selector dropdown */}
              <select
                value={item.loaded?.globalTrayId ?? ''}
                onChange={(e) => handleSlotChange(item.slot_id || 0, e.target.value)}
                className={`flex-1 px-2 py-1 rounded border text-xs bg-bambu-dark-secondary focus:outline-none focus:ring-1 focus:ring-bambu-green ${
                  item.status === 'match'
                    ? 'border-bambu-green/50 text-bambu-green'
                    : item.status === 'type_only'
                    ? 'border-yellow-400/50 text-yellow-400'
                    : 'border-orange-400/50 text-orange-400'
                } ${item.isManual ? 'ring-1 ring-blue-400/50' : ''}`}
                title={item.isManual ? 'Manually selected' : 'Auto-matched'}
              >
                <option value="" className="bg-bambu-dark text-bambu-gray">
                  -- Select slot --
                </option>
                {loadedFilaments
                  .filter((f) => item.nozzle_id == null || f.extruderId === item.nozzle_id)
                  .map((f) => (
                  <option key={f.globalTrayId} value={f.globalTrayId} className="bg-bambu-dark text-white">
                    {f.label}: {f.type} ({f.colorName})
                  </option>
                ))}
              </select>
              {/* Status icon */}
              {item.status === 'match' ? (
                <Check className="w-3 h-3 text-bambu-green" />
              ) : item.status === 'type_only' ? (
                <span title="Same type, different color">
                  <AlertTriangle className="w-3 h-3 text-yellow-400" />
                </span>
              ) : (
                <span title="Filament type not loaded">
                  <AlertTriangle className="w-3 h-3 text-orange-400" />
                </span>
              )}
            </div>
          ))}
          {costDisplay}
          {hasTypeMismatch && (
            <p className="text-xs text-orange-400 mt-2">Required filament type not found in printer.</p>
          )}
        </div>
      )}
    </div>
  );
}
