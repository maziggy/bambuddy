import { useState, useRef, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
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
  FileSpreadsheet,
  GitCompare,
  Loader2,
  FolderKanban,
  ChevronLeft,
  ChevronRight,
  Settings,
  User,
  Play,
  ClipboardList,
} from 'lucide-react';
import { api } from '../api/client';
import { openInSlicer, type SlicerType } from '../utils/slicer';
import { formatDateTime, formatDateOnly, parseUTCDate, type TimeFormat, formatDuration } from '../utils/date';
import { useIsMobile } from '../hooks/useIsMobile';
import type { Archive, ProjectListItem } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ModelViewerModal } from '../components/ModelViewerModal';
import { PrintModal } from '../components/PrintModal';
import { UploadModal } from '../components/UploadModal';
import { ConfirmModal } from '../components/ConfirmModal';
import { EditArchiveModal } from '../components/EditArchiveModal';
import { ContextMenu, type ContextMenuItem } from '../components/ContextMenu';
import { BatchTagModal } from '../components/BatchTagModal';
import { BatchProjectModal } from '../components/BatchProjectModal';
import { CalendarView } from '../components/CalendarView';
import { QRCodeModal } from '../components/QRCodeModal';
import { PhotoGalleryModal } from '../components/PhotoGalleryModal';
import { ProjectPageModal } from '../components/ProjectPageModal';
import { TimelapseViewer } from '../components/TimelapseViewer';
import { CompareArchivesModal } from '../components/CompareArchivesModal';
import { PendingUploadsPanel } from '../components/PendingUploadsPanel';
import { TagManagementModal } from '../components/TagManagementModal';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { formatFileSize } from '../utils/file';

type TFunction = (key: string, options?: Record<string, unknown>) => string;

/**
 * Check if an archive filename represents a sliced/printable file.
 * Matches: .gcode, .gcode.3mf, .gcode.anything
 */
function isSlicedFile(filename: string | null | undefined): boolean {
  if (!filename) return false;
  const lower = filename.toLowerCase();
  // Match .gcode at end OR .gcode. followed by anything (like .gcode.3mf)
  return lower.endsWith('.gcode') || lower.includes('.gcode.');
}

function getArchiveFileType(filename: string | null | undefined): string | undefined {
  if (!filename) return undefined;
  const lower = filename.toLowerCase();
  if (lower.endsWith('.3mf')) return '3mf';
  if (lower.endsWith('.stl')) return 'stl';
  if (lower.endsWith('.gcode') || lower.includes('.gcode.')) return 'gcode';
  return lower.split('.').pop();
}

// formatDate imported from '../utils/date' - handles UTC conversion

/**
 * Open an archive file in the slicer.
 * Fetches a short-lived download token, then builds a token-authenticated URL
 * that bypasses auth middleware (slicer protocol handlers can't send auth headers).
 */
async function openInSlicerWithToken(
  archiveId: number,
  filename: string,
  resourceType: 'file' | 'source',
  slicer: SlicerType,
): Promise<void> {
  try {
    if (resourceType === 'source') {
      const { token } = await api.createSourceSlicerToken(archiveId);
      const path = api.getSourceSlicerDownloadUrl(archiveId, token, filename);
      openInSlicer(`${window.location.origin}${path}`, slicer);
    } else {
      const { token } = await api.createArchiveSlicerToken(archiveId);
      const path = api.getArchiveSlicerDownloadUrl(archiveId, token, filename);
      openInSlicer(`${window.location.origin}${path}`, slicer);
    }
  } catch {
    // Fallback to direct URL (works when auth is disabled)
    const path = resourceType === 'source'
      ? api.getSource3mfForSlicer(archiveId, filename)
      : api.getArchiveForSlicer(archiveId, filename);
    openInSlicer(`${window.location.origin}${path}`, slicer);
  }
}

