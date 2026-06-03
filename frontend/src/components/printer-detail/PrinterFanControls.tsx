import { useEffect, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AirVent, Fan } from 'lucide-react';
import { api } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import type { PrinterControlCapabilities } from '../../utils/printerCapabilities';

const FAN_PRESETS = [0, 25, 50, 75, 100] as const;

interface FanRowProps {
  label: string;
  icon: React.ReactNode;
  reportedPercent: number;
  fanIndex: 1 | 2 | 3;
  printerId: number;
  disabled: boolean;
}

function FanRow({ label, icon, reportedPercent, fanIndex, printerId, disabled }: FanRowProps) {
  const { showToast } = useToast();
  const [localPercent, setLocalPercent] = useState(reportedPercent);
  const pendingRef = useRef(false);
  const lastSentRef = useRef(reportedPercent);

  useEffect(() => {
    if (!pendingRef.current) {
      setLocalPercent(reportedPercent);
      lastSentRef.current = reportedPercent;
    }
  }, [reportedPercent]);

  const mutation = useMutation({
    mutationFn: (p: number) => api.setFanSpeed(printerId, fanIndex, p),
    onError: (error: Error) => {
      pendingRef.current = false;
      setLocalPercent(lastSentRef.current);
      showToast(error.message, 'error');
    },
    onSettled: () => {
      pendingRef.current = false;
    },
  });

  const commit = (value: number) => {
    if (disabled || value === lastSentRef.current) return;
    pendingRef.current = true;
    lastSentRef.current = value;
    mutation.mutate(value);
  };

  return (
    <div className="p-2 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary space-y-2">
      <div className="flex items-center gap-2">
        <div className="text-bambu-gray flex-shrink-0">{icon}</div>
        <span className="text-xs text-bambu-gray flex-1">{label}</span>
        <span className="text-xs tabular-nums text-white font-medium">{localPercent}%</span>
      </div>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={localPercent}
        disabled={disabled || mutation.isPending}
        onChange={(e) => setLocalPercent(parseInt(e.target.value, 10))}
        onPointerUp={(e) => commit(parseInt((e.target as HTMLInputElement).value, 10))}
        onKeyUp={(e) => {
          if (e.key === 'Enter') commit(localPercent);
        }}
        className="w-full accent-bambu-green disabled:opacity-50 h-1.5 cursor-pointer"
      />
      <div className="flex gap-1">
        {FAN_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            disabled={disabled || mutation.isPending}
            onClick={() => {
              setLocalPercent(preset);
              commit(preset);
            }}
            className={`flex-1 py-0.5 rounded text-[10px] transition-colors ${
              localPercent === preset
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'bg-bambu-dark-secondary text-bambu-gray hover:bg-bambu-dark-tertiary'
            }`}
          >
            {preset}%
          </button>
        ))}
      </div>
    </div>
  );
}

interface PrinterFanControlsProps {
  printerId: number;
  capabilities: PrinterControlCapabilities;
  coolingFanSpeed: number | null;
  auxFanSpeed: number | null;
  chamberFanSpeed: number | null;
  canControl: boolean;
  connected: boolean;
}

export function PrinterFanControls({
  printerId,
  capabilities,
  coolingFanSpeed,
  auxFanSpeed,
  chamberFanSpeed,
  canControl,
  connected,
}: PrinterFanControlsProps) {
  const { t } = useTranslation();
  const disabled = !canControl || !connected;

  const fans: {
    label: string;
    icon: React.ReactNode;
    percent: number;
    index: 1 | 2 | 3;
  }[] = [];

  if (capabilities.showPartFan) {
    fans.push({
      label: t('printers.fans.partCooling'),
      icon: <Fan className="w-4 h-4" />,
      percent: coolingFanSpeed ?? 0,
      index: 1,
    });
  }
  if (capabilities.showAuxFan) {
    fans.push({
      label: t('printers.fans.auxiliary'),
      icon: <Fan className="w-4 h-4" />,
      percent: auxFanSpeed ?? 0,
      index: 2,
    });
  }
  if (capabilities.showChamberFan) {
    fans.push({
      label: t('printers.fans.chamber'),
      icon: <AirVent className="w-4 h-4" />,
      percent: chamberFanSpeed ?? 0,
      index: 3,
    });
  }

  if (fans.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-bambu-gray">
        {t('printerDetail.fans')}
      </h3>
      {fans.map((f) => (
        <FanRow
          key={f.index}
          label={f.label}
          icon={f.icon}
          reportedPercent={f.percent}
          fanIndex={f.index}
          printerId={printerId}
          disabled={disabled}
        />
      ))}
    </div>
  );
}
