import { useState, useCallback, useRef, useEffect } from 'react';
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

function formatDateTime(iso: string | null): string {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' +
      d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '-';
  }
}

const BLANK_OPTIONS = [
  { label: 'Off', value: 0 },
  { label: '1m', value: 60 },
  { label: '2m', value: 120 },
  { label: '5m', value: 300 },
  { label: '10m', value: 600 },
  { label: '30m', value: 1800 },
];

// --- Device Tab ---

function DeviceTab({ device }: { device: SpoolBuddyDevice }) {
  const { t } = useTranslation();

  return (
    <div className="space-y-4">
      {/* About */}
      <div className="bg-zinc-800 rounded-lg p-4">
        <div className="flex items-center gap-3 mb-2">
          <img src="/img/spoolbuddy_logo_dark_small.png" alt="SpoolBuddy" className="h-7 w-auto" />
        </div>
        <p className="text-xs text-zinc-500 mb-1">Part of Bambuddy</p>
        <span className="text-xs text-zinc-500">github.com/maziggy/bambuddy</span>
      </div>

      {/* NFC Reader + Device Info side by side */}
      <div className="grid grid-cols-2 gap-3">
        {/* NFC Reader */}
        <div className="bg-zinc-800 rounded-lg p-3">
          <h3 className="text-sm font-semibold text-zinc-300 mb-2">
            {t('spoolbuddy.settings.nfcReader', 'NFC Reader')}
          </h3>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between">
              <span className="text-zinc-500">{t('spoolbuddy.settings.type', 'Type')}</span>
              <span className="text-zinc-300 font-mono">
                {device.nfc_reader_type || 'N/A'}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">{t('spoolbuddy.settings.connection', 'Connection')}</span>
              <span className="text-zinc-300 font-mono">
                {device.nfc_connection || 'N/A'}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-zinc-500">{t('spoolbuddy.status.status', 'Status')}</span>
              <div className="flex items-center gap-1.5">
                <div className={`w-2 h-2 rounded-full ${
                  device.nfc_ok ? 'bg-green-500' : device.nfc_reader_type ? 'bg-red-500' : 'bg-zinc-600'
                }`} />
                <span className={
                  device.nfc_ok ? 'text-green-400' : device.nfc_reader_type ? 'text-red-400' : 'text-zinc-500'
                }>
                  {device.nfc_ok
                    ? t('spoolbuddy.status.nfcReady', 'Ready')
                    : device.nfc_reader_type
                      ? t('common.error', 'Error')
                      : t('spoolbuddy.settings.notConnected', 'N/A')}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Device Info */}
        <div className="bg-zinc-800 rounded-lg p-3">
          <h3 className="text-sm font-semibold text-zinc-300 mb-2">
            {t('spoolbuddy.settings.deviceInfo', 'Device Info')}
          </h3>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between">
              <span className="text-zinc-500">{t('spoolbuddy.settings.hostname', 'Host')}</span>
              <span className="text-zinc-300 truncate ml-2">{device.hostname}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">IP</span>
              <span className="text-zinc-300">{device.ip_address}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">{t('spoolbuddy.settings.uptime', 'Uptime')}</span>
              <span className="text-zinc-300">{formatUptime(device.uptime_s)}</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-zinc-500">{t('spoolbuddy.status.status', 'Status')}</span>
              <div className="flex items-center gap-1.5">
                <div className={`w-2 h-2 rounded-full ${device.online ? 'bg-green-500' : 'bg-zinc-600'}`} />
                <span className={device.online ? 'text-green-400' : 'text-zinc-500'}>
                  {device.online ? t('spoolbuddy.status.online', 'Online') : t('spoolbuddy.status.offline', 'Offline')}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Device ID (full width, below cards) */}
      <div className="bg-zinc-800 rounded-lg px-3 py-2 flex justify-between items-center text-xs">
        <span className="text-zinc-500">Device ID</span>
        <span className="text-zinc-400 font-mono">{device.device_id}</span>
      </div>
    </div>
  );
}

// --- Display Tab ---

