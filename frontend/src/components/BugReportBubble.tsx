import { useState, useRef, useCallback, useEffect } from 'react';
import { Bug, X, Loader2, CheckCircle, AlertCircle, Trash2, Upload, Circle, CheckCircle2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { bugReportApi } from '../api/client';

type ViewState = 'form' | 'logging' | 'stopping' | 'submitting' | 'success' | 'error';

const MAX_DIMENSION = 1920;
const JPEG_QUALITY = 0.7;
const MAX_LOG_SECONDS = 300; // 5 minutes

function compressImage(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      let { width, height } = img;
      if (width > MAX_DIMENSION || height > MAX_DIMENSION) {
        const scale = MAX_DIMENSION / Math.max(width, height);
        width = Math.round(width * scale);
        height = Math.round(height * scale);
      }
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (!ctx) { reject(new Error('No canvas context')); return; }
      ctx.drawImage(img, 0, 0, width, height);
      const dataUrl = canvas.toDataURL('image/jpeg', JPEG_QUALITY);
      resolve(dataUrl.replace(/^data:[^;]+;base64,/, ''));
    };
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

export function BugReportBubble() {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [viewState, setViewState] = useState<ViewState>('form');
  const [description, setDescription] = useState('');
  const [email, setEmail] = useState('');
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [issueUrl, setIssueUrl] = useState<string | null>(null);
  const [issueNumber, setIssueNumber] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState('');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [wasDebug, setWasDebug] = useState(false);
  const modalRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const handleStopLoggingRef = useRef<() => void>(() => {});

  // Elapsed timer for logging phase — auto-stop at 5 minutes
  useEffect(() => {
    if (viewState !== 'logging') return;
    if (elapsedSeconds >= MAX_LOG_SECONDS) {
      handleStopLoggingRef.current();
      return;
    }
    const timer = setTimeout(() => setElapsedSeconds((s) => s + 1), 1000);
    return () => clearTimeout(timer);
  }, [viewState, elapsedSeconds]);

  const handleOpen = () => {
    setIsOpen(true);
    setViewState('form');
    setDescription('');
    setEmail('');
    setScreenshot(null);
    setIssueUrl(null);
    setIssueNumber(null);
    setErrorMessage('');
    setElapsedSeconds(0);
    setWasDebug(false);
  };

  const handleClose = () => {
    setIsOpen(false);
  };

  const handleFile = useCallback(async (file: File) => {
    if (!file.type.startsWith('image/')) return;
    try {
      const b64 = await compressImage(file);
      setScreenshot(b64);
    } catch {
      // Ignore read errors
    }
  }, []);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) handleFile(file);
        break;
      }
    }
  }, [handleFile]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const handleStartLogging = async () => {
    if (!description.trim()) return;
    try {
      const result = await bugReportApi.startLogging();
      setWasDebug(result.was_debug);
      setElapsedSeconds(0);
      setViewState('logging');
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : t('bugReport.unexpectedError'));
      setViewState('error');
    }
  };

  const handleStopLogging = async () => {
    setViewState('stopping');
    try {
      const stopResult = await bugReportApi.stopLogging(wasDebug);
      await handleSubmitReport(stopResult.logs);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : t('bugReport.unexpectedError'));
      setViewState('error');
    }
  };
  handleStopLoggingRef.current = handleStopLogging;

  const handleSubmitReport = async (debugLogs: string) => {
    setViewState('submitting');
    try {
      const result = await bugReportApi.submit({
        description: description.trim(),
        email: email.trim() || undefined,
        screenshot_base64: screenshot || undefined,
        include_support_info: true,
        debug_logs: debugLogs || undefined,
      });
      if (result.success) {
        setIssueUrl(result.issue_url || null);
        setIssueNumber(result.issue_number || null);
        setViewState('success');
      } else {
        setErrorMessage(result.message);
        setViewState('error');
      }
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : t('bugReport.unexpectedError'));
      setViewState('error');
    }
  };

  return (
    <>
      {/* Floating bubble */}
      <button
        onClick={handleOpen}
        className="fixed bottom-4 right-4 z-40 w-12 h-12 rounded-full bg-red-500 hover:bg-red-600 text-white shadow-lg hover:shadow-xl transition-all duration-200 hover:scale-110 flex items-center justify-center"
        title={t('bugReport.title')}
      >
        <Bug className="w-5 h-5" />
      </button>

      {/* Slide-in panel anchored to bottom-right */}
      {isOpen && (
        <div
          id="bug-report-modal"
          className="fixed bottom-20 right-4 z-50 w-full max-w-md"
          onPaste={handlePaste}
        >
          <div
            ref={modalRef}
            className="bg-white dark:bg-gray-800 rounded-lg shadow-2xl border border-gray-200 dark:border-gray-700 max-h-[80vh] overflow-y-auto"
          >
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700 sticky top-0 bg-white dark:bg-gray-800 z-10">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                <Bug className="w-5 h-5 text-red-500" />
                {t('bugReport.title')}
              </h2>
              <button
                onClick={handleClose}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-4 space-y-4">
              {viewState === 'form' && (
                <>
                  {/* Description */}
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                      {t('bugReport.description')} *
                    </label>
                    <textarea
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder={t('bugReport.descriptionPlaceholder')}
                      rows={3}
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-vertical"
                    />
                  </div>

                  {/* Email (optional) */}
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                      {t('bugReport.email')}
                    </label>
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder={t('bugReport.emailPlaceholder')}
                      className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                    <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                      {t('bugReport.emailPrivacy')}
                    </p>
                  </div>

                  {/* Screenshot — upload, paste, or drag */}
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                      {t('bugReport.screenshot')}
                    </label>
                    {screenshot ? (
                      <div className="relative">
                        <img
                          src={`data:image/jpeg;base64,${screenshot}`}
                          alt={t('bugReport.screenshot')}
                          className="w-full max-h-40 object-contain rounded-lg border border-gray-200 dark:border-gray-600"
                        />
                        <button
                          onClick={() => setScreenshot(null)}
                          className="absolute top-2 right-2 p-1 bg-red-500 hover:bg-red-600 text-white rounded-full shadow"
                          title={t('common.delete')}
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        onDragOver={handleDragOver}
                        onDragLeave={handleDragLeave}
                        onDrop={handleDrop}
                        className={`w-full flex flex-col items-center gap-2 px-4 py-4 border-2 border-dashed rounded-lg transition-colors cursor-pointer ${
                          isDragging
                            ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-500'
                            : 'border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:border-gray-400 dark:hover:border-gray-500 hover:text-gray-600 dark:hover:text-gray-300'
                        }`}
                      >
                        <Upload className="w-5 h-5" />
                        <span className="text-sm">{t('bugReport.uploadOrPaste')}</span>
                      </button>
                    )}
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept="image/*"
                      className="hidden"
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) handleFile(file);
                        e.target.value = '';
                      }}
                    />
                  </div>

                  {/* Data collection notice */}
                  <details className="text-xs bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
                    <summary className="cursor-pointer font-medium text-amber-700 dark:text-amber-300 hover:text-amber-800 dark:hover:text-amber-200">
                      {t('bugReport.dataCollectedSummary')}
                    </summary>
                    <div className="mt-2 space-y-2 pl-2 border-l-2 border-amber-300 dark:border-amber-700 text-amber-800 dark:text-amber-200">
                      <p className="font-medium">{t('bugReport.dataIncluded')}</p>
                      <p>{t('bugReport.dataIncludedList')}</p>
                      <p className="font-medium">{t('bugReport.dataNeverIncluded')}</p>
                      <p>{t('bugReport.dataNeverIncludedList')}</p>
                    </div>
                  </details>

                  {/* Buttons */}
                  <div className="flex justify-end gap-2 pt-2">
                    <button
                      onClick={handleClose}
                      className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg transition-colors"
                    >
                      {t('common.cancel')}
                    </button>
                    <button
                      onClick={handleStartLogging}
                      disabled={!description.trim()}
                      className="px-4 py-2 text-sm font-medium text-white bg-red-500 hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
                    >
                      {t('bugReport.startLogging')}
                    </button>
                  </div>
                </>
              )}

              {viewState === 'logging' && (
                <div className="py-6 space-y-6">
                  {/* 3-step progress indicator */}
                  <div className="space-y-3 px-2">
                    {/* Step 1: Completed */}
                    <div className="flex items-center gap-3">
                      <CheckCircle2 className="w-5 h-5 text-green-500 flex-shrink-0" />
                      <span className="text-sm text-green-700 dark:text-green-400">{t('bugReport.stepEnableLogging')}</span>
                    </div>
                    {/* Step 2: Active */}
                    <div className="flex items-center gap-3">
                      <span className="relative flex h-5 w-5 flex-shrink-0 items-center justify-center">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-3 w-3 bg-blue-500"></span>
                      </span>
                      <span className="text-sm font-medium text-blue-700 dark:text-blue-300">{t('bugReport.stepReproduce')}</span>
                    </div>
                    {/* Step 3: Upcoming */}
                    <div className="flex items-center gap-3">
                      <Circle className="w-5 h-5 text-gray-300 dark:text-gray-600 flex-shrink-0" />
                      <span className="text-sm text-gray-400 dark:text-gray-500">{t('bugReport.stepStopLogging')}</span>
                    </div>
                  </div>

                  {/* Elapsed timer */}
                  <div className="text-center">
                    <p className="text-3xl font-mono text-blue-500">{formatElapsed(elapsedSeconds)}</p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">{t('bugReport.maxDuration', { minutes: 5 })}</p>
                  </div>

                  {/* Stop & Submit button */}
                  <div className="flex justify-center">
                    <button
                      onClick={handleStopLogging}
                      className="px-6 py-2.5 text-sm font-medium text-white bg-red-500 hover:bg-red-600 rounded-lg transition-colors"
                    >
                      {t('bugReport.stopAndSubmit')}
                    </button>
                  </div>
                </div>
              )}

              {(viewState === 'stopping' || viewState === 'submitting') && (
                <div className="flex flex-col items-center justify-center py-8 gap-3">
                  <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
                  <p className="text-sm text-gray-600 dark:text-gray-400">
                    {viewState === 'stopping' ? t('bugReport.stoppingLogs') : t('bugReport.submitting')}
                  </p>
                </div>
              )}

              {viewState === 'success' && (
                <div className="flex flex-col items-center justify-center py-8 gap-3">
                  <CheckCircle className="w-12 h-12 text-green-500" />
                  <p className="text-lg font-semibold text-gray-900 dark:text-white">{t('bugReport.thankYou')}</p>
                  <p className="text-sm text-gray-600 dark:text-gray-400">{t('bugReport.submitted')}</p>
                  {issueUrl && (
                    <a
                      href={issueUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm text-blue-500 hover:text-blue-600 underline"
                    >
                      {t('bugReport.viewIssue')} #{issueNumber}
                    </a>
                  )}
                  <button
                    onClick={handleClose}
                    className="mt-4 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg transition-colors"
                  >
                    {t('common.close')}
                  </button>
                </div>
              )}

              {viewState === 'error' && (
                <div className="flex flex-col items-center justify-center py-8 gap-3">
                  <AlertCircle className="w-12 h-12 text-red-500" />
                  <p className="text-lg font-semibold text-gray-900 dark:text-white">{t('bugReport.submitFailed')}</p>
                  <p className="text-sm text-gray-600 dark:text-gray-400 text-center">{errorMessage}</p>
                  <div className="flex gap-2 mt-4">
                    <button
                      onClick={() => setViewState('form')}
                      className="px-4 py-2 text-sm font-medium text-white bg-red-500 hover:bg-red-600 rounded-lg transition-colors"
                    >
                      {t('bugReport.submit')}
                    </button>
                    <button
                      onClick={handleClose}
                      className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg transition-colors"
                    >
                      {t('common.close')}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
