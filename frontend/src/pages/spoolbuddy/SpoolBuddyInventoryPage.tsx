import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useOutletContext } from 'react-router-dom';
import { Search, X, Package } from 'lucide-react';
import { api } from '../../api/client';
import type { InventorySpool, SpoolAssignment } from '../../api/client';
import { resolveSpoolColorName } from '../../utils/colors';
import { formatSlotLabel } from '../../utils/amsHelpers';
import { InventorySpoolInfoCard } from '../../components/spoolbuddy/InventorySpoolInfoCard';
import { AssignToAmsModal } from '../../components/spoolbuddy/AssignToAmsModal';
import type { SpoolBuddyOutletContext } from '../../components/spoolbuddy/SpoolBuddyLayout';

type FilterMode = 'all' | 'in_ams' | string; // string = material name

function spoolColor(spool: InventorySpool): string {
  if (spool.rgba) return `#${spool.rgba.substring(0, 6)}`;
  return '#808080';
}

function spoolRemaining(spool: InventorySpool): number {
  return Math.max(0, spool.label_weight - spool.weight_used);
}

function spoolPct(spool: InventorySpool): number {
  if (spool.label_weight <= 0) return 0;
  return Math.max(0, Math.min(100, ((spool.label_weight - spool.weight_used) / spool.label_weight) * 100));
}

function spoolDisplayName(spool: InventorySpool): string {
  const parts = [spool.material];
  if (spool.subtype) parts.push(spool.subtype);
  return parts.join(' ');
}

function assignmentLabel(a: SpoolAssignment): string {
  const isExternal = a.ams_id === 254 || a.ams_id === 255;
  const isHt = !isExternal && a.ams_id >= 128;
  return formatSlotLabel(a.ams_id, a.tray_id, isHt, isExternal);
}

/* Spool circle — same style as AMS page tray slots */
function SpoolCircle({ color, size = 56 }: { color: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 56 56">
      <circle cx="28" cy="28" r="26" fill={color} />
      <circle cx="28" cy="28" r="20" fill={color} style={{ filter: 'brightness(0.85)' }} />
      <ellipse cx="20" cy="20" rx="6" ry="4" fill="white" opacity="0.3" />
      <circle cx="28" cy="28" r="8" fill="#2d2d2d" />
      <circle cx="28" cy="28" r="5" fill="#1a1a1a" />
    </svg>
  );
}

