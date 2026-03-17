import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, RefreshCw, AlertTriangle, X } from 'lucide-react';
import type { MatchedSpool } from '../../hooks/useSpoolBuddyState';
import { spoolbuddyApi } from '../../api/client';
import { SpoolIcon } from './SpoolIcon';

// Storage key for default core weight (shared with SpoolInfoCard)
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

interface TagDetectedModalProps {
  isOpen: boolean;
  onClose: () => void;
  spool: MatchedSpool | null;
  tagUid: string | null;
  scaleWeight: number | null;
  weightStable: boolean;
  onSyncWeight: () => void;
  onAssignToAms: () => void;
  onLinkSpool?: () => void;
  onAddToInventory: () => void;
}

export function TagDetectedModal({
  isOpen,
  onClose,
  spool,
  tagUid,
  scaleWeight,
  weightStable,
  onSyncWeight,
  onAssignToAms,
  onLinkSpool,
  onAddToInventory,
}: TagDetectedModalProps) {
  const [syncing, setSyncing] = useState(false);
  const [synced, setSynced] = useState(false);

  // Reset sync state when spool changes
  useEffect(() => {
    setSyncing(false);
    setSynced(false);
  }, [spool?.id]);

  // Handle escape key
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
    };
  }, [isOpen, handleKeyDown]);

  if (!isOpen) return null;

  const handleSyncWeight = async () => {
    if (scaleWeight === null || !weightStable || !spool) return;
    setSyncing(true);
    try {
      await spoolbuddyApi.updateSpoolWeight(spool.id, Math.round(scaleWeight));
      setSynced(true);
      onSyncWeight();
      setTimeout(() => setSynced(false), 3000);
    } catch (e) {
      console.error('Failed to sync weight:', e);
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 animate-fade-in" onClick={onClose}>
      <div
        className="bg-zinc-800 rounded-2xl shadow-2xl w-full max-w-xl mx-4 animate-slide-up"
        onClick={(e) => e.stopPropagation()}
      >
        {spool ? (
          <KnownSpoolView
            spool={spool}
            scaleWeight={scaleWeight}
            weightStable={weightStable}
            syncing={syncing}
            synced={synced}
            onSyncWeight={handleSyncWeight}
            onAssignToAms={onAssignToAms}
            onClose={onClose}
          />
        ) : (
          <UnknownTagView
            tagUid={tagUid}
            scaleWeight={scaleWeight}
            onAddToInventory={onAddToInventory}
            onLinkSpool={onLinkSpool}
            onClose={onClose}
          />
        )}
      </div>
    </div>
  );
}

// --- Known spool view ---

interface KnownSpoolViewProps {
  spool: MatchedSpool;
  scaleWeight: number | null;
  weightStable: boolean;
  syncing: boolean;
  synced: boolean;
  onSyncWeight: () => void;
  onAssignToAms: () => void;
  onClose: () => void;
}

