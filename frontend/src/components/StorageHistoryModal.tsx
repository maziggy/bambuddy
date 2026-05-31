import { useState } from 'react';
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
import { api, type StorageHistoryResponse } from '../api/client';
import { useTheme } from '../contexts/ThemeContext';

type TimeRange = '24h' | '48h' | '7d';

const TIME_RANGES: { value: TimeRange; labelKey: string; hours: number }[] = [
  { value: '24h', labelKey: 'storage.history24h', hours: 24 },
  { value: '48h', labelKey: 'storage.history48h', hours: 48 },
  { value: '7d', labelKey: 'storage.history7d', hours: 168 },
];

interface StorageHistoryModalProps {
  isOpen: boolean;
  onClose: () => void;
  unitId: number;
  unitName: string;
}

export function StorageHistoryModal({ isOpen, onClose, unitId, unitName }: StorageHistoryModalProps) {
  const { t } = useTranslation();
  const { resolvedMode } = useTheme();
  const isDark = resolvedMode !== 'light';
  const [range, setRange] = useState<TimeRange>('24h');

  const hours = TIME_RANGES.find((r) => r.value === range)?.hours ?? 24;

  const { data, isLoading } = useQuery<StorageHistoryResponse>({
    queryKey: ['storage-history', unitId, hours],
    queryFn: () => api.getStorageHistory(unitId, hours),
    enabled: isOpen,
    refetchInterval: 60_000,
  });

  if (!isOpen) return null;

  const chartData = (data?.readings ?? []).map((r) => ({
    time: new Date(r.recorded_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    temp: r.temp,
    humidity: r.humidity,
  }));

  const tempUnit = data?.temp_unit ?? '°C';
  const gridColor = isDark ? '#374151' : '#e5e7eb';
  const textColor = isDark ? '#9ca3af' : '#6b7280';

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-2xl max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div>
            <h2 className="text-lg font-semibold text-white">{t('storage.historyTitle')}</h2>
            <p className="text-sm text-bambu-gray">{unitName}</p>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Time range selector */}
        <div className="flex gap-2 p-4 pb-0">
          {TIME_RANGES.map((r) => (
            <button
              key={r.value}
              onClick={() => setRange(r.value)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                range === r.value
                  ? 'bg-bambu-green text-white'
                  : 'bg-bambu-dark border border-bambu-dark-tertiary text-bambu-gray hover:text-white'
              }`}
            >
              {t(r.labelKey)}
            </button>
          ))}
        </div>

        {/* Charts */}
        <div className="p-4 space-y-6">
          {isLoading ? (
            <div className="h-40 flex items-center justify-center text-bambu-gray">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-bambu-green" />
            </div>
          ) : chartData.length === 0 ? (
            <p className="text-center text-bambu-gray py-8">{t('storage.noHistory')}</p>
          ) : (
            <>
              {/* Temperature chart */}
              {chartData.some((d) => d.temp !== null) && (
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <Thermometer className="w-4 h-4 text-orange-400" />
                    <span className="text-sm font-medium text-white">{t('storage.temperature')} ({tempUnit})</span>
                  </div>
                  <ResponsiveContainer width="100%" height={160}>
                    <AreaChart data={chartData}>
                      <defs>
                        <linearGradient id="tempGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#f97316" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#f97316" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                      <XAxis dataKey="time" tick={{ fontSize: 10, fill: textColor }} />
                      <YAxis tick={{ fontSize: 10, fill: textColor }} />
                      <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: 8 }} labelStyle={{ color: '#fff' }} itemStyle={{ color: '#f97316' }} />
                      <Area type="monotone" dataKey="temp" stroke="#f97316" fill="url(#tempGrad)" strokeWidth={2} dot={false} connectNulls />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}

              {/* Humidity chart */}
              {chartData.some((d) => d.humidity !== null) && (
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <Droplets className="w-4 h-4 text-blue-400" />
                    <span className="text-sm font-medium text-white">{t('storage.humidity')} (%)</span>
                  </div>
                  <ResponsiveContainer width="100%" height={160}>
                    <AreaChart data={chartData}>
                      <defs>
                        <linearGradient id="humidGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#60a5fa" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#60a5fa" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                      <XAxis dataKey="time" tick={{ fontSize: 10, fill: textColor }} />
                      <YAxis tick={{ fontSize: 10, fill: textColor }} domain={[0, 100]} />
                      <Tooltip contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: 8 }} labelStyle={{ color: '#fff' }} itemStyle={{ color: '#60a5fa' }} />
                      <Area type="monotone" dataKey="humidity" stroke="#60a5fa" fill="url(#humidGrad)" strokeWidth={2} dot={false} connectNulls />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
