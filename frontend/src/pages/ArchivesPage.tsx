import { useState, useRef, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Download,
  Trash2,
  Clock,
  Package,
  Layers,
  Search,
  Filter,
  Image,
  Box,
  Printer,
  Upload,
  ExternalLink,
  CheckSquare,
  Square,
  X,
  Globe,
  Pencil,
  LayoutGrid,
  List,
  CalendarDays,
  ArrowUpDown,
  Star,
  Tag,
  StickyNote,
  FolderOpen,
  Calendar,
  AlertCircle,
  Copy,
  Film,
  ScanSearch,
  QrCode,
  Camera,
  FileText,
  FileCode,
  MoreVertical,
} from 'lucide-react';
import { api } from '../api/client';
import { useIsMobile } from '../hooks/useIsMobile';
import type { Archive } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ModelViewerModal } from '../components/ModelViewerModal';
import { ReprintModal } from '../components/ReprintModal';
import { UploadModal } from '../components/UploadModal';
import { ConfirmModal } from '../components/ConfirmModal';
import { EditArchiveModal } from '../components/EditArchiveModal';
import { ContextMenu, type ContextMenuItem } from '../components/ContextMenu';
import { BatchTagModal } from '../components/BatchTagModal';
import { CalendarView } from '../components/CalendarView';
import { QRCodeModal } from '../components/QRCodeModal';
import { PhotoGalleryModal } from '../components/PhotoGalleryModal';
import { ProjectPageModal } from '../components/ProjectPageModal';
import { TimelapseViewer } from '../components/TimelapseViewer';
import { AddToQueueModal } from '../components/AddToQueueModal';
import { useToast } from '../contexts/ToastContext';

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function ArchiveCard({
  archive,
  printerName,
  isSelected,
  onSelect,
  selectionMode,
}: {
  archive: Archive;
  printerName: string;
  isSelected: boolean;
  onSelect: (id: number) => void;
  selectionMode: boolean;
}) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const isMobile = useIsMobile();
  const [showViewer, setShowViewer] = useState(false);
  const [showReprint, setShowReprint] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [showTimelapse, setShowTimelapse] = useState(false);
  const [showTimelapseSelect, setShowTimelapseSelect] = useState(false);
  const [availableTimelapses, setAvailableTimelapses] = useState<Array<{ name: string; path: string; size: number; mtime: string | null }>>([]);
  const [showQRCode, setShowQRCode] = useState(false);
  const [showPhotos, setShowPhotos] = useState(false);
  const [showProjectPage, setShowProjectPage] = useState(false);
  const [showSchedule, setShowSchedule] = useState(false);
  const [showDeleteSource3mfConfirm, setShowDeleteSource3mfConfirm] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const source3mfInputRef = useRef<HTMLInputElement>(null);

  const source3mfUploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadSource3mf(archive.id, file),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(`Source 3MF attached: ${data.filename}`);
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to upload source 3MF', 'error');
    },
  });

  const source3mfDeleteMutation = useMutation({
    mutationFn: () => api.deleteSource3mf(archive.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast('Source 3MF removed');
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to remove source 3MF', 'error');
    },
  });

  const timelapseScanMutation = useMutation({
    mutationFn: () => api.scanArchiveTimelapse(archive.id),
    onSuccess: (data) => {
      if (data.status === 'attached') {
        queryClient.invalidateQueries({ queryKey: ['archives'] });
        showToast(`Timelapse attached: ${data.filename}`);
      } else if (data.status === 'exists') {
        showToast('Timelapse already attached');
      } else if (data.status === 'not_found' && data.available_files && data.available_files.length > 0) {
        // Show selection dialog
        setAvailableTimelapses(data.available_files);
        setShowTimelapseSelect(true);
      } else {
        showToast(data.message || 'No matching timelapse found', 'warning');
      }
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to scan for timelapse', 'error');
    },
  });

  const timelapseSelectMutation = useMutation({
    mutationFn: (filename: string) => api.selectArchiveTimelapse(archive.id, filename),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(`Timelapse attached: ${data.filename}`);
      setShowTimelapseSelect(false);
      setAvailableTimelapses([]);
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to attach timelapse', 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteArchive(archive.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast('Archive deleted');
    },
    onError: () => {
      showToast('Failed to delete archive', 'error');
    },
  });

  const favoriteMutation = useMutation({
    mutationFn: () => api.toggleFavorite(archive.id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(data.is_favorite ? 'Added to favorites' : 'Removed from favorites');
    },
  });

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY });
  };

  const contextMenuItems: ContextMenuItem[] = [
    {
      label: 'Print',
      icon: <Printer className="w-4 h-4" />,
      onClick: () => setShowReprint(true),
    },
    {
      label: 'Schedule',
      icon: <Calendar className="w-4 h-4" />,
      onClick: () => setShowSchedule(true),
    },
    {
      label: 'Open in Bambu Studio',
      icon: <ExternalLink className="w-4 h-4" />,
      onClick: () => {
        const filename = archive.print_name || archive.filename || 'model';
        const downloadUrl = `${window.location.origin}${api.getArchiveForSlicer(archive.id, filename)}`;
        window.location.href = `bambustudioopen://${encodeURIComponent(downloadUrl)}`;
      },
    },
    {
      label: 'View on MakerWorld',
      icon: <Globe className="w-4 h-4" />,
      onClick: () => archive.makerworld_url && window.open(archive.makerworld_url, '_blank'),
      disabled: !archive.makerworld_url,
    },
    { label: '', divider: true, onClick: () => {} },
    {
      label: '3D Preview',
      icon: <Box className="w-4 h-4" />,
      onClick: () => setShowViewer(true),
    },
    {
      label: 'View Timelapse',
      icon: <Film className="w-4 h-4" />,
      onClick: () => setShowTimelapse(true),
      disabled: !archive.timelapse_path,
    },
    {
      label: 'Scan for Timelapse',
      icon: <ScanSearch className="w-4 h-4" />,
      onClick: () => timelapseScanMutation.mutate(),
      disabled: !archive.printer_id || !!archive.timelapse_path || timelapseScanMutation.isPending,
    },
    { label: '', divider: true, onClick: () => {} },
    {
      label: archive.source_3mf_path ? 'Download Source 3MF' : 'Upload Source 3MF',
      icon: <FileCode className="w-4 h-4" />,
      onClick: () => {
        if (archive.source_3mf_path) {
          const link = document.createElement('a');
          link.href = api.getSource3mfDownloadUrl(archive.id);
          link.download = `${archive.print_name || archive.filename}_source.3mf`;
          link.click();
        } else {
          source3mfInputRef.current?.click();
        }
      },
    },
    ...(archive.source_3mf_path ? [{
      label: 'Replace Source 3MF',
      icon: <Upload className="w-4 h-4" />,
      onClick: () => source3mfInputRef.current?.click(),
    },
    {
      label: 'Remove Source 3MF',
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => setShowDeleteSource3mfConfirm(true),
      danger: true,
    }] : []),
    { label: '', divider: true, onClick: () => {} },
    {
      label: 'Download',
      icon: <Download className="w-4 h-4" />,
      onClick: () => {
        const link = document.createElement('a');
        link.href = api.getArchiveDownload(archive.id);
        link.download = `${archive.print_name || archive.filename}.3mf`;
        link.click();
      },
    },
    {
      label: 'Copy Download Link',
      icon: <Copy className="w-4 h-4" />,
      onClick: () => {
        const url = `${window.location.origin}${api.getArchiveDownload(archive.id)}`;
        navigator.clipboard.writeText(url).then(() => {
          showToast('Link copied to clipboard');
        }).catch(() => {
          showToast('Failed to copy link', 'error');
        });
      },
    },
    {
      label: 'QR Code',
      icon: <QrCode className="w-4 h-4" />,
      onClick: () => setShowQRCode(true),
    },
    {
      label: `View Photos${archive.photos?.length ? ` (${archive.photos.length})` : ''}`,
      icon: <Camera className="w-4 h-4" />,
      onClick: () => setShowPhotos(true),
      disabled: !archive.photos?.length,
    },
    {
      label: 'Project Page',
      icon: <FileText className="w-4 h-4" />,
      onClick: () => setShowProjectPage(true),
    },
    { label: '', divider: true, onClick: () => {} },
    {
      label: archive.is_favorite ? 'Remove from Favorites' : 'Add to Favorites',
      icon: <Star className={`w-4 h-4 ${archive.is_favorite ? 'fill-yellow-400 text-yellow-400' : ''}`} />,
      onClick: () => favoriteMutation.mutate(),
    },
    {
      label: 'Edit',
      icon: <Pencil className="w-4 h-4" />,
      onClick: () => setShowEdit(true),
    },
    {
      label: isSelected ? 'Deselect' : 'Select',
      icon: isSelected ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />,
      onClick: () => onSelect(archive.id),
    },
    { label: '', divider: true, onClick: () => {} },
    {
      label: 'Delete',
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => setShowDeleteConfirm(true),
      danger: true,
    },
  ];

  return (
    <Card
      className={`relative flex flex-col ${isSelected ? 'ring-2 ring-bambu-green' : ''} ${selectionMode ? 'cursor-pointer' : ''}`}
      onContextMenu={handleContextMenu}
      onClick={selectionMode ? () => onSelect(archive.id) : undefined}
    >
      {/* Selection checkbox */}
      {selectionMode && (
        <button
          className="absolute top-2 left-2 z-10 p-1 rounded bg-black/50 hover:bg-black/70 transition-colors"
          onClick={(e) => { e.stopPropagation(); onSelect(archive.id); }}
        >
          {isSelected ? (
            <CheckSquare className="w-5 h-5 text-bambu-green" />
          ) : (
            <Square className="w-5 h-5 text-white" />
          )}
        </button>
      )}

      {/* Thumbnail */}
      <div className="aspect-video bg-bambu-dark relative flex-shrink-0 overflow-hidden rounded-t-xl">
        {archive.thumbnail_path ? (
          <img
            src={api.getArchiveThumbnail(archive.id)}
            alt={archive.print_name || archive.filename}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Image className="w-12 h-12 text-bambu-dark-tertiary" />
          </div>
        )}
        {/* Mobile menu button */}
        {isMobile && (
          <button
            className="absolute top-2 right-10 p-1.5 rounded bg-black/50 hover:bg-black/70 transition-colors"
            onClick={(e) => {
              e.stopPropagation();
              const rect = e.currentTarget.getBoundingClientRect();
              setContextMenu({ x: rect.left, y: rect.bottom + 4 });
            }}
            title="Menu"
          >
            <MoreVertical className="w-5 h-5 text-white" />
          </button>
        )}
        {/* Favorite star */}
        <button
          className="absolute top-2 right-2 p-1 rounded bg-black/50 hover:bg-black/70 transition-colors"
          onClick={(e) => {
            e.stopPropagation();
            favoriteMutation.mutate();
          }}
          title={archive.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
        >
          <Star
            className={`w-5 h-5 ${archive.is_favorite ? 'text-yellow-400 fill-yellow-400' : 'text-white'}`}
          />
        </button>
        {archive.status === 'failed' && (
          <div className="absolute top-2 left-12 px-2 py-1 rounded text-xs bg-red-500/80 text-white">
            failed
          </div>
        )}
        {/* Duplicate badge */}
        {archive.duplicate_count > 0 && (
          <div
            className="absolute top-2 right-2 px-2 py-1 rounded text-xs bg-purple-500/80 text-white flex items-center gap-1"
            title="This model has been printed before"
          >
            <Copy className="w-3 h-3" />
            duplicate
          </div>
        )}
        {/* Source 3MF badge */}
        {archive.source_3mf_path && (
          <button
            className="absolute bottom-2 left-2 p-1.5 rounded bg-black/60 hover:bg-black/80 transition-colors"
            onClick={(e) => {
              e.stopPropagation();
              // Open source 3MF in Bambu Studio - use filename in URL for slicer compatibility
              const sourceName = (archive.print_name || archive.filename || 'source').replace(/\.gcode\.3mf$/i, '') + '_source';
              const downloadUrl = `${window.location.origin}${api.getSource3mfForSlicer(archive.id, sourceName)}`;
              window.location.href = `bambustudioopen://${encodeURIComponent(downloadUrl)}`;
            }}
            title="Open source 3MF in Bambu Studio (right-click for more options)"
          >
            <FileCode className="w-4 h-4 text-orange-400" />
          </button>
        )}
        {/* Timelapse badge */}
        {archive.timelapse_path && (
          <button
            className="absolute bottom-2 right-2 p-1.5 rounded bg-black/60 hover:bg-black/80 transition-colors"
            onClick={(e) => {
              e.stopPropagation();
              setShowTimelapse(true);
            }}
            title="View timelapse"
          >
            <Film className="w-4 h-4 text-bambu-green" />
          </button>
        )}
        {/* Photos badge */}
        {archive.photos && archive.photos.length > 0 && (
          <button
            className={`absolute bottom-2 ${archive.timelapse_path ? 'right-12' : 'right-2'} p-1.5 rounded bg-black/60 hover:bg-black/80 transition-colors`}
            onClick={(e) => {
              e.stopPropagation();
              setShowPhotos(true);
            }}
            title={`View ${archive.photos.length} photo${archive.photos.length > 1 ? 's' : ''}`}
          >
            <Camera className="w-4 h-4 text-blue-400" />
            {archive.photos.length > 1 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-blue-500 rounded-full text-[10px] text-white flex items-center justify-center">
                {archive.photos.length}
              </span>
            )}
          </button>
        )}
      </div>

      <CardContent className="p-4 flex-1 flex flex-col">
        {/* Title */}
        <h3 className="font-medium text-white mb-1 truncate">
          {archive.print_name || archive.filename}
        </h3>
        <p className="text-xs text-bambu-gray mb-3">{printerName}</p>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-2 text-xs mb-4 min-h-[48px]">
          {(archive.print_time_seconds || archive.actual_time_seconds) && (
            <div className="flex items-center gap-1.5 text-bambu-gray" title={
              archive.time_accuracy
                ? `Estimated: ${formatDuration(archive.print_time_seconds || 0)}\nActual: ${formatDuration(archive.actual_time_seconds || 0)}\nAccuracy: ${archive.time_accuracy.toFixed(0)}%`
                : archive.actual_time_seconds
                  ? `Actual: ${formatDuration(archive.actual_time_seconds)}`
                  : `Estimated: ${formatDuration(archive.print_time_seconds || 0)}`
            }>
              <Clock className="w-3 h-3" />
              {formatDuration(archive.actual_time_seconds || archive.print_time_seconds || 0)}
              {archive.time_accuracy && (
                <span className={`text-[10px] px-1 rounded ${
                  archive.time_accuracy >= 95 && archive.time_accuracy <= 105
                    ? 'bg-bambu-green/20 text-bambu-green'
                    : archive.time_accuracy > 105
                      ? 'bg-blue-500/20 text-blue-400'
                      : 'bg-orange-500/20 text-orange-400'
                }`}>
                  {archive.time_accuracy > 100 ? '+' : ''}{(archive.time_accuracy - 100).toFixed(0)}%
                </span>
              )}
            </div>
          )}
          {archive.filament_used_grams && (
            <div className="flex items-center gap-1.5 text-bambu-gray">
              <Package className="w-3 h-3" />
              {archive.filament_used_grams.toFixed(1)}g
            </div>
          )}
          {(archive.layer_height || archive.total_layers) && (
            <div className="flex items-center gap-1.5 text-bambu-gray">
              <Layers className="w-3 h-3" />
              {archive.total_layers && <span>{archive.total_layers} layers</span>}
              {archive.total_layers && archive.layer_height && <span className="text-bambu-gray/50">·</span>}
              {archive.layer_height && <span>{archive.layer_height}mm</span>}
            </div>
          )}
          {archive.filament_type && (
            <div className="flex items-center gap-1.5 col-span-2">
              <span className="text-bambu-gray text-xs">{archive.filament_type}</span>
              {archive.filament_color && (
                <div className="flex items-center gap-0.5 flex-wrap">
                  {archive.filament_color.split(',').map((color, i) => (
                    <div
                      key={i}
                      className="w-3 h-3 rounded-full border border-white/20"
                      style={{ backgroundColor: color }}
                      title={color}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Tags & Notes */}
        {(archive.tags || archive.notes) && (
          <div className="flex flex-wrap items-center gap-1.5 mb-3">
            {archive.notes && (
              <div
                className="flex items-center gap-1 px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded text-xs"
                title={archive.notes}
              >
                <StickyNote className="w-3 h-3" />
              </div>
            )}
            {archive.tags?.split(',').map((tag, i) => (
              <span
                key={i}
                className="px-1.5 py-0.5 bg-bambu-dark-tertiary text-bambu-gray-light rounded text-xs"
              >
                {tag.trim()}
              </span>
            ))}
          </div>
        )}

        {/* Spacer to push content to bottom */}
        <div className="flex-1" />

        {/* Date & Size */}
        <div className="flex items-center justify-between text-xs text-bambu-gray border-t border-bambu-dark-tertiary pt-3">
          <span>{formatDate(archive.created_at)}</span>
          <span>{formatFileSize(archive.file_size)}</span>
        </div>

        {/* Actions */}
        <div className="flex gap-1 mt-3">
          <Button
            variant="primary"
            size="sm"
            className="flex-1 min-w-0"
            onClick={() => setShowReprint(true)}
          >
            <Printer className="w-3 h-3 flex-shrink-0" />
            <span className="hidden sm:inline">Print</span>
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="min-w-0 p-1 sm:p-1.5"
            onClick={() => {
              // Use bambustudioopen:// protocol like MakerWorld does
              const filename = archive.print_name || archive.filename || 'model';
              const downloadUrl = `${window.location.origin}${api.getArchiveForSlicer(archive.id, filename)}`;
              window.location.href = `bambustudioopen://${encodeURIComponent(downloadUrl)}`;
            }}
            title="Open in Bambu Studio"
          >
            <ExternalLink className="w-3 h-3 sm:w-4 sm:h-4" />
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="min-w-0 p-1 sm:p-1.5"
            onClick={() => archive.makerworld_url && window.open(archive.makerworld_url, '_blank')}
            disabled={!archive.makerworld_url}
            title={archive.makerworld_url ? `MakerWorld: ${archive.designer || 'View project'}` : 'Not from MakerWorld'}
          >
            <Globe className={`w-3 h-3 sm:w-4 sm:h-4 ${!archive.makerworld_url ? 'opacity-20' : ''}`} />
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="min-w-0 p-1 sm:p-1.5"
            onClick={() => setShowViewer(true)}
            title="3D Preview"
          >
            <Box className="w-3 h-3 sm:w-4 sm:h-4" />
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="min-w-0 p-1 sm:p-1.5"
            onClick={() => {
              const link = document.createElement('a');
              link.href = api.getArchiveDownload(archive.id);
              link.download = `${archive.print_name || archive.filename}.3mf`;
              link.click();
            }}
            title="Download"
          >
            <Download className="w-3 h-3 sm:w-4 sm:h-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="min-w-0 p-1 sm:p-1.5"
            onClick={() => setShowEdit(true)}
            title="Edit"
          >
            <Pencil className="w-3 h-3 sm:w-4 sm:h-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="min-w-0 p-1 sm:p-1.5"
            onClick={() => setShowDeleteConfirm(true)}
            title="Delete"
          >
            <Trash2 className="w-3 h-3 sm:w-4 sm:h-4 text-red-400" />
          </Button>
        </div>
      </CardContent>

      {/* Edit Modal */}
      {showEdit && (
        <EditArchiveModal
          archive={archive}
          onClose={() => setShowEdit(false)}
        />
      )}

      {/* 3D Viewer Modal */}
      {showViewer && (
        <ModelViewerModal
          archiveId={archive.id}
          title={archive.print_name || archive.filename}
          onClose={() => setShowViewer(false)}
        />
      )}

      {/* Reprint Modal */}
      {showReprint && (
        <ReprintModal
          archiveId={archive.id}
          archiveName={archive.print_name || archive.filename}
          onClose={() => setShowReprint(false)}
          onSuccess={() => {
            // Could show a toast notification here
          }}
        />
      )}

      {/* Delete Confirmation */}
      {showDeleteConfirm && (
        <ConfirmModal
          title="Delete Archive"
          message={`Are you sure you want to delete "${archive.print_name || archive.filename}"? This action cannot be undone.`}
          confirmText="Delete"
          variant="danger"
          onConfirm={() => {
            deleteMutation.mutate();
            setShowDeleteConfirm(false);
          }}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}

      {/* Delete Source 3MF Confirmation */}
      {showDeleteSource3mfConfirm && (
        <ConfirmModal
          title="Remove Source 3MF"
          message={`Are you sure you want to remove the source 3MF file from "${archive.print_name || archive.filename}"? This will delete the original slicer project file.`}
          confirmText="Remove"
          variant="danger"
          onConfirm={() => {
            source3mfDeleteMutation.mutate();
            setShowDeleteSource3mfConfirm(false);
          }}
          onCancel={() => setShowDeleteSource3mfConfirm(false)}
        />
      )}

      {/* Context Menu */}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={contextMenuItems}
          onClose={() => setContextMenu(null)}
        />
      )}

      {/* Timelapse Viewer Modal */}
      {showTimelapse && archive.timelapse_path && (
        <TimelapseViewer
          src={api.getArchiveTimelapse(archive.id)}
          title={`${archive.print_name || archive.filename} - Timelapse`}
          downloadFilename={`${archive.print_name || archive.filename}_timelapse.mp4`}
          onClose={() => setShowTimelapse(false)}
        />
      )}

      {/* Timelapse Selection Modal */}
      {showTimelapseSelect && availableTimelapses.length > 0 && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
          <div className="bg-card-dark rounded-lg max-w-lg w-full max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-gray-700">
              <div>
                <h3 className="text-lg font-semibold text-white">Select Timelapse</h3>
                <p className="text-sm text-gray-400 mt-1">
                  No auto-match found. Select the timelapse for this print:
                </p>
              </div>
              <button
                onClick={() => {
                  setShowTimelapseSelect(false);
                  setAvailableTimelapses([]);
                }}
                className="text-gray-400 hover:text-white p-1"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="overflow-y-auto flex-1 p-2">
              {availableTimelapses.map((file) => (
                <button
                  key={file.name}
                  onClick={() => timelapseSelectMutation.mutate(file.name)}
                  disabled={timelapseSelectMutation.isPending}
                  className="w-full text-left p-3 rounded-lg hover:bg-gray-700 transition-colors flex items-center gap-3 disabled:opacity-50"
                >
                  <Film className="w-8 h-8 text-bambu-green flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-white font-medium truncate">{file.name}</p>
                    <p className="text-sm text-gray-400">
                      {formatFileSize(file.size)}
                      {file.mtime && ` • ${formatDate(file.mtime)}`}
                    </p>
                  </div>
                </button>
              ))}
            </div>
            <div className="p-4 border-t border-gray-700">
              <Button
                variant="secondary"
                onClick={() => {
                  setShowTimelapseSelect(false);
                  setAvailableTimelapses([]);
                }}
                className="w-full"
              >
                Cancel
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* QR Code Modal */}
      {showQRCode && (
        <QRCodeModal
          archiveId={archive.id}
          archiveName={archive.print_name || archive.filename}
          onClose={() => setShowQRCode(false)}
        />
      )}

      {/* Photo Gallery Modal */}
      {showPhotos && archive.photos && archive.photos.length > 0 && (
        <PhotoGalleryModal
          archiveId={archive.id}
          archiveName={archive.print_name || archive.filename}
          photos={archive.photos}
          onClose={() => setShowPhotos(false)}
          onDelete={async (filename) => {
            try {
              await api.deleteArchivePhoto(archive.id, filename);
              queryClient.invalidateQueries({ queryKey: ['archives'] });
              showToast('Photo deleted');
            } catch {
              showToast('Failed to delete photo', 'error');
            }
          }}
        />
      )}

      {/* Project Page Modal */}
      {showProjectPage && (
        <ProjectPageModal
          archiveId={archive.id}
          archiveName={archive.print_name || archive.filename}
          onClose={() => setShowProjectPage(false)}
        />
      )}

      {showSchedule && (
        <AddToQueueModal
          archiveId={archive.id}
          archiveName={archive.print_name || archive.filename}
          onClose={() => setShowSchedule(false)}
        />
      )}

      {/* Hidden file input for source 3MF upload */}
      <input
        ref={source3mfInputRef}
        type="file"
        accept=".3mf"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) {
            source3mfUploadMutation.mutate(file);
          }
          e.target.value = '';
        }}
      />
    </Card>
  );
}

type SortOption = 'date-desc' | 'date-asc' | 'name-asc' | 'name-desc' | 'size-desc' | 'size-asc';
type ViewMode = 'grid' | 'list' | 'calendar';
type Collection = 'all' | 'recent' | 'this-week' | 'this-month' | 'favorites' | 'failed' | 'duplicates';

const collections: { id: Collection; label: string; icon: React.ReactNode }[] = [
  { id: 'all', label: 'All Archives', icon: <FolderOpen className="w-4 h-4" /> },
  { id: 'recent', label: 'Last 24 Hours', icon: <Clock className="w-4 h-4" /> },
  { id: 'this-week', label: 'This Week', icon: <Calendar className="w-4 h-4" /> },
  { id: 'this-month', label: 'This Month', icon: <Calendar className="w-4 h-4" /> },
  { id: 'favorites', label: 'Favorites', icon: <Star className="w-4 h-4" /> },
  { id: 'failed', label: 'Failed Prints', icon: <AlertCircle className="w-4 h-4" /> },
  { id: 'duplicates', label: 'Duplicates', icon: <Copy className="w-4 h-4" /> },
];

export function ArchivesPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState('');
  const [filterPrinter, setFilterPrinter] = useState<number | null>(null);
  const [filterMaterial, setFilterMaterial] = useState<string | null>(null);
  const [filterColors, setFilterColors] = useState<Set<string>>(new Set());
  const [colorFilterMode, setColorFilterMode] = useState<'or' | 'and'>('or');
  const [filterFavorites, setFilterFavorites] = useState(false);
  const [filterTag, setFilterTag] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [isSelectionMode, setIsSelectionMode] = useState(false);
  const [showBulkDeleteConfirm, setShowBulkDeleteConfirm] = useState(false);
  const [showBatchTag, setShowBatchTag] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const [sortBy, setSortBy] = useState<SortOption>('date-desc');
  const [collection, setCollection] = useState<Collection>('all');

  const { data: archives, isLoading } = useQuery({
    queryKey: ['archives', filterPrinter],
    queryFn: () => api.getArchives(filterPrinter || undefined),
  });

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const bulkDeleteMutation = useMutation({
    mutationFn: async (ids: number[]) => {
      await Promise.all(ids.map((id) => api.deleteArchive(id)));
      return ids.length;
    },
    onSuccess: (count) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      setSelectedIds(new Set());
      showToast(`${count} archive${count !== 1 ? 's' : ''} deleted`);
    },
    onError: () => {
      showToast('Failed to delete archives', 'error');
    },
  });

  const printerMap = new Map(printers?.map((p) => [p.id, p.name]) || []);

  // Extract unique materials and colors from archives
  const uniqueMaterials = [...new Set(
    archives?.flatMap(a => a.filament_type?.split(', ') || []).filter(Boolean) || []
  )].sort();

  const uniqueColors = [...new Set(
    archives?.flatMap(a => a.filament_color?.split(',') || []).filter(Boolean) || []
  )];

  const uniqueTags = [...new Set(
    archives?.flatMap(a => a.tags?.split(',').map(t => t.trim()) || []).filter(Boolean) || []
  )].sort();

  const filteredArchives = archives
    ?.filter((a) => {
      // Collection filter
      const now = new Date();
      const archiveDate = new Date(a.created_at);
      let matchesCollection = true;

      switch (collection) {
        case 'recent':
          matchesCollection = (now.getTime() - archiveDate.getTime()) < 24 * 60 * 60 * 1000;
          break;
        case 'this-week':
          matchesCollection = (now.getTime() - archiveDate.getTime()) < 7 * 24 * 60 * 60 * 1000;
          break;
        case 'this-month':
          matchesCollection = archiveDate.getMonth() === now.getMonth() && archiveDate.getFullYear() === now.getFullYear();
          break;
        case 'favorites':
          matchesCollection = a.is_favorite === true;
          break;
        case 'failed':
          matchesCollection = a.status === 'failed';
          break;
        case 'duplicates':
          matchesCollection = a.duplicate_count > 0;
          break;
      }

      // Search filter
      const matchesSearch = (a.print_name || a.filename).toLowerCase().includes(search.toLowerCase());

      // Material filter
      const matchesMaterial = !filterMaterial ||
        (a.filament_type?.split(', ').includes(filterMaterial));

      // Color filter (AND: must have all selected colors, OR: must have any selected color)
      const archiveColors = a.filament_color?.split(',') || [];
      const matchesColor = filterColors.size === 0 ||
        (colorFilterMode === 'or'
          ? archiveColors.some(c => filterColors.has(c))
          : [...filterColors].every(c => archiveColors.includes(c)));

      // Favorites filter (only apply if not using favorites collection)
      const matchesFavorites = collection === 'favorites' || !filterFavorites || a.is_favorite;

      // Tag filter
      const archiveTags = a.tags?.split(',').map(t => t.trim()) || [];
      const matchesTag = !filterTag || archiveTags.includes(filterTag);

      return matchesCollection && matchesSearch && matchesMaterial && matchesColor && matchesFavorites && matchesTag;
    })
    .sort((a, b) => {
      switch (sortBy) {
        case 'date-desc':
          return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
        case 'date-asc':
          return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
        case 'name-asc':
          return (a.print_name || a.filename).localeCompare(b.print_name || b.filename);
        case 'name-desc':
          return (b.print_name || b.filename).localeCompare(a.print_name || a.filename);
        case 'size-desc':
          return b.file_size - a.file_size;
        case 'size-asc':
          return a.file_size - b.file_size;
        default:
          return 0;
      }
    });

  const selectionMode = isSelectionMode || selectedIds.size > 0;

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const selectAll = () => {
    if (filteredArchives) {
      setSelectedIds(new Set(filteredArchives.map((a) => a.id)));
    }
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
    setIsSelectionMode(false);
  };

  const toggleColor = (color: string) => {
    setFilterColors((prev) => {
      const next = new Set(prev);
      if (next.has(color)) {
        next.delete(color);
      } else {
        next.add(color);
      }
      return next;
    });
  };

  const clearColorFilter = () => {
    setFilterColors(new Set());
  };

  const clearTopFilters = () => {
    setSearch('');
    setFilterPrinter(null);
    setFilterMaterial(null);
    setFilterFavorites(false);
    setFilterTag(null);
  };

  const hasTopFilters = search || filterPrinter || filterMaterial || filterFavorites || filterTag;

  // Drag & drop handlers for page-wide upload
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.types.includes('Files')) {
      setIsDraggingOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    // Only hide if leaving the page (not entering a child)
    if (e.currentTarget === e.target) {
      setIsDraggingOver(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDraggingOver(false);

    const droppedFiles = Array.from(e.dataTransfer.files).filter(f => f.name.endsWith('.3mf'));
    if (droppedFiles.length > 0) {
      setUploadFiles(droppedFiles);
      setShowUpload(true);
    } else if (e.dataTransfer.files.length > 0) {
      showToast('Only .3mf files are supported', 'warning');
    }
  }, [showToast]);

  // Keyboard shortcuts
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    const target = e.target as HTMLElement;
    // Ignore if typing in an input/textarea
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
      if (e.key === 'Escape') {
        target.blur();
      }
      return;
    }

    switch (e.key) {
      case '/':
        e.preventDefault();
        searchInputRef.current?.focus();
        break;
      case 'u':
      case 'U':
        if (!e.metaKey && !e.ctrlKey) {
          e.preventDefault();
          setShowUpload(true);
        }
        break;
      case 'Escape':
        if (selectionMode) {
          clearSelection();
        }
        break;
    }
  }, [selectionMode]);

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return (
    <div
      className="p-4 md:p-8 relative min-h-full"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Drag & Drop Overlay */}
      {isDraggingOver && (
        <div className="fixed inset-0 z-50 bg-bambu-dark/90 flex items-center justify-center pointer-events-none">
          <div className="border-4 border-dashed border-bambu-green rounded-xl p-12 text-center">
            <Upload className="w-16 h-16 mx-auto mb-4 text-bambu-green" />
            <p className="text-2xl font-semibold text-white mb-2">Drop .3mf files here</p>
            <p className="text-bambu-gray">Release to upload</p>
          </div>
        </div>
      )}

      {/* Selection Toolbar */}
      {selectionMode && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl px-4 py-3 flex items-center gap-4">
          <Button variant="secondary" size="sm" onClick={clearSelection}>
            <X className="w-4 h-4" />
            Close
          </Button>
          <div className="w-px h-6 bg-bambu-dark-tertiary" />
          <span className="text-white font-medium">
            {selectedIds.size} selected
          </span>
          <div className="w-px h-6 bg-bambu-dark-tertiary" />
          <Button variant="secondary" size="sm" onClick={selectAll}>
            Select All
          </Button>
          <div className="w-px h-6 bg-bambu-dark-tertiary" />
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowBatchTag(true)}
          >
            <Tag className="w-4 h-4" />
            Tags
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              const ids = Array.from(selectedIds);
              Promise.all(ids.map(id => api.toggleFavorite(id)))
                .then(() => {
                  queryClient.invalidateQueries({ queryKey: ['archives'] });
                  showToast(`Toggled favorites for ${ids.length} archive${ids.length !== 1 ? 's' : ''}`);
                })
                .catch(() => {
                  showToast('Failed to update favorites', 'error');
                });
            }}
          >
            <Star className="w-4 h-4" />
            Favorite
          </Button>
          <Button
            size="sm"
            className="bg-red-500 hover:bg-red-600"
            onClick={() => setShowBulkDeleteConfirm(true)}
          >
            <Trash2 className="w-4 h-4" />
            Delete
          </Button>
        </div>
      )}

      <div className="flex items-center justify-between mb-8">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-white">Archives</h1>
            <select
              className="px-3 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray-light text-sm focus:border-bambu-green focus:outline-none"
              value={collection}
              onChange={(e) => setCollection(e.target.value as Collection)}
            >
              {collections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
          <p className="text-bambu-gray">
            {filteredArchives?.length || 0} of {archives?.length || 0} prints
          </p>
        </div>
        <div className="flex items-center gap-3">
          {!selectionMode && (
            <Button variant="secondary" onClick={() => setIsSelectionMode(true)}>
              <CheckSquare className="w-4 h-4" />
              Select
            </Button>
          )}
          <Button onClick={() => setShowUpload(true)}>
            <Upload className="w-4 h-4" />
            Upload 3MF
          </Button>
        </div>
      </div>

      {/* Filters */}
      <Card className="mb-6">
        <CardContent className="py-4">
          <div className="flex flex-col md:flex-row gap-3 md:gap-4 md:items-center md:flex-wrap">
            {/* Search - full width on mobile */}
            <div className="w-full md:flex-1 relative md:min-w-[200px]">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
              <input
                ref={searchInputRef}
                type="text"
                placeholder="Search archives..."
                className="w-full pl-10 pr-4 py-3 md:py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            {/* Filters - horizontal scroll on mobile */}
            <div className="flex gap-2 md:gap-4 overflow-x-auto pb-1 md:pb-0 -mx-4 px-4 md:mx-0 md:px-0 md:flex-wrap scrollbar-hide">
            <div className="flex items-center gap-2 flex-shrink-0">
              <Filter className="w-4 h-4 text-bambu-gray hidden md:block" />
              <select
                className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={filterPrinter || ''}
                onChange={(e) =>
                  setFilterPrinter(e.target.value ? Number(e.target.value) : null)
                }
              >
                <option value="">All Printers</option>
                {printers?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <Package className="w-4 h-4 text-bambu-gray hidden md:block" />
              <select
                className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={filterMaterial || ''}
                onChange={(e) =>
                  setFilterMaterial(e.target.value || null)
                }
              >
                <option value="">All Materials</option>
                {uniqueMaterials.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
            <button
              onClick={() => setFilterFavorites(!filterFavorites)}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors flex-shrink-0 ${
                filterFavorites
                  ? 'bg-yellow-500/20 border-yellow-500 text-yellow-400'
                  : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
              }`}
              title={filterFavorites ? 'Show all' : 'Show favorites only'}
            >
              <Star className={`w-4 h-4 ${filterFavorites ? 'fill-yellow-400' : ''}`} />
              <span className="text-sm hidden md:inline">Favorites</span>
            </button>
            {uniqueTags.length > 0 && (
              <div className="flex items-center gap-2 flex-shrink-0">
                <Tag className="w-4 h-4 text-bambu-gray hidden md:block" />
                <select
                  className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  value={filterTag || ''}
                  onChange={(e) => setFilterTag(e.target.value || null)}
                >
                  <option value="">All Tags</option>
                  {uniqueTags.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="flex items-center gap-2 flex-shrink-0">
              <ArrowUpDown className="w-4 h-4 text-bambu-gray hidden md:block" />
              <select
                className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as SortOption)}
              >
                <option value="date-desc">Newest first</option>
                <option value="date-asc">Oldest first</option>
                <option value="name-asc">Name A-Z</option>
                <option value="name-desc">Name Z-A</option>
                <option value="size-desc">Largest first</option>
                <option value="size-asc">Smallest first</option>
              </select>
            </div>
            <div className="flex items-center border border-bambu-dark-tertiary rounded-lg overflow-hidden flex-shrink-0">
              <button
                className={`p-2 ${viewMode === 'grid' ? 'bg-bambu-green text-white' : 'bg-bambu-dark text-bambu-gray hover:text-white'}`}
                onClick={() => setViewMode('grid')}
                title="Grid view"
              >
                <LayoutGrid className="w-4 h-4" />
              </button>
              <button
                className={`p-2 ${viewMode === 'list' ? 'bg-bambu-green text-white' : 'bg-bambu-dark text-bambu-gray hover:text-white'}`}
                onClick={() => setViewMode('list')}
                title="List view"
              >
                <List className="w-4 h-4" />
              </button>
              <button
                className={`p-2 ${viewMode === 'calendar' ? 'bg-bambu-green text-white' : 'bg-bambu-dark text-bambu-gray hover:text-white'}`}
                onClick={() => setViewMode('calendar')}
                title="Calendar view"
              >
                <CalendarDays className="w-4 h-4" />
              </button>
            </div>
            </div>
            {hasTopFilters && (
              <Button
                variant="ghost"
                size="sm"
                onClick={clearTopFilters}
                className="text-bambu-gray hover:text-white"
              >
                <X className="w-4 h-4" />
                Reset
              </Button>
            )}
          </div>
          {/* Color Filter */}
          {uniqueColors.length > 0 && (
            <div className="flex items-center gap-3 mt-4 pt-4 border-t border-bambu-dark-tertiary">
              <span className="text-xs text-bambu-gray">Colors:</span>
              {filterColors.size > 1 && (
                <button
                  onClick={() => setColorFilterMode(m => m === 'or' ? 'and' : 'or')}
                  className={`px-2 py-0.5 text-xs rounded transition-colors ${
                    colorFilterMode === 'and'
                      ? 'bg-bambu-green text-white'
                      : 'bg-bambu-dark-tertiary text-bambu-gray hover:text-white'
                  }`}
                  title={colorFilterMode === 'or' ? 'Match ANY selected color' : 'Match ALL selected colors'}
                >
                  {colorFilterMode.toUpperCase()}
                </button>
              )}
              <div className="flex items-center gap-1.5 flex-wrap">
                {uniqueColors.map((color) => (
                  <button
                    key={color}
                    onClick={() => toggleColor(color)}
                    className={`w-6 h-6 rounded-full border-2 transition-all ${
                      filterColors.has(color)
                        ? 'border-bambu-green scale-110'
                        : 'border-white/20 hover:border-white/40'
                    }`}
                    style={{ backgroundColor: color }}
                    title={color}
                  />
                ))}
              </div>
              {filterColors.size > 0 && (
                <button
                  onClick={clearColorFilter}
                  className="text-xs text-bambu-gray hover:text-white flex items-center gap-1"
                >
                  <X className="w-3 h-3" />
                  Clear
                </button>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Archives */}
      {isLoading ? (
        <div className="text-center py-12 text-bambu-gray">Loading archives...</div>
      ) : filteredArchives?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-bambu-gray">
              {search ? 'No archives match your search' : 'No archives yet'}
            </p>
            <p className="text-sm text-bambu-gray mt-2">
              Archives are created automatically when prints complete
            </p>
          </CardContent>
        </Card>
      ) : viewMode === 'calendar' ? (
        <Card className="p-6">
          <CalendarView
            archives={filteredArchives || []}
            onArchiveClick={(archive) => {
              // Switch to grid view and search for the archive
              setSearch(archive.print_name || archive.filename);
              setViewMode('grid');
            }}
          />
        </Card>
      ) : viewMode === 'grid' ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {filteredArchives?.map((archive) => (
            <ArchiveCard
              key={archive.id}
              archive={archive}
              printerName={archive.printer_id ? printerMap.get(archive.printer_id) || 'Unknown' : 'No Printer'}
              isSelected={selectedIds.has(archive.id)}
              onSelect={toggleSelect}
              selectionMode={selectionMode}
            />
          ))}
        </div>
      ) : viewMode === 'list' ? (
        <Card>
          <div className="divide-y divide-bambu-dark-tertiary">
            {/* List Header */}
            <div className="grid grid-cols-12 gap-4 px-4 py-3 text-xs text-bambu-gray font-medium">
              <div className="col-span-1"></div>
              <div className="col-span-4">Name</div>
              <div className="col-span-2">Printer</div>
              <div className="col-span-2">Date</div>
              <div className="col-span-1">Size</div>
              <div className="col-span-2 text-right">Actions</div>
            </div>
            {/* List Items */}
            {filteredArchives?.map((archive) => (
              <div
                key={archive.id}
                className={`grid grid-cols-12 gap-4 px-4 py-3 items-center hover:bg-bambu-dark-tertiary/30 ${
                  selectedIds.has(archive.id) ? 'bg-bambu-green/10' : ''
                }`}
              >
                <div className="col-span-1 flex items-center gap-2">
                  {selectionMode && (
                    <button onClick={() => toggleSelect(archive.id)}>
                      {selectedIds.has(archive.id) ? (
                        <CheckSquare className="w-4 h-4 text-bambu-green" />
                      ) : (
                        <Square className="w-4 h-4 text-bambu-gray" />
                      )}
                    </button>
                  )}
                  {archive.thumbnail_path ? (
                    <img
                      src={api.getArchiveThumbnail(archive.id)}
                      alt=""
                      className="w-10 h-10 object-cover rounded"
                    />
                  ) : (
                    <div className="w-10 h-10 bg-bambu-dark rounded flex items-center justify-center">
                      <Image className="w-5 h-5 text-bambu-dark-tertiary" />
                    </div>
                  )}
                </div>
                <div className="col-span-4">
                  <div className="flex items-center gap-2">
                    <p className="text-white text-sm truncate">{archive.print_name || archive.filename}</p>
                    {archive.timelapse_path && (
                      <span title="Has timelapse">
                        <Film className="w-3.5 h-3.5 text-bambu-green flex-shrink-0" />
                      </span>
                    )}
                  </div>
                  {archive.filament_type && (
                    <div className="flex items-center gap-1.5 mt-0.5">
                      <span className="text-xs text-bambu-gray">{archive.filament_type}</span>
                      {archive.filament_color && (
                        <div className="flex items-center gap-0.5 flex-wrap">
                          {archive.filament_color.split(',').map((color, i) => (
                            <div
                              key={i}
                              className="w-2.5 h-2.5 rounded-full border border-white/20"
                              style={{ backgroundColor: color }}
                              title={color}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div className="col-span-2 text-sm text-bambu-gray truncate">
                  {archive.printer_id ? printerMap.get(archive.printer_id) || 'Unknown' : 'No Printer'}
                </div>
                <div className="col-span-2 text-sm text-bambu-gray">
                  {new Date(archive.created_at).toLocaleDateString()}
                </div>
                <div className="col-span-1 text-sm text-bambu-gray">
                  {formatFileSize(archive.file_size)}
                </div>
                <div className="col-span-2 flex justify-end gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      const filename = archive.print_name || archive.filename || 'model';
                      const downloadUrl = `${window.location.origin}${api.getArchiveForSlicer(archive.id, filename)}`;
                      window.location.href = `bambustudioopen://${encodeURIComponent(downloadUrl)}`;
                    }}
                    title="Open in Slicer"
                  >
                    <ExternalLink className="w-4 h-4" />
                  </Button>
                  {archive.makerworld_url && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => window.open(archive.makerworld_url!, '_blank')}
                      title="MakerWorld"
                    >
                      <Globe className="w-4 h-4" />
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      const link = document.createElement('a');
                      link.href = api.getArchiveDownload(archive.id);
                      link.download = `${archive.print_name || archive.filename}.3mf`;
                      link.click();
                    }}
                    title="Download"
                  >
                    <Download className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {/* Upload Modal */}
      {showUpload && (
        <UploadModal
          onClose={() => {
            setShowUpload(false);
            setUploadFiles([]);
          }}
          initialFiles={uploadFiles}
        />
      )}

      {/* Bulk Delete Confirmation */}
      {showBulkDeleteConfirm && (
        <ConfirmModal
          title="Delete Archives"
          message={`Are you sure you want to delete ${selectedIds.size} archive${selectedIds.size > 1 ? 's' : ''}? This action cannot be undone.`}
          confirmText={`Delete ${selectedIds.size}`}
          variant="danger"
          onConfirm={() => {
            bulkDeleteMutation.mutate(Array.from(selectedIds));
            setShowBulkDeleteConfirm(false);
          }}
          onCancel={() => setShowBulkDeleteConfirm(false)}
        />
      )}

      {/* Batch Tag Modal */}
      {showBatchTag && (
        <BatchTagModal
          selectedIds={Array.from(selectedIds)}
          existingTags={uniqueTags}
          onClose={() => setShowBatchTag(false)}
        />
      )}
    </div>
  );
}
