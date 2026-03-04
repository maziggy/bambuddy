import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
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

  // Auto-select first printer
  useEffect(() => {
    if (!selectedPrinterId && printers.length > 0) {
      onPrinterChange(printers[0].id);
    }
  }, [printers, selectedPrinterId, onPrinterChange]);

  // Clock - update every second for kiosk display
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const formatTime = (date: Date) =>
    date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  return (
    <div className="h-11 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary flex items-center px-3 gap-4 shrink-0">
      {/* Logo */}
      <div className="flex items-center gap-2 shrink-0">
        <div className="w-6 h-6 rounded bg-bambu-green flex items-center justify-center">
          <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
          </svg>
        </div>
        <span className="text-white font-semibold text-sm">SpoolBuddy</span>
      </div>

      {/* Printer selector - centered */}
      <div className="flex-1 flex justify-center">
        <select
          value={selectedPrinterId ?? ''}
          onChange={(e) => onPrinterChange(Number(e.target.value))}
          className="bg-bambu-dark text-white text-sm px-3 py-1.5 rounded border border-bambu-dark-tertiary focus:outline-none focus:border-bambu-green min-w-[150px]"
        >
          {printers.length === 0 ? (
            <option value="">{t('spoolbuddy.status.noPrinters', 'No printers')}</option>
          ) : (
            printers.map((printer: Printer) => (
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
          <div className={`w-2.5 h-2.5 rounded-full ${deviceOnline ? 'bg-bambu-green shadow-[0_0_6px_rgba(34,197,94,0.5)]' : 'bg-bambu-gray'}`} />
          <span className="text-xs text-white/50">{deviceOnline ? t('spoolbuddy.status.online', 'Online') : t('spoolbuddy.status.offline', 'Offline')}</span>
        </div>

        {/* Clock */}
        <span className="text-white/50 text-sm font-mono min-w-[50px] text-right">
          {formatTime(currentTime)}
        </span>
      </div>
    </div>
  );
}
