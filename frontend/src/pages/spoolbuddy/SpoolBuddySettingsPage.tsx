import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useOutletContext } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { SpoolBuddyOutletContext } from '../../components/spoolbuddy/SpoolBuddyLayout';
import { spoolbuddyApi, type SpoolBuddyDevice } from '../../api/client';

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function ScaleCalibration({ device, weight, weightStable, rawAdc }: {
  device: SpoolBuddyDevice;
  weight: number | null;
  weightStable: boolean;
  rawAdc: number | null;
}) {
  const { t } = useTranslation();
  const [calibrating, setCalibrating] = useState(false);
  const [calStep, setCalStep] = useState<'idle' | 'tare' | 'weight'>('idle');
  const [knownWeight, setKnownWeight] = useState(500);
  const [taring, setTaring] = useState(false);

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
        await spoolbuddyApi.tare(device.device_id);
        setCalStep('weight');
      } catch (e) {
        console.error('Failed to tare:', e);
      } finally {
        setCalibrating(false);
      }
    } else if (calStep === 'weight') {
      if (rawAdc === null) return;
      setCalibrating(true);
      try {
        await spoolbuddyApi.setCalibrationFactor(device.device_id, knownWeight, rawAdc);
        setCalStep('idle');
      } catch (e) {
        console.error('Failed to calibrate:', e);
      } finally {
        setCalibrating(false);
      }
    }
  };

  return (
    <div className="bg-zinc-800 rounded-lg p-4">
      <h3 className="text-base font-semibold text-zinc-100 mb-4">
        {t('spoolbuddy.settings.scaleCalibration', 'Scale Calibration')}
      </h3>

      {/* Current weight */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-zinc-400">{t('spoolbuddy.settings.currentWeight', 'Current weight')}</span>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${weightStable ? 'bg-green-500' : 'bg-amber-500 animate-pulse'}`} />
          <span className="text-sm font-mono text-zinc-200">
            {weight !== null ? `${weight.toFixed(1)} g` : '-- g'}
          </span>
        </div>
      </div>

      {/* Tare offset + calibration factor */}
      <div className="grid grid-cols-2 gap-4 mb-4 text-xs">
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.settings.tareOffset', 'Tare offset')}</span>
          <span className="text-zinc-400 font-mono">{device.tare_offset}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.settings.calFactor', 'Cal. factor')}</span>
          <span className="text-zinc-400 font-mono">{device.calibration_factor.toFixed(2)}</span>
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
        <div className="border border-zinc-700 rounded-lg p-3 space-y-3">
          <div className="text-sm font-medium text-zinc-200">
            {calStep === 'tare'
              ? t('spoolbuddy.settings.calStep1', 'Step 1: Remove all items from the scale')
              : t('spoolbuddy.settings.calStep2', 'Step 2: Place known weight on scale')}
          </div>

          {calStep === 'weight' && (
            <div className="flex items-center gap-2">
              <label className="text-xs text-zinc-400">{t('spoolbuddy.settings.knownWeight', 'Known weight (g)')}</label>
              <input
                type="number"
                value={knownWeight}
                onChange={(e) => setKnownWeight(Number(e.target.value))}
                className="w-24 px-2 py-2 bg-zinc-900 border border-zinc-600 rounded text-sm text-zinc-100 focus:outline-none focus:border-green-500 min-h-[44px]"
                min={1}
              />
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

function DeviceInfoCard({ device }: { device: SpoolBuddyDevice }) {
  const { t } = useTranslation();

  return (
    <div className="bg-zinc-800 rounded-lg p-4">
      <h3 className="text-base font-semibold text-zinc-100 mb-4">
        {t('spoolbuddy.settings.deviceInfo', 'Device Info')}
      </h3>

      <div className="space-y-2 text-sm">
        <div className="flex justify-between">
          <span className="text-zinc-500">Device ID</span>
          <span className="text-zinc-300 font-mono text-xs">{device.device_id}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.settings.hostname', 'Hostname')}</span>
          <span className="text-zinc-300">{device.hostname}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">IP</span>
          <span className="text-zinc-300">{device.ip_address}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.settings.firmware', 'Firmware')}</span>
          <span className="text-zinc-300">{device.firmware_version ?? '-'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">NFC</span>
          <span className={device.nfc_ok ? 'text-green-400' : 'text-zinc-500'}>
            {device.nfc_ok ? t('spoolbuddy.status.nfcReady', 'Ready') : t('spoolbuddy.status.nfcOff', 'Off')}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.settings.scale', 'Scale')}</span>
          <span className={device.scale_ok ? 'text-green-400' : 'text-red-400'}>
            {device.scale_ok ? 'OK' : t('common.error', 'Error')}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.settings.uptime', 'Uptime')}</span>
          <span className="text-zinc-300">{formatUptime(device.uptime_s)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">{t('spoolbuddy.status.status', 'Status')}</span>
          <span className={device.online ? 'text-green-400' : 'text-zinc-500'}>
            {device.online ? t('spoolbuddy.status.online', 'Online') : t('spoolbuddy.status.offline', 'Offline')}
          </span>
        </div>
      </div>
    </div>
  );
}

export function SpoolBuddySettingsPage() {
  const { sbState } = useOutletContext<SpoolBuddyOutletContext>();
  const { t } = useTranslation();

  const { data: devices = [] } = useQuery({
    queryKey: ['spoolbuddy-devices'],
    queryFn: () => spoolbuddyApi.getDevices(),
    refetchInterval: 10000,
  });

  // Use first device (most common setup) or find one matching current state
  const device = sbState.deviceId
    ? devices.find((d) => d.device_id === sbState.deviceId) ?? devices[0]
    : devices[0];

  return (
    <div className="h-full flex flex-col p-4">
      <h1 className="text-xl font-semibold text-zinc-100 mb-4">
        {t('spoolbuddy.nav.settings', 'Settings')}
      </h1>

      <div className="flex-1 min-h-0 overflow-y-auto space-y-4">
        {!device ? (
          <div className="flex items-center justify-center h-32">
            <div className="text-center text-zinc-500">
              <p className="text-sm">{t('spoolbuddy.settings.noDevice', 'No SpoolBuddy device found')}</p>
            </div>
          </div>
        ) : (
          <>
            <ScaleCalibration
              device={device}
              weight={sbState.weight}
              weightStable={sbState.weightStable}
              rawAdc={sbState.rawAdc}
            />
            <DeviceInfoCard device={device} />
          </>
        )}
      </div>
    </div>
  );
}
