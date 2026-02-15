import { useQuery } from '@tanstack/react-query';
import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Package,
  Clock,
  CheckCircle,
  XCircle,
  DollarSign,
  Printer,
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
} from 'lucide-react';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { api } from '../api/client';
import { PrintCalendar } from '../components/PrintCalendar';
import { FilamentTrends } from '../components/FilamentTrends';
import { Dashboard, type DashboardWidget } from '../components/Dashboard';
import { getCurrencySymbol } from '../utils/currency';

// Widget Components
function QuickStatsWidget({
  stats,
  currency,
  t,
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
  t: (key: string) => string;
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-bambu-dark text-bambu-green">
          <Package className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-bambu-gray">{t('stats.totalPrints')}</p>
          <p className="text-xl font-bold text-white">{stats?.total_prints || 0}</p>
        </div>
      </div>
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-bambu-dark text-blue-400">
          <Clock className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-bambu-gray">{t('stats.printTime')}</p>
          <p className="text-xl font-bold text-white">{stats?.total_print_time_hours.toFixed(1) || 0}h</p>
        </div>
      </div>
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-bambu-dark text-orange-400">
          <Package className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-bambu-gray">{t('stats.filamentUsed')}</p>
          <p className="text-xl font-bold text-white">{((stats?.total_filament_grams || 0) / 1000).toFixed(2)}kg</p>
        </div>
      </div>
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-bambu-dark text-green-400">
          <DollarSign className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-bambu-gray">{t('stats.filamentCost')}</p>
          <p className="text-xl font-bold text-white">{currency} {stats?.total_cost.toFixed(2) || '0.00'}</p>
        </div>
      </div>
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-bambu-dark text-yellow-400">
          <Zap className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-bambu-gray">{t('stats.energyUsed')}</p>
          <p className="text-xl font-bold text-white">{stats?.total_energy_kwh.toFixed(2) || '0.00'} kWh</p>
        </div>
      </div>
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-bambu-dark text-yellow-500">
          <DollarSign className="w-5 h-5" />
        </div>
        <div>
          <p className="text-xs text-bambu-gray">{t('stats.energyCost')}</p>
          <p className="text-xl font-bold text-white">{currency} {stats?.total_energy_cost.toFixed(2) || '0.00'}</p>
        </div>
      </div>
    </div>
  );
}

