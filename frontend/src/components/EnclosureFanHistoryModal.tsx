import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Wind, Clock, Activity, Timer } from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { api, type EnclosureFanHistoryResponse } from '../api/client';
import { parseUTCDate, applyTimeFormat, type TimeFormat } from '../utils/date';
import { useTheme } from '../contexts/ThemeContext';
import { useQuery as useSettingsQuery } from '@tanstack/react-query';

type TimeRange = '6h' | '24h' | '48h' | '7d';

const TIME_RANGES: { value: TimeRange; label: string; hours: number }[] = [
  { value: '6h', label: '6h', hours: 6 },
  { value: '24h', label: '24h', hours: 24 },
  { value: '48h', label: '48h', hours: 48 },
  { value: '7d', label: '7d', hours: 168 },
];

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

interface EnclosureFanHistoryModalProps {
  isOpen: boolean;
  onClose: () => void;
  printerId: number;
  printerName: string;
}

export function EnclosureFanHistoryModal({
  isOpen,
  onClose,
  printerId,
  printerName,
}: EnclosureFanHistoryModalProps) {
  const { t } = useTranslation();
  const { mode: themeMode } = useTheme();
  const isDark = themeMode === 'dark';
  const [timeRange, setTimeRange] = useState<TimeRange>('24h');

  const { data: settings } = useSettingsQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });
  const timeFormat: TimeFormat = settings?.time_format || 'system';

  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [isOpen, onClose]);

  const hours = TIME_RANGES.find(r => r.value === timeRange)?.hours || 24;

  const { data, isLoading, error } = useQuery<EnclosureFanHistoryResponse>({
    queryKey: ['enclosure-fan-history', printerId, hours],
    queryFn: () => api.getEnclosureFanHistory(printerId, hours),
    enabled: isOpen,
    refetchInterval: 60000,
  });

  if (!isOpen) return null;

  // Build a step-function dataset: 0=off, 1=on across the time window
  const windowStart = Date.now() - hours * 3600 * 1000;
  const windowEnd = Date.now();

  const chartData: { time: number; on: number }[] = [];

  if (data && data.runs.length > 0) {
    chartData.push({ time: windowStart, on: 0 });

    for (const run of data.runs) {
      const start = (parseUTCDate(run.started_at) || new Date()).getTime();
      const end = run.ended_at
        ? (parseUTCDate(run.ended_at) || new Date()).getTime()
        : windowEnd;

      // Step up just before start
      if (chartData[chartData.length - 1].time < start - 1) {
        chartData.push({ time: start - 1, on: 0 });
      }
      chartData.push({ time: start, on: 1 });
      chartData.push({ time: end, on: 1 });
      chartData.push({ time: end + 1, on: 0 });
    }

    chartData.push({ time: windowEnd, on: data.is_on ? 1 : 0 });
  } else {
    chartData.push({ time: windowStart, on: 0 });
    chartData.push({ time: windowEnd, on: 0 });
  }

  // Theme styles
  const modalBg = isDark ? '#2d2d2d' : '#ffffff';
  const cardBg = isDark ? '#1d1d1d' : '#f3f4f6';
  const borderColor = isDark ? '#3d3d3d' : '#e5e7eb';
  const textPrimary = isDark ? '#ffffff' : '#111827';
  const textSecondary = isDark ? '#9ca3af' : '#4b5563';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="rounded-xl w-full max-w-3xl max-h-[90vh] overflow-hidden shadow-xl"
        style={{ backgroundColor: modalBg }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b" style={{ borderColor }}>
          <div>
            <div className="flex items-center gap-2">
              <Wind className="w-5 h-5" style={{ color: '#22d3ee' }} />
              <h2 className="text-lg font-semibold" style={{ color: textPrimary }}>
                {t('printers.enclosure.fanHistoryTitle')}
              </h2>
              {data?.is_on != null && (
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                  data.is_on
                    ? 'bg-cyan-500/20 text-cyan-400'
                    : 'bg-gray-500/20 text-gray-400'
                }`}>
                  {data.is_on ? t('printers.enclosure.fanOn') : t('printers.enclosure.fanOff')}
                </span>
              )}
            </div>
            <p className="text-sm mt-0.5" style={{ color: textSecondary }}>{printerName}</p>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg transition-colors" style={{ color: textSecondary }}>
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-6 overflow-y-auto max-h-[calc(90vh-80px)]">
          {/* Time range */}
          <div className="flex justify-end">
            <div className="inline-flex gap-1 rounded-lg p-1" style={{ backgroundColor: cardBg }}>
              {TIME_RANGES.map(range => (
                <button
                  key={range.value}
                  onClick={() => setTimeRange(range.value)}
                  className={`px-3 py-1 text-sm rounded-md transition-colors ${
                    timeRange === range.value ? 'bg-cyan-600 text-white' : ''
                  }`}
                  style={timeRange !== range.value ? { color: textSecondary } : undefined}
                >
                  {range.label}
                </button>
              ))}
            </div>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <div className="flex items-center gap-1.5 mb-1">
                <Clock className="w-3.5 h-3.5" style={{ color: textSecondary }} />
                <p className="text-xs" style={{ color: textSecondary }}>{t('printers.enclosure.fanTotalRuntime')}</p>
              </div>
              <p className="text-xl font-bold" style={{ color: '#22d3ee' }}>
                {data ? formatDuration(data.total_runtime_seconds) : '—'}
              </p>
            </div>
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <div className="flex items-center gap-1.5 mb-1">
                <Activity className="w-3.5 h-3.5" style={{ color: textSecondary }} />
                <p className="text-xs" style={{ color: textSecondary }}>{t('printers.enclosure.fanRuns')}</p>
              </div>
              <p className="text-xl font-bold" style={{ color: textPrimary }}>
                {data?.run_count ?? '—'}
              </p>
            </div>
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <div className="flex items-center gap-1.5 mb-1">
                <Timer className="w-3.5 h-3.5" style={{ color: textSecondary }} />
                <p className="text-xs" style={{ color: textSecondary }}>{t('printers.enclosure.fanAvgRun')}</p>
              </div>
              <p className="text-xl font-bold" style={{ color: textPrimary }}>
                {data?.avg_duration_seconds != null ? formatDuration(data.avg_duration_seconds) : '—'}
              </p>
            </div>
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <div className="flex items-center gap-1.5 mb-1">
                <Timer className="w-3.5 h-3.5" style={{ color: textSecondary }} />
                <p className="text-xs" style={{ color: textSecondary }}>{t('printers.enclosure.fanLongestRun')}</p>
              </div>
              <p className="text-xl font-bold" style={{ color: textPrimary }}>
                {data?.longest_run_seconds != null ? formatDuration(data.longest_run_seconds) : '—'}
              </p>
            </div>
          </div>

          {/* Chart */}
          <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
            {isLoading ? (
              <div className="h-[200px] flex items-center justify-center" style={{ color: textSecondary }}>
                Loading...
              </div>
            ) : error ? (
              <div className="h-[200px] flex items-center justify-center text-red-500">
                Error loading data
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={isDark ? '#3d3d3d' : '#e5e7eb'} />
                  <XAxis
                    dataKey="time"
                    type="number"
                    domain={[windowStart, windowEnd]}
                    tickFormatter={(ts) => {
                      const date = new Date(ts);
                      if (hours > 24) return date.toLocaleDateString([], { day: 'numeric', month: 'short' });
                      return date.toLocaleTimeString([], applyTimeFormat({ hour: '2-digit', minute: '2-digit' }, timeFormat));
                    }}
                    stroke={textSecondary}
                    tick={{ fontSize: 11 }}
                  />
                  <YAxis
                    domain={[0, 1]}
                    ticks={[0, 1]}
                    tickFormatter={(v) => v === 1 ? 'ON' : 'OFF'}
                    stroke={textSecondary}
                    tick={{ fontSize: 11 }}
                    width={36}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: isDark ? '#2d2d2d' : '#ffffff',
                      border: `1px solid ${borderColor}`,
                      borderRadius: '8px',
                      color: textPrimary,
                    }}
                    labelFormatter={(ts) => new Date(ts).toLocaleString(undefined, applyTimeFormat({
                      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                    }, timeFormat))}
                    formatter={(value) => [value === 1 ? 'ON' : 'OFF', 'Fan']}
                  />
                  <Area
                    type="stepAfter"
                    dataKey="on"
                    stroke="#22d3ee"
                    fill="#22d3ee"
                    fillOpacity={0.25}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>

          {data?.run_count === 0 && !isLoading && (
            <p className="text-sm text-center" style={{ color: textSecondary }}>
              {t('printers.enclosure.fanNoActivity')}
            </p>
          )}

          <p className="text-xs text-center" style={{ color: textSecondary }}>
            {t('printers.enclosure.pollNote')}
          </p>
        </div>
      </div>
    </div>
  );
}