function KnownSpoolView({ spool, scaleWeight, weightStable, syncing, synced, onSyncWeight, onAssignToAms, onClose }: KnownSpoolViewProps) {
  const { t } = useTranslation();
  const colorHex = spool.rgba ? `#${spool.rgba.slice(0, 6)}` : '#808080';

  const coreWeight = (spool.core_weight && spool.core_weight > 0)
    ? spool.core_weight
    : getDefaultCoreWeight();

  const grossWeight = scaleWeight !== null
    ? Math.round(Math.max(0, scaleWeight))
    : null;

  const remaining = grossWeight !== null
    ? Math.round(Math.max(0, grossWeight - coreWeight))
    : null;

  const labelWeight = Math.round(spool.label_weight || 1000);
  const fillPercent = remaining !== null ? Math.min(100, Math.round((remaining / labelWeight) * 100)) : null;
  const fillColor = fillPercent !== null
    ? fillPercent > 50 ? '#22c55e' : fillPercent > 20 ? '#eab308' : '#ef4444'
    : '#808080';

  // Weight comparison
  const netWeight = Math.max(0, (spool.label_weight || 0) - (spool.weight_used || 0));
  const calculatedWeight = netWeight + coreWeight;
  const difference = grossWeight !== null ? grossWeight - calculatedWeight : null;
  const isMatch = difference !== null ? Math.abs(difference) <= 50 : null;

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-semibold text-zinc-100">
          {t('spoolbuddy.modal.spoolDetected', 'Spool Detected')}
        </h2>
        <button onClick={onClose} className="p-2 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-700 transition-colors">
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Spool info */}
      <div className="flex items-start gap-5 mb-5">
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

          {remaining !== null && (
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
                    style={{ width: `${fillPercent}%`, backgroundColor: fillColor }}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Details grid */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm bg-zinc-900/50 rounded-lg p-4 mb-5">
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
              {isMatch ? <Check className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
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
      <div className="flex gap-3">
        <button
          onClick={onAssignToAms}
          className="flex-1 px-5 py-3 rounded-xl text-sm font-medium bg-green-600 text-white hover:bg-green-700 transition-colors min-h-[44px]"
        >
          {t('spoolbuddy.modal.assignToAms', 'Assign to AMS')}
        </button>
        <button
          onClick={onSyncWeight}
          disabled={!weightStable || scaleWeight === null || syncing}
          className={`flex-1 px-5 py-3 rounded-xl text-sm font-medium transition-colors min-h-[44px] ${
            synced
              ? 'bg-green-600/20 text-green-400'
              : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600 disabled:opacity-40 disabled:cursor-not-allowed'
          }`}
        >
          {syncing ? (
            <RefreshCw className="w-4 h-4 animate-spin inline-block mr-1.5" />
          ) : synced ? (
            <Check className="w-4 h-4 inline-block mr-1.5" />
          ) : null}
          {syncing
            ? t('spoolbuddy.modal.syncing', 'Syncing...')
            : synced
              ? t('spoolbuddy.modal.weightSynced', 'Synced!')
              : t('spoolbuddy.dashboard.syncWeight', 'Sync Weight')}
        </button>
        <button
          onClick={onClose}
          className="px-5 py-3 rounded-xl text-sm font-medium bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors min-h-[44px]"
        >
          {t('spoolbuddy.dashboard.close', 'Close')}
        </button>
      </div>
    </div>
  );
}

// --- Unknown tag view ---

interface UnknownTagViewProps {
  tagUid: string | null;
  scaleWeight: number | null;
  onAddToInventory: () => void;
  onLinkSpool?: () => void;
  onClose: () => void;
}

function UnknownTagView({ tagUid, scaleWeight, onAddToInventory, onLinkSpool, onClose }: UnknownTagViewProps) {
  const { t } = useTranslation();
  const grossWeight = scaleWeight !== null
    ? Math.round(Math.max(0, scaleWeight))
    : null;

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-semibold text-zinc-100">
          {t('spoolbuddy.modal.newTagDetected', 'New Tag Detected')}
        </h2>
        <button onClick={onClose} className="p-2 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-700 transition-colors">
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Tag info */}
      <div className="flex flex-col items-center text-center mb-6">
        <div className="w-20 h-20 rounded-2xl bg-green-500/15 flex items-center justify-center mb-4">
          <svg className="w-10 h-10 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z" />
          </svg>
        </div>

        <p className="text-sm text-zinc-500 font-mono mb-3">{tagUid}</p>

        {grossWeight !== null && (
          <div className="text-sm text-zinc-400">
            <span className="font-mono font-semibold text-zinc-200 text-lg">{grossWeight}g</span>
            <span className="ml-2">{t('spoolbuddy.dashboard.onScale', 'on scale')}</span>
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div className="flex gap-3">
        <button
          onClick={onAddToInventory}
          className="flex-1 px-5 py-3 rounded-xl text-sm font-medium bg-green-600 text-white hover:bg-green-700 transition-colors min-h-[44px]"
        >
          {t('spoolbuddy.modal.addToInventory', 'Add to Inventory')}
        </button>
        {onLinkSpool && (
          <button
            onClick={onLinkSpool}
            className="flex-1 px-5 py-3 rounded-xl text-sm font-medium bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors min-h-[44px]"
          >
            {t('spoolbuddy.dashboard.linkSpool', 'Link to Spool')}
          </button>
        )}
        <button
          onClick={onClose}
          className="px-5 py-3 rounded-xl text-sm font-medium bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors min-h-[44px]"
        >
          {t('spoolbuddy.dashboard.close', 'Close')}
        </button>
      </div>
    </div>
  );
}