function DisplayTab({ device, onBrightnessChange, onBlankTimeoutChange }: {
  device: SpoolBuddyDevice;
  onBrightnessChange: (value: number) => void;
  onBlankTimeoutChange: (value: number) => void;
}) {
  const { t } = useTranslation();
  const [brightness, setBrightness] = useState(device.display_brightness);
  const [blankTimeout, setBlankTimeout] = useState(device.display_blank_timeout);
  const [saved, setSaved] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const savedTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Sync local state when device data updates from server
  useEffect(() => {
    setBrightness(device.display_brightness);
    setBlankTimeout(device.display_blank_timeout);
  }, [device.display_brightness, device.display_blank_timeout]);

  const showSaved = useCallback(() => {
    setSaved(true);
    if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
    savedTimerRef.current = setTimeout(() => setSaved(false), 1500);
  }, []);

  const sendDisplayUpdate = useCallback((b: number, bt: number) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      spoolbuddyApi.updateDisplay(device.device_id, b, bt)
        .then(() => showSaved())
        .catch((e) => console.error('Failed to update display:', e));
    }, 300);
  }, [device.device_id, showSaved]);

  const handleBrightnessChange = (value: number) => {
    setBrightness(value);
    onBrightnessChange(value);  // Instant layout update
    sendDisplayUpdate(value, blankTimeout);
  };

  const handleBlankTimeoutChange = (value: number) => {
    setBlankTimeout(value);
    onBlankTimeoutChange(value);  // Instant layout update
    sendDisplayUpdate(brightness, value);
  };

  return (
    <div className="space-y-4">
      {/* Brightness */}
      <div className="bg-zinc-800 rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-zinc-300">
            {t('spoolbuddy.settings.brightness', 'Brightness')}
          </h3>
          {saved && (
            <span className="text-xs text-green-400 flex items-center gap-1 animate-pulse">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
              {t('spoolbuddy.settings.saved', 'Saved')}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <svg className="w-4 h-4 text-zinc-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
          </svg>
          <input
            type="range"
            min={0}
            max={100}
            value={brightness}
            onChange={(e) => handleBrightnessChange(parseInt(e.target.value))}
            className="flex-1 h-2 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-green-500"
          />
          <span className="text-sm font-mono text-zinc-400 w-10 text-right">{brightness}%</span>
        </div>
        {!device.has_backlight && (
          <p className="text-xs text-zinc-600 mt-2">
            {t('spoolbuddy.settings.noBacklight', 'No DSI backlight detected. Brightness control requires a DSI display.')}
          </p>
        )}
      </div>

      {/* Screen blank timeout */}
      <div className="bg-zinc-800 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-zinc-300 mb-1">
          {t('spoolbuddy.settings.screenBlank', 'Screen Blank Timeout')}
        </h3>
        <p className="text-xs text-zinc-500 mb-3">
          {t('spoolbuddy.settings.screenBlankDesc', 'Screen turns off after inactivity. Touch to wake.')}
        </p>
        <div className="grid grid-cols-3 gap-2">
          {BLANK_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => handleBlankTimeoutChange(opt.value)}
              className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors min-h-[40px] ${
                blankTimeout === opt.value
                  ? 'bg-green-600 text-white'
                  : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <p className="text-xs text-zinc-600 text-center">
        {t('spoolbuddy.settings.displayNote', 'Brightness is applied as a software filter.')}
      </p>
    </div>
  );
}

// --- Scale Tab ---

function StepIndicator({ step, labels }: { step: 'tare' | 'weight'; labels: { tare: string; weight: string } }) {
  return (
    <div className="flex flex-col items-center w-16 shrink-0 pt-1">
      {/* Step 1 circle */}
      <div className={`flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold ${
        step === 'tare'
          ? 'bg-green-600 text-white'
          : 'bg-green-600/20 text-green-400'
      }`}>
        {step === 'weight' ? (
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        ) : '1'}
      </div>
      <span className={`text-[10px] mt-0.5 ${step === 'tare' ? 'text-green-400 font-medium' : 'text-green-400/60'}`}>
        {labels.tare}
      </span>

      {/* Connector line */}
      <div className={`w-px h-5 my-1 ${step === 'weight' ? 'bg-green-600/40' : 'bg-zinc-700'}`} />

      {/* Step 2 circle */}
      <div className={`flex items-center justify-center w-7 h-7 rounded-full text-xs font-bold ${
        step === 'weight'
          ? 'bg-green-600 text-white'
          : 'bg-zinc-700 text-zinc-500'
      }`}>
        2
      </div>
      <span className={`text-[10px] mt-0.5 ${step === 'weight' ? 'text-green-400 font-medium' : 'text-zinc-600'}`}>
        {labels.weight}
      </span>
    </div>
  );
}

function ScaleTab({ device, weight, weightStable, rawAdc }: {
  device: SpoolBuddyDevice;
  weight: number | null;
  weightStable: boolean;
  rawAdc: number | null;
}) {
  const { t } = useTranslation();
  const [calStep, setCalStep] = useState<'idle' | 'tare' | 'weight'>('idle');
  const [knownWeight, setKnownWeight] = useState('500');
  const [tareRawAdc, setTareRawAdc] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ type: 'ok' | 'error'; msg: string } | null>(null);

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
    setBusy(true);
    setStatus(null);
    try {
      await spoolbuddyApi.tare(device.device_id);
      setStatus({ type: 'ok', msg: t('spoolbuddy.settings.tareSet', 'Tare command sent. Waiting for device...') });
    } catch {
      setStatus({ type: 'error', msg: t('spoolbuddy.settings.tareFailed', 'Failed to send tare command') });
    } finally {
      setBusy(false);
    }
  };

  const handleCalStep = async () => {
    if (calStep === 'tare') {
      setBusy(true);
      setStatus(null);
      try {
        setTareRawAdc(rawAdc);
        await spoolbuddyApi.tare(device.device_id);
        setStatus({ type: 'ok', msg: t('spoolbuddy.settings.zeroSet', 'Zero point set. Place known weight on scale.') });
        setCalStep('weight');
      } catch {
        setStatus({ type: 'error', msg: t('spoolbuddy.settings.tareFailed', 'Failed to send tare command') });
      } finally {
        setBusy(false);
      }
    } else if (calStep === 'weight') {
      const weightNum = parseFloat(knownWeight);
      if (rawAdc === null || !weightNum || weightNum <= 0) return;
      setBusy(true);
      setStatus(null);
      try {
        await spoolbuddyApi.setCalibrationFactor(device.device_id, weightNum, rawAdc, tareRawAdc ?? undefined);
        setStatus({ type: 'ok', msg: t('spoolbuddy.settings.calibrationDone', 'Calibration complete!') });
        setCalStep('idle');
      } catch {
        setStatus({ type: 'error', msg: t('spoolbuddy.settings.calibrationFailed', 'Calibration failed') });
      } finally {
        setBusy(false);
      }
    }
  };

  // --- Idle state: weight card + buttons ---
  if (calStep === 'idle') {
    return (
      <div className="flex flex-col h-full">
        {/* Weight + info card */}
        <div className="bg-zinc-800 rounded-lg p-3 mb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${weightStable ? 'bg-green-500' : 'bg-amber-500 animate-pulse'}`} />
              <span className="text-lg font-mono text-zinc-200">
                {weight !== null ? `${weight.toFixed(1)} g` : '-- g'}
              </span>
            </div>
            <div className="text-xs text-zinc-500 text-right">
              <span>{t('spoolbuddy.settings.tareOffset', 'Tare')}: {device.tare_offset}</span>
              <span className="mx-1.5">&middot;</span>
              <span>{t('spoolbuddy.settings.calFactor', 'Factor')}: {device.calibration_factor.toFixed(2)}</span>
            </div>
          </div>
          {device.last_calibrated_at && (
            <div className="text-xs text-zinc-600 mt-1">
              {t('spoolbuddy.settings.lastCalibrated', 'Last calibrated')}: {formatDateTime(device.last_calibrated_at)}
            </div>
          )}
        </div>

        {/* Status message */}
        {status && (
          <div className={`rounded-lg px-3 py-2 mb-3 text-sm ${
            status.type === 'ok' ? 'bg-green-900/30 text-green-300 border border-green-800' : 'bg-red-900/30 text-red-300 border border-red-800'
          }`}>
            {status.msg}
          </div>
        )}

        {/* Action buttons */}
        <div className="flex gap-2">
          <button
            onClick={handleTare}
            disabled={busy}
            className="flex-1 px-4 py-2.5 rounded-lg text-sm font-medium bg-zinc-700 text-zinc-200 hover:bg-zinc-600 disabled:opacity-40 transition-colors min-h-[44px] flex items-center justify-center gap-2"
          >
            {busy && (
              <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            {t('spoolbuddy.weight.tare', 'Tare')}
          </button>
          <button
            onClick={() => { setCalStep('tare'); setStatus(null); }}
            className="flex-1 px-4 py-2.5 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 transition-colors min-h-[44px]"
          >
            {t('spoolbuddy.weight.calibrate', 'Calibrate')}
          </button>
        </div>
      </div>
    );
  }

  // --- Calibration wizard: step indicator left + content right ---
  return (
    <div className="flex gap-3">
      {/* Left: step indicator */}
      <StepIndicator step={calStep} labels={{ tare: t('spoolbuddy.weight.tare', 'Tare'), weight: t('spoolbuddy.settings.knownWeight', 'Known weight') }} />

      {/* Right: content */}
      <div className="flex-1 min-w-0">
        {/* Live weight bar */}
        <div className="flex items-center gap-2 bg-zinc-800 rounded-lg px-3 py-1.5 mb-1.5">
          <div className={`w-2 h-2 rounded-full shrink-0 ${weightStable ? 'bg-green-500' : 'bg-amber-500 animate-pulse'}`} />
          <span className="text-sm font-mono text-zinc-200">
            {weight !== null ? `${weight.toFixed(1)} g` : '-- g'}
          </span>
          <span className={`text-xs ml-auto ${weightStable ? 'text-green-400' : 'text-amber-400'}`}>
            {weightStable ? t('spoolbuddy.settings.stable', 'Stable') : t('spoolbuddy.settings.settling', 'Settling...')}
          </span>
        </div>

        {/* Status message */}
        {status && (
          <div className={`rounded-lg px-3 py-1.5 mb-1.5 text-xs ${
            status.type === 'ok' ? 'bg-green-900/30 text-green-300 border border-green-800' : 'bg-red-900/30 text-red-300 border border-red-800'
          }`}>
            {status.msg}
          </div>
        )}

        {/* Step content */}
        {calStep === 'tare' ? (
          <p className="text-sm text-zinc-300 mb-3">
            {t('spoolbuddy.settings.calStep1', 'Remove all items from the scale and press Set Zero.')}
          </p>
        ) : (
          <>
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-xs text-zinc-400 shrink-0">{t('spoolbuddy.settings.knownWeight', 'Known weight')}</span>
              <div className="flex-1 bg-zinc-900 border border-zinc-600 rounded px-3 py-1 text-right text-lg font-mono text-zinc-100">
                {knownWeight || '0'}<span className="text-zinc-500 ml-1">g</span>
              </div>
            </div>
            <div className="grid grid-cols-4 gap-1 mb-1.5">
              {['7','8','9','backspace','4','5','6','.','1','2','3','0'].map((key) => (
                <button
                  key={key}
                  onClick={() => numpadPress(key)}
                  className={`rounded text-lg font-medium transition-colors h-[48px] active:scale-95 ${
                    key === 'backspace'
                      ? 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'
                      : 'bg-zinc-800 text-zinc-100 hover:bg-zinc-700 border border-zinc-700'
                  }`}
                >
                  {key === 'backspace' ? '\u232B' : key}
                </button>
              ))}
            </div>
          </>
        )}

        {/* Action buttons */}
        <div className="flex gap-2">
          <button
            onClick={() => { setCalStep('idle'); setStatus(null); }}
            className="flex-1 px-4 py-2 rounded-lg text-sm bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors h-[40px]"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            onClick={handleCalStep}
            disabled={busy}
            className="flex-1 px-4 py-2 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-40 transition-colors h-[40px] flex items-center justify-center gap-2"
          >
            {busy && (
              <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            {calStep === 'tare' ? t('spoolbuddy.settings.setZero', 'Set Zero') : t('spoolbuddy.settings.calibrateNow', 'Calibrate')}
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Updates Tab ---

function UpdatesTab({ device }: { device: SpoolBuddyDevice }) {
  const { t } = useTranslation();
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sshExpanded, setSSHExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const isUpdating = device.update_status === 'pending' || device.update_status === 'updating';

  const { data: updateResult, isFetching: checking, refetch } = useQuery({
    queryKey: ['spoolbuddy-update-check', device.device_id],
    queryFn: () => spoolbuddyApi.checkDaemonUpdate(device.device_id),
    staleTime: 30 * 1000,
  });

  const { data: sshKeyData } = useQuery({
    queryKey: ['spoolbuddy-ssh-key'],
    queryFn: () => spoolbuddyApi.getSSHPublicKey(),
    enabled: sshExpanded,
    staleTime: Infinity,
  });

  const applyUpdate = async () => {
    setApplying(true);
    setError(null);
    try {
      await spoolbuddyApi.triggerUpdate(device.device_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to trigger update');
    } finally {
      setApplying(false);
    }
  };

  const copyKey = () => {
    if (sshKeyData?.public_key) {
      navigator.clipboard.writeText(sshKeyData.public_key);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const displayVersion = device.firmware_version
    || (updateResult?.current_version && updateResult.current_version !== '0.0.0' ? updateResult.current_version : null);

  return (
    <div className="space-y-3">
      {/* Version + Update status + Check — single card */}
      <div className="bg-zinc-800 rounded-lg p-3 space-y-3">
        {/* Version row */}
        <div className="flex justify-between items-center text-sm">
          <span className="text-zinc-500">{t('spoolbuddy.settings.currentVersion', 'Current Version')}</span>
          <span className="text-zinc-200 font-mono">
            {displayVersion || (
              <span className="text-zinc-500 italic text-xs">{t('spoolbuddy.settings.versionPending', 'Waiting for daemon...')}</span>
            )}
          </span>
        </div>

        {/* Update progress */}
        {isUpdating && (
          <div className="flex items-center gap-2 text-sm">
            <svg className="w-4 h-4 animate-spin text-green-400 flex-shrink-0" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <span className="text-green-300 text-xs">
              {device.update_message || t('spoolbuddy.settings.updateWaiting', 'Updating...')}
            </span>
          </div>
        )}

        {/* Update error */}
        {device.update_status === 'error' && (
          <p className="text-xs text-red-300">{device.update_message || t('spoolbuddy.settings.updateFailed', 'Update failed')}</p>
        )}

        {/* Error from trigger */}
        {error && <p className="text-xs text-red-300">{error}</p>}

        {/* Update available → Apply button */}
        {updateResult?.update_available ? (
          <>
            <p className="text-xs text-green-300">
              {t('spoolbuddy.settings.updateAvailable', 'Update available')}: {displayVersion} → {updateResult.latest_version}
            </p>
            <button
              onClick={applyUpdate}
              disabled={applying || isUpdating || !device.online}
              className="w-full px-3 py-2 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-40 transition-colors flex items-center justify-center gap-2"
            >
              {applying && (
                <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              )}
              {!device.online
                ? t('spoolbuddy.settings.deviceOffline', 'Device Offline')
                : t('spoolbuddy.settings.applyUpdate', 'Apply Update')}
            </button>
          </>
        ) : updateResult && !isUpdating ? (
          /* Up to date → Check + Force buttons side by side */
          <div className="flex gap-2">
            <button
              onClick={() => refetch()}
              disabled={checking || applying}
              className="flex-1 px-3 py-2 rounded-lg text-xs font-medium bg-zinc-700 text-zinc-300 hover:bg-zinc-600 disabled:opacity-40 transition-colors flex items-center justify-center gap-1"
            >
              {checking && (
                <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              )}
              {t('spoolbuddy.settings.checkUpdates', 'Check for Updates')}
            </button>
            <button
              onClick={applyUpdate}
              disabled={applying || isUpdating || !device.online}
              className="px-3 py-2 rounded-lg text-xs font-medium bg-zinc-700 text-zinc-400 hover:bg-zinc-600 hover:text-zinc-200 disabled:opacity-40 transition-colors flex items-center justify-center gap-1"
            >
              {applying && (
                <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              )}
              {t('spoolbuddy.settings.forceUpdate', 'Force Update')}
            </button>
          </div>
        ) : !isUpdating ? (
          /* No result yet → Check button */
          <button
            onClick={() => refetch()}
            disabled={checking}
            className="w-full px-3 py-2 rounded-lg text-sm font-medium bg-zinc-700 text-zinc-200 hover:bg-zinc-600 disabled:opacity-40 transition-colors flex items-center justify-center gap-2"
          >
            {checking && (
              <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            {t('spoolbuddy.settings.checkUpdates', 'Check for Updates')}
          </button>
        ) : null}
      </div>

      {/* SSH Setup — collapsible */}
      <div className="bg-zinc-800 rounded-lg p-3">
        <button
          onClick={() => setSSHExpanded(!sshExpanded)}
          className="w-full flex justify-between items-center text-xs"
        >
          <span className="font-medium text-zinc-400">
            {t('spoolbuddy.settings.sshSetup', 'SSH Setup')}
          </span>
          <svg
            className={`w-3 h-3 text-zinc-500 transition-transform ${sshExpanded ? 'rotate-180' : ''}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {sshExpanded && (
          <div className="mt-2 space-y-2">
            <p className="text-xs text-zinc-500">
              {t('spoolbuddy.settings.sshDescription', 'SSH key is deployed automatically. For manual setup, add this key to ~/.ssh/authorized_keys on the device.')}
            </p>
            {sshKeyData?.public_key ? (
              <div className="relative">
                <pre className="bg-zinc-900 rounded p-2 text-[10px] text-zinc-400 font-mono break-all whitespace-pre-wrap">
                  {sshKeyData.public_key}
                </pre>
                <button
                  onClick={copyKey}
                  className="absolute top-1 right-1 px-1.5 py-0.5 rounded text-[10px] bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors"
                >
                  {copied ? t('common.copied', 'Copied!') : t('common.copy', 'Copy')}
                </button>
              </div>
            ) : (
              <span className="text-[10px] text-zinc-500 italic">
                {t('spoolbuddy.settings.sshKeyLoading', 'Loading...')}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// --- Main Settings Page ---

type SettingsTab = 'device' | 'display' | 'scale' | 'updates';

export function SpoolBuddySettingsPage() {
  const { sbState, setDisplayBrightness, setDisplayBlankTimeout } = useOutletContext<SpoolBuddyOutletContext>();
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<SettingsTab>('device');

  const { data: devices = [] } = useQuery({
    queryKey: ['spoolbuddy-devices'],
    queryFn: () => spoolbuddyApi.getDevices(),
    refetchInterval: 10000,
  });

  // Use first device (most common setup) or find one matching current state
  const device = sbState.deviceId
    ? devices.find((d) => d.device_id === sbState.deviceId) ?? devices[0]
    : devices[0];


  const tabs: { id: SettingsTab; label: string }[] = [
    { id: 'device', label: t('spoolbuddy.settings.tabDevice', 'Device') },
    { id: 'display', label: t('spoolbuddy.settings.tabDisplay', 'Display') },
    { id: 'scale', label: t('spoolbuddy.settings.tabScale', 'Scale') },
    { id: 'updates', label: t('spoolbuddy.settings.tabUpdates', 'Updates') },
  ];

  return (
    <div className="h-full flex flex-col p-4">
      <h1 className="text-xl font-semibold text-zinc-100 mb-3">
        {t('spoolbuddy.nav.settings', 'Settings')}
      </h1>

      {/* Tab bar */}
      <div className="flex gap-1 bg-zinc-800/50 rounded-lg p-1 mb-4">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex-1 px-2 py-2 rounded-md text-sm font-medium transition-colors min-h-[36px] ${
              activeTab === tab.id
                ? 'bg-zinc-700 text-zinc-100'
                : 'text-zinc-500 hover:text-zinc-300'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {!device ? (
          <div className="flex items-center justify-center h-32">
            <div className="text-center text-zinc-500">
              <p className="text-sm">{t('spoolbuddy.settings.noDevice', 'No SpoolBuddy device found')}</p>
            </div>
          </div>
        ) : (
          <>
            {activeTab === 'device' && <DeviceTab device={device} />}
            {activeTab === 'display' && (
              <DisplayTab
                device={device}
                onBrightnessChange={setDisplayBrightness}
                onBlankTimeoutChange={setDisplayBlankTimeout}
              />
            )}
            {activeTab === 'scale' && (
              <ScaleTab
                device={device}
                weight={sbState.weight}
                weightStable={sbState.weightStable}
                rawAdc={sbState.rawAdc}
              />
            )}
            {activeTab === 'updates' && <UpdatesTab device={device} />}
          </>
        )}
      </div>
    </div>
  );
}
