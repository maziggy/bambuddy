import { useState, useMemo, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
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
} from 'lucide-react';
import { api } from '../api/client';
import type { PrintQueueItem } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { EditQueueItemModal } from '../components/EditQueueItemModal';
import { AddToQueueModal } from '../components/AddToQueueModal';
import { useToast } from '../contexts/ToastContext';

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return '--';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return 'ASAP';
  const date = new Date(dateString);
  const now = new Date();
  const diff = date.getTime() - now.getTime();

  if (diff < -60000) return 'Overdue';
  if (diff < 0) return 'Now';
  if (diff < 60000) return 'In less than a minute';
  if (diff < 3600000) return `In ${Math.round(diff / 60000)} min`;
  if (diff < 86400000) return `In ${Math.round(diff / 3600000)} hours`;
  return date.toLocaleString();
}

function StatusBadge({ status }: { status: PrintQueueItem['status'] }) {
  const config = {
    pending: { icon: Clock, color: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20', label: 'Pending' },
    printing: { icon: Play, color: 'text-blue-400 bg-blue-400/10 border-blue-400/20', label: 'Printing' },
    completed: { icon: CheckCircle, color: 'text-green-400 bg-green-400/10 border-green-400/20', label: 'Completed' },
    failed: { icon: XCircle, color: 'text-red-400 bg-red-400/10 border-red-400/20', label: 'Failed' },
    skipped: { icon: SkipForward, color: 'text-orange-400 bg-orange-400/10 border-orange-400/20', label: 'Skipped' },
    cancelled: { icon: X, color: 'text-gray-400 bg-gray-400/10 border-gray-400/20', label: 'Cancelled' },
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
}: {
  item: PrintQueueItem;
  position?: number;
  onEdit: () => void;
  onCancel: () => void;
  onRemove: () => void;
  onStop: () => void;
  onRequeue: () => void;
  onStart: () => void;
}) {
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
      `}
    >
      <div className="flex items-center gap-4 p-4">
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
              src={api.getArchiveThumbnail(item.archive_id)}
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
              {item.archive_name || `Archive #${item.archive_id}`}
            </p>
            <Link
              to={`/archives?highlight=${item.archive_id}`}
              className="text-bambu-gray hover:text-bambu-green transition-colors flex-shrink-0"
              title="View archive"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </Link>
          </div>

          <div className="flex items-center gap-3 text-sm text-bambu-gray">
            <span className="flex items-center gap-1.5">
              <Printer className="w-3.5 h-3.5" />
              {item.printer_name || `Printer #${item.printer_id}`}
            </span>
            {item.print_time_seconds && (
              <span className="flex items-center gap-1.5">
                <Timer className="w-3.5 h-3.5" />
                {formatDuration(item.print_time_seconds)}
              </span>
            )}
            {isPending && !item.manual_start && (
              <span className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                {formatRelativeTime(item.scheduled_time)}
              </span>
            )}
          </div>

          {/* Options badges */}
          <div className="flex items-center gap-2 mt-2">
            {item.manual_start && (
              <span className="text-xs px-2 py-0.5 bg-purple-500/10 text-purple-400 rounded-full border border-purple-500/20 flex items-center gap-1">
                <Hand className="w-3 h-3" />
                Staged
              </span>
            )}
            {item.require_previous_success && (
              <span className="text-xs px-2 py-0.5 bg-orange-500/10 text-orange-400 rounded-full border border-orange-500/20">
                Requires previous success
              </span>
            )}
            {item.auto_off_after && (
              <span className="text-xs px-2 py-0.5 bg-blue-500/10 text-blue-400 rounded-full border border-blue-500/20 flex items-center gap-1">
                <Power className="w-3 h-3" />
                Auto power off
              </span>
            )}
          </div>

          {/* Progress bar for printing items - TODO: integrate with WebSocket */}
          {isPrinting && (
            <div className="mt-3">
              <div className="h-2 bg-bambu-dark rounded-full overflow-hidden">
                <div className="h-full bg-gradient-to-r from-blue-500 to-blue-400 animate-pulse w-full opacity-50" />
              </div>
              <p className="text-xs text-bambu-gray mt-1">Printing in progress...</p>
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
              title="Stop Print"
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
                  title="Start Print"
                  className="text-bambu-green hover:text-bambu-green-light hover:bg-bambu-green/10"
                >
                  <Play className="w-4 h-4" />
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={onEdit}
                title="Edit"
              >
                <Pencil className="w-4 h-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={onCancel}
                title="Cancel"
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
                title="Re-queue"
                className="text-bambu-green hover:text-bambu-green/80 hover:bg-bambu-green/10"
              >
                <RefreshCw className="w-4 h-4" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={onRemove}
                title="Remove"
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
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [filterPrinter, setFilterPrinter] = useState<number | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>('');
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
      showToast('Queue item cancelled');
    },
    onError: () => showToast('Failed to cancel item', 'error'),
  });

  const removeMutation = useMutation({
    mutationFn: (id: number) => api.removeFromQueue(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast('Queue item removed');
    },
    onError: () => showToast('Failed to remove item', 'error'),
  });

  const stopMutation = useMutation({
    mutationFn: (id: number) => api.stopQueueItem(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast('Print stopped');
    },
    onError: () => showToast('Failed to stop print', 'error'),
  });

  const startMutation = useMutation({
    mutationFn: (id: number) => api.startQueueItem(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast('Print released to queue');
    },
    onError: () => showToast('Failed to start print', 'error'),
  });

  const reorderMutation = useMutation({
    mutationFn: (items: { id: number; position: number }[]) => api.reorderQueue(items),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
    },
    onError: () => showToast('Failed to reorder queue', 'error'),
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
      showToast(`Cleared ${count} history item${count !== 1 ? 's' : ''}`);
    },
    onError: () => showToast('Failed to clear history', 'error'),
  });

  const pendingItems = useMemo(() => {
    const items = queue?.filter(i => i.status === 'pending') || [];

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
        cmp = (a.archive_name || '').localeCompare(b.archive_name || '');
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
  }, [queue, pendingSortBy, pendingSortAsc]);
  const activeItems = queue?.filter(i => i.status === 'printing') || [];
  const historyItems = useMemo(() => {
    const items = queue?.filter(i => ['completed', 'failed', 'skipped', 'cancelled'].includes(i.status)) || [];
    return [...items].sort((a, b) => {
      let cmp: number;
      if (historySortBy === 'name') {
        cmp = (a.archive_name || '').localeCompare(b.archive_name || '');
      } else if (historySortBy === 'printer') {
        cmp = (a.printer_name || '').localeCompare(b.printer_name || '');
      } else {
        // Default: by date - most recent first (desc) is the natural order
        cmp = new Date(b.completed_at || b.created_at).getTime() - new Date(a.completed_at || a.created_at).getTime();
      }
      return historySortAsc ? -cmp : cmp;
    });
  }, [queue, historySortBy, historySortAsc]);

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

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <ListOrdered className="w-7 h-7 text-bambu-green" />
            Print Queue
          </h1>
          <p className="text-bambu-gray mt-1">Schedule and manage your print jobs</p>
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
                <p className="text-sm text-bambu-gray">Printing</p>
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
                <p className="text-sm text-bambu-gray">Queued</p>
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
                <p className="text-sm text-bambu-gray">Total Queue Time</p>
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
                <p className="text-sm text-bambu-gray">History</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4 mb-6">
        <select
          className="px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
          value={filterPrinter || ''}
          onChange={(e) => setFilterPrinter(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All Printers</option>
          {printers?.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>

        <select
          className="px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="">All Status</option>
          <option value="pending">Pending</option>
          <option value="printing">Printing</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="skipped">Skipped</option>
          <option value="cancelled">Cancelled</option>
        </select>

        <div className="flex-1" />

        {historyItems.length > 0 && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowClearHistoryConfirm(true)}
          >
            <Trash2 className="w-4 h-4" />
            Clear History
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-bambu-gray">Loading...</div>
      ) : queue?.length === 0 ? (
        <Card className="p-12 text-center border-dashed">
          <Calendar className="w-16 h-16 text-bambu-gray mx-auto mb-4 opacity-50" />
          <h3 className="text-xl font-medium text-white mb-2">No prints scheduled</h3>
          <p className="text-bambu-gray max-w-md mx-auto">
            Schedule a print from the Archives page using the "Schedule" option in the context menu,
            or drag and drop files to get started.
          </p>
        </Card>
      ) : (
        <div className="space-y-8">
          {/* Active Prints */}
          {activeItems.length > 0 && (
            <div>
              <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
                Currently Printing
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
                  Queued
                  <span className="text-sm font-normal text-bambu-gray">
                    ({pendingItems.length} item{pendingItems.length !== 1 ? 's' : ''})
                  </span>
                  <span className="text-xs text-bambu-gray ml-2" title="Position only affects ASAP items. Scheduled items run at their set time.">
                    Drag to reorder (ASAP only)
                  </span>
                </h2>
                <div className="flex items-center gap-2">
                  <select
                    className="px-3 py-1.5 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    value={pendingSortBy}
                    onChange={(e) => setPendingSortBy(e.target.value as 'position' | 'name' | 'printer' | 'time')}
                  >
                    <option value="position">Sort by Position</option>
                    <option value="name">Sort by Name</option>
                    <option value="printer">Sort by Printer</option>
                    <option value="time">Sort by Schedule</option>
                  </select>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setPendingSortAsc(!pendingSortAsc)}
                    title={pendingSortAsc ? 'Ascending' : 'Descending'}
                    className="px-2"
                  >
                    {pendingSortAsc ? <ArrowUp className="w-4 h-4" /> : <ArrowDown className="w-4 h-4" />}
                  </Button>
                </div>
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
                  History
                  <span className="text-sm font-normal text-bambu-gray">
                    ({historyItems.length} item{historyItems.length !== 1 ? 's' : ''})
                  </span>
                </h2>
                <div className="flex items-center gap-2">
                  <select
                    className="px-3 py-1.5 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    value={historySortBy}
                    onChange={(e) => setHistorySortBy(e.target.value as 'date' | 'name' | 'printer')}
                  >
                    <option value="date">Sort by Date</option>
                    <option value="name">Sort by Name</option>
                    <option value="printer">Sort by Printer</option>
                  </select>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setHistorySortAsc(!historySortAsc)}
                    title={historySortAsc ? 'Ascending (oldest first)' : 'Descending (newest first)'}
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
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Edit Modal */}
      {editItem && (
        <EditQueueItemModal
          item={editItem}
          onClose={() => setEditItem(null)}
        />
      )}

      {/* Re-queue Modal */}
      {requeueItem && (
        <AddToQueueModal
          archiveId={requeueItem.archive_id}
          archiveName={requeueItem.archive_name || `Archive #${requeueItem.archive_id}`}
          onClose={() => setRequeueItem(null)}
        />
      )}

      {/* Confirm Action Modal */}
      {confirmAction && (
        <ConfirmModal
          title={
            confirmAction.type === 'cancel' ? 'Cancel Scheduled Print' :
            confirmAction.type === 'stop' ? 'Stop Print' :
            'Remove from History'
          }
          message={
            confirmAction.type === 'cancel'
              ? `Are you sure you want to cancel "${confirmAction.item.archive_name || 'this print'}"?`
              : confirmAction.type === 'stop'
              ? `Are you sure you want to stop the current print "${confirmAction.item.archive_name || 'this print'}"? This will cancel the print job on the printer.`
              : `Are you sure you want to remove "${confirmAction.item.archive_name || 'this item'}" from the queue history?`
          }
          confirmText={
            confirmAction.type === 'cancel' ? 'Cancel Print' :
            confirmAction.type === 'stop' ? 'Stop Print' :
            'Remove'
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
          title="Clear History"
          message={`Are you sure you want to remove all ${historyItems.length} item${historyItems.length !== 1 ? 's' : ''} from the history?`}
          confirmText="Clear History"
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
