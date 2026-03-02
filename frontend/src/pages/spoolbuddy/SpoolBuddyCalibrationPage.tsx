import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { SpoolBuddyOutletContext } from '../../components/spoolbuddy/SpoolBuddyLayout';
import { spoolbuddyApi, type SpoolBuddyDevice } from '../../api/client';

function ScaleCalibration({ device, weight, weightStable, rawAdc }: {
  device: SpoolBuddyDevice;
  weight: number | null;
  weightStable: boolean;
  rawAdc: number | null;
}) {
  const { t } = useTranslation();
  const [calibrating, setCalibrating] = useState(false);
  const [calStep, setCalStep] = useState<'idle' | 'tare' | 'weight'>('idle');
  const [knownWeight, setKnownWeight] = useState('500');
  const [tareRawAdc, setTareRawAdc] = useState<number | null>(null);
  const [taring, setTaring] = useState(false);

  const numpadPress = (key: string) => {
    if (key === 'backspace') {
      setKnownWeight((v) => v.slice(0, -1) || '');
    } else if (key === '.' && !knownWeight.includes('.')) {
      setKnownWeight((v) => v + '.');
    } else if (key >= '0' && key <= '9') {
      setKnownWeight((v) => (v === '0' ? key : v + key));
    }
  };

  const handleTare = async () => {
    setTaring(true);
    try {
      await spoolbuddyApi.tare(device.device_id);
    } catch (e) {
      console.error('Failed to tare:', e);
    } finally {
      setTaring(false);
    }
  };

  const startCalibration = () => {
    setCalStep('tare');
  };

  const handleCalStep = async () => {
    if (calStep === 'tare') {
      setCalibrating(true);
      try {
        // Capture raw ADC before taring — this is our zero reference
        setTareRawAdc(rawAdc);
        await spoolbuddyApi.tare(device.device_id);
        setCalStep('weight');
      } catch (e) {
        console.error('Failed to tare:', e);
      } finally {
        setCalibrating(false);
      }
    } else if (calStep === 'weight') {
      const weightNum = parseFloat(knownWeight);
      if (rawAdc === null || !weightNum || weightNum <= 0) return;
      setCalibrating(true);
      try {
        await spoolbuddyApi.setCalibrationFactor(device.device_id, weightNum, rawAdc, tareRawAdc ?? undefined);
        setCalStep('idle');
      } catch (e) {
        console.error('Failed to calibrate:', e);
      } finally {
        setCalibrating(false);
      }
    }
  };

  return (
    <div className="space-y-4">
      {/* Current weight */}
      <div className="bg-zinc-800 rounded-lg p-4">
        <div className="flex items-center justify-between">
          <span className="text-sm text-zinc-400">{t('spoolbuddy.settings.currentWeight', 'Current weight')}</span>
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${weightStable ? 'bg-green-500' : 'bg-amber-500 animate-pulse'}`} />
            <span className="text-lg font-mono text-zinc-200">
              {weight !== null ? `${weight.toFixed(1)} g` : '-- g'}
            </span>
          </div>
        </div>

        {/* Tare offset + calibration factor */}
        <div className="grid grid-cols-2 gap-4 mt-3 text-xs">
          <div className="flex justify-between">
            <span className="text-zinc-500">{t('spoolbuddy.settings.tareOffset', 'Tare offset')}</span>
            <span className="text-zinc-400 font-mono">{device.tare_offset}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-zinc-500">{t('spoolbuddy.settings.calFactor', 'Cal. factor')}</span>
            <span className="text-zinc-400 font-mono">{device.calibration_factor.toFixed(2)}</span>
          </div>
        </div>
      </div>

      {/* Calibration flow */}
      {calStep === 'idle' ? (
        <div className="flex gap-2">
          <button
            onClick={handleTare}
            disabled={taring}
            className="flex-1 px-4 py-2.5 rounded-lg text-sm font-medium bg-zinc-700 text-zinc-200 hover:bg-zinc-600 disabled:opacity-40 transition-colors min-h-[44px]"
          >
            {taring ? '...' : t('spoolbuddy.weight.tare', 'Tare')}
          </button>
          <button
            onClick={startCalibration}
            className="flex-1 px-4 py-2.5 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 transition-colors min-h-[44px]"
          >
            {t('spoolbuddy.weight.calibrate', 'Calibrate')}
          </button>
        </div>
      ) : (
        <div className="bg-zinc-800 border border-zinc-700 rounded-lg p-3 space-y-2">
          <div className="text-sm font-medium text-zinc-200">
            {calStep === 'tare'
              ? t('spoolbuddy.settings.calStep1', 'Step 1: Remove all items from the scale')
              : t('spoolbuddy.settings.calStep2', 'Step 2: Place known weight on scale')}
          </div>

          {calStep === 'weight' && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="text-xs text-zinc-400">{t('spoolbuddy.settings.knownWeight', 'Known weight (g)')}</span>
                <div className="flex-1 bg-zinc-900 border border-zinc-600 rounded px-3 py-1.5 text-right text-base font-mono text-zinc-100">
                  {knownWeight || '0'}<span className="text-zinc-500 ml-1">g</span>
                </div>
              </div>
              <div className="grid grid-cols-4 gap-1">
                {['7','8','9','backspace','4','5','6','.','1','2','3','0'].map((key) => (
                  <button
                    key={key}
                    onClick={() => numpadPress(key)}
                    className={`py-2 rounded text-sm font-medium transition-colors min-h-[36px] ${
                      key === 'backspace'
                        ? 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'
                        : 'bg-zinc-800 text-zinc-100 hover:bg-zinc-700 border border-zinc-700'
                    }`}
                  >
                    {key === 'backspace' ? '\u232B' : key}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="flex gap-2">
            <button
              onClick={() => setCalStep('idle')}
              className="flex-1 px-4 py-2 rounded-lg text-sm bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors min-h-[40px]"
            >
              {t('common.cancel', 'Cancel')}
            </button>
            <button
              onClick={handleCalStep}
              disabled={calibrating}
              className="flex-1 px-4 py-2 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-40 transition-colors min-h-[40px]"
            >
              {calibrating ? '...' : calStep === 'tare' ? t('spoolbuddy.settings.setZero', 'Set Zero') : t('spoolbuddy.settings.calibrateNow', 'Calibrate')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function SpoolBuddyCalibrationPage() {
  const { sbState } = useOutletContext<SpoolBuddyOutletContext>();
  const { t } = useTranslation();
  const navigate = useNavigate();

  const { data: devices = [] } = useQuery({
    queryKey: ['spoolbuddy-devices'],
    queryFn: () => spoolbuddyApi.getDevices(),
    refetchInterval: 10000,
  });

  const device = sbState.deviceId
    ? devices.find((d) => d.device_id === sbState.deviceId) ?? devices[0]
    : devices[0];

  return (
    <div className="h-full flex flex-col p-4">
      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={() => navigate('/spoolbuddy/settings')}
          className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <h1 className="text-xl font-semibold text-zinc-100">
          {t('spoolbuddy.settings.scaleCalibration', 'Scale Calibration')}
        </h1>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto">
        {!device ? (
          <div className="flex items-center justify-center h-32">
            <div className="text-center text-zinc-500">
              <p className="text-sm">{t('spoolbuddy.settings.noDevice', 'No SpoolBuddy device found')}</p>
            </div>
          </div>
        ) : (
          <ScaleCalibration
            device={device}
            weight={sbState.weight}
            weightStable={sbState.weightStable}
            rawAdc={sbState.rawAdc}
          />
        )}
      </div>
    </div>
  );
}
