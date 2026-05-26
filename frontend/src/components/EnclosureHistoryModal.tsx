import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Thermometer, Droplets } from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { api, type EnclosureHistoryResponse } from '../api/client';
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

interface EnclosureHistoryModalProps {
  isOpen: boolean;
  onClose: () => void;
  printerId: number;
  printerName: string;
}

export function EnclosureHistoryModal({
  isOpen,
  onClose,
  printerId,
  printerName,
}: EnclosureHistoryModalProps) {
  const { mode: themeMode } = useTheme();
  const { t } = useTranslation();
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

  const { data, isLoading, error } = useQuery<EnclosureHistoryResponse>({
    queryKey: ['enclosure-history', printerId, hours],
    queryFn: () => api.getEnclosureHistory(printerId, hours),
    enabled: isOpen,
    refetchInterval: 60000,
  });

  if (!isOpen) return null;

  const windowStart = Date.now() - hours * 3600 * 1000;
  const windowEnd = Date.now();

  const chartData = (data?.readings ?? []).map(r => ({
    time: (parseUTCDate(r.recorded_at) || new Date()).getTime(),
    temp: r.temp != null ? Math.round(r.temp * 10) / 10 : null,
    humidity: r.humidity != null ? Math.round(r.humidity * 10) / 10 : null,
  }));

  const modalBg = isDark ? '#2d2d2d' : '#ffffff';
  const cardBg = isDark ? '#1d1d1d' : '#f3f4f6';
  const borderColor = isDark ? '#3d3d3d' : '#e5e7eb';
  const textPrimary = isDark ? '#ffffff' : '#111827';
  const textSecondary = isDark ? '#9ca3af' : '#4b5563';
  const gridColor = isDark ? '#3d3d3d' : '#e5e7eb';

  const tempUnit = data?.temp_unit ?? '°C';
  const humUnit = data?.humidity_unit ?? '%';

  const tempValues = chartData.map(d => d.temp).filter((v): v is number => v != null);
  const humValues = chartData.map(d => d.humidity).filter((v): v is number => v != null);

  const tempMin = tempValues.length ? Math.min(...tempValues) : null;
  const tempMax = tempValues.length ? Math.max(...tempValues) : null;
  const humMin = humValues.length ? Math.min(...humValues) : null;
  const humMax = humValues.length ? Math.max(...humValues) : null;

  const tickFormatter = (ts: number) => {
    const date = new Date(ts);
    if (hours > 24) return date.toLocaleDateString([], { day: 'numeric', month: 'short' });
    return date.toLocaleTimeString([], applyTimeFormat({ hour: '2-digit', minute: '2-digit' }, timeFormat));
  };

  const labelFormatter = (ts: number) =>
    new Date(ts).toLocaleString(undefined, applyTimeFormat({
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    }, timeFormat));

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
              <Thermometer className="w-5 h-5" style={{ color: '#4ade80' }} />
              <h2 className="text-lg font-semibold" style={{ color: textPrimary }}>
                {t('printers.enclosure.historyTitle')}
              </h2>
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
                    timeRange === range.value ? 'bg-green-600 text-white' : ''
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
                <Thermometer className="w-3.5 h-3.5" style={{ color: '#4ade80' }} />
                <p className="text-xs" style={{ color: textSecondary }}>{t('printers.enclosure.currentTemp')}</p>
              </div>
              <p className="text-xl font-bold" style={{ color: '#4ade80' }}>
                {data?.current_temp != null ? `${Math.round(data.current_temp * 10) / 10}${tempUnit}` : '—'}
              </p>
            </div>
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <div className="flex items-center gap-1.5 mb-1">
                <Thermometer className="w-3.5 h-3.5" style={{ color: textSecondary }} />
                <p className="text-xs" style={{ color: textSecondary }}>{t('printers.enclosure.minMax')}</p>
              </div>
              <p className="text-xl font-bold" style={{ color: textPrimary }}>
                {tempMin != null && tempMax != null
                  ? `${Math.round(tempMin * 10) / 10} / ${Math.round(tempMax * 10) / 10}`
                  : '—'}
              </p>
            </div>
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <div className="flex items-center gap-1.5 mb-1">
                <Droplets className="w-3.5 h-3.5" style={{ color: '#60a5fa' }} />
                <p className="text-xs" style={{ color: textSecondary }}>{t('printers.enclosure.currentHumidity')}</p>
              </div>
              <p className="text-xl font-bold" style={{ color: '#60a5fa' }}>
                {data?.current_humidity != null ? `${Math.round(data.current_humidity)}${humUnit}` : '—'}
              </p>
            </div>
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <div className="flex items-center gap-1.5 mb-1">
                <Droplets className="w-3.5 h-3.5" style={{ color: textSecondary }} />
                <p className="text-xs" style={{ color: textSecondary }}>{t('printers.enclosure.minMax')}</p>
              </div>
              <p className="text-xl font-bold" style={{ color: textPrimary }}>
                {humMin != null && humMax != null
                  ? `${Math.round(humMin)} / ${Math.round(humMax)}`
                  : '—'}
              </p>
            </div>
          </div>

          {/* Temperature chart */}
          <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
            <p className="text-xs uppercase tracking-wider mb-3 font-medium" style={{ color: textSecondary }}>
              {t('printers.enclosure.tempChartLabel', { unit: tempUnit })}
            </p>
            {isLoading ? (
              <div className="h-[160px] flex items-center justify-center" style={{ color: textSecondary }}>Loading...</div>
            ) : error ? (
              <div className="h-[160px] flex items-center justify-center text-red-500">Error loading data</div>
            ) : (
              <ResponsiveContainer width="100%" height={160}>
                <AreaChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                  <XAxis
                    dataKey="time"
                    type="number"
                    domain={[windowStart, windowEnd]}
                    tickFormatter={tickFormatter}
                    stroke={textSecondary}
                    tick={{ fontSize: 11 }}
                  />
                  <YAxis
                    dataKey="temp"
                    stroke={textSecondary}
                    tick={{ fontSize: 11 }}
                    width={36}
                    tickFormatter={v => `${v}°`}
                  />
                  <Tooltip
                    contentStyle={{ backgroundColor: isDark ? '#2d2d2d' : '#ffffff', border: `1px solid ${borderColor}`, borderRadius: '8px', color: textPrimary }}
                    labelFormatter={(ts) => labelFormatter(ts as number)}
                    formatter={(value) => [`${value}${tempUnit}`, t('printers.enclosure.temperature')]}
                  />
                  <Area
                    type="monotone"
                    dataKey="temp"
                    stroke="#4ade80"
                    fill="#4ade80"
                    fillOpacity={0.15}
                    strokeWidth={2}
                    dot={false}
                    connectNulls
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* Humidity chart */}
          <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
            <p className="text-xs uppercase tracking-wider mb-3 font-medium" style={{ color: textSecondary }}>
              {t('printers.enclosure.humidityChartLabel', { unit: humUnit })}
            </p>
            {isLoading ? (
              <div className="h-[160px] flex items-center justify-center" style={{ color: textSecondary }}>Loading...</div>
            ) : error ? (
              <div className="h-[160px] flex items-center justify-center text-red-500">Error loading data</div>
            ) : (
              <ResponsiveContainer width="100%" height={160}>
                <AreaChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                  <XAxis
                    dataKey="time"
                    type="number"
                    domain={[windowStart, windowEnd]}
                    tickFormatter={tickFormatter}
                    stroke={textSecondary}
                    tick={{ fontSize: 11 }}
                  />
                  <YAxis
                    dataKey="humidity"
                    stroke={textSecondary}
                    tick={{ fontSize: 11 }}
                    width={36}
                    tickFormatter={v => `${v}%`}
                  />
                  <Tooltip
                    contentStyle={{ backgroundColor: isDark ? '#2d2d2d' : '#ffffff', border: `1px solid ${borderColor}`, borderRadius: '8px', color: textPrimary }}
                    labelFormatter={(ts) => labelFormatter(ts as number)}
                    formatter={(value) => [`${value}${humUnit}`, t('printers.enclosure.humidity')]}
                  />
                  <Area
                    type="monotone"
                    dataKey="humidity"
                    stroke="#60a5fa"
                    fill="#60a5fa"
                    fillOpacity={0.15}
                    strokeWidth={2}
                    dot={false}
                    connectNulls
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>

          {!isLoading && chartData.length === 0 && (
            <p className="text-sm text-center" style={{ color: textSecondary }}>
              {t('printers.enclosure.noReadings')}
            </p>
          )}

          <p className="text-xs text-center" style={{ color: textSecondary }}>
            {t('printers.enclosure.readingsNote')}
          </p>
        </div>
      </div>
    </div>
  );
}
