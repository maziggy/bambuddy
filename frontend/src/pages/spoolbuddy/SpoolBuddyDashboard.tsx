import { useState, useEffect, useMemo, useRef } from 'react';
import { useOutletContext } from 'react-router-dom';
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { SpoolBuddyOutletContext } from '../../components/spoolbuddy/SpoolBuddyLayout';
import { api, type InventorySpool, type Printer, type PrinterStatus } from '../../api/client';
import type { MatchedSpool } from '../../hooks/useSpoolBuddyState';
import { useToast } from '../../contexts/ToastContext';
import { SpoolIcon } from '../../components/spoolbuddy/SpoolIcon';
import { SpoolInfoCard, UnknownTagCard } from '../../components/spoolbuddy/SpoolInfoCard';
import { AssignToAmsModal } from '../../components/spoolbuddy/AssignToAmsModal';
import { LinkSpoolModal } from '../../components/spoolbuddy/LinkSpoolModal';

function normalizeHexTag(value: string | null | undefined): string {
  if (!value) return '';
  return value.replace(/[^0-9a-f]/gi, '').toUpperCase();
}

function tagsEquivalent(a: string | null | undefined, b: string | null | undefined): boolean {
  const aNorm = normalizeHexTag(a);
  const bNorm = normalizeHexTag(b);
  if (!aNorm || !bNorm) return false;
  if (aNorm === bNorm) return true;
  // Some readers report shortened UID forms.
  return aNorm.endsWith(bNorm) || bNorm.endsWith(aNorm);
}

// Color palette for the cycling spool animation
const SPOOL_COLORS = [
  '#00AE42', '#FF6B35', '#3B82F6', '#EF4444', '#A855F7',
  '#FBBF24', '#14B8A6', '#EC4899', '#F97316', '#22C55E',
];

