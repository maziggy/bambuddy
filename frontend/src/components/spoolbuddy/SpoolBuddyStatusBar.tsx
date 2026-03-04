import { useTranslation } from 'react-i18next';

interface Alert {
  type: 'warning' | 'error' | 'info';
  message: string;
}

interface SpoolBuddyStatusBarProps {
  alert?: Alert | null;
}

export function SpoolBuddyStatusBar({ alert }: SpoolBuddyStatusBarProps) {
  const { t } = useTranslation();

  const statusColor = !alert
    ? 'bg-bambu-green'
    : alert.type === 'error'
    ? 'bg-red-500'
    : alert.type === 'warning'
    ? 'bg-amber-500'
    : 'bg-bambu-green';

  const borderColor = !alert
    ? 'border-bambu-dark-tertiary'
    : alert.type === 'error'
    ? 'border-red-500'
    : alert.type === 'warning'
    ? 'border-amber-500'
    : 'border-bambu-dark-tertiary';

  return (
    <div className={`h-8 bg-bambu-dark-secondary border-t-2 ${borderColor} flex items-center px-3 gap-3 shrink-0`}>
      {/* Status LED */}
      <div className={`w-3 h-3 rounded-full ${statusColor} animate-pulse`} />

      {/* Status message */}
      <div className="flex-1 text-sm text-white/50 truncate">
        {alert ? (
          <span>{alert.message}</span>
        ) : (
          <span className="text-bambu-green">{t('spoolbuddy.status.systemReady', 'System Ready')}</span>
        )}
      </div>
    </div>
  );
}
