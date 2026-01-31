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
  Check,
  CheckSquare,
  Square,
} from 'lucide-react';
import { api } from '../api/client';
import { parseUTCDate, formatDateTime, type TimeFormat } from '../utils/date';
import type { PrintQueueItem, PrintQueueBulkUpdate } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { PrintModal } from '../components/PrintModal';
import { useToast } from '../contexts/ToastContext';

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return '--';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatRelativeTime(dateString: string | null, timeFormat: TimeFormat = 'system'): string {
  if (!dateString) return 'ASAP';
  const date = parseUTCDate(dateString);
  if (!date) return 'ASAP';
  const now = new Date();
  const diff = date.getTime() - now.getTime();

  if (diff < -60000) return 'Overdue';
  if (diff < 0) return 'Now';
  if (diff < 60000) return 'In less than a minute';
  if (diff < 3600000) return `In ${Math.round(diff / 60000)} min`;
  if (diff < 86400000) return `In ${Math.round(diff / 3600000)} hours`;
  return formatDateTime(dateString, timeFormat);
}

function StatusBadge({ status, waitingReason }: { status: PrintQueueItem['status']; waitingReason?: string | null }) {
  // Special case: pending with waiting_reason shows as "Waiting"
  if (status === 'pending' && waitingReason) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border text-purple-400 bg-purple-400/10 border-purple-400/20">
        <Clock className="w-3.5 h-3.5" />
        Waiting
      </span>
    );
  }

  const config = {
    pending: { icon: Clock, color: 'text-status-warning bg-status-warning/10 border-status-warning/20', label: 'Pending' },
    printing: { icon: Play, color: 'text-blue-400 bg-blue-400/10 border-blue-400/20', label: 'Printing' },
    completed: { icon: CheckCircle, color: 'text-status-ok bg-status-ok/10 border-status-ok/20', label: 'Completed' },
    failed: { icon: XCircle, color: 'text-status-error bg-status-error/10 border-status-error/20', label: 'Failed' },
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

// Bulk edit modal for multiple queue items
function BulkEditModal({
  selectedCount,
  printers,
  onSave,
  onClose,
  isSaving,
}: {
  selectedCount: number;
  printers: { id: number; name: string }[];
  onSave: (data: Partial<PrintQueueBulkUpdate>) => void;
  onClose: () => void;
  isSaving: boolean;
}) {
  const [printerId, setPrinterId] = useState<number | null | 'unchanged'>('unchanged');
  const [manualStart, setManualStart] = useState<boolean | 'unchanged'>('unchanged');
  const [autoOffAfter, setAutoOffAfter] = useState<boolean | 'unchanged'>('unchanged');
  const [requirePreviousSuccess, setRequirePreviousSuccess] = useState<boolean | 'unchanged'>('unchanged');
  const [bedLevelling, setBedLevelling] = useState<boolean | 'unchanged'>('unchanged');
  const [flowCali, setFlowCali] = useState<boolean | 'unchanged'>('unchanged');
  const [vibrationCali, setVibrationCali] = useState<boolean | 'unchanged'>('unchanged');
  const [layerInspect, setLayerInspect] = useState<boolean | 'unchanged'>('unchanged');
  const [timelapse, setTimelapse] = useState<boolean | 'unchanged'>('unchanged');
  const [useAms, setUseAms] = useState<boolean | 'unchanged'>('unchanged');

  const handleSave = () => {
    const data: Partial<PrintQueueBulkUpdate> = {};
    if (printerId !== 'unchanged') data.printer_id = printerId;
    if (manualStart !== 'unchanged') data.manual_start = manualStart;
    if (autoOffAfter !== 'unchanged') data.auto_off_after = autoOffAfter;
    if (requirePreviousSuccess !== 'unchanged') data.require_previous_success = requirePreviousSuccess;
    if (bedLevelling !== 'unchanged') data.bed_levelling = bedLevelling;
    if (flowCali !== 'unchanged') data.flow_cali = flowCali;
    if (vibrationCali !== 'unchanged') data.vibration_cali = vibrationCali;
    if (layerInspect !== 'unchanged') data.layer_inspect = layerInspect;
    if (timelapse !== 'unchanged') data.timelapse = timelapse;
    if (useAms !== 'unchanged') data.use_ams = useAms;
    onSave(data);
  };

  const hasChanges = printerId !== 'unchanged' || manualStart !== 'unchanged' || autoOffAfter !== 'unchanged' ||
    requirePreviousSuccess !== 'unchanged' || bedLevelling !== 'unchanged' || flowCali !== 'unchanged' ||
    vibrationCali !== 'unchanged' || layerInspect !== 'unchanged' || timelapse !== 'unchanged' || useAms !== 'unchanged';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
      <div className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">
            Edit {selectedCount} Item{selectedCount !== 1 ? 's' : ''}
          </h2>
          <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded">
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <p className="text-sm text-bambu-gray">
            Only changed settings will be applied to selected items.
          </p>

          {/* Printer Assignment */}
          <div>
            <label className="block text-sm font-medium text-white mb-2">Printer</label>
            <select
              value={printerId === null ? 'null' : printerId === 'unchanged' ? 'unchanged' : String(printerId)}
              onChange={(e) => {
                const val = e.target.value;
                if (val === 'unchanged') setPrinterId('unchanged');
                else if (val === 'null') setPrinterId(null);
                else setPrinterId(Number(val));
              }}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="unchanged">— No change —</option>
              <option value="null">Unassigned</option>
              {printers.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          {/* Queue Options */}
          <div>
            <label className="block text-sm font-medium text-white mb-2">Queue Options</label>
            <div className="space-y-2">
              <TriStateToggle label="Staged (manual start)" value={manualStart} onChange={setManualStart} />
              <TriStateToggle label="Auto power off after print" value={autoOffAfter} onChange={setAutoOffAfter} />
              <TriStateToggle label="Require previous success" value={requirePreviousSuccess} onChange={setRequirePreviousSuccess} />
            </div>
          </div>

          {/* Print Options */}
          <div>
            <label className="block text-sm font-medium text-white mb-2">Print Options</label>
            <div className="space-y-2">
              <TriStateToggle label="Bed levelling" value={bedLevelling} onChange={setBedLevelling} />
              <TriStateToggle label="Flow calibration" value={flowCali} onChange={setFlowCali} />
              <TriStateToggle label="Vibration calibration" value={vibrationCali} onChange={setVibrationCali} />
              <TriStateToggle label="First layer inspection" value={layerInspect} onChange={setLayerInspect} />
              <TriStateToggle label="Timelapse" value={timelapse} onChange={setTimelapse} />
              <TriStateToggle label="Use AMS" value={useAms} onChange={setUseAms} />
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-3 p-4 border-t border-bambu-dark-tertiary">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            onClick={handleSave}
            disabled={!hasChanges || isSaving}
          >
            {isSaving ? 'Saving...' : 'Apply Changes'}
          </Button>
        </div>
      </div>
    </div>
  );
}

// Tri-state toggle for bulk edit (unchanged / on / off)
function TriStateToggle({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean | 'unchanged';
  onChange: (val: boolean | 'unchanged') => void;
}) {
  return (
    <div className="flex items-center justify-between py-1">
      <span className="text-sm text-bambu-gray">{label}</span>
      <div className="flex items-center gap-1 bg-bambu-dark rounded-lg p-0.5">
        <button
          onClick={() => onChange('unchanged')}
          className={`px-2 py-1 text-xs rounded ${value === 'unchanged' ? 'bg-bambu-dark-tertiary text-white' : 'text-bambu-gray hover:text-white'}`}
        >
          —
        </button>
        <button
          onClick={() => onChange(false)}
          className={`px-2 py-1 text-xs rounded ${value === false ? 'bg-red-500/20 text-red-400' : 'text-bambu-gray hover:text-white'}`}
        >
          Off
        </button>
        <button
          onClick={() => onChange(true)}
          className={`px-2 py-1 text-xs rounded ${value === true ? 'bg-bambu-green/20 text-bambu-green' : 'text-bambu-gray hover:text-white'}`}
        >
          On
        </button>
      </div>
    </div>
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
        {/* Selection checkbox for pending items */}
        {isPending && onToggleSelect && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleSelect();
            }}
            className={`flex items-center justify-center w-6 h-6 rounded border transition-colors ${
              isSelected
                ? 'bg-bambu-green border-bambu-green text-white'
                : 'border-white/30 bg-black/30 hover:border-bambu-green/50'
            }`}
          >
            {isSelected && <Check className="w-4 h-4" />}
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

        {/* Thumbnail - use plate-specific thumbnail if plate_id is set */}
        <div className="w-14 h-14 flex-shrink-0 bg-bambu-dark rounded-lg overflow-hidden">
          {item.archive_thumbnail ? (
            <img
              src={
                item.plate_id != null
                  ? api.getArchivePlateThumbnail(item.archive_id!, item.plate_id)
                  : api.getArchiveThumbnail(item.archive_id!)
              }
              alt=""
              className="w-full h-full object-cover"
            />
          ) : item.library_file_thumbnail ? (
            <img
              src={
                item.plate_id != null
                  ? api.getLibraryFilePlateThumbnail(item.library_file_id!, item.plate_id)
                  : api.getLibraryFileThumbnailUrl(item.library_file_id!)
              }
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
                title="View archive"
              >
                <ExternalLink className="w-3.5 h-3.5" />
              </Link>
            ) : item.library_file_id ? (
              <Link
                to={`/library?highlight=${item.library_file_id}`}
                className="text-bambu-gray hover:text-bambu-green transition-colors flex-shrink-0"
                title="View in File Manager"
              >
                <ExternalLink className="w-3.5 h-3.5" />
              </Link>
            ) : null}
          </div>

          <div className="flex items-center gap-3 text-sm text-bambu-gray">
            <span className={`flex items-center gap-1.5 ${item.printer_id === null && !item.target_model ? 'text-orange-400' : ''} ${item.target_model ? 'text-blue-400' : ''}`}>
              <Printer className="w-3.5 h-3.5" />
              {item.target_model
                ? `Any ${item.target_model}${item.required_filament_types?.length ? ` (${item.required_filament_types.join(', ')})` : ''}`
                : item.printer_id === null
                  ? 'Unassigned'
                  : (item.printer_name || `Printer #${item.printer_id}`)}
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
                {formatRelativeTime(item.scheduled_time, timeFormat)}
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

          {/* Waiting reason for model-based assignments */}
          {item.waiting_reason && item.status === 'pending' && (
            <p className="text-xs text-purple-400 mt-2 flex items-start gap-1">
              <AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />
              <span>{item.waiting_reason}</span>
            </p>
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
        <StatusBadge status={item.status} waitingReason={item.waiting_reason} />

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
  const [selectedItems, setSelectedItems] = useState<number[]>([]);
  const [showBulkEditModal, setShowBulkEditModal] = useState(false);
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

  const bulkUpdateMutation = useMutation({
    mutationFn: (data: PrintQueueBulkUpdate) => api.bulkUpdateQueue(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      setSelectedItems([]);
      setShowBulkEditModal(false);
      showToast(result.message);
    },
    onError: () => showToast('Failed to update items', 'error'),
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
      showToast(`Cancelled ${count} item${count !== 1 ? 's' : ''}`);
    },
    onError: () => showToast('Failed to cancel items', 'error'),
  });

  const handleToggleSelect = (id: number) => {
    setSelectedItems(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    );
  };

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
  }, [queue, pendingSortBy, pendingSortAsc]);

  const handleSelectAll = () => {
    const allPendingIds = pendingItems.map(i => i.id);
    if (selectedItems.length === allPendingIds.length) {
      setSelectedItems([]);
    } else {
      setSelectedItems(allPendingIds);
    }
  };

  const activeItems = queue?.filter(i => i.status === 'printing') || [];
  const historyItems = useMemo(() => {
    const items = queue?.filter(i => ['completed', 'failed', 'skipped', 'cancelled'].includes(i.status)) || [];
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
          value={filterPrinter === -1 ? 'unassigned' : (filterPrinter || '')}
          onChange={(e) => {
            const val = e.target.value;
            if (val === 'unassigned') setFilterPrinter(-1);
            else if (val === '') setFilterPrinter(null);
            else setFilterPrinter(Number(val));
          }}
        >
          <option value="">All Printers</option>
          <option value="unassigned">Unassigned</option>
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
                    timeFormat={timeFormat}
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
                  {selectedItems.length === pendingItems.length && pendingItems.length > 0 ? 'Deselect All' : 'Select All'}
                </Button>
                {selectedItems.length > 0 && (
                  <>
                    <span className="text-sm text-bambu-gray">
                      {selectedItems.length} selected
                    </span>
                    <div className="h-4 w-px bg-bambu-dark-tertiary" />
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setShowBulkEditModal(true)}
                      className="flex items-center gap-2 text-bambu-green hover:text-bambu-green-light"
                    >
                      <Pencil className="w-4 h-4" />
                      Edit Selected
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => bulkCancelMutation.mutate(selectedItems)}
                      className="flex items-center gap-2 text-red-400 hover:text-red-300"
                      disabled={bulkCancelMutation.isPending}
                    >
                      <X className="w-4 h-4" />
                      Cancel Selected
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
                    timeFormat={timeFormat}
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
            confirmAction.type === 'cancel' ? 'Cancel Scheduled Print' :
            confirmAction.type === 'stop' ? 'Stop Print' :
            'Remove from History'
          }
          message={
            confirmAction.type === 'cancel'
              ? `Are you sure you want to cancel "${confirmAction.item.archive_name || confirmAction.item.library_file_name || 'this print'}"?`
              : confirmAction.type === 'stop'
              ? `Are you sure you want to stop the current print "${confirmAction.item.archive_name || confirmAction.item.library_file_name || 'this print'}"? This will cancel the print job on the printer.`
              : `Are you sure you want to remove "${confirmAction.item.archive_name || confirmAction.item.library_file_name || 'this item'}" from the queue history?`
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

      {/* Bulk Edit Modal */}
      {showBulkEditModal && (
        <BulkEditModal
          selectedCount={selectedItems.length}
          printers={printers?.map(p => ({ id: p.id, name: p.name })) || []}
          onSave={(data) => {
            if (Object.keys(data).length > 0) {
              bulkUpdateMutation.mutate({ item_ids: selectedItems, ...data });
            }
          }}
          onClose={() => setShowBulkEditModal(false)}
          isSaving={bulkUpdateMutation.isPending}
        />
      )}
    </div>
  );
}
