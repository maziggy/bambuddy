import { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Upload, X, AlertTriangle, CheckCircle, SkipForward, RefreshCw, Loader2, ChevronDown, ChevronUp } from 'lucide-react';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { Toggle } from './Toggle';

interface RestoreResult {
  success: boolean;
  message: string;
  restored?: Record<string, number>;
  skipped?: Record<string, number>;
  skipped_details?: Record<string, string[]>;
  files_restored?: number;
  total_skipped?: number;
  new_api_keys?: Array<{ name: string; key: string; key_prefix: string }>;
}

interface RestoreModalProps {
  onClose: () => void;
  onRestore: (file: File, overwrite: boolean) => Promise<RestoreResult>;
  onSuccess: () => void;
}

type ModalState = 'options' | 'restoring' | 'result';

export function RestoreModal({ onClose, onRestore, onSuccess }: RestoreModalProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<ModalState>('options');
  const [overwrite, setOverwrite] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [result, setResult] = useState<RestoreResult | null>(null);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && state !== 'restoring') {
        // Use handleClose for result state to trigger onSuccess
        if (state === 'result' && result?.success) {
          onSuccess();
        }
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, onSuccess, state, result]);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setSelectedFile(file);
    }
  };

  const handleRestore = async () => {
    if (!selectedFile) return;

    setState('restoring');
    try {
      const restoreResult = await onRestore(selectedFile, overwrite);
      setResult(restoreResult);
      setState('result');
      // Don't call onSuccess here - wait until modal closes
      // This prevents race condition with query cache
    } catch {
      setResult({
        success: false,
        message: t('restore.failedToRestore'),
      });
      setState('result');
    }
  };

  const handleClose = () => {
    // If restore was successful, trigger refresh before closing
    if (result?.success) {
      onSuccess();
    }
    onClose();
  };

  const toggleCategory = (category: string) => {
    setExpandedCategories(prev => {
      const next = new Set(prev);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  };

  const totalRestored = result?.restored
    ? Object.values(result.restored).reduce((a, b) => a + b, 0) + (result.files_restored || 0)
    : 0;
  const totalSkipped = result?.total_skipped || 0;

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onMouseDown={(e) => {
        // Only close if clicking directly on the backdrop, not on children
        if (e.target === e.currentTarget && state !== 'restoring') {
          onClose();
        }
      }}
    >
      <Card className="w-full max-w-lg">
        <CardContent className="p-0">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-full ${
                state === 'result' && result?.success
                  ? 'bg-bambu-green/20 text-bambu-green'
                  : state === 'result' && !result?.success
                  ? 'bg-red-500/20 text-red-500'
                  : 'bg-blue-500/20 text-blue-500'
              }`}>
                {state === 'result' && result?.success ? (
                  <CheckCircle className="w-5 h-5" />
                ) : state === 'result' && !result?.success ? (
                  <AlertTriangle className="w-5 h-5" />
                ) : (
                  <Upload className="w-5 h-5" />
                )}
              </div>
              <div>
                <h3 className="text-lg font-semibold text-white">
                  {state === 'options' && t('restore.title')}
                  {state === 'restoring' && t('restore.restoring')}
                  {state === 'result' && (result?.success ? t('restore.complete') : t('restore.failed'))}
                </h3>
                <p className="text-sm text-bambu-gray">
                  {state === 'options' && t('restore.importDescription')}
                  {state === 'restoring' && t('restore.pleaseWait')}
                  {state === 'result' && result?.message}
                </p>
              </div>
            </div>
            {state !== 'restoring' && (
              <button
                onClick={handleClose}
                className="p-2 hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            )}
          </div>

          {/* Options State */}
          {state === 'options' && (
            <>
              <div className="p-4 space-y-4">
                {/* File Selection */}
                <div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".json,.zip"
                    className="hidden"
                    onChange={handleFileSelect}
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className={`w-full p-4 border-2 border-dashed rounded-lg transition-colors ${
                      selectedFile
                        ? 'border-bambu-green bg-bambu-green/10'
                        : 'border-bambu-dark-tertiary hover:border-bambu-gray'
                    }`}
                  >
                    {selectedFile ? (
                      <div className="flex items-center justify-center gap-2 text-bambu-green">
                        <CheckCircle className="w-5 h-5" />
                        <span className="font-medium">{selectedFile.name}</span>
                      </div>
                    ) : (
                      <div className="flex flex-col items-center gap-2 text-bambu-gray">
                        <Upload className="w-8 h-8" />
                        <span>{t('restore.clickToSelect')}</span>
                      </div>
                    )}
                  </button>
                </div>

                {/* Info Box */}
                <div className="p-3 rounded-lg bg-blue-500/10 border border-blue-500/30">
                  <div className="flex items-start gap-2 text-sm">
                    <AlertTriangle className="w-4 h-4 text-blue-500 dark:text-blue-400 mt-0.5 flex-shrink-0" />
                    <div className="text-blue-700 dark:text-blue-200">
                      <p className="font-medium mb-1">{t('restore.duplicateHandling')}</p>
                      <ul className="text-blue-600 dark:text-blue-200/80 space-y-1 text-xs">
                        <li><strong>{t('restore.dupPrinters')}</strong> - {t('restore.dupPrintersDesc')}</li>
                        <li><strong>{t('restore.dupSmartPlugs')}</strong> - {t('restore.dupSmartPlugsDesc')}</li>
                        <li><strong>{t('restore.dupNotificationProviders')}</strong> - {t('restore.dupNotificationProvidersDesc')}</li>
                        <li><strong>{t('restore.dupFilaments')}</strong> - {t('restore.dupFilamentsDesc')}</li>
                        <li><strong>{t('restore.dupArchives')}</strong> - {t('restore.dupArchivesDesc')}</li>
                        <li><strong>{t('restore.dupPendingUploads')}</strong> - {t('restore.dupPendingUploadsDesc')}</li>
                        <li><strong>{t('restore.dupSettingsTemplates')}</strong> - {t('restore.dupSettingsTemplatesDesc')}</li>
                      </ul>
                    </div>
                  </div>
                </div>

                {/* Overwrite Toggle */}
                <div className="p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-white font-medium flex items-center gap-2">
                        {overwrite ? (
                          <RefreshCw className="w-4 h-4 text-orange-400" />
                        ) : (
                          <SkipForward className="w-4 h-4 text-bambu-gray" />
                        )}
                        {overwrite ? t('restore.replaceExisting') : t('restore.keepExisting')}
                      </p>
                      <p className="text-sm text-bambu-gray mt-1">
                        {overwrite
                          ? t('restore.overwriteDescription')
                          : t('restore.skipDescription')}
                      </p>
                    </div>
                    <Toggle checked={overwrite} onChange={setOverwrite} />
                  </div>
                </div>

                {overwrite && (
                  <div className="p-3 rounded-lg bg-orange-500/10 border border-orange-500/30">
                    <div className="flex items-start gap-2 text-sm">
                      <AlertTriangle className="w-4 h-4 text-orange-500 dark:text-orange-400 mt-0.5 flex-shrink-0" />
                      <div className="text-orange-700 dark:text-orange-200">
                        <span className="font-medium">{t('restore.caution')}</span> {t('restore.overwriteWarning')}
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* Footer */}
              <div className="flex items-center justify-end gap-3 p-4 border-t border-bambu-dark-tertiary">
                <Button type="button" variant="secondary" onClick={onClose}>
                  {t('common.cancel')}
                </Button>
                <Button
                  type="button"
                  onClick={handleRestore}
                  disabled={!selectedFile}
                  className="bg-bambu-green hover:bg-bambu-green-dark disabled:opacity-50"
                >
                  <Upload className="w-4 h-4 mr-2" />
                  {t('restore.restore')}
                </Button>
              </div>
            </>
          )}

          {/* Restoring State */}
          {state === 'restoring' && (
            <div className="p-8 flex flex-col items-center gap-4">
              <Loader2 className="w-12 h-12 text-bambu-green animate-spin" />
              <p className="text-bambu-gray">{t('restore.processing')}</p>
            </div>
          )}

          {/* Result State */}
          {state === 'result' && result && (
            <>
              <div className="p-4 space-y-4 max-h-[400px] overflow-y-auto">
                {/* Summary */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="p-3 rounded-lg bg-bambu-green/10 border border-bambu-green/30">
                    <div className="text-2xl font-bold text-bambu-green">{totalRestored}</div>
                    <div className="text-sm text-bambu-gray">{t('restore.itemsRestored')}</div>
                  </div>
                  <div className="p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/30">
                    <div className="text-2xl font-bold text-yellow-500">{totalSkipped}</div>
                    <div className="text-sm text-bambu-gray">{t('restore.itemsSkipped')}</div>
                  </div>
                </div>

                {/* Restored Details */}
                {result.restored && Object.entries(result.restored).some(([, count]) => count > 0) && (
                  <div className="space-y-2">
                    <h4 className="text-sm font-medium text-bambu-gray flex items-center gap-2">
                      <CheckCircle className="w-4 h-4 text-bambu-green" />
                      {t('restore.restoredSection')}
                    </h4>
                    <div className="space-y-1">
                      {Object.entries(result.restored)
                        .filter(([, count]) => count > 0)
                        .map(([key, count]) => (
                          <div key={key} className="flex items-center justify-between text-sm p-2 rounded bg-bambu-dark">
                            <span className="text-white">{t(`backup.categories.${key}`, { defaultValue: key })}</span>
                            <span className="text-bambu-green font-medium">{count}</span>
                          </div>
                        ))}
                      {(result.files_restored || 0) > 0 && (
                        <div className="flex items-center justify-between text-sm p-2 rounded bg-bambu-dark">
                          <span className="text-white">{t('restore.filesSection')}</span>
                          <span className="text-bambu-green font-medium">{result.files_restored}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Skipped Details */}
                {result.skipped && Object.entries(result.skipped).some(([, count]) => count > 0) && (
                  <div className="space-y-2">
                    <h4 className="text-sm font-medium text-bambu-gray flex items-center gap-2">
                      <SkipForward className="w-4 h-4 text-yellow-500" />
                      {t('restore.skippedSection')}
                    </h4>
                    <div className="space-y-1">
                      {Object.entries(result.skipped)
                        .filter(([, count]) => count > 0)
                        .map(([key, count]) => {
                          const details = result.skipped_details?.[key] || [];
                          const isExpanded = expandedCategories.has(key);
                          return (
                            <div key={key}>
                              <button
                                onClick={() => details.length > 0 && toggleCategory(key)}
                                className={`w-full flex items-center justify-between text-sm p-2 rounded bg-bambu-dark ${
                                  details.length > 0 ? 'hover:bg-bambu-dark-tertiary cursor-pointer' : ''
                                }`}
                              >
                                <span className="text-white flex items-center gap-2">
                                  {t(`backup.categories.${key}`, { defaultValue: key })}
                                  {details.length > 0 && (
                                    isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />
                                  )}
                                </span>
                                <span className="text-yellow-500 font-medium">{count}</span>
                              </button>
                              {isExpanded && details.length > 0 && (
                                <div className="mt-1 ml-4 p-2 rounded bg-bambu-dark-tertiary text-xs text-bambu-gray space-y-1">
                                  {details.slice(0, 10).map((item, i) => (
                                    <div key={i}>{item}</div>
                                  ))}
                                  {details.length > 10 && (
                                    <div className="text-bambu-gray/60">{t('restore.andMore', { count: details.length - 10 })}</div>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}
                    </div>
                  </div>
                )}

                {/* Newly Generated API Keys */}
                {result.new_api_keys && result.new_api_keys.length > 0 && (
                  <div className="space-y-2">
                    <h4 className="text-sm font-medium text-bambu-gray flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4 text-orange-500" />
                      {t('restore.newApiKeys')}
                    </h4>
                    <div className="p-3 rounded bg-orange-500/10 border border-orange-500/30">
                      <p className="text-xs text-orange-200 mb-2">
                        {t('restore.copyKeysWarning')}
                      </p>
                      <div className="space-y-2">
                        {result.new_api_keys.map((apiKey: { name: string; key: string; key_prefix: string }, i: number) => (
                          <div key={i} className="p-2 rounded bg-bambu-dark">
                            <div className="text-sm text-white font-medium mb-1">{apiKey.name}</div>
                            <div className="flex items-center gap-2">
                              <code className="text-xs text-bambu-green bg-bambu-dark-tertiary px-2 py-1 rounded font-mono flex-1 break-all">
                                {apiKey.key}
                              </code>
                              <button
                                onClick={() => navigator.clipboard.writeText(apiKey.key)}
                                className="text-xs text-bambu-gray hover:text-white px-2 py-1 rounded bg-bambu-dark-tertiary"
                              >
                                {t('restore.copy')}
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}

                {totalRestored === 0 && totalSkipped === 0 && (
                  <div className="p-4 text-center text-bambu-gray">
                    {t('restore.noDataFound')}
                  </div>
                )}
              </div>

              {/* Footer */}
              <div className="flex items-center justify-end gap-3 p-4 border-t border-bambu-dark-tertiary">
                <Button onClick={handleClose}>
                  {t('common.close')}
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
