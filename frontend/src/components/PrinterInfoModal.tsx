import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Copy, Check, Signal } from 'lucide-react';
import { Card, CardContent } from './Card';
import { formatDateOnly } from '../utils/date';
import type { Printer, PrinterStatus } from '../api/client';

interface PrinterInfoModalProps {
  printer: Printer;
  status?: PrinterStatus;
  totalPrintHours?: number;
  onClose: () => void;
}

function getPrinterImage(model: string | null | undefined): string {
  if (!model) return '/img/printers/default.png';
  const m = model.toLowerCase().replace(/\s+/g, '');
  if (m.includes('x1e')) return '/img/printers/x1e.png';
  if (m.includes('x1c') || m.includes('x1carbon')) return '/img/printers/x1c.png';
  if (m.includes('x1')) return '/img/printers/x1c.png';
  if (m.includes('h2dpro') || m.includes('h2d-pro')) return '/img/printers/h2dpro.png';
  if (m.includes('h2d')) return '/img/printers/h2d.png';
  if (m.includes('h2c')) return '/img/printers/h2c.png';
  if (m.includes('h2s')) return '/img/printers/h2d.png';
  if (m.includes('p2s')) return '/img/printers/p1s.png';
  if (m.includes('p1s')) return '/img/printers/p1s.png';
  if (m.includes('p1p')) return '/img/printers/p1p.png';
  if (m.includes('a1mini')) return '/img/printers/a1mini.png';
  if (m.includes('a1')) return '/img/printers/a1.png';
  return '/img/printers/default.png';
}

function getWifiLabel(rssi: number): { key: string; color: string } {
  if (rssi >= -50) return { key: 'printers.wifiSignal.excellent', color: 'text-bambu-green' };
  if (rssi >= -60) return { key: 'printers.wifiSignal.good', color: 'text-bambu-green' };
  if (rssi >= -70) return { key: 'printers.wifiSignal.fair', color: 'text-yellow-400' };
  if (rssi >= -80) return { key: 'printers.wifiSignal.weak', color: 'text-orange-400' };
  return { key: 'printers.wifiSignal.veryWeak', color: 'text-red-400' };
}

function CopyButton({ value }: { value: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard may not be available in non-secure contexts
    }
  };

  return (
    <button
      onClick={handleCopy}
      className="ml-2 p-1 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors"
      title={copied ? t('printers.copied') : t('printers.copyToClipboard')}
    >
      {copied ? <Check className="w-3.5 h-3.5 text-bambu-green" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}

export function PrinterInfoModal({ printer, status, totalPrintHours, onClose }: PrinterInfoModalProps) {
  const { t } = useTranslation();

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const rows: { label: string; value: React.ReactNode }[] = [];

  // Model
  rows.push({
    label: t('printers.model'),
    value: printer.model ?? '—',
  });

  // Connection Status
  rows.push({
    label: t('common.status'),
    value: (
      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${
        status?.connected
          ? 'bg-bambu-green/20 text-bambu-green'
          : 'bg-red-500/20 text-red-400'
      }`}>
        <span className={`w-1.5 h-1.5 rounded-full ${status?.connected ? 'bg-bambu-green' : 'bg-red-400'}`} />
        {status?.connected ? t('printers.status.available') : t('printers.status.offline')}
      </span>
    ),
  });

  // State
  if (status?.state) {
    const stateMap: Record<string, string> = {
      IDLE: 'printers.status.idle',
      RUNNING: 'printers.status.printing',
      PAUSE: 'printers.status.paused',
      FINISH: 'printers.status.finished',
      FAILED: 'printers.status.error',
    };
    rows.push({
      label: t('printers.state'),
      value: t(stateMap[status.state] ?? 'printers.status.unknown'),
    });
  }

  // IP Address
  rows.push({
    label: t('printers.ipAddress'),
    value: (
      <span className="flex items-center">
        <span className="font-mono">{printer.ip_address}</span>
        <CopyButton value={printer.ip_address} />
      </span>
    ),
  });

  // Serial Number
  rows.push({
    label: t('printers.serialNumber'),
    value: (
      <span className="flex items-center">
        <span className="font-mono truncate">{printer.serial_number}</span>
        <CopyButton value={printer.serial_number} />
      </span>
    ),
  });

  // WiFi Signal
  if (status?.wifi_signal != null) {
    const wifi = getWifiLabel(status.wifi_signal);
    rows.push({
      label: t('printers.wifiSignalLabel'),
      value: (
        <span className="flex items-center gap-2">
          <Signal className={`w-4 h-4 ${wifi.color}`} />
          <span className={wifi.color}>{t(wifi.key)}</span>
          <span className="text-bambu-gray text-xs">({status.wifi_signal} dBm)</span>
        </span>
      ),
    });
  }

  // Firmware
  rows.push({
    label: t('printers.firmware'),
    value: status?.firmware_version ?? '—',
  });

  // Developer Mode
  if (status?.developer_mode != null) {
    rows.push({
      label: t('printers.developerMode'),
      value: (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
          status.developer_mode
            ? 'bg-bambu-green/20 text-bambu-green'
            : 'bg-bambu-dark-tertiary text-bambu-gray'
        }`}>
          {status.developer_mode ? t('printers.enabled') : t('printers.disabled')}
        </span>
      ),
    });
  }

  // Nozzle Count
  rows.push({
    label: t('printers.nozzleCount'),
    value: printer.nozzle_count,
  });

  // SD Card
  if (status?.sdcard != null) {
    rows.push({
      label: t('printers.sdCard'),
      value: (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
          status.sdcard
            ? 'bg-bambu-green/20 text-bambu-green'
            : 'bg-bambu-dark-tertiary text-bambu-gray'
        }`}>
          {status.sdcard ? t('printers.inserted') : t('printers.notInserted')}
        </span>
      ),
    });
  }

  // Auto-Archive
  rows.push({
    label: t('printers.autoArchive'),
    value: (
      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
        printer.auto_archive
          ? 'bg-bambu-green/20 text-bambu-green'
          : 'bg-bambu-dark-tertiary text-bambu-gray'
      }`}>
        {printer.auto_archive ? t('printers.enabled') : t('printers.disabled')}
      </span>
    ),
  });

  // Total Print Hours
  if (totalPrintHours != null && totalPrintHours > 0) {
    rows.push({
      label: t('printers.totalPrintHours'),
      value: `${Math.round(totalPrintHours)}h`,
    });
  }

  // Location
  if (printer.location) {
    rows.push({
      label: t('printers.sort.location'),
      value: printer.location,
    });
  }

  // Added date
  rows.push({
    label: t('printers.addedOn'),
    value: formatDateOnly(printer.created_at),
  });

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <Card className="w-full max-w-md" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">
              {printer.name}
            </h2>
            <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded flex-shrink-0">
              <X className="w-5 h-5 text-bambu-gray" />
            </button>
          </div>

          {/* Printer Image */}
          <div className="flex justify-center mb-4">
            <img
              src={getPrinterImage(printer.model)}
              alt={printer.model ?? printer.name}
              className="h-24 object-contain"
            />
          </div>

          <div className="space-y-0">
            {rows.map((row, i) => (
              <div key={i} className="flex items-center justify-between gap-4 py-2.5 border-b border-bambu-dark-tertiary last:border-0">
                <span className="text-sm text-bambu-gray whitespace-nowrap">{row.label}</span>
                <span className="text-sm text-white text-right">{row.value}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
