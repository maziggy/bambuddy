import { useState, useCallback, useRef, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useOutletContext } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type { SpoolBuddyOutletContext } from '../../components/spoolbuddy/SpoolBuddyLayout';
import { spoolbuddyApi, type SpoolBuddyDevice, type DaemonUpdateCheck } from '../../api/client';
import { DiagnosticModal } from '../../components/spoolbuddy/DiagnosticModal';
import { FileText, Wand2, Zap } from 'lucide-react';
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
  const [diagnosticOpen, setDiagnosticOpen] = useState<'nfc' | 'scale' | 'read_tag' | null>(null);
  const [backendUrl, setBackendUrl] = useState('');
  const [apiToken, setApiToken] = useState('');
  const [systemBusy, setSystemBusy] = useState(false);
  const [systemMsg, setSystemMsg] = useState<{ type: 'ok' | 'error'; text: string } | null>(null);

  useEffect(() => {
    if (!backendUrl && device.backend_url) {
      setBackendUrl(device.backend_url);
    }
  }, [device.backend_url, backendUrl]);

  const saveConfig = async () => {
    if (!backendUrl.trim()) {
      setSystemMsg({ type: 'error', text: t('spoolbuddy.settings.systemFieldsRequired', 'Backend URL is required.') });
      return;
    }

    setSystemBusy(true);
    setSystemMsg(null);
    try {
      await spoolbuddyApi.updateSystemConfig(
        device.device_id,
        backendUrl.trim(),
        apiToken.trim() || undefined
      );
      setSystemMsg({ type: 'ok', text: t('spoolbuddy.settings.systemQueued', 'Config queued.') });
    } catch (e) {
      setSystemMsg({ type: 'error', text: e instanceof Error ? e.message : t('common.error', 'Error') });
    } finally {
      setSystemBusy(false);
    }
  };

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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {/* Backend/Auth Config */}
        <div className="bg-zinc-800 rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-semibold text-zinc-300">
            {t('spoolbuddy.settings.systemConfig', 'Backend & Auth')}
          </h3>

          <div className="space-y-2">
            <label className="text-xs text-zinc-500 block">
              {t('spoolbuddy.settings.backendUrl', 'Bambuddy Backend URL')}
            </label>
            <input
              value={backendUrl}
              onChange={(e) => setBackendUrl(e.target.value)}
              placeholder="http://192.168.1.100:5000"
              className="w-full px-3 py-2 rounded bg-zinc-900 border border-zinc-700 text-zinc-100 text-sm"
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs text-zinc-500 block">
              {t('spoolbuddy.settings.apiToken', 'API Token')}
            </label>
            <input
              type="password"
              value={apiToken}
              onChange={(e) => setApiToken(e.target.value)}
              placeholder={t('spoolbuddy.settings.apiTokenPlaceholder', 'Enter API token')}
              className="w-full px-3 py-2 rounded bg-zinc-900 border border-zinc-700 text-zinc-100 text-sm"
            />
          </div>

          <div className="flex gap-2">
            <button
              onClick={saveConfig}
              disabled={systemBusy}
              className="px-3 py-2 rounded bg-green-700 hover:bg-green-600 disabled:bg-zinc-700 text-sm font-medium text-zinc-100"
            >
              {t('spoolbuddy.settings.saveConfig', 'Save Config')}
            </button>
          </div>

          {systemMsg && (
            <div className={`text-xs ${systemMsg.type === 'ok' ? 'text-green-400' : 'text-red-400'}`}>
              {systemMsg.text}
            </div>
          )}
        </div>

        {/* Diagnostic Buttons */}
        <div className="bg-zinc-800 rounded-lg p-4 space-y-3">
          {/* NFC Diagnostic Button */}
          <button
            onClick={() => setDiagnosticOpen('nfc')}
            className="w-full bg-blue-700 hover:bg-blue-600 transition-colors rounded-lg p-3 text-left"
          >
            <div className="flex items-center gap-2 mb-1">
              <Wand2 className="w-4 h-4 text-blue-300" />
              <span className="text-sm font-semibold text-blue-100">
                {t('spoolbuddy.settings.nfcDiagnostic', 'NFC Diagnostic')}
              </span>
            </div>
            <p className="text-xs text-blue-200/70">
              {t('spoolbuddy.settings.testNfc', 'Test reader')}
            </p>
          </button>

          {/* Scale Diagnostic Button */}
          <button
            onClick={() => setDiagnosticOpen('scale')}
            className="w-full bg-yellow-700 hover:bg-yellow-600 transition-colors rounded-lg p-3 text-left"
          >
            <div className="flex items-center gap-2 mb-1">
              <Zap className="w-4 h-4 text-yellow-300" />
              <span className="text-sm font-semibold text-yellow-100">
                {t('spoolbuddy.settings.scaleDiagnostic', 'Scale Diagnostic')}
              </span>
            </div>
            <p className="text-xs text-yellow-200/70">
              {t('spoolbuddy.settings.testScale', 'Test accuracy')}
            </p>
          </button>

          {/* Read Tag Diagnostic Button */}
          <button
            onClick={() => setDiagnosticOpen('read_tag')}
            className="w-full bg-emerald-700 hover:bg-emerald-600 transition-colors rounded-lg p-3 text-left"
          >
            <div className="flex items-center gap-2 mb-1">
              <FileText className="w-4 h-4 text-emerald-300" />
              <span className="text-sm font-semibold text-emerald-100">
                {t('spoolbuddy.settings.readTagDiagnostic', 'Read Tag Diagnostic')}
              </span>
            </div>
            <p className="text-xs text-emerald-200/70">
              {t('spoolbuddy.settings.testReadTag', 'Run read_tag.py')}
            </p>
          </button>
        </div>
      </div>

      {/* Diagnostic Modal */}
      {diagnosticOpen && device && (
        <DiagnosticModal
          type={diagnosticOpen}
          deviceId={device.device_id}
          onClose={() => setDiagnosticOpen(null)}
        />
      )}
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

  const isUpdating = device.update_status === 'pending' || device.update_status === 'updating';

  const { data: updateResult, isLoading: checking, refetch } = useQuery({
    queryKey: ['spoolbuddy-update-check', device.device_id],
    queryFn: () => spoolbuddyApi.checkDaemonUpdate(device.device_id, true),
    staleTime: 4 * 60 * 1000,
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

  const displayVersion = device.firmware_version
    || (updateResult?.current_version && updateResult.current_version !== '0.0.0' ? updateResult.current_version : null);

  return (
    <div className="space-y-4">
      {/* Current version */}
      <div className="bg-zinc-800 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-zinc-300 mb-3">
          {t('spoolbuddy.settings.daemonVersion', 'Daemon Version')}
        </h3>
        <div className="flex justify-between items-center text-sm">
          <span className="text-zinc-500">{t('spoolbuddy.settings.currentVersion', 'Current')}</span>
          <span className="text-zinc-200 font-mono">
            {displayVersion || (
              <span className="text-zinc-500 italic">{t('spoolbuddy.settings.versionPending', 'Waiting for daemon...')}</span>
            )}
          </span>
        </div>
      </div>

      {/* Update progress (shown when update is in progress) */}
      {isUpdating && (
        <div className="bg-zinc-800 rounded-lg p-4">
          <div className="flex items-center gap-3">
            <svg className="w-5 h-5 animate-spin text-green-400 flex-shrink-0" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <div>
              <p className="text-sm font-medium text-green-300">
                {t('spoolbuddy.settings.updating', 'Updating...')}
              </p>
              <p className="text-xs text-zinc-400 mt-0.5">
                {device.update_message || t('spoolbuddy.settings.updateWaiting', 'Waiting for device...')}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Update complete */}
      {device.update_status === 'complete' && (
        <div className="rounded-lg p-3 text-sm bg-green-900/30 border border-green-800">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 text-green-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            <p className="text-green-300">{device.update_message || t('spoolbuddy.settings.updateComplete', 'Update complete!')}</p>
          </div>
        </div>
      )}

      {/* Update error */}
      {device.update_status === 'error' && (
        <div className="rounded-lg p-3 text-sm bg-red-900/30 border border-red-800">
          <p className="text-red-300">{device.update_message || t('spoolbuddy.settings.updateFailed', 'Update failed')}</p>
        </div>
      )}

      {/* Check for updates */}
      <div className="bg-zinc-800 rounded-lg p-4 space-y-3">
        <button
          onClick={() => refetch()}
          disabled={checking || isUpdating}
          className="w-full px-4 py-2.5 rounded-lg text-sm font-medium bg-zinc-700 text-zinc-200 hover:bg-zinc-600 disabled:opacity-40 transition-colors min-h-[44px] flex items-center justify-center gap-2"
        >
          {checking && (
            <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          )}
          {checking ? t('spoolbuddy.settings.checking', 'Checking...') : t('spoolbuddy.settings.checkUpdates', 'Check for Updates')}
        </button>

        {/* Error feedback */}
        {error && (
          <div className="rounded-lg p-3 text-sm bg-red-900/30 border border-red-800">
            <p className="text-red-300">{error}</p>
          </div>
        )}

        {/* Result feedback */}
        {updateResult && (
          <div className={`rounded-lg p-3 text-sm ${
            updateResult.update_available
              ? 'bg-green-900/30 border border-green-800'
              : 'bg-zinc-700/50'
          }`}>
            {updateResult.update_available ? (
              <div className="space-y-3">
                <div className="space-y-1">
                  <p className="text-green-300 font-medium">
                    {t('spoolbuddy.settings.updateAvailable', 'Update available')}: v{updateResult.latest_version}
                  </p>
                  <p className="text-xs text-zinc-400">
                    {displayVersion ? `${displayVersion} → ${updateResult.latest_version}` : ''}
                  </p>
                </div>
                <button
                  onClick={applyUpdate}
                  disabled={applying || isUpdating || !device.online}
                  className="w-full px-4 py-2.5 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-40 transition-colors min-h-[44px] flex items-center justify-center gap-2"
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
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <svg className="w-4 h-4 text-green-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                <p className="text-zinc-300">{t('spoolbuddy.settings.upToDate', 'Up to date')}</p>
              </div>
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
