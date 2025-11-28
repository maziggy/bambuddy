import { useMemo, useState } from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  Legend,
} from 'recharts';
import type { Archive } from '../api/client';

interface FilamentTrendsProps {
  archives: Archive[];
  currency?: string;
}

type TimeRange = '7d' | '30d' | '90d' | '365d' | 'all';

const COLORS = ['#00ae42', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316'];

function getDateRange(range: TimeRange): Date {
  const now = new Date();
  switch (range) {
    case '7d':
      return new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    case '30d':
      return new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    case '90d':
      return new Date(now.getTime() - 90 * 24 * 60 * 60 * 1000);
    case '365d':
      return new Date(now.getTime() - 365 * 24 * 60 * 60 * 1000);
    case 'all':
      return new Date(0);
  }
}

export function FilamentTrends({ archives, currency = '$' }: FilamentTrendsProps) {
  const [timeRange, setTimeRange] = useState<TimeRange>('30d');

  // Filter archives by time range
  const filteredArchives = useMemo(() => {
    const startDate = getDateRange(timeRange);
    return archives.filter(a => new Date(a.completed_at || a.created_at) >= startDate);
  }, [archives, timeRange]);

  // Calculate daily usage data
  const dailyData = useMemo(() => {
    const dataMap = new Map<string, { date: string; filament: number; cost: number; prints: number }>();

    filteredArchives.forEach(archive => {
      const date = new Date(archive.completed_at || archive.created_at);
      const key = date.toISOString().split('T')[0];

      const existing = dataMap.get(key) || { date: key, filament: 0, cost: 0, prints: 0 };
      existing.filament += archive.filament_used_grams || 0;
      existing.cost += archive.cost || 0;
      existing.prints += 1;
      dataMap.set(key, existing);
    });

    return Array.from(dataMap.values())
      .sort((a, b) => a.date.localeCompare(b.date))
      .map(d => ({
        ...d,
        dateLabel: new Date(d.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      }));
  }, [filteredArchives]);

  // Calculate weekly aggregated data for longer time ranges
  const weeklyData = useMemo(() => {
    if (timeRange === '7d' || timeRange === '30d') return dailyData;

    const dataMap = new Map<string, { week: string; filament: number; cost: number; prints: number }>();

    filteredArchives.forEach(archive => {
      const date = new Date(archive.completed_at || archive.created_at);
      // Get week start (Sunday)
      const weekStart = new Date(date);
      weekStart.setDate(date.getDate() - date.getDay());
      const key = weekStart.toISOString().split('T')[0];

      const existing = dataMap.get(key) || { week: key, filament: 0, cost: 0, prints: 0 };
      existing.filament += archive.filament_used_grams || 0;
      existing.cost += archive.cost || 0;
      existing.prints += 1;
      dataMap.set(key, existing);
    });

    return Array.from(dataMap.values())
      .sort((a, b) => a.week.localeCompare(b.week))
      .map(d => ({
        date: d.week,
        dateLabel: `Week of ${new Date(d.week).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`,
        ...d,
      }));
  }, [filteredArchives, dailyData, timeRange]);

  // Usage by filament type
  const filamentTypeData = useMemo(() => {
    const dataMap = new Map<string, number>();

    filteredArchives.forEach(archive => {
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
  }, [filteredArchives]);

  // Monthly comparison data
  const monthlyComparison = useMemo(() => {
    const now = new Date();
    const months: { month: string; filament: number; cost: number; prints: number }[] = [];

    for (let i = 5; i >= 0; i--) {
      const monthDate = new Date(now.getFullYear(), now.getMonth() - i, 1);
      const monthEnd = new Date(now.getFullYear(), now.getMonth() - i + 1, 0);
      const monthStr = monthDate.toLocaleDateString('en-US', { month: 'short', year: '2-digit' });

      const monthArchives = archives.filter(a => {
        const d = new Date(a.completed_at || a.created_at);
        return d >= monthDate && d <= monthEnd;
      });

      months.push({
        month: monthStr,
        filament: Math.round(monthArchives.reduce((sum, a) => sum + (a.filament_used_grams || 0), 0)),
        cost: monthArchives.reduce((sum, a) => sum + (a.cost || 0), 0),
        prints: monthArchives.length,
      });
    }

    return months;
  }, [archives]);

  const chartData = timeRange === '7d' || timeRange === '30d' ? dailyData : weeklyData;
  const totalFilament = filteredArchives.reduce((sum, a) => sum + (a.filament_used_grams || 0), 0);
  const totalCost = filteredArchives.reduce((sum, a) => sum + (a.cost || 0), 0);

  return (
    <div className="space-y-6">
      {/* Time Range Selector */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-white">Filament Usage Trends</h3>
        <div className="flex gap-1 bg-bambu-dark rounded-lg p-1">
          {(['7d', '30d', '90d', '365d', 'all'] as TimeRange[]).map((range) => (
            <button
              key={range}
              onClick={() => setTimeRange(range)}
              className={`px-3 py-1 text-sm rounded-md transition-colors ${
                timeRange === range
                  ? 'bg-bambu-green text-white'
                  : 'text-bambu-gray hover:text-white'
              }`}
            >
              {range === 'all' ? 'All' : range.replace('d', 'D')}
            </button>
          ))}
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-bambu-dark rounded-lg p-4">
          <p className="text-sm text-bambu-gray">Period Filament</p>
          <p className="text-2xl font-bold text-white">{(totalFilament / 1000).toFixed(2)}kg</p>
          <p className="text-xs text-bambu-gray">{totalFilament.toFixed(0)}g total</p>
        </div>
        <div className="bg-bambu-dark rounded-lg p-4">
          <p className="text-sm text-bambu-gray">Period Cost</p>
          <p className="text-2xl font-bold text-white">{currency}{totalCost.toFixed(2)}</p>
          <p className="text-xs text-bambu-gray">{filteredArchives.length} prints</p>
        </div>
        <div className="bg-bambu-dark rounded-lg p-4">
          <p className="text-sm text-bambu-gray">Avg per Print</p>
          <p className="text-2xl font-bold text-white">
            {filteredArchives.length > 0
              ? (totalFilament / filteredArchives.length).toFixed(0)
              : 0}g
          </p>
          <p className="text-xs text-bambu-gray">
            {currency}{filteredArchives.length > 0 ? (totalCost / filteredArchives.length).toFixed(2) : '0.00'} avg
          </p>
        </div>
      </div>

      {/* Usage Over Time Chart */}
      {chartData.length > 0 ? (
        <div className="bg-bambu-dark rounded-lg p-4">
          <h4 className="text-sm font-medium text-bambu-gray mb-4">Usage Over Time</h4>
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
                formatter={(value: number) => [`${value.toFixed(0)}g`, 'Filament']}
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
          No data for selected time range
        </div>
      )}

      {/* Bottom Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Filament Type Distribution */}
        <div className="bg-bambu-dark rounded-lg p-4">
          <h4 className="text-sm font-medium text-bambu-gray mb-4">By Filament Type</h4>
          {filamentTypeData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie
                  data={filamentTypeData}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={80}
                  paddingAngle={2}
                  dataKey="value"
                  label={({ name, percent }) => `${name} ${((percent || 0) * 100).toFixed(0)}%`}
                  labelLine={false}
                >
                  {filamentTypeData.map((_, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#2d2d2d',
                    border: '1px solid #3d3d3d',
                    borderRadius: '8px',
                  }}
                  formatter={(value: number) => [`${value}g`, 'Usage']}
                />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[200px] flex items-center justify-center text-bambu-gray">
              No filament data
            </div>
          )}
        </div>

        {/* Monthly Comparison */}
        <div className="bg-bambu-dark rounded-lg p-4">
          <h4 className="text-sm font-medium text-bambu-gray mb-4">Monthly Comparison</h4>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={monthlyComparison}>
              <CartesianGrid strokeDasharray="3 3" stroke="#3d3d3d" />
              <XAxis dataKey="month" stroke="#9ca3af" tick={{ fontSize: 12 }} />
              <YAxis stroke="#9ca3af" tick={{ fontSize: 12 }} tickFormatter={(v) => `${v}g`} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#2d2d2d',
                  border: '1px solid #3d3d3d',
                  borderRadius: '8px',
                }}
                formatter={(value: number, name: string) => [
                  name === 'filament' ? `${value}g` : name === 'cost' ? `${currency}${value.toFixed(2)}` : value,
                  name === 'filament' ? 'Filament' : name === 'cost' ? 'Cost' : 'Prints'
                ]}
              />
              <Legend />
              <Bar dataKey="filament" name="Filament (g)" fill="#00ae42" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
