import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, TrendingDown, ShoppingCart, Check,
  ChevronDown, ChevronUp, Info, Edit2,
} from 'lucide-react';
import { api } from '../api/client';
import type { InventorySpool, SpoolUsageRecord, FilamentSkuSettings } from '../api/client';
import { useToast } from '../contexts/ToastContext';

// ── Types ─────────────────────────────────────────────────────────────────────

interface SkuGroup {
  key: string;
  material: string;
  subtype: string | null;
  brand: string | null;
  spools: InventorySpool[];
}

interface SkuForecast {
  group: SkuGroup;
  settings: FilamentSkuSettings | null;
  totalRemainingG: number;
  totalSpools: number;
  dailyRateG: number | null;
  rateTier: 'history' | 'delta' | 'none';
  daysRemaining: number | null;
  projectedEmptyDate: Date | null;
  reorderByDate: Date | null;
  reorderAlert: boolean;
  stockBreakAlert: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function skuKey(material: string, subtype: string | null, brand: string | null) {
  return `${material}||${subtype ?? ''}||${brand ?? ''}`;
}

function addDays(date: Date, days: number): Date {
  const d = new Date(date);
  d.setDate(d.getDate() + days);
  return d;
}

function formatDate(date: Date): string {
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function daysBetween(a: Date, b: Date): number {
  return Math.round((b.getTime() - a.getTime()) / 86400000);
}

/** Exponentially-weighted rate from ISO week buckets (most recent week = weight 1). */
function computeHistoryRate(records: SpoolUsageRecord[]): number | null {
  if (records.length === 0) return null;

  // Bucket by ISO week string "YYYY-Www"
  const buckets = new Map<string, number>();
  for (const r of records) {
    const d = new Date(r.created_at);
    // ISO week: use Thursday of the week to assign year
    const thursday = new Date(d);
    thursday.setDate(d.getDate() - ((d.getDay() + 6) % 7) + 3);
    const year = thursday.getFullYear();
    const jan4 = new Date(year, 0, 4);
    const week = Math.ceil(((thursday.getTime() - jan4.getTime()) / 86400000 + jan4.getDay() + 1) / 7);
    const key = `${year}-W${String(week).padStart(2, '0')}`;
    buckets.set(key, (buckets.get(key) ?? 0) + r.weight_used);
  }

  const sortedWeeks = [...buckets.keys()].sort().reverse(); // newest first
  if (sortedWeeks.length < 2) return null;

  let weightedSum = 0;
  let weightTotal = 0;
  const decay = 0.75;
  sortedWeeks.forEach((week, i) => {
    const w = Math.pow(decay, i);
    weightedSum += (buckets.get(week)!) * w;
    weightTotal += w;
  });

  const gramsPerWeek = weightedSum / weightTotal;
  return gramsPerWeek / 7; // grams/day
}

/** Simple delta heuristic: total used across all spools / days since oldest spool was added. */
function computeDeltaRate(spools: InventorySpool[]): number | null {
  const totalUsed = spools.reduce((s, sp) => s + sp.weight_used, 0);
  if (totalUsed === 0) return null;

  const now = Date.now();
  const oldestMs = spools.reduce((min, sp) => {
    const t = new Date(sp.created_at).getTime();
    return t < min ? t : min;
  }, now);

  const daysSinceOldest = (now - oldestMs) / 86400000;
  if (daysSinceOldest < 1) return null;
  return totalUsed / daysSinceOldest;
}

// ── Main component ────────────────────────────────────────────────────────────

export function ForecastPanel({ spools }: { spools: InventorySpool[] }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const { data: skuSettingsList = [] } = useQuery({
    queryKey: ['sku-settings'],
    queryFn: api.getSkuSettings,
    staleTime: 60_000,
  });

  // Fetch usage history for all spools in one call (limit high enough to be useful)
  const { data: usageHistory = [] } = useQuery({
    queryKey: ['all-usage-history-forecast'],
    queryFn: () => api.getAllUsageHistory(2000),
    staleTime: 60_000,
  });

  const settingsMap = useMemo(() => {
    const m = new Map<string, FilamentSkuSettings>();
    for (const s of skuSettingsList) {
      m.set(skuKey(s.material, s.subtype, s.brand), s);
    }
    return m;
  }, [skuSettingsList]);

  // Index usage records by spool_id
  const usageBySpoolId = useMemo(() => {
    const m = new Map<number, SpoolUsageRecord[]>();
    for (const r of usageHistory) {
      const arr = m.get(r.spool_id) ?? [];
      arr.push(r);
      m.set(r.spool_id, arr);
    }
    return m;
  }, [usageHistory]);

  // Build SKU groups from active spools
  const groups = useMemo((): SkuGroup[] => {
    const map = new Map<string, SkuGroup>();
    for (const spool of spools) {
      if (spool.archived_at) continue;
      const key = skuKey(spool.material, spool.subtype, spool.brand);
      const g = map.get(key) ?? {
        key,
        material: spool.material,
        subtype: spool.subtype,
        brand: spool.brand,
        spools: [],
      };
      g.spools.push(spool);
      map.set(key, g);
    }
    return [...map.values()].sort((a, b) =>
      a.material.localeCompare(b.material) || (a.brand ?? '').localeCompare(b.brand ?? '')
    );
  }, [spools]);

  // Compute forecasts
  const forecasts = useMemo((): SkuForecast[] => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    return groups.map((group) => {
      const settings = settingsMap.get(group.key) ?? null;
      const leadTime = settings?.lead_time_days ?? 7;
      const safetyMargin = settings?.safety_margin_days ?? 14;

      const totalRemainingG = group.spools.reduce(
        (sum, s) => sum + Math.max(0, s.label_weight - s.weight_used),
        0
      );

      // Gather usage history for all spools in group
      const groupHistory: SpoolUsageRecord[] = [];
      for (const s of group.spools) {
        const records = usageBySpoolId.get(s.id) ?? [];
        groupHistory.push(...records);
      }

      let dailyRateG: number | null = null;
      let rateTier: SkuForecast['rateTier'] = 'none';

      const historyRate = computeHistoryRate(groupHistory);
      if (historyRate !== null) {
        dailyRateG = historyRate;
        rateTier = 'history';
      } else {
        const deltaRate = computeDeltaRate(group.spools);
        if (deltaRate !== null) {
          dailyRateG = deltaRate;
          rateTier = 'delta';
        }
      }

      const daysRemaining =
        dailyRateG && dailyRateG > 0 ? Math.floor(totalRemainingG / dailyRateG) : null;

      const projectedEmptyDate =
        daysRemaining !== null ? addDays(today, daysRemaining) : null;

      const reorderByDate =
        projectedEmptyDate !== null
          ? addDays(projectedEmptyDate, -(safetyMargin))
          : null;

      const reorderAlert =
        reorderByDate !== null && daysBetween(today, reorderByDate) <= 0;

      const stockBreakAlert =
        daysRemaining !== null && daysRemaining <= leadTime;

      return {
        group,
        settings,
        totalRemainingG,
        totalSpools: group.spools.length,
        dailyRateG,
        rateTier,
        daysRemaining,
        projectedEmptyDate,
        reorderByDate,
        reorderAlert,
        stockBreakAlert,
      };
    });
  }, [groups, settingsMap, usageBySpoolId]);

  const alerts = forecasts.filter((f) => f.stockBreakAlert || f.reorderAlert);

  return (
    <div className="space-y-4">
      {/* Alert strip */}
      {alerts.length > 0 && (
        <div className="space-y-2">
          {alerts.map((f) => (
            <AlertBanner key={f.group.key} forecast={f} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {forecasts.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-bambu-gray">
          <TrendingDown className="w-10 h-10 mb-3 opacity-40" />
          <p className="text-sm">No active spools to forecast.</p>
        </div>
      )}

      {/* Forecast rows */}
      <div className="space-y-2">
        {forecasts.map((f) => (
          <ForecastRow
            key={f.group.key}
            forecast={f}
            onSaved={() => queryClient.invalidateQueries({ queryKey: ['sku-settings'] })}
            showToast={showToast}
          />
        ))}
      </div>

      {/* Legend */}
      {forecasts.length > 0 && (
        <div className="flex flex-wrap items-center gap-4 pt-2 text-xs text-bambu-gray border-t border-bambu-dark-tertiary">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-bambu-green inline-block" /> Trend (history-based)</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-400 inline-block" /> Estimated (weight delta)</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-bambu-gray/40 inline-block" /> No data</span>
        </div>
      )}
    </div>
  );
}

// ── Alert Banner ──────────────────────────────────────────────────────────────

function AlertBanner({ forecast: f }: { forecast: SkuForecast }) {
  const label = [f.group.brand, f.group.material, f.group.subtype].filter(Boolean).join(' ');
  const isBreak = f.stockBreakAlert;

  return (
    <div
      className={`flex items-start gap-3 px-4 py-3 rounded-lg border text-sm ${
        isBreak
          ? 'bg-red-500/10 border-red-500/30 text-red-300'
          : 'bg-yellow-500/10 border-yellow-500/30 text-yellow-300'
      }`}
    >
      <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
      <div>
        <span className="font-medium">{label}</span>
        {isBreak ? (
          <span className="ml-2">
            Stock break risk — only {f.daysRemaining}d remaining, shorter than lead time ({f.settings?.lead_time_days ?? 7}d).
          </span>
        ) : (
          <span className="ml-2">
            Reorder now — reorder date {f.reorderByDate ? formatDate(f.reorderByDate) : '—'} has passed.
          </span>
        )}
      </div>
    </div>
  );
}

// ── Forecast Row ──────────────────────────────────────────────────────────────

function ForecastRow({
  forecast: f,
  onSaved,
  showToast,
}: {
  forecast: SkuForecast;
  onSaved: () => void;
  showToast: (msg: string, type: 'success' | 'error') => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editingLead, setEditingLead] = useState(false);
  const [editingMargin, setEditingMargin] = useState(false);
  const [leadInput, setLeadInput] = useState(String(f.settings?.lead_time_days ?? 7));
  const [marginInput, setMarginInput] = useState(String(f.settings?.safety_margin_days ?? 14));

  const upsertMutation = useMutation({
    mutationFn: api.upsertSkuSettings,
    onSuccess: () => {
      onSaved();
      showToast('Settings saved', 'success');
    },
    onError: () => showToast('Failed to save settings', 'error'),
  });

  const label = [f.group.brand, f.group.material, f.group.subtype].filter(Boolean).join(' ');
  const colorStyle =
    f.group.spools[0]?.rgba ? `#${f.group.spools[0].rgba.substring(0, 6)}` : '#4B5563';

  const remainPct =
    f.totalRemainingG > 0 && f.group.spools.length > 0
      ? Math.round(
          (f.totalRemainingG /
            f.group.spools.reduce((s, sp) => s + sp.label_weight, 0)) *
            100
        )
      : 0;

  const daysColor =
    f.daysRemaining === null
      ? 'text-bambu-gray'
      : f.stockBreakAlert
      ? 'text-red-400'
      : f.reorderAlert
      ? 'text-yellow-400'
      : f.daysRemaining < 30
      ? 'text-yellow-400'
      : 'text-green-400';

  function saveLeadTime() {
    const v = parseInt(leadInput, 10);
    if (isNaN(v) || v < 1) return;
    upsertMutation.mutate({
      material: f.group.material,
      subtype: f.group.subtype,
      brand: f.group.brand,
      lead_time_days: v,
      safety_margin_days: f.settings?.safety_margin_days ?? 14,
    });
    setEditingLead(false);
  }

  function saveMargin() {
    const v = parseInt(marginInput, 10);
    if (isNaN(v) || v < 1) return;
    upsertMutation.mutate({
      material: f.group.material,
      subtype: f.group.subtype,
      brand: f.group.brand,
      lead_time_days: f.settings?.lead_time_days ?? 7,
      safety_margin_days: v,
    });
    setEditingMargin(false);
  }

  const tierBadge =
    f.rateTier === 'history' ? (
      <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-bambu-green/15 text-bambu-green">
        <span className="w-1.5 h-1.5 rounded-full bg-bambu-green" />
        Trend
      </span>
    ) : f.rateTier === 'delta' ? (
      <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-blue-400/15 text-blue-400">
        <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />
        Est.
      </span>
    ) : (
      <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-bambu-dark-tertiary text-bambu-gray/60">
        <span className="w-1.5 h-1.5 rounded-full bg-bambu-gray/40" />
        No data
      </span>
    );

  return (
    <div
      className={`bg-bambu-dark-secondary rounded-lg border transition-colors ${
        f.stockBreakAlert
          ? 'border-red-500/40'
          : f.reorderAlert
          ? 'border-yellow-500/40'
          : 'border-bambu-dark-tertiary'
      }`}
    >
      {/* Main row */}
      <div
        className="grid items-center gap-3 px-4 py-3 cursor-pointer select-none"
        style={{ gridTemplateColumns: '16px 1fr 120px 110px 110px 120px 120px 90px' }}
        onClick={() => setExpanded((e) => !e)}
      >
        {/* Color swatch */}
        <span
          className="w-3 h-3 rounded-full border border-black/20 flex-shrink-0"
          style={{ backgroundColor: colorStyle }}
        />

        {/* Label */}
        <div className="min-w-0">
          <div className="text-sm font-medium text-white truncate">{label}</div>
          <div className="text-xs text-bambu-gray">
            {f.totalSpools} spool{f.totalSpools !== 1 ? 's' : ''}
          </div>
        </div>

        {/* Stock remaining */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <div className="flex-1 h-1.5 bg-bambu-dark-tertiary rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  remainPct > 50 ? 'bg-bambu-green' : remainPct > 20 ? 'bg-yellow-500' : 'bg-red-500'
                }`}
                style={{ width: `${Math.min(remainPct, 100)}%` }}
              />
            </div>
          </div>
          <span className="text-xs text-bambu-gray">
            {Math.round(f.totalRemainingG)}g
          </span>
        </div>

        {/* Daily rate */}
        <div className="flex flex-col gap-0.5">
          <span className="text-sm text-white">
            {f.dailyRateG !== null ? `${f.dailyRateG.toFixed(1)}g/d` : '—'}
          </span>
          {tierBadge}
        </div>

        {/* Days remaining */}
        <div className={`text-sm font-semibold ${daysColor}`}>
          {f.daysRemaining !== null ? (
            <span>{f.daysRemaining}d</span>
          ) : (
            <span className="text-bambu-gray font-normal">—</span>
          )}
        </div>

        {/* Projected empty */}
        <div className="text-xs text-bambu-gray">
          {f.projectedEmptyDate ? formatDate(f.projectedEmptyDate) : '—'}
        </div>

        {/* Reorder by */}
        <div className={`text-xs font-medium ${f.reorderAlert ? 'text-yellow-400' : 'text-bambu-gray'}`}>
          {f.reorderByDate ? formatDate(f.reorderByDate) : '—'}
        </div>

        {/* Status + expand */}
        <div className="flex items-center justify-end gap-2">
          {f.stockBreakAlert ? (
            <AlertTriangle className="w-4 h-4 text-red-400" title="Stock break risk" />
          ) : f.reorderAlert ? (
            <ShoppingCart className="w-4 h-4 text-yellow-400" title="Reorder now" />
          ) : f.daysRemaining !== null ? (
            <Check className="w-4 h-4 text-bambu-green/60" />
          ) : null}
          {expanded ? (
            <ChevronUp className="w-4 h-4 text-bambu-gray" />
          ) : (
            <ChevronDown className="w-4 h-4 text-bambu-gray" />
          )}
        </div>
      </div>

      {/* Expanded settings panel */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-bambu-dark-tertiary">
          <div className="pt-3 grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Lead time */}
            <SettingField
              label="Lead Time"
              hint="How many days for a reorder to arrive"
              unit="days"
              editing={editingLead}
              value={f.settings?.lead_time_days ?? 7}
              inputValue={leadInput}
              onInputChange={setLeadInput}
              onEdit={() => { setLeadInput(String(f.settings?.lead_time_days ?? 7)); setEditingLead(true); }}
              onSave={saveLeadTime}
              onCancel={() => setEditingLead(false)}
              isPending={upsertMutation.isPending}
            />

            {/* Safety margin */}
            <SettingField
              label="Safety Margin"
              hint="Buffer days before projected empty to trigger reorder alert"
              unit="days"
              editing={editingMargin}
              value={f.settings?.safety_margin_days ?? 14}
              inputValue={marginInput}
              onInputChange={setMarginInput}
              onEdit={() => { setMarginInput(String(f.settings?.safety_margin_days ?? 14)); setEditingMargin(true); }}
              onSave={saveMargin}
              onCancel={() => setEditingMargin(false)}
              isPending={upsertMutation.isPending}
            />
          </div>

          {/* Spool breakdown */}
          {f.group.spools.length > 1 && (
            <div className="mt-3 pt-3 border-t border-bambu-dark-tertiary">
              <p className="text-xs text-bambu-gray mb-2">Individual spools</p>
              <div className="space-y-1">
                {f.group.spools.map((s) => {
                  const remaining = Math.max(0, s.label_weight - s.weight_used);
                  const pct = s.label_weight > 0 ? (remaining / s.label_weight) * 100 : 0;
                  return (
                    <div key={s.id} className="flex items-center gap-3 text-xs text-bambu-gray">
                      <span className="font-mono text-bambu-gray/60 w-8">#{s.id}</span>
                      <div className="flex-1 h-1.5 bg-bambu-dark-tertiary rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${pct > 50 ? 'bg-bambu-green' : pct > 20 ? 'bg-yellow-500' : 'bg-red-500'}`}
                          style={{ width: `${Math.min(pct, 100)}%` }}
                        />
                      </div>
                      <span className="w-14 text-right">{Math.round(remaining)}g</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Setting field with inline edit ────────────────────────────────────────────

function SettingField({
  label, hint, unit, editing, value, inputValue,
  onInputChange, onEdit, onSave, onCancel, isPending,
}: {
  label: string;
  hint: string;
  unit: string;
  editing: boolean;
  value: number;
  inputValue: string;
  onInputChange: (v: string) => void;
  onEdit: () => void;
  onSave: () => void;
  onCancel: () => void;
  isPending: boolean;
}) {
  return (
    <div className="bg-bambu-dark-tertiary/40 rounded-lg p-3 space-y-1">
      <div className="flex items-center gap-1.5">
        <span className="text-xs font-medium text-white">{label}</span>
        <span title={hint}>
          <Info className="w-3 h-3 text-bambu-gray/50" />
        </span>
      </div>
      {editing ? (
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => { e.preventDefault(); onSave(); }}
        >
          <input
            type="number"
            min={1}
            max={365}
            value={inputValue}
            onChange={(e) => onInputChange(e.target.value)}
            className="w-20 px-2 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-sm text-white focus:outline-none focus:border-bambu-green"
            autoFocus
            disabled={isPending}
          />
          <span className="text-xs text-bambu-gray">{unit}</span>
          <button
            type="submit"
            disabled={isPending}
            className="px-2 py-1 bg-bambu-green text-white text-xs rounded hover:bg-bambu-green/80 transition-colors disabled:opacity-50"
          >
            Save
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={isPending}
            className="px-2 py-1 text-xs text-bambu-gray hover:text-white transition-colors"
          >
            Cancel
          </button>
        </form>
      ) : (
        <div className="flex items-center gap-2">
          <span className="text-lg font-semibold text-white">{value}</span>
          <span className="text-xs text-bambu-gray">{unit}</span>
          <button
            onClick={onEdit}
            className="p-1 text-bambu-gray hover:text-white rounded transition-colors"
            title={`Edit ${label}`}
          >
            <Edit2 className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
}

// ── Column headers (exported for use in InventoryPage toolbar) ────────────────

export function ForecastColumnHeaders() {
  return (
    <div
      className="grid items-center gap-3 px-4 py-2 text-xs font-medium text-bambu-gray uppercase tracking-wide border-b border-bambu-dark-tertiary"
      style={{ gridTemplateColumns: '16px 1fr 120px 110px 110px 120px 120px 90px' }}
    >
      <span />
      <span>SKU</span>
      <span>Stock</span>
      <span>Daily Rate</span>
      <span>Days Left</span>
      <span>Empty By</span>
      <span>Reorder By</span>
      <span />
    </div>
  );
}
