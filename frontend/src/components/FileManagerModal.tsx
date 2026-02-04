import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
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
  Box,
} from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { ModelViewer } from './ModelViewer';
import { GcodeViewer } from './GcodeViewer';
import type { PlateMetadata } from '../types/plates';
import { useToast } from '../contexts/ToastContext';

interface FileManagerModalProps {
  printerId: number;
  printerName: string;
  onClose: () => void;
}

type PrinterViewerTab = '3d' | 'gcode';

interface PrinterFileViewerModalProps {
  printerId: number;
  filePath: string;
  filename: string;
  onClose: () => void;
}

function PrinterFileViewerModal({ printerId, filePath, filename, onClose }: PrinterFileViewerModalProps) {
  const [activeTab, setActiveTab] = useState<PrinterViewerTab | null>(null);
  const [plates, setPlates] = useState<PlateMetadata[]>([]);
  const [platesLoading, setPlatesLoading] = useState(false);
  const [selectedPlateId, setSelectedPlateId] = useState<number | null>(null);

  const ext = filename.toLowerCase().split('.').pop() || '';
  const hasModel = ext === '3mf' || ext === 'stl';
  const hasGcode = ext === 'gcode' || ext === '3mf';

  useEffect(() => {
    setActiveTab(hasModel ? '3d' : hasGcode ? 'gcode' : null);
  }, [hasModel, hasGcode]);

  useEffect(() => {
    setPlates([]);
    setSelectedPlateId(null);

    if (!hasModel) return;

    setPlatesLoading(true);
    api.getPrinterFilePlates(printerId, filePath)
      .then((data) => setPlates(data.plates || []))
      .catch(() => setPlates([]))
      .finally(() => setPlatesLoading(false));
  }, [filePath, hasModel, printerId]);

  const hasMultiplePlates = plates.length > 1;
  const selectedPlate = selectedPlateId == null
    ? null
    : plates.find((plate) => plate.index === selectedPlateId) ?? null;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-6" onClick={onClose}>
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-4xl h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white truncate flex-1 mr-4">{filename}</h2>
          <Button variant="ghost" size="sm" onClick={onClose}>
            <X className="w-5 h-5" />
          </Button>
        </div>

        <div className="flex border-b border-bambu-dark-tertiary">
          <button
            onClick={() => hasModel && setActiveTab('3d')}
            disabled={!hasModel}
            className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors ${
              activeTab === '3d'
                ? 'text-bambu-green border-b-2 border-bambu-green'
                : hasModel
                  ? 'text-bambu-gray hover:text-white'
                  : 'text-bambu-gray/30 cursor-not-allowed'
            }`}
          >
            <Box className="w-4 h-4" />
            3D Model
            {!hasModel && <span className="text-xs">(not available)</span>}
          </button>
          <button
            onClick={() => hasGcode && setActiveTab('gcode')}
            disabled={!hasGcode}
            className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors ${
              activeTab === 'gcode'
                ? 'text-bambu-green border-b-2 border-bambu-green'
                : hasGcode
                  ? 'text-bambu-gray hover:text-white'
                  : 'text-bambu-gray/30 cursor-not-allowed'
            }`}
          >
            <FileText className="w-4 h-4" />
            G-code Preview
            {!hasGcode && <span className="text-xs">(not sliced)</span>}
          </button>
        </div>

        <div className="flex-1 overflow-hidden p-4">
          {activeTab === '3d' && hasModel ? (
            <div className="w-full h-full flex flex-col gap-3">
              {hasMultiplePlates && (
                <div className="rounded-lg border border-bambu-dark-tertiary bg-bambu-dark p-3">
                  <div className="flex items-center gap-2 text-sm text-bambu-gray mb-2">
                    <Box className="w-4 h-4" />
                    Plates
                    {platesLoading && <Loader2 className="w-3 h-3 animate-spin" />}
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                    <button
                      type="button"
                      onClick={() => setSelectedPlateId(null)}
                      className={`flex items-center gap-2 rounded-lg border p-2 text-left transition-colors ${
                        selectedPlateId == null
                          ? 'border-bambu-green bg-bambu-green/10'
                          : 'border-bambu-dark-tertiary bg-bambu-dark-secondary hover:border-bambu-gray'
                      }`}
                    >
                      <div className="w-10 h-10 rounded bg-bambu-dark-tertiary flex items-center justify-center">
                        <Box className="w-5 h-5 text-bambu-gray" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-white font-medium truncate">All Plates</p>
                        <p className="text-xs text-bambu-gray truncate">
                          {plates.length} plate{plates.length !== 1 ? 's' : ''}
                        </p>
                      </div>
                      {selectedPlateId == null && (
                        <CheckSquare className="w-4 h-4 text-bambu-green flex-shrink-0" />
                      )}
                    </button>
                    {plates.map((plate) => (
                      <button
                        key={plate.index}
                        type="button"
                        onClick={() => setSelectedPlateId(plate.index)}
                        className={`flex items-center gap-2 rounded-lg border p-2 text-left transition-colors ${
                          selectedPlateId === plate.index
                            ? 'border-bambu-green bg-bambu-green/10'
                            : 'border-bambu-dark-tertiary bg-bambu-dark-secondary hover:border-bambu-gray'
                        }`}
                      >
                        {plate.has_thumbnail ? (
                          <img
                            src={api.getPrinterFilePlateThumbnail(printerId, plate.index, filePath)}
                            alt={`Plate ${plate.index}`}
                            className="w-10 h-10 rounded object-cover bg-bambu-dark-tertiary"
                          />
                        ) : (
                          <div className="w-10 h-10 rounded bg-bambu-dark-tertiary flex items-center justify-center">
                            <Box className="w-5 h-5 text-bambu-gray" />
                          </div>
                        )}
                        <div className="min-w-0 flex-1">
                          <p className="text-sm text-white font-medium truncate">
                            {plate.name || `Plate ${plate.index}`}
                          </p>
                          <p className="text-xs text-bambu-gray truncate">
                            {plate.objects.length > 0
                              ? plate.objects.slice(0, 2).join(', ') + (plate.objects.length > 2 ? '…' : '')
                              : `${plate.filaments.length} filament${plate.filaments.length !== 1 ? 's' : ''}`}
                          </p>
                        </div>
                        {selectedPlateId === plate.index && (
                          <CheckSquare className="w-4 h-4 text-bambu-green flex-shrink-0" />
                        )}
                      </button>
                    ))}
                  </div>
                  {selectedPlate && (
                    <div className="mt-3 text-xs text-bambu-gray flex flex-wrap gap-x-4 gap-y-1">
                      <span>Plate {selectedPlate.index}</span>
                      {selectedPlate.print_time_seconds != null && (
                        <span>ETA {Math.round(selectedPlate.print_time_seconds / 60)} min</span>
                      )}
                      {selectedPlate.filament_used_grams != null && (
                        <span>{selectedPlate.filament_used_grams.toFixed(1)} g</span>
                      )}
                      {selectedPlate.filaments.length > 0 && (
                        <span>{selectedPlate.filaments.length} filament{selectedPlate.filaments.length !== 1 ? 's' : ''}</span>
                      )}
                    </div>
                  )}
                </div>
              )}
              <div className="flex-1">
                <ModelViewer
                  url={api.getPrinterFileDownloadUrl(printerId, filePath)}
                  fileType={ext}
                  selectedPlateId={selectedPlateId}
                  className="w-full h-full"
                />
              </div>
            </div>
          ) : activeTab === 'gcode' && hasGcode ? (
            <GcodeViewer
              gcodeUrl={api.getPrinterFileGcodeUrl(printerId, filePath)}
              className="w-full h-full"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-bambu-gray">
              No preview available for this file
            </div>
          )}
        </div>
      </div>
    </div>
  );
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

