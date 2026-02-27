import { useState, useEffect, useMemo } from 'react';
import { useQuery, useQueries } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { WifiOff } from 'lucide-react';
import { api, type Printer } from '../../api/client';

interface SpoolBuddyTopBarProps {
  selectedPrinterId: number | null;
  onPrinterChange: (id: number) => void;
  deviceOnline: boolean;
}

export function SpoolBuddyTopBar({ selectedPrinterId, onPrinterChange, deviceOnline }: SpoolBuddyTopBarProps) {
  const { t } = useTranslation();
  const [currentTime, setCurrentTime] = useState(new Date());

  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
  });

  // Fetch status for each printer to determine which are online
  const statusQueries = useQueries({
    queries: printers.map((printer: Printer) => ({
      queryKey: ['printerStatus', printer.id],
      queryFn: () => api.getPrinterStatus(printer.id),
      refetchInterval: 10000,
    })),
  });

  const onlinePrinters = useMemo(() => {
    return printers.filter((_: Printer, i: number) => statusQueries[i]?.data?.connected);
  }, [printers, statusQueries]);

  // Auto-select first online printer
  useEffect(() => {
    const currentStillOnline = onlinePrinters.some((p: Printer) => p.id === selectedPrinterId);
    if ((!selectedPrinterId || !currentStillOnline) && onlinePrinters.length > 0) {
      onPrinterChange(onlinePrinters[0].id);
    }
  }, [onlinePrinters, selectedPrinterId, onPrinterChange]);

  // Clock - update every second for kiosk display
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const formatTime = (date: Date) =>
    date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  return (
    <div className="h-12 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary flex items-center px-3 gap-4 shrink-0">
      {/* Logo */}
      <div className="flex items-center shrink-0">
        <img src="/img/spoolbuddy_logo_dark_small.png" alt="SpoolBuddy" width={113} height={28} className="h-7 w-auto" />
      </div>

      {/* Printer selector - centered */}
      <div className="flex-1 flex justify-center">
        <select
          value={selectedPrinterId ?? ''}
          onChange={(e) => onPrinterChange(Number(e.target.value))}
          className="bg-bambu-dark text-white text-base px-4 py-2 rounded border border-bambu-dark-tertiary focus:outline-none focus:border-bambu-green min-w-[180px]"
        >
          {onlinePrinters.length === 0 ? (
            <option value="">{t('spoolbuddy.status.noPrinters', 'No printers online')}</option>
          ) : (
            onlinePrinters.map((printer: Printer) => (
              <option key={printer.id} value={printer.id}>
                {printer.name}
              </option>
            ))
          )}
        </select>
      </div>

      {/* Right side indicators */}
      <div className="flex items-center gap-3 shrink-0">
        {/* WiFi signal bars */}
        <div className="flex items-center" title={deviceOnline ? t('spoolbuddy.status.online', 'Online') : t('spoolbuddy.status.offline', 'Offline')}>
          {deviceOnline ? (
            <div className="flex items-end gap-0.5 h-4">
              {[1, 2, 3, 4].map((level) => (
                <div
                  key={level}
                  className={`w-1 rounded-sm ${level <= 4 ? 'bg-white' : 'bg-bambu-dark-tertiary'}`}
                  style={{ height: `${level * 4}px` }}
                />
              ))}
            </div>
          ) : (
            <WifiOff className="w-5 h-5 text-red-400" />
          )}
        </div>

        {/* Device LED */}
        <div className="flex items-center gap-1.5">
          <div className={`w-3 h-3 rounded-full ${deviceOnline ? 'bg-bambu-green shadow-[0_0_6px_rgba(34,197,94,0.5)]' : 'bg-bambu-gray'}`} />
          <span className="text-sm text-white/50">{deviceOnline ? t('spoolbuddy.status.online', 'Online') : t('spoolbuddy.status.offline', 'Offline')}</span>
        </div>

        {/* Clock */}
        <span className="text-white/50 text-base font-mono min-w-[50px] text-right">
          {formatTime(currentTime)}
        </span>
      </div>
    </div>
  );
}