function SuccessRateWidget({
  stats,
  printerMap,
  size = 1,
  t,
}: {
  stats: {
    total_prints: number;
    successful_prints: number;
    failed_prints: number;
    prints_by_printer: Record<string, number>;
  } | undefined;
  printerMap: Map<string, string>;
  size?: 1 | 2 | 4;
  t: (key: string) => string;
}) {
  const successRate = stats?.total_prints
    ? Math.round((stats.successful_prints / stats.total_prints) * 100)
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
  t,
}: {
  stats: {
    average_time_accuracy: number | null;
    time_accuracy_by_printer: Record<string, number> | null;
  } | undefined;
  printerMap: Map<string, string>;
  size?: 1 | 2 | 4;
  t: (key: string) => string;
}) {
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

function FilamentTypesWidget({
  stats,
  size = 1,
  t,
}: {
  stats: {
    total_prints: number;
    prints_by_filament_type: Record<string, number>;
  } | undefined;
  size?: 1 | 2 | 4;
  t: (key: string, options?: Record<string, unknown>) => string;
}) {
  if (!stats?.prints_by_filament_type || Object.keys(stats.prints_by_filament_type).length === 0) {
    return <p className="text-bambu-gray text-center py-4">{t('stats.noFilamentData')}</p>;
  }

  // Sort by print count descending
  const sortedEntries = Object.entries(stats.prints_by_filament_type).sort(
    ([, a], [, b]) => b - a
  );

  // Limit entries based on size
  const maxEntries = size === 1 ? 5 : size === 2 ? 8 : 999;
  const displayEntries = sortedEntries.slice(0, maxEntries);
  const hasMore = sortedEntries.length > maxEntries;

  // Use grid layout when expanded
  if (size === 4 && displayEntries.length > 4) {
    return (
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        {displayEntries.map(([type, count]) => {
          const percentage = Math.round((count / (stats.total_prints || 1)) * 100);
          return (
            <div key={type}>
              <div className="flex justify-between text-sm mb-1">
                <span className="text-white truncate max-w-[120px]">{type}</span>
                <span className="text-bambu-gray">{count}</span>
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

  return (
    <div className="space-y-3">
      {displayEntries.map(([type, count]) => {
        const percentage = Math.round((count / (stats.total_prints || 1)) * 100);
        return (
          <div key={type}>
            <div className="flex justify-between text-sm mb-1">
              <span className="text-white">{type}</span>
              <span className="text-bambu-gray">{count} {t('common.prints')}</span>
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
      {hasMore && (
        <p className="text-xs text-bambu-gray text-center pt-1">
          {t('common.more', { count: sortedEntries.length - maxEntries })}
        </p>
      )}
    </div>
  );
}

function PrintActivityWidget({
  printDates,
  size = 2,
}: {
  printDates: string[];
  size?: 1 | 2 | 4;
}) {
  // Show more months when widget is larger - cell size auto-calculated
  const months = size === 1 ? 3 : size === 2 ? 6 : 12;
  return <PrintCalendar printDates={printDates} months={months} />;
}

function PrintsByPrinterWidget({
  stats,
  printerMap,
  t,
}: {
  stats: { prints_by_printer: Record<string, number> } | undefined;
  printerMap: Map<string, string>;
  t: (key: string) => string;
}) {
  if (!stats?.prints_by_printer || Object.keys(stats.prints_by_printer).length === 0) {
    return <p className="text-bambu-gray text-center py-4">{t('stats.noPrinterData')}</p>;
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
              {printerMap.get(printerId) || `${t('common.printer')} ${printerId}`}
            </p>
            <p className="text-xs text-bambu-gray">{count} {t('common.prints')}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function FilamentTrendsWidget({
  archives,
  currency,
  t,
}: {
  archives: Parameters<typeof FilamentTrends>[0]['archives'];
  currency: string;
  t: (key: string) => string;
}) {
  if (!archives || archives.length === 0) {
    return <p className="text-bambu-gray text-center py-4">{t('stats.noPrintData')}</p>;
  }
  return <FilamentTrends archives={archives} currency={currency} />;
}

function FailureAnalysisWidget({ size = 1, t }: { size?: 1 | 2 | 4; t: (key: string, options?: Record<string, unknown>) => string }) {
  const { data: analysis, isLoading } = useQuery({
    queryKey: ['failureAnalysis'],
    queryFn: () => api.getFailureAnalysis({ days: 30 }),
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-4">
        <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
      </div>
    );
  }

  if (!analysis || analysis.total_prints === 0) {
    return <p className="text-bambu-gray text-center py-4">{t('stats.noPrintDataLast30Days')}</p>;
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

export function StatsPage() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const [isExporting, setIsExporting] = useState(false);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [dashboardKey, setDashboardKey] = useState(0);
  const [hiddenCount, setHiddenCount] = useState(0);
  const [isRecalculating, setIsRecalculating] = useState(false);

  // Read hidden count from localStorage
  useEffect(() => {
    const updateHiddenCount = () => {
      try {
        const saved = localStorage.getItem('bambusy-dashboard-layout');
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
    const interval = setInterval(updateHiddenCount, 500);
    return () => {
      window.removeEventListener('storage', updateHiddenCount);
      clearInterval(interval);
    };
  }, [dashboardKey]);

  const { data: stats, isLoading, refetch: refetchStats } = useQuery({
    queryKey: ['archiveStats'],
    queryFn: api.getArchiveStats,
  });

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: archives } = useQuery({
    queryKey: ['archives'],
    queryFn: () => api.getArchives(undefined, undefined, 1000, 0),
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
      await refetchStats();
      showToast(t('stats.recalculatedCosts', { count: result.updated }));
    } catch {
      showToast(t('stats.recalculateFailed'), 'error');
    } finally {
      setIsRecalculating(false);
    }
  };

  const currency = getCurrencySymbol(settings?.currency || 'USD');
  const printerMap = new Map(printers?.map((p) => [String(p.id), p.name]) || []);
  const printDates = archives?.map((a) => a.created_at) || [];

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
      component: <QuickStatsWidget stats={stats} currency={currency} t={t} />,
      defaultSize: 2,
    },
    {
      id: 'success-rate',
      title: t('stats.successRate'),
      component: (size) => <SuccessRateWidget stats={stats} printerMap={printerMap} size={size} t={t} />,
      defaultSize: 1,
    },
    {
      id: 'time-accuracy',
      title: t('stats.timeAccuracy'),
      component: (size) => <TimeAccuracyWidget stats={stats} printerMap={printerMap} size={size} t={t} />,
      defaultSize: 1,
    },
    {
      id: 'filament-types',
      title: t('stats.filamentTypes'),
      component: (size) => <FilamentTypesWidget stats={stats} size={size} t={t} />,
      defaultSize: 1,
    },
    {
      id: 'failure-analysis',
      title: t('stats.failureAnalysis'),
      component: (size) => <FailureAnalysisWidget size={size} t={t} />,
      defaultSize: 1,
    },
    {
      id: 'print-activity',
      title: t('stats.printActivity'),
      component: (size) => <PrintActivityWidget printDates={printDates} size={size} />,
      defaultSize: 2,
    },
    {
      id: 'prints-by-printer',
      title: t('stats.printsByPrinter'),
      component: <PrintsByPrinterWidget stats={stats} printerMap={printerMap} t={t} />,
      defaultSize: 2,
    },
    {
      id: 'filament-trends',
      title: t('stats.filamentTrends'),
      component: <FilamentTrendsWidget archives={archives || []} currency={currency} t={t} />,
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
              localStorage.removeItem('bambusy-dashboard-layout');
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
        </div>
      </div>

      <Dashboard
        key={dashboardKey}
        widgets={widgets}
        storageKey="bambusy-dashboard-layout"
        stackBelow={640}
        hideControls
      />
    </div>
  );
}
