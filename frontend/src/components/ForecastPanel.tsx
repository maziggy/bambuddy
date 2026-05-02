import { useState, useMemo, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle, TrendingDown, ShoppingCart, Check, BellOff,
  ChevronDown, ChevronUp, Info, Edit2, X, Lock,
  ArrowUp, ArrowDown, ArrowUpDown, Package, Trash2, BarChart2,
  CreditCard, PackageCheck, Download, RotateCcw,
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend,
} from 'recharts';
import { api } from '../api/client';
import type { InventorySpool, SpoolUsageRecord, FilamentSkuSettings, ShoppingListItem } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';

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
  totalLabelG: number;
  totalSpools: number;
  totalUsedG: number;
  dailyRateG: number | null;
  dailyRateStdDev: number | null;
  rateTier: 'history' | 'delta' | 'none';
  effectiveLeadTimeDays: number;
  safetyStockG: number;
  reorderPointG: number;
  daysRemaining: number | null;
  daysUntilROP: number | null;
  projectedEmptyDate: Date | null;
  reorderTriggerDate: Date | null;
  reorderAlert: boolean;
  stockBreakAlert: boolean;
}

type SortKey = 'material' | 'used' | 'days_left' | 'stock';
type SortDir = 'asc' | 'desc';
type ChartDays = 7 | 30 | 180;

// ── Constants ─────────────────────────────────────────────────────────────────

const Z_95 = 1.65;
const CHART_COLORS = ['#1DB954', '#3B82F6', '#F59E0B', '#EF4444', '#8B5CF6'];

// ── Pure helpers ──────────────────────────────────────────────────────────────

function skuKey(material: string, subtype: string | null, brand: string | null) {
  return `${material}||${subtype ?? ''}||${brand ?? ''}`;
}

function addDays(date: Date, days: number): Date {
  const d = new Date(date);
  d.setDate(d.getDate() + Math.round(days));
  return d;
}

