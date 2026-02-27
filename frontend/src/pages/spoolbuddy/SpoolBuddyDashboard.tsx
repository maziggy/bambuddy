import { useState, useEffect, useMemo, useRef } from 'react';
import { useOutletContext } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { SpoolBuddyOutletContext } from '../../components/spoolbuddy/SpoolBuddyLayout';
import { api, spoolbuddyApi, type InventorySpool } from '../../api/client';
import { SpoolIcon } from '../../components/spoolbuddy/SpoolIcon';
import { SpoolInfoCard, UnknownTagCard } from '../../components/spoolbuddy/SpoolInfoCard';
import { LinkSpoolModal } from '../../components/spoolbuddy/LinkSpoolModal';

// Color palette for the cycling spool animation
const SPOOL_COLORS = [
  '#00AE42', '#FF6B35', '#3B82F6', '#EF4444', '#A855F7',
  '#FBBF24', '#14B8A6', '#EC4899', '#F97316', '#22C55E',
];

// --- Idle state with color-cycling spool and NFC waves ---
function ColorCyclingSpool() {
  const { t } = useTranslation();
  const [colorIndex, setColorIndex] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setColorIndex((prev) => (prev + 1) % SPOOL_COLORS.length);
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const currentColor = SPOOL_COLORS[colorIndex];

  return (
    <div className="flex flex-col items-center text-center">
      {/* Animated spool with NFC waves */}
      <div className="relative mb-6 flex items-center justify-center" style={{ width: 160, height: 160 }}>
        {/* NFC wave rings */}
        <div className="absolute w-24 h-24 rounded-full border-2 border-green-500/30 animate-ping" style={{ animationDuration: '2.5s' }} />
        <div className="absolute w-32 h-32 rounded-full border border-green-500/20 animate-ping" style={{ animationDuration: '2.5s', animationDelay: '0.4s' }} />
        <div className="absolute w-40 h-40 rounded-full border border-green-500/10 animate-ping" style={{ animationDuration: '2.5s', animationDelay: '0.8s' }} />

        {/* Spool icon with color transition and glow */}
        <div className="relative">
          <div
            className="absolute -inset-4 rounded-full blur-2xl opacity-30 transition-colors duration-1000"
            style={{ backgroundColor: currentColor }}
          />
          <div className="transition-all duration-1000">
            <SpoolIcon color={currentColor} isEmpty={false} size={100} />
          </div>
        </div>
      </div>

      {/* Text content */}
      <div className="space-y-2">
        <p className="text-xl font-medium text-zinc-300">
          {t('spoolbuddy.dashboard.readyToScan', 'Ready to scan')}
        </p>
        <p className="text-sm text-zinc-500">
          {t('spoolbuddy.dashboard.idleMessage', 'Place a spool on the scale to identify it')}
        </p>
      </div>

      {/* Subtle hint */}
      <div className="mt-6 flex items-center gap-2 text-sm text-zinc-600">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span>{t('spoolbuddy.dashboard.nfcHint', 'NFC tag will be read automatically')}</span>
      </div>
    </div>
  );
}

// --- Offline state ---
function DeviceOfflineState() {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col items-center text-center">
      {/* Offline icon */}
      <div className="relative mb-6 flex items-center justify-center" style={{ width: 160, height: 160 }}>
        <div className="w-24 h-24 rounded-full bg-zinc-800 flex items-center justify-center">
          <svg className="w-12 h-12 text-zinc-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M18.364 5.636a9 9 0 010 12.728m0 0l-12.728-12.728m12.728 12.728L5.636 5.636m12.728 0a9 9 0 00-12.728 0m0 12.728a9 9 0 010-12.728" />
          </svg>
        </div>
      </div>

      <div className="space-y-2">
        <p className="text-lg font-medium text-zinc-500">
          {t('spoolbuddy.status.deviceOffline', 'Device Offline')}
        </p>
        <p className="text-sm text-zinc-600">
          {t('spoolbuddy.status.connectDisplay', 'Connect the SpoolBuddy display to scan spools')}
        </p>
      </div>

      <div className="mt-6 flex items-center gap-2 text-xs text-zinc-600">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8.111 16.404a5.5 5.5 0 017.778 0M12 20h.01m-7.08-7.071c3.904-3.905 10.236-3.905 14.14 0M1.394 9.393c5.857-5.857 15.355-5.857 21.213 0" />
        </svg>
        <span>{t('spoolbuddy.status.waitingConnection', 'Waiting for device connection...')}</span>
      </div>
    </div>
  );
}

