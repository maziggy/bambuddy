import { useTranslation } from 'react-i18next';
import { spoolbuddyApi } from '../../api/client';

interface WeightDisplayProps {
  weight: number | null;
  weightStable: boolean;
  deviceOnline: boolean;
  deviceId: string | null;
}

export function WeightDisplay({ weight, weightStable, deviceOnline, deviceId }: WeightDisplayProps) {
  const { t } = useTranslation();

  const handleTare = async () => {
    if (!deviceId) return;
    try {
      await spoolbuddyApi.tare(deviceId);
    } catch (e) {
      console.error('Failed to tare:', e);
    }
  };

  const formatWeight = (w: number | null) => {
    if (w === null) return '--.-';
    return w.toFixed(1);
  };

  return (
    <div className="flex flex-col items-center gap-3">
      {/* Weight readout */}
      <div className="flex items-baseline gap-2">
        <span className="text-5xl font-light tabular-nums text-zinc-100">
          {formatWeight(weight)}
        </span>
        <span className="text-xl text-zinc-400">g</span>
      </div>

      {/* Stability indicator */}
      <div className="flex items-center gap-2">
        <div className={`w-2 h-2 rounded-full ${
          !deviceOnline
            ? 'bg-zinc-600'
            : weightStable
            ? 'bg-green-500 shadow-[0_0_6px_rgba(34,197,94,0.5)]'
            : 'bg-amber-500 animate-pulse'
        }`} />
        <span className="text-xs text-zinc-400">
          {!deviceOnline
            ? t('spoolbuddy.weight.noReading', 'No reading')
            : weightStable
            ? t('spoolbuddy.weight.stable', 'Stable')
            : t('spoolbuddy.weight.measuring', 'Measuring...')}
        </span>
      </div>

      {/* Tare button */}
      <button
        onClick={handleTare}
        disabled={!deviceOnline || !deviceId}
        className="px-4 py-2 text-sm font-medium rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors min-h-[40px]"
      >
        {t('spoolbuddy.weight.tare', 'Tare')}
      </button>
    </div>
  );
}