function formatDate(date: Date): string {
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatDateShort(date: Date): string {
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/**
 * Compute a time-weighted daily consumption rate and standard deviation.
 *
 * Algorithm:
 *   1. Sort all usage events by timestamp (oldest → newest).
 *   2. Convert each event into a g/day intensity = weight_used / elapsed_days,
 *      where elapsed_days is the gap to the previous event (floor: 0.5d to
 *      avoid inflated rates from same-day prints).
 *   3. Apply exponential age-decay: each observation is weighted by
 *      exp(-λ * age_days) so recent prints dominate. λ = ln(2)/30 gives a
 *      30-day half-life — prints from a month ago count half as much.
 *   4. Compute the weighted mean and weighted variance → std dev.
 *
 * Returns null when there is only one event (no gap to measure) — the
 * delta-rate fallback handles that case.
 */
function computeHistoryRate(records: SpoolUsageRecord[]): { rate: number; stdDev: number } | null {
  if (records.length < 2) return null;

  // Sort ascending by time
  const sorted = [...records].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );

  const now = Date.now();
  // λ for 30-day half-life: ln(2)/30
  const lambda = Math.LN2 / 30;

  const observations: { rate: number; weight: number }[] = [];

  for (let i = 1; i < sorted.length; i++) {
    const prev = new Date(sorted[i - 1].created_at).getTime();
    const curr = new Date(sorted[i].created_at).getTime();
    const elapsedDays = Math.max((curr - prev) / 86400000, 0.5); // floor at 0.5d
    const ageDays = (now - curr) / 86400000;

    // g/day for this interval
    const intervalRate = sorted[i].weight_used / elapsedDays;
    // Exponential age-decay weight
    const w = Math.exp(-lambda * ageDays);

    observations.push({ rate: intervalRate, weight: w });
  }

  const totalW = observations.reduce((s, o) => s + o.weight, 0);
  if (totalW === 0) return null;

  const mean = observations.reduce((s, o) => s + o.rate * o.weight, 0) / totalW;
  const variance = observations.reduce((s, o) => s + o.weight * (o.rate - mean) ** 2, 0) / totalW;

  return { rate: mean, stdDev: Math.sqrt(variance) };
}

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

function buildProjectionSeries(
  forecast: SkuForecast,
  days = 60,
): { day: number; label: string; stock: number; rop: number }[] {
  if (forecast.dailyRateG === null) return [];
  const rate = forecast.dailyRateG;
  const result = [];
  for (let d = 0; d <= days; d++) {
    const stock = Math.max(0, forecast.totalRemainingG - rate * d);
    result.push({
      day: d,
      label: formatDateShort(addDays(new Date(), d)),
      stock: Math.round(stock),
      rop: Math.round(forecast.reorderPointG),
    });
    if (stock === 0) break;
  }
  return result;
}

// ── Main component ────────────────────────────────────────────────────────────

export function ForecastPanel({ spools }: { spools: InventorySpool[] }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { t } = useTranslation();
  const { hasPermission, hasAnyPermission } = useAuth();

  const canRead = hasPermission('inventory:forecast_read');
  const canWrite = hasAnyPermission('inventory:forecast_write', 'inventory:update');

  // All hooks must run unconditionally — guard render is deferred until after hooks
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>('material');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [cartModal, setCartModal] = useState<SkuForecast | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const [chartDays, setChartDays] = useState<ChartDays>(30);

  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings, enabled: canRead });
  const { data: skuSettingsList = [] } = useQuery({ queryKey: ['sku-settings'], queryFn: api.getSkuSettings, staleTime: 60_000, enabled: canRead });
  const { data: usageHistory = [] } = useQuery({ queryKey: ['all-usage-history-forecast'], queryFn: () => api.getAllUsageHistory(5000), staleTime: 60_000, enabled: canRead });
  const { data: shoppingList = [] } = useQuery({ queryKey: ['shopping-list'], queryFn: api.getShoppingList, staleTime: 30_000, enabled: canRead });

  const globalLeadTime = settings?.forecast_global_lead_time_days ?? 0;

  const settingsMap = useMemo(() => {
    const m = new Map<string, FilamentSkuSettings>();
    for (const s of skuSettingsList) m.set(skuKey(s.material, s.subtype, s.brand), s);
    return m;
  }, [skuSettingsList]);

  const usageBySpoolId = useMemo(() => {
    const m = new Map<number, SpoolUsageRecord[]>();
    for (const r of usageHistory) {
      const arr = m.get(r.spool_id) ?? [];
      arr.push(r);
      m.set(r.spool_id, arr);
    }
    return m;
  }, [usageHistory]);

  const groups = useMemo((): SkuGroup[] => {
    const map = new Map<string, SkuGroup>();
    for (const spool of spools) {
      if (spool.archived_at) continue;
      const key = skuKey(spool.material, spool.subtype, spool.brand);
      const g = map.get(key) ?? { key, material: spool.material, subtype: spool.subtype, brand: spool.brand, spools: [] };
      g.spools.push(spool);
      map.set(key, g);
    }
    return [...map.values()];
  }, [spools]);

  const forecasts = useMemo((): SkuForecast[] => {
    const today = new Date(); today.setHours(0, 0, 0, 0);

    return groups.map((group): SkuForecast => {
      const skuSettings = settingsMap.get(group.key) ?? null;
      const skuLeadTime = skuSettings?.lead_time_days ?? 0;
      const effectiveLeadTimeDays = Math.max(globalLeadTime, skuLeadTime);
      const marginValue = skuSettings?.safety_margin_value ?? 14;
      const marginUnit = skuSettings?.safety_margin_unit ?? 'days';

      const totalRemainingG = group.spools.reduce((s, sp) => s + Math.max(0, sp.label_weight - sp.weight_used), 0);
      const totalLabelG = group.spools.reduce((s, sp) => s + sp.label_weight, 0);
      const totalUsedG = group.spools.reduce((s, sp) => s + sp.weight_used, 0);

      const groupHistory: SpoolUsageRecord[] = [];
      for (const s of group.spools) groupHistory.push(...(usageBySpoolId.get(s.id) ?? []));

      let dailyRateG: number | null = null;
      let dailyRateStdDev: number | null = null;
      let rateTier: SkuForecast['rateTier'] = 'none';

      const histResult = computeHistoryRate(groupHistory);
      if (histResult !== null) {
        dailyRateG = histResult.rate;
        dailyRateStdDev = histResult.stdDev;
        rateTier = 'history';
      } else {
        const delta = computeDeltaRate(group.spools);
        if (delta !== null) { dailyRateG = delta; rateTier = 'delta'; }
      }

      const σ = dailyRateStdDev ?? (dailyRateG !== null ? dailyRateG * 0.2 : 0);
      const statisticalSafetyStockG = Z_95 * σ * Math.sqrt(effectiveLeadTimeDays);
      // safety margin: user-defined buffer on top of statistical safety stock
      const safetyMarginG = marginUnit === 'g'
        ? marginValue
        : (dailyRateG !== null ? dailyRateG * marginValue : marginValue * 5);
      const safetyStockG = statisticalSafetyStockG + safetyMarginG;
      const reorderPointG = dailyRateG !== null
        ? dailyRateG * effectiveLeadTimeDays + safetyStockG
        : 0;

      const daysRemaining = dailyRateG && dailyRateG > 0 ? Math.floor(totalRemainingG / dailyRateG) : null;
      const projectedEmptyDate = daysRemaining !== null ? addDays(today, daysRemaining) : null;

      const daysUntilROP = dailyRateG && dailyRateG > 0
        ? Math.floor((totalRemainingG - reorderPointG) / dailyRateG)
        : null;

      const reorderTriggerDate = daysUntilROP !== null ? addDays(today, Math.max(0, daysUntilROP)) : null;
      const stockBreakAlert = daysRemaining !== null && effectiveLeadTimeDays > 0 && daysRemaining <= effectiveLeadTimeDays;
      const reorderAlert = !stockBreakAlert && daysUntilROP !== null && daysUntilROP <= 0;

      return {
        group, settings: skuSettings,
        totalRemainingG, totalLabelG, totalSpools: group.spools.length, totalUsedG,
        dailyRateG, dailyRateStdDev,
        rateTier,
        effectiveLeadTimeDays, safetyStockG, reorderPointG,
        daysRemaining, daysUntilROP,
        projectedEmptyDate, reorderTriggerDate,
        reorderAlert, stockBreakAlert,
      };
    });
  }, [groups, settingsMap, usageBySpoolId, globalLeadTime]);

  const sortedForecasts = useMemo(() => {
    const arr = [...forecasts];
    arr.sort((a, b) => {
      let va: number | string = 0;
      let vb: number | string = 0;
      switch (sortKey) {
        case 'material':
          va = [a.group.material, a.group.subtype ?? '', a.group.brand ?? ''].join(' ').toLowerCase();
          vb = [b.group.material, b.group.subtype ?? '', b.group.brand ?? ''].join(' ').toLowerCase();
          break;
        case 'used':
          va = a.totalUsedG; vb = b.totalUsedG;
          break;
        case 'days_left':
          va = a.daysRemaining ?? 999999; vb = b.daysRemaining ?? 999999;
          break;
        case 'stock':
          va = a.totalRemainingG; vb = b.totalRemainingG;
          break;
      }
      const cmp = va < vb ? -1 : va > vb ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return arr;
  }, [forecasts, sortKey, sortDir]);

  const alerts = useMemo(() => forecasts.filter((f) => !f.settings?.alerts_snoozed && (f.stockBreakAlert || f.reorderAlert)), [forecasts]);

  const top5 = useMemo(() =>
    [...forecasts]
      .filter((f) => f.dailyRateG !== null)
      .sort((a, b) => b.totalUsedG - a.totalUsedG)
      .slice(0, 5),
    [forecasts]
  );

  // ── Read permission guard — all hooks above this point ──────────────────────
  if (!canRead) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-bambu-gray gap-3">
        <Lock className="w-8 h-8 opacity-40" />
        <p className="text-sm">{t('forecast.noReadAccess')}</p>
      </div>
    );
  }

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir(key === 'days_left' ? 'asc' : 'desc'); }
  }

  const shoppingListBadge = shoppingList.length > 0 ? shoppingList.length : null;

  return (
    <div className="space-y-5">

      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Alert button */}
        {alerts.length > 0 && (
          <button
            onClick={() => setAlertsOpen((o) => !o)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ${
              alerts.some((f) => f.stockBreakAlert)
                ? 'bg-red-500/15 border-red-500/30 text-red-300 hover:bg-red-500/25'
                : 'bg-yellow-500/15 border-yellow-500/30 text-yellow-300 hover:bg-yellow-500/25'
            }`}
          >
            <AlertTriangle className="w-4 h-4" />
            {t('forecast.alertCount', { count: alerts.length })}
            {alertsOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        )}

        {/* Global lead time */}
        {canWrite && (
          <GlobalLeadTimeSetting
            value={globalLeadTime}
            onSave={(v) => {
              api.updateSettings({ forecast_global_lead_time_days: v }).then(() => {
                queryClient.invalidateQueries({ queryKey: ['settings'] });
                showToast(t('forecast.globalLeadTimeSaved'), 'success');
              });
            }}
          />
        )}

        {/* Shopping list toggle */}
        <button
          onClick={() => setListOpen((o) => !o)}
          className="relative flex items-center gap-2 px-3 py-1.5 rounded-lg border border-bambu-dark-tertiary text-bambu-gray hover:bg-bambu-dark-tertiary text-sm transition-colors ml-auto"
        >
          <ShoppingCart className="w-4 h-4" />
          <span className="hidden sm:inline">{t('forecast.shoppingList')}</span>
          {shoppingListBadge && (
            <span className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-bambu-green text-white text-[10px] font-bold flex items-center justify-center">
              {shoppingListBadge}
            </span>
          )}
        </button>
      </div>

      {/* ── Collapsed alerts panel ── */}
      {alertsOpen && alerts.length > 0 && (
        <div className="space-y-2">
          {alerts.map((f) => (
            <AlertBanner key={f.group.key} forecast={f} onCart={() => setCartModal(f)} />
          ))}
        </div>
      )}

      {/* ── Shopping list panel ── */}
      {listOpen && (
        <ShoppingListPanel
          items={shoppingList}
          forecasts={forecasts}
          globalLeadTime={globalLeadTime}
          canWrite={canWrite}
          onClose={() => setListOpen(false)}
          onRemove={(id) => {
            api.removeFromShoppingList(id)
              .then(() => queryClient.invalidateQueries({ queryKey: ['shopping-list'] }))
              .catch(() => showToast(t('forecast.failedSaveSettings'), 'error'));
          }}
          onClear={() => {
            api.clearShoppingList()
              .then(() => queryClient.invalidateQueries({ queryKey: ['shopping-list'] }))
              .catch(() => showToast(t('forecast.failedSaveSettings'), 'error'));
          }}
        />
      )}

      {/* ── Usage + projection chart ── */}
      {top5.length > 0 && <UsageChart forecasts={top5} days={chartDays} onDaysChange={setChartDays} />}

      {/* ── Table ── */}
      {forecasts.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-bambu-gray">
          <TrendingDown className="w-10 h-10 mb-3 opacity-40" />
          <p className="text-sm">{t('forecast.noSpools')}</p>
        </div>
      ) : (
        <div className="bg-bambu-dark-secondary rounded-lg overflow-hidden border border-bambu-dark-tertiary">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-bambu-dark-tertiary bg-bambu-dark-tertiary/30">
                  {/* Color dot */}
                  <th className="w-8 px-4 py-3" />
                  <SortableTh col="material" active={sortKey} dir={sortDir} onSort={handleSort}>
                    {t('forecast.sku')}
                  </SortableTh>
                  <SortableTh col="stock" active={sortKey} dir={sortDir} onSort={handleSort}>
                    {t('forecast.stock')}
                  </SortableTh>
                  <SortableTh col="used" active={sortKey} dir={sortDir} onSort={handleSort}>
                    {t('forecast.dailyRate')}
                  </SortableTh>
                  <SortableTh col="days_left" active={sortKey} dir={sortDir} onSort={handleSort}>
                    {t('forecast.daysLeft')}
                  </SortableTh>
                  <th className="px-4 py-3 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">
                    {t('forecast.emptyBy')}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">
                    {t('forecast.reorderBy')}
                  </th>
                  {/* Actions */}
                  <th className="w-24 px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-bambu-dark-tertiary">
                {sortedForecasts.map((f) => (
                  <ForecastRow
                    key={f.group.key}
                    forecast={f}
                    globalLeadTime={globalLeadTime}
                    canWrite={canWrite}
                    onSaved={() => queryClient.invalidateQueries({ queryKey: ['sku-settings'] })}
                    onCart={() => setCartModal(f)}
                    showToast={showToast}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {/* Legend */}
          <div className="flex flex-wrap items-center gap-4 px-4 py-3 text-xs text-bambu-gray border-t border-bambu-dark-tertiary bg-bambu-dark-tertiary/20">
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-bambu-green inline-block" />
              {t('forecast.trendLegend')}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-blue-400 inline-block" />
              {t('forecast.estimatedLegend')}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-bambu-gray/40 inline-block" />
              {t('forecast.noDataLegend')}
            </span>
          </div>
        </div>
      )}

      {/* ── Add to cart modal ── */}
      {cartModal && (
        <AddToCartModal
          forecast={cartModal}
          onClose={() => setCartModal(null)}
          onAdd={(item) => {
            api.addToShoppingList(item).then(() => {
              queryClient.invalidateQueries({ queryKey: ['shopping-list'] });
              showToast(t('forecast.addedToCart'), 'success');
              setCartModal(null);
              setListOpen(true);
            }).catch(() => showToast(t('forecast.failedAddItem'), 'error'));
          }}
        />
      )}
    </div>
  );
}

// ── Sortable th ───────────────────────────────────────────────────────────────

function SortableTh({
  col, active, dir, onSort, children,
}: {
  col: SortKey;
  active: SortKey;
  dir: SortDir;
  onSort: (k: SortKey) => void;
  children: React.ReactNode;
}) {
  const isActive = active === col;
  return (
    <th
      className="px-4 py-3 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide cursor-pointer select-none hover:text-white transition-colors"
      onClick={() => onSort(col)}
    >
      <span className="inline-flex items-center">
        {children}
        {isActive
          ? dir === 'asc'
            ? <ArrowUp className="w-3 h-3 ml-1 text-bambu-green" />
            : <ArrowDown className="w-3 h-3 ml-1 text-bambu-green" />
          : <ArrowUpDown className="w-3 h-3 ml-1 opacity-40" />
        }
      </span>
    </th>
  );
}

// ── Alert Banner ──────────────────────────────────────────────────────────────

function AlertBanner({ forecast: f, onCart }: { forecast: SkuForecast; onCart: () => void }) {
  const { t } = useTranslation();
  const label = [f.group.brand, f.group.material, f.group.subtype].filter(Boolean).join(' ');
  const isBreak = f.stockBreakAlert;

  return (
    <div className={`flex items-center gap-3 px-4 py-3 rounded-lg border text-sm ${
      isBreak ? 'bg-red-500/10 border-red-500/30 text-red-300' : 'bg-yellow-500/10 border-yellow-500/30 text-yellow-300'
    }`}>
      <AlertTriangle className="w-4 h-4 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <span className="font-medium">{label}</span>
        {isBreak ? (
          <span className="ml-2 text-xs opacity-80">
            {t('forecast.stockBreakRisk')} — {t('forecast.stockBreakDetail', { days: f.daysRemaining, lt: f.effectiveLeadTimeDays })}
          </span>
        ) : (
          <span className="ml-2 text-xs opacity-80">
            {t('forecast.reorderNow')} — {t('forecast.reorderTriggerPassed', { date: f.reorderTriggerDate ? formatDate(f.reorderTriggerDate) : '—' })}
          </span>
        )}
      </div>
      <button
        onClick={onCart}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded border border-current text-xs opacity-70 hover:opacity-100 transition-opacity"
      >
        <ShoppingCart className="w-3 h-3" /> {t('forecast.order')}
      </button>
    </div>
  );
}

// ── Usage + Projection Chart ──────────────────────────────────────────────────

const CHART_TIMEFRAMES: { label: string; value: ChartDays }[] = [
  { label: '1W', value: 7 },
  { label: '1M', value: 30 },
  { label: '6M', value: 180 },
];

function UsageChart({ forecasts, days: maxDays, onDaysChange }: {
  forecasts: SkuForecast[];
  days: ChartDays;
  onDaysChange: (d: ChartDays) => void;
}) {
  const { t } = useTranslation();
  const days = Array.from({ length: maxDays + 1 }, (_, i) => i);

  const series = forecasts.map((f, idx) => ({
    key: f.group.key,
    label: [f.group.brand, f.group.material, f.group.subtype].filter(Boolean).join(' '),
    color: CHART_COLORS[idx % CHART_COLORS.length],
    rop: f.reorderPointG,
    points: buildProjectionSeries(f, maxDays),
  }));

  const chartData = days.map((d) => {
    const row: Record<string, number | string> = { day: d, label: formatDateShort(addDays(new Date(), d)) };
    for (const s of series) {
      const pt = s.points.find((p) => p.day === d);
      row[s.key] = pt?.stock ?? 0;
    }
    return row;
  });

  const lastNonZeroDay = (() => {
    for (let d = maxDays; d >= 0; d--) {
      if (series.some((s) => (chartData[d]?.[s.key] as number) > 0)) return d;
    }
    return maxDays;
  })();

  const trimmedData = chartData.slice(0, lastNonZeroDay + 1);
  const ropLines = series.filter((s) => s.rop > 0);

  return (
    <div className="bg-bambu-dark-secondary rounded-lg overflow-hidden border border-bambu-dark-tertiary p-4">
      <div className="flex items-center gap-2 mb-4">
        <TrendingDown className="w-4 h-4 text-bambu-green" />
        <h3 className="text-sm font-semibold text-white">{t('forecast.chartTitle')}</h3>
        <span className="text-xs text-bambu-gray ml-1 hidden sm:inline">{t('forecast.dashedLinesROP')}</span>
        <div className="ml-auto flex items-center bg-bambu-dark-tertiary rounded-lg p-0.5">
          {CHART_TIMEFRAMES.map((tf) => (
            <button
              key={tf.value}
              onClick={() => onDaysChange(tf.value)}
              className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                maxDays === tf.value
                  ? 'bg-bambu-dark-secondary text-white shadow'
                  : 'text-bambu-gray hover:text-white'
              }`}
            >
              {tf.label}
            </button>
          ))}
        </div>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={trimmedData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <defs>
            {series.map((s) => (
              <linearGradient key={s.key} id={`grad-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={s.color} stopOpacity={0.25} />
                <stop offset="95%" stopColor={s.color} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" strokeOpacity={0.5} />
          <XAxis
            dataKey="label"
            tick={{ fill: '#6B7280', fontSize: 10 }}
            interval={Math.max(0, Math.ceil(lastNonZeroDay / 8) - 1)}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: '#6B7280', fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(1)}kg` : `${v}g`}
            width={48}
          />
          <Tooltip
            contentStyle={{ background: '#1a1a2e', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: '#9CA3AF' }}
            itemStyle={{ color: '#E5E7EB' }}
            formatter={(value, name) => {
              if (typeof value !== 'number') return '';
              const s = series.find((x) => x.key === String(name));
              return `${value}g — ${s?.label ?? name}`;
            }}
          />
          <Legend
            formatter={(value) => {
              const s = series.find((x) => x.key === value);
              return <span style={{ color: '#9CA3AF', fontSize: 11 }}>{s?.label ?? value}</span>;
            }}
          />
          {series.map((s) => (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stroke={s.color}
              strokeWidth={2}
              fill={`url(#grad-${s.key})`}
              dot={false}
              activeDot={{ r: 3 }}
            />
          ))}
          {ropLines.map((s) => (
            <ReferenceLine
              key={`rop-${s.key}`}
              y={s.rop}
              stroke={s.color}
              strokeDasharray="4 3"
              strokeOpacity={0.6}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Global lead time setting (compact inline) ─────────────────────────────────

function GlobalLeadTimeSetting({ value, onSave }: { value: number; onSave: (v: number) => void }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [input, setInput] = useState(String(value));

  function save() {
    const v = parseInt(input, 10);
    if (isNaN(v) || v < 0) return;
    onSave(v);
    setEditing(false);
  }

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 bg-bambu-dark-tertiary/40 rounded-lg border border-bambu-dark-tertiary text-xs text-bambu-gray">
      <Info className="w-3.5 h-3.5 flex-shrink-0" aria-label={t('forecast.globalLeadTimeHint')} />
      <span className="hidden sm:inline">{t('forecast.globalLeadTime')}:</span>
      {editing ? (
        <form className="flex items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <input
            type="number" min={0} max={365}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className="w-14 px-1.5 py-0.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-sm text-white focus:outline-none focus:border-bambu-green"
            autoFocus
          />
          <span className="text-bambu-gray">d</span>
          <button type="submit" className="px-2 py-0.5 bg-bambu-green text-white text-xs rounded hover:bg-bambu-green/80">{t('forecast.save')}</button>
          <button type="button" onClick={() => setEditing(false)} className="text-xs text-bambu-gray hover:text-white">✕</button>
        </form>
      ) : (
        <div className="flex items-center gap-1.5">
          <span className="font-semibold text-white">{value}d</span>
          <button onClick={() => { setInput(String(value)); setEditing(true); }} className="p-0.5 text-bambu-gray hover:text-white rounded transition-colors">
            <Edit2 className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
}

// ── Forecast Row ──────────────────────────────────────────────────────────────

function ForecastRow({
  forecast: f, globalLeadTime, canWrite, onSaved, onCart, showToast,
}: {
  forecast: SkuForecast;
  globalLeadTime: number;
  canWrite: boolean;
  onSaved: () => void;
  onCart: () => void;
  showToast: (msg: string, type: 'success' | 'error') => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [editingLead, setEditingLead] = useState(false);
  const [editingMargin, setEditingMargin] = useState(false);
  const [leadInput, setLeadInput] = useState(String(f.settings?.lead_time_days ?? 0));
  const [marginInput, setMarginInput] = useState(String(f.settings?.safety_margin_value ?? 14));
  const [marginUnit, setMarginUnit] = useState<'days' | 'g'>(f.settings?.safety_margin_unit ?? 'days');

  // Sync inputs when remote settings change and the field is not actively being edited.
  useEffect(() => {
    if (!editingLead) setLeadInput(String(f.settings?.lead_time_days ?? 0));
  }, [f.settings?.lead_time_days, editingLead]);
  useEffect(() => {
    if (!editingMargin) {
      setMarginInput(String(f.settings?.safety_margin_value ?? 14));
      setMarginUnit(f.settings?.safety_margin_unit ?? 'days');
    }
  }, [f.settings?.safety_margin_value, f.settings?.safety_margin_unit, editingMargin]);

  const upsertMutation = useMutation({
    mutationFn: api.upsertSkuSettings,
    onSuccess: () => { onSaved(); showToast(t('forecast.settingsSaved'), 'success'); },
    onError: () => showToast(t('forecast.failedSaveSettings'), 'error'),
  });

  const snoozed = f.settings?.alerts_snoozed ?? false;

  const label = [f.group.brand, f.group.material, f.group.subtype].filter(Boolean).join(' ');
  const colorStyle = f.group.spools[0]?.rgba ? `#${f.group.spools[0].rgba.substring(0, 6)}` : '#4B5563';
  const remainPct = f.totalLabelG > 0 ? Math.round((f.totalRemainingG / f.totalLabelG) * 100) : 0;

  const daysColor = snoozed ? 'text-bambu-gray'
    : f.daysRemaining === null ? 'text-bambu-gray'
    : f.stockBreakAlert ? 'text-red-400'
    : f.reorderAlert ? 'text-yellow-400'
    : f.daysRemaining < 30 ? 'text-yellow-400'
    : 'text-green-400';

  function upsert(lead: number, marginVal: number, marginUnitArg: 'days' | 'g', alertsSnoozed = snoozed) {
    upsertMutation.mutate({ material: f.group.material, subtype: f.group.subtype, brand: f.group.brand, lead_time_days: lead, safety_margin_value: marginVal, safety_margin_unit: marginUnitArg, alerts_snoozed: alertsSnoozed });
  }

  function toggleSnooze(e: React.MouseEvent) {
    e.stopPropagation();
    upsert(f.settings?.lead_time_days ?? 0, f.settings?.safety_margin_value ?? 14, f.settings?.safety_margin_unit ?? 'days', !snoozed);
  }

  const tierBadge = f.rateTier === 'history'
    ? <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-bambu-green/15 text-bambu-green"><span className="w-1.5 h-1.5 rounded-full bg-bambu-green" />{t('forecast.trend')}</span>
    : f.rateTier === 'delta'
    ? <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-blue-400/15 text-blue-400"><span className="w-1.5 h-1.5 rounded-full bg-blue-400" />{t('forecast.estimated')}</span>
    : <span className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-bambu-dark-tertiary text-bambu-gray/60"><span className="w-1.5 h-1.5 rounded-full bg-bambu-gray/40" />{t('forecast.noData')}</span>;

  const rowAlertBorder = snoozed ? '' : f.stockBreakAlert ? 'bg-red-500/5' : f.reorderAlert ? 'bg-yellow-500/5' : '';

  return (
    <>
      <tr
        className={`cursor-pointer hover:bg-bambu-dark-tertiary/40 transition-colors ${rowAlertBorder} ${snoozed ? 'opacity-50' : ''}`}
        onClick={() => setExpanded((e) => !e)}
      >
        {/* Color dot */}
        <td className="px-4 py-3">
          <span
            className="block w-3 h-3 rounded-full border border-black/20"
            style={{ backgroundColor: colorStyle }}
          />
        </td>

        {/* SKU */}
        <td className="px-4 py-3">
          <div className="text-sm font-medium text-white">{label}</div>
          <div className="text-xs text-bambu-gray">{t('forecast.spoolCount', { count: f.totalSpools })}</div>
        </td>

        {/* Stock */}
        <td className="px-4 py-3 min-w-[120px]">
          <div className="h-1.5 bg-bambu-dark-tertiary rounded-full overflow-hidden mb-1 w-24">
            <div
              className={`h-full rounded-full ${remainPct > 50 ? 'bg-bambu-green' : remainPct > 20 ? 'bg-yellow-500' : 'bg-red-500'}`}
              style={{ width: `${Math.min(remainPct, 100)}%` }}
            />
          </div>
          <span className="text-xs text-bambu-gray">{Math.round(f.totalRemainingG)}g</span>
        </td>

        {/* Rate */}
        <td className="px-4 py-3">
          <div className="text-sm text-white">{f.dailyRateG !== null ? `${f.dailyRateG.toFixed(1)}g/d` : '—'}</div>
          <div className="mt-0.5">{tierBadge}</div>
        </td>

        {/* Days left */}
        <td className="px-4 py-3">
          <span className={`text-sm font-semibold ${daysColor}`}>
            {f.daysRemaining !== null ? `${f.daysRemaining}d` : <span className="text-bambu-gray font-normal">—</span>}
          </span>
        </td>

        {/* Empty by */}
        <td className="px-4 py-3">
          <span className="text-sm text-bambu-gray">
            {f.projectedEmptyDate ? formatDate(f.projectedEmptyDate) : '—'}
          </span>
        </td>

        {/* Reorder by */}
        <td className="px-4 py-3">
          <span className={`text-sm font-medium ${!snoozed && f.reorderAlert ? 'text-yellow-400' : 'text-bambu-gray'}`}>
            {f.reorderTriggerDate ? formatDate(f.reorderTriggerDate) : '—'}
          </span>
        </td>

        {/* Actions */}
        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-end gap-1">
            {canWrite && (
              <button
                onClick={onCart}
                className="p-1.5 text-bambu-gray hover:text-bambu-green rounded transition-colors"
                title={t('forecast.addToCart')}
              >
                <ShoppingCart className="w-4 h-4" />
              </button>
            )}
            {!snoozed && (f.stockBreakAlert ? (
              <AlertTriangle className="w-4 h-4 text-red-400" aria-label={t('forecast.stockBreakRisk')} />
            ) : f.reorderAlert ? (
              <AlertTriangle className="w-4 h-4 text-yellow-400" aria-label={t('forecast.reorderNow')} />
            ) : f.daysRemaining !== null ? (
              <Check className="w-4 h-4 text-bambu-green/50" />
            ) : null)}
            {canWrite && (
              <button
                onClick={toggleSnooze}
                className={`p-1 rounded transition-colors ${snoozed ? 'text-bambu-gray/70 hover:text-white' : 'text-bambu-dark-tertiary hover:text-bambu-gray'}`}
                title={t(snoozed ? 'forecast.alertsEnabled' : 'forecast.alertsSnoozed')}
              >
                <BellOff className="w-3.5 h-3.5" />
              </button>
            )}
            <button
              onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
              className="p-1.5 text-bambu-gray hover:text-white rounded transition-colors"
            >
              {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
          </div>
        </td>
      </tr>

      {/* ── Expanded detail row ── */}
      {expanded && (
        <tr className="bg-bambu-dark-tertiary/10">
          <td colSpan={8} className="px-6 py-4">
            <div className="space-y-4">

              {/* Logistics summary */}
              <div className="grid grid-cols-3 gap-3">
                <LogisticStat
                  label={t('forecast.effectiveLeadTime')}
                  value={`${f.effectiveLeadTimeDays}d`}
                  hint={t('forecast.effectiveLeadTimeHint', { global: globalLeadTime, sku: f.settings?.lead_time_days ?? 0 })}
                />
                <LogisticStat
                  label={t('forecast.safetyMarginLabel')}
                  value={`${Math.round(f.safetyStockG)}g`}
                  hint={t('forecast.safetyMarginHint')}
                />
                <LogisticStat
                  label={t('forecast.reorderPoint')}
                  value={`${Math.round(f.reorderPointG)}g`}
                  hint={t('forecast.reorderPointHint')}
                />
              </div>

              {/* Per-SKU settings — write-gated */}
              {canWrite && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <SettingField
                    label={t('forecast.skuLeadTimeOverride')}
                    hint={t('forecast.skuLeadTimeHint')}
                    unit={t('forecast.leadTime')}
                    editing={editingLead}
                    value={f.settings?.lead_time_days ?? 0}
                    inputValue={leadInput}
                    onInputChange={setLeadInput}
                    onEdit={() => { setLeadInput(String(f.settings?.lead_time_days ?? 0)); setEditingLead(true); }}
                    onSave={() => {
                      const v = parseInt(leadInput, 10);
                      if (!isNaN(v) && v >= 0) { upsert(v, f.settings?.safety_margin_value ?? 14, marginUnit); setEditingLead(false); }
                    }}
                    onCancel={() => setEditingLead(false)}
                    isPending={upsertMutation.isPending}
                    saveLabel={t('forecast.save')}
                    cancelLabel={t('forecast.cancel')}
                  />
                  <SafetyMarginField
                    value={f.settings?.safety_margin_value ?? 14}
                    unit={marginUnit}
                    editing={editingMargin}
                    inputValue={marginInput}
                    dailyRateG={f.dailyRateG}
                    onInputChange={setMarginInput}
                    onUnitChange={(u) => setMarginUnit(u)}
                    onEdit={() => { setMarginInput(String(f.settings?.safety_margin_value ?? 14)); setMarginUnit(f.settings?.safety_margin_unit ?? 'days'); setEditingMargin(true); }}
                    onSave={() => {
                      const v = parseInt(marginInput, 10);
                      if (!isNaN(v) && v >= 0) { upsert(f.settings?.lead_time_days ?? 0, v, marginUnit); setEditingMargin(false); }
                    }}
                    onCancel={() => setEditingMargin(false)}
                    isPending={upsertMutation.isPending}
                    saveLabel={t('forecast.save')}
                    cancelLabel={t('forecast.cancel')}
                    safetyMarginLabel={t('forecast.safetyMarginLabel')}
                  />
                </div>
              )}

              {/* Individual spools — shown when group has >1 spool */}
              {f.group.spools.length > 1 && (
                <div className="border-t border-bambu-dark-tertiary pt-3">
                  <p className="text-xs text-bambu-gray mb-2">{t('forecast.individualSpools')}</p>
                  <div className="bg-bambu-dark-secondary rounded-lg overflow-hidden border border-bambu-dark-tertiary">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b border-bambu-dark-tertiary bg-bambu-dark-tertiary/30">
                          <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">#</th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('inventory.remaining')}</th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('inventory.used')}</th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('forecast.labelWeight')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-bambu-dark-tertiary">
                        {f.group.spools.map((s) => {
                          const remaining = Math.max(0, s.label_weight - s.weight_used);
                          const pct = s.label_weight > 0 ? (remaining / s.label_weight) * 100 : 0;
                          return (
                            <tr key={s.id} className="hover:bg-bambu-dark-tertiary/30 transition-colors">
                              <td className="px-4 py-2">
                                <span className="text-xs font-mono text-bambu-gray/70">#{s.id}</span>
                              </td>
                              <td className="px-4 py-2">
                                <div className="flex items-center gap-3">
                                  <div className="w-24 h-1.5 bg-bambu-dark-tertiary rounded-full overflow-hidden flex-shrink-0">
                                    <div
                                      className={`h-full rounded-full ${pct > 50 ? 'bg-bambu-green' : pct > 20 ? 'bg-yellow-500' : 'bg-red-500'}`}
                                      style={{ width: `${Math.min(pct, 100)}%` }}
                                    />
                                  </div>
                                  <span className="text-sm text-white">{Math.round(remaining)}g</span>
                                </div>
                              </td>
                              <td className="px-4 py-2">
                                <span className="text-sm text-bambu-gray">{Math.round(s.weight_used)}g</span>
                              </td>
                              <td className="px-4 py-2">
                                <span className="text-sm text-bambu-gray">{s.label_weight}g</span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Logistic stat chip ────────────────────────────────────────────────────────

function LogisticStat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="bg-bambu-dark-tertiary/40 rounded-lg p-3" title={hint}>
      <div className="text-xs text-bambu-gray mb-1">{label}</div>
      <div className="text-lg font-semibold text-white">{value}</div>
    </div>
  );
}

// ── Setting field ─────────────────────────────────────────────────────────────

function SettingField({
  label, hint, unit, editing, value, inputValue,
  onInputChange, onEdit, onSave, onCancel, isPending,
  saveLabel = 'Save', cancelLabel = 'Cancel',
}: {
  label: string; hint: string; unit: string; editing: boolean;
  value: number; inputValue: string;
  onInputChange: (v: string) => void; onEdit: () => void;
  onSave: () => void; onCancel: () => void; isPending: boolean;
  saveLabel?: string; cancelLabel?: string;
}) {
  return (
    <div className="bg-bambu-dark-tertiary/40 rounded-lg p-3 space-y-1">
      <div className="flex items-center gap-1.5">
        <span className="text-xs font-medium text-white">{label}</span>
        <span title={hint}><Info className="w-3 h-3 text-bambu-gray/50" /></span>
      </div>
      {editing ? (
        <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); onSave(); }}>
          <input
            type="number" min={0} max={365}
            value={inputValue} onChange={(e) => onInputChange(e.target.value)}
            className="w-20 px-2 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-sm text-white focus:outline-none focus:border-bambu-green"
            autoFocus disabled={isPending}
          />
          <span className="text-xs text-bambu-gray">{unit}</span>
          <button type="submit" disabled={isPending} className="px-2 py-1 bg-bambu-green text-white text-xs rounded hover:bg-bambu-green/80 disabled:opacity-50">{saveLabel}</button>
          <button type="button" onClick={onCancel} disabled={isPending} className="px-2 py-1 text-xs text-bambu-gray hover:text-white">{cancelLabel}</button>
        </form>
      ) : (
        <div className="flex items-center gap-2">
          <span className="text-lg font-semibold text-white">{value}</span>
          <span className="text-xs text-bambu-gray">{unit}</span>
          <button onClick={onEdit} className="p-1 text-bambu-gray hover:text-white rounded transition-colors"><Edit2 className="w-3 h-3" /></button>
        </div>
      )}
    </div>
  );
}

// ── Safety margin field (dual unit: days | grams) ────────────────────────────

function SafetyMarginField({
  value, unit, editing, inputValue, dailyRateG,
  onInputChange, onUnitChange, onEdit, onSave, onCancel, isPending,
  saveLabel = 'Save', cancelLabel = 'Cancel', safetyMarginLabel = 'Safety Margin',
}: {
  value: number; unit: 'days' | 'g'; editing: boolean; inputValue: string;
  dailyRateG: number | null;
  onInputChange: (v: string) => void; onUnitChange: (u: 'days' | 'g') => void;
  onEdit: () => void; onSave: () => void; onCancel: () => void; isPending: boolean;
  saveLabel?: string; cancelLabel?: string; safetyMarginLabel?: string;
}) {
  const { t } = useTranslation();
  const displayG = unit === 'g' ? value : (dailyRateG !== null ? Math.round(dailyRateG * value) : null);
  const hint = unit === 'days'
    ? t('forecast.safetyMarginHintDays', {
        approx: displayG !== null ? t('forecast.safetyMarginHintDaysApprox', { g: displayG }) : '',
      })
    : t('forecast.safetyMarginHintG', {
        approx: dailyRateG !== null ? t('forecast.safetyMarginHintGApprox', { days: Math.round(value / dailyRateG) }) : '',
      });

  return (
    <div className="bg-bambu-dark-tertiary/40 rounded-lg p-3 space-y-1">
      <div className="flex items-center gap-1.5">
        <span className="text-xs font-medium text-white">{safetyMarginLabel}</span>
        <span title={hint}><Info className="w-3 h-3 text-bambu-gray/50" /></span>
      </div>
      {editing ? (
        <form className="flex items-center gap-2 flex-wrap" onSubmit={(e) => { e.preventDefault(); onSave(); }}>
          <input
            type="number" min={0} max={unit === 'g' ? 10000 : 365}
            value={inputValue} onChange={(e) => onInputChange(e.target.value)}
            className="w-20 px-2 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-sm text-white focus:outline-none focus:border-bambu-green"
            autoFocus disabled={isPending}
          />
          {/* Unit toggle */}
          <div className="flex bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded overflow-hidden text-xs">
            <button type="button" onClick={() => onUnitChange('days')} className={`px-2 py-1 transition-colors ${unit === 'days' ? 'bg-bambu-green text-white' : 'text-bambu-gray hover:text-white'}`}>days</button>
            <button type="button" onClick={() => onUnitChange('g')} className={`px-2 py-1 transition-colors ${unit === 'g' ? 'bg-bambu-green text-white' : 'text-bambu-gray hover:text-white'}`}>g</button>
          </div>
          <button type="submit" disabled={isPending} className="px-2 py-1 bg-bambu-green text-white text-xs rounded hover:bg-bambu-green/80 disabled:opacity-50">{saveLabel}</button>
          <button type="button" onClick={onCancel} disabled={isPending} className="px-2 py-1 text-xs text-bambu-gray hover:text-white">{cancelLabel}</button>
        </form>
      ) : (
        <div className="flex items-center gap-2">
          <span className="text-lg font-semibold text-white">{value}</span>
          <span className="text-xs text-bambu-gray">{unit}</span>
          {displayG !== null && unit === 'days' && (
            <span className="text-xs text-bambu-gray/60">≈ {displayG}g</span>
          )}
          {unit === 'g' && dailyRateG !== null && (
            <span className="text-xs text-bambu-gray/60">≈ {Math.round(value / dailyRateG)}d</span>
          )}
          <button onClick={onEdit} className="p-1 text-bambu-gray hover:text-white rounded transition-colors"><Edit2 className="w-3 h-3" /></button>
        </div>
      )}
    </div>
  );
}

// ── Shopping list panel ───────────────────────────────────────────────────────

function ShoppingListPanel({
  items, forecasts, globalLeadTime, canWrite, onClose, onRemove, onClear,
}: {
  items: ShoppingListItem[];
  forecasts: SkuForecast[];
  globalLeadTime: number;
  canWrite: boolean;
  onClose: () => void;
  onRemove: (id: number) => void;
  onClear: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [view, setView] = useState<'list' | 'logistics'>('list');

  const statusMutation = useMutation({
    mutationFn: async ({ id, status, item, avgSpoolG }: {
      id: number;
      status: 'pending' | 'purchased' | 'received';
      item?: ShoppingListItem;
      avgSpoolG?: number;
    }) => {
      await api.updateShoppingListStatus(id, status);
      if (status === 'received' && item) {
        // Add received spools to stock category
        const spoolWeight = avgSpoolG ?? 1000;
        const spoolBase: Parameters<typeof api.bulkCreateSpools>[0] = {
          material: item.material,
          subtype: item.subtype,
          brand: item.brand,
          label_weight: spoolWeight,
          core_weight: 0,
          core_weight_catalog_id: null,
          color_name: null, rgba: null, extra_colors: null, effect_type: null,
          nozzle_temp_min: null, nozzle_temp_max: null,
          note: item.note ?? null,
          tag_uid: null, tray_uuid: null,
          data_origin: 'manual', tag_type: null,
          cost_per_kg: null,
          last_scale_weight: null, last_weighed_at: null,
          weight_used: 0,
          slicer_filament: null, slicer_filament_name: null,
          added_full: null, last_used: null, encode_time: null,
          category: 'Stock',
          low_stock_threshold_pct: null,
        };
        await api.bulkCreateSpools(spoolBase, item.quantity_spools);
        await api.removeFromShoppingList(id);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['shopping-list'] });
      queryClient.invalidateQueries({ queryKey: ['spools'] });
    },
  });

  // Build a forecast lookup keyed by (material||subtype||brand)
  const forecastMap = useMemo(() => {
    const m = new Map<string, SkuForecast>();
    for (const f of forecasts) m.set(f.group.key, f);
    return m;
  }, [forecasts]);

  // Resolve a forecast for each cart item
  const cartForecasts = useMemo(() =>
    items.map((item) => ({
      item,
      forecast: forecastMap.get(skuKey(item.material, item.subtype, item.brand)) ?? null,
    })),
    [items, forecastMap]
  );

  // Items where stock break before replenishment is detected
  const breakAlerts = useMemo(() =>
    cartForecasts.filter(({ forecast: f }) => {
      if (!f || f.dailyRateG === null) return false;
      // Stock runs out before the lead time window ends
      return f.stockBreakAlert || (f.daysRemaining !== null && f.daysRemaining <= f.effectiveLeadTimeDays);
    }),
    [cartForecasts]
  );

  function downloadCsv() {
    const headers = [t('forecast.qty'), t('forecast.material'), 'Brand', 'Subtype', `${t('forecast.weight')} (g)`, `${t('forecast.leadTime')} (d)`, t('forecast.expectedRestock'), t('forecast.status'), t('forecast.note')];
    const rows = items.map((i) => {
      const f = forecastMap.get(skuKey(i.material, i.subtype, i.brand)) ?? null;
      const avgSpoolG = f && f.totalSpools > 0 ? f.totalLabelG / f.totalSpools : 1000;
      const totalWeightG = Math.round(i.quantity_spools * avgSpoolG);
      const lt = f?.effectiveLeadTimeDays ?? globalLeadTime ?? 0;
      const restock = lt > 0 ? formatDate(addDays(new Date(), lt)) : '';
      return [
        i.quantity_spools,
        i.material,
        i.brand ?? '',
        i.subtype ?? '',
        totalWeightG,
        lt || '',
        restock,
        i.status,
        i.note ?? '',
      ].map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',');
    });
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `shopping-list-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 100);
  }

  return (
    <div className="bg-bambu-dark-secondary rounded-lg overflow-hidden border border-bambu-dark-tertiary">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-bambu-dark-tertiary bg-bambu-dark-tertiary/30">
        <div className="flex items-center gap-3">
          <ShoppingCart className="w-4 h-4 text-bambu-green" />
          <h3 className="text-sm font-semibold text-white">{t('forecast.shoppingList')}</h3>
          <span className="text-xs text-bambu-gray">{t('forecast.shoppingListItems', { count: items.length })}</span>
          {/* View toggle */}
          {items.length > 0 && (
            <div className="flex bg-bambu-dark-tertiary rounded-md p-0.5 ml-1">
              <button
                onClick={() => setView('list')}
                className={`flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium rounded transition-colors ${view === 'list' ? 'bg-bambu-dark-secondary text-white shadow' : 'text-bambu-gray hover:text-white'}`}
              >
                <Package className="w-3 h-3" />
                {t('forecast.listView')}
              </button>
              <button
                onClick={() => setView('logistics')}
                className={`flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium rounded transition-colors ${view === 'logistics' ? 'bg-bambu-dark-secondary text-white shadow' : 'text-bambu-gray hover:text-white'}`}
              >
                <BarChart2 className="w-3 h-3" />
                {t('forecast.logisticsView')}
                {breakAlerts.length > 0 && (
                  <span className="w-3.5 h-3.5 rounded-full bg-red-500 text-white text-[9px] font-bold flex items-center justify-center">
                    {breakAlerts.length}
                  </span>
                )}
              </button>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          {items.length > 0 && (
            <>
              <button onClick={downloadCsv} className="flex items-center gap-1.5 text-xs text-bambu-gray hover:text-white transition-colors px-2 py-1 rounded border border-bambu-dark-tertiary hover:bg-bambu-dark-tertiary">
                <Download className="w-3 h-3" />
                {t('forecast.downloadCsv')}
              </button>
              {canWrite && (
                <button onClick={onClear} className="text-xs text-red-400 hover:text-red-300 transition-colors px-2 py-1 rounded border border-red-500/20 hover:bg-red-500/10">
                  {t('forecast.clearAll')}
                </button>
              )}
            </>
          )}
          <button onClick={onClose} className="p-1 text-bambu-gray hover:text-white transition-colors"><X className="w-4 h-4" /></button>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-col items-center py-8 text-bambu-gray">
          <Package className="w-8 h-8 mb-2 opacity-30" />
          <p className="text-sm">{t('forecast.shoppingListEmpty')}</p>
        </div>
      ) : view === 'list' ? (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-bambu-dark-tertiary bg-bambu-dark-tertiary/20">
                <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('forecast.qty')}</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('forecast.material')}</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('forecast.weight')}</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('forecast.leadTime')}</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('forecast.expectedRestock')}</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('forecast.status')}</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('forecast.note')}</th>
                <th className="px-4 py-2 text-right text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('forecast.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-bambu-dark-tertiary">
              {items.map((item) => {
                const lbl = [item.brand, item.material, item.subtype].filter(Boolean).join(' ');
                const hasBreak = breakAlerts.some((a) => a.item.id === item.id);
                const f = forecastMap.get(skuKey(item.material, item.subtype, item.brand)) ?? null;
                const avgSpoolG = f && f.totalSpools > 0 ? f.totalLabelG / f.totalSpools : 1000;
                const totalWeightG = Math.round(item.quantity_spools * avgSpoolG);
                const lt = f?.effectiveLeadTimeDays ?? globalLeadTime ?? 0;
                const restockDate = lt > 0 ? addDays(new Date(), lt) : null;
                const isPurchased = item.status === 'purchased' || item.status === 'received';
                const isReceived = item.status === 'received';
                const isMutating = statusMutation.isPending;

                return (
                  <tr key={item.id} className={`hover:bg-bambu-dark-tertiary/30 transition-colors ${hasBreak && !isPurchased ? 'bg-red-500/5' : ''}`}>
                    {/* Qty */}
                    <td className="px-4 py-2.5">
                      <span className="text-sm font-semibold text-bambu-green">{item.quantity_spools}×</span>
                    </td>
                    {/* Material */}
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-white">{lbl}</span>
                        {hasBreak && !isPurchased && (
                          <AlertTriangle className="w-3.5 h-3.5 text-red-400 flex-shrink-0" aria-label={t('forecast.stockBreakBefore')} />
                        )}
                      </div>
                    </td>
                    {/* Weight */}
                    <td className="px-4 py-2.5">
                      <span className="text-sm text-white">
                        {totalWeightG >= 1000 ? `${(totalWeightG / 1000).toFixed(1)}kg` : `${totalWeightG}g`}
                      </span>
                    </td>
                    {/* Lead time */}
                    <td className="px-4 py-2.5">
                      <span className="text-sm text-bambu-gray">{lt > 0 ? `${lt}d` : '—'}</span>
                    </td>
                    {/* Expected restock */}
                    <td className="px-4 py-2.5">
                      <span className="text-sm text-bambu-gray">
                        {restockDate ? formatDate(restockDate) : '—'}
                      </span>
                    </td>
                    {/* Status badge — read-only */}
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                        isReceived ? 'bg-bambu-green/20 text-bambu-green' :
                        isPurchased ? 'bg-blue-400/20 text-blue-400' :
                        'bg-bambu-dark-tertiary text-bambu-gray'
                      }`}>
                        {isReceived ? t('forecast.received') : isPurchased ? t('forecast.purchased') : t('forecast.pending')}
                      </span>
                    </td>
                    {/* Note */}
                    <td className="px-4 py-2.5">
                      <span className="text-xs text-bambu-gray">{item.note || '—'}</span>
                    </td>
                    {/* Actions */}
                    <td className="px-4 py-2.5">
                      <div className="flex items-center justify-end gap-1">
                        {canWrite && (
                          <>
                            {/* Purchased icon — available when pending */}
                            <button
                              onClick={() => statusMutation.mutate({ id: item.id, status: isPurchased ? 'pending' : 'purchased' })}
                              disabled={isMutating || isReceived}
                              title={isPurchased ? t('forecast.resetToPending') : t('forecast.markPurchased')}
                              className={`p-1 rounded transition-colors disabled:opacity-30 ${
                                isPurchased
                                  ? 'text-blue-400 hover:text-bambu-gray'
                                  : 'text-bambu-gray hover:text-blue-400'
                              }`}
                            >
                              {isPurchased ? <RotateCcw className="w-3.5 h-3.5" /> : <CreditCard className="w-3.5 h-3.5" />}
                            </button>
                            {/* Received icon — available only after purchasing */}
                            <button
                              onClick={() => statusMutation.mutate({ id: item.id, status: 'received', item, avgSpoolG })}
                              disabled={isMutating || !isPurchased || isReceived}
                              title={t('forecast.markReceived')}
                              className="p-1 rounded transition-colors text-bambu-gray hover:text-bambu-green disabled:opacity-30"
                            >
                              <PackageCheck className="w-3.5 h-3.5" />
                            </button>
                            {/* Delete */}
                            <button
                              onClick={() => onRemove(item.id)}
                              className="p-1 text-bambu-gray hover:text-red-400 transition-colors"
                              title={t('forecast.remove')}
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        /* Logistics view — exclude received items */
        <div className="divide-y divide-bambu-dark-tertiary">
          {cartForecasts.filter(({ item }) => item.status !== 'received').map(({ item, forecast }) => (
            <CartLogisticsRow
              key={item.id}
              item={item}
              forecast={forecast}
              globalLeadTime={globalLeadTime}
              canWrite={canWrite}
              onRemove={() => onRemove(item.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Cart logistics row ────────────────────────────────────────────────────────

function CartLogisticsRow({
  item, forecast: f, globalLeadTime, canWrite, onRemove,
}: {
  item: ShoppingListItem;
  forecast: SkuForecast | null;
  globalLeadTime: number;
  canWrite: boolean;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const label = [item.brand, item.material, item.subtype].filter(Boolean).join(' ');

  // Build a timeline showing stock depletion, arrival bump, then post-arrival depletion.
  // Two points are inserted at day `lt` (just-before and just-after arrival) so the
  // chart shows a clean vertical step rather than a smooth interpolated slope.
  const chartData = useMemo(() => {
    if (!f || f.dailyRateG === null || f.dailyRateG <= 0) return null;
    const rate = f.dailyRateG;
    const lt = f.effectiveLeadTimeDays;
    const avgSpoolG = f.totalSpools > 0 ? f.totalLabelG / f.totalSpools : 1000;
    const arrivalG = item.quantity_spools * avgSpoolG;

    const stockAtArrival = Math.max(0, f.totalRemainingG - rate * lt);
    const peakG = stockAtArrival + arrivalG;
    const daysPostArrival = Math.ceil(peakG / rate);
    const clampedMax = Math.min(lt + daysPostArrival + 5, 365);

    type Point = { day: number; label: string; stock: number; rop: number; safetyStock: number; arrival?: boolean };
    const points: Point[] = [];

    for (let d = 0; d <= clampedMax; d++) {
      const dateLabel = formatDateShort(addDays(new Date(), d));
      if (d === lt) {
        // Just before arrival — pre-bump stock level
        points.push({ day: d, label: dateLabel, stock: Math.round(stockAtArrival), rop: Math.round(f.reorderPointG), safetyStock: Math.round(f.safetyStockG) });
        // Just after arrival — post-bump peak (same x label, creates the vertical step)
        points.push({ day: d, label: dateLabel, stock: Math.round(peakG), rop: Math.round(f.reorderPointG), safetyStock: Math.round(f.safetyStockG), arrival: true });
      } else {
        const stock = d < lt
          ? Math.max(0, f.totalRemainingG - rate * d)
          : Math.max(0, peakG - rate * (d - lt));
        points.push({ day: d, label: dateLabel, stock: Math.round(stock), rop: Math.round(f.reorderPointG), safetyStock: Math.round(f.safetyStockG) });
      }
    }

    return { points, lt, maxDays: clampedMax, arrivalG, peakG, stockAtArrival };
  }, [f, item.quantity_spools]);

  // Determine break scenario: stock hits zero before arrival
  const stockBreaksAt = useMemo(() => {
    if (!f || f.dailyRateG === null || f.dailyRateG <= 0) return null;
    const zeroDay = Math.floor(f.totalRemainingG / f.dailyRateG);
    if (zeroDay < f.effectiveLeadTimeDays) return zeroDay;
    return null;
  }, [f]);

  const hasBreak = stockBreaksAt !== null;

  return (
    <div className={`px-4 py-4 ${hasBreak ? 'bg-red-500/5' : ''}`}>
      {/* Row header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 min-w-0">
          {hasBreak
            ? <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0" />
            : <Check className="w-4 h-4 text-bambu-green/60 flex-shrink-0" />
          }
          <span className="text-sm font-medium text-white truncate">{label}</span>
          <span className="text-xs text-bambu-gray flex-shrink-0">{t('forecast.spoolCount', { count: item.quantity_spools })} ordered</span>
        </div>
        {canWrite && (
          <button onClick={onRemove} className="p-1 text-bambu-gray hover:text-red-400 transition-colors flex-shrink-0">
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Break alert */}
      {hasBreak && (
        <div className="mb-3 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-300">
          <span className="font-medium">{t('forecast.stockBreakIn', { days: stockBreaksAt })}</span>
          {' '}{t('forecast.stockRunsOutBefore', { lt: f!.effectiveLeadTimeDays })}
          {f!.dailyRateG !== null && (
            <span> {t('forecast.atRate', { rate: f!.dailyRateG.toFixed(1) })}{' '}
              <span className="font-semibold">{t('forecast.moreSpools', { count: Math.ceil((f!.dailyRateG * f!.effectiveLeadTimeDays - f!.totalRemainingG) / ((f!.totalLabelG / (f!.totalSpools || 1)) || 1000)) })}</span>
              {' '}{t('forecast.bridgeGap')}
            </span>
          )}
        </div>
      )}

      {/* No forecast data */}
      {(!f || f.dailyRateG === null) ? (
        <div className="py-4 text-center text-xs text-bambu-gray">
          {t('forecast.noUsageData')}
        </div>
      ) : chartData ? (
        <>
          {/* Key stats row */}
          <div className="grid grid-cols-5 gap-2 mb-3">
            <div className="bg-bambu-dark-tertiary/40 rounded-lg px-2.5 py-2 text-center">
              <div className="text-xs text-bambu-gray mb-0.5">{t('forecast.stock')}</div>
              <div className="text-sm font-semibold text-white">{Math.round(f.totalRemainingG)}g</div>
            </div>
            <div className="bg-bambu-dark-tertiary/40 rounded-lg px-2.5 py-2 text-center">
              <div className="text-xs text-bambu-gray mb-0.5">{t('forecast.leadTime')}</div>
              <div className="text-sm font-semibold text-white">{f.effectiveLeadTimeDays}d</div>
              <div className="text-[10px] text-bambu-gray/60">max(g:{globalLeadTime}, sku:{f.settings?.lead_time_days ?? 0})</div>
            </div>
            <div className="bg-bambu-dark-tertiary/40 rounded-lg px-2.5 py-2 text-center">
              <div className="text-xs text-bambu-gray mb-0.5">{t('forecast.safetyMarginLabel')}</div>
              <div className="text-sm font-semibold text-white">{Math.round(f.safetyStockG)}g</div>
            </div>
            <div className={`rounded-lg px-2.5 py-2 text-center ${hasBreak ? 'bg-red-500/15' : 'bg-bambu-dark-tertiary/40'}`}>
              <div className="text-xs text-bambu-gray mb-0.5">{t('forecast.daysLeft')}</div>
              <div className={`text-sm font-semibold ${hasBreak ? 'text-red-400' : 'text-green-400'}`}>
                {f.daysRemaining ?? '—'}d
              </div>
            </div>
            {chartData && (
              <div className="bg-bambu-green/15 rounded-lg px-2.5 py-2 text-center">
                <div className="text-xs text-bambu-gray mb-0.5">{t('forecast.onArrival')}</div>
                <div className="text-sm font-semibold text-bambu-green">{Math.round(chartData.arrivalG)}g</div>
                <div className="text-[10px] text-bambu-gray/60">+{t('forecast.spoolCount', { count: item.quantity_spools })}</div>
              </div>
            )}
          </div>

          {/* Chart */}
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={chartData.points} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <defs>
                {/* Pre-arrival fill: red if break, amber if tight, green if ok */}
                <linearGradient id={`cart-pre-${item.id}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={hasBreak ? '#EF4444' : '#1DB954'} stopOpacity={0.25} />
                  <stop offset="95%" stopColor={hasBreak ? '#EF4444' : '#1DB954'} stopOpacity={0.02} />
                </linearGradient>
                {/* Post-arrival fill: always green */}
                <linearGradient id={`cart-post-${item.id}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#1DB954" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#1DB954" stopOpacity={0.03} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" strokeOpacity={0.4} />
              <XAxis
                dataKey="label"
                tick={{ fill: '#6B7280', fontSize: 9 }}
                interval={Math.max(0, Math.ceil(chartData.maxDays / 6) - 1)}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#6B7280', fontSize: 9 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(1)}kg` : `${v}g`}
                width={44}
              />
              <Tooltip
                contentStyle={{ background: '#1a1a2e', border: '1px solid #374151', borderRadius: 8, fontSize: 11 }}
                labelStyle={{ color: '#9CA3AF' }}
                formatter={(value, name) => {
                  if (typeof value !== 'number') return '';
                  if (name === 'stock') return `${value}g — ${t('forecast.stock')}`;
                  if (name === 'rop') return `${value}g — ${t('forecast.reorderPoint')}`;
                  if (name === 'safetyStock') return `${value}g — ${t('forecast.safetyMarginLabel')}`;
                  return `${value}`;
                }}
              />
              {/* Single stock area — linear interpolation renders the vertical step correctly
                  because the two duplicate-label points at arrival day create an instant jump */}
              <Area
                type="linear"
                dataKey="stock"
                stroke="#1DB954"
                strokeWidth={2}
                fill={`url(#cart-post-${item.id})`}
                dot={false}
                activeDot={{ r: 3 }}
              />
              {/* Reorder point */}
              {f.reorderPointG > 0 && (
                <ReferenceLine
                  y={f.reorderPointG}
                  stroke="#F59E0B"
                  strokeDasharray="5 3"
                  strokeOpacity={0.8}
                  label={{ value: 'ROP', position: 'insideTopRight', fill: '#F59E0B', fontSize: 9 }}
                />
              )}
              {/* Safety stock floor */}
              {f.safetyStockG > 0 && (
                <ReferenceLine
                  y={f.safetyStockG}
                  stroke="#6B7280"
                  strokeDasharray="3 3"
                  strokeOpacity={0.6}
                  label={{ value: 'SS', position: 'insideTopRight', fill: '#6B7280', fontSize: 9 }}
                />
              )}
              {/* Arrival / lead-time-end vertical line */}
              <ReferenceLine
                x={formatDateShort(addDays(new Date(), chartData.lt))}
                stroke="#3B82F6"
                strokeWidth={1.5}
                strokeDasharray="4 3"
                strokeOpacity={0.9}
                label={{ value: `+${chartData.arrivalG >= 1000 ? `${(chartData.arrivalG / 1000).toFixed(1)}kg` : `${Math.round(chartData.arrivalG)}g`} arrives (d${chartData.lt})`, position: 'insideTopLeft', fill: '#3B82F6', fontSize: 9 }}
              />
              {/* Stock break — only shown when stock hits zero before arrival */}
              {stockBreaksAt !== null && (
                <ReferenceLine
                  x={formatDateShort(addDays(new Date(), stockBreaksAt))}
                  stroke="#EF4444"
                  strokeWidth={1.5}
                  strokeOpacity={0.9}
                  label={{ value: 'OUT', position: 'insideTopLeft', fill: '#EF4444', fontSize: 9 }}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>

          {/* Legend */}
          <div className="flex flex-wrap items-center gap-3 mt-2 text-[10px] text-bambu-gray">
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t-2 border-yellow-400 border-dashed" /> {t('forecast.ropLabel')}</span>
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t border-bambu-gray border-dashed" /> {t('forecast.safetyStockLegend')}</span>
            <span className="flex items-center gap-1"><span className="inline-block w-4 border-t-2 border-blue-400 border-dashed" /> {t('forecast.stockArrivalLegend')}</span>
            {hasBreak && <span className="flex items-center gap-1 text-red-400"><span className="inline-block w-4 border-t-2 border-red-400" /> {t('forecast.stockoutLegend')}</span>}
          </div>
        </>
      ) : null}
    </div>
  );
}

// ── Add to Cart Modal ─────────────────────────────────────────────────────────

function AddToCartModal({
  forecast: f, onClose, onAdd,
}: {
  forecast: SkuForecast;
  onClose: () => void;
  onAdd: (item: { material: string; subtype: string | null; brand: string | null; quantity_spools: number; note: string | null }) => void;
}) {
  const { t } = useTranslation();
  const label = [f.group.brand, f.group.material, f.group.subtype].filter(Boolean).join(' ');
  const [mode, setMode] = useState<'qty' | 'duration'>('qty');
  const [qty, setQty] = useState('1');
  const [durationDays, setDurationDays] = useState('30');
  const [note, setNote] = useState('');

  const spoolsForDuration = useMemo(() => {
    if (!f.dailyRateG || f.dailyRateG <= 0) return null;
    const neededG = f.dailyRateG * Number(durationDays);
    const avgSpoolG = f.group.spools.length > 0
      ? f.group.spools.reduce((s, sp) => s + sp.label_weight, 0) / f.group.spools.length
      : 1000;
    return Math.ceil(neededG / avgSpoolG);
  }, [f, durationDays]);

  const finalQty = mode === 'qty' ? parseInt(qty, 10) || 1 : (spoolsForDuration ?? 1);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    onAdd({ material: f.group.material, subtype: f.group.subtype, brand: f.group.brand, quantity_spools: finalQty, note: note || null });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-bambu-dark-secondary rounded-2xl border border-bambu-dark-tertiary w-full max-w-sm shadow-2xl">
        <div className="flex items-center justify-between px-5 pt-5 pb-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <ShoppingCart className="w-5 h-5 text-bambu-green" />
            <h2 className="text-base font-semibold text-white">{t('forecast.addToCartTitle')}</h2>
          </div>
          <button onClick={onClose} className="p-1 text-bambu-gray hover:text-white transition-colors"><X className="w-5 h-5" /></button>
        </div>

        <form onSubmit={submit} className="p-5 space-y-4">
          <div className="text-sm text-bambu-gray">{label}</div>

          <div className="flex bg-bambu-dark-tertiary rounded-lg p-0.5">
            <button
              type="button"
              onClick={() => setMode('qty')}
              className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-colors ${mode === 'qty' ? 'bg-bambu-dark-secondary text-white shadow' : 'text-bambu-gray hover:text-white'}`}
            >
              {t('forecast.byQuantity')}
            </button>
            <button
              type="button"
              onClick={() => setMode('duration')}
              className={`flex-1 py-1.5 text-xs font-medium rounded-md transition-colors ${mode === 'duration' ? 'bg-bambu-dark-secondary text-white shadow' : 'text-bambu-gray hover:text-white'}`}
            >
              {t('forecast.byDuration')}
            </button>
          </div>

          {mode === 'qty' ? (
            <div className="space-y-1.5">
              <label className="text-xs text-bambu-gray">{t('forecast.numberOfSpools')}</label>
              <input
                type="number" min={1} max={99}
                value={qty} onChange={(e) => setQty(e.target.value)}
                className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
                autoFocus
              />
            </div>
          ) : (
            <div className="space-y-2">
              <div className="space-y-1.5">
                <label className="text-xs text-bambu-gray">{t('forecast.lastHowManyDays')}</label>
                <input
                  type="number" min={1} max={365}
                  value={durationDays} onChange={(e) => setDurationDays(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
                  autoFocus
                />
              </div>
              {f.dailyRateG !== null ? (
                <div className="flex items-center gap-2 px-3 py-2 bg-bambu-dark-tertiary/50 rounded-lg">
                  <span className="text-xs text-bambu-gray">≈</span>
                  <span className="text-sm font-semibold text-bambu-green">{t('forecast.spoolCount', { count: spoolsForDuration ?? 0 })}</span>
                  <span className="text-xs text-bambu-gray">at {f.dailyRateG.toFixed(1)}g/day</span>
                </div>
              ) : (
                <div className="text-xs text-yellow-400 px-1">{t('forecast.noUsageQty')}</div>
              )}
            </div>
          )}

          <div className="space-y-1.5">
            <label className="text-xs text-bambu-gray">{t('forecast.noteOptional')}</label>
            <input
              type="text" maxLength={200}
              value={note} onChange={(e) => setNote(e.target.value)}
              placeholder={t('forecast.notePlaceholder')}
              className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/40 focus:outline-none focus:border-bambu-green"
            />
          </div>

          <div className="flex items-center gap-3 pt-1">
            <button
              type="submit"
              className="flex-1 py-2 bg-bambu-green text-white text-sm font-medium rounded-lg hover:bg-bambu-green/80 transition-colors"
            >
              {t('forecast.addNSpools', { count: finalQty })}
            </button>
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-bambu-gray hover:text-white border border-bambu-dark-tertiary rounded-lg transition-colors">
              {t('forecast.cancel')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Column headers (re-exported for InventoryPage) ────────────────────────────

export function ForecastColumnHeaders() {
  return null;
}