const SORT_OPTIONS: { value: SortOption; label: string }[] = [
  { value: 'name-asc', label: 'Name (A-Z)' },
  { value: 'name-desc', label: 'Name (Z-A)' },
  { value: 'size-asc', label: 'Size (smallest)' },
  { value: 'size-desc', label: 'Size (largest)' },
  { value: 'date-asc', label: 'Date (oldest)' },
  { value: 'date-desc', label: 'Date (newest)' },
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
  const [viewerFile, setViewerFile] = useState<{ path: string; name: string } | null>(null);

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
      showToast(t('printerFiles.toast.filesDeleted', { count: filesToDelete.length }));
      queryClient.invalidateQueries({ queryKey: ['printerFiles', printerId] });
      setSelectedFiles(new Set());
      setFilesToDelete([]);
    },
    onError: (error: Error) => {
      showToast(t('printerFiles.toast.deleteFailed', { error: error.message }), 'error');
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
      // Single file - direct download with auth
      api.downloadPrinterFile(printerId, paths[0]).catch((err) => {
        console.error('Printer file download failed:', err);
      });
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
      showToast(`Downloaded ${paths.length} files as ZIP`);
      setSelectedFiles(new Set());
    } catch (error) {
      showToast(`Download failed: ${error instanceof Error ? error.message : 'Unknown error'}`, 'error');
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
    { path: '/', label: 'Root' },
    { path: '/cache', label: 'Cache' },
    { path: '/model', label: 'Models' },
    { path: '/timelapse', label: 'Timelapse' },
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
                <h2 className="text-lg font-semibold text-white">{t('printerFiles.title')}</h2>
                <p className="text-sm text-bambu-gray">{printerName}</p>
              </div>
            </div>
            <div className="flex items-center gap-4">
              {/* Storage info */}
              {storageData && (storageData.used_bytes != null || storageData.free_bytes != null) && (
                <div className="text-sm text-bambu-gray flex items-center gap-2">
                  {storageData.used_bytes != null && (
                    <span>{t('printerFiles.storageUsed')} {formatStorageSize(storageData.used_bytes)}</span>
                  )}
                  {storageData.used_bytes != null && storageData.free_bytes != null && (
                    <span className="text-bambu-dark-tertiary">|</span>
                  )}
                  {storageData.free_bytes != null && (
                    <span>{t('printerFiles.storageFree')} {formatStorageSize(storageData.free_bytes)}</span>
                  )}
                </div>
              )}
              <button
                onClick={onClose}
                className="text-bambu-gray hover:text-white transition-colors"
                title="Close file manager"
                aria-label="Close file manager"
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
              placeholder={t('printerFiles.filterPlaceholder')}
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
              title="Sort files"
              aria-label="Sort files"
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
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
              title="Go to parent folder"
              aria-label="Go to parent folder"
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
                No files in this directory
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
                          <div className="flex items-center gap-3">
                            <span className="text-sm text-bambu-gray">
                              {formatFileSize(file.size)}
                            </span>
                            {(file.name.toLowerCase().endsWith('.3mf') || file.name.toLowerCase().endsWith('.gcode') || file.name.toLowerCase().endsWith('.stl')) && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setViewerFile({ path: file.path, name: file.name });
                                }}
                                className="p-1 rounded hover:bg-bambu-dark text-bambu-gray hover:text-bambu-green"
                                title="3D View"
                              >
                                <Box className="w-4 h-4" />
                              </button>
                            )}
                          </div>
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
                ? `${selectedFiles.size} selected`
                : searchQuery
                  ? `${data?.files?.filter(f => f.name.toLowerCase().includes(searchQuery.toLowerCase())).length || 0} of ${data?.files?.length || 0} items`
                  : `${data?.files?.length || 0} items`
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
                    Deselect All
                  </button>
                ) : (
                  <button
                    onClick={selectAllFiles}
                    className="flex items-center gap-1 text-xs text-bambu-gray hover:text-white transition-colors"
                  >
                    <CheckSquare className="w-4 h-4" />
                    Select All
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
                  Download{selectedFiles.size > 1 ? ` (${selectedFiles.size})` : ''}
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
              {t('printerFiles.deleteButton')}{selectedFiles.size > 1 ? ` (${selectedFiles.size})` : ''}
            </Button>
          </div>
        </div>
      </div>

      {/* Delete Confirmation Modal */}
      {filesToDelete.length > 0 && (
        <ConfirmModal
          title={filesToDelete.length > 1 ? t('printerFiles.deleteFiles', { count: filesToDelete.length }) : t('fileManager.deleteFile')}
          message={
            filesToDelete.length > 1
              ? t('printerFiles.deleteFilesConfirm', { count: filesToDelete.length })
              : t('printerFiles.deleteFileConfirm', { name: filesToDelete[0].split('/').pop() })
          }
          confirmText={t('common.delete')}
          variant="danger"
          onConfirm={() => {
            deleteMutation.mutate(filesToDelete);
          }}
          onCancel={() => setFilesToDelete([])}
        />
      )}

      {viewerFile && (
        <PrinterFileViewerModal
          printerId={printerId}
          filePath={viewerFile.path}
          filename={viewerFile.name}
          onClose={() => setViewerFile(null)}
        />
      )}
    </div>
  );
}
