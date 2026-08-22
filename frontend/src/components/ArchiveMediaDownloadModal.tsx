import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CheckSquare, Download, Film, Loader2, Square, Video, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { formatFileSize } from '../utils/file';
import { Button } from './Button';

interface ArchiveMediaDownloadModalProps {
  archiveId: number;
  archiveName: string;
  printerName: string;
  onClose: () => void;
}

export function ArchiveMediaDownloadModal({
  archiveId,
  archiveName,
  printerName,
  onClose,
}: ArchiveMediaDownloadModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [downloadStarting, setDownloadStarting] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState<{ current: number; total: number } | null>(null);
  const initializedSelectionArchiveRef = useRef<number | null>(null);
  const downloadAbortRef = useRef<AbortController | null>(null);
  const mediaQuery = useQuery({
    queryKey: ['archive-printer-media', archiveId],
    queryFn: () => api.getArchivePrinterMedia(archiveId),
    staleTime: 60_000,
  });

  const remoteFiles = useMemo(() => mediaQuery.data?.remote_files ?? [], [mediaQuery.data]);

  useEffect(() => {
    if (!mediaQuery.data || initializedSelectionArchiveRef.current === archiveId) return;
    initializedSelectionArchiveRef.current = archiveId;
    if (remoteFiles.length === 1) {
      setSelectedPaths(new Set([remoteFiles[0].path]));
    }
  }, [archiveId, mediaQuery.data, remoteFiles]);

  useEffect(() => {
    const availablePaths = new Set(remoteFiles.map(file => file.path));
    setSelectedPaths(current => new Set([...current].filter(path => availablePaths.has(path))));
  }, [remoteFiles]);

  useEffect(() => () => downloadAbortRef.current?.abort(), []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const togglePath = (path: string) => {
    setSelectedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const downloadLocalTimelapse = async () => {
    const sourceName = mediaQuery.data?.local_timelapse?.name ?? '';
    const extension = sourceName.includes('.') ? `.${sourceName.split('.').pop()}` : '';
    try {
      await api.downloadArchiveTimelapse(archiveId, `${archiveName}_timelapse${extension}`);
    } catch (error) {
      showToast(t('printerFiles.downloadFailed', {
        error: error instanceof Error ? error.message : t('printerFiles.unknownError'),
      }), 'error');
    }
  };

  const downloadSelected = async () => {
    if (!mediaQuery.data?.printer_id || selectedPaths.size === 0) return;
    const selectedFiles = remoteFiles.filter(file => selectedPaths.has(file.path));
    if (selectedFiles.length === 0) return;
    const controller = new AbortController();
    downloadAbortRef.current = controller;
    setDownloadStarting(true);
    try {
      const result = await api.downloadPrinterFilesAsZip(
        mediaQuery.data.printer_id,
        selectedFiles.map(file => file.path),
        Object.fromEntries(selectedFiles.map(file => [file.path, file.size])),
        `${archiveName.replace(/[^a-zA-Z0-9]/g, '_')}-printer-videos.zip`,
        true,
        controller.signal,
        (completed, total) => setDownloadProgress({ current: completed, total }),
      );
      if (result.failed > 0) {
        showToast(t('printerFiles.zipPartial', {
          successful: result.successful,
          total: result.requested,
        }), 'warning');
      } else {
        showToast(t('printerFiles.zipStarted', { count: result.successful }));
      }
    } catch (error) {
      showToast(t('printerFiles.downloadFailed', {
        error: error instanceof Error ? error.message : t('printerFiles.unknownError'),
      }), 'error');
    } finally {
      if (downloadAbortRef.current === controller) downloadAbortRef.current = null;
      setDownloadProgress(null);
      setDownloadStarting(false);
    }
  };

  const hasMedia = !!mediaQuery.data?.local_timelapse || remoteFiles.length > 0;

  const warningText = (warning: string) => {
    if (warning === 'printer_files_forbidden') return t('printers.permission.noFiles');
    if (warning === 'printer_missing') return t('archives.media.printerMissing');
    if (warning === 'timelapse_unavailable') return t('archives.media.timelapseUnavailable');
    return t('archives.media.ipcamUnavailable');
  };

  const warnings = (mediaQuery.data?.warnings ?? []).map((warning) => (
    <p key={warning} className="text-xs text-amber-600 dark:text-amber-400">
      {warningText(warning)}
    </p>
  ));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-bambu-dark-tertiary bg-bambu-dark-secondary"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-bambu-dark-tertiary p-4">
          <div className="min-w-0">
            <h3 className="flex items-center gap-2 text-lg font-semibold text-white">
              <Video className="h-5 w-5 text-bambu-green" />
              {t('archives.media.title')}
            </h3>
            <p className="truncate text-sm text-bambu-gray">{archiveName} · {printerName}</p>
          </div>
          <button onClick={onClose} className="rounded p-1 text-bambu-gray hover:bg-bambu-dark-tertiary hover:text-white">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {mediaQuery.isLoading ? (
            <div className="flex items-center justify-center gap-2 py-12 text-bambu-gray">
              <Loader2 className="h-5 w-5 animate-spin" />
              {t('archives.media.searching')}
            </div>
          ) : mediaQuery.isError ? (
            <p className="py-8 text-center text-red-500">
              {t('archives.media.searchFailed')}
            </p>
          ) : !hasMedia ? (
            <div className="space-y-3 py-8 text-center">
              <p className="text-bambu-gray">{t('archives.media.none')}</p>
              {warnings}
            </div>
          ) : (
            <div className="space-y-4">
              {mediaQuery.data?.local_timelapse && (
                <div className="flex items-center gap-3 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark p-3">
                  <Film className="h-5 w-5 shrink-0 text-bambu-green" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-white">
                      {mediaQuery.data.local_timelapse.name}
                    </p>
                    <p className="text-xs text-bambu-gray">
                      {t('archives.media.attachedTimelapse')} · {formatFileSize(mediaQuery.data.local_timelapse.size)}
                    </p>
                  </div>
                  <Button variant="secondary" size="sm" onClick={downloadLocalTimelapse}>
                    <Download className="h-4 w-4" />
                    {t('common.download')}
                  </Button>
                </div>
              )}

              {remoteFiles.length > 0 && (
                <div>
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <p className="text-sm text-bambu-gray">
                      {t('archives.media.printerFiles')} ({remoteFiles.length})
                    </p>
                    <button
                      className="text-xs text-bambu-green hover:text-bambu-green-light"
                      onClick={() => setSelectedPaths(
                        selectedPaths.size === remoteFiles.length
                          ? new Set()
                          : new Set(remoteFiles.map((file) => file.path)),
                      )}
                    >
                      {selectedPaths.size === remoteFiles.length
                        ? t('common.deselectAll')
                        : t('common.selectAll')}
                    </button>
                  </div>
                  <div className="space-y-1">
                    {remoteFiles.map((file) => {
                      const selected = selectedPaths.has(file.path);
                      return (
                        <button
                          key={file.path}
                          className={`flex w-full items-center gap-3 rounded-lg border p-3 text-left transition-colors ${
                            selected
                              ? 'border-bambu-green/60 bg-bambu-green/10'
                              : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray'
                          }`}
                          onClick={() => togglePath(file.path)}
                        >
                          {selected
                            ? <CheckSquare className="h-5 w-5 shrink-0 text-bambu-green" />
                            : <Square className="h-5 w-5 shrink-0 text-bambu-gray" />}
                          {file.kind === 'timelapse'
                            ? <Film className="h-5 w-5 shrink-0 text-bambu-green" />
                            : <Video className="h-5 w-5 shrink-0 text-blue-400" />}
                          <span className="min-w-0 flex-1 truncate text-sm text-white">{file.name}</span>
                          <span className="shrink-0 text-xs text-bambu-gray">{formatFileSize(file.size)}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {warnings}
            </div>
          )}
        </div>

        {remoteFiles.length > 0 && (
          <div className="flex items-center justify-end border-t border-bambu-dark-tertiary p-4">
            <Button
              variant="primary"
              onClick={downloadSelected}
              disabled={selectedPaths.size === 0 || downloadStarting}
            >
              {downloadStarting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              {downloadProgress
                ? `${downloadProgress.current}/${downloadProgress.total}`
                : `${t('archives.media.downloadSelected')} (${selectedPaths.size})`}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
