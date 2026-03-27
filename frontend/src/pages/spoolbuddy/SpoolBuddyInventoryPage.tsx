import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Search, X, Package } from 'lucide-react';
import { api } from '../../api/client';
import type { InventorySpool, SpoolAssignment } from '../../api/client';
import { resolveSpoolColorName } from '../../utils/colors';
import { formatSlotLabel } from '../../utils/amsHelpers';

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
  const { t } = useTranslation();
  const [searchQuery, setSearchQuery] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');
  const [selectedSpoolId, setSelectedSpoolId] = useState<number | null>(null);

  const { data: spoolmanSettings } = useQuery({
    queryKey: ['spoolman-settings'],
    queryFn: api.getSpoolmanSettings,
    staleTime: 5 * 60 * 1000,
  });

  const { data: spools = [], isLoading } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(false),
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

  // Spoolman iframe mode
  const spoolmanEnabled = spoolmanSettings?.spoolman_enabled === 'true' && spoolmanSettings?.spoolman_url;
  if (spoolmanEnabled) {
    return (
      <div className="h-full flex flex-col">
        <iframe
          src={`${spoolmanSettings.spoolman_url.replace(/\/+$/, '')}/spool`}
          className="flex-1 w-full border-0"
          title="Spoolman"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
        />
      </div>
    );
  }

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
        return (
          <SpoolDetailModal
            spool={liveSpool}
            assignment={assignmentMap[liveSpool.id]}
            onClose={() => setSelectedSpoolId(null)}
          />
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
function SpoolDetailModal({ spool, assignment, onClose }: {
  spool: InventorySpool;
  assignment?: SpoolAssignment;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const color = spoolColor(spool);
  const pct = spoolPct(spool);
  const remaining = spoolRemaining(spool);
  const colorName = resolveSpoolColorName(spool.color_name, spool.rgba);

  return (
    <div className="fixed inset-0 z-50" onClick={onClose}>
      <div
        className="h-full w-full bg-bambu-dark overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        {/* Header with spool icon */}
        <div className="flex items-center gap-4 p-4 pb-3">
          <SpoolCircle color={color} size={72} />
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-white">
              {spoolDisplayName(spool)}
            </h2>
            {spool.brand && (
              <p className="text-sm text-white/50">{spool.brand}</p>
            )}
            <div className="flex items-center gap-1.5 mt-1">
              <span
                className="w-3 h-3 rounded-full border border-white/10"
                style={{ backgroundColor: color }}
              />
              <span className="text-sm text-white/60">
                {colorName || '-'}
              </span>
            </div>
          </div>
        </div>

        <div className="px-4 pb-4 space-y-4">
          {/* Remaining bar */}
          <div>
            <div className="flex justify-between text-xs text-white/50 mb-1.5">
              <span>{t('spoolbuddy.inventory.remaining', 'Remaining')}</span>
              <span>{Math.round(remaining)}g ({Math.round(pct)}%)</span>
            </div>
            <div className="h-3 bg-bambu-dark-secondary rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${pct > 50 ? 'bg-bambu-green' : pct > 20 ? 'bg-yellow-500' : 'bg-red-500'}`}
                style={{ width: `${Math.min(pct, 100)}%` }}
              />
            </div>
          </div>

          {/* AMS location */}
          {assignment && (
            <div className="flex items-center gap-2">
              <span className="px-2.5 py-1 rounded-md text-xs font-bold bg-bambu-green/20 text-bambu-green">
                {assignmentLabel(assignment)}
              </span>
              {assignment.printer_name && (
                <span className="text-xs text-white/40">{assignment.printer_name}</span>
              )}
            </div>
          )}

          {/* Detail grid */}
          <div className="grid grid-cols-2 gap-2.5">
            <DetailItem
              label={t('spoolbuddy.inventory.labelWeight', 'Label Weight')}
              value={`${spool.label_weight}g`}
            />
            <DetailItem
              label={t('spoolbuddy.inventory.weightUsed', 'Used')}
              value={spool.weight_used > 0 ? `${Math.round(spool.weight_used)}g` : '-'}
            />
            <DetailItem
              label={t('spoolbuddy.inventory.coreWeight', 'Core Weight')}
              value={spool.core_weight > 0 ? `${spool.core_weight}g` : '-'}
            />
            <DetailItem
              label={t('spoolbuddy.inventory.grossWeight', 'Gross Weight')}
              value={`${spool.label_weight + spool.core_weight}g`}
            />
            {spool.nozzle_temp_min != null && spool.nozzle_temp_max != null && (
              <DetailItem
                label={t('spoolbuddy.inventory.nozzleTemp', 'Nozzle Temp')}
                value={`${spool.nozzle_temp_min}-${spool.nozzle_temp_max}°C`}
              />
            )}
            {spool.cost_per_kg != null && spool.cost_per_kg > 0 && (
              <DetailItem
                label={t('spoolbuddy.inventory.costPerKg', 'Cost/kg')}
                value={`${spool.cost_per_kg.toFixed(2)}/kg`}
              />
            )}
            {spool.last_scale_weight != null && (
              <DetailItem
                label={t('spoolbuddy.inventory.lastScaleWeight', 'Scale Weight')}
                value={`${Math.round(spool.last_scale_weight)}g`}
              />
            )}
            {spool.tag_uid && (
              <DetailItem
                label={t('spoolbuddy.inventory.tagId', 'Tag')}
                value={spool.tag_uid}
                mono
              />
            )}
            {(spool.slicer_filament_name || spool.slicer_filament) && (
              <DetailItem
                label={t('spoolbuddy.inventory.slicerFilament', 'Slicer Filament')}
                value={spool.slicer_filament_name || spool.slicer_filament || ''}
              />
            )}
          </div>

          {/* K-Profiles */}
          {spool.k_profiles && spool.k_profiles.length > 0 && (
            <div>
              <p className="text-xs text-white/40 mb-1.5">{t('spoolbuddy.inventory.kProfiles', 'PA K-Profiles')}</p>
              <div className="space-y-1">
                {spool.k_profiles.map(kp => (
                  <div key={kp.id} className="flex items-center justify-between bg-bambu-dark-secondary rounded-lg px-3 py-2">
                    <span className="text-sm text-white/70 truncate">
                      {kp.name || `${kp.nozzle_diameter}mm ${kp.nozzle_type || ''}`}
                    </span>
                    <span className="text-sm font-mono text-bambu-green shrink-0 ml-2">
                      {kp.k_value.toFixed(3)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Note */}
          {spool.note && (
            <div className="bg-bambu-dark-secondary rounded-lg p-3">
              <p className="text-xs text-white/40 mb-1">{t('spoolbuddy.inventory.note', 'Note')}</p>
              <p className="text-sm text-white/70">{spool.note}</p>
            </div>
          )}

          {/* Close button */}
          <button
            onClick={onClose}
            className="w-full py-3 rounded-xl bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary text-white/60 hover:text-white text-sm font-medium transition-colors"
          >
            {t('spoolbuddy.inventory.close', 'Close')}
          </button>
        </div>
      </div>
    </div>
  );
}

function DetailItem({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="bg-bambu-dark-secondary rounded-lg px-3 py-2">
      <p className="text-[10px] text-white/40 uppercase tracking-wide">{label}</p>
      <p className={`text-sm text-white mt-0.5 truncate ${mono ? 'font-mono text-xs' : ''}`}>{value}</p>
    </div>
  );
}