// --- Idle state with slow color-cycling spool ---
function IdleSpool() {
  const { t } = useTranslation();
  const [colorIndex, setColorIndex] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setColorIndex((prev) => (prev + 1) % SPOOL_COLORS.length);
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  const currentColor = SPOOL_COLORS[colorIndex];

  return (
    <div className="flex flex-col items-center text-center">
      {/* Animated spool with optimized NFC waves */}
      <div className="relative mb-6 flex items-center justify-center" style={{ width: 160, height: 160 }}>
        {/* NFC wave rings: transform + opacity only for Pi-friendly rendering */}
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          {[0, 1].map((i) => (
            <div
              key={i}
              className="absolute rounded-full border spoolbuddy-optimized-ping"
              style={{
                width: 80,
                height: 80,
                borderColor: `${currentColor}4D`,
                transition: 'border-color 140ms linear',
                animationDelay: `${i * 0.8}s`,
              }}
            />
          ))}
        </div>

        {/* Spool icon with lightweight radial glow */}
        <div className="relative overflow-hidden rounded-full">
          <div
            className="absolute inset-0 rounded-full opacity-30 spoolbuddy-spool-glow"
            style={{
              background: `radial-gradient(circle, ${currentColor} 0%, transparent 70%)`,
            }}
          />
          <div className="relative" style={{ transition: 'opacity 140ms linear' }}>
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
  const { sbState, selectedPrinterId } = useOutletContext<SpoolBuddyOutletContext>();
  const { t } = useTranslation();
  const { showToast } = useToast();

  const { data: spoolmanSettings } = useQuery({
    queryKey: ['spoolman-settings'],
    queryFn: api.getSpoolmanSettings,
    staleTime: 5 * 60 * 1000,
  });
  const spoolmanMode = spoolmanSettings?.spoolman_enabled === 'true' && !!spoolmanSettings?.spoolman_url;

  // Fetch spools for stats, tag lookup, and untagged list
  const { data: spools = [], refetch: refetchSpools } = useQuery({
    queryKey: spoolmanMode ? ['spoolman-inventory-spools'] : ['inventory-spools'],
    queryFn: () => spoolmanMode ? api.getSpoolmanInventorySpools(false) : api.getSpools(false),
    enabled: spoolmanSettings !== undefined,
  });

  // Fetch printers and their statuses for the status badges
  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
  });

  const statusQueries = useQueries({
    queries: printers.map((printer: Printer) => ({
      queryKey: ['printerStatus', printer.id],
      queryFn: () => api.getPrinterStatus(printer.id),
      refetchInterval: 10000,
      select: (data: PrinterStatus) => ({
        connected: data?.connected,
        awaiting_plate_clear: data?.awaiting_plate_clear === true,
      }),
    })),
  });

  // Plate-clear: collect printers that are waiting for the operator to confirm.
  // The kiosk's API key passes the printers:clear_plate gate (not in the
  // _APIKEY_DENIED_PERMISSIONS set), so no extra perm wiring is needed here.
  const platesPending = printers
    .map((printer: Printer, i: number) => ({
      printer,
      pending: statusQueries[i]?.data?.awaiting_plate_clear === true,
    }))
    .filter((row: { pending: boolean }) => row.pending);

  const queryClient = useQueryClient();
  const clearPlateMutation = useMutation({
    mutationFn: (printerId: number) => api.clearPlate(printerId),
    onSuccess: (_data, printerId) => {
      // Optimistically clear the flag so the row vanishes immediately; the
      // backend already broadcasts a printer_status WS event after clearing,
      // but we don't want the user to see the row linger while that round-trips.
      queryClient.setQueryData(['printerStatus', printerId], (old: PrinterStatus | undefined) =>
        old ? { ...old, awaiting_plate_clear: false } : old
      );
      showToast(t('spoolbuddy.dashboard.plateClearedToast', 'Plate marked as cleared'), 'success');
    },
    onError: () => {
      showToast(t('spoolbuddy.dashboard.plateClearFailed', 'Could not mark plate as cleared'), 'error');
    },
  });

  // Current Spool card state - persists until user closes or new tag detected
  const [displayedTagId, setDisplayedTagId] = useState<string | null>(null);
  const [displayedWeight, setDisplayedWeight] = useState<number | null>(null);
  const [hiddenTagId, setHiddenTagId] = useState<string | null>(null);
  const [showLinkModal, setShowLinkModal] = useState(false);
  const [showAssignAmsModal, setShowAssignAmsModal] = useState(false);
  const [showQuickAddModal, setShowQuickAddModal] = useState(false);
  const [quickAddBusy, setQuickAddBusy] = useState(false);
  const [justLinkedSpool, setJustLinkedSpool] = useState<Omit<MatchedSpool, 'tag_uid'> | null>(null);

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
  const displayedSpool = useMemo((): InventorySpool | null => {
    if (sbState.matchedSpool?.id) {
      const byId = spools.find((s) => s.id === sbState.matchedSpool?.id);
      if (byId) return byId;
    }
    if (!displayedTagId) return null;
    const byTag = spools.find((s) => tagsEquivalent(s.tag_uid, displayedTagId));
    if (byTag) return byTag;
    // When a Bambu tray UUID (32-char) is linked, Spoolman stores it in extra.tag and
    // _map_spoolman_spool routes it to tray_uuid, not tag_uid. tagsEquivalent only
    // compares tag_uid, so it misses this spool until the device re-scans and
    // sbState.matchedSpool is populated. Hold the spool returned by the link call
    // as a temporary bridge. Cast is safe: SpoolInfoCard only reads the 9 fields
    // present in MatchedSpool; AssignToAmsModal is guarded by !justLinkedSpool below.
    if (justLinkedSpool) return justLinkedSpool as unknown as InventorySpool;
    return null;
  }, [displayedTagId, sbState.matchedSpool, spools, justLinkedSpool]);

  // Untagged spools for the Link feature
  const untaggedSpools = useMemo(() => {
    return spoolmanMode
      ? spools.filter((s) => !s.tag_uid && !s.tray_uuid && !s.archived_at)
      : spools.filter((s) => !s.tag_uid && !s.archived_at);
  }, [spools, spoolmanMode]);

  // Handle tag detection - show card when tag detected, keep until user closes or new tag
  useEffect(() => {
    if (currentTagId) {
      const isHidden = hiddenTagId === currentTagId;
      const isDifferentTag = displayedTagId !== null && displayedTagId !== currentTagId;

      if (isDifferentTag || (!isHidden && displayedTagId !== currentTagId)) {
        setDisplayedTagId(currentTagId);
        setDisplayedWeight(null);
        setHiddenTagId(null);
        setJustLinkedSpool(null);
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
        setJustLinkedSpool(null);
      }
    }
  }, [currentTagId, currentWeight, weightStable, displayedTagId, hiddenTagId]);

  // Auto-sync weight once when known spool first detected

  const handleCloseSpoolCard = () => {
    setHiddenTagId(displayedTagId);
  };

  const handleLinkTagToSpool = async (spool: InventorySpool) => {
    if (!displayedTagId) return;
    try {
      if (spoolmanMode) {
        const tag_uid = sbState.unknownTagUid || undefined;
        const tray_uuid = (!sbState.unknownTagUid && sbState.unknownTrayUuid) ? sbState.unknownTrayUuid : undefined;
        if (!tag_uid && !tray_uuid) {
          showToast(t('spoolman.linkFailed'), 'error');
          return;
        }
        const raw = await api.linkTagToSpoolmanSpool(spool.id, { tray_uuid, tag_uid });
        const updated = raw as InventorySpool | undefined;
        if (!updated) {
          showToast(t('spoolman.linkFailed'), 'error');
          return;
        }
        const { id, material, subtype, color_name, rgba, brand, label_weight, core_weight, weight_used } = updated;
        setJustLinkedSpool({ id, material, subtype, color_name, rgba, brand, label_weight, core_weight, weight_used });
        showToast(t('spoolman.linkSuccess'), 'success');
      } else {
        await api.linkTagToSpool(spool.id, {
          tag_uid: displayedTagId,
          tag_type: 'generic',
          data_origin: 'nfc_link',
        });
      }
      refetchSpools();
    } catch (e) {
      console.error('Failed to link tag:', e);
      showToast(t('spoolman.linkFailed'), 'error');
    } finally {
      setShowLinkModal(false);
    }
  };

  const handleQuickAddToInventory = async () => {
    if (!displayedTagId) return;
    setQuickAddBusy(true);
    try {
      const weight = liveWeight ?? displayedWeight;
      if (spoolmanMode) {
        const created = await api.createSpoolmanInventorySpool({
          material: 'PLA',
          subtype: null,
          color_name: null,
          rgba: null,
          brand: null,
          label_weight: 1000,
          core_weight: 250,
          core_weight_catalog_id: null,
          weight_used: 0,
          slicer_filament: null,
          slicer_filament_name: null,
          nozzle_temp_min: null,
          nozzle_temp_max: null,
          note: null,
          added_full: null,
          last_used: null,
          encode_time: null,
          tag_uid: null,
          tray_uuid: null,
          data_origin: null,
          tag_type: null,
          cost_per_kg: null,
          last_scale_weight: weight !== null ? Math.round(weight) : null,
          last_weighed_at: weight !== null ? new Date().toISOString() : null,
          category: null,
          low_stock_threshold_pct: null,
        });
        await api.linkTagToSpoolmanSpool(created.id, {
          tag_uid: sbState.unknownTagUid || undefined,
          tray_uuid: (!sbState.unknownTagUid && sbState.unknownTrayUuid) ? sbState.unknownTrayUuid : undefined,
        });
      } else {
        await api.createSpool({
          material: 'PLA',
          subtype: null,
          color_name: null,
          rgba: null,
          brand: null,
          label_weight: 1000,
          core_weight: 250,
          core_weight_catalog_id: null,
          weight_used: 0,
          slicer_filament: null,
          slicer_filament_name: null,
          nozzle_temp_min: null,
          nozzle_temp_max: null,
          note: null,
          added_full: null,
          last_used: null,
          encode_time: null,
          tag_uid: displayedTagId,
          tray_uuid: null,
          data_origin: 'spoolbuddy',
          tag_type: 'generic',
          cost_per_kg: null,
          last_scale_weight: weight !== null ? Math.round(weight) : null,
          last_weighed_at: weight !== null ? new Date().toISOString() : null,
          category: null,
          low_stock_threshold_pct: null,
        });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error('Failed to quick-add spool:', msg);
      showToast(msg || t('spoolbuddy.errors.quickAddFailed', 'Failed to add spool'), 'error');
    } finally {
      setShowQuickAddModal(false);
      setQuickAddBusy(false);
      refetchSpools();
    }
  };

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
                <div className={`w-2.5 h-2.5 rounded-full ${sbState.deviceOnline ? 'bg-green-500' : 'bg-red-500'}`} />
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

          {/* Printer status badges */}
          {printers.length > 0 && (
            <div className="mt-3 border border-dashed border-zinc-700/50 rounded-xl p-4">
              <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wide mb-2.5">
                {t('spoolbuddy.dashboard.printers', 'Printers')}
              </h2>
              <div className="flex flex-wrap gap-2 overflow-hidden">
                {printers.map((printer: Printer, i: number) => {
                  const isOnline = statusQueries[i]?.data?.connected ?? false;
                  return (
                    <div
                      key={printer.id}
                      className="flex items-center gap-1.5 px-2.5 py-1 bg-zinc-800/50 rounded-lg"
                      title={`${printer.name} — ${isOnline ? 'Online' : 'Offline'}`}
                    >
                      <div className={`w-2 h-2 rounded-full shrink-0 ${isOnline ? 'bg-green-500' : 'bg-zinc-600'}`} />
                      <span className="text-xs text-zinc-400 truncate max-w-[100px]">{printer.name}</span>
                    </div>
                  );
                })}
              </div>

              {/* Plate-ready pills — same compact size as the printer badges above so
                  the row stays scannable when multiple printers finish at once.
                  Wraps via flex-wrap. Each pill is independently tappable. */}
              {platesPending.length > 0 && (
                <div
                  className="mt-2 flex flex-wrap gap-2"
                  data-testid="plate-clear-section"
                  aria-label={t('spoolbuddy.dashboard.plateReadyLabel', 'Plates ready to clear')}
                >
                  {platesPending.map(({ printer }: { printer: Printer }) => (
                    <button
                      key={printer.id}
                      type="button"
                      onClick={() => clearPlateMutation.mutate(printer.id)}
                      disabled={clearPlateMutation.isPending}
                      data-testid={`plate-clear-button-${printer.id}`}
                      title={t('spoolbuddy.dashboard.plateReady', 'Plate ready: {{name}}', { name: printer.name })}
                      className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 active:bg-amber-500/30 border border-amber-500/30 text-amber-200 transition-colors disabled:opacity-60 disabled:cursor-wait"
                    >
                      <svg className="w-3 h-3 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M12 9v4" />
                        <path d="M12 17h.01" />
                        <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                      </svg>
                      <span className="text-xs truncate max-w-[100px]">{printer.name}</span>
                      <span className="text-xs opacity-70" aria-hidden="true">·</span>
                      <span className="text-xs font-medium">
                        {t('spoolbuddy.dashboard.plateClearAction', 'Clear')}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right column: Current Spool */}
        <div className="w-7/12 min-h-0">
          <div className="border border-dashed border-zinc-700/50 rounded-xl p-6 h-full flex flex-col">
            <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wide mb-4 shrink-0">
              {t('spoolbuddy.dashboard.currentSpool', 'Current Spool')}
            </h2>
            <div className="flex-1 flex items-center justify-center min-h-0">
              {!sbState.deviceOnline ? (
                <DeviceOfflineState />
              ) : (displayedSpool || sbState.matchedSpool) && displayedTagId && hiddenTagId !== displayedTagId ? (
                <SpoolInfoCard
                  spool={(() => {
                    const s = displayedSpool ?? sbState.matchedSpool!;
                    return {
                      id: s.id,
                      tag_uid: displayedTagId,
                      material: s.material,
                      subtype: s.subtype,
                      color_name: s.color_name,
                      rgba: s.rgba,
                      brand: s.brand,
                      label_weight: s.label_weight,
                      core_weight: s.core_weight,
                      weight_used: s.weight_used,
                    };
                  })()}
                  scaleWeight={liveWeight ?? displayedWeight}
                  onSyncWeight={() => refetchSpools()}
                  onAssignToAms={() => setShowAssignAmsModal(true)}
                  onClose={handleCloseSpoolCard}
                />
              ) : currentTagId && displayedTagId && !displayedSpool && !sbState.matchedSpool && hiddenTagId !== displayedTagId ? (
                <UnknownTagCard
                  tagUid={displayedTagId}
                  scaleWeight={liveWeight ?? displayedWeight}
                  onLinkSpool={untaggedSpools.length > 0 ? () => setShowLinkModal(true) : undefined}
                  onAddToInventory={() => setShowQuickAddModal(true)}
                  onClose={handleCloseSpoolCard}
                />
              ) : (
                <IdleSpool />
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Assign to AMS Modal — only when displayedSpool is a real InventorySpool,
          not the justLinkedSpool bridge (which only has the 9 MatchedSpool fields). */}
      {displayedSpool && !justLinkedSpool && displayedTagId && (
        <AssignToAmsModal
          isOpen={showAssignAmsModal}
          onClose={() => setShowAssignAmsModal(false)}
          spool={displayedSpool}
          printerId={selectedPrinterId}
          spoolmanMode={spoolmanMode}
        />
      )}

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

      {/* Quick-add to Inventory Modal */}
      {showQuickAddModal && displayedTagId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-zinc-800 rounded-2xl p-6 mx-4 max-w-sm w-full border border-zinc-700">
            <h3 className="text-lg font-semibold text-zinc-100 mb-3">
              {t('spoolbuddy.modal.addToInventory', 'Add to Inventory')}
            </h3>

            {/* Hint */}
            <div className="flex gap-2.5 p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg mb-4">
              <svg className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <p className="text-sm text-amber-200/80">
                {t('spoolbuddy.modal.quickAddHint', 'For best results, add the spool in the Bambuddy web interface first (with material, color, brand), then use "Link to Spool" here to assign the NFC tag.')}
              </p>
            </div>

            <p className="text-sm text-zinc-400 mb-1">
              {t('spoolbuddy.modal.quickAddDesc', 'This will create a basic PLA spool entry with this NFC tag. You can edit the details later in Bambuddy.')}
            </p>
            <p className="text-xs text-zinc-500 font-mono mb-5">{displayedTagId}</p>

            <div className="flex gap-3">
              <button
                onClick={() => setShowQuickAddModal(false)}
                className="flex-1 px-4 py-2.5 rounded-lg text-sm font-medium bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors min-h-[44px]"
              >
                {t('common.cancel', 'Cancel')}
              </button>
              <button
                onClick={handleQuickAddToInventory}
                disabled={quickAddBusy}
                className="flex-1 px-4 py-2.5 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 transition-colors min-h-[44px]"
              >
                {quickAddBusy ? t('common.saving', 'Saving...') : t('spoolbuddy.modal.addAnyway', 'Add Anyway')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
