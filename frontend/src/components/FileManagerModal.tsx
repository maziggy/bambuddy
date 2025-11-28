import { useState, useEffect } from 'react';
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

export function FileManagerModal({ printerId, printerName, onClose }: FileManagerModalProps) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [currentPath, setCurrentPath] = useState('/');
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [fileToDelete, setFileToDelete] = useState<string | null>(null);

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
    mutationFn: (path: string) => api.deletePrinterFile(printerId, path),
    onSuccess: (_, path) => {
      showToast(`Deleted: ${path.split('/').pop()}`);
      queryClient.invalidateQueries({ queryKey: ['printerFiles', printerId] });
      setSelectedFile(null);
    },
    onError: (error: Error) => {
      showToast(`Delete failed: ${error.message}`, 'error');
    },
  });

  const navigateToFolder = (path: string) => {
    setCurrentPath(path);
    setSelectedFile(null);
  };

  const navigateUp = () => {
    if (currentPath === '/') return;
    const parts = currentPath.split('/').filter(Boolean);
    parts.pop();
    setCurrentPath(parts.length ? '/' + parts.join('/') : '/');
    setSelectedFile(null);
  };

  const handleDownload = (path: string) => {
    window.open(api.getPrinterFileDownloadUrl(printerId, path), '_blank');
  };

  const handleDelete = (path: string) => {
    setFileToDelete(path);
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
                <h2 className="text-lg font-semibold text-white">File Manager</h2>
                <p className="text-sm text-bambu-gray">{printerName}</p>
              </div>
            </div>
            <div className="flex items-center gap-4">
              {/* Storage info */}
              {storageData?.used_bytes != null && storageData.used_bytes > 0 && (
                <div className="text-sm text-bambu-gray">
                  Used: {formatStorageSize(storageData.used_bytes)}
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
              placeholder="Filter files..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-40 pl-8 pr-3 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
            />
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
                No files in this directory
              </div>
            ) : (
              <div className="space-y-1">
                {/* Filter and sort: directories first, then files */}
                {[...data.files]
                  .filter((file) =>
                    !searchQuery || file.name.toLowerCase().includes(searchQuery.toLowerCase())
                  )
                  .sort((a, b) => {
                    if (a.is_directory && !b.is_directory) return -1;
                    if (!a.is_directory && b.is_directory) return 1;
                    return a.name.localeCompare(b.name);
                  })
                  .map((file) => {
                    const FileIcon = getFileIcon(file.name, file.is_directory);
                    const isSelected = selectedFile === file.path;

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
                          } else {
                            setSelectedFile(isSelected ? null : file.path);
                          }
                        }}
                      >
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
          <div className="text-sm text-bambu-gray">
            {searchQuery
              ? `${data?.files?.filter(f => f.name.toLowerCase().includes(searchQuery.toLowerCase())).length || 0} of ${data?.files?.length || 0} items`
              : `${data?.files?.length || 0} items`
            }
          </div>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              disabled={!selectedFile}
              onClick={() => selectedFile && handleDownload(selectedFile)}
            >
              <Download className="w-4 h-4" />
              Download
            </Button>
            <Button
              variant="secondary"
              disabled={!selectedFile || deleteMutation.isPending}
              onClick={() => selectedFile && handleDelete(selectedFile)}
              className="text-red-400 hover:text-red-300"
            >
              {deleteMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Trash2 className="w-4 h-4" />
              )}
              Delete
            </Button>
          </div>
        </div>
      </div>

      {/* Delete Confirmation Modal */}
      {fileToDelete && (
        <ConfirmModal
          title="Delete File"
          message={`Delete "${fileToDelete.split('/').pop()}"? This cannot be undone.`}
          confirmText="Delete"
          variant="danger"
          onConfirm={() => {
            deleteMutation.mutate(fileToDelete);
            setFileToDelete(null);
          }}
          onCancel={() => setFileToDelete(null)}
        />
      )}
    </div>
  );
}
