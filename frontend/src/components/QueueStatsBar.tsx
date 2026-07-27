import { Play, Clock, Timer, Weight, CheckCircle } from 'lucide-react';
import { formatDuration } from '../utils/date';

function formatWeight(g: number): string {
  if (g >= 1000) return `${(g / 1000).toFixed(1)}kg`;
  return `${Math.round(g)}g`;
}

export function QueueStatsBar({
  activeCount,
  pendingCount,
  totalTime,
  totalWeight,
  historyCount,
  t,
}: {
  activeCount: number;
  pendingCount: number;
  totalTime: number;
  totalWeight: number;
  historyCount: number;
  t: (key: string) => string;
}) {
  const stats = [
    { icon: Play, value: activeCount, label: t('queue.summary.printing'), color: 'text-blue-600 dark:text-blue-400' },
    { icon: Clock, value: pendingCount, label: t('queue.summary.queued'), color: 'text-yellow-600 dark:text-yellow-400' },
    { icon: Timer, value: formatDuration(totalTime), label: t('queue.summary.totalTime'), color: 'text-bambu-green' },
    { icon: Weight, value: formatWeight(totalWeight), label: t('queue.summary.totalWeight'), color: 'text-purple-600 dark:text-purple-400' },
    { icon: CheckCircle, value: historyCount, label: t('queue.summary.history'), color: 'text-bambu-gray' },
  ];

  return (
    <div className="flex items-center gap-3 sm:gap-5 flex-wrap px-4 py-3 bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary mb-6">
      {stats.map((stat, i) => (
        <div key={i} className="flex items-center gap-3">
          {i > 0 && <span className="hidden sm:block text-bambu-dark-tertiary">|</span>}
          <div className="flex items-center gap-1.5">
            <stat.icon className={`w-4 h-4 ${stat.color}`} />
            <span className="text-sm font-semibold text-white">{stat.value}</span>
            <span className="text-xs sm:text-sm text-bambu-gray">{stat.label}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
