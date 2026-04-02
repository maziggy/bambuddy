import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, AlertTriangle, RefreshCw } from 'lucide-react';
import type { InventorySpool } from '../../api/client';
import { spoolbuddyApi } from '../../api/client';
import { SpoolIcon } from './SpoolIcon';

const DEFAULT_CORE_WEIGHT_KEY = 'spoolbuddy-default-core-weight';

function getDefaultCoreWeight(): number {
  try {
    const stored = localStorage.getItem(DEFAULT_CORE_WEIGHT_KEY);
    if (stored) {
      const weight = parseInt(stored, 10);
      if (weight >= 0 && weight <= 500) return weight;
    }
  } catch {
    // Ignore errors
  }
  return 250;
}

interface InventorySpoolInfoCardProps {
  spool: InventorySpool;
  liveScaleWeight: number | null;
  persistedGrossWeight?: number | null;
  onClose?: () => void;
  onSyncWeight?: () => void;
  onAssignToAms?: () => void;
  className?: string;
}

export function InventorySpoolInfoCard({
  spool,
  liveScaleWeight,
  persistedGrossWeight,
  onClose,
  onSyncWeight,
  onAssignToAms,
  className,
}: InventorySpoolInfoCardProps) {
  const { t } = useTranslation();
  const [syncing, setSyncing] = useState(false);
  const [synced, setSynced] = useState(false);
  const [syncedGrossWeight, setSyncedGrossWeight] = useState<number | null>(null);

  const colorHex = spool.rgba ? `#${spool.rgba.slice(0, 6)}` : '#808080';

  const coreWeight = (spool.core_weight && spool.core_weight > 0)
    ? spool.core_weight
    : getDefaultCoreWeight();

  const grossWeightFromScale = liveScaleWeight !== null
    ? Math.round(Math.max(0, liveScaleWeight))
    : null;

  // Inventory scenario: prefer the most recently synced value in this modal session.
  const displayedGrossWeight = syncedGrossWeight ?? (
    persistedGrossWeight !== undefined
      ? (persistedGrossWeight !== null ? Math.round(Math.max(0, persistedGrossWeight)) : null)
      : grossWeightFromScale
  );

  const inventoryRemaining = Math.round(Math.max(0,
    (spool.label_weight || 0) - (spool.weight_used || 0)
  ));

  // Use live scale for remaining/fill only when scale has a meaningful reading.
  const minDynamicScaleReading = 10;
  const useDynamicRemaining = grossWeightFromScale !== null
    && grossWeightFromScale >= minDynamicScaleReading;

  const remaining = useDynamicRemaining
    ? Math.round(Math.max(0, grossWeightFromScale - coreWeight))
    : inventoryRemaining;

  const labelWeight = Math.round(spool.label_weight || 1000);
  const fillPercent = labelWeight > 0 ? Math.min(100, Math.round((remaining / labelWeight) * 100)) : null;
  const fillColor = fillPercent !== null
    ? (fillPercent > 50 ? '#22c55e' : fillPercent > 20 ? '#eab308' : '#ef4444')
    : '#808080';

  const netWeight = Math.max(0,
    (spool.label_weight || 0) - (spool.weight_used || 0)
  );
  const calculatedWeight = netWeight + coreWeight;
  const difference = grossWeightFromScale !== null ? grossWeightFromScale - calculatedWeight : null;
  const isMatch = difference !== null ? Math.abs(difference) <= 50 : null;

  // Inventory fallback so gross is always populated across spools.
  const inventoryDerivedGrossWeight = Math.round(calculatedWeight);
  const resolvedGrossWeight = displayedGrossWeight ?? inventoryDerivedGrossWeight;
  const nozzleTempRange = (spool.nozzle_temp_min != null && spool.nozzle_temp_max != null)
    ? `${spool.nozzle_temp_min}-${spool.nozzle_temp_max}\u00B0C`
    : null;
  const slicerPreset = spool.slicer_filament_name || spool.slicer_filament || null;
  const note = spool.note?.trim() || null;
  const kFactorSummary = (spool.k_profiles && spool.k_profiles.length > 0)
    ? Array.from(new Set(spool.k_profiles.map(kp => kp.k_value.toFixed(3)))).join(', ')
    : null;

  const handleSyncWeight = async () => {
    if (liveScaleWeight === null) return;
    const roundedLiveWeight = Math.round(liveScaleWeight);
    setSyncing(true);
    try {
      await spoolbuddyApi.updateSpoolWeight(spool.id, roundedLiveWeight);
      setSyncedGrossWeight(roundedLiveWeight);
      setSynced(true);
      onSyncWeight?.();
      setTimeout(() => setSynced(false), 3000);
    } catch (e) {
      console.error('Failed to sync weight:', e);
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className={`flex flex-col items-center space-y-4 max-w-md ${className ?? ''}`}>
      <div className="flex items-start gap-5">
        <div className="relative shrink-0">
          <SpoolIcon color={colorHex} isEmpty={false} size={100} />
          {fillPercent !== null && (
            <div
              className="absolute -bottom-2 -right-2 px-2 py-0.5 rounded-full text-xs font-bold text-white shadow-lg"
              style={{ backgroundColor: fillColor }}
            >
              {fillPercent}%
            </div>
          )}
        </div>

        <div className="flex-1 min-w-0 pt-1">
          <h3 className="text-lg font-semibold text-zinc-100">
            {spool.color_name || 'Unknown color'}
          </h3>
          <p className="text-sm text-zinc-400">
            {spool.brand} &bull; {spool.material}
            {spool.subtype && ` ${spool.subtype}`}
          </p>

          <div className="mt-3">
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-bold font-mono text-zinc-100">{remaining}g</span>
              <span className="text-sm text-zinc-500">/ {labelWeight}g</span>
            </div>
            <p className="text-xs text-zinc-500 mt-0.5">{t('spoolbuddy.spool.remaining', 'Remaining')}</p>

            <div className="mt-2 max-w-xs">
              <div className="h-2 bg-zinc-700 rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{ width: `${fillPercent ?? 0}%`, backgroundColor: fillColor }}
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm bg-zinc-800 rounded-lg p-4 w-full">
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.dashboard.grossWeight', 'Gross weight')}</span>
          <span className="font-mono text-zinc-300">{resolvedGrossWeight}g</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.spool.coreWeight', 'Core')}</span>
          <span className="font-mono text-zinc-300">{coreWeight}g</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.dashboard.spoolSize', 'Spool size')}</span>
          <span className="font-mono text-zinc-300">{labelWeight}g</span>
        </div>
        <div className="flex justify-between items-center">
          <span className="text-zinc-500">{t('spoolbuddy.spool.scaleWeight', 'Scale')}</span>
          {grossWeightFromScale !== null ? (
            <span className={`flex items-center gap-1 font-mono ${isMatch ? 'text-green-500' : 'text-yellow-500'}`}>
              {grossWeightFromScale}g
              {isMatch ? (
                <Check className="w-3.5 h-3.5" />
              ) : (
                <>
                  <AlertTriangle className="w-3.5 h-3.5" />
                  <button
                    onClick={handleSyncWeight}
                    className="p-1 hover:bg-green-500/20 rounded transition-colors text-green-500"
                    title={t('spoolbuddy.dashboard.syncWeight', 'Sync Weight')}
                  >
                    <RefreshCw className="w-4 h-4" />
                  </button>
                </>
              )}
            </span>
          ) : (
            <span className="text-zinc-500">{'\u2014'}</span>
          )}
        </div>
        <div className="flex justify-between items-center">
          <span className="text-zinc-500">{t('spoolbuddy.dashboard.tagId', 'Tag')}</span>
          <span className="font-mono text-xs text-zinc-400 truncate max-w-[120px]" title={spool.tag_uid || ''}>
            {spool.tag_uid ? spool.tag_uid.slice(-8) : '\u2014'}
          </span>
        </div>
        {nozzleTempRange && (
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">{t('spoolbuddy.inventory.nozzleTemp', 'Nozzle')}</span>
            <span className="font-mono text-zinc-300">{nozzleTempRange}</span>
          </div>
        )}
        {spool.cost_per_kg != null && spool.cost_per_kg > 0 && (
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">{t('spoolbuddy.inventory.costPerKg', 'Cost/kg')}</span>
            <span className="font-mono text-zinc-300">{spool.cost_per_kg.toFixed(2)}/kg</span>
          </div>
        )}
        {kFactorSummary && (
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">{t('spoolbuddy.inventory.kProfiles', 'K-Profile')}</span>
            <span className="font-mono text-zinc-300 truncate max-w-[220px] text-right" title={kFactorSummary}>{kFactorSummary}</span>
          </div>
        )}
        {slicerPreset && (
          <div className="min-w-0">
            <p className="text-xs text-zinc-500 mb-1">{t('spoolbuddy.inventory.slicerFilament', 'Slicer Filament')}</p>
            <p className="text-sm text-zinc-300 whitespace-pre-wrap break-words">{slicerPreset}</p>
          </div>
        )}
        {note && (
          <div className="col-span-2">
            <p className="text-xs text-zinc-500 mb-1">{t('spoolbuddy.inventory.note', 'Note')}</p>
            <p className="text-sm leading-5 text-zinc-300 whitespace-pre-wrap break-words max-h-[3.75rem] overflow-y-auto pr-1">{note}</p>
          </div>
        )}
      </div>

      <div className="flex gap-2 justify-center">
        {onAssignToAms && (
          <button
            onClick={onAssignToAms}
            className="px-5 py-2.5 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 transition-colors min-h-[44px]"
          >
            {t('spoolbuddy.modal.assignToAms', 'Assign to AMS')}
          </button>
        )}
        <button
          onClick={handleSyncWeight}
          disabled={liveScaleWeight === null || syncing}
          className={`px-5 py-2.5 rounded-lg text-sm font-medium transition-colors min-h-[44px] ${
            synced
              ? 'bg-green-600/20 text-green-400'
              : onAssignToAms
                ? 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600 disabled:opacity-40 disabled:cursor-not-allowed'
                : 'bg-green-600 text-white hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed'
          }`}
        >
          {syncing ? '...' : synced ? t('spoolbuddy.dashboard.weightSynced', 'Synced!') : t('spoolbuddy.dashboard.syncWeight', 'Sync Weight')}
        </button>
        {onClose && (
          <button
            onClick={onClose}
            className="px-5 py-2.5 rounded-lg text-sm font-medium bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors min-h-[44px]"
          >
            {t('spoolbuddy.dashboard.close', 'Close')}
          </button>
        )}
      </div>
    </div>
  );
}
