import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  X,
  Folder,
  File,
  ChevronLeft,
  Download,
  Trash2,
  Loader2,
  HardDrive,
  RefreshCw,
  Film,
  FileBox,
  FileText,
  Image,
  Search,
  ArrowUpDown,
  CheckSquare,
  Square,
  MinusSquare,
} from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';

interface FileManagerModalProps {
  printerId: number;
  printerName: string;
  onClose: () => void;
}

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function formatStorageSize(bytes: number): string {
  if (bytes === 0) return '0 GB';
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) {
    return `${gb.toFixed(1)} GB`;
  }
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(0)} MB`;
}

function getFileIcon(filename: string, isDirectory: boolean) {
  if (isDirectory) return Folder;

  const ext = filename.toLowerCase().split('.').pop() || '';
  switch (ext) {
    case '3mf':
      return FileBox;
    case 'gcode':
      return FileText;
    case 'mp4':
    case 'avi':
      return Film;
    case 'png':
    case 'jpg':
    case 'jpeg':
      return Image;
    default:
      return File;
  }
}

type SortOption = 'name-asc' | 'name-desc' | 'size-asc' | 'size-desc' | 'date-asc' | 'date-desc';

const SORT_KEYS: { value: SortOption; key: string }[] = [
  { value: 'name-asc', key: 'nameAsc' },
  { value: 'name-desc', key: 'nameDesc' },
  { value: 'size-asc', key: 'sizeAsc' },
  { value: 'size-desc', key: 'sizeDesc' },
  { value: 'date-asc', key: 'dateAsc' },
  { value: 'date-desc', key: 'dateDesc' },
];

export function FileManagerModal({ printerId, printerName, onClose }: FileManagerModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [currentPath, setCurrentPath] = useState('/');
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');
  const [filesToDelete, setFilesToDelete] = useState<string[]>([]);
  const [sortBy, setSortBy] = useState<SortOption>('name-asc');
  const [downloadProgress, setDownloadProgress] = useState<{ current: number; total: number } | null>(null);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['printerFiles', printerId, currentPath],
    queryFn: () => api.getPrinterFiles(printerId, currentPath),
  });

  const { data: storageData } = useQuery({
    queryKey: ['printerStorage', printerId],
    queryFn: () => api.getPrinterStorage(printerId),
    staleTime: 30000, // Cache for 30 seconds
  });

  const deleteMutation = useMutation({
    mutationFn: async (paths: string[]) => {
      // Delete files one by one
      for (const path of paths) {
        await api.deletePrinterFile(printerId, path);
      }
    },
    onSuccess: () => {
      showToast(t('files.deletedCount', { count: filesToDelete.length }));
      queryClient.invalidateQueries({ queryKey: ['printerFiles', printerId] });
      setSelectedFiles(new Set());
      setFilesToDelete([]);
    },
    onError: (error: Error) => {
      showToast(t('files.deleteFailed', { error: error.message }), 'error');
    },
  });

  const navigateToFolder = (path: string) => {
    setCurrentPath(path);
    setSelectedFiles(new Set());
  };

  const navigateUp = () => {
    if (currentPath === '/') return;
    const parts = currentPath.split('/').filter(Boolean);
    parts.pop();
    setCurrentPath(parts.length ? '/' + parts.join('/') : '/');
    setSelectedFiles(new Set());
  };

  const toggleFileSelection = (path: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedFiles(prev => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const selectAllFiles = () => {
    if (!data?.files) return;
    const filePaths = data.files
      .filter(f => !f.is_directory && (!searchQuery || f.name.toLowerCase().includes(searchQuery.toLowerCase())))
      .map(f => f.path);
    setSelectedFiles(new Set(filePaths));
  };

  const deselectAllFiles = () => {
    setSelectedFiles(new Set());
  };

  const handleDownload = async () => {
    if (selectedFiles.size === 0) return;

    const paths = Array.from(selectedFiles);

    if (paths.length === 1) {
      // Single file - direct download
      window.open(api.getPrinterFileDownloadUrl(printerId, paths[0]), '_blank');
      setSelectedFiles(new Set());
      return;
    }

    // Multiple files - download as ZIP
    setDownloadProgress({ current: 0, total: paths.length });
    try {
      const blob = await api.downloadPrinterFilesAsZip(printerId, paths);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${printerName.replace(/[^a-zA-Z0-9]/g, '_')}-files.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast(t('files.downloadedAsZip', { count: paths.length }));
      setSelectedFiles(new Set());
    } catch (error) {
      showToast(t('files.downloadFailed', { error: error instanceof Error ? error.message : t('common.unknownError') }), 'error');
    } finally {
      setDownloadProgress(null);
    }
  };

  const handleDelete = () => {
    if (selectedFiles.size === 0) return;
    setFilesToDelete(Array.from(selectedFiles));
  };

  // Quick navigation buttons for common directories
  const quickDirs = [
    { path: '/', label: t('files.root') },
    { path: '/cache', label: t('files.cache') },
    { path: '/model', label: t('files.models') },
    { path: '/timelapse', label: t('files.timelapse') },
  ];

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[85vh] flex flex-col bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary flex-shrink-0">
            <div className="flex items-center gap-3">
              <HardDrive className="w-5 h-5 text-bambu-green" />
              <div>
                <h2 className="text-lg font-semibold text-white">{t('settings.fileManager')}</h2>
                <p className="text-sm text-bambu-gray">{printerName}</p>
              </div>
            </div>
            <div className="flex items-center gap-4">
              {/* Storage info */}
              {storageData && (storageData.used_bytes != null || storageData.free_bytes != null) && (
                <div className="text-sm text-bambu-gray flex items-center gap-2">
                  {storageData.used_bytes != null && (
                    <span>{t('files.used')} {formatStorageSize(storageData.used_bytes)}</span>
                  )}
                  {storageData.used_bytes != null && storageData.free_bytes != null && (
                    <span className="text-bambu-dark-tertiary">|</span>
                  )}
                  {storageData.free_bytes != null && (
                    <span>{t('files.free')} {formatStorageSize(storageData.free_bytes)}</span>
                  )}
                </div>
              )}
              <button
                onClick={onClose}
                className="text-bambu-gray hover:text-white transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

        {/* Quick Navigation */}
        <div className="flex items-center gap-2 p-3 border-b border-bambu-dark-tertiary bg-bambu-dark/50 flex-shrink-0">
          {quickDirs.map((dir) => (
            <button
              key={dir.path}
              onClick={() => {
                navigateToFolder(dir.path);
                setSearchQuery('');
              }}
              className={`px-3 py-1 text-sm rounded-full transition-colors ${
                currentPath === dir.path
                  ? 'bg-bambu-green text-white'
                  : 'bg-bambu-dark-tertiary text-bambu-gray hover:text-white'
              }`}
            >
              {dir.label}
            </button>
          ))}
          <div className="flex-1" />
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
            <input
              type="text"
              placeholder={t('files.filterPlaceholder')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-40 pl-8 pr-3 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
            />
          </div>
          <div className="relative flex items-center gap-1">
            <ArrowUpDown className="w-4 h-4 text-bambu-gray" />
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortOption)}
              className="appearance-none bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm py-1.5 pl-2 pr-6 focus:border-bambu-green focus:outline-none cursor-pointer"
            >
              {SORT_KEYS.map((option) => (
                <option key={option.value} value={option.value}>
                  {t(`files.sort.${option.key}`)}
                </option>
              ))}
            </select>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => refetch()}
            disabled={isLoading}
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
          </Button>
        </div>

        {/* Path breadcrumb */}
        <div className="flex items-center gap-2 px-4 py-2 bg-bambu-dark text-sm flex-shrink-0">
            <button
              onClick={navigateUp}
              disabled={currentPath === '/'}
              className="p-1 rounded hover:bg-bambu-dark-tertiary disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-bambu-gray font-mono">{currentPath}</span>
          </div>

        {/* File list */}
        <div className="flex-1 overflow-y-auto p-2 min-h-0">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
              </div>
            ) : !data?.files?.length ? (
              <div className="text-center py-12 text-bambu-gray">
                {t('files.noFiles')}
              </div>
            ) : (
              <div className="space-y-1">
                {/* Filter and sort: directories first, then files with selected sort */}
                {[...data.files]
                  .filter((file) =>
                    !searchQuery || file.name.toLowerCase().includes(searchQuery.toLowerCase())
                  )
                  .sort((a, b) => {
                    // Directories always first
                    if (a.is_directory && !b.is_directory) return -1;
                    if (!a.is_directory && b.is_directory) return 1;

                    // Apply selected sort within same type
                    switch (sortBy) {
                      case 'name-asc':
                        return a.name.localeCompare(b.name);
                      case 'name-desc':
                        return b.name.localeCompare(a.name);
                      case 'size-asc':
                        return a.size - b.size;
                      case 'size-desc':
                        return b.size - a.size;
                      case 'date-asc': {
                        const aTime = a.mtime ? new Date(a.mtime).getTime() : 0;
                        const bTime = b.mtime ? new Date(b.mtime).getTime() : 0;
                        return aTime - bTime;
                      }
                      case 'date-desc': {
                        const aTime = a.mtime ? new Date(a.mtime).getTime() : 0;
                        const bTime = b.mtime ? new Date(b.mtime).getTime() : 0;
                        return bTime - aTime;
                      }
                      default:
                        return a.name.localeCompare(b.name);
                    }
                  })
                  .map((file) => {
                    const FileIcon = getFileIcon(file.name, file.is_directory);
                    const isSelected = selectedFiles.has(file.path);

                    return (
                      <div
                        key={file.path}
                        className={`flex items-center gap-3 p-2 rounded-lg cursor-pointer transition-colors ${
                          isSelected
                            ? 'bg-bambu-green/20 border border-bambu-green/50'
                            : 'hover:bg-bambu-dark-tertiary'
                        }`}
                        onClick={() => {
                          if (file.is_directory) {
                            navigateToFolder(file.path);
                          }
                        }}
                      >
                        {/* Checkbox for files only */}
                        {!file.is_directory ? (
                          <button
                            onClick={(e) => toggleFileSelection(file.path, e)}
                            className="flex-shrink-0 text-bambu-gray hover:text-white"
                          >
                            {isSelected ? (
                              <CheckSquare className="w-5 h-5 text-bambu-green" />
                            ) : (
                              <Square className="w-5 h-5" />
                            )}
                          </button>
                        ) : null}
                        <FileIcon
                          className={`w-5 h-5 flex-shrink-0 ${
                            file.is_directory ? 'text-bambu-green' : 'text-bambu-gray'
                          }`}
                        />
                        <span className="flex-1 text-white truncate">{file.name}</span>
                        {!file.is_directory && (
                          <span className="text-sm text-bambu-gray">
                            {formatFileSize(file.size)}
                          </span>
                        )}
                        {file.is_directory && (
                          <ChevronLeft className="w-4 h-4 text-bambu-gray rotate-180" />
                        )}
                      </div>
                    );
                  })}
              </div>
            )}
          </div>

        {/* Action bar */}
        <div className="flex items-center justify-between p-4 border-t border-bambu-dark-tertiary bg-bambu-dark/50 flex-shrink-0">
          <div className="flex items-center gap-4">
            <div className="text-sm text-bambu-gray">
              {selectedFiles.size > 0
                ? t('files.selectedCount', { count: selectedFiles.size })
                : searchQuery
                  ? t('files.filteredItems', {
                      filtered: data?.files?.filter(f => f.name.toLowerCase().includes(searchQuery.toLowerCase())).length || 0,
                      total: data?.files?.length || 0
                    })
                  : t('files.itemsCount', { count: data?.files?.length || 0 })
              }
            </div>
            {/* Select All / Deselect All */}
            {data?.files?.some(f => !f.is_directory) && (
              <div className="flex items-center gap-2">
                {selectedFiles.size > 0 ? (
                  <button
                    onClick={deselectAllFiles}
                    className="flex items-center gap-1 text-xs text-bambu-gray hover:text-white transition-colors"
                  >
                    <MinusSquare className="w-4 h-4" />
                    {t('files.deselectAll')}
                  </button>
                ) : (
                  <button
                    onClick={selectAllFiles}
                    className="flex items-center gap-1 text-xs text-bambu-gray hover:text-white transition-colors"
                  >
                    <CheckSquare className="w-4 h-4" />
                    {t('files.selectAll')}
                  </button>
                )}
              </div>
            )}
          </div>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              disabled={selectedFiles.size === 0 || downloadProgress !== null}
              onClick={handleDownload}
            >
              {downloadProgress ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {downloadProgress.current}/{downloadProgress.total}
                </>
              ) : (
                <>
                  <Download className="w-4 h-4" />
                  {selectedFiles.size > 1
                    ? t('files.downloadWithCount', { count: selectedFiles.size })
                    : t('common.download')
                  }
                </>
              )}
            </Button>
            <Button
              variant="secondary"
              disabled={selectedFiles.size === 0 || deleteMutation.isPending}
              onClick={handleDelete}
              className="text-red-400 hover:text-red-300"
            >
              {deleteMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Trash2 className="w-4 h-4" />
              )}
              {selectedFiles.size > 1
                ? t('files.deleteWithCount', { count: selectedFiles.size })
                : t('common.delete')
              }
            </Button>
          </div>
        </div>
      </div>

      {/* Delete Confirmation Modal */}
      {filesToDelete.length > 0 && (
        <ConfirmModal
          title={filesToDelete.length > 1 ? t('files.deleteBulkTitle', { count: filesToDelete.length }) : t('files.deleteFile')}
          message={
            filesToDelete.length > 1
              ? t('files.deleteMultipleConfirm', { count: filesToDelete.length })
              : t('files.deleteConfirm', { name: filesToDelete[0].split('/').pop() })
          }
          confirmText={t('common.delete')}
          variant="danger"
          onConfirm={() => {
            deleteMutation.mutate(filesToDelete);
          }}
          onCancel={() => setFilesToDelete([])}
        />
      )}
    </div>
  );
}