export function SpoolBuddyInventoryPage() {
  const { sbState, selectedPrinterId } = useOutletContext<SpoolBuddyOutletContext>();
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');
  const [selectedSpoolId, setSelectedSpoolId] = useState<number | null>(null);
  const [showAssignAmsModal, setShowAssignAmsModal] = useState(false);

  const { data: spoolmanSettings } = useQuery({
    queryKey: ['spoolman-settings'],
    queryFn: api.getSpoolmanSettings,
    staleTime: 5 * 60 * 1000,
  });

  const spoolmanMode = spoolmanSettings?.spoolman_enabled === 'true' && !!spoolmanSettings?.spoolman_url;

  const { data: spools = [], isLoading, refetch: refetchSpools } = useQuery({
    queryKey: spoolmanMode ? ['spoolman-inventory-spools'] : ['inventory-spools'],
    queryFn: () => spoolmanMode ? api.getSpoolmanInventorySpools(false) : api.getSpools(false),
    enabled: spoolmanSettings !== undefined,
    refetchInterval: 30000,
  });

  const { data: assignments = [] } = useQuery({
    queryKey: ['spool-assignments'],
    queryFn: () => api.getAssignments(),
    refetchInterval: 30000,
  });

  // Build assignment lookup: spool_id → assignment
  const assignmentMap = useMemo(() => {
    const map: Record<number, SpoolAssignment> = {};
    assignments.forEach(a => { map[a.spool_id] = a; });
    return map;
  }, [assignments]);

  const activeSpools = useMemo(() => spools.filter(s => !s.archived_at), [spools]);

  // Spools that have an AMS assignment
  const assignedSpoolIds = useMemo(() => new Set(assignments.map(a => a.spool_id)), [assignments]);
  const inAmsCount = useMemo(() => activeSpools.filter(s => assignedSpoolIds.has(s.id)).length, [activeSpools, assignedSpoolIds]);

  // Unique materials for filter pills
  const materials = useMemo(() => {
    const set = new Set<string>();
    activeSpools.forEach(s => set.add(s.material));
    return Array.from(set).sort();
  }, [activeSpools]);

  // Filter and sort
  const filteredSpools = useMemo(() => {
    let list = activeSpools;

    if (filterMode === 'in_ams') {
      list = list.filter(s => assignedSpoolIds.has(s.id));
    } else if (filterMode !== 'all') {
      list = list.filter(s => s.material === filterMode);
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      list = list.filter(s =>
        s.material.toLowerCase().includes(q) ||
        (s.subtype && s.subtype.toLowerCase().includes(q)) ||
        (s.brand && s.brand.toLowerCase().includes(q)) ||
        (s.color_name && s.color_name.toLowerCase().includes(q)) ||
        (s.note && s.note.toLowerCase().includes(q))
      );
    }

    // Sort: assigned spools first (by slot label), then by most recently updated
    return [...list].sort((a, b) => {
      const aAssigned = assignedSpoolIds.has(a.id) ? 0 : 1;
      const bAssigned = assignedSpoolIds.has(b.id) ? 0 : 1;
      if (aAssigned !== bAssigned) return aAssigned - bAssigned;
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
    });
  }, [activeSpools, filterMode, searchQuery, assignedSpoolIds]);

  return (
    <div className="h-full flex flex-col">
      {/* Search + filter pills */}
      <div className="px-3 pt-3 pb-2 space-y-2.5">
        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/40" />
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder={t('spoolbuddy.inventory.searchPlaceholder', 'Search spools...')}
            className="w-full pl-9 pr-8 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-sm text-white placeholder-white/30 focus:outline-none focus:border-bambu-green"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-white/40 hover:text-white/60"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Filter pills — inline scrollable row */}
        <div className="flex gap-1.5 overflow-x-auto no-scrollbar">
          <FilterPill
            active={filterMode === 'all'}
            onClick={() => setFilterMode('all')}
            label={`${t('spoolbuddy.inventory.all', 'All')} (${activeSpools.length})`}
            green
          />
          {inAmsCount > 0 && (
            <FilterPill
              active={filterMode === 'in_ams'}
              onClick={() => setFilterMode('in_ams')}
              label={`${t('spoolbuddy.inventory.inAms', 'In AMS')} (${inAmsCount})`}
            />
          )}
          {materials.map(mat => (
            <FilterPill
              key={mat}
              active={filterMode === mat}
              onClick={() => setFilterMode(filterMode === mat ? 'all' : mat)}
              label={mat}
            />
          ))}
        </div>
      </div>

      {/* Spool grid */}
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-8 h-8 border-2 border-bambu-green border-t-transparent rounded-full animate-spin" />
          </div>
        ) : filteredSpools.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-white/30">
            <Package className="w-12 h-12 mb-3" />
            <p className="text-sm">
              {searchQuery || filterMode !== 'all'
                ? t('spoolbuddy.inventory.noResults', 'No spools match your filters')
                : t('spoolbuddy.inventory.empty', 'No spools in inventory')}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-2">
            {filteredSpools.map(spool => (
              <CatalogCard
                key={spool.id}
                spool={spool}
                assignment={assignmentMap[spool.id]}
                onClick={() => setSelectedSpoolId(spool.id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Detail modal — look up spool from live query data so it stays current */}
      {selectedSpoolId != null && (() => {
        const liveSpool = spools.find(s => s.id === selectedSpoolId);
        if (!liveSpool) return null;
        const handleCloseDetail = () => {
          setSelectedSpoolId(null);
          setShowAssignAmsModal(false);
        };
        return (
          <>
            <SpoolDetailModal
              spool={liveSpool}
              assignment={assignmentMap[liveSpool.id]}
              sbState={sbState}
              onSyncWeight={() => {
                void refetchSpools();
              }}
              onAssignToAms={() => setShowAssignAmsModal(true)}
              onClose={handleCloseDetail}
            />
            <AssignToAmsModal
              isOpen={showAssignAmsModal}
              onClose={() => setShowAssignAmsModal(false)}
              spool={liveSpool}
              printerId={selectedPrinterId}
            />
          </>
        );
      })()}
    </div>
  );
}

/* Filter pill button */
function FilterPill({ active, onClick, label, green }: {
  active: boolean;
  onClick: () => void;
  label: string;
  green?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-1.5 rounded-full text-sm font-medium border whitespace-nowrap shrink-0 transition-colors ${
        active
          ? green
            ? 'bg-bambu-green/20 text-bambu-green border-bambu-green/50'
            : 'bg-white/10 text-white border-white/20'
          : 'bg-transparent text-white/40 border-bambu-dark-tertiary hover:text-white/60'
      }`}
    >
      {label}
    </button>
  );
}

/* Catalog-style spool card matching the mockup */
function CatalogCard({ spool, assignment, onClick }: {
  spool: InventorySpool;
  assignment?: SpoolAssignment;
  onClick: () => void;
}) {
  const color = spoolColor(spool);
  const pct = spoolPct(spool);
  const remaining = spoolRemaining(spool);
  const colorName = resolveSpoolColorName(spool.color_name, spool.rgba);

  return (
    <button
      onClick={onClick}
      className="bg-bambu-dark-secondary rounded-xl p-3 flex flex-col items-center text-center gap-1.5 border border-transparent hover:border-bambu-green/50 transition-colors"
    >
      {/* Spool icon */}
      <SpoolCircle color={color} size={56} />

      {/* Material + Subtype */}
      <p className="text-xs font-semibold text-white leading-tight truncate w-full">
        {spoolDisplayName(spool)}
      </p>

      {/* Color dot + name */}
      <div className="flex items-center gap-1 min-w-0 max-w-full">
        <span
          className="w-2.5 h-2.5 rounded-full shrink-0 border border-white/10"
          style={{ backgroundColor: color }}
        />
        <span className="text-[11px] text-white/50 truncate">
          {colorName || '-'}
        </span>
      </div>

      {/* Fill bar + weight */}
      <div className="w-full space-y-0.5">
        <div className="h-1.5 bg-bambu-dark-tertiary rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full ${pct > 50 ? 'bg-bambu-green' : pct > 20 ? 'bg-yellow-500' : 'bg-red-500'}`}
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
        <p className="text-[11px] text-white/40">
          {Math.round(remaining)}g ({Math.round(pct)}%)
        </p>
      </div>

      {/* AMS location badge */}
      {assignment && (
        <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-bambu-green/20 text-bambu-green">
          {assignmentLabel(assignment)}
        </span>
      )}
    </button>
  );
}

/* Detail bottom sheet */
function SpoolDetailModal({ spool, assignment, sbState, onSyncWeight, onAssignToAms, onClose }: {
  spool: InventorySpool;
  assignment?: SpoolAssignment;
  sbState: SpoolBuddyOutletContext['sbState'];
  onSyncWeight: () => void;
  onAssignToAms: () => void;
  onClose: () => void;
}) {
  const useLiveScaleWeight = sbState.deviceOnline && sbState.weight !== null;
  const modalScaleWeight = useLiveScaleWeight
    ? Math.round(sbState.weight as number)
    : null;
  const persistedGrossWeight = spool.last_scale_weight != null ? Math.round(spool.last_scale_weight) : null;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="w-full max-w-md bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-2xl p-4 overflow-y-auto max-h-[90vh]"
        onClick={e => e.stopPropagation()}
      >
        <div className="space-y-3">
          {assignment && (
            <div className="flex items-center justify-center gap-2">
              <span className="px-2.5 py-1 rounded-md text-xs font-bold bg-bambu-green/20 text-bambu-green">
                {assignmentLabel(assignment)}
              </span>
              {assignment.printer_name && (
                <span className="text-xs text-zinc-400">{assignment.printer_name}</span>
              )}
            </div>
          )}

          <div className="flex justify-center">
            <InventorySpoolInfoCard
              spool={spool}
              liveScaleWeight={modalScaleWeight}
              persistedGrossWeight={persistedGrossWeight}
              onSyncWeight={onSyncWeight}
              onAssignToAms={onAssignToAms}
              onClose={onClose}
              className="max-w-md"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
