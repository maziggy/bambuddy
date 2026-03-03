import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts';
import type { ArchiveSlim } from '../api/client';
import { MetricToggle, type Metric } from './MetricToggle';
import { parseUTCDate } from '../utils/date';
import { formatWeight } from '../utils/weight';

interface FilamentTrendsProps {
  archives: ArchiveSlim[];
  currency?: string;
  dateFrom?: string;
  dateTo?: string;
}

const COLORS = ['#00ae42', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316'];

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const HOUR_SUFFIXES = ['12am', '1am', '2am', '3am', '4am', '5am', '6am', '7am', '8am', '9am', '10am', '11am', '12pm', '1pm', '2pm', '3pm', '4pm', '5pm', '6pm', '7pm', '8pm', '9pm', '10pm', '11pm'];

export function FilamentTrends({ archives, currency = '$', dateFrom, dateTo }: FilamentTrendsProps) {
  const { t } = useTranslation();
  const [filamentTypeMetric, setFilamentTypeMetric] = useState<Metric>('weight');
  const [colorMetric, setColorMetric] = useState<Metric>('weight');

  // Calculate daily usage data
  const dailyData = useMemo(() => {
    const dataMap = new Map<string, { date: string; filament: number; cost: number; prints: number }>();

    archives.forEach(archive => {
      const date = parseUTCDate(archive.completed_at || archive.created_at) || new Date();
      // Use local date string for grouping
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

      const existing = dataMap.get(key) || { date: key, filament: 0, cost: 0, prints: 0 };
      existing.filament += archive.filament_used_grams || 0;
      existing.cost += archive.cost || 0;
      existing.prints += archive.quantity || 1;
      dataMap.set(key, existing);
    });

    return Array.from(dataMap.values())
      .sort((a, b) => a.date.localeCompare(b.date))
      .map(d => ({
        ...d,
        dateLabel: new Date(d.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      }));
  }, [archives]);

  // Compute effective span in days from props or archive spread
  const spanDays = useMemo(() => {
    if (dateFrom && dateTo) {
      return Math.max((new Date(dateTo).getTime() - new Date(dateFrom).getTime()) / 86400000, 0) + 1;
    }
    if (dateFrom) {
      return Math.max((Date.now() - new Date(dateFrom).getTime()) / 86400000, 0) + 1;
    }
    if (archives.length < 2) return 0;
    const times = archives.map(a => new Date(a.completed_at || a.created_at).getTime());
    return (Math.max(...times) - Math.min(...times)) / 86400000;
  }, [archives, dateFrom, dateTo]);

  // Calculate hourly data for short timeframes (≤ 7 days)
  const hourlyData = useMemo(() => {
    if (spanDays > 7) return [];

    const dataMap = new Map<string, { date: string; filament: number; cost: number; prints: number }>();
    const multiDay = spanDays > 1;

    archives.forEach(archive => {
      const date = parseUTCDate(archive.completed_at || archive.created_at) || new Date();
      const h = date.getHours();
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${String(h).padStart(2, '0')}`;

      const existing = dataMap.get(key) || { date: key, filament: 0, cost: 0, prints: 0 };
      existing.filament += archive.filament_used_grams || 0;
      existing.cost += archive.cost || 0;
      existing.prints += archive.quantity || 1;
      dataMap.set(key, existing);
    });

    return Array.from(dataMap.values())
      .sort((a, b) => a.date.localeCompare(b.date))
      .map(d => {
        const [datePart, hourPart] = d.date.split('T');
        const dt = new Date(datePart);
        const h = parseInt(hourPart, 10);
        const label = multiDay
          ? `${DAY_NAMES[dt.getDay()]} ${HOUR_SUFFIXES[h]}`
          : HOUR_SUFFIXES[h];
        return { ...d, dateLabel: label };
      });
  }, [archives, spanDays]);

  // Calculate weekly aggregated data when there are many daily points
  const weeklyData = useMemo(() => {
    if (dailyData.length <= 60) return dailyData;

    const dataMap = new Map<string, { week: string; filament: number; cost: number; prints: number }>();

    dailyData.forEach(day => {
      const date = new Date(day.date);
      const weekStart = new Date(date);
      weekStart.setDate(date.getDate() - date.getDay());
      const key = `${weekStart.getFullYear()}-${String(weekStart.getMonth() + 1).padStart(2, '0')}-${String(weekStart.getDate()).padStart(2, '0')}`;

      const existing = dataMap.get(key) || { week: key, filament: 0, cost: 0, prints: 0 };
      existing.filament += day.filament;
      existing.cost += day.cost;
      existing.prints += day.prints;
      dataMap.set(key, existing);
    });

    return Array.from(dataMap.values())
      .sort((a, b) => a.week.localeCompare(b.week))
      .map(d => ({
        date: d.week,
        dateLabel: `Week of ${new Date(d.week).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`,
        ...d,
      }));
  }, [dailyData]);

  // Usage by filament type
  const filamentTypeData = useMemo(() => {
    const dataMap = new Map<string, number>();

    archives.forEach(archive => {
      const type = archive.filament_type || 'Unknown';
      // Handle multiple types (e.g., "PLA, PETG")
      const types = type.split(', ');
      types.forEach(t => {
        const grams = (archive.filament_used_grams || 0) / types.length;
        dataMap.set(t, (dataMap.get(t) || 0) + grams);
      });
    });

    return Array.from(dataMap.entries())
      .map(([name, value]) => ({ name, value: Math.round(value) }))
      .sort((a, b) => b.value - a.value);
  }, [archives]);

  // Usage by filament type (print count)
  const filamentTypePrintData = useMemo(() => {
    const dataMap = new Map<string, number>();
    archives.forEach(archive => {
      const type = archive.filament_type || 'Unknown';
      const types = type.split(', ');
      types.forEach(t => {
        dataMap.set(t, (dataMap.get(t) || 0) + 1);
      });
    });
    return Array.from(dataMap.entries())
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value);
  }, [archives]);

  // Usage by filament type (print time in hours)
  const filamentTypeTimeData = useMemo(() => {
    const dataMap = new Map<string, number>();
    archives.forEach(archive => {
      const type = archive.filament_type || 'Unknown';
      const types = type.split(', ');
      const seconds = (archive.actual_time_seconds || archive.print_time_seconds || 0) / types.length;
      types.forEach(t => {
        dataMap.set(t, (dataMap.get(t) || 0) + seconds);
      });
    });
    return Array.from(dataMap.entries())
      .map(([name, seconds]) => ({ name, value: Math.round((seconds / 3600) * 10) / 10 }))
      .sort((a, b) => b.value - a.value);
  }, [archives]);

  // Success rate by filament type
  const filamentSuccessData = useMemo(() => {
    const map = new Map<string, { completed: number; failed: number }>();
    archives.forEach(a => {
      if (a.status !== 'completed' && a.status !== 'failed') return;
      const types = (a.filament_type || 'Unknown').split(', ');
      types.forEach(type => {
        const entry = map.get(type) || { completed: 0, failed: 0 };
        if (a.status === 'completed') entry.completed++;
        else entry.failed++;
        map.set(type, entry);
      });
    });
    return Array.from(map.entries())
      .filter(([, v]) => v.completed + v.failed >= 2)
      .map(([name, v]) => {
        const total = v.completed + v.failed;
        const rate = Math.round((v.completed / total) * 100);
        return { name, rate, total };
      })
      .sort((a, b) => b.rate - a.rate);
  }, [archives]);

  // Color distribution
  const colorData = useMemo(() => {
    const colorMap = new Map<string, { count: number; weight: number }>();

    archives.forEach(a => {
      if (!a.filament_color) return;
      const colors = a.filament_color.split(',').map(c => c.trim());
      const weightPerColor = (a.filament_used_grams || 0) / colors.length;

      colors.forEach(hex => {
        const entry = colorMap.get(hex) || { count: 0, weight: 0 };
        entry.count++;
        entry.weight += weightPerColor;
        colorMap.set(hex, entry);
      });
    });

    return Array.from(colorMap.entries())
      .map(([hex, data]) => ({
        hex,
        value: colorMetric === 'prints' ? data.count : Math.round(data.weight),
      }))
      .sort((a, b) => b.value - a.value);
  }, [archives, colorMetric]);

  const activeFilamentTypeData =
    filamentTypeMetric === 'weight' ? filamentTypeData :
    filamentTypeMetric === 'prints' ? filamentTypePrintData :
    filamentTypeTimeData;

  const chartData = spanDays <= 7 && hourlyData.length > 0 ? hourlyData : weeklyData;
  const totalFilament = archives.reduce((sum, a) => sum + (a.filament_used_grams || 0), 0);
  const totalCost = archives.reduce((sum, a) => sum + (a.cost || 0), 0);
  const totalPrints = archives.reduce((sum, a) => sum + (a.quantity || 1), 0);
  const printerCount = new Set(archives.map(a => a.printer_id).filter(Boolean)).size;

  return (
    <div className="space-y-4">
      {/* Summary Cards */}
      <div className="grid grid-cols-3 gap-2 max-[640px]:grid-cols-1">
        <div className="bg-bambu-dark rounded-lg p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm text-bambu-gray leading-none">{t('stats.periodFilament')}</p>
            <p className="text-2xl font-bold text-white leading-none">{formatWeight(totalFilament)}</p>
          </div>
          <p className="text-xs text-bambu-gray">{printerCount} {t('nav.printers').toLowerCase()}</p>
        </div>
        <div className="bg-bambu-dark rounded-lg p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm text-bambu-gray leading-none">{t('stats.periodCost')}</p>
            <p className="text-2xl font-bold text-white leading-none">{currency}{totalCost.toFixed(2)}</p>
          </div>
          <p className="text-xs text-bambu-gray">{totalPrints} {t('common.prints')}</p>
        </div>
        <div className="bg-bambu-dark rounded-lg p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm text-bambu-gray leading-none">{t('stats.avgPerPrint')}</p>
            <p className="text-2xl font-bold text-white leading-none">
              {totalPrints > 0
                ? (totalFilament / totalPrints).toFixed(0)
                : 0}g
            </p>
          </div>
          <p className="text-xs text-bambu-gray">
            {currency}{totalPrints > 0 ? (totalCost / totalPrints).toFixed(2) : '0.00'} avg
          </p>
        </div>
      </div>

      {/* Usage Over Time Chart */}
      {chartData.length > 0 ? (
        <div className="bg-bambu-dark rounded-lg p-4">
          <h4 className="text-sm font-medium text-bambu-gray mb-4">{t('stats.usageOverTime')}</h4>
          <ResponsiveContainer width="100%" height={250}>
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="colorFilament" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00ae42" stopOpacity={0.3}/>
                  <stop offset="95%" stopColor="#00ae42" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#3d3d3d" />
              <XAxis
                dataKey="dateLabel"
                stroke="#9ca3af"
                tick={{ fontSize: 12 }}
                interval="preserveStartEnd"
              />
              <YAxis
                stroke="#9ca3af"
                tick={{ fontSize: 12 }}
                tickFormatter={(value) => `${value}g`}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#2d2d2d',
                  border: '1px solid #3d3d3d',
                  borderRadius: '8px',
                }}
                labelStyle={{ color: '#fff' }}
                formatter={(value) => [`${Number(value ?? 0).toFixed(0)}g`, 'Filament']}
              />
              <Area
                type="monotone"
                dataKey="filament"
                stroke="#00ae42"
                strokeWidth={2}
                fillOpacity={1}
                fill="url(#colorFilament)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="bg-bambu-dark rounded-lg p-8 text-center text-bambu-gray">
          {t('stats.noPrintDataInRange')}
        </div>
      )}

      {/* Bottom Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Filament Type Distribution */}
        <div className="bg-bambu-dark rounded-lg p-4">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-sm font-medium text-bambu-gray">{t('stats.byMaterial')}</h4>
            <MetricToggle value={filamentTypeMetric} onChange={setFilamentTypeMetric} />
          </div>
          {activeFilamentTypeData.length > 0 ? (
            <div className="flex items-center gap-4">
              <ResponsiveContainer width={160} height={160}>
                <PieChart>
                  <Pie
                    data={activeFilamentTypeData}
                    cx="50%"
                    cy="50%"
                    innerRadius={40}
                    outerRadius={70}
                    paddingAngle={2}
                    dataKey="value"
                  >
                    {activeFilamentTypeData.map((_, index) => (
                      <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#2d2d2d',
                      border: '1px solid #3d3d3d',
                      borderRadius: '8px',
                    }}
                    formatter={(value) => [
                      filamentTypeMetric === 'weight' ? formatWeight(Number(value ?? 0)) :
                      filamentTypeMetric === 'time' ? `${Number(value ?? 0)}h` :
                      `${value ?? 0}`,
                      filamentTypeMetric === 'weight' ? 'Usage' : filamentTypeMetric === 'time' ? 'Time' : 'Prints',
                    ]}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-2 overflow-hidden">
                {activeFilamentTypeData.map((entry, index) => {
                  const total = activeFilamentTypeData.reduce((sum, e) => sum + e.value, 0);
                  const percent = total > 0 ? ((entry.value / total) * 100).toFixed(0) : 0;
                  return (
                    <div key={entry.name} className="flex items-center gap-2 text-sm">
                      <div
                        className="w-3 h-3 rounded-sm flex-shrink-0"
                        style={{ backgroundColor: COLORS[index % COLORS.length] }}
                      />
                      <span className="text-white truncate flex-1">{entry.name}</span>
                      <span className="text-bambu-gray flex-shrink-0">
                        {filamentTypeMetric === 'weight' ? formatWeight(entry.value) :
                         filamentTypeMetric === 'time' ? `${entry.value}h` :
                         entry.value} · {percent}%
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="h-[160px] flex items-center justify-center text-bambu-gray">
              {t('stats.noFilamentData')}
            </div>
          )}
        </div>

        {/* Success by Material */}
        <div className="bg-bambu-dark rounded-lg p-4">
          <h4 className="text-sm font-medium text-bambu-gray mb-4">{t('stats.filamentSuccess')}</h4>
          {filamentSuccessData.length > 0 ? (
            <div className="space-y-1.5">
              {filamentSuccessData.map(d => (
                <div key={d.name} className="flex items-center gap-2 text-sm">
                  <span className="text-white truncate w-20 flex-shrink-0">{d.name}</span>
                  <div className="flex-1 h-1.5 bg-bambu-dark-secondary rounded-full">
                    <div
                      className={`h-full rounded-full transition-all ${
                        d.rate >= 90 ? 'bg-status-ok' : d.rate >= 70 ? 'bg-status-warning' : 'bg-status-error'
                      }`}
                      style={{ width: `${d.rate}%` }}
                    />
                  </div>
                  <span className={`font-medium flex-shrink-0 tabular-nums ${
                    d.rate >= 90 ? 'text-status-ok' : d.rate >= 70 ? 'text-status-warning' : 'text-status-error'
                  }`}>
                    {d.rate}%
                  </span>
                  <span className="text-bambu-gray flex-shrink-0 text-xs">({d.total})</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="h-[160px] flex items-center justify-center text-bambu-gray">
              {t('stats.noArchiveData')}
            </div>
          )}
        </div>

        {/* Color Distribution */}
        <div className="bg-bambu-dark rounded-lg p-4">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-sm font-medium text-bambu-gray">{t('stats.colorDistribution')}</h4>
            <MetricToggle value={colorMetric} onChange={setColorMetric} exclude={['time']} />
          </div>
          {colorData.length > 0 ? (() => {
            const colorTotal = colorData.reduce((sum, e) => sum + e.value, 0);
            return (
              <div>
                <div className="relative mx-auto" style={{ width: 160, height: 160 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={colorData}
                        cx="50%"
                        cy="50%"
                        innerRadius={45}
                        outerRadius={70}
                        paddingAngle={2}
                        dataKey="value"
                      >
                        {colorData.map((entry, index) => (
                          <Cell key={`color-${index}`} fill={entry.hex} stroke="#1a1a1a" strokeWidth={1} />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={{
                          backgroundColor: '#2d2d2d',
                          border: '1px solid #3d3d3d',
                          borderRadius: '8px',
                        }}
                        formatter={(value) => [
                          colorMetric === 'weight' ? formatWeight(Number(value ?? 0)) : `${value ?? 0}`,
                          colorMetric === 'weight' ? t('stats.filamentByWeight') : t('stats.filamentByPrints'),
                        ]}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <span className="text-lg font-bold text-white">
                      {colorMetric === 'weight' ? formatWeight(colorTotal) : colorTotal}
                    </span>
                    <span className="text-[10px] text-bambu-gray">
                      {colorData.length} {colorData.length === 1 ? 'color' : 'colors'}
                    </span>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 mt-2">
                  {colorData.slice(0, 8).map((entry) => {
                    const percent = colorTotal > 0 ? ((entry.value / colorTotal) * 100).toFixed(0) : 0;
                    return (
                      <div key={entry.hex} className="flex items-center gap-1.5 text-xs min-w-0">
                        <div className="w-2.5 h-2.5 rounded-full flex-shrink-0 border border-white/20"
                          style={{ backgroundColor: entry.hex }} />
                        <span className="text-bambu-gray truncate">
                          {percent}%
                        </span>
                      </div>
                    );
                  })}
                </div>
                {colorData.length > 8 && (
                  <p className="text-[10px] text-bambu-gray mt-1 text-center">+{colorData.length - 8} more</p>
                )}
              </div>
            );
          })() : (
            <div className="h-[160px] flex items-center justify-center text-bambu-gray">
              {t('stats.noColorData')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