function ArchiveCard({
  archive,
  printerName,
  isSelected,
  onSelect,
  selectionMode,
  projects,
  isHighlighted,
  timeFormat = 'system',
  preferredSlicer = 'bambu_studio',
  t,
}: {
  archive: Archive;
  printerName: string;
  isSelected: boolean;
  onSelect: (id: number) => void;
  selectionMode: boolean;
  projects: ProjectListItem[] | undefined;
  isHighlighted?: boolean;
  timeFormat?: TimeFormat;
  preferredSlicer?: SlicerType;
  t: TFunction;
}) {
  // Debug: log when card is highlighted
  if (isHighlighted) {
    console.log('ArchiveCard isHighlighted=true for archive:', archive.id);
  }

  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission, canModify } = useAuth();
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
  const [showDeleteF3dConfirm, setShowDeleteF3dConfirm] = useState(false);
  const [showDeleteTimelapseConfirm, setShowDeleteTimelapseConfirm] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const [currentPlateIndex, setCurrentPlateIndex] = useState<number | null>(null);
  const [showPlateNav, setShowPlateNav] = useState(false);
  const source3mfInputRef = useRef<HTMLInputElement>(null);
  const f3dInputRef = useRef<HTMLInputElement>(null);
  const timelapseInputRef = useRef<HTMLInputElement>(null);

  // Fetch plates data for multi-plate browsing (lazy - only when hovering)
  const { data: platesData } = useQuery({
    queryKey: ['archive-plates', archive.id],
    queryFn: () => api.getArchivePlates(archive.id),
    enabled: showPlateNav, // Only fetch when user hovers to see navigation
    staleTime: 5 * 60 * 1000, // Cache for 5 minutes
  });

  const plates = platesData?.plates ?? [];
  const isMultiPlate = platesData?.is_multi_plate ?? false;
  const displayPlateIndex = currentPlateIndex ?? 0;

  const timelapseDeleteMutation = useMutation({
    mutationFn: () => api.deleteArchiveTimelapse(archive.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.timelapseRemoved'));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedRemoveTimelapse'), 'error');
    },
  });

  const timelapseUploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadArchiveTimelapse(archive.id, file),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.timelapseUploaded', { filename: data.filename }));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedUploadTimelapse'), 'error');
    },
  });

  const source3mfUploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadSource3mf(archive.id, file),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.source3mfAttached', { filename: data.filename }));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedUploadSource3mf'), 'error');
    },
  });

  const source3mfDeleteMutation = useMutation({
    mutationFn: () => api.deleteSource3mf(archive.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.source3mfRemoved'));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedRemoveSource3mf'), 'error');
    },
  });

  const f3dUploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadF3d(archive.id, file),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.f3dAttached', { filename: data.filename }));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedUploadF3d'), 'error');
    },
  });

  const f3dDeleteMutation = useMutation({
    mutationFn: () => api.deleteF3d(archive.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.f3dRemoved'));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedRemoveF3d'), 'error');
    },
  });

  const timelapseScanMutation = useMutation({
    mutationFn: () => api.scanArchiveTimelapse(archive.id),
    onSuccess: (data) => {
      if (data.status === 'attached') {
        queryClient.invalidateQueries({ queryKey: ['archives'] });
        showToast(t('archives.toast.timelapseAttached', { filename: data.filename }));
      } else if (data.status === 'exists') {
        showToast(t('archives.toast.timelapseAlreadyAttached'));
      } else if (data.status === 'not_found' && data.available_files && data.available_files.length > 0) {
        // Show selection dialog
        setAvailableTimelapses(data.available_files);
        setShowTimelapseSelect(true);
      } else {
        showToast(data.message || t('archives.toast.noMatchingTimelapse'), 'warning');
      }
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedScanTimelapse'), 'error');
    },
  });

  const timelapseSelectMutation = useMutation({
    mutationFn: (filename: string) => api.selectArchiveTimelapse(archive.id, filename),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.timelapseAttached', { filename: data.filename }));
      setShowTimelapseSelect(false);
      setAvailableTimelapses([]);
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedAttachTimelapse'), 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteArchive(archive.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.archiveDeleted'));
    },
    onError: () => {
      showToast(t('archives.toast.failedDeleteArchive'), 'error');
    },
  });

  const favoriteMutation = useMutation({
    mutationFn: () => api.toggleFavorite(archive.id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(data.is_favorite ? t('archives.toast.addedToFavorites') : t('archives.toast.removedFromFavorites'));
    },
  });

  // Query for linked folders
  const { data: linkedFolders } = useQuery({
    queryKey: ['archive-folders', archive.id],
    queryFn: () => api.getLibraryFoldersByArchive(archive.id),
  });

  const assignProjectMutation = useMutation({
    mutationFn: (projectId: number | null) => api.updateArchive(archive.id, { project_id: projectId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      showToast(t('archives.toast.projectUpdated'));
    },
    onError: () => {
      showToast(t('archives.toast.failedUpdateProject'), 'error');
    },
  });

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY });
  };

  const isGcodeFile = isSlicedFile(archive.filename);

  const contextMenuItems: ContextMenuItem[] = [
    // For gcode files: show Print option
    // For source files: show Slice as the primary action
    ...(isGcodeFile ? [
      {
        label: t('archives.menu.print'),
        icon: <Printer className="w-4 h-4" />,
        onClick: () => setShowReprint(true),
        disabled: !archive.file_path || !canModify('archives', 'reprint', archive.created_by_id),
        title: !archive.file_path ? t('archives.card.noFileForReprint') : !canModify('archives', 'reprint', archive.created_by_id) ? t('archives.permission.noReprint') : undefined,
      },
      {
        label: t('archives.menu.schedule'),
        icon: <Calendar className="w-4 h-4" />,
        onClick: () => setShowSchedule(true),
        disabled: !archive.file_path || !hasPermission('queue:create'),
        title: !archive.file_path ? t('archives.card.noFileForReprint') : !hasPermission('queue:create') ? t('archives.permission.noAddToQueue') : undefined,
      },
      {
        label: t('archives.menu.openInBambuStudio'),
        icon: <ExternalLink className="w-4 h-4" />,
        onClick: () => {
          const filename = archive.print_name || archive.filename || 'model';
          openInSlicerWithToken(archive.id, filename, 'file', preferredSlicer);
        },
        disabled: !archive.file_path,
        title: !archive.file_path ? t('archives.card.noFileForReprint') : undefined,
      },
    ] : [
      {
        label: t('archives.menu.slice'),
        icon: <ExternalLink className="w-4 h-4" />,
        onClick: () => {
          const filename = archive.print_name || archive.filename || 'model';
          openInSlicerWithToken(archive.id, filename, 'file', preferredSlicer);
        },
      },
    ]),
    {
      label: archive.external_url ? t('archives.menu.externalLink') : t('archives.menu.viewOnMakerWorld'),
      icon: <Globe className="w-4 h-4" />,
      onClick: () => {
        const url = archive.external_url || archive.makerworld_url;
        if (url) window.open(url, '_blank');
      },
      disabled: !archive.external_url && !archive.makerworld_url,
    },
    { label: '', divider: true, onClick: () => {} },
    {
      label: t('archives.menu.preview3d'),
      icon: <Box className="w-4 h-4" />,
      onClick: () => setShowViewer(true),
    },
    {
      label: t('archives.menu.viewTimelapse'),
      icon: <Film className="w-4 h-4" />,
      onClick: () => setShowTimelapse(true),
      disabled: !archive.timelapse_path,
    },
    {
      label: t('archives.menu.scanForTimelapse'),
      icon: <ScanSearch className="w-4 h-4" />,
      onClick: () => timelapseScanMutation.mutate(),
      disabled: !archive.printer_id || !!archive.timelapse_path || timelapseScanMutation.isPending || !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    {
      label: t('archives.menu.uploadTimelapse'),
      icon: <Upload className="w-4 h-4" />,
      onClick: () => timelapseInputRef.current?.click(),
      disabled: !!archive.timelapse_path || !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    ...(archive.timelapse_path ? [{
      label: t('archives.menu.removeTimelapse'),
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => setShowDeleteTimelapseConfirm(true),
      danger: true,
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    }] : []),
    { label: '', divider: true, onClick: () => {} },
    {
      label: archive.source_3mf_path ? t('archives.menu.downloadSource3mf') : t('archives.menu.uploadSource3mf'),
      icon: <FileCode className="w-4 h-4" />,
      onClick: () => {
        if (archive.source_3mf_path) {
          api.downloadSource3mf(archive.id).catch((err) => {
            console.error('Source 3MF download failed:', err);
          });
        } else {
          source3mfInputRef.current?.click();
        }
      },
      disabled: !archive.source_3mf_path && !canModify('archives', 'update', archive.created_by_id),
      title: !archive.source_3mf_path && !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUploadFiles') : undefined,
    },
    ...(archive.source_3mf_path ? [{
      label: t('archives.menu.replaceSource3mf'),
      icon: <Upload className="w-4 h-4" />,
      onClick: () => source3mfInputRef.current?.click(),
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    {
      label: t('archives.menu.removeSource3mf'),
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => setShowDeleteSource3mfConfirm(true),
      danger: true,
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    }] : []),
    {
      label: archive.f3d_path ? t('archives.menu.replaceF3d') : t('archives.menu.uploadF3d'),
      icon: <Box className="w-4 h-4" />,
      onClick: () => f3dInputRef.current?.click(),
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    ...(archive.f3d_path ? [{
      label: t('archives.menu.downloadF3d'),
      icon: <Download className="w-4 h-4" />,
      onClick: () => {
        api.downloadF3d(archive.id).catch((err) => {
          console.error('F3D download failed:', err);
        });
      },
    },
    {
      label: t('archives.menu.removeF3d'),
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => setShowDeleteF3dConfirm(true),
      danger: true,
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    }] : []),
    { label: '', divider: true, onClick: () => {} },
    {
      label: t('archives.menu.download'),
      icon: <Download className="w-4 h-4" />,
      onClick: () => {
        api.downloadArchive(archive.id, `${archive.print_name || archive.filename}.3mf`).catch((err) => {
          console.error('Archive download failed:', err);
        });
      },
      disabled: !hasPermission('archives:read'),
      title: !hasPermission('archives:read') ? t('archives.permission.noDownload') : undefined,
    },
    {
      label: t('archives.menu.copyDownloadLink'),
      icon: <Copy className="w-4 h-4" />,
      onClick: () => {
        const url = `${window.location.origin}${api.getArchiveDownload(archive.id)}`;
        navigator.clipboard.writeText(url).then(() => {
          showToast(t('archives.toast.linkCopied'));
        }).catch(() => {
          showToast(t('archives.toast.failedCopyLink'), 'error');
        });
      },
      disabled: !hasPermission('archives:read'),
      title: !hasPermission('archives:read') ? t('archives.permission.noCopyLink') : undefined,
    },
    {
      label: t('archives.menu.qrCode'),
      icon: <QrCode className="w-4 h-4" />,
      onClick: () => setShowQRCode(true),
    },
    {
      label: archive.photos?.length ? t('archives.menu.viewPhotosCount', { count: archive.photos.length }) : t('archives.menu.viewPhotos'),
      icon: <Camera className="w-4 h-4" />,
      onClick: () => setShowPhotos(true),
      disabled: !archive.photos?.length,
    },
    {
      label: t('archives.menu.projectPage'),
      icon: <FileText className="w-4 h-4" />,
      onClick: () => setShowProjectPage(true),
    },
    { label: '', divider: true, onClick: () => {} },
    {
      label: archive.is_favorite ? t('archives.menu.removeFromFavorites') : t('archives.menu.addToFavorites'),
      icon: <Star className={`w-4 h-4 ${archive.is_favorite ? 'fill-yellow-400 text-yellow-400' : ''}`} />,
      onClick: () => favoriteMutation.mutate(),
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    {
      label: t('archives.menu.edit'),
      icon: <Pencil className="w-4 h-4" />,
      onClick: () => setShowEdit(true),
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    ...(archive.project_id && archive.project_name ? [{
      label: t('archives.menu.goToProject', { name: archive.project_name }),
      icon: <FolderKanban className="w-4 h-4 text-bambu-green" />,
      onClick: () => window.location.href = '/projects',
    }] : []),
    {
      label: t('archives.menu.addToProject'),
      icon: <FolderKanban className="w-4 h-4" />,
      onClick: () => {},
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
      submenu: (() => {
        const items: ContextMenuItem[] = [];

        // Add "Remove from Project" if archive is in a project
        if (archive.project_id) {
          items.push({
            label: t('archives.menu.removeFromProject'),
            icon: <X className="w-4 h-4" />,
            onClick: () => assignProjectMutation.mutate(null),
            disabled: !canModify('archives', 'update', archive.created_by_id),
          });
        }

        // Add project options
        if (!projects) {
          items.push({
            label: t('archives.menu.loading'),
            icon: <Loader2 className="w-4 h-4 animate-spin" />,
            onClick: () => {},
            disabled: true,
          });
        } else {
          const activeProjects = projects.filter(p => p.status === 'active');
          if (activeProjects.length === 0) {
            items.push({
              label: t('archives.menu.noProjectsAvailable'),
              icon: <FolderKanban className="w-4 h-4 opacity-50" />,
              onClick: () => {},
              disabled: true,
            });
          } else {
            activeProjects.forEach(p => {
              items.push({
                label: p.name,
                icon: <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: p.color || '#888' }} />,
                onClick: () => assignProjectMutation.mutate(p.id),
                disabled: archive.project_id === p.id || !canModify('archives', 'update', archive.created_by_id),
              });
            });
          }
        }

        return items;
      })(),
    },
    {
      label: isSelected ? t('archives.menu.deselect') : t('archives.menu.select'),
      icon: isSelected ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />,
      onClick: () => onSelect(archive.id),
    },
    { label: '', divider: true, onClick: () => {} },
    {
      label: t('archives.menu.delete'),
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => setShowDeleteConfirm(true),
      danger: true,
      disabled: !canModify('archives', 'delete', archive.created_by_id),
      title: !canModify('archives', 'delete', archive.created_by_id) ? t('archives.permission.noDelete') : undefined,
    },
  ];

  return (
    <Card
      data-archive-id={archive.id}
      className={`relative flex flex-col group ${isSelected ? 'ring-2 ring-bambu-green' : ''} ${selectionMode ? 'cursor-pointer' : ''}`}
      style={isHighlighted ? { outline: '4px solid #facc15', outlineOffset: '2px' } : undefined}
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

      {/* Thumbnail with plate navigation */}
      <div
        className="aspect-video bg-bambu-dark relative flex-shrink-0 overflow-hidden rounded-t-xl"
        onMouseEnter={() => setShowPlateNav(true)}
        onMouseLeave={() => setShowPlateNav(false)}
      >
        {archive.thumbnail_path ? (
          <img
            src={
              currentPlateIndex !== null && plates.length > 0
                ? api.getArchivePlateThumbnail(archive.id, plates[displayPlateIndex]?.index ?? 0)
                : api.getArchiveThumbnail(archive.id)
            }
            alt={archive.print_name || archive.filename}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Image className="w-12 h-12 text-bambu-dark-tertiary" />
          </div>
        )}
        {/* Plate navigation - only show for multi-plate archives */}
        {isMultiPlate && plates.length > 1 && (
          <>
            {/* Left arrow */}
            <button
              className={`absolute left-1 top-1/2 -translate-y-1/2 p-1 rounded-full bg-black/60 hover:bg-black/80 transition-all ${
                isMobile ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
              }`}
              onClick={(e) => {
                e.stopPropagation();
                setCurrentPlateIndex((prev) => {
                  const current = prev ?? 0;
                  return current > 0 ? current - 1 : plates.length - 1;
                });
              }}
              title={t('archives.card.previousPlate')}
            >
              <ChevronLeft className="w-4 h-4 text-white" />
            </button>
            {/* Right arrow */}
            <button
              className={`absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded-full bg-black/60 hover:bg-black/80 transition-all ${
                isMobile ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
              }`}
              onClick={(e) => {
                e.stopPropagation();
                setCurrentPlateIndex((prev) => {
                  const current = prev ?? 0;
                  return current < plates.length - 1 ? current + 1 : 0;
                });
              }}
              title={t('archives.card.nextPlate')}
            >
              <ChevronRight className="w-4 h-4 text-white" />
            </button>
            {/* Dots indicator */}
            <div
              className={`absolute bottom-1 left-1/2 -translate-x-1/2 flex gap-1 px-2 py-1 rounded-full bg-black/50 transition-all ${
                isMobile ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
              }`}
            >
              {plates.map((plate, idx) => (
                <button
                  key={plate.index}
                  className={`w-2 h-2 rounded-full transition-colors ${
                    idx === displayPlateIndex ? 'bg-bambu-green' : 'bg-white/50 hover:bg-white/80'
                  }`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setCurrentPlateIndex(idx);
                  }}
                  title={plate.name || t('archives.card.plateNumber', { index: plate.index })}
                />
              ))}
            </div>
          </>
        )}
        {/* Context menu button - visible on mobile, shows on hover for desktop */}
        <button
          className={`absolute top-2 left-2 p-1.5 rounded bg-black/50 hover:bg-black/70 transition-all ${
            isMobile ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
          } ${selectionMode ? 'left-10' : ''}`}
          onClick={(e) => {
            e.stopPropagation();
            const rect = e.currentTarget.getBoundingClientRect();
            setContextMenu({ x: rect.left, y: rect.bottom + 4 });
          }}
          title={t('archives.card.moreOptions')}
        >
          <MoreVertical className="w-5 h-5 text-white" />
        </button>
        {/* Favorite star */}
        <button
          className={`absolute top-2 right-2 p-1 rounded transition-colors ${
            canModify('archives', 'update', archive.created_by_id)
              ? 'bg-black/50 hover:bg-black/70'
              : 'bg-black/30 cursor-not-allowed'
          }`}
          onClick={(e) => {
            e.stopPropagation();
            if (canModify('archives', 'update', archive.created_by_id)) {
              favoriteMutation.mutate();
            }
          }}
          disabled={!canModify('archives', 'update', archive.created_by_id)}
          title={!canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : (archive.is_favorite ? t('archives.card.removeFromFavorites') : t('archives.card.addToFavorites'))}
        >
          <Star
            className={`w-5 h-5 ${archive.is_favorite ? 'text-yellow-400 fill-yellow-400' : 'text-white'} ${!canModify('archives', 'update', archive.created_by_id) ? 'opacity-50' : ''}`}
          />
        </button>
        {(archive.status === 'failed' || archive.status === 'aborted') && (
          <div className="absolute top-2 left-12 px-2 py-1 rounded text-xs bg-status-error/80 text-white">
            {archive.status === 'aborted' ? t('archives.card.cancelled') : t('archives.card.failed')}
          </div>
        )}
        {/* Duplicate badge */}
        {archive.duplicate_count > 0 && (
          <div
            className="absolute top-2 right-12 px-2 py-1 rounded text-xs bg-purple-500/80 text-white flex items-center gap-1"
            title={t('archives.card.duplicateTitle')}
          >
            <Copy className="w-3 h-3" />
            {t('archives.card.duplicate')}
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
              openInSlicerWithToken(archive.id, sourceName, 'source', preferredSlicer);
            }}
            title={t('archives.card.openSource3mf')}
          >
            <FileCode className="w-4 h-4 text-orange-400" />
          </button>
        )}
        {/* F3D badge */}
        {archive.f3d_path && (
          <button
            className={`absolute bottom-2 ${archive.source_3mf_path ? 'left-12' : 'left-2'} p-1.5 rounded bg-black/60 hover:bg-black/80 transition-colors`}
            onClick={(e) => {
              e.stopPropagation();
              // Download F3D file
              api.downloadF3d(archive.id).catch((err) => {
                console.error('F3D download failed:', err);
              });
            }}
            title={t('archives.card.downloadF3d')}
          >
            <Box className="w-4 h-4 text-cyan-400" />
          </button>
        )}
        {/* 3D preview badge */}
        <button
          className="absolute bottom-2 right-2 p-1.5 rounded bg-black/60 hover:bg-black/80 transition-colors"
          onClick={(e) => {
            e.stopPropagation();
            setShowViewer(true);
          }}
          title={t('archives.card.preview3d')}
        >
          <Layers className="w-4 h-4 text-white" />
        </button>
        {/* Timelapse badge */}
        {archive.timelapse_path && (
          <button
            className="absolute bottom-2 right-12 p-1.5 rounded bg-black/60 hover:bg-black/80 transition-colors"
            onClick={(e) => {
              e.stopPropagation();
              setShowTimelapse(true);
            }}
            title={t('archives.card.viewTimelapse')}
          >
            <Film className="w-4 h-4 text-bambu-green" />
          </button>
        )}
        {/* Photos badge */}
        {archive.photos && archive.photos.length > 0 && (
          <button
            className={`absolute bottom-2 ${archive.timelapse_path ? 'right-[5.5rem]' : 'right-12'} p-1.5 rounded bg-black/60 hover:bg-black/80 transition-colors`}
            onClick={(e) => {
              e.stopPropagation();
              setShowPhotos(true);
            }}
            title={archive.photos.length === 1 ? t('archives.card.viewPhoto') : t('archives.card.viewPhotos', { count: archive.photos.length })}
          >
            <Camera className="w-4 h-4 text-blue-400" />
            {archive.photos.length > 1 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 bg-blue-500 rounded-full text-[10px] text-white flex items-center justify-center">
                {archive.photos.length}
              </span>
            )}
          </button>
        )}
        {/* Linked folder badge */}
        {linkedFolders && linkedFolders.length > 0 && (
          <Link
            to={`/files?folder=${linkedFolders[0].id}`}
            className="absolute bottom-2 p-1.5 rounded bg-black/60 hover:bg-black/80 transition-colors"
            onClick={(e) => e.stopPropagation()}
            title={t('archives.card.openFolder', { name: linkedFolders[0].name })}
            style={{ left: archive.source_3mf_path ? (archive.f3d_path ? '5.5rem' : '3rem') : (archive.f3d_path ? '3rem' : '0.5rem') }}
          >
            <FolderOpen className="w-4 h-4 text-yellow-400" />
          </Link>
        )}
      </div>

      <CardContent className="p-4 flex-1 flex flex-col">
        {/* Title */}
        <div className="flex items-center justify-between gap-2 mb-1">
          <h3 className="min-w-0 font-medium text-white truncate">
            {archive.print_name || archive.filename}
          </h3>
          <Button
            variant="ghost"
            size="sm"
            className="p-1 sm:p-1.5 shrink-0"
            onClick={() => setShowEdit(true)}
            disabled={!canModify('archives', 'update', archive.created_by_id)}
            title={!canModify('archives', 'update', archive.created_by_id) ? t('archives.card.noPermissionEdit') : t('archives.card.edit')}
          >
            <Pencil className="w-3 h-3 sm:w-4 sm:h-4" />
          </Button>
        </div>
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <p className="text-xs text-bambu-gray">{printerName}</p>
          {/* File type badge */}
          <span
            className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
              isSlicedFile(archive.filename)
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'bg-orange-500/20 text-orange-400'
            }`}
            title={
              isSlicedFile(archive.filename)
                ? t('archives.card.slicedFile')
                : t('archives.card.sourceFile')
            }
          >
            {isSlicedFile(archive.filename) ? t('archives.card.gcode') : t('archives.card.source')}
          </span>
          {archive.project_name && (
            <span
              className="text-xs px-1.5 py-0.5 rounded-full truncate max-w-[120px]"
              style={{
                backgroundColor: `${projects?.find(p => p.id === archive.project_id)?.color || '#6b7280'}20`,
                color: projects?.find(p => p.id === archive.project_id)?.color || '#6b7280'
              }}
              title={t('archives.card.project', { name: archive.project_name })}
            >
              {archive.project_name}
            </span>
          )}
        </div>

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
              {archive.total_layers && <span>{archive.total_layers === 1 ? t('archives.card.layer', { count: archive.total_layers }) : t('archives.card.layers', { count: archive.total_layers })}</span>}
              {archive.total_layers && archive.layer_height && <span className="text-bambu-gray/50">·</span>}
              {archive.layer_height && <span>{archive.layer_height}mm</span>}
            </div>
          )}
          {archive.object_count != null && archive.object_count > 0 && (
            <div className="flex items-center gap-1.5 text-bambu-gray" title={archive.object_count === 1 ? t('archives.card.object', { count: archive.object_count }) : t('archives.card.objects', { count: archive.object_count })}>
              <Box className="w-3 h-3" />
              {archive.object_count === 1 ? t('archives.card.object', { count: archive.object_count }) : t('archives.card.objects', { count: archive.object_count })}
            </div>
          )}
          {archive.sliced_for_model && (
            <div className="flex items-center gap-1.5 text-bambu-gray" title={t('archives.card.slicedFor', { model: archive.sliced_for_model })}>
              <Printer className="w-3 h-3" />
              {archive.sliced_for_model}
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

        {/* Date, Size & Creator */}
        <div className="flex items-center justify-between text-xs text-bambu-gray border-t border-bambu-dark-tertiary pt-3">
          <span>{formatDateTime(archive.created_at, timeFormat)}</span>
          <div className="flex items-center gap-2">
            {archive.created_by_username && (
              <span className="flex items-center gap-1" title={t('archives.card.uploadedBy', { name: archive.created_by_username })}>
                <User className="w-3 h-3" />
                {archive.created_by_username}
              </span>
            )}
            <span>{formatFileSize(archive.file_size)}</span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-1 mt-3">
          {isSlicedFile(archive.filename) ? (
            // Sliced file - can print directly
            <>
              <Button
                variant="primary"
                size="sm"
                className="flex-1 min-w-0"
                onClick={() => setShowReprint(true)}
                disabled={!archive.file_path || !canModify('archives', 'reprint', archive.created_by_id)}
                title={!archive.file_path ? t('archives.card.noFileForReprint') : !canModify('archives', 'reprint', archive.created_by_id) ? t('archives.card.noPermissionReprint') : undefined}
              >
                <Printer className="w-3 h-3 flex-shrink-0" />
                <span className="hidden sm:inline">{t('archives.card.reprint')}</span>
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="flex-1 min-w-0"
                onClick={() => setShowSchedule(true)}
                disabled={!archive.file_path || !hasPermission('queue:create')}
                title={!archive.file_path ? t('archives.card.noFileForReprint') : !hasPermission('queue:create') ? t('archives.permission.noAddToQueue') : t('archives.card.schedulePrint')}
              >
                <Calendar className="w-3 h-3 flex-shrink-0" />
                <span className="hidden sm:inline">{t('archives.card.schedule')}</span>
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="min-w-0 p-1 sm:p-1.5"
                onClick={() => {
                  const filename = archive.print_name || archive.filename || 'model';
                  openInSlicerWithToken(archive.id, filename, 'file', preferredSlicer);
                }}
                title={t('archives.card.openInBambuStudio')}
              >
                <ExternalLink className="w-3 h-3 sm:w-4 sm:h-4" />
              </Button>
            </>
          ) : (
            // Source file only - must open in slicer first
            <Button
              variant="primary"
              size="sm"
              className="flex-1 min-w-0"
              onClick={() => {
                const filename = archive.print_name || archive.filename || 'model';
                openInSlicerWithToken(archive.id, filename, 'file', preferredSlicer);
              }}
              title={t('archives.card.openInBambuStudioToSlice')}
            >
              <ExternalLink className="w-3 h-3 flex-shrink-0" />
              <span className="hidden sm:inline">{t('archives.card.slice')}</span>
            </Button>
          )}
          <Button
            variant="secondary"
            size="sm"
            className="min-w-0 p-1 sm:p-1.5"
            onClick={() => {
              const url = archive.external_url || archive.makerworld_url;
              if (url) window.open(url, '_blank');
            }}
            disabled={!archive.external_url && !archive.makerworld_url}
            title={
              archive.external_url
                ? t('archives.card.externalLink')
                : archive.makerworld_url
                  ? t('archives.card.makerWorld', { designer: archive.designer || t('archives.card.viewProject') })
                  : t('archives.card.noExternalLink')
            }
          >
            <Globe className={`w-3 h-3 sm:w-4 sm:h-4 ${!archive.external_url && !archive.makerworld_url ? 'opacity-20' : ''}`} />
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="min-w-0 p-1 sm:p-1.5"
            onClick={() => {
              api.downloadArchive(archive.id, `${archive.print_name || archive.filename}.3mf`).catch((err) => {
                console.error('Archive download failed:', err);
              });
            }}
            title={t('archives.card.download')}
          >
            <Download className="w-3 h-3 sm:w-4 sm:h-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="min-w-0 p-1 sm:p-1.5"
            onClick={() => setShowDeleteConfirm(true)}
            disabled={!canModify('archives', 'delete', archive.created_by_id)}
            title={!canModify('archives', 'delete', archive.created_by_id) ? t('archives.card.noPermissionDelete') : t('archives.card.delete')}
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
          fileType={getArchiveFileType(archive.filename)}
          onClose={() => setShowViewer(false)}
        />
      )}

      {/* Reprint Modal */}
      {showReprint && (
        <PrintModal
          mode="reprint"
          archiveId={archive.id}
          archiveName={archive.print_name || archive.filename}
          onClose={() => setShowReprint(false)}
        />
      )}

      {/* Delete Confirmation */}
      {showDeleteConfirm && (
        <ConfirmModal
          title={t('archives.modal.deleteArchive')}
          message={t('archives.modal.deleteConfirm', { name: archive.print_name || archive.filename })}
          confirmText={t('archives.modal.deleteButton')}
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
          title={t('archives.modal.removeSource3mf')}
          message={t('archives.modal.removeSource3mfConfirm', { name: archive.print_name || archive.filename })}
          confirmText={t('archives.modal.removeButton')}
          variant="danger"
          onConfirm={() => {
            source3mfDeleteMutation.mutate();
            setShowDeleteSource3mfConfirm(false);
          }}
          onCancel={() => setShowDeleteSource3mfConfirm(false)}
        />
      )}

      {/* Delete F3D Confirmation */}
      {showDeleteF3dConfirm && (
        <ConfirmModal
          title={t('archives.modal.removeF3d')}
          message={t('archives.modal.removeF3dConfirm', { name: archive.print_name || archive.filename })}
          confirmText={t('archives.modal.removeButton')}
          variant="danger"
          onConfirm={() => {
            f3dDeleteMutation.mutate();
            setShowDeleteF3dConfirm(false);
          }}
          onCancel={() => setShowDeleteF3dConfirm(false)}
        />
      )}

      {/* Delete Timelapse Confirmation */}
      {showDeleteTimelapseConfirm && (
        <ConfirmModal
          title={t('archives.modal.removeTimelapse')}
          message={t('archives.modal.removeTimelapseConfirm', { name: archive.print_name || archive.filename })}
          confirmText={t('archives.modal.removeButton')}
          variant="danger"
          onConfirm={() => {
            timelapseDeleteMutation.mutate();
            setShowDeleteTimelapseConfirm(false);
          }}
          onCancel={() => setShowDeleteTimelapseConfirm(false)}
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
          title={t('archives.modal.timelapse', { name: archive.print_name || archive.filename })}
          downloadFilename={`${archive.print_name || archive.filename}_timelapse.mp4`}
          archiveId={archive.id}
          onClose={() => setShowTimelapse(false)}
          onEdit={() => {
            queryClient.invalidateQueries({ queryKey: ['archives'] });
            setShowTimelapse(false);  // Close viewer to reload fresh video
          }}
        />
      )}

      {/* Timelapse Selection Modal */}
      {showTimelapseSelect && availableTimelapses.length > 0 && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
          <div className="bg-card-dark rounded-lg max-w-lg w-full max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-gray-700">
              <div>
                <h3 className="text-lg font-semibold text-white">{t('archives.modal.selectTimelapse')}</h3>
                <p className="text-sm text-gray-400 mt-1">
                  {t('archives.modal.selectTimelapseDesc')}
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
                      {file.mtime && ` • ${formatDateTime(file.mtime, timeFormat)}`}
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
              showToast(t('archives.toast.photoDeleted'));
            } catch {
              showToast(t('archives.toast.failedDeletePhoto'), 'error');
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
        <PrintModal
          mode="add-to-queue"
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
      {/* Hidden file input for F3D upload */}
      <input
        ref={f3dInputRef}
        type="file"
        accept=".f3d"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) {
            f3dUploadMutation.mutate(file);
          }
          e.target.value = '';
        }}
      />
      {/* Hidden file input for timelapse upload */}
      <input
        ref={timelapseInputRef}
        type="file"
        accept=".mp4,.avi,.mkv"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) {
            timelapseUploadMutation.mutate(file);
          }
          e.target.value = '';
        }}
      />
    </Card>
  );
}

function ArchiveListRow({
  archive,
  printerName,
  isSelected,
  onSelect,
  selectionMode,
  projects,
  isHighlighted,
  preferredSlicer = 'bambu_studio',
  t,
}: {
  archive: Archive;
  printerName: string;
  isSelected: boolean;
  onSelect: (id: number) => void;
  selectionMode: boolean;
  projects: ProjectListItem[] | undefined;
  isHighlighted?: boolean;
  preferredSlicer?: SlicerType;
  t: TFunction;
}) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission, canModify } = useAuth();
  const [showEdit, setShowEdit] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showReprint, setShowReprint] = useState(false);
  const [showSchedule, setShowSchedule] = useState(false);
  const [showViewer, setShowViewer] = useState(false);
  const [showTimelapse, setShowTimelapse] = useState(false);
  const [showTimelapseSelect, setShowTimelapseSelect] = useState(false);
  const [availableTimelapses, setAvailableTimelapses] = useState<Array<{ name: string; path: string; size: number; mtime: string | null }>>([]);
  const [showQRCode, setShowQRCode] = useState(false);
  const [showPhotos, setShowPhotos] = useState(false);
  const [showProjectPage, setShowProjectPage] = useState(false);
  const [showDeleteSource3mfConfirm, setShowDeleteSource3mfConfirm] = useState(false);
  const [showDeleteF3dConfirm, setShowDeleteF3dConfirm] = useState(false);
  const [showDeleteTimelapseConfirm, setShowDeleteTimelapseConfirm] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const source3mfInputRef = useRef<HTMLInputElement>(null);
  const f3dInputRef = useRef<HTMLInputElement>(null);
  const timelapseInputRef = useRef<HTMLInputElement>(null);

  const timelapseDeleteMutation = useMutation({
    mutationFn: () => api.deleteArchiveTimelapse(archive.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.timelapseRemoved'));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedRemoveTimelapse'), 'error');
    },
  });

  const timelapseUploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadArchiveTimelapse(archive.id, file),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.timelapseUploaded', { filename: data.filename }));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedUploadTimelapse'), 'error');
    },
  });

  const source3mfUploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadSource3mf(archive.id, file),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.source3mfAttached', { filename: data.filename }));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedUploadSource3mf'), 'error');
    },
  });

  const source3mfDeleteMutation = useMutation({
    mutationFn: () => api.deleteSource3mf(archive.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.source3mfRemoved'));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedRemoveSource3mf'), 'error');
    },
  });

  const f3dUploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadF3d(archive.id, file),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.f3dAttached', { filename: data.filename }));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedUploadF3d'), 'error');
    },
  });

  const f3dDeleteMutation = useMutation({
    mutationFn: () => api.deleteF3d(archive.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.f3dRemoved'));
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedRemoveF3d'), 'error');
    },
  });

  const timelapseScanMutation = useMutation({
    mutationFn: () => api.scanArchiveTimelapse(archive.id),
    onSuccess: (data) => {
      if (data.status === 'attached') {
        queryClient.invalidateQueries({ queryKey: ['archives'] });
        showToast(t('archives.toast.timelapseAttached', { filename: data.filename }));
      } else if (data.status === 'exists') {
        showToast(t('archives.toast.timelapseAlreadyAttached'));
      } else if (data.status === 'not_found' && data.available_files && data.available_files.length > 0) {
        setAvailableTimelapses(data.available_files);
        setShowTimelapseSelect(true);
      } else {
        showToast(data.message || t('archives.toast.noMatchingTimelapse'), 'warning');
      }
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedScanTimelapse'), 'error');
    },
  });

  const timelapseSelectMutation = useMutation({
    mutationFn: (filename: string) => api.selectArchiveTimelapse(archive.id, filename),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.timelapseAttached', { filename: data.filename }));
      setShowTimelapseSelect(false);
      setAvailableTimelapses([]);
    },
    onError: (error: Error) => {
      showToast(error.message || t('archives.toast.failedAttachTimelapse'), 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteArchive(archive.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('archives.toast.archiveDeleted'));
    },
    onError: () => {
      showToast(t('archives.toast.failedDeleteArchive'), 'error');
    },
  });

  const favoriteMutation = useMutation({
    mutationFn: () => api.toggleFavorite(archive.id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(data.is_favorite ? t('archives.toast.addedToFavorites') : t('archives.toast.removedFromFavorites'));
    },
  });

  // Query for linked folders
  const { data: linkedFolders } = useQuery({
    queryKey: ['archive-folders', archive.id],
    queryFn: () => api.getLibraryFoldersByArchive(archive.id),
  });

  const assignProjectMutation = useMutation({
    mutationFn: (projectId: number | null) => api.updateArchive(archive.id, { project_id: projectId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      showToast(t('archives.toast.projectUpdated'));
    },
    onError: () => {
      showToast(t('archives.toast.failedUpdateProject'), 'error');
    },
  });

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY });
  };

  const isGcodeFile = isSlicedFile(archive.filename);

  const contextMenuItems: ContextMenuItem[] = [
    ...(isGcodeFile ? [
      {
        label: t('archives.menu.print'),
        icon: <Printer className="w-4 h-4" />,
        onClick: () => setShowReprint(true),
        disabled: !archive.file_path || !canModify('archives', 'reprint', archive.created_by_id),
        title: !archive.file_path ? t('archives.card.noFileForReprint') : !canModify('archives', 'reprint', archive.created_by_id) ? t('archives.permission.noReprint') : undefined,
      },
      {
        label: t('archives.menu.schedule'),
        icon: <Calendar className="w-4 h-4" />,
        onClick: () => setShowSchedule(true),
        disabled: !archive.file_path || !hasPermission('queue:create'),
        title: !archive.file_path ? t('archives.card.noFileForReprint') : !hasPermission('queue:create') ? t('archives.permission.noAddToQueue') : undefined,
      },
      {
        label: t('archives.menu.openInBambuStudio'),
        icon: <ExternalLink className="w-4 h-4" />,
        onClick: () => {
          const filename = archive.print_name || archive.filename || 'model';
          openInSlicerWithToken(archive.id, filename, 'file', preferredSlicer);
        },
        disabled: !archive.file_path,
        title: !archive.file_path ? t('archives.card.noFileForReprint') : undefined,
      },
    ] : [
      {
        label: t('archives.menu.slice'),
        icon: <ExternalLink className="w-4 h-4" />,
        onClick: () => {
          const filename = archive.print_name || archive.filename || 'model';
          openInSlicerWithToken(archive.id, filename, 'file', preferredSlicer);
        },
      },
    ]),
    {
      label: archive.external_url ? t('archives.menu.externalLink') : t('archives.menu.viewOnMakerWorld'),
      icon: <Globe className="w-4 h-4" />,
      onClick: () => {
        const url = archive.external_url || archive.makerworld_url;
        if (url) window.open(url, '_blank');
      },
      disabled: !archive.external_url && !archive.makerworld_url,
    },
    { label: '', divider: true, onClick: () => {} },
    {
      label: t('archives.menu.preview3d'),
      icon: <Box className="w-4 h-4" />,
      onClick: () => setShowViewer(true),
    },
    {
      label: t('archives.menu.viewTimelapse'),
      icon: <Film className="w-4 h-4" />,
      onClick: () => setShowTimelapse(true),
      disabled: !archive.timelapse_path,
    },
    {
      label: t('archives.menu.scanForTimelapse'),
      icon: <ScanSearch className="w-4 h-4" />,
      onClick: () => timelapseScanMutation.mutate(),
      disabled: !archive.printer_id || !!archive.timelapse_path || timelapseScanMutation.isPending || !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    {
      label: t('archives.menu.uploadTimelapse'),
      icon: <Upload className="w-4 h-4" />,
      onClick: () => timelapseInputRef.current?.click(),
      disabled: !!archive.timelapse_path || !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    ...(archive.timelapse_path ? [{
      label: t('archives.menu.removeTimelapse'),
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => setShowDeleteTimelapseConfirm(true),
      danger: true,
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    }] : []),
    { label: '', divider: true, onClick: () => {} },
    {
      label: archive.source_3mf_path ? t('archives.menu.downloadSource3mf') : t('archives.menu.uploadSource3mf'),
      icon: <FileCode className="w-4 h-4" />,
      onClick: () => {
        if (archive.source_3mf_path) {
          api.downloadSource3mf(archive.id).catch((err) => {
            console.error('Source 3MF download failed:', err);
          });
        } else {
          source3mfInputRef.current?.click();
        }
      },
      disabled: !archive.source_3mf_path && !canModify('archives', 'update', archive.created_by_id),
      title: !archive.source_3mf_path && !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUploadFiles') : undefined,
    },
    ...(archive.source_3mf_path ? [{
      label: t('archives.menu.replaceSource3mf'),
      icon: <Upload className="w-4 h-4" />,
      onClick: () => source3mfInputRef.current?.click(),
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    {
      label: t('archives.menu.removeSource3mf'),
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => setShowDeleteSource3mfConfirm(true),
      danger: true,
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    }] : []),
    {
      label: archive.f3d_path ? t('archives.menu.replaceF3d') : t('archives.menu.uploadF3d'),
      icon: <Box className="w-4 h-4" />,
      onClick: () => f3dInputRef.current?.click(),
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    ...(archive.f3d_path ? [{
      label: t('archives.menu.downloadF3d'),
      icon: <Download className="w-4 h-4" />,
      onClick: () => {
        api.downloadF3d(archive.id).catch((err) => {
          console.error('F3D download failed:', err);
        });
      },
    },
    {
      label: t('archives.menu.removeF3d'),
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => setShowDeleteF3dConfirm(true),
      danger: true,
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    }] : []),
    { label: '', divider: true, onClick: () => {} },
    {
      label: t('archives.menu.download'),
      icon: <Download className="w-4 h-4" />,
      onClick: () => {
        api.downloadArchive(archive.id, `${archive.print_name || archive.filename}.3mf`).catch((err) => {
          console.error('Archive download failed:', err);
        });
      },
      disabled: !hasPermission('archives:read'),
      title: !hasPermission('archives:read') ? t('archives.permission.noDownload') : undefined,
    },
    {
      label: t('archives.menu.copyDownloadLink'),
      icon: <Copy className="w-4 h-4" />,
      onClick: () => {
        const url = `${window.location.origin}${api.getArchiveDownload(archive.id)}`;
        navigator.clipboard.writeText(url).then(() => {
          showToast(t('archives.toast.linkCopied'));
        }).catch(() => {
          showToast(t('archives.toast.failedCopyLink'), 'error');
        });
      },
      disabled: !hasPermission('archives:read'),
      title: !hasPermission('archives:read') ? t('archives.permission.noCopyLink') : undefined,
    },
    {
      label: t('archives.menu.qrCode'),
      icon: <QrCode className="w-4 h-4" />,
      onClick: () => setShowQRCode(true),
    },
    {
      label: archive.photos?.length ? t('archives.menu.viewPhotosCount', { count: archive.photos.length }) : t('archives.menu.viewPhotos'),
      icon: <Camera className="w-4 h-4" />,
      onClick: () => setShowPhotos(true),
      disabled: !archive.photos?.length,
    },
    {
      label: t('archives.menu.projectPage'),
      icon: <FileText className="w-4 h-4" />,
      onClick: () => setShowProjectPage(true),
    },
    { label: '', divider: true, onClick: () => {} },
    {
      label: archive.is_favorite ? t('archives.menu.removeFromFavorites') : t('archives.menu.addToFavorites'),
      icon: <Star className={`w-4 h-4 ${archive.is_favorite ? 'fill-yellow-400 text-yellow-400' : ''}`} />,
      onClick: () => favoriteMutation.mutate(),
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    {
      label: t('archives.menu.edit'),
      icon: <Pencil className="w-4 h-4" />,
      onClick: () => setShowEdit(true),
      disabled: !canModify('archives', 'update', archive.created_by_id),
      title: !canModify('archives', 'update', archive.created_by_id) ? t('archives.permission.noUpdateArchives') : undefined,
    },
    ...(archive.project_id && archive.project_name ? [{
      label: t('archives.menu.goToProject', { name: archive.project_name }),
      icon: <FolderKanban className="w-4 h-4 text-bambu-green" />,
      onClick: () => window.location.href = '/projects',
    }] : []),
    {
      label: t('archives.menu.addToProject'),
      icon: <FolderKanban className="w-4 h-4" />,
      onClick: () => {},
      submenu: (() => {
        const items: ContextMenuItem[] = [];
        if (archive.project_id) {
          items.push({
            label: t('archives.menu.removeFromProject'),
            icon: <X className="w-4 h-4" />,
            onClick: () => assignProjectMutation.mutate(null),
          });
        }
        if (!projects) {
          items.push({
            label: t('archives.menu.loading'),
            icon: <Loader2 className="w-4 h-4 animate-spin" />,
            onClick: () => {},
            disabled: true,
          });
        } else {
          const activeProjects = projects.filter(p => p.status === 'active');
          if (activeProjects.length === 0) {
            items.push({
              label: t('archives.menu.noProjectsAvailable'),
              icon: <FolderKanban className="w-4 h-4 opacity-50" />,
              onClick: () => {},
              disabled: true,
            });
          } else {
            activeProjects.forEach(p => {
              items.push({
                label: p.name,
                icon: <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: p.color || '#888' }} />,
                onClick: () => assignProjectMutation.mutate(p.id),
                disabled: archive.project_id === p.id,
              });
            });
          }
        }
        return items;
      })(),
    },
    {
      label: isSelected ? t('archives.menu.deselect') : t('archives.menu.select'),
      icon: isSelected ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />,
      onClick: () => onSelect(archive.id),
    },
    { label: '', divider: true, onClick: () => {} },
    {
      label: t('archives.menu.delete'),
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => setShowDeleteConfirm(true),
      danger: true,
      disabled: !canModify('archives', 'delete', archive.created_by_id),
      title: !canModify('archives', 'delete', archive.created_by_id) ? t('archives.permission.noDelete') : undefined,
    },
  ];

  return (
    <>
      <div
        data-archive-id={archive.id}
        className={`grid grid-cols-12 gap-4 px-4 py-3 items-center hover:bg-bambu-dark-tertiary/30 ${
          isSelected ? 'bg-bambu-green/10' : ''
        }`}
        style={isHighlighted ? { outline: '4px solid #facc15', outlineOffset: '-4px' } : undefined}
        onContextMenu={handleContextMenu}
      >
        <div className="col-span-1 flex items-center gap-2">
          {selectionMode && (
            <button onClick={() => onSelect(archive.id)}>
              {isSelected ? (
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
            {(archive.status === 'failed' || archive.status === 'aborted') && (
              <span className="px-1.5 py-0.5 rounded text-[10px] leading-tight bg-status-error/80 text-white flex-shrink-0">
                {archive.status === 'aborted' ? t('archives.card.cancelled') : t('archives.card.failed')}
              </span>
            )}
            {archive.timelapse_path && (
              <span title={t('archives.list.hasTimelapse')}>
                <Film className="w-3.5 h-3.5 text-bambu-green flex-shrink-0" />
              </span>
            )}
            {linkedFolders && linkedFolders.length > 0 && (
              <Link
                to={`/files?folder=${linkedFolders[0].id}`}
                className="flex-shrink-0"
                title={t('archives.card.openFolder', { name: linkedFolders[0].name })}
                onClick={(e) => e.stopPropagation()}
              >
                <FolderOpen className="w-3.5 h-3.5 text-yellow-400" />
              </Link>
            )}
          </div>
          {(archive.filament_type || archive.sliced_for_model) && (
            <div className="flex items-center gap-1.5 mt-0.5">
              {archive.sliced_for_model && (
                <span className="text-xs text-bambu-gray flex items-center gap-1" title={t('archives.card.slicedFor', { model: archive.sliced_for_model })}>
                  <Printer className="w-2.5 h-2.5" />
                  {archive.sliced_for_model}
                </span>
              )}
              {archive.sliced_for_model && archive.filament_type && (
                <span className="text-bambu-gray/50">·</span>
              )}
              {archive.filament_type && (
                <span className="text-xs text-bambu-gray">{archive.filament_type}</span>
              )}
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
          {printerName}
        </div>
        <div className="col-span-2 text-sm text-bambu-gray">
          <div>{formatDateOnly(archive.created_at)}</div>
          {archive.created_by_username && (
            <div className="flex items-center gap-1 text-xs opacity-75" title={t('archives.card.uploadedBy', { name: archive.created_by_username })}>
              <User className="w-3 h-3" />
              {archive.created_by_username}
            </div>
          )}
        </div>
        <div className="col-span-1 text-sm text-bambu-gray">
          {formatFileSize(archive.file_size)}
        </div>
        <div className="col-span-2 flex justify-end gap-1">
          {isSlicedFile(archive.filename) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowReprint(true)}
              disabled={!canModify('archives', 'reprint', archive.created_by_id)}
              title={!canModify('archives', 'reprint', archive.created_by_id) ? t('archives.card.noPermissionReprint') : t('archives.card.reprint')}
              className="text-bambu-green hover:text-bambu-green-light hover:bg-bambu-green/10"
            >
              <Play className="w-4 h-4" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              const filename = archive.print_name || archive.filename || 'model';
              openInSlicerWithToken(archive.id, filename, 'file', preferredSlicer);
            }}
            title={t('archives.card.openInBambuStudio')}
          >
            <ExternalLink className="w-4 h-4" />
          </Button>
          {(archive.external_url || archive.makerworld_url) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => window.open((archive.external_url || archive.makerworld_url)!, '_blank')}
              title={archive.external_url ? t('archives.card.externalLink') : 'MakerWorld'}
            >
              <Globe className="w-4 h-4" />
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              api.downloadArchive(archive.id, `${archive.print_name || archive.filename}.3mf`).catch((err) => {
                console.error('Archive download failed:', err);
              });
            }}
            title={t('archives.card.download')}
          >
            <Download className="w-4 h-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowEdit(true)}
            disabled={!canModify('archives', 'update', archive.created_by_id)}
            title={!canModify('archives', 'update', archive.created_by_id) ? t('archives.card.noPermissionEdit') : t('archives.card.edit')}
          >
            <Pencil className="w-4 h-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowDeleteConfirm(true)}
            disabled={!canModify('archives', 'delete', archive.created_by_id)}
            title={!canModify('archives', 'delete', archive.created_by_id) ? t('archives.card.noPermissionDelete') : t('archives.card.delete')}
          >
            <Trash2 className="w-4 h-4 text-red-400" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              setContextMenu({ x: rect.left, y: rect.bottom + 4 });
            }}
            title={t('archives.card.moreOptions')}
          >
            <MoreVertical className="w-4 h-4" />
          </Button>
        </div>
      </div>

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
          fileType={getArchiveFileType(archive.filename)}
          onClose={() => setShowViewer(false)}
        />
      )}

      {/* Reprint Modal */}
      {showReprint && (
        <PrintModal
          mode="reprint"
          archiveId={archive.id}
          archiveName={archive.print_name || archive.filename}
          onClose={() => setShowReprint(false)}
        />
      )}

      {/* Delete Confirmation */}
      {showDeleteConfirm && (
        <ConfirmModal
          title={t('archives.modal.deleteArchive')}
          message={t('archives.modal.deleteConfirm', { name: archive.print_name || archive.filename })}
          confirmText={t('archives.modal.deleteButton')}
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
          title={t('archives.modal.removeSource3mf')}
          message={t('archives.modal.removeSource3mfConfirm', { name: archive.print_name || archive.filename })}
          confirmText={t('archives.modal.removeButton')}
          variant="danger"
          onConfirm={() => {
            source3mfDeleteMutation.mutate();
            setShowDeleteSource3mfConfirm(false);
          }}
          onCancel={() => setShowDeleteSource3mfConfirm(false)}
        />
      )}

      {/* Delete F3D Confirmation */}
      {showDeleteF3dConfirm && (
        <ConfirmModal
          title={t('archives.modal.removeF3d')}
          message={t('archives.modal.removeF3dConfirm', { name: archive.print_name || archive.filename })}
          confirmText={t('archives.modal.removeButton')}
          variant="danger"
          onConfirm={() => {
            f3dDeleteMutation.mutate();
            setShowDeleteF3dConfirm(false);
          }}
          onCancel={() => setShowDeleteF3dConfirm(false)}
        />
      )}

      {/* Delete Timelapse Confirmation */}
      {showDeleteTimelapseConfirm && (
        <ConfirmModal
          title={t('archives.modal.removeTimelapse')}
          message={t('archives.modal.removeTimelapseConfirm', { name: archive.print_name || archive.filename })}
          confirmText={t('archives.modal.removeButton')}
          variant="danger"
          onConfirm={() => {
            timelapseDeleteMutation.mutate();
            setShowDeleteTimelapseConfirm(false);
          }}
          onCancel={() => setShowDeleteTimelapseConfirm(false)}
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
          title={t('archives.modal.timelapse', { name: archive.print_name || archive.filename })}
          downloadFilename={`${archive.print_name || archive.filename}_timelapse.mp4`}
          archiveId={archive.id}
          onClose={() => setShowTimelapse(false)}
          onEdit={() => {
            queryClient.invalidateQueries({ queryKey: ['archives'] });
            setShowTimelapse(false);
          }}
        />
      )}

      {/* Timelapse Selection Modal */}
      {showTimelapseSelect && availableTimelapses.length > 0 && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
          <div className="bg-card-dark rounded-lg max-w-lg w-full max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-gray-700">
              <div>
                <h3 className="text-lg font-semibold text-white">{t('archives.modal.selectTimelapse')}</h3>
                <p className="text-sm text-gray-400 mt-1">
                  {t('archives.modal.selectTimelapseDesc')}
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
                  className="w-full text-left p-3 rounded-lg hover:bg-gray-700 transition-colors mb-1"
                >
                  <div className="font-medium text-white">{file.name}</div>
                  <div className="text-sm text-gray-400 flex gap-3">
                    <span>{formatFileSize(file.size)}</span>
                    {file.mtime && (
                      <span>{formatDateOnly(file.mtime)}</span>
                    )}
                  </div>
                </button>
              ))}
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
      {showPhotos && archive.photos && (
        <PhotoGalleryModal
          archiveId={archive.id}
          archiveName={archive.print_name || archive.filename}
          photos={archive.photos}
          onClose={() => setShowPhotos(false)}
          onDelete={async (filename) => {
            try {
              await api.deleteArchivePhoto(archive.id, filename);
              queryClient.invalidateQueries({ queryKey: ['archives'] });
              showToast(t('archives.toast.photoDeleted'));
            } catch {
              showToast(t('archives.toast.failedDeletePhoto'), 'error');
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

      {/* Schedule Modal */}
      {showSchedule && (
        <PrintModal
          mode="add-to-queue"
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
      {/* Hidden file input for F3D upload */}
      <input
        ref={f3dInputRef}
        type="file"
        accept=".f3d"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) {
            f3dUploadMutation.mutate(file);
          }
          e.target.value = '';
        }}
      />
      {/* Hidden file input for timelapse upload */}
      <input
        ref={timelapseInputRef}
        type="file"
        accept=".mp4,.avi,.mkv"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) {
            timelapseUploadMutation.mutate(file);
          }
          e.target.value = '';
        }}
      />
    </>
  );
}

type SortOption = 'date-desc' | 'date-asc' | 'name-asc' | 'name-desc' | 'size-desc' | 'size-asc';
type ViewMode = 'grid' | 'list' | 'calendar' | 'log';
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
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission, hasAnyPermission } = useAuth();
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState('');
  const [filterPrinter, setFilterPrinter] = useState<number | null>(() => {
    const saved = localStorage.getItem('archiveFilterPrinter');
    return saved ? Number(saved) : null;
  });
  const [filterMaterial, setFilterMaterial] = useState<string | null>(() =>
    localStorage.getItem('archiveFilterMaterial')
  );
  const [filterColors, setFilterColors] = useState<Set<string>>(() => {
    const saved = localStorage.getItem('archiveFilterColors');
    return saved ? new Set(JSON.parse(saved)) : new Set();
  });
  const [colorFilterMode, setColorFilterMode] = useState<'or' | 'and'>(() =>
    (localStorage.getItem('archiveColorFilterMode') as 'or' | 'and') || 'or'
  );
  const [filterFavorites, setFilterFavorites] = useState(() =>
    localStorage.getItem('archiveFilterFavorites') === 'true'
  );
  const [hideFailed, setHideFailed] = useState(() =>
    localStorage.getItem('archiveHideFailed') === 'true'
  );
  const [filterTag, setFilterTag] = useState<string | null>(() =>
    localStorage.getItem('archiveFilterTag')
  );
  const [filterFileType, setFilterFileType] = useState<'all' | 'gcode' | 'source'>(() =>
    (localStorage.getItem('archiveFilterFileType') as 'all' | 'gcode' | 'source') || 'all'
  );
  const [showUpload, setShowUpload] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [isSelectionMode, setIsSelectionMode] = useState(false);
  const [showBulkDeleteConfirm, setShowBulkDeleteConfirm] = useState(false);
  const [showBatchTag, setShowBatchTag] = useState(false);
  const [showBatchProject, setShowBatchProject] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>(() =>
    (localStorage.getItem('archiveViewMode') as ViewMode) || 'grid'
  );
  const [sortBy, setSortBy] = useState<SortOption>(() =>
    (localStorage.getItem('archiveSortBy') as SortOption) || 'date-desc'
  );
  const [collection, setCollection] = useState<Collection>(() =>
    (localStorage.getItem('archiveCollection') as Collection) || 'all'
  );
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [showCompareModal, setShowCompareModal] = useState(false);
  const [showTagManagement, setShowTagManagement] = useState(false);
  const [highlightedArchiveId, setHighlightedArchiveId] = useState<number | null>(null);

  // Log view state
  const [logFilterUser, setLogFilterUser] = useState<string | null>(() =>
    localStorage.getItem('logFilterUser') || null
  );
  const [logFilterStatus, setLogFilterStatus] = useState<string | null>(() =>
    localStorage.getItem('logFilterStatus')
  );
  const [logFilterDateFrom, setLogFilterDateFrom] = useState(() =>
    localStorage.getItem('logFilterDateFrom') || ''
  );
  const [logFilterDateTo, setLogFilterDateTo] = useState(() =>
    localStorage.getItem('logFilterDateTo') || ''
  );
  const [logOffset, setLogOffset] = useState(() => {
    const saved = localStorage.getItem('logOffset');
    return saved ? Number(saved) : 0;
  });
  const [showClearLogConfirm, setShowClearLogConfirm] = useState(false);
  const [logPageSize, setLogPageSize] = useState(() => {
    const saved = localStorage.getItem('logPageSize');
    return saved ? Number(saved) : 25;
  });

  // Clear highlight after 5 seconds and scroll to highlighted element
  useEffect(() => {
    if (highlightedArchiveId) {
      // Scroll to highlighted element after a short delay (to let the view render)
      const scrollTimer = setTimeout(() => {
        const element = document.querySelector(`[data-archive-id="${highlightedArchiveId}"]`);
        if (element) {
          element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }, 100);

      // Clear highlight after 5 seconds
      const clearTimer = setTimeout(() => setHighlightedArchiveId(null), 5000);
      return () => {
        clearTimeout(scrollTimer);
        clearTimeout(clearTimer);
      };
    }
  }, [highlightedArchiveId]);

  const { data: archives, isLoading } = useQuery({
    queryKey: ['archives', filterPrinter],
    queryFn: () => api.getArchives(filterPrinter || undefined),
  });

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const { data: users } = useQuery({
    queryKey: ['users'],
    queryFn: api.getUsers,
    enabled: viewMode === 'log',
  });

  const { data: printLogData, isLoading: isLogLoading } = useQuery({
    queryKey: ['print-log', filterPrinter, logFilterUser, logFilterStatus, logFilterDateFrom, logFilterDateTo, search, logOffset, logPageSize],
    queryFn: () => api.getPrintLog({
      search: search || undefined,
      printerId: filterPrinter || undefined,
      username: logFilterUser || undefined,
      status: logFilterStatus || undefined,
      dateFrom: logFilterDateFrom || undefined,
      dateTo: logFilterDateTo || undefined,
      limit: logPageSize,
      offset: logOffset,
    }),
    enabled: viewMode === 'log',
  });

  const timeFormat: TimeFormat = settings?.time_format || 'system';
  const preferredSlicer: SlicerType = settings?.preferred_slicer || 'bambu_studio';

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
      showToast(t('archives.toast.failedDeleteArchives'), 'error');
    },
  });

  const clearLogMutation = useMutation({
    mutationFn: () => api.clearPrintLog(),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['print-log'] });
      setLogOffset(0);
      showToast(t('archives.log.cleared', { count: data.deleted }));
    },
    onError: () => {
      showToast(t('archives.log.clearFailed'), 'error');
    },
  });

  // Persist all filters to localStorage
  useEffect(() => {
    if (filterPrinter !== null) {
      localStorage.setItem('archiveFilterPrinter', filterPrinter.toString());
    } else {
      localStorage.removeItem('archiveFilterPrinter');
    }
  }, [filterPrinter]);

  useEffect(() => {
    if (filterMaterial) {
      localStorage.setItem('archiveFilterMaterial', filterMaterial);
    } else {
      localStorage.removeItem('archiveFilterMaterial');
    }
  }, [filterMaterial]);

  useEffect(() => {
    localStorage.setItem('archiveFilterColors', JSON.stringify([...filterColors]));
  }, [filterColors]);

  useEffect(() => {
    localStorage.setItem('archiveColorFilterMode', colorFilterMode);
  }, [colorFilterMode]);

  useEffect(() => {
    localStorage.setItem('archiveFilterFavorites', filterFavorites.toString());
  }, [filterFavorites]);

  useEffect(() => {
    localStorage.setItem('archiveHideFailed', hideFailed.toString());
  }, [hideFailed]);

  useEffect(() => {
    if (filterTag) {
      localStorage.setItem('archiveFilterTag', filterTag);
    } else {
      localStorage.removeItem('archiveFilterTag');
    }
  }, [filterTag]);

  useEffect(() => {
    localStorage.setItem('archiveFilterFileType', filterFileType);
  }, [filterFileType]);

  useEffect(() => {
    localStorage.setItem('archiveViewMode', viewMode);
  }, [viewMode]);

  useEffect(() => {
    localStorage.setItem('archiveSortBy', sortBy);
  }, [sortBy]);

  useEffect(() => {
    localStorage.setItem('archiveCollection', collection);
  }, [collection]);

  // Persist log view filters
  useEffect(() => {
    if (logFilterUser) {
      localStorage.setItem('logFilterUser', logFilterUser);
    } else {
      localStorage.removeItem('logFilterUser');
    }
  }, [logFilterUser]);

  useEffect(() => {
    if (logFilterStatus) {
      localStorage.setItem('logFilterStatus', logFilterStatus);
    } else {
      localStorage.removeItem('logFilterStatus');
    }
  }, [logFilterStatus]);

  useEffect(() => {
    if (logFilterDateFrom) {
      localStorage.setItem('logFilterDateFrom', logFilterDateFrom);
    } else {
      localStorage.removeItem('logFilterDateFrom');
    }
  }, [logFilterDateFrom]);

  useEffect(() => {
    if (logFilterDateTo) {
      localStorage.setItem('logFilterDateTo', logFilterDateTo);
    } else {
      localStorage.removeItem('logFilterDateTo');
    }
  }, [logFilterDateTo]);

  useEffect(() => {
    localStorage.setItem('logOffset', logOffset.toString());
  }, [logOffset]);

  useEffect(() => {
    localStorage.setItem('logPageSize', logPageSize.toString());
  }, [logPageSize]);

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
      const archiveDate = parseUTCDate(a.created_at) || new Date(0);
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
          matchesCollection = a.status === 'failed' || a.status === 'aborted';
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

      // Hide failed filter (don't apply when viewing failed collection)
      const matchesHideFailed = collection === 'failed' || !hideFailed || (a.status !== 'failed' && a.status !== 'aborted');

      // Tag filter
      const archiveTags = a.tags?.split(',').map(t => t.trim()) || [];
      const matchesTag = !filterTag || archiveTags.includes(filterTag);

      // File type filter (gcode = sliced, source = project file only)
      const isGcodeFile = isSlicedFile(a.filename);
      const matchesFileType = filterFileType === 'all' ||
        (filterFileType === 'gcode' && isGcodeFile) ||
        (filterFileType === 'source' && !isGcodeFile);

      return matchesCollection && matchesSearch && matchesMaterial && matchesColor && matchesFavorites && matchesHideFailed && matchesTag && matchesFileType;
    })
    .sort((a, b) => {
      switch (sortBy) {
        case 'date-desc':
          return (parseUTCDate(b.created_at)?.getTime() || 0) - (parseUTCDate(a.created_at)?.getTime() || 0);
        case 'date-asc':
          return (parseUTCDate(a.created_at)?.getTime() || 0) - (parseUTCDate(b.created_at)?.getTime() || 0);
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
    setHideFailed(false);
    setFilterTag(null);
    setFilterFileType('all');
  };

  const hasTopFilters = search || filterPrinter || filterMaterial || filterFavorites || hideFailed || filterTag || filterFileType !== 'all';

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
      showToast(t('archives.page.only3mfSupported'), 'warning');
    }
  }, [showToast, t]);

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
            <p className="text-bambu-gray">{t('archives.releaseToUpload')}</p>
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
            disabled={!hasAnyPermission('archives:update_own', 'archives:update_all')}
            title={!hasAnyPermission('archives:update_own', 'archives:update_all') ? t('archives.permission.noUpdateArchives') : undefined}
          >
            <Tag className="w-4 h-4" />
            Tags
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowBatchProject(true)}
            disabled={!hasAnyPermission('archives:update_own', 'archives:update_all')}
            title={!hasAnyPermission('archives:update_own', 'archives:update_all') ? t('archives.permission.noUpdateArchives') : undefined}
          >
            <FolderKanban className="w-4 h-4" />
            Project
          </Button>
          <Button
            variant="secondary"
            size="sm"
            disabled={!hasAnyPermission('archives:update_own', 'archives:update_all')}
            title={!hasAnyPermission('archives:update_own', 'archives:update_all') ? t('archives.permission.noUpdateArchives') : undefined}
            onClick={() => {
              const ids = Array.from(selectedIds);
              Promise.all(ids.map(id => api.toggleFavorite(id)))
                .then(() => {
                  queryClient.invalidateQueries({ queryKey: ['archives'] });
                  showToast(`Toggled favorites for ${ids.length} archive${ids.length !== 1 ? 's' : ''}`);
                })
                .catch(() => {
                  showToast(t('archives.toast.failedUpdateFavorites'), 'error');
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
            disabled={!hasAnyPermission('archives:delete_own', 'archives:delete_all')}
            title={!hasAnyPermission('archives:delete_own', 'archives:delete_all') ? t('archives.permission.noDelete') : undefined}
          >
            <Trash2 className="w-4 h-4" />
            Delete
          </Button>
        </div>
      )}

      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
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
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          {/* Export dropdown */}
          <div className="relative">
            <Button
              variant="secondary"
              onClick={() => setShowExportMenu(!showExportMenu)}
              disabled={isExporting}
            >
              {isExporting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <FileSpreadsheet className="w-4 h-4" />
              )}
              Export
            </Button>
            {showExportMenu && (
              <div className="absolute right-0 top-full mt-1 w-48 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl z-20">
                <button
                  className="w-full px-4 py-2 text-left text-white hover:bg-bambu-dark-tertiary transition-colors flex items-center gap-2 rounded-t-lg"
                  onClick={async () => {
                    setShowExportMenu(false);
                    setIsExporting(true);
                    try {
                      const { blob, filename } = await api.exportArchives({
                        format: 'csv',
                        printerId: filterPrinter || undefined,
                        status: collection === 'failed' ? 'failed' : undefined,
                        search: search || undefined,
                      });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = filename;
                      a.click();
                      URL.revokeObjectURL(url);
                      showToast(t('archives.toast.exportDownloaded'));
                    } catch {
                      showToast(t('archives.toast.exportFailed'), 'error');
                    } finally {
                      setIsExporting(false);
                    }
                  }}
                >
                  <FileText className="w-4 h-4" />
                  Export as CSV
                </button>
                <button
                  className="w-full px-4 py-2 text-left text-white hover:bg-bambu-dark-tertiary transition-colors flex items-center gap-2 rounded-b-lg"
                  onClick={async () => {
                    setShowExportMenu(false);
                    setIsExporting(true);
                    try {
                      const { blob, filename } = await api.exportArchives({
                        format: 'xlsx',
                        printerId: filterPrinter || undefined,
                        status: collection === 'failed' ? 'failed' : undefined,
                        search: search || undefined,
                      });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = filename;
                      a.click();
                      URL.revokeObjectURL(url);
                      showToast(t('archives.toast.exportDownloaded'));
                    } catch {
                      showToast(t('archives.toast.exportFailed'), 'error');
                    } finally {
                      setIsExporting(false);
                    }
                  }}
                >
                  <FileSpreadsheet className="w-4 h-4" />
                  Export as Excel
                </button>
              </div>
            )}
          </div>
          {/* Compare button (only when 2-5 items selected) */}
          {selectedIds.size >= 2 && selectedIds.size <= 5 && (
            <Button
              variant="secondary"
              onClick={() => setShowCompareModal(true)}
            >
              <GitCompare className="w-4 h-4" />
              Compare ({selectedIds.size})
            </Button>
          )}
          {!selectionMode && (
            <Button variant="secondary" onClick={() => setIsSelectionMode(true)}>
              <CheckSquare className="w-4 h-4" />
              Select
            </Button>
          )}
          <Button
            onClick={() => setShowUpload(true)}
            disabled={!hasPermission('archives:create')}
            title={!hasPermission('archives:create') ? t('archives.permission.noCreate') : undefined}
          >
            <Upload className="w-4 h-4" />
            Upload 3MF
          </Button>
        </div>
      </div>

      {/* View mode toggle — always visible */}
      <div className="flex items-center border border-bambu-dark-tertiary rounded-lg overflow-hidden flex-shrink-0 w-fit mb-4">
        <button
          className={`p-2 ${viewMode === 'grid' ? 'bg-bambu-green text-white' : 'bg-bambu-dark text-bambu-gray hover:text-white'}`}
          onClick={() => setViewMode('grid')}
          title={t('archives.gridView')}
        >
          <LayoutGrid className="w-4 h-4" />
        </button>
        <button
          className={`p-2 ${viewMode === 'list' ? 'bg-bambu-green text-white' : 'bg-bambu-dark text-bambu-gray hover:text-white'}`}
          onClick={() => setViewMode('list')}
          title={t('archives.listView')}
        >
          <List className="w-4 h-4" />
        </button>
        <button
          className={`p-2 ${viewMode === 'calendar' ? 'bg-bambu-green text-white' : 'bg-bambu-dark text-bambu-gray hover:text-white'}`}
          onClick={() => setViewMode('calendar')}
          title={t('archives.calendarView')}
        >
          <CalendarDays className="w-4 h-4" />
        </button>
        <button
          className={`p-2 ${viewMode === 'log' ? 'bg-bambu-green text-white' : 'bg-bambu-dark text-bambu-gray hover:text-white'}`}
          onClick={() => setViewMode('log')}
          title={t('archives.logView')}
        >
          <ClipboardList className="w-4 h-4" />
        </button>
      </div>

      {/* Filters (hidden in log view which has its own filters) */}
      {viewMode !== 'log' && <Card className="mb-6">
        <CardContent className="py-4">
          <div className="flex flex-col md:flex-row gap-3 md:gap-4 md:items-center md:flex-wrap">
            {/* Search - full width on mobile */}
            <div className="w-full md:flex-1 relative md:min-w-[200px]">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
              <input
                ref={searchInputRef}
                type="text"
                placeholder={t('archives.searchPlaceholder')}
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
            <div className="flex items-center gap-2 flex-shrink-0">
              <FileCode className="w-4 h-4 text-bambu-gray hidden md:block" />
              <select
                className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={filterFileType}
                onChange={(e) => setFilterFileType(e.target.value as 'all' | 'gcode' | 'source')}
              >
                <option value="all">All Files</option>
                <option value="gcode">Sliced (GCODE)</option>
                <option value="source">Source Only</option>
              </select>
            </div>
            <button
              onClick={() => setFilterFavorites(!filterFavorites)}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors flex-shrink-0 ${
                filterFavorites
                  ? 'bg-yellow-500/20 border-yellow-500 text-yellow-400'
                  : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
              }`}
              title={filterFavorites ? t('archives.showAll') : t('archives.showFavoritesOnly')}
            >
              <Star className={`w-4 h-4 ${filterFavorites ? 'fill-yellow-400' : ''}`} />
              <span className="text-sm hidden md:inline">Favorites</span>
            </button>
            <button
              onClick={() => setHideFailed(!hideFailed)}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors flex-shrink-0 ${
                hideFailed
                  ? 'bg-red-500/20 border-red-500 text-red-400'
                  : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
              }`}
              title={hideFailed ? t('archives.showFailedPrints') : t('archives.hideFailedPrints')}
            >
              <AlertCircle className={`w-4 h-4 ${hideFailed ? '' : ''}`} />
              <span className="text-sm hidden md:inline">Hide Failed</span>
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
                <button
                  onClick={() => setShowTagManagement(true)}
                  className="p-2 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-green transition-colors"
                  title={t('archives.manageTags')}
                >
                  <Settings className="w-4 h-4" />
                </button>
              </div>
            )}
            <div className="flex items-center gap-2 flex-shrink-0">
              <ArrowUpDown className="w-4 h-4 text-bambu-gray hidden md:block" />
              <select
                className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as SortOption)}
              >
                <option value="date-desc">{t('archives.sortNewest')}</option>
                <option value="date-asc">{t('archives.sortOldest')}</option>
                <option value="name-asc">{t('archives.sortName')} A-Z</option>
                <option value="name-desc">{t('archives.sortName')} Z-A</option>
                <option value="size-desc">{t('archives.sortLargest')}</option>
                <option value="size-asc">{t('archives.sortSmallest')}</option>
              </select>
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
      </Card>}

      {/* Pending Uploads Panel (visible when in queue mode with pending files) */}
      <PendingUploadsPanel />

      {/* Archives */}
      {isLoading ? (
        <div className="text-center py-12 text-bambu-gray">{t('archives.loadingArchives')}</div>
      ) : filteredArchives?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-bambu-gray">
              {search ? t('archives.noArchivesSearch') : t('archives.noArchivesYet')}
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
              // Switch to grid view and highlight the archive
              setSearch(''); // Clear search to show all archives
              setViewMode('grid');
              setHighlightedArchiveId(archive.id);
            }}
            highlightedArchiveId={highlightedArchiveId}
          />
        </Card>
      ) : viewMode === 'grid' ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {filteredArchives?.map((archive) => (
            <ArchiveCard
              key={archive.id}
              archive={archive}
              printerName={archive.printer_id ? printerMap.get(archive.printer_id) || 'Unknown' : (archive.sliced_for_model ? `Sliced for ${archive.sliced_for_model}` : 'No Printer')}
              isSelected={selectedIds.has(archive.id)}
              onSelect={toggleSelect}
              selectionMode={selectionMode}
              projects={projects}
              isHighlighted={archive.id === highlightedArchiveId}
              timeFormat={timeFormat}
              preferredSlicer={preferredSlicer}
              t={t}
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
              <ArchiveListRow
                key={archive.id}
                archive={archive}
                printerName={archive.printer_id ? printerMap.get(archive.printer_id) || 'Unknown' : (archive.sliced_for_model ? `Sliced for ${archive.sliced_for_model}` : 'No Printer')}
                isSelected={selectedIds.has(archive.id)}
                onSelect={toggleSelect}
                selectionMode={selectionMode}
                projects={projects}
                isHighlighted={archive.id === highlightedArchiveId}
                preferredSlicer={preferredSlicer}
                t={t}
              />
            ))}
          </div>
        </Card>
      ) : viewMode === 'log' ? (
        <div className="space-y-4">
          {/* Log filters */}
          <Card>
            <CardContent className="py-3">
              <div className="flex flex-col md:flex-row gap-3 md:items-center md:flex-wrap">
                {/* Search */}
                <div className="flex-1 relative md:min-w-[200px]">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
                  <input
                    type="text"
                    placeholder={t('archives.searchPlaceholder')}
                    className="w-full pl-10 pr-4 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none text-sm"
                    value={search}
                    onChange={(e) => { setSearch(e.target.value); setLogOffset(0); }}
                  />
                </div>
                {/* Printer filter */}
                <select
                  className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none text-sm"
                  value={filterPrinter || ''}
                  onChange={(e) => { setFilterPrinter(e.target.value ? Number(e.target.value) : null); setLogOffset(0); }}
                >
                  <option value="">{t('archives.log.allPrinters')}</option>
                  {printers?.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
                {/* User filter */}
                <select
                  className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none text-sm"
                  value={logFilterUser || ''}
                  onChange={(e) => { setLogFilterUser(e.target.value || null); setLogOffset(0); }}
                >
                  <option value="">{t('archives.log.allUsers')}</option>
                  {users?.map((u) => (
                    <option key={u.id} value={u.username}>{u.username}</option>
                  ))}
                </select>
                {/* Status filter */}
                <select
                  className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none text-sm"
                  value={logFilterStatus || ''}
                  onChange={(e) => { setLogFilterStatus(e.target.value || null); setLogOffset(0); }}
                >
                  <option value="">{t('archives.log.allStatuses')}</option>
                  <option value="completed">{t('archives.status.completed')}</option>
                  <option value="failed">{t('archives.status.failed')}</option>
                  <option value="stopped">{t('archives.status.stopped')}</option>
                  <option value="cancelled">{t('archives.log.cancelled')}</option>
                  <option value="skipped">{t('archives.log.skipped')}</option>
                </select>
                {/* Date range */}
                <div className="flex items-center gap-2">
                  <label className="text-sm text-bambu-gray">{t('archives.log.dateFrom')}</label>
                  <input
                    type="date"
                    className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none text-sm"
                    value={logFilterDateFrom}
                    onChange={(e) => { setLogFilterDateFrom(e.target.value); setLogOffset(0); }}
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-sm text-bambu-gray">{t('archives.log.dateTo')}</label>
                  <input
                    type="date"
                    className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none text-sm"
                    value={logFilterDateTo}
                    onChange={(e) => { setLogFilterDateTo(e.target.value); setLogOffset(0); }}
                  />
                </div>
                {/* Clear log button */}
                <div className="ml-auto">
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => setShowClearLogConfirm(true)}
                    disabled={!hasPermission('archives:delete_all') || clearLogMutation.isPending}
                  >
                    <Trash2 className="w-4 h-4" />
                    {t('archives.log.clearLog')}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Log table */}
          <Card>
            {isLogLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
              </div>
            ) : !printLogData?.items.length ? (
              <div className="text-center py-12 text-bambu-gray">
                {t('archives.log.noEntries')}
              </div>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-bambu-dark-tertiary text-bambu-gray text-left">
                        <th className="px-4 py-3 font-medium">{t('archives.log.date')}</th>
                        <th className="px-4 py-3 font-medium">{t('archives.log.printName')}</th>
                        <th className="px-4 py-3 font-medium">{t('archives.log.printer')}</th>
                        <th className="px-4 py-3 font-medium">{t('archives.log.user')}</th>
                        <th className="px-4 py-3 font-medium">{t('archives.log.status')}</th>
                        <th className="px-4 py-3 font-medium">{t('archives.log.duration')}</th>
                        <th className="px-4 py-3 font-medium">{t('archives.log.filament')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-bambu-dark-tertiary">
                      {printLogData.items.map((entry) => (
                        <tr key={entry.id} className="hover:bg-bambu-dark-secondary/50">
                          <td className="px-4 py-3 text-white whitespace-nowrap">
                            {formatDateTime(entry.started_at || entry.created_at, timeFormat)}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              {entry.thumbnail_path && (
                                <img
                                  src={api.getPrintLogThumbnail(entry.id)}
                                  alt=""
                                  className="w-8 h-8 rounded object-cover flex-shrink-0"
                                  onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                                />
                              )}
                              <span className="text-white truncate max-w-[200px]">
                                {entry.print_name || '—'}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-3 text-bambu-gray-light">{entry.printer_name || '—'}</td>
                          <td className="px-4 py-3 text-bambu-gray-light">{entry.created_by_username || '—'}</td>
                          <td className="px-4 py-3">
                            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                              entry.status === 'completed' ? 'bg-green-500/20 text-green-400' :
                              entry.status === 'failed' ? 'bg-red-500/20 text-red-400' :
                              entry.status === 'stopped' ? 'bg-yellow-500/20 text-yellow-400' :
                              entry.status === 'cancelled' ? 'bg-orange-500/20 text-orange-400' :
                              entry.status === 'skipped' ? 'bg-blue-500/20 text-blue-400' :
                              'bg-gray-500/20 text-gray-400'
                            }`}>
                              {entry.status}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-bambu-gray-light whitespace-nowrap">
                            {entry.duration_seconds ? formatDuration(entry.duration_seconds) : '—'}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-1.5">
                              {entry.filament_color && (
                                <span
                                  className="w-3 h-3 rounded-full border border-white/20 flex-shrink-0"
                                  style={{ backgroundColor: entry.filament_color.startsWith('#') ? entry.filament_color : undefined }}
                                />
                              )}
                              <span className="text-bambu-gray-light text-xs">
                                {entry.filament_type || '—'}
                              </span>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {/* Pagination */}
                <div className="flex items-center justify-between px-4 py-3 border-t border-bambu-dark-tertiary flex-wrap gap-2">
                  <div className="flex items-center gap-3">
                    <span className="text-sm text-bambu-gray">
                      {t('archives.log.showing', { count: Math.min(logOffset + logPageSize, printLogData.total), total: printLogData.total })}
                    </span>
                    <div className="flex items-center gap-1.5">
                      <label className="text-xs text-bambu-gray">{t('archives.log.rowsPerPage')}</label>
                      <select
                        className="px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-xs focus:border-bambu-green focus:outline-none"
                        value={logPageSize}
                        onChange={(e) => { setLogPageSize(Number(e.target.value)); setLogOffset(0); }}
                      >
                        <option value={10}>10</option>
                        <option value={25}>25</option>
                        <option value={50}>50</option>
                        <option value={100}>100</option>
                      </select>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-bambu-gray">
                      {t('archives.log.page')} {Math.floor(logOffset / logPageSize) + 1} / {Math.max(1, Math.ceil(printLogData.total / logPageSize))}
                    </span>
                    <Button variant="secondary" size="sm" onClick={() => setLogOffset(Math.max(0, logOffset - logPageSize))} disabled={logOffset === 0}>
                      <ChevronLeft className="w-4 h-4" />
                    </Button>
                    <Button variant="secondary" size="sm" onClick={() => setLogOffset(logOffset + logPageSize)} disabled={logOffset + logPageSize >= printLogData.total}>
                      <ChevronRight className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </>
            )}
          </Card>

        </div>
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
          title={t('archives.modal.deleteArchives')}
          message={t('archives.modal.deleteArchivesConfirm', { count: selectedIds.size })}
          confirmText={t('archives.modal.deleteCount', { count: selectedIds.size })}
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

      {/* Batch Project Modal */}
      {showBatchProject && (
        <BatchProjectModal
          selectedIds={Array.from(selectedIds)}
          onClose={() => setShowBatchProject(false)}
        />
      )}

      {/* Compare Archives Modal */}
      {showCompareModal && selectedIds.size >= 2 && selectedIds.size <= 5 && (
        <CompareArchivesModal
          archiveIds={Array.from(selectedIds)}
          onClose={() => {
            setShowCompareModal(false);
            setSelectedIds(new Set());
            setIsSelectionMode(false);
          }}
        />
      )}

      {/* Tag Management Modal */}
      {showTagManagement && (
        <TagManagementModal onClose={() => setShowTagManagement(false)} />
      )}

      {/* Clear Log Confirmation */}
      {showClearLogConfirm && (
        <ConfirmModal
          title={t('archives.log.clearLogTitle')}
          message={t('archives.log.clearLogConfirm')}
          confirmText={t('archives.log.clearLogButton')}
          variant="danger"
          onConfirm={() => {
            clearLogMutation.mutate();
            setShowClearLogConfirm(false);
          }}
          onCancel={() => setShowClearLogConfirm(false)}
        />
      )}
    </div>
  );
}
