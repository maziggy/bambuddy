import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X, Flame, Square, Box, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { api, type HeaterSensorKind, type PrinterSensorHistoryResponse } from '../api/client';
import { parseUTCDate, applyTimeFormat, type TimeFormat } from '../utils/date';
import { useTranslation } from 'react-i18next';

interface HeaterHistoryModalProps {
  isOpen: boolean;
  onClose: () => void;
  printerId: number;
  printerName: string;
  initialKind?: HeaterSensorKind;
  availableKinds?: HeaterSensorKind[];
}

type TimeRange = '6h' | '24h' | '48h' | '7d';

const TIME_RANGES: { value: TimeRange; label: string; hours: number }[] = [
  { value: '6h', label: '6h', hours: 6 },
  { value: '24h', label: '24h', hours: 24 },
  { value: '48h', label: '48h', hours: 48 },
  { value: '7d', label: '7d', hours: 168 },
];

const KIND_COLORS: Record<HeaterSensorKind, string> = {
  nozzle: '#fb923c',
  nozzle_2: '#f97316',
  bed: '#60a5fa',
  chamber: '#34d399',
};

const KIND_TARGET_COLORS: Record<HeaterSensorKind, string> = {
  nozzle: '#fed7aa',
  nozzle_2: '#fdba74',
  bed: '#bfdbfe',
  chamber: '#a7f3d0',
};

