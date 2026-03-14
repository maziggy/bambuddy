import { useQuery } from '@tanstack/react-query';
import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Package,
  Clock,
  CheckCircle,
  XCircle,
  DollarSign,
  Target,
  Zap,
  AlertTriangle,
  TrendingDown,
  FileSpreadsheet,
  FileText,
  Loader2,
  Eye,
  RotateCcw,
  Calculator,
  Calendar,
  ChevronDown,
} from 'lucide-react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { api, type ArchiveSlim } from '../api/client';
import { PrintCalendar } from '../components/PrintCalendar';
import { FilamentTrends } from '../components/FilamentTrends';
import { Dashboard, type DashboardWidget } from '../components/Dashboard';
import { getCurrencySymbol } from '../utils/currency';
import { formatWeight } from '../utils/weight';
import { parseUTCDate, formatDuration } from '../utils/date';
import { MetricToggle, type Metric } from '../components/MetricToggle';

// Timeframe types and helpers
type TimeframePreset = 'today' | 'this-week' | 'this-month' | 'last-7' | 'last-30' | 'last-90' | 'this-year' | 'all-time' | 'custom';

interface TimeframeState {
  preset: TimeframePreset;
  dateFrom: string | undefined; // YYYY-MM-DD
  dateTo: string | undefined;   // YYYY-MM-DD
}

function computeDateRange(preset: TimeframePreset): { dateFrom?: string; dateTo?: string } {
  const now = new Date();
  const y = now.getUTCFullYear(), m = now.getUTCMonth(), d = now.getUTCDate();
  const fmt = (dt: Date) => dt.toISOString().split('T')[0];
  const todayStr = fmt(now);

  switch (preset) {
    case 'today':
      return { dateFrom: todayStr, dateTo: todayStr };
    case 'this-week': {
      const day = now.getUTCDay();
      const start = new Date(Date.UTC(y, m, d - (day === 0 ? 6 : day - 1)));
      return { dateFrom: fmt(start), dateTo: todayStr };
    }
    case 'this-month':
      return { dateFrom: fmt(new Date(Date.UTC(y, m, 1))), dateTo: todayStr };
    case 'last-7':
      return { dateFrom: fmt(new Date(Date.UTC(y, m, d - 6))), dateTo: todayStr };
    case 'last-30':
      return { dateFrom: fmt(new Date(Date.UTC(y, m, d - 29))), dateTo: todayStr };
    case 'last-90':
      return { dateFrom: fmt(new Date(Date.UTC(y, m, d - 89))), dateTo: todayStr };
    case 'this-year':
      return { dateFrom: fmt(new Date(Date.UTC(y, 0, 1))), dateTo: todayStr };
    case 'all-time':
      return { dateFrom: undefined, dateTo: undefined };
    case 'custom':
      return {};
  }
}

const TIMEFRAME_PRESETS: TimeframePreset[] = [
  'today', 'this-week', 'this-month',
  'last-7', 'last-30', 'last-90',
  'this-year', 'all-time',
];

// Constants
const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const HOUR_LABELS = [
  '12am', '1am', '2am', '3am', '4am', '5am',
  '6am', '7am', '8am', '9am', '10am', '11am',
  '12pm', '1pm', '2pm', '3pm', '4pm', '5pm',
  '6pm', '7pm', '8pm', '9pm', '10pm', '11pm',
];

const DURATION_BUCKETS = [
  { key: '<30m', max: 1800 },
  { key: '30m-1h', max: 3600 },
  { key: '1-2h', max: 7200 },
  { key: '2-4h', max: 14400 },
  { key: '4-8h', max: 28800 },
  { key: '8-12h', max: 43200 },
  { key: '12-24h', max: 86400 },
  { key: '24h+', max: Infinity },
];

const RECHARTS_TOOLTIP_STYLE = {
  backgroundColor: '#2d2d2d',
  border: '1px solid #3d3d3d',
  borderRadius: '8px',
};

