import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X, Droplets, Thermometer, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  ReferenceLine,
} from 'recharts';
import { api, type AMSHistoryResponse } from '../api/client';
import { parseUTCDate, applyTimeFormat, type TimeFormat } from '../utils/date';
import { useTranslation } from 'react-i18next';
import { useTheme } from '../contexts/ThemeContext';

interface AMSHistoryModalProps {
  isOpen: boolean;
  onClose: () => void;
  printerId: number;
  printerName: string;
  amsId: number;
  amsLabel: string;
  initialMode?: 'humidity' | 'temperature';
  thresholds?: {
    humidityGood: number;
    humidityFair: number;
    tempGood: number;
    tempFair: number;
  };
}

type TimeRange = '6h' | '24h' | '48h' | '7d';

const TIME_RANGES: { value: TimeRange; label: string; hours: number }[] = [
  { value: '6h', label: '6h', hours: 6 },
  { value: '24h', label: '24h', hours: 24 },
  { value: '48h', label: '48h', hours: 48 },
  { value: '7d', label: '7d', hours: 168 },
];

export function AMSHistoryModal({
  isOpen,
  onClose,
  printerId,
  printerName,
  amsId,
  amsLabel,
  initialMode = 'humidity',
  thresholds,
}: AMSHistoryModalProps) {
  const { t } = useTranslation();
  const { mode: themeMode } = useTheme();
  const [timeRange, setTimeRange] = useState<TimeRange>('24h');
  const [mode, setMode] = useState<'humidity' | 'temperature'>(initialMode);
  const isDark = themeMode === 'dark';

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const timeFormat: TimeFormat = settings?.time_format || 'system';

  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const hours = TIME_RANGES.find(r => r.value === timeRange)?.hours || 24;

  const { data, isLoading, error } = useQuery<AMSHistoryResponse>({
    queryKey: ['ams-history', printerId, amsId, hours],
    queryFn: () => api.getAMSHistory(printerId, amsId, hours),
    enabled: isOpen,
    refetchInterval: 60000, // Refresh every minute
  });

  if (!isOpen) return null;

  // Format data for chart
  const chartData = data?.data.map(point => {
    const date = parseUTCDate(point.recorded_at) || new Date();
    const timeOptions: Intl.DateTimeFormatOptions = {
      hour: '2-digit',
      minute: '2-digit',
      ...(hours > 24 ? { day: 'numeric', month: 'short' } : {}),
    };
    return {
      time: date.getTime(),
      humidity: point.humidity,
      temperature: point.temperature,
      timeLabel: date.toLocaleTimeString([], applyTimeFormat(timeOptions, timeFormat)),
    };
  }) || [];

  // Get thresholds
  const humidityGood = thresholds?.humidityGood || 40;
  const humidityFair = thresholds?.humidityFair || 60;
  const tempGood = thresholds?.tempGood || 30;
  const tempFair = thresholds?.tempFair || 35;

  // Current values (last data point)
  const lastPoint = chartData[chartData.length - 1];
  const currentHumidity = lastPoint?.humidity;
  const currentTemp = lastPoint?.temperature;

  // Trend calculation (compare first and last 20% of data)
  const getTrend = (values: (number | null)[]) => {
    const filtered = values.filter((v): v is number => v != null);
    if (filtered.length < 4) return 'stable';
    const firstQuarter = filtered.slice(0, Math.floor(filtered.length / 4));
    const lastQuarter = filtered.slice(-Math.floor(filtered.length / 4));
    const firstAvg = firstQuarter.reduce((a, b) => a + b, 0) / firstQuarter.length;
    const lastAvg = lastQuarter.reduce((a, b) => a + b, 0) / lastQuarter.length;
    const diff = lastAvg - firstAvg;
    if (Math.abs(diff) < 2) return 'stable';
    return diff > 0 ? 'up' : 'down';
  };

  const humidityTrend = getTrend(chartData.map(d => d.humidity));
  const tempTrend = getTrend(chartData.map(d => d.temperature));

  const TrendIcon = ({ trend }: { trend: string }) => {
    if (trend === 'up') return <TrendingUp className="w-4 h-4 text-red-400" />;
    if (trend === 'down') return <TrendingDown className="w-4 h-4 text-green-400" />;
    return <Minus className="w-4 h-4 text-gray-400 dark:text-bambu-gray" />;
  };

  // Get status color for current value
  const getHumidityColor = (value: number | undefined | null) => {
    if (value == null) return '#9ca3af';
    if (value <= humidityGood) return '#22a352';
    if (value <= humidityFair) return '#d4a017';
    return '#c62828';
  };

  const getTempColor = (value: number | undefined | null) => {
    if (value == null) return '#9ca3af';
    if (value <= tempGood) return '#22a352';
    if (value <= tempFair) return '#d4a017';
    return '#c62828';
  };

  // Theme-aware styles (using isDark since dark: prefix doesn't work in portals)
  const modalBg = isDark ? '#2d2d2d' : '#ffffff';
  const cardBg = isDark ? '#1d1d1d' : '#f3f4f6';
  const borderColor = isDark ? '#3d3d3d' : '#e5e7eb';
  const textPrimary = isDark ? '#ffffff' : '#111827';
  const textSecondary = isDark ? '#9ca3af' : '#4b5563';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="rounded-xl w-full max-w-4xl max-h-[90vh] overflow-hidden shadow-xl"
        style={{ backgroundColor: modalBg }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-6 py-4 border-b"
          style={{ borderColor }}
        >
          <div>
            <h2 className="text-lg font-semibold" style={{ color: textPrimary }}>
              {amsLabel} {t('common.history', 'History')}
            </h2>
            <p className="text-sm" style={{ color: textSecondary }}>{printerName}</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg transition-colors"
            style={{ color: textSecondary }}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6 overflow-y-auto max-h-[calc(90vh-80px)]">
          {/* Time Range & Mode Selector */}
          <div className="flex items-center justify-between">
            <div className="flex gap-1 rounded-lg p-1" style={{ backgroundColor: cardBg }}>
              <button
                onClick={() => setMode('humidity')}
                className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors ${
                  mode === 'humidity' ? 'bg-blue-600 text-white' : ''
                }`}
                style={mode !== 'humidity' ? { color: textSecondary } : undefined}
              >
                <Droplets className="w-4 h-4" />
                {t('common.humidity', 'Humidity')}
              </button>
              <button
                onClick={() => setMode('temperature')}
                className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors ${
                  mode === 'temperature' ? 'bg-orange-600 text-white' : ''
                }`}
                style={mode !== 'temperature' ? { color: textSecondary } : undefined}
              >
                <Thermometer className="w-4 h-4" />
                {t('common.temperature', 'Temperature')}
              </button>
            </div>

            <div className="flex gap-1 rounded-lg p-1" style={{ backgroundColor: cardBg }}>
              {TIME_RANGES.map(range => (
                <button
                  key={range.value}
                  onClick={() => setTimeRange(range.value)}
                  className={`px-3 py-1 text-sm rounded-md transition-colors ${
                    timeRange === range.value ? 'bg-bambu-green text-white' : ''
                  }`}
                  style={timeRange !== range.value ? { color: textSecondary } : undefined}
                >
                  {range.label}
                </button>
              ))}
            </div>
          </div>

          {/* Stats Cards */}
          <div className="grid grid-cols-4 gap-4">
            {mode === 'humidity' ? (
              <>
                <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
                  <p className="text-xs" style={{ color: textSecondary }}>{t('common.current', 'Current')}</p>
                  <div className="flex items-center gap-2">
                    <p className="text-2xl font-bold" style={{ color: getHumidityColor(currentHumidity) }}>
                      {currentHumidity != null ? `${currentHumidity}%` : '—'}
                    </p>
                    <TrendIcon trend={humidityTrend} />
                  </div>
                </div>
                <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
                  <p className="text-xs" style={{ color: textSecondary }}>{t('common.average', 'Average')}</p>
                  <p className="text-2xl font-bold" style={{ color: textPrimary }}>
                    {data?.avg_humidity != null ? `${data.avg_humidity}%` : '—'}
                  </p>
                </div>
                <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
                  <p className="text-xs" style={{ color: textSecondary }}>{t('common.min', 'Min')}</p>
                  <p className="text-2xl font-bold text-green-500">
                    {data?.min_humidity != null ? `${data.min_humidity}%` : '—'}
                  </p>
                </div>
                <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
                  <p className="text-xs" style={{ color: textSecondary }}>{t('common.max', 'Max')}</p>
                  <p className="text-2xl font-bold text-red-500">
                    {data?.max_humidity != null ? `${data.max_humidity}%` : '—'}
                  </p>
                </div>
              </>
            ) : (
              <>
                <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
                  <p className="text-xs" style={{ color: textSecondary }}>{t('common.current', 'Current')}</p>
                  <div className="flex items-center gap-2">
                    <p className="text-2xl font-bold" style={{ color: getTempColor(currentTemp) }}>
                      {currentTemp != null ? `${currentTemp}°C` : '—'}
                    </p>
                    <TrendIcon trend={tempTrend} />
                  </div>
                </div>
                <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
                  <p className="text-xs" style={{ color: textSecondary }}>{t('common.average', 'Average')}</p>
                  <p className="text-2xl font-bold" style={{ color: textPrimary }}>
                    {data?.avg_temperature != null ? `${data.avg_temperature}°C` : '—'}
                  </p>
                </div>
                <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
                  <p className="text-xs" style={{ color: textSecondary }}>{t('common.min', 'Min')}</p>
                  <p className="text-2xl font-bold text-blue-500">
                    {data?.min_temperature != null ? `${data.min_temperature}°C` : '—'}
                  </p>
                </div>
                <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
                  <p className="text-xs" style={{ color: textSecondary }}>{t('common.max', 'Max')}</p>
                  <p className="text-2xl font-bold text-red-500">
                    {data?.max_temperature != null ? `${data.max_temperature}°C` : '—'}
                  </p>
                </div>
              </>
            )}
          </div>

          {/* Chart */}
          <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
            {isLoading ? (
              <div className="h-[300px] flex items-center justify-center" style={{ color: textSecondary }}>
                {t('common.loading', 'Loading...')}
              </div>
            ) : error ? (
              <div className="h-[300px] flex items-center justify-center text-red-500">
                {t('common.error', 'Error loading data')}
              </div>
            ) : chartData.length === 0 ? (
              <div className="h-[300px] flex items-center justify-center" style={{ color: textSecondary }}>
                {t('common.noData', 'No data available for this time range')}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={isDark ? '#3d3d3d' : '#e5e7eb'} />
                  <XAxis
                    dataKey="time"
                    type="number"
                    domain={['dataMin', 'dataMax']}
                    tickFormatter={(ts) => {
                      const date = new Date(ts);
                      if (hours > 24) {
                        return date.toLocaleDateString([], { day: 'numeric', month: 'short' });
                      }
                      return date.toLocaleTimeString([], applyTimeFormat({ hour: '2-digit', minute: '2-digit' }, timeFormat));
                    }}
                    stroke={isDark ? '#9ca3af' : '#6b7280'}
                    tick={{ fontSize: 12 }}
                  />
                  <YAxis
                    stroke={isDark ? '#9ca3af' : '#6b7280'}
                    tick={{ fontSize: 12 }}
                    domain={mode === 'humidity' ? [0, 100] : ['auto', 'auto']}
                    tickFormatter={(value) => mode === 'humidity' ? `${value}%` : `${value}°C`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: isDark ? '#2d2d2d' : '#ffffff',
                      border: `1px solid ${isDark ? '#3d3d3d' : '#e5e7eb'}`,
                      borderRadius: '8px',
                      color: isDark ? '#fff' : '#000',
                    }}
                    labelFormatter={(ts) => new Date(ts).toLocaleString(undefined, applyTimeFormat({
                      year: 'numeric',
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    }, timeFormat))}
                    formatter={(value) => [
                      mode === 'humidity' ? `${value ?? 0}%` : `${value ?? 0}°C`,
                      mode === 'humidity' ? 'Humidity' : 'Temperature'
                    ]}
                  />
                  <Legend />

                  {/* Threshold lines */}
                  {mode === 'humidity' ? (
                    <>
                      <ReferenceLine y={humidityGood} stroke="#22a352" strokeDasharray="5 5" label={{ value: 'Good', fill: '#22a352', fontSize: 10 }} />
                      <ReferenceLine y={humidityFair} stroke="#d4a017" strokeDasharray="5 5" label={{ value: 'Fair', fill: '#d4a017', fontSize: 10 }} />
                    </>
                  ) : (
                    <>
                      <ReferenceLine y={tempGood} stroke="#22a352" strokeDasharray="5 5" label={{ value: 'Good', fill: '#22a352', fontSize: 10 }} />
                      <ReferenceLine y={tempFair} stroke="#d4a017" strokeDasharray="5 5" label={{ value: 'Fair', fill: '#d4a017', fontSize: 10 }} />
                    </>
                  )}

                  <Line
                    type="monotone"
                    dataKey={mode}
                    name={mode === 'humidity' ? 'Humidity' : 'Temperature'}
                    stroke={mode === 'humidity' ? '#3b82f6' : '#f97316'}
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* Info */}
          <div className="text-xs text-center" style={{ color: textSecondary }}>
            {t('amsHistory.recordingInfo', 'Data is recorded every 5 minutes while the printer is connected')}
          </div>
        </div>
      </div>
    </div>
  );
}