// --- Main Dashboard ---
export function SpoolBuddyDashboard() {
  const { sbState } = useOutletContext<SpoolBuddyOutletContext>();
  const { t } = useTranslation();

  // Fetch spools for stats, tag lookup, and untagged list
  const { data: spools = [], refetch: refetchSpools } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(false),
  });

  // Current Spool card state - persists until user closes or new tag detected
  const [displayedTagId, setDisplayedTagId] = useState<string | null>(null);
  const [displayedWeight, setDisplayedWeight] = useState<number | null>(null);
  const [hiddenTagId, setHiddenTagId] = useState<string | null>(null);
  const [showLinkModal, setShowLinkModal] = useState(false);

  // Track current tag from state
  const currentTagId = sbState.matchedSpool?.tag_uid ?? sbState.unknownTagUid ?? null;
  const currentWeight = sbState.weight;
  const weightStable = sbState.weightStable;

  // Stabilized scale display: only update when change exceeds threshold to prevent bouncing
  const stableDisplayWeight = useRef<number | null>(null);
  const WEIGHT_THRESHOLD = 3; // grams - ignore changes smaller than this
  if (currentWeight === null) {
    stableDisplayWeight.current = null;
  } else if (stableDisplayWeight.current === null || Math.abs(currentWeight - stableDisplayWeight.current) >= WEIGHT_THRESHOLD || weightStable) {
    stableDisplayWeight.current = currentWeight;
  }
  const scaleDisplayValue = stableDisplayWeight.current;

  // Find spool by tag_id in the loaded spools list
  const displayedSpool = useMemo(() => {
    if (!displayedTagId) return null;
    return spools.find((s) => s.tag_uid === displayedTagId) ?? null;
  }, [displayedTagId, spools]);

  // Untagged spools for the Link feature
  const untaggedSpools = useMemo(() => {
    return spools.filter((s) => !s.tag_uid && !s.archived_at);
  }, [spools]);

  // Handle tag detection - show card when tag detected, keep until user closes or new tag
  useEffect(() => {
    if (currentTagId) {
      const isHidden = hiddenTagId === currentTagId;
      const isDifferentTag = displayedTagId !== null && displayedTagId !== currentTagId;

      if (isDifferentTag || (!isHidden && displayedTagId !== currentTagId)) {
        setDisplayedTagId(currentTagId);
        setDisplayedWeight(null);
        setHiddenTagId(null);
      }

      // Update weight when stable and card is visible
      if (!isHidden && currentWeight !== null && weightStable) {
        setDisplayedWeight(Math.round(Math.max(0, currentWeight)));
      }
    } else {
      // Tag removed - clear hidden state so same tag can show when re-placed
      if (hiddenTagId) {
        setDisplayedTagId(null);
        setHiddenTagId(null);
        setDisplayedWeight(null);
      }
    }
  }, [currentTagId, currentWeight, weightStable, displayedTagId, hiddenTagId]);

  // Auto-sync weight once when known spool first detected
  const [weightUpdatedForSpool, setWeightUpdatedForSpool] = useState<number | null>(null);

  useEffect(() => {
    if (displayedSpool?.id !== weightUpdatedForSpool) {
      setWeightUpdatedForSpool(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayedSpool?.id]);

  useEffect(() => {
    if (displayedSpool && currentTagId && weightStable && weightUpdatedForSpool !== displayedSpool.id) {
      setWeightUpdatedForSpool(displayedSpool.id);
      const newWeight = currentWeight !== null ? Math.round(Math.max(0, currentWeight)) : null;
      if (newWeight !== null) {
        spoolbuddyApi.updateSpoolWeight(displayedSpool.id, newWeight)
          .catch((err) => console.error('Failed to update spool weight:', err));
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayedSpool?.id, currentTagId, weightStable]);

  const handleCloseSpoolCard = () => {
    setHiddenTagId(displayedTagId);
  };

  const handleLinkTagToSpool = async (spool: InventorySpool) => {
    if (!displayedTagId) return;
    try {
      await api.updateSpool(spool.id, { tag_uid: displayedTagId });
      setShowLinkModal(false);
      refetchSpools();
    } catch (e) {
      console.error('Failed to link tag:', e);
    }
  };

  // Close handler for the Current Spool card
  const showCard = displayedTagId && hiddenTagId !== displayedTagId;
  const isMatchedSpool = displayedSpool !== null;
  const isUnknownTag = showCard && !isMatchedSpool;

  // For unknown tags, use live weight or stored displayed weight
  const useScaleWeight = currentWeight !== null &&
    (currentTagId === displayedTagId || (currentTagId === null && displayedTagId !== null));
  const liveWeight = useScaleWeight ? currentWeight : null;

  // Stats
  const totalSpools = spools.length;
  const materials = new Set(spools.map((s) => s.material)).size;
  const brands = new Set(spools.filter((s) => s.brand).map((s) => s.brand)).size;

  return (
    <div className="h-full flex flex-col p-4">
      {/* Compact stats bar */}
      <div className="flex items-center gap-6 px-4 py-1.5 bg-zinc-800/50 rounded-xl border border-zinc-700/50 mb-3 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xl font-bold text-zinc-100">{totalSpools}</span>
          <span className="text-sm text-zinc-500">{t('spoolbuddy.inventory.spools', 'Spools')}</span>
        </div>
        <div className="w-px h-5 bg-zinc-700" />
        <div className="flex items-center gap-2">
          <span className="text-xl font-bold text-zinc-100">{materials}</span>
          <span className="text-sm text-zinc-500">{t('spoolbuddy.spool.material', 'Materials')}</span>
        </div>
        <div className="w-px h-5 bg-zinc-700" />
        <div className="flex items-center gap-2">
          <span className="text-xl font-bold text-zinc-100">{brands}</span>
          <span className="text-sm text-zinc-500">{t('spoolbuddy.spool.brand', 'Brands')}</span>
        </div>
      </div>

      {/* Main content: Device (left) + Current Spool (right) */}
      <div className="flex-1 flex gap-4 min-h-0">
        {/* Left column */}
        <div className="w-5/12 flex flex-col min-h-0">
          {/* Device card */}
          <div className="border border-dashed border-zinc-700/50 rounded-xl p-4">
            <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wide mb-3">
              {t('spoolbuddy.dashboard.device', 'Device')}
            </h2>

            <div className="space-y-2.5">
              {/* Connection status */}
              <div className="flex items-center gap-3">
                <div className={`w-2.5 h-2.5 rounded-full ${sbState.deviceOnline ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`} />
                <span className="text-base text-zinc-400">
                  {sbState.deviceOnline ? t('spoolbuddy.status.online', 'Online') : t('spoolbuddy.status.offline', 'Disconnected')}
                </span>
              </div>

              {/* Scale weight */}
              <div className="flex items-center justify-between p-3 bg-zinc-800/50 rounded-lg">
                <div className="flex items-center gap-2">
                  <svg className={`w-4 h-4 ${sbState.deviceOnline ? 'text-green-500' : 'text-zinc-500'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5m0 16H9m3 0h3" />
                  </svg>
                  <span className="text-sm text-zinc-500">{t('spoolbuddy.spool.scaleWeight', 'Scale')}</span>
                </div>
                <span className="text-lg font-mono font-semibold text-zinc-100">
                  {scaleDisplayValue !== null ? `${Math.abs(scaleDisplayValue) <= 20 ? 0 : Math.round(Math.max(0, scaleDisplayValue))}g` : '\u2014'}
                </span>
              </div>

              {/* NFC status */}
              <div className="flex items-center justify-between p-3 bg-zinc-800/50 rounded-lg">
                <div className="flex items-center gap-2">
                  <svg className={`w-4 h-4 ${sbState.deviceOnline ? 'text-green-500' : 'text-zinc-500'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z" />
                  </svg>
                  <span className="text-sm text-zinc-500">NFC</span>
                </div>
                <span className={`text-sm font-medium ${currentTagId ? 'text-green-500' : 'text-zinc-500'}`}>
                  {currentTagId ? t('spoolbuddy.dashboard.tagDetected', 'Tag detected') : t('spoolbuddy.dashboard.noTag', 'No tag')}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Right column: Current Spool */}
        <div className="w-7/12 min-h-0">
          <div className="border border-dashed border-zinc-700/50 rounded-xl p-6 h-full flex flex-col">
            <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wide mb-4 shrink-0">
              {t('spoolbuddy.dashboard.currentSpool', 'Current Spool')}
            </h2>
            <div className="flex-1 flex items-center justify-center min-h-0">
              {showCard && isMatchedSpool && displayedSpool ? (
                <SpoolInfoCard
                  spool={{
                    id: displayedSpool.id,
                    tag_uid: displayedTagId!,
                    material: displayedSpool.material,
                    subtype: displayedSpool.subtype,
                    color_name: displayedSpool.color_name,
                    rgba: displayedSpool.rgba,
                    brand: displayedSpool.brand,
                    label_weight: displayedSpool.label_weight,
                    core_weight: displayedSpool.core_weight,
                    weight_used: displayedSpool.weight_used,
                  }}
                  scaleWeight={liveWeight}
                  weightStable={weightStable}
                  onClose={handleCloseSpoolCard}
                  onSyncWeight={() => refetchSpools()}
                />
              ) : showCard && isUnknownTag ? (
                <UnknownTagCard
                  tagUid={displayedTagId!}
                  scaleWeight={liveWeight ?? (displayedWeight !== null ? displayedWeight : null)}
                  onLinkSpool={untaggedSpools.length > 0 ? () => setShowLinkModal(true) : undefined}
                  onClose={handleCloseSpoolCard}
                />
              ) : (
                sbState.deviceOnline ? <ColorCyclingSpool /> : <DeviceOfflineState />
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Link Tag to Spool Modal */}
      {displayedTagId && (
        <LinkSpoolModal
          isOpen={showLinkModal}
          onClose={() => setShowLinkModal(false)}
          tagId={displayedTagId}
          untaggedSpools={untaggedSpools}
          onLink={handleLinkTagToSpool}
        />
      )}
    </div>
  );
}
