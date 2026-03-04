import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, AlertTriangle, RefreshCw } from 'lucide-react';
import type { MatchedSpool } from '../../hooks/useSpoolBuddyState';
import { spoolbuddyApi } from '../../api/client';
import { SpoolIcon } from './SpoolIcon';

// Storage key for default core weight
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
  return 250; // Default 250g (typical Bambu spool core)
}

interface SpoolInfoCardProps {
  spool: MatchedSpool;
  scaleWeight: number | null;
  weightStable: boolean;
  onClose?: () => void;
  onSyncWeight?: () => void;
}

export function SpoolInfoCard({ spool, scaleWeight, weightStable, onClose, onSyncWeight }: SpoolInfoCardProps) {
  const { t } = useTranslation();
  const [syncing, setSyncing] = useState(false);
  const [synced, setSynced] = useState(false);

  const colorHex = spool.rgba ? `#${spool.rgba.slice(0, 6)}` : '#808080';

  // Use spool's core_weight if set, otherwise fall back to default
  const coreWeight = (spool.core_weight && spool.core_weight > 0)
    ? spool.core_weight
    : getDefaultCoreWeight();

  // Gross weight from scale (live) or fallback
  const grossWeight = scaleWeight !== null
    ? Math.round(Math.max(0, scaleWeight))
    : null;

  // Remaining filament = gross - core
  const remaining = grossWeight !== null
    ? Math.round(Math.max(0, grossWeight - coreWeight))
    : null;

  const labelWeight = Math.round(spool.label_weight || 1000);
  const fillPercent = remaining !== null ? Math.min(100, Math.round((remaining / labelWeight) * 100)) : null;
  const fillColor = fillPercent !== null
    ? fillPercent > 50 ? '#22c55e' : fillPercent > 20 ? '#eab308' : '#ef4444'
    : '#808080';

  // Weight comparison (scale vs calculated expected)
  const netWeight = Math.max(0,
    (spool.label_weight || 0) - (spool.weight_used || 0)
  );
  const calculatedWeight = netWeight + coreWeight;
  const difference = grossWeight !== null ? grossWeight - calculatedWeight : null;
  const isMatch = difference !== null ? Math.abs(difference) <= 50 : null;

  const handleSyncWeight = async () => {
    if (scaleWeight === null || !weightStable) return;
    setSyncing(true);
    try {
      await spoolbuddyApi.updateSpoolWeight(spool.id, Math.round(scaleWeight));
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
    <div className="flex flex-col items-center space-y-4 max-w-md">
      {/* Top section: Spool icon + main info */}
      <div className="flex items-start gap-5">
        {/* Spool visualization */}
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

        {/* Main info */}
        <div className="flex-1 min-w-0 pt-1">
          <h3 className="text-lg font-semibold text-zinc-100">
            {spool.color_name || 'Unknown color'}
          </h3>
          <p className="text-sm text-zinc-400">
            {spool.brand} &bull; {spool.material}
            {spool.subtype && ` ${spool.subtype}`}
          </p>

          {/* Filament remaining - big number */}
          {remaining !== null && (
            <div className="mt-3">
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-bold font-mono text-zinc-100">{remaining}g</span>
                <span className="text-sm text-zinc-500">/ {labelWeight}g</span>
              </div>
              <p className="text-xs text-zinc-500 mt-0.5">{t('spoolbuddy.spool.remaining', 'Remaining')}</p>

              {/* Fill bar */}
              <div className="mt-2 max-w-xs">
                <div className="h-2 bg-zinc-700 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${fillPercent}%`, backgroundColor: fillColor }}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Details grid */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm bg-zinc-800 rounded-lg p-3 w-full">
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.dashboard.grossWeight', 'Gross weight')}</span>
          <span className="font-mono text-zinc-300">{grossWeight !== null ? `${grossWeight}g` : '\u2014'}</span>
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
          {grossWeight !== null ? (
            <span className={`flex items-center gap-1 font-mono ${isMatch ? 'text-green-500' : 'text-yellow-500'}`}>
              {grossWeight}g
              {isMatch ? (
                <Check className="w-3 h-3" />
              ) : (
                <>
                  <AlertTriangle className="w-3 h-3" />
                  <button
                    onClick={handleSyncWeight}
                    className="p-1 hover:bg-green-500/20 rounded transition-colors text-green-500"
                    title={t('spoolbuddy.dashboard.syncWeight', 'Sync Weight')}
                  >
                    <RefreshCw className="w-3.5 h-3.5" />
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
      </div>

      {/* Action buttons */}
      <div className="flex gap-2 justify-center">
        <button
          onClick={handleSyncWeight}
          disabled={!weightStable || scaleWeight === null || syncing}
          className={`px-5 py-2.5 rounded-lg text-sm font-medium transition-colors min-h-[44px] ${
            synced
              ? 'bg-green-600/20 text-green-400'
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

interface UnknownTagCardProps {
  tagUid: string;
  scaleWeight: number | null;
  coreWeight?: number;
  onLinkSpool?: () => void;
  onClose?: () => void;
}

export function UnknownTagCard({ tagUid, scaleWeight, coreWeight, onLinkSpool, onClose }: UnknownTagCardProps) {
  const { t } = useTranslation();
  const defaultCoreWeight = coreWeight ?? getDefaultCoreWeight();
  const grossWeight = scaleWeight !== null
    ? Math.round(Math.max(0, scaleWeight))
    : null;
  const estimatedRemaining = grossWeight !== null
    ? Math.round(Math.max(0, grossWeight - defaultCoreWeight))
    : null;

  return (
    <div className="flex flex-col items-center text-center space-y-5">
      <div className="w-20 h-20 rounded-2xl bg-green-500/15 flex items-center justify-center">
        <svg className="w-10 h-10 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z" />
        </svg>
      </div>
      <div>
        <h3 className="text-lg font-semibold text-zinc-100">{t('spoolbuddy.dashboard.newTag', 'New Tag Detected')}</h3>
        <p className="text-sm text-zinc-500 font-mono mt-1">{tagUid}</p>
      </div>
      {grossWeight !== null && (
        <div className="text-sm text-zinc-400">
          <span className="font-mono font-semibold">{grossWeight}g</span> {t('spoolbuddy.dashboard.onScale', 'on scale')}
          {estimatedRemaining !== null && estimatedRemaining > 0 && (
            <span className="text-zinc-500"> &bull; ~{estimatedRemaining}g filament</span>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2 justify-center">
        {onLinkSpool && (
          <button
            onClick={onLinkSpool}
            className="px-5 py-2.5 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 transition-colors min-h-[44px]"
          >
            <svg className="w-4 h-4 inline-block mr-1.5 -mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
            </svg>
            {t('spoolbuddy.dashboard.linkSpool', 'Link to Spool')}
          </button>
        )}
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