export function HeaterHistoryModal({
  isOpen,
  onClose,
  printerId,
  printerName,
  initialKind = 'nozzle',
  availableKinds = ['nozzle', 'bed', 'chamber'],
}: HeaterHistoryModalProps) {
  const { t } = useTranslation();
  const [timeRange, setTimeRange] = useState<TimeRange>('24h');
  const [kind, setKind] = useState<HeaterSensorKind>(initialKind);

  useEffect(() => {
    setKind(initialKind);
  }, [initialKind]);

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const timeFormat: TimeFormat = settings?.time_format || 'system';

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const hours = TIME_RANGES.find(r => r.value === timeRange)?.hours || 24;

  const { data, isLoading, error } = useQuery<PrinterSensorHistoryResponse>({
    queryKey: ['printer-sensor-history', printerId, hours, availableKinds.join(',')],
    queryFn: () => api.getPrinterSensorHistory(printerId, hours, availableKinds),
    enabled: isOpen,
    refetchInterval: 60000,
  });

  if (!isOpen) return null;

  const series = data?.series.find(s => s.sensor_kind === kind);

  const rawPoints = (series?.data || []).map(p => {
    const date = parseUTCDate(p.recorded_at) || new Date();
    return {
      time: date.getTime(),
      value: p.value,
      target: p.target,
    };
  });

  const domainStart = Date.now() - hours * 60 * 60 * 1000;
  const domainEnd = Date.now();
  const chartData = [...rawPoints];
  if (chartData.length > 0) {
    const first = chartData[0];
    if (first.time > domainStart) {
      chartData.unshift({ ...first, time: domainStart });
    }
    const last = chartData[chartData.length - 1];
    if (last.time < domainEnd) {
      chartData.push({ ...last, time: domainEnd });
    }
  }

  const lastPoint = chartData[chartData.length - 1];
  const currentValue = lastPoint?.value;
  const currentTarget = lastPoint?.target;

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

  const trend = getTrend(chartData.map(d => d.value));

  const TrendIcon = ({ trend }: { trend: string }) => {
    if (trend === 'up') return <TrendingUp className="w-4 h-4 text-red-600 dark:text-red-400" />;
    if (trend === 'down') return <TrendingDown className="w-4 h-4 text-green-600 dark:text-green-400" />;
    return <Minus className="w-4 h-4 text-gray-400 dark:text-bambu-gray" />;
  };

  const modalBg = 'var(--bg-secondary)';
  const cardBg = 'var(--bg-primary)';
  const borderColor = 'var(--border-color)';
  const textPrimary = 'var(--text-primary)';
  const textSecondary = 'var(--text-secondary)';
  const axisColor = 'var(--text-muted)';

  const kindLabel = (k: HeaterSensorKind) => {
    switch (k) {
      case 'nozzle':
        return t('printers.heaterHistory.nozzle', 'Nozzle');
      case 'nozzle_2':
        return t('printers.heaterHistory.nozzle2', 'Nozzle 2');
      case 'bed':
        return t('printers.heaterHistory.bed', 'Bed');
      case 'chamber':
        return t('printers.heaterHistory.chamber', 'Chamber');
    }
  };

  const KindIcon = ({ k }: { k: HeaterSensorKind }) => {
    if (k === 'nozzle' || k === 'nozzle_2') return <Flame className="w-4 h-4" />;
    if (k === 'bed') return <Square className="w-4 h-4" />;
    return <Box className="w-4 h-4" />;
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="rounded-xl w-full max-w-4xl max-h-[90vh] overflow-hidden shadow-xl"
        style={{ backgroundColor: modalBg }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b" style={{ borderColor }}>
          <div>
            <h2 className="text-lg font-semibold" style={{ color: textPrimary }}>
              {t('printers.heaterHistory.title', 'Heater History')}
            </h2>
            <p className="text-sm" style={{ color: textSecondary }}>{printerName}</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg transition-colors"
            style={{ color: textSecondary }}
            aria-label={t('common.close', 'Close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-6 overflow-y-auto max-h-[calc(90vh-80px)]">
          <div className="flex items-center justify-between max-[550px]:flex-col max-[550px]:items-start max-[550px]:gap-3">
            <div className="inline-flex gap-1 rounded-lg p-1 max-w-full flex-wrap w-fit" style={{ backgroundColor: cardBg }}>
              {availableKinds.map(k => (
                <button
                  key={k}
                  onClick={() => setKind(k)}
                  className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded-md transition-colors ${
                    kind === k ? 'text-white' : ''
                  }`}
                  style={kind === k ? { backgroundColor: KIND_COLORS[k] } : { color: textSecondary }}
                >
                  <KindIcon k={k} />
                  {kindLabel(k)}
                </button>
              ))}
            </div>

            <div className="inline-flex gap-1 rounded-lg p-1 max-w-full flex-wrap w-fit" style={{ backgroundColor: cardBg }}>
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

          <div className="grid grid-cols-4 gap-4 max-[550px]:grid-cols-2">
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <p className="text-xs" style={{ color: textSecondary }}>{t('common.current', 'Current')}</p>
              <div className="flex items-center gap-2">
                <p className="text-2xl font-bold" style={{ color: KIND_COLORS[kind] }}>
                  {currentValue != null ? `${Math.round(currentValue)}°C` : '—'}
                </p>
                <TrendIcon trend={trend} />
              </div>
              {currentTarget != null && currentTarget > 0 && (
                <p className="text-xs mt-1" style={{ color: textSecondary }}>
                  {t('common.target', 'Target')}: {Math.round(currentTarget)}°C
                </p>
              )}
            </div>
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <p className="text-xs" style={{ color: textSecondary }}>{t('common.average', 'Average')}</p>
              <p className="text-2xl font-bold" style={{ color: textPrimary }}>
                {series?.avg_value != null ? `${series.avg_value}°C` : '—'}
              </p>
            </div>
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <p className="text-xs" style={{ color: textSecondary }}>{t('common.min', 'Min')}</p>
              <p className="text-2xl font-bold" style={{ color: textPrimary }}>
                {series?.min_value != null ? `${Math.round(series.min_value)}°C` : '—'}
              </p>
            </div>
            <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
              <p className="text-xs" style={{ color: textSecondary }}>{t('common.max', 'Max')}</p>
              <p className="text-2xl font-bold" style={{ color: textPrimary }}>
                {series?.max_value != null ? `${Math.round(series.max_value)}°C` : '—'}
              </p>
            </div>
          </div>

          <div className="rounded-lg p-4" style={{ backgroundColor: cardBg }}>
            {isLoading ? (
              <div className="h-64 flex items-center justify-center" style={{ color: textSecondary }}>
                {t('common.loading', 'Loading...')}
              </div>
            ) : error ? (
              <div className="h-64 flex items-center justify-center text-red-700 dark:text-red-400">
                {t('printers.heaterHistory.error', 'Failed to load history')}
              </div>
            ) : chartData.length === 0 ? (
              <div className="h-64 flex items-center justify-center" style={{ color: textSecondary }}>
                {t('printers.heaterHistory.empty', 'No data recorded yet')}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={borderColor} />
                  <XAxis
                    dataKey="time"
                    type="number"
                    domain={[domainStart, domainEnd]}
                    tickFormatter={ts =>
                      new Date(ts).toLocaleTimeString(
                        [],
                        applyTimeFormat({ hour: '2-digit', minute: '2-digit' }, timeFormat),
                      )
                    }
                    stroke={axisColor}
                    fontSize={11}
                  />
                  <YAxis
                    stroke={axisColor}
                    fontSize={11}
                    domain={[0, 'auto']}
                    tickFormatter={v => `${Math.round(v)}°`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: modalBg,
                      border: `1px solid ${borderColor}`,
                      borderRadius: 6,
                      color: textPrimary,
                    }}
                    labelFormatter={(ts) =>
                      new Date(ts as number).toLocaleString(
                        undefined,
                        applyTimeFormat(
                          { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' },
                          timeFormat,
                        ),
                      )
                    }
                    formatter={(value) => (value != null ? `${Math.round(Number(value))}°C` : '—')}
                  />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="value"
                    name={t('common.current', 'Current')}
                    stroke={KIND_COLORS[kind]}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Line
                    type="stepAfter"
                    dataKey="target"
                    name={t('common.target', 'Target')}
                    stroke={KIND_TARGET_COLORS[kind]}
                    strokeDasharray="4 4"
                    strokeWidth={1.5}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
