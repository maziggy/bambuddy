import { useState, useMemo, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import type { DragEndEvent } from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  Clock,
  Trash2,
  Play,
  X,
  CheckCircle,
  XCircle,
  AlertCircle,
  Calendar,
  Printer,
  GripVertical,
  SkipForward,
  ExternalLink,
  Power,
  StopCircle,
  Pencil,
  RefreshCw,
  Timer,
  ListOrdered,
  Layers,
  ArrowUp,
  ArrowDown,
  Hand,
  CheckSquare,
  Square,
  User,
} from 'lucide-react';
import { api } from '../api/client';
import { type TimeFormat } from '../utils/date';
import { formatRelativeTime } from '../utils/relativeTime';
import type { PrintQueueItem, Permission } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { PrintModal } from '../components/PrintModal';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return '--';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function StatusBadge({ status }: { status: PrintQueueItem['status'] }) {
  const { t } = useTranslation();
  const config = {
    pending: { icon: Clock, color: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20', label: t('queue.status.pending') },
    printing: { icon: Play, color: 'text-blue-400 bg-blue-400/10 border-blue-400/20', label: t('queue.status.printing') },
    completed: { icon: CheckCircle, color: 'text-green-400 bg-green-400/10 border-green-400/20', label: t('queue.status.completed') },
    failed: { icon: XCircle, color: 'text-red-400 bg-red-400/10 border-red-400/20', label: t('queue.status.failed') },
    skipped: { icon: SkipForward, color: 'text-orange-400 bg-orange-400/10 border-orange-400/20', label: t('queue.status.skipped') },
    cancelled: { icon: X, color: 'text-gray-400 bg-gray-400/10 border-gray-400/20', label: t('queue.status.cancelled') },
  };

  const { icon: Icon, color, label } = config[status];

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${color}`}>
      <Icon className="w-3.5 h-3.5" />
      {label}
    </span>
  );
}

// Sortable queue item for drag and drop
function SortableQueueItem({
  item,
  position,
  onEdit,
  onCancel,
  onRemove,
  onStop,
  onRequeue,
  onStart,
  timeFormat = 'system',
  isSelected = false,
  onToggleSelect,
  hasPermission,
  canModify,
}: {
  item: PrintQueueItem;
  position?: number;
  onEdit: () => void;
  onCancel: () => void;
  onRemove: () => void;
  onStop: () => void;
  onRequeue: () => void;
  onStart: () => void;
  timeFormat?: TimeFormat;
  isSelected?: boolean;
  onToggleSelect?: () => void;
  hasPermission: (permission: Permission) => boolean;
  canModify: (resource: 'queue' | 'archives' | 'library', action: 'update' | 'delete' | 'reprint', createdById: number | null | undefined) => boolean;
}) {
  const { t } = useTranslation();
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id, disabled: item.status !== 'pending' });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const isPrinting = item.status === 'printing';
  const isPending = item.status === 'pending';
  const isHistory = ['completed', 'failed', 'skipped', 'cancelled'].includes(item.status);

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`
        group relative bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary
        transition-all duration-200 hover:border-bambu-dark-tertiary/80
        ${isDragging ? 'opacity-50 scale-[1.02] shadow-xl z-50' : ''}
        ${isPrinting ? 'border-blue-500/30 bg-gradient-to-r from-blue-500/5 to-transparent' : ''}
        ${isSelected ? 'border-bambu-green/50 bg-bambu-green/5' : ''}
      `}
    >
      <div className="flex items-center gap-4 p-4">
        {/* Selection checkbox for pending items */}
        {isPending && onToggleSelect && (
          <button
            type="button"
            onClick={onToggleSelect}
            className="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
          >
            {isSelected ? (
              <CheckSquare className="w-4 h-4 text-bambu-green" />
            ) : (
              <Square className="w-4 h-4 text-bambu-gray" />
            )}
          </button>
        )}

        {/* Drag handle or position number */}
        {isPending ? (
          <div
            {...attributes}
            {...listeners}
            className="flex items-center justify-center w-10 h-10 md:w-8 md:h-8 rounded-lg bg-bambu-dark cursor-grab active:cursor-grabbing hover:bg-bambu-dark-tertiary transition-colors touch-manipulation"
          >
            <GripVertical className="w-6 h-6 md:w-4 md:h-4 text-bambu-gray" />
          </div>
        ) : position !== undefined ? (
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-bambu-dark text-bambu-gray text-sm font-medium">
            #{position}
          </div>
        ) : (
          <div className="w-8" />
        )}

        {/* Thumbnail */}
        <div className="w-14 h-14 flex-shrink-0 bg-bambu-dark rounded-lg overflow-hidden">
          {item.archive_thumbnail ? (
            <img
              src={api.getArchiveThumbnail(item.archive_id!)}
              alt=""
              className="w-full h-full object-cover"
            />
          ) : item.library_file_thumbnail ? (
            <img
              src={api.getLibraryFileThumbnailUrl(item.library_file_id!)}
              alt=""
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-bambu-gray">
              <Layers className="w-6 h-6" />
            </div>
          )}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <p className="text-white font-medium truncate">
              {item.archive_name || item.library_file_name || `File #${item.archive_id || item.library_file_id}`}
            </p>
            {item.archive_id ? (
              <Link
                to={`/archives?highlight=${item.archive_id}`}
                className="text-bambu-gray hover:text-bambu-green transition-colors flex-shrink-0"
                title={t('queue.viewArchive')}
              >
                <ExternalLink className="w-3.5 h-3.5" />
              </Link>
            ) : item.library_file_id ? (
              <Link
                to={`/library?highlight=${item.library_file_id}`}
                className="text-bambu-gray hover:text-bambu-green transition-colors flex-shrink-0"
                title={t('queue.viewInFileManager')}
              >
                <ExternalLink className="w-3.5 h-3.5" />
              </Link>
            ) : null}
          </div>

          <div className="flex items-center gap-3 text-sm text-bambu-gray">
            <span className={`flex items-center gap-1.5 ${item.printer_id === null ? 'text-orange-400' : ''}`}>
              <Printer className="w-3.5 h-3.5" />
              {item.target_model
                ? t('queue.anyModel', { model: item.target_model, location: item.target_location ? ` @ ${item.target_location}` : '', filaments: item.required_filament_types?.length ? ` (${item.required_filament_types.join(', ')})` : '' })
                : item.printer_id === null
                  ? t('queue.unassigned')
                  : (item.printer_name || t('queue.printerNumber', { id: item.printer_id }))}
            </span>
            {item.print_time_seconds && (
              <span className="flex items-center gap-1.5">
                <Timer className="w-3.5 h-3.5" />
                {formatDuration(item.print_time_seconds)}
              </span>
            )}
            {item.created_by_username && (
              <span className="flex items-center gap-1.5" title={t('queue.addedBy', { username: item.created_by_username })}>
                <User className="w-3.5 h-3.5" />
                {item.created_by_username}
              </span>
            )}
            {isPending && !item.manual_start && (
              <span className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                {formatRelativeTime(item.scheduled_time, { t, timeFormat })}
              </span>
            )}
          </div>

          {/* Options badges */}
          <div className="flex items-center gap-2 mt-2">
            {item.manual_start && (
              <span className="text-xs px-2 py-0.5 bg-purple-500/10 text-purple-400 rounded-full border border-purple-500/20 flex items-center gap-1">
                <Hand className="w-3 h-3" />
                {t('queue.staged')}
              </span>
            )}
            {item.require_previous_success && (
              <span className="text-xs px-2 py-0.5 bg-orange-500/10 text-orange-400 rounded-full border border-orange-500/20">
                {t('queue.requiresPreviousSuccess')}
              </span>
            )}
            {item.auto_off_after && (
              <span className="text-xs px-2 py-0.5 bg-blue-500/10 text-blue-400 rounded-full border border-blue-500/20 flex items-center gap-1">
                <Power className="w-3 h-3" />
                {t('queue.autoPowerOff')}
              </span>
            )}
          </div>

          {/* Progress bar for printing items - TODO: integrate with WebSocket */}
          {isPrinting && (
            <div className="mt-3">
              <div className="h-2 bg-bambu-dark rounded-full overflow-hidden">
                <div className="h-full bg-gradient-to-r from-blue-500 to-blue-400 animate-pulse w-full opacity-50" />
              </div>
              <p className="text-xs text-bambu-gray mt-1">{t('queue.printingInProgress')}</p>
            </div>
          )}

          {/* Error message */}
          {item.error_message && (
            <p className="text-xs text-red-400 mt-2 flex items-center gap-1">
              <AlertCircle className="w-3 h-3" />
              {item.error_message}
            </p>
          )}
        </div>

        {/* Status badge */}
        <StatusBadge status={item.status} />

        {/* Actions */}
        <div className="flex items-center gap-1">
          {isPrinting && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onStop}
              disabled={!hasPermission('printers:control')}
              title={!hasPermission('printers:control') ? t('queue.permissions.noStopPermission') : t('queue.stopPrint')}
              className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
            >
              <StopCircle className="w-4 h-4" />
            </Button>
          )}
          {isPending && (
            <>
              {item.manual_start && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onStart}
                  disabled={!hasPermission('printers:control')}
                  title={!hasPermission('printers:control') ? t('queue.permissions.noStartPermission') : t('queue.startPrint')}
                  className="text-bambu-green hover:text-bambu-green-light hover:bg-bambu-green/10"
                >
                  <Play className="w-4 h-4" />
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={onEdit}
                disabled={!canModify('queue', 'update', item.created_by_id)}
                title={!canModify('queue', 'update', item.created_by_id) ? t('queue.permissions.noEditPermission') : t('common.edit')}
              >
                <Pencil className="w-4 h-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={onCancel}
                disabled={!canModify('queue', 'delete', item.created_by_id)}
                title={!canModify('queue', 'delete', item.created_by_id) ? t('queue.permissions.noCancelPermission') : t('common.cancel')}
                className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
              >
                <X className="w-4 h-4" />
              </Button>
            </>
          )}
          {isHistory && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={onRequeue}
                disabled={!hasPermission('queue:create')}
                title={!hasPermission('queue:create') ? t('queue.permissions.noRequeuePermission') : t('queue.requeue')}
                className="text-bambu-green hover:text-bambu-green/80 hover:bg-bambu-green/10"
              >
                <RefreshCw className="w-4 h-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={onRemove}
                disabled={!canModify('queue', 'delete', item.created_by_id)}
                title={!canModify('queue', 'delete', item.created_by_id) ? t('queue.permissions.noRemovePermission') : t('queue.remove')}
              >
                <Trash2 className="w-4 h-4" />
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function QueuePage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission, hasAnyPermission, canModify } = useAuth();
  const [filterPrinter, setFilterPrinter] = useState<number | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [filterLocation, setFilterLocation] = useState<string>('');
  const [showClearHistoryConfirm, setShowClearHistoryConfirm] = useState(false);
  const [editItem, setEditItem] = useState<PrintQueueItem | null>(null);
  const [requeueItem, setRequeueItem] = useState<PrintQueueItem | null>(null);
  const [confirmAction, setConfirmAction] = useState<{
    type: 'cancel' | 'remove' | 'stop';
    item: PrintQueueItem;
  } | null>(null);
  const [historySortBy, setHistorySortBy] = useState<'date' | 'name' | 'printer'>(() => {
    const saved = localStorage.getItem('queue.historySortBy');
    return (saved as 'date' | 'name' | 'printer') || 'date';
  });
  const [historySortAsc, setHistorySortAsc] = useState(() => {
    const saved = localStorage.getItem('queue.historySortAsc');
    return saved !== null ? saved === 'true' : false;
  });
  const [pendingSortBy, setPendingSortBy] = useState<'position' | 'name' | 'printer' | 'time'>(() => {
    const saved = localStorage.getItem('queue.pendingSortBy');
    return (saved as 'position' | 'name' | 'printer' | 'time') || 'position';
  });
  const [pendingSortAsc, setPendingSortAsc] = useState(() => {
    const saved = localStorage.getItem('queue.pendingSortAsc');
    return saved !== null ? saved === 'true' : true;
  });
  const [selectedItems, setSelectedItems] = useState<number[]>([]);
  const [, setShowBulkEditModal] = useState(false);

  // Persist sort settings to localStorage
  useEffect(() => {
    localStorage.setItem('queue.historySortBy', historySortBy);
  }, [historySortBy]);

  useEffect(() => {
    localStorage.setItem('queue.historySortAsc', String(historySortAsc));
  }, [historySortAsc]);

  useEffect(() => {
    localStorage.setItem('queue.pendingSortBy', pendingSortBy);
  }, [pendingSortBy]);

  useEffect(() => {
    localStorage.setItem('queue.pendingSortAsc', String(pendingSortAsc));
  }, [pendingSortAsc]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const timeFormat: TimeFormat = settings?.time_format || 'system';

  const { data: queue, isLoading } = useQuery({
    queryKey: ['queue', filterPrinter, filterStatus],
    queryFn: () => api.getQueue(filterPrinter || undefined, filterStatus || undefined),
    refetchInterval: 5000,
  });

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
  });

  const cancelMutation = useMutation({
    mutationFn: (id: number) => api.cancelQueueItem(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('queue.toast.itemCancelled'));
    },
    onError: () => showToast(t('queue.toast.failedToCancel'), 'error'),
  });

  const removeMutation = useMutation({
    mutationFn: (id: number) => api.removeFromQueue(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('queue.toast.itemRemoved'));
    },
    onError: () => showToast(t('queue.toast.failedToRemove'), 'error'),
  });

  const stopMutation = useMutation({
    mutationFn: (id: number) => api.stopQueueItem(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('queue.toast.printStopped'));
    },
    onError: () => showToast(t('queue.toast.failedToStop'), 'error'),
  });

  const startMutation = useMutation({
    mutationFn: (id: number) => api.startQueueItem(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('queue.toast.printReleased'));
    },
    onError: () => showToast(t('queue.toast.failedToStart'), 'error'),
  });

  const reorderMutation = useMutation({
    mutationFn: (items: { id: number; position: number }[]) => api.reorderQueue(items),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
    },
    onError: () => showToast(t('queue.toast.failedToReorder'), 'error'),
  });

  const clearHistoryMutation = useMutation({
    mutationFn: async () => {
      const historyItems = queue?.filter(i =>
        ['completed', 'failed', 'skipped', 'cancelled'].includes(i.status)
      ) || [];
      for (const item of historyItems) {
        await api.removeFromQueue(item.id);
      }
      return historyItems.length;
    },
    onSuccess: (count) => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('queue.toast.clearedHistory', { count }));
    },
    onError: () => showToast(t('queue.toast.failedToClear'), 'error'),
  });

  const bulkCancelMutation = useMutation({
    mutationFn: async (ids: number[]) => {
      for (const id of ids) {
        await api.cancelQueueItem(id);
      }
      return ids.length;
    },
    onSuccess: (count) => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      setSelectedItems([]);
      showToast(t('queue.toast.cancelledItems', { count }));
    },
    onError: () => showToast(t('queue.toast.failedToCancelItems'), 'error'),
  });

  const handleToggleSelect = (id: number) => {
    setSelectedItems(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    );
  };

  // Get unique locations from printers for the filter dropdown
  const uniqueLocations = useMemo(() => {
    const locations = new Set<string>();
    printers?.forEach(p => {
      if (p.location) locations.add(p.location);
    });
    // Also include locations from queue items (for model-based assignments)
    queue?.forEach(item => {
      if (item.target_location) locations.add(item.target_location);
    });
    return Array.from(locations).sort();
  }, [printers, queue]);

  // Helper to check if a queue item matches the location filter
  const matchesLocationFilter = useCallback((item: PrintQueueItem): boolean => {
    if (!filterLocation) return true;
    // For model-based assignments, check target_location
    if (item.target_location) return item.target_location === filterLocation;
    // For printer-based assignments, check the printer's location
    if (item.printer_id) {
      const printer = printers?.find(p => p.id === item.printer_id);
      return printer?.location === filterLocation;
    }
    return false;
  }, [filterLocation, printers]);


  const pendingItems = useMemo(() => {
    let items = queue?.filter(i => i.status === 'pending') || [];

    // Apply location filter
    if (filterLocation) {
      items = items.filter(matchesLocationFilter);
    }

    // Helper to get scheduled time as timestamp (ASAP/placeholder = 0 for earliest)
    const getScheduledTime = (item: PrintQueueItem): number => {
      if (!item.scheduled_time) return 0;
      const time = new Date(item.scheduled_time).getTime();
      // Placeholder dates (> 6 months out) are treated as ASAP
      const sixMonthsFromNow = Date.now() + (180 * 24 * 60 * 60 * 1000);
      return time > sixMonthsFromNow ? 0 : time;
    };

    return [...items].sort((a, b) => {
      let cmp: number;
      if (pendingSortBy === 'name') {
        const aName = a.archive_name || a.library_file_name || '';
        const bName = b.archive_name || b.library_file_name || '';
        cmp = aName.localeCompare(bName);
      } else if (pendingSortBy === 'printer') {
        cmp = (a.printer_name || '').localeCompare(b.printer_name || '');
      } else if (pendingSortBy === 'time') {
        // Sort by scheduled start time (when print will begin)
        cmp = getScheduledTime(a) - getScheduledTime(b);
      } else {
        cmp = a.position - b.position;
      }
      return pendingSortAsc ? cmp : -cmp;
    });
  }, [queue, pendingSortBy, pendingSortAsc, matchesLocationFilter, filterLocation]);

  const handleSelectAll = () => {
    const allPendingIds = pendingItems.map(i => i.id);
    if (selectedItems.length === allPendingIds.length) {
      setSelectedItems([]);
    } else {
      setSelectedItems(allPendingIds);
    }
  };

  const activeItems = useMemo(() => {
    let items = queue?.filter(i => i.status === 'printing') || [];
    if (filterLocation) {
      items = items.filter(matchesLocationFilter);
    }
    return items;
  }, [queue, filterLocation, matchesLocationFilter]);


  const historyItems = useMemo(() => {
    let items = queue?.filter(i => ['completed', 'failed', 'skipped', 'cancelled'].includes(i.status)) || [];
    if (filterLocation) {
      items = items.filter(matchesLocationFilter);
    }
    return [...items].sort((a, b) => {
      let cmp: number;
      if (historySortBy === 'name') {
        const aName = a.archive_name || a.library_file_name || '';
        const bName = b.archive_name || b.library_file_name || '';
        cmp = aName.localeCompare(bName);
      } else if (historySortBy === 'printer') {
        cmp = (a.printer_name || '').localeCompare(b.printer_name || '');
      } else {
        // Default: by date - most recent first (desc) is the natural order
        cmp = new Date(b.completed_at || b.created_at).getTime() - new Date(a.completed_at || a.created_at).getTime();
      }
      return historySortAsc ? -cmp : cmp;
    });
  }, [queue, historySortBy, historySortAsc, matchesLocationFilter, filterLocation]);

  // Calculate total queue time
  const totalQueueTime = useMemo(() => {
    return pendingItems.reduce((acc, item) => acc + (item.print_time_seconds || 0), 0);
  }, [pendingItems]);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = pendingItems.findIndex(i => i.id === active.id);
    const newIndex = pendingItems.findIndex(i => i.id === over.id);

    if (oldIndex !== -1 && newIndex !== -1) {
      const reordered = arrayMove(pendingItems, oldIndex, newIndex);
      const updates = reordered.map((item, index) => ({
        id: item.id,
        position: index + 1,
      }));
      reorderMutation.mutate(updates);
    }
  };

  const handleSelectAll = () => {
    if (selectedItems.length === pendingItems.length && pendingItems.length > 0) {
      setSelectedItems([]);
    } else {
      setSelectedItems(pendingItems.map(i => i.id));
    }
  };

  const handleToggleSelect = (id: number) => {
    setSelectedItems(prev =>
      prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]
    );
  };

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <ListOrdered className="w-7 h-7 text-bambu-green" />
            {t('queue.title')}
          </h1>
          <p className="text-bambu-gray mt-1">{t('queue.description')}</p>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
        <Card className="bg-gradient-to-br from-blue-500/10 to-transparent border-blue-500/20">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-blue-500/20 flex items-center justify-center">
                <Play className="w-5 h-5 text-blue-400" />
              </div>
              <div>
                <p className="text-2xl font-bold text-white">{activeItems.length}</p>
                <p className="text-sm text-bambu-gray">{t('queue.summary.printing')}</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-yellow-500/10 to-transparent border-yellow-500/20">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-yellow-500/20 flex items-center justify-center">
                <Clock className="w-5 h-5 text-yellow-400" />
              </div>
              <div>
                <p className="text-2xl font-bold text-white">{pendingItems.length}</p>
                <p className="text-sm text-bambu-gray">{t('queue.summary.queued')}</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-bambu-green/10 to-transparent border-bambu-green/20">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-bambu-green/20 flex items-center justify-center">
                <Timer className="w-5 h-5 text-bambu-green" />
              </div>
              <div>
                <p className="text-2xl font-bold text-white">{formatDuration(totalQueueTime)}</p>
                <p className="text-sm text-bambu-gray">{t('queue.summary.totalQueueTime')}</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-gray-500/10 to-transparent border-gray-500/20">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gray-500/20 flex items-center justify-center">
                <CheckCircle className="w-5 h-5 text-gray-400" />
              </div>
              <div>
                <p className="text-2xl font-bold text-white">{historyItems.length}</p>
                <p className="text-sm text-bambu-gray">{t('queue.summary.history')}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4 mb-6">
        <select
          className="px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
          value={filterPrinter === -1 ? 'unassigned' : (filterPrinter || '')}
          onChange={(e) => {
            const val = e.target.value;
            if (val === 'unassigned') setFilterPrinter(-1);
            else if (val === '') setFilterPrinter(null);
            else setFilterPrinter(Number(val));
          }}
        >
          <option value="">{t('queue.filters.allPrinters')}</option>
          <option value="unassigned">{t('queue.unassigned')}</option>
          {printers?.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>

        <select
          className="px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="">{t('queue.filters.allStatus')}</option>
          <option value="pending">{t('queue.status.pending')}</option>
          <option value="printing">{t('queue.status.printing')}</option>
          <option value="completed">{t('queue.status.completed')}</option>
          <option value="failed">{t('queue.status.failed')}</option>
          <option value="skipped">{t('queue.status.skipped')}</option>
          <option value="cancelled">{t('queue.status.cancelled')}</option>
        </select>

        {uniqueLocations.length > 0 && (
          <select
            className="px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            value={filterLocation}
            onChange={(e) => setFilterLocation(e.target.value)}
          >
            <option value="">All Locations</option>
            {uniqueLocations.map((loc) => (
              <option key={loc} value={loc}>{loc}</option>
            ))}
          </select>
        )}

        <div className="flex-1" />

        {historyItems.length > 0 && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowClearHistoryConfirm(true)}
            disabled={!hasPermission('queue:delete_all')}
            title={!hasPermission('queue:delete_all') ? t('queue.permissions.noClearHistoryPermission') : undefined}
          >
            <Trash2 className="w-4 h-4" />
            {t('queue.clearHistory')}
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-bambu-gray">{t('common.loading')}</div>
      ) : queue?.length === 0 ? (
        <Card className="p-12 text-center border-dashed">
          <Calendar className="w-16 h-16 text-bambu-gray mx-auto mb-4 opacity-50" />
          <h3 className="text-xl font-medium text-white mb-2">{t('queue.empty.title')}</h3>
          <p className="text-bambu-gray max-w-md mx-auto">
            {t('queue.empty.description')}
          </p>
        </Card>
      ) : (
        <div className="space-y-8">
          {/* Active Prints */}
          {activeItems.length > 0 && (
            <div>
              <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
                {t('queue.currentlyPrinting')}
              </h2>
              <div className="space-y-3">
                {activeItems.map((item) => (
                  <SortableQueueItem
                    key={item.id}
                    item={item}
                    onEdit={() => {}}
                    onCancel={() => {}}
                    onRemove={() => {}}
                    onStop={() => setConfirmAction({ type: 'stop', item })}
                    onRequeue={() => {}}
                    onStart={() => {}}
                    timeFormat={timeFormat}
                    hasPermission={hasPermission}
                    canModify={canModify}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Pending Queue */}
          {pendingItems.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <Clock className="w-5 h-5 text-yellow-400" />
                  {t('queue.summary.queued')}
                  <span className="text-sm font-normal text-bambu-gray">
                    ({t('queue.itemCount', { count: pendingItems.length })})
                  </span>
                  <span className="text-xs text-bambu-gray ml-2" title={t('queue.dragReorderTooltip')}>
                    {t('queue.dragToReorder')}
                  </span>
                </h2>
                <div className="flex items-center gap-2">
                  <select
                    className="px-3 py-1.5 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    value={pendingSortBy}
                    onChange={(e) => setPendingSortBy(e.target.value as 'position' | 'name' | 'printer' | 'time')}
                  >
                    <option value="position">{t('queue.sort.byPosition')}</option>
                    <option value="name">{t('queue.sort.byName')}</option>
                    <option value="printer">{t('queue.sort.byPrinter')}</option>
                    <option value="time">{t('queue.sort.bySchedule')}</option>
                  </select>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setPendingSortAsc(!pendingSortAsc)}
                    title={pendingSortAsc ? t('queue.sort.ascending') : t('queue.sort.descending')}
                    className="px-2"
                  >
                    {pendingSortAsc ? <ArrowUp className="w-4 h-4" /> : <ArrowDown className="w-4 h-4" />}
                  </Button>
                </div>
              </div>

              {/* Bulk action toolbar */}
              <div className="flex items-center gap-3 mb-4 p-3 bg-bambu-dark rounded-lg">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleSelectAll}
                  className="flex items-center gap-2"
                >
                  {selectedItems.length === pendingItems.length && pendingItems.length > 0 ? (
                    <CheckSquare className="w-4 h-4 text-bambu-green" />
                  ) : (
                    <Square className="w-4 h-4" />
                  )}
                  {selectedItems.length === pendingItems.length && pendingItems.length > 0 ? t('queue.bulkActions.deselectAll') : t('queue.bulkActions.selectAll')}
                </Button>
                {selectedItems.length > 0 && (
                  <>
                    <span className="text-sm text-bambu-gray">
                      {t('queue.bulkActions.selected', { count: selectedItems.length })}
                    </span>
                    <div className="h-4 w-px bg-bambu-dark-tertiary" />
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setShowBulkEditModal(true)}
                      className="flex items-center gap-2 text-bambu-green hover:text-bambu-green-light"
                      disabled={!hasAnyPermission('queue:update_own', 'queue:update_all')}
                      title={!hasAnyPermission('queue:update_own', 'queue:update_all') ? t('queue.permissions.noEditPermission') : undefined}
                    >
                      <Pencil className="w-4 h-4" />
                      {t('queue.bulkActions.editSelected')}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => bulkCancelMutation.mutate(selectedItems)}
                      className="flex items-center gap-2 text-red-400 hover:text-red-300"
                      disabled={bulkCancelMutation.isPending || !hasAnyPermission('queue:delete_own', 'queue:delete_all')}
                      title={!hasAnyPermission('queue:delete_own', 'queue:delete_all') ? t('queue.permissions.noCancelPermission') : undefined}
                    >
                      <X className="w-4 h-4" />
                      {t('queue.bulkActions.cancelSelected')}
                    </Button>
                  </>
                )}
              </div>

              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={handleDragEnd}
              >
                <SortableContext
                  items={pendingItems.map(i => i.id)}
                  strategy={verticalListSortingStrategy}
                >
                  <div className="space-y-3">
                    {pendingItems.map((item, index) => (
                      <SortableQueueItem
                        key={item.id}
                        item={item}
                        position={index + 1}
                        onEdit={() => setEditItem(item)}
                        onCancel={() => setConfirmAction({ type: 'cancel', item })}
                        onRemove={() => {}}
                        onStop={() => {}}
                        onRequeue={() => {}}
                        onStart={() => startMutation.mutate(item.id)}
                        timeFormat={timeFormat}
                        isSelected={selectedItems.includes(item.id)}
                        onToggleSelect={() => handleToggleSelect(item.id)}
                        hasPermission={hasPermission}
                        canModify={canModify}
                      />
                    ))}
                  </div>
                </SortableContext>
              </DndContext>
            </div>
          )}

          {/* History */}
          {historyItems.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <CheckCircle className="w-5 h-5 text-bambu-gray" />
                  {t('queue.summary.history')}
                  <span className="text-sm font-normal text-bambu-gray">
                    ({t('queue.itemCount', { count: historyItems.length })})
                  </span>
                </h2>
                <div className="flex items-center gap-2">
                  <select
                    className="px-3 py-1.5 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    value={historySortBy}
                    onChange={(e) => setHistorySortBy(e.target.value as 'date' | 'name' | 'printer')}
                  >
                    <option value="date">{t('queue.sort.byDate')}</option>
                    <option value="name">{t('queue.sort.byName')}</option>
                    <option value="printer">{t('queue.sort.byPrinter')}</option>
                  </select>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setHistorySortAsc(!historySortAsc)}
                    title={historySortAsc ? t('queue.sort.ascendingOldest') : t('queue.sort.descendingNewest')}
                    className="px-2"
                  >
                    {historySortAsc ? <ArrowUp className="w-4 h-4" /> : <ArrowDown className="w-4 h-4" />}
                  </Button>
                </div>
              </div>
              <div className="space-y-3">
                {historyItems.slice(0, 20).map((item, index) => (
                  <SortableQueueItem
                    key={item.id}
                    item={item}
                    position={index + 1}
                    onEdit={() => {}}
                    onCancel={() => {}}
                    onRemove={() => setConfirmAction({ type: 'remove', item })}
                    onStop={() => {}}
                    onRequeue={() => setRequeueItem(item)}
                    onStart={() => {}}
                    timeFormat={timeFormat}
                    hasPermission={hasPermission}
                    canModify={canModify}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Edit Modal */}
      {editItem && (
        <PrintModal
          mode="edit-queue-item"
          archiveId={editItem.archive_id ?? undefined}
          libraryFileId={editItem.library_file_id ?? undefined}
          archiveName={editItem.archive_name || editItem.library_file_name || `File #${editItem.archive_id || editItem.library_file_id}`}
          queueItem={editItem}
          onClose={() => setEditItem(null)}
        />
      )}

      {/* Re-queue Modal */}
      {requeueItem && (
        <PrintModal
          mode="add-to-queue"
          archiveId={requeueItem.archive_id ?? undefined}
          libraryFileId={requeueItem.library_file_id ?? undefined}
          archiveName={requeueItem.archive_name || requeueItem.library_file_name || `File #${requeueItem.archive_id || requeueItem.library_file_id}`}
          onClose={() => setRequeueItem(null)}
        />
      )}

      {/* Confirm Action Modal */}
      {confirmAction && (
        <ConfirmModal
          title={
            confirmAction.type === 'cancel' ? t('queue.confirm.cancelTitle') :
            confirmAction.type === 'stop' ? t('queue.confirm.stopTitle') :
            t('queue.confirm.removeTitle')
          }
          message={
            confirmAction.type === 'cancel'
              ? t('queue.confirm.cancelMessage', { name: confirmAction.item.archive_name || confirmAction.item.library_file_name || t('queue.confirm.thisPrint') })
              : confirmAction.type === 'stop'
              ? t('queue.confirm.stopMessage', { name: confirmAction.item.archive_name || confirmAction.item.library_file_name || t('queue.confirm.thisPrint') })
              : t('queue.confirm.removeMessage', { name: confirmAction.item.archive_name || confirmAction.item.library_file_name || t('queue.confirm.thisItem') })
          }
          confirmText={
            confirmAction.type === 'cancel' ? t('queue.confirm.cancelButton') :
            confirmAction.type === 'stop' ? t('queue.confirm.stopButton') :
            t('queue.remove')
          }
          variant="danger"
          onConfirm={() => {
            if (confirmAction.type === 'cancel') {
              cancelMutation.mutate(confirmAction.item.id);
            } else if (confirmAction.type === 'stop') {
              stopMutation.mutate(confirmAction.item.id);
            } else {
              removeMutation.mutate(confirmAction.item.id);
            }
            setConfirmAction(null);
          }}
          onCancel={() => setConfirmAction(null)}
        />
      )}

      {/* Clear History Confirm Modal */}
      {showClearHistoryConfirm && (
        <ConfirmModal
          title={t('queue.clearHistory')}
          message={t('queue.confirm.clearHistoryMessage', { count: historyItems.length })}
          confirmText={t('queue.clearHistory')}
          variant="danger"
          onConfirm={() => {
            clearHistoryMutation.mutate();
            setShowClearHistoryConfirm(false);
          }}
          onCancel={() => setShowClearHistoryConfirm(false)}
        />
      )}
    </div>
  );
}
