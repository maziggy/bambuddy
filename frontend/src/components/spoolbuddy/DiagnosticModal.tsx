import { useState, useEffect, useCallback } from 'react';
import { X, Play, RotateCw } from 'lucide-react';
import { spoolbuddyApi } from '../../api/client';
import { useTranslation } from 'react-i18next';

interface DiagnosticModalProps {
  type: 'scale' | 'nfc' | 'read_tag';
  deviceId: string;
  onClose: () => void;
}

export function DiagnosticModal({ type, deviceId, onClose }: DiagnosticModalProps) {
  const { t } = useTranslation();
  const [isRunning, setIsRunning] = useState(false);
  const [output, setOutput] = useState<string>('');
  const [error, setError] = useState<string>('');
  const [hasRun, setHasRun] = useState(false);

  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isRunning) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isRunning, onClose]);

  const runDiagnostic = useCallback(async () => {
    setIsRunning(true);
    setOutput('');
    setError('');
    setHasRun(true);

    try {
      // Step 1: Queue the diagnostic on the device
      setOutput(t('spoolbuddy.diagnostic.queuing', 'Queuing diagnostic on device...\n'));
      await spoolbuddyApi.queueDiagnostics(deviceId, type);

      // Step 2: Poll for results with timeout
      let result = null;
      const maxRetries = 60; // 30s timeout with 500ms polling
      let retryCount = 0;

      while (retryCount < maxRetries && !result) {
        // Wait a bit before polling
        await new Promise(resolve => setTimeout(resolve, 500));

        try {
          result = await spoolbuddyApi.getDiagnosticResult(deviceId, type);
          break;
        } catch (e) {
          // Not ready yet, continue polling
          retryCount++;
          if (retryCount % 4 === 0) {
            // Update every 2 seconds (after 4 retries of 500ms)
            setOutput(prev => prev + '.');
          }
        }
      }

      if (!result) {
        throw new Error('Diagnostic timed out - device did not report results');
      }

      setOutput(result.output);
      if (!result.success) {
        setError(`Exit code: ${result.exit_code}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error');
      setOutput('');
    } finally {
      setIsRunning(false);
    }
  }, [type, deviceId]);

  const title = type === 'scale'
    ? t('spoolbuddy.diagnostic.scaleTitle', 'Scale Diagnostic')
    : type === 'read_tag'
      ? t('spoolbuddy.diagnostic.readTagTitle', 'Read Tag Diagnostic')
      : t('spoolbuddy.diagnostic.nfcTitle', 'NFC Reader Diagnostic');

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 animate-fade-in"
      onClick={onClose}
    >
      <div
        className="bg-zinc-800 rounded-lg shadow-xl w-full max-w-2xl mx-4 max-h-[80vh] flex flex-col animate-slide-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex justify-between items-center p-4 border-b border-zinc-700">
          <h2 className="text-lg font-semibold text-white">{title}</h2>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-white transition-colors"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-auto p-4 bg-black/50 font-mono text-sm">
          {isRunning ? (
            <div className="flex items-center gap-2 text-green-400">
              <div className="animate-spin w-4 h-4 border-2 border-green-400 border-t-transparent rounded-full" />
              <span>{t('spoolbuddy.diagnostic.running', 'Running diagnostic on device...')}</span>
            </div>
          ) : output ? (
            <>
              <div className="text-green-400 whitespace-pre-wrap break-words">
                {output}
              </div>
              {error && (
                <div className="text-red-400 mt-2">
                  ❌ {error}
                </div>
              )}
            </>
          ) : hasRun ? (
            <div>
              {error ? (
                <div className="text-red-400">ERROR: {error}</div>
              ) : (
                <span className="text-green-400">{t('spoolbuddy.diagnostic.completed', 'Diagnostic completed successfully.')}</span>
              )}
            </div>
                ) : (
            <div className="text-zinc-500">
              {t('spoolbuddy.diagnostic.clickStart', 'Click "Run Diagnostic" to start the hardware diagnostic on')} {deviceId}.
            </div>
          )}
        </div>
        
        {/* Footer */}
        <div className="flex gap-2 p-4 border-t border-zinc-700 bg-zinc-800">
          <button
            onClick={runDiagnostic}
            disabled={isRunning}
            className="flex-1 flex items-center justify-center gap-2 bg-green-600 hover:bg-green-700 disabled:bg-gray-600 disabled:cursor-not-allowed px-4 py-2 rounded font-semibold text-white transition-colors"
          >
            {isRunning ? (
              <>
                <div className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
                {t('spoolbuddy.diagnostic.runningBtn', 'Running...')}
              </>
            ) : hasRun ? (
              <>
                <RotateCw size={16} />
                {t('spoolbuddy.diagnostic.runAgain', 'Run Again')}
              </>
            ) : (
              <>
                <Play size={16} />
                {t('spoolbuddy.diagnostic.runBtn', 'Run Diagnostic')}
              </>
            )}
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 rounded bg-zinc-700 hover:bg-zinc-600 text-white font-semibold transition-colors"
          >
            {t('spoolbuddy.diagnostic.close', 'Close')}
          </button>
        </div>
      </div>
    </div>
  );
}