// Widget Components
function QuickStatsWidget({
  stats,
  currency,
}: {
  stats: {
    total_prints: number;
    successful_prints: number;
    failed_prints: number;
    total_print_time_hours: number;
    total_filament_grams: number;
    total_cost: number;
    total_energy_kwh: number;
    total_energy_cost: number;
  } | undefined;
  currency: string;
}) {
  const { t } = useTranslation();

  const items = [
    { icon: Package, color: 'text-bambu-green', label: t('stats.totalPrints'), value: `${stats?.total_prints || 0}` },
    { icon: Clock, color: 'text-blue-400', label: t('stats.printTime'), value: `${stats?.total_print_time_hours?.toFixed(1) ?? '0'}h` },
    { icon: Package, color: 'text-orange-400', label: t('stats.filamentUsed'), value: formatWeight(stats?.total_filament_grams || 0) },
    { icon: DollarSign, color: 'text-green-400', label: t('stats.filamentCost'), value: `${currency} ${stats?.total_cost?.toFixed(2) ?? '0.00'}` },
    { icon: Zap, color: 'text-yellow-400', label: t('stats.energyUsed'), value: `${stats?.total_energy_kwh?.toFixed(3) ?? '0.000'} kWh` },
    { icon: DollarSign, color: 'text-yellow-500', label: t('stats.energyCost'), value: `${currency} ${stats?.total_energy_cost?.toFixed(2) ?? '0.00'}` },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
      {items.map((item) => (
        <div key={item.label} className="flex items-start gap-3">
          <div className={`p-2 rounded-lg bg-bambu-dark ${item.color}`}>
            <item.icon className="w-5 h-5" />
          </div>
          <div>
            <p className="text-xs text-bambu-gray">{item.label}</p>
            <p className="text-xl font-bold text-white">{item.value}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function SuccessRateWidget({
  stats,
  printerMap,
  size = 1,
}: {
  stats: {
    total_prints: number;
    successful_prints: number;
    failed_prints: number;
    prints_by_printer: Record<string, number>;
  } | undefined;
  printerMap: Map<string, string>;
  size?: 1 | 2 | 4;
}) {
  const { t } = useTranslation();
  const completedAndFailed = (stats?.successful_prints || 0) + (stats?.failed_prints || 0);
  const successRate = completedAndFailed
    ? Math.round((stats!.successful_prints / completedAndFailed) * 100)
    : 0;

  // Scale gauge size based on widget size
  const gaugeSize = size === 1 ? 112 : size === 2 ? 128 : 144;
  const radius = gaugeSize / 2 - 8;
  const circumference = radius * 2 * Math.PI;

  return (
    <div className="flex items-center gap-6">
      <div className="relative flex-shrink-0" style={{ width: gaugeSize, height: gaugeSize }}>
        <svg className="w-full h-full -rotate-90">
          <circle
            cx={gaugeSize / 2}
            cy={gaugeSize / 2}
            r={radius}
            fill="none"
            stroke="#3d3d3d"
            strokeWidth="10"
          />
          <circle
            cx={gaugeSize / 2}
            cy={gaugeSize / 2}
            r={radius}
            fill="none"
            stroke="#00ae42"
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={`${(successRate / 100) * circumference} ${circumference}`}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className={`font-bold text-white ${size >= 2 ? 'text-2xl' : 'text-xl'}`}>{successRate}%</span>
        </div>
      </div>
      <div className="flex-1 min-w-0">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-status-ok flex-shrink-0" />
            <span className="text-sm text-bambu-gray">{t('stats.successful')}</span>
            <span className="text-sm text-white font-medium">{stats?.successful_prints || 0}</span>
          </div>
          <div className="flex items-center gap-2">
            <XCircle className="w-4 h-4 text-status-error flex-shrink-0" />
            <span className="text-sm text-bambu-gray">{t('stats.failed')}</span>
            <span className="text-sm text-white font-medium">{stats?.failed_prints || 0}</span>
          </div>
        </div>
        {/* Show per-printer breakdown when expanded */}
        {size >= 2 && stats?.prints_by_printer && Object.keys(stats.prints_by_printer).length > 0 && (
          <div className="mt-4 pt-4 border-t border-bambu-dark-tertiary">
            <p className="text-xs text-bambu-gray font-medium mb-2">{t('stats.printsByPrinter')}</p>
            <div className={`grid gap-x-6 gap-y-1 ${size === 4 ? 'grid-cols-3' : 'grid-cols-2'}`} style={{ width: 'fit-content' }}>
              {Object.entries(stats.prints_by_printer).map(([printerId, count]) => (
                <div key={printerId} className="flex items-center gap-3 text-sm">
                  <span className="text-bambu-gray truncate max-w-[120px]">
                    {printerMap.get(printerId) || `${t('common.printer')} ${printerId}`}
                  </span>
                  <span className="text-white font-medium">{count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function TimeAccuracyWidget({
  stats,
  printerMap,
  size = 1,
}: {
  stats: {
    average_time_accuracy: number | null;
    time_accuracy_by_printer: Record<string, number> | null;
  } | undefined;
  printerMap: Map<string, string>;
  size?: 1 | 2 | 4;
}) {
  const { t } = useTranslation();
  const accuracy = stats?.average_time_accuracy;

  if (accuracy === null || accuracy === undefined) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-bambu-gray text-center py-4">{t('stats.noTimeAccuracyData')}</p>
      </div>
    );
  }

  // Normalize accuracy for display (100% = perfect, clamp between 50-150 for gauge)
  const displayValue = Math.min(150, Math.max(50, accuracy));
  const normalizedForGauge = ((displayValue - 50) / 100) * 100; // 50-150 -> 0-100

  // Color based on accuracy
  const getColor = (acc: number) => {
    if (acc >= 95 && acc <= 105) return '#00ae42'; // Green - within 5%
    if (acc > 105) return '#3b82f6'; // Blue - faster than expected
    return '#f97316'; // Orange - slower than expected
  };

  const color = getColor(accuracy);
  const deviation = accuracy - 100;

  // Scale gauge size based on widget size
  const gaugeSize = size === 1 ? 112 : size === 2 ? 128 : 144;
  const radius = gaugeSize / 2 - 8;
  const circumference = radius * 2 * Math.PI;

  // Show more printers when expanded
  const maxPrinters = size === 1 ? 3 : size === 2 ? 6 : 999;
  const printerEntries = stats?.time_accuracy_by_printer
    ? Object.entries(stats.time_accuracy_by_printer).slice(0, maxPrinters)
    : [];

  return (
    <div className="flex items-center gap-6">
      <div className="relative flex-shrink-0" style={{ width: gaugeSize, height: gaugeSize }}>
        <svg className="w-full h-full -rotate-90">
          <circle
            cx={gaugeSize / 2}
            cy={gaugeSize / 2}
            r={radius}
            fill="none"
            stroke="#3d3d3d"
            strokeWidth="10"
          />
          <circle
            cx={gaugeSize / 2}
            cy={gaugeSize / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={`${(normalizedForGauge / 100) * circumference} ${circumference}`}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={`font-bold text-white ${size >= 2 ? 'text-2xl' : 'text-xl'}`}>{accuracy.toFixed(0)}%</span>
          <span className={`text-xs ${deviation >= 0 ? 'text-blue-400' : 'text-orange-400'}`}>
            {deviation >= 0 ? '+' : ''}{deviation.toFixed(0)}%
          </span>
        </div>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 text-xs text-bambu-gray">
          <Target className="w-3 h-3 flex-shrink-0" />
          <span>{t('stats.perfectEstimate')}</span>
        </div>
        {printerEntries.length > 0 && (
          <div className={`mt-2 ${size === 4 ? 'grid grid-cols-3 gap-x-6 gap-y-1' : size === 2 ? 'grid grid-cols-2 gap-x-6 gap-y-1' : 'space-y-1'}`} style={{ width: 'fit-content' }}>
            {printerEntries.map(([printerId, acc]) => (
              <div key={printerId} className="flex items-center gap-2 text-xs">
                <span className="text-bambu-gray truncate max-w-[100px]">
                  {printerMap.get(printerId) || `${t('common.printer')} ${printerId}`}
                </span>
                <span className={`font-medium ${
                  acc >= 95 && acc <= 105 ? 'text-status-ok' :
                  acc > 105 ? 'text-blue-400' : 'text-status-warning'
                }`}>
                  {acc.toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function HourlyHeatmap({ printDates, dateFrom, dateTo }: { printDates: string[]; dateFrom: string; dateTo: string }) {
  const { days, hourlyCounts, maxCount } = useMemo(() => {
    const start = new Date(dateFrom + 'T00:00:00');
    const end = new Date(dateTo + 'T00:00:00');
    const days: { key: string; label: string }[] = [];
    const fmtLocal = (d: Date) =>
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const current = new Date(start);
    while (current <= end) {
      days.push({
        key: fmtLocal(current),
        label: current.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }),
      });
      current.setDate(current.getDate() + 1);
    }

    // Count prints per (day, hour)
    const counts: Record<string, number> = {};
    let max = 0;
    printDates.forEach(d => {
      const date = parseUTCDate(d);
      if (!date) return;
      const dayKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
      const k = `${dayKey}-${date.getHours()}`;
      counts[k] = (counts[k] || 0) + 1;
      if (counts[k] > max) max = counts[k];
    });

    return { days, hourlyCounts: counts, maxCount: Math.max(1, max) };
  }, [printDates, dateFrom, dateTo]);

  const getColor = (count: number) => {
    if (count === 0) return 'bg-bambu-dark';
    const intensity = count / maxCount;
    if (intensity <= 0.25) return 'bg-bambu-green/30';
    if (intensity <= 0.5) return 'bg-bambu-green/50';
    if (intensity <= 0.75) return 'bg-bambu-green/75';
    return 'bg-bambu-green';
  };

  const cellSize = 20;
  const gap = 2;

  const dayLabelWidth = 80;

  return (
    <div className="w-full overflow-x-auto">
      <div className="inline-flex flex-col" style={{ gap }}>
        {/* Hour labels row */}
        <div className="flex" style={{ gap, marginLeft: dayLabelWidth + 4 }}>
          {HOUR_LABELS.map((label, i) => (
            <div
              key={i}
              className="text-bambu-gray text-[10px] text-center"
              style={{ width: cellSize, visibility: i % 2 === 0 ? 'visible' : 'hidden' }}
            >
              {label}
            </div>
          ))}
        </div>

        {/* Day rows */}
        {days.map(day => (
          <div key={day.key} className="flex items-center" style={{ gap }}>
            <div
              className="text-bambu-gray text-[10px] flex-shrink-0 truncate"
              style={{ width: dayLabelWidth }}
            >
              {day.label}
            </div>
            {Array.from({ length: 24 }, (_, hour) => {
              const count = hourlyCounts[`${day.key}-${hour}`] || 0;
              return (
                <div
                  key={hour}
                  className={`rounded-sm ${getColor(count)}`}
                  style={{ width: cellSize, height: cellSize }}
                  title={`${day.label} ${HOUR_LABELS[hour]}: ${count} print${count !== 1 ? 's' : ''}`}
                />
              );
            })}
          </div>
        ))}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-2 mt-3 text-bambu-gray text-xs">
        <span>Less</span>
        <div className="flex" style={{ gap }}>
          <div className="rounded-sm bg-bambu-dark" style={{ width: cellSize, height: cellSize }} />
          <div className="rounded-sm bg-bambu-green/30" style={{ width: cellSize, height: cellSize }} />
          <div className="rounded-sm bg-bambu-green/50" style={{ width: cellSize, height: cellSize }} />
          <div className="rounded-sm bg-bambu-green/75" style={{ width: cellSize, height: cellSize }} />
          <div className="rounded-sm bg-bambu-green" style={{ width: cellSize, height: cellSize }} />
        </div>
        <span>More</span>
      </div>
    </div>
  );
}

function PrintActivityWidget({
  printDates,
  size = 2,
  dateFrom,
  dateTo,
}: {
  printDates: string[];
  size?: 1 | 2 | 4;
  dateFrom?: string;
  dateTo?: string;
}) {
  const spanDays = useMemo(() => {
    if (dateFrom && dateTo) {
      return Math.max((new Date(dateTo).getTime() - new Date(dateFrom).getTime()) / 86400000, 0) + 1;
    }
    if (dateFrom) {
      return Math.max((Date.now() - new Date(dateFrom).getTime()) / 86400000, 0) + 1;
    }
    return Infinity;
  }, [dateFrom, dateTo]);

  if (spanDays <= 7 && dateFrom && dateTo) {
    return <HourlyHeatmap printDates={printDates} dateFrom={dateFrom} dateTo={dateTo} />;
  }

  // Calculate months from the timeframe span, fall back to size-based default for all-time
  const sizeDefault = size === 1 ? 3 : size === 2 ? 6 : 12;
  const months = spanDays === Infinity
    ? sizeDefault
    : Math.max(1, Math.ceil(spanDays / 30));
  return <PrintCalendar printDates={printDates} months={months} />;
}

function PrinterStatsWidget({
  stats,
  archives,
  printerMap,
  currency,
}: {
  stats: { prints_by_printer: Record<string, number> } | undefined;
  archives: ArchiveSlim[];
  printerMap: Map<string, string>;
  currency: string;
}) {
  const { t } = useTranslation();
  const [printerMetric, setPrinterMetric] = useState<Metric>('weight');
  const [habitsMetric, setHabitsMetric] = useState<Metric>('weight');

  // Per-printer data
  const printerData = useMemo(() => {
    const map = new Map<string, { prints: number; weight: number; time: number; cost: number }>();
    if (stats?.prints_by_printer) {
      Object.entries(stats.prints_by_printer).forEach(([id, count]) => {
        const entry = map.get(id) || { prints: 0, weight: 0, time: 0, cost: 0 };
        entry.prints = count;
        map.set(id, entry);
      });
    }
    archives.forEach(a => {
      if (!a.printer_id) return;
      const id = String(a.printer_id);
      const entry = map.get(id) || { prints: 0, weight: 0, time: 0, cost: 0 };
      entry.weight += a.filament_used_grams || 0;
      entry.time += a.actual_time_seconds || a.print_time_seconds || 0;
      entry.cost += a.depreciation_cost || 0;
      if (!stats?.prints_by_printer) entry.prints++;
      map.set(id, entry);
    });
    return Array.from(map.entries())
      .map(([id, v]) => ({
        name: printerMap.get(id) || `${t('common.printer')} ${id}`,
        value: printerMetric === 'prints' ? v.prints :
               printerMetric === 'weight' ? Math.round(v.weight) :
               printerMetric === 'cost' ? Math.round(v.cost * 100) / 100 :
               Math.round((v.time / 3600) * 10) / 10,
      }))
      .sort((a, b) => b.value - a.value);
  }, [stats, archives, printerMap, printerMetric, t]);

  // Hourly distribution (time of day)
  const hourlyData = useMemo(() => {
    const hours = Array.from({ length: 24 }, (_, i) => ({
      hour: i,
      label: HOUR_LABELS[i],
      total: 0,
      failures: 0,
    }));

    archives.forEach(a => {
      if (!a.started_at) return;
      const date = parseUTCDate(a.started_at);
      if (!date) return;
      const h = date.getHours();
      hours[h].total++;
      if (a.status === 'failed') {
        hours[h].failures++;
      }
    });

    return hours;
  }, [archives]);

  // Duration distribution
  const durationData = useMemo(() => {
    const counts = DURATION_BUCKETS.map(b => ({ name: b.key, count: 0 }));
    archives.forEach(a => {
      const seconds = a.actual_time_seconds || a.print_time_seconds;
      if (!seconds || seconds <= 0) return;
      for (let i = 0; i < DURATION_BUCKETS.length; i++) {
        if (seconds <= DURATION_BUCKETS[i].max) {
          counts[i].count++;
          break;
        }
      }
    });
    return counts;
  }, [archives]);

  // Habits (avg per day-of-week)
  const habitsData = useMemo(() => {
    const dayValues = [0, 0, 0, 0, 0, 0, 0];
    const weeksSet = new Set<string>();
    archives.forEach(a => {
      const date = parseUTCDate(a.created_at) || new Date(a.created_at);
      let day = date.getDay() - 1;
      if (day < 0) day = 6;
      if (habitsMetric === 'prints') dayValues[day]++;
      else if (habitsMetric === 'weight') dayValues[day] += a.filament_used_grams || 0;
      else dayValues[day] += (a.actual_time_seconds || a.print_time_seconds || 0) / 3600;
      const weekStart = new Date(date);
      weekStart.setDate(date.getDate() - ((date.getDay() + 6) % 7));
      weeksSet.add(`${weekStart.getFullYear()}-${String(weekStart.getMonth() + 1).padStart(2, '0')}-${String(weekStart.getDate()).padStart(2, '0')}`);
    });
    const numWeeks = Math.max(weeksSet.size, 1);
    return DAY_LABELS.map((name, i) => ({
      name,
      avg: Math.round((dayValues[i] / numWeeks) * 10) / 10,
    }));
  }, [archives, habitsMetric]);

  // Printer cost by day of week (depreciation only)
  const printerCostByDay = useMemo(() => {
    const dayValues = [0, 0, 0, 0, 0, 0, 0];
    archives.forEach(a => {
      if (!a.depreciation_cost) return;
      const date = parseUTCDate(a.created_at) || new Date(a.created_at);
      let day = date.getDay() - 1;
      if (day < 0) day = 6;
      dayValues[day] += a.depreciation_cost;
    });
    return DAY_LABELS.map((name, i) => ({
      name,
      cost: Math.round(dayValues[i] * 100) / 100,
    }));
  }, [archives]);

  const hasPrinterCostData = archives.some(a => a.depreciation_cost && a.depreciation_cost > 0);

  const metricStyle = (m: Metric) => ({
    unit: m === 'weight' ? 'g' : m === 'time' ? 'h' : m === 'cost' ? ` ${currency}` : '',
    color: m === 'weight' ? '#00ae42' : m === 'time' ? '#3b82f6' : m === 'cost' ? '#a855f7' : '#f59e0b',
  });

  const ps = metricStyle(printerMetric);
  const pLabel = printerMetric === 'weight' ? t('stats.filamentByWeight') :
                 printerMetric === 'time' ? t('stats.hours') :
                 printerMetric === 'cost' ? t('stats.depreciationCost') :
                 t('common.prints');
  const hs = metricStyle(habitsMetric);
  const hLabel = habitsMetric === 'weight' ? t('stats.avgWeight') : habitsMetric === 'time' ? t('stats.avgTime') : t('stats.avgPrints');

  return (
    <div className="space-y-4">
      {/* By Printer */}
      <div className="bg-bambu-dark rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-sm font-medium text-bambu-gray">{t('stats.printsByPrinter')}</h4>
          <MetricToggle value={printerMetric} onChange={setPrinterMetric} />
        </div>
        {printerData.length > 0 ? (
          <ResponsiveContainer width="100%" height={Math.max(140, printerData.length * 40)}>
            <BarChart data={printerData} layout="vertical" margin={{ left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#3d3d3d" />
              <XAxis type="number" stroke="#9ca3af" tick={{ fontSize: 11 }} unit={ps.unit} />
              <YAxis type="category" dataKey="name" stroke="#9ca3af" tick={{ fontSize: 11 }} width={100} />
              <Tooltip
                contentStyle={RECHARTS_TOOLTIP_STYLE}
                formatter={(v: number | undefined) => [
                  printerMetric === 'weight' ? formatWeight(Number(v ?? 0)) :
                  printerMetric === 'cost' ? `${currency}${(v ?? 0).toFixed(2)}` :
                  `${v ?? 0}${ps.unit}`,
                  pLabel,
                ]}
              />
              <Bar dataKey="value" fill={ps.color} radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-bambu-gray text-center py-4">{t('stats.noPrinterData')}</p>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Print Duration */}
        <div className="bg-bambu-dark rounded-lg p-4">
          <h4 className="text-sm font-medium text-bambu-gray mb-3">{t('stats.printDuration')}</h4>
          {archives.length > 0 ? (
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={durationData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#3d3d3d" />
                <XAxis dataKey="name" stroke="#9ca3af" tick={{ fontSize: 11 }} />
                <YAxis stroke="#9ca3af" tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip contentStyle={RECHARTS_TOOLTIP_STYLE} />
                <Bar dataKey="count" name={t('common.prints')} fill="#00ae42" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-bambu-gray text-center py-4">{t('stats.noArchiveData')}</p>
          )}
        </div>

        {/* Print Habits */}
        <div className="bg-bambu-dark rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-medium text-bambu-gray">{t('stats.printHabits')}</h4>
            <MetricToggle value={habitsMetric} onChange={setHabitsMetric} exclude={['cost']} />
          </div>
          {archives.length > 0 ? (
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={habitsData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#3d3d3d" />
                <XAxis dataKey="name" stroke="#9ca3af" tick={{ fontSize: 11 }} />
                <YAxis stroke="#9ca3af" tick={{ fontSize: 11 }} unit={hs.unit} />
                <Tooltip contentStyle={RECHARTS_TOOLTIP_STYLE} formatter={(v: number | undefined) => [`${v ?? 0}${hs.unit}`, hLabel]} />
                <Bar dataKey="avg" fill={hs.color} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-bambu-gray text-center py-4">{t('stats.noArchiveData')}</p>
          )}
        </div>

        {/* Print Time of Day */}
        <div className="bg-bambu-dark rounded-lg p-4">
          <h4 className="text-sm font-medium text-bambu-gray mb-3">{t('stats.printTimeOfDay')}</h4>
          {archives.length > 0 ? (
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={hourlyData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#3d3d3d" />
                <XAxis dataKey="label" stroke="#9ca3af" tick={{ fontSize: 10 }} interval={5} />
                <YAxis stroke="#9ca3af" tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip contentStyle={RECHARTS_TOOLTIP_STYLE} />
                <Bar dataKey="total" name={t('stats.totalPrints')} fill="#00ae42" radius={[2, 2, 0, 0]} />
                <Bar dataKey="failures" name={t('stats.failed')} fill="#ef4444" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-bambu-gray text-center py-4">{t('stats.noArchiveData')}</p>
          )}
        </div>

        {/* Printer Cost by Day of Week */}
        {hasPrinterCostData && (
          <div className="bg-bambu-dark rounded-lg p-4">
            <h4 className="text-sm font-medium text-bambu-gray mb-3">{t('stats.printerCost')}</h4>
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={printerCostByDay}>
                <CartesianGrid strokeDasharray="3 3" stroke="#3d3d3d" />
                <XAxis dataKey="name" stroke="#9ca3af" tick={{ fontSize: 11 }} />
                <YAxis stroke="#9ca3af" tick={{ fontSize: 11 }} />
                <Tooltip
                  contentStyle={RECHARTS_TOOLTIP_STYLE}
                  formatter={(v: number | undefined) => [`${currency}${(v ?? 0).toFixed(2)}`, t('stats.depreciationCost')]}
                />
                <Bar dataKey="cost" fill="#a855f7" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}

function FilamentTrendsWidget({
  archives,
  currency,
  dateFrom,
  dateTo,
}: {
  archives: Parameters<typeof FilamentTrends>[0]['archives'];
  currency: string;
  dateFrom?: string;
  dateTo?: string;
}) {
  const { t } = useTranslation();
  if (!archives || archives.length === 0) {
    return <p className="text-bambu-gray text-center py-4">{t('stats.noPrintData')}</p>;
  }
  return <FilamentTrends archives={archives} currency={currency} dateFrom={dateFrom} dateTo={dateTo} />;
}

function FailureAnalysisWidget({ size = 1, dateFrom, dateTo }: {
  size?: 1 | 2 | 4;
  dateFrom?: string;
  dateTo?: string;
}) {
  const { t } = useTranslation();
  const hasDateRange = !!(dateFrom || dateTo);
  const { data: analysis, isLoading } = useQuery({
    queryKey: ['failureAnalysis', dateFrom, dateTo],
    queryFn: () => api.getFailureAnalysis({
      ...(hasDateRange ? { dateFrom, dateTo } : { days: 30 }),
    }),
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-4">
        <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
      </div>
    );
  }

  if (!analysis || analysis.total_prints === 0) {
    return <p className="text-bambu-gray text-center py-4">{hasDateRange ? t('stats.noPrintDataInRange') : t('stats.noPrintDataLast30Days')}</p>;
  }

  // Show more reasons when expanded
  const maxReasons = size === 1 ? 5 : size === 2 ? 8 : 999;
  const allReasons = Object.entries(analysis.failures_by_reason).sort(([, a], [, b]) => b - a);
  const topReasons = allReasons.slice(0, maxReasons);
  const hasMore = allReasons.length > maxReasons;

  return (
    <div className={`${size >= 2 ? 'flex gap-8' : 'space-y-4'}`}>
      {/* Summary */}
      <div className={size >= 2 ? 'flex-shrink-0' : ''}>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <AlertTriangle className={`w-5 h-5 ${analysis.failure_rate > 20 ? 'text-status-error' : analysis.failure_rate > 10 ? 'text-status-warning' : 'text-status-ok'}`} />
            <span className={`font-bold text-white ${size >= 2 ? 'text-3xl' : 'text-2xl'}`}>{analysis.failure_rate.toFixed(1)}%</span>
          </div>
        </div>
        <div className="text-sm text-bambu-gray mt-1">
          {t('stats.failedPrintsCount', { failed: analysis.failed_prints, total: analysis.total_prints })}
        </div>
        {/* Trend indicator */}
        {analysis.trend && analysis.trend.length >= 2 && (
          <div className={`${size >= 2 ? 'mt-4' : 'mt-2 pt-2 border-t border-bambu-dark-tertiary'}`}>
            <div className="flex items-center gap-2 text-sm">
              <TrendingDown className={`w-4 h-4 ${
                analysis.trend[analysis.trend.length - 1].failure_rate < analysis.trend[analysis.trend.length - 2].failure_rate
                  ? 'text-status-ok'
                  : 'text-status-error'
              }`} />
              <span className="text-bambu-gray">
                {t('stats.lastWeekRate', { rate: analysis.trend[analysis.trend.length - 1].failure_rate.toFixed(1) })}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Failure Reasons */}
      {topReasons.length > 0 && (
        <div className={`flex-1 ${size >= 2 ? 'border-l border-bambu-dark-tertiary pl-8' : 'pt-2'}`}>
          <p className="text-xs text-bambu-gray font-medium mb-2">
            {size >= 2 ? t('stats.failureReasons') : t('stats.topFailureReasons')}
          </p>
          <div className={`${size === 4 ? 'grid grid-cols-2 gap-x-6 gap-y-1' : 'space-y-1'}`}>
            {topReasons.map(([reason, count]) => (
              <div key={reason} className="flex items-center justify-between text-sm">
                <span className={`text-white truncate ${size === 4 ? 'max-w-[200px]' : 'max-w-[160px]'}`}>
                  {reason || t('common.unknown')}
                </span>
                <span className="text-bambu-gray ml-2">{count}</span>
              </div>
            ))}
          </div>
          {hasMore && (
            <p className="text-xs text-bambu-gray mt-2">
              {t('common.more', { count: allReasons.length - maxReasons })}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function RecordsWidget({ archives, currency }: { archives: ArchiveSlim[]; currency: string }) {
  const { t } = useTranslation();

  const records = useMemo(() => {
    const result: Array<{
      icon: typeof Clock;
      iconColor: string;
      label: string;
      value: string;
      detail: string | null;
    }> = [];

    if (archives.length === 0) return result;

    // Find the archive with the highest value for a given field
    const findMax = (getter: (a: ArchiveSlim) => number | null | undefined): { archive: ArchiveSlim | null; value: number } => {
      let best: ArchiveSlim | null = null;
      let bestVal = 0;
      archives.forEach(a => {
        const v = getter(a);
        if (v && v > bestVal) { bestVal = v; best = a; }
      });
      return { archive: best, value: bestVal };
    };

    const longest = findMax(a => a.actual_time_seconds);
    if (longest.archive) {
      result.push({
        icon: Clock, iconColor: 'text-blue-400', label: t('stats.longestPrint'),
        value: formatDuration(longest.value),
        detail: longest.archive.print_name || null,
      });
    }

    const heaviest = findMax(a => a.filament_used_grams);
    if (heaviest.archive) {
      result.push({
        icon: Package, iconColor: 'text-orange-400', label: t('stats.heaviestPrint'),
        value: formatWeight(heaviest.value),
        detail: heaviest.archive.print_name || null,
      });
    }

    const costliest = findMax(a => a.cost);
    if (costliest.archive) {
      result.push({
        icon: DollarSign, iconColor: 'text-green-400', label: t('stats.mostExpensivePrint'),
        value: `${currency}${costliest.value.toFixed(2)}`,
        detail: costliest.archive.print_name || null,
      });
    }

    // Busiest day
    const dayCounts = new Map<string, number>();
    archives.forEach(a => {
      const date = parseUTCDate(a.created_at) || new Date(a.created_at);
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
      dayCounts.set(key, (dayCounts.get(key) || 0) + 1);
    });
    let busiestDay = '';
    let busiestCount = 0;
    dayCounts.forEach((count, day) => {
      if (count > busiestCount) {
        busiestCount = count;
        busiestDay = day;
      }
    });
    if (busiestCount > 1) {
      result.push({
        icon: Calendar,
        iconColor: 'text-purple-400',
        label: t('stats.busiestDay'),
        value: `${busiestCount} ${t('common.prints')}`,
        detail: (() => { const [y, m, d] = busiestDay.split('-').map(Number); return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); })(),
      });
    }

    // Success streak
    const sorted = [...archives]
      .filter(a => a.status === 'completed' || a.status === 'failed')
      .sort((a, b) => new Date(b.completed_at || b.created_at).getTime() - new Date(a.completed_at || a.created_at).getTime());
    let streak = 0;
    for (const a of sorted) {
      if (a.status === 'completed') streak++;
      else break;
    }
    if (streak > 0) {
      result.push({
        icon: Zap,
        iconColor: 'text-yellow-400',
        label: t('stats.successStreak'),
        value: `${streak}`,
        detail: streak === 1 ? t('stats.streakPrint') : t('stats.streakPrints', { count: streak }),
      });
    }

    return result;
  }, [archives, currency, t]);

  if (records.length === 0) {
    return <p className="text-bambu-gray text-center py-4">{t('stats.noArchiveData')}</p>;
  }

  return (
    <div className="space-y-3">
      {records.map((record, i) => (
        <div key={i} className="flex items-center gap-3">
          <div className={`p-1.5 rounded-lg bg-bambu-dark ${record.iconColor}`}>
            <record.icon className="w-4 h-4" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs text-bambu-gray">{record.label}</p>
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-bold text-white">{record.value}</span>
              {record.detail && (
                <span className="text-xs text-bambu-gray truncate">{record.detail}</span>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function StatsPage() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const [isExporting, setIsExporting] = useState(false);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [dashboardKey, setDashboardKey] = useState(0);
  const [hiddenCount, setHiddenCount] = useState(0);
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [timeframe, setTimeframe] = useState<TimeframeState>(() => {
    try {
      const saved = localStorage.getItem('bambusy-stats-timeframe');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (parsed.preset) return parsed;
      }
    } catch { /* ignore */ }
    return { preset: 'all-time', dateFrom: undefined, dateTo: undefined };
  });
  const [showTimeframePicker, setShowTimeframePicker] = useState(false);

  // Persist timeframe selection
  useEffect(() => {
    localStorage.setItem('bambusy-stats-timeframe', JSON.stringify(timeframe));
  }, [timeframe]);

  const effectiveDateRange = useMemo(() => {
    if (timeframe.preset === 'custom') {
      return { dateFrom: timeframe.dateFrom, dateTo: timeframe.dateTo };
    }
    return computeDateRange(timeframe.preset);
  }, [timeframe]);

  // Read hidden count from localStorage
  useEffect(() => {
    const updateHiddenCount = () => {
      try {
        const saved = localStorage.getItem('bambusy-dashboard-layout-v2');
        if (saved) {
          const layout = JSON.parse(saved);
          setHiddenCount(layout.hidden?.length || 0);
        }
      } catch {
        setHiddenCount(0);
      }
    };
    updateHiddenCount();
    // Listen for storage changes
    window.addEventListener('storage', updateHiddenCount);
    // Also poll for changes (since storage event doesn't fire for same-tab changes)
    const interval = setInterval(updateHiddenCount, 2000);
    return () => {
      window.removeEventListener('storage', updateHiddenCount);
      clearInterval(interval);
    };
  }, [dashboardKey]);

  const { data: stats, isLoading, refetch: refetchStats } = useQuery({
    queryKey: ['archiveStats', effectiveDateRange.dateFrom, effectiveDateRange.dateTo],
    queryFn: () => api.getArchiveStats({
      dateFrom: effectiveDateRange.dateFrom,
      dateTo: effectiveDateRange.dateTo,
    }),
  });

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: archives, refetch: refetchArchives } = useQuery({
    queryKey: ['archivesSlim', effectiveDateRange.dateFrom, effectiveDateRange.dateTo],
    queryFn: () => api.getArchivesSlim(effectiveDateRange.dateFrom, effectiveDateRange.dateTo),
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const handleExport = async (format: 'csv' | 'xlsx') => {
    setShowExportMenu(false);
    setIsExporting(true);
    try {
      const { blob, filename } = await api.exportStats({ format, days: 90 });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      showToast(t('stats.exportDownloaded'));
    } catch {
      showToast(t('stats.exportFailed'), 'error');
    } finally {
      setIsExporting(false);
    }
  };

  const handleRecalculateCosts = async () => {
    setIsRecalculating(true);
    try {
      const result = await api.recalculateCosts();
      await Promise.all([refetchStats(), refetchArchives()]);
      showToast(t('stats.recalculatedCosts', { count: result.updated }));
    } catch {
      showToast(t('stats.recalculateFailed'), 'error');
    } finally {
      setIsRecalculating(false);
    }
  };

  const currency = getCurrencySymbol(settings?.currency || 'USD');
  const printerMap = new Map(printers?.map((p) => [String(p.id), p.name]) || []);
  const printDates = useMemo(() => archives?.map((a) => a.created_at) || [], [archives]);

  if (isLoading) {
    return (
      <div className="p-4 md:p-8">
        <div className="text-center py-12 text-bambu-gray">{t('stats.loadingStats')}</div>
      </div>
    );
  }

  // Define dashboard widgets
  // Sizes: 1 = quarter (1/4), 2 = half (1/2), 4 = full width
  // Widgets can use render functions to receive the current size for responsive content
  const widgets: DashboardWidget[] = [
    {
      id: 'quick-stats',
      title: t('stats.quickStats'),
      component: <QuickStatsWidget stats={stats} currency={currency} />,
      defaultSize: 2,
    },
    {
      id: 'success-rate',
      title: t('stats.successRate'),
      component: (size) => <SuccessRateWidget stats={stats} printerMap={printerMap} size={size} />,
      defaultSize: 1,
    },
    {
      id: 'time-accuracy',
      title: t('stats.timeAccuracy'),
      component: (size) => <TimeAccuracyWidget stats={stats} printerMap={printerMap} size={size} />,
      defaultSize: 1,
    },
    {
      id: 'failure-analysis',
      title: t('stats.failureAnalysis'),
      component: (size) => <FailureAnalysisWidget size={size} dateFrom={effectiveDateRange.dateFrom} dateTo={effectiveDateRange.dateTo} />,
      defaultSize: 1,
    },
    {
      id: 'print-activity',
      title: t('stats.printActivity'),
      component: (size) => <PrintActivityWidget printDates={printDates} size={size} dateFrom={effectiveDateRange.dateFrom} dateTo={effectiveDateRange.dateTo} />,
      defaultSize: 2,
    },
    {
      id: 'records',
      title: t('stats.records'),
      component: <RecordsWidget archives={archives || []} currency={currency} />,
      defaultSize: 1,
    },
    {
      id: 'printer-stats',
      title: t('stats.printerStats'),
      component: <PrinterStatsWidget stats={stats} archives={archives || []} printerMap={printerMap} currency={currency} />,
      defaultSize: 4,
    },
    {
      id: 'filament-trends',
      title: t('stats.filamentTrends'),
      component: <FilamentTrendsWidget archives={archives || []} currency={currency} dateFrom={effectiveDateRange.dateFrom} dateTo={effectiveDateRange.dateTo} />,
      defaultSize: 4,
    },
  ];

  return (
    <div className="p-4 md:p-8">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">{t('stats.title')}</h1>
          <p className="text-bambu-gray">{t('stats.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Hidden widgets button - toggles panel in Dashboard */}
          {hiddenCount > 0 && (
            <Button
              variant="secondary"
              onClick={() => {
                // Toggle the hidden panel in Dashboard by triggering a custom event
                window.dispatchEvent(new CustomEvent('toggle-hidden-panel'));
              }}
            >
              <Eye className="w-4 h-4" />
              {t('stats.hiddenCount', { count: hiddenCount })}
            </Button>
          )}
          {/* Reset Layout */}
          <Button
            variant="secondary"
            onClick={() => {
              localStorage.removeItem('bambusy-dashboard-layout-v2');
              setDashboardKey(prev => prev + 1);
              showToast(t('stats.layoutReset'));
            }}
            disabled={!hasPermission('settings:update')}
            title={!hasPermission('settings:update') ? t('stats.noPermissionResetLayout') : undefined}
          >
            <RotateCcw className="w-4 h-4" />
            {t('stats.resetLayout')}
          </Button>
          {/* Recalculate Costs */}
          <Button
            variant="secondary"
            onClick={handleRecalculateCosts}
            disabled={isRecalculating || !hasPermission('archives:update_all')}
            title={!hasPermission('archives:update_all') ? t('stats.noPermissionRecalculate') : t('stats.recalculateCostsHint')}
          >
            {isRecalculating ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Calculator className="w-4 h-4" />
            )}
            {t('stats.recalculateCosts')}
          </Button>
          {/* Export dropdown */}
          <div className="relative">
            <Button
              variant="secondary"
              onClick={() => setShowExportMenu(!showExportMenu)}
              disabled={isExporting}
            >
              {isExporting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <FileSpreadsheet className="w-4 h-4" />
              )}
              {t('stats.exportStats')}
            </Button>
            {showExportMenu && (
              <div className="absolute right-0 top-full mt-1 w-48 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl z-20">
                <button
                  className="w-full px-4 py-2 text-left text-white hover:bg-bambu-dark-tertiary transition-colors flex items-center gap-2 rounded-t-lg"
                  onClick={() => handleExport('csv')}
                >
                  <FileText className="w-4 h-4" />
                  {t('stats.exportAsCsv')}
                </button>
                <button
                  className="w-full px-4 py-2 text-left text-white hover:bg-bambu-dark-tertiary transition-colors flex items-center gap-2 rounded-b-lg"
                  onClick={() => handleExport('xlsx')}
                >
                  <FileSpreadsheet className="w-4 h-4" />
                  {t('stats.exportAsExcel')}
                </button>
              </div>
            )}
          </div>
          {/* Timeframe Selector */}
          <div className="relative">
            <Button
              variant="secondary"
              onClick={() => setShowTimeframePicker(!showTimeframePicker)}
            >
              <Calendar className="w-4 h-4" />
              {t(`stats.timeframe.${timeframe.preset}`)}
              <ChevronDown className="w-3 h-3" />
            </Button>

            {showTimeframePicker && (
              <>
                <div
                  className="fixed inset-0 z-10"
                  onClick={() => setShowTimeframePicker(false)}
                />
                <div className="absolute right-0 top-full mt-1 w-64 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl z-20 p-2">
                  {TIMEFRAME_PRESETS.map((preset) => (
                    <button
                      key={preset}
                      className={`w-full px-3 py-2 text-left text-sm rounded-md transition-colors ${
                        timeframe.preset === preset
                          ? 'bg-bambu-green text-white'
                          : 'text-white hover:bg-bambu-dark-tertiary'
                      }`}
                      onClick={() => {
                        setTimeframe({ preset, dateFrom: undefined, dateTo: undefined });
                        setShowTimeframePicker(false);
                      }}
                    >
                      {t(`stats.timeframe.${preset}`)}
                    </button>
                  ))}

                  <div className="border-t border-bambu-dark-tertiary my-2" />

                  <button
                    className={`w-full px-3 py-2 text-left text-sm rounded-md transition-colors ${
                      timeframe.preset === 'custom'
                        ? 'bg-bambu-green text-white'
                        : 'text-white hover:bg-bambu-dark-tertiary'
                    }`}
                    onClick={() => setTimeframe(prev => ({ ...prev, preset: 'custom' }))}
                  >
                    {t('stats.timeframe.custom')}
                  </button>

                  {timeframe.preset === 'custom' && (
                    <div className="mt-2 px-1 pb-1 space-y-2">
                      <div>
                        <label className="text-xs text-bambu-gray block mb-1">{t('stats.timeframe.from')}</label>
                        <input
                          type="date"
                          value={timeframe.dateFrom || ''}
                          max={timeframe.dateTo || new Date().toISOString().split('T')[0]}
                          onChange={(e) => setTimeframe(prev => ({ ...prev, dateFrom: e.target.value || undefined }))}
                          className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded-md px-3 py-1.5 text-sm text-white [color-scheme:dark]"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-bambu-gray block mb-1">{t('stats.timeframe.to')}</label>
                        <input
                          type="date"
                          value={timeframe.dateTo || ''}
                          min={timeframe.dateFrom}
                          max={new Date().toISOString().split('T')[0]}
                          onChange={(e) => setTimeframe(prev => ({ ...prev, dateTo: e.target.value || undefined }))}
                          className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded-md px-3 py-1.5 text-sm text-white [color-scheme:dark]"
                        />
                      </div>
                      <Button
                        variant="primary"
                        onClick={() => setShowTimeframePicker(false)}
                        className="w-full"
                      >
                        {t('common.apply')}
                      </Button>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <Dashboard
        key={dashboardKey}
        widgets={widgets}
        storageKey="bambusy-dashboard-layout-v2"
        stackBelow={640}
        hideControls
      />
    </div>
  );
}
