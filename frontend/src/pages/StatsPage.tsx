import { useQuery } from '@tanstack/react-query';
import {
  Package,
  Clock,
  CheckCircle,
  XCircle,
  DollarSign,
  Printer,
} from 'lucide-react';
import { api } from '../api/client';
import { PrintCalendar } from '../components/PrintCalendar';
import { FilamentTrends } from '../components/FilamentTrends';
import { Dashboard, type DashboardWidget } from '../components/Dashboard';

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
  } | undefined;
  currency: string;
}) {
  return (
    <div className="grid grid-cols-2 gap-4">
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-bambu-dark text-bambu-green">
          <Package className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-bambu-gray">Total Prints</p>
          <p className="text-xl font-bold text-white">{stats?.total_prints || 0}</p>
        </div>
      </div>
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-bambu-dark text-blue-400">
          <Clock className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-bambu-gray">Print Time</p>
          <p className="text-xl font-bold text-white">{stats?.total_print_time_hours.toFixed(1) || 0}h</p>
        </div>
      </div>
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-bambu-dark text-orange-400">
          <Package className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-bambu-gray">Filament Used</p>
          <p className="text-xl font-bold text-white">{((stats?.total_filament_grams || 0) / 1000).toFixed(2)}kg</p>
        </div>
      </div>
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-bambu-dark text-green-400">
          <DollarSign className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-bambu-gray">Total Cost</p>
          <p className="text-xl font-bold text-white">{currency} {stats?.total_cost.toFixed(2) || '0.00'}</p>
        </div>
      </div>
    </div>
  );
}

function SuccessRateWidget({
  stats,
}: {
  stats: {
    total_prints: number;
    successful_prints: number;
    failed_prints: number;
  } | undefined;
}) {
  const successRate = stats?.total_prints
    ? Math.round((stats.successful_prints / stats.total_prints) * 100)
    : 0;

  return (
    <div className="flex items-center gap-6">
      <div className="relative w-28 h-28">
        <svg className="w-full h-full -rotate-90">
          <circle cx="56" cy="56" r="48" fill="none" stroke="#3d3d3d" strokeWidth="10" />
          <circle
            cx="56"
            cy="56"
            r="48"
            fill="none"
            stroke="#00ae42"
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={`${successRate * 3.02} 302`}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-xl font-bold text-white">{successRate}%</span>
        </div>
      </div>
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <CheckCircle className="w-4 h-4 text-bambu-green" />
          <span className="text-sm text-bambu-gray">Successful:</span>
          <span className="text-sm text-white font-medium">{stats?.successful_prints || 0}</span>
        </div>
        <div className="flex items-center gap-2">
          <XCircle className="w-4 h-4 text-red-400" />
          <span className="text-sm text-bambu-gray">Failed:</span>
          <span className="text-sm text-white font-medium">{stats?.failed_prints || 0}</span>
        </div>
      </div>
    </div>
  );
}

function FilamentTypesWidget({
  stats,
}: {
  stats: {
    total_prints: number;
    prints_by_filament_type: Record<string, number>;
  } | undefined;
}) {
  if (!stats?.prints_by_filament_type || Object.keys(stats.prints_by_filament_type).length === 0) {
    return <p className="text-bambu-gray text-center py-4">No filament data available</p>;
  }

  // Sort by print count descending
  const sortedEntries = Object.entries(stats.prints_by_filament_type).sort(
    ([, a], [, b]) => b - a
  );

  return (
    <div className="space-y-3">
      {sortedEntries.map(([type, count]) => {
        const percentage = Math.round((count / (stats.total_prints || 1)) * 100);
        return (
          <div key={type}>
            <div className="flex justify-between text-sm mb-1">
              <span className="text-white">{type}</span>
              <span className="text-bambu-gray">{count} prints</span>
            </div>
            <div className="h-2 bg-bambu-dark rounded-full">
              <div
                className="h-full bg-bambu-green rounded-full transition-all"
                style={{ width: `${percentage}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PrintActivityWidget({ printDates }: { printDates: string[] }) {
  return <PrintCalendar printDates={printDates} months={4} />;
}

function PrintsByPrinterWidget({
  stats,
  printerMap,
}: {
  stats: { prints_by_printer: Record<string, number> } | undefined;
  printerMap: Map<string, string>;
}) {
  if (!stats?.prints_by_printer || Object.keys(stats.prints_by_printer).length === 0) {
    return <p className="text-bambu-gray text-center py-4">No printer data available</p>;
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {Object.entries(stats.prints_by_printer).map(([printerId, count]) => (
        <div key={printerId} className="flex items-center gap-3 p-3 bg-bambu-dark rounded-lg">
          <div className="p-2 bg-bambu-dark-tertiary rounded-lg">
            <Printer className="w-4 h-4 text-bambu-green" />
          </div>
          <div>
            <p className="text-white font-medium text-sm">
              {printerMap.get(printerId) || `Printer ${printerId}`}
            </p>
            <p className="text-xs text-bambu-gray">{count} prints</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function FilamentTrendsWidget({
  archives,
  currency,
}: {
  archives: Parameters<typeof FilamentTrends>[0]['archives'];
  currency: string;
}) {
  if (!archives || archives.length === 0) {
    return <p className="text-bambu-gray text-center py-4">No print data available</p>;
  }
  return <FilamentTrends archives={archives} currency={currency} />;
}

export function StatsPage() {
  const { data: stats, isLoading } = useQuery({
    queryKey: ['archiveStats'],
    queryFn: api.getArchiveStats,
  });

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: archives } = useQuery({
    queryKey: ['archives'],
    queryFn: () => api.getArchives(undefined, 1000, 0),
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const currency = settings?.currency || '$';
  const printerMap = new Map(printers?.map((p) => [String(p.id), p.name]) || []);
  const printDates = archives?.map((a) => a.created_at) || [];

  if (isLoading) {
    return (
      <div className="p-8">
        <div className="text-center py-12 text-bambu-gray">Loading statistics...</div>
      </div>
    );
  }

  // Define dashboard widgets
  // Sizes: 1 = quarter (1/4), 2 = half (1/2), 4 = full width
  const widgets: DashboardWidget[] = [
    {
      id: 'quick-stats',
      title: 'Quick Stats',
      component: <QuickStatsWidget stats={stats} currency={currency} />,
      defaultSize: 2,
    },
    {
      id: 'success-rate',
      title: 'Success Rate',
      component: <SuccessRateWidget stats={stats} />,
      defaultSize: 1,
    },
    {
      id: 'filament-types',
      title: 'Filament Types',
      component: <FilamentTypesWidget stats={stats} />,
      defaultSize: 1,
    },
    {
      id: 'print-activity',
      title: 'Print Activity',
      component: <PrintActivityWidget printDates={printDates} />,
      defaultSize: 2,
    },
    {
      id: 'prints-by-printer',
      title: 'Prints by Printer',
      component: <PrintsByPrinterWidget stats={stats} printerMap={printerMap} />,
      defaultSize: 2,
    },
    {
      id: 'filament-trends',
      title: 'Filament Usage Trends',
      component: <FilamentTrendsWidget archives={archives || []} currency={currency} />,
      defaultSize: 4,
    },
  ];

  return (
    <div className="p-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white">Dashboard</h1>
        <p className="text-bambu-gray">Drag widgets to rearrange. Click the eye icon to hide.</p>
      </div>

      <Dashboard widgets={widgets} storageKey="bambusy-dashboard-layout" />
    </div>
  );
}
