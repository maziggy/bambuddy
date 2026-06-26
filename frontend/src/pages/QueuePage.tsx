import { useState, useMemo, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  DndContext,
  DragOverlay,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import type { DragEndEvent, DragStartEvent } from '@dnd-kit/core';
import {
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
  User,
  Pause,
  Weight,
  ChevronDown,
  ChevronRight,
  List,
  GanttChart,
  Code,
  Snail,
  Package,
  PackageOpen,
  Ungroup,
  Ban,
  PlayCircle,
} from 'lucide-react';
import { api, ApiError } from '../api/client';
import { type TimeFormat, formatETA, formatDuration, formatRelativeTime, parseUTCDate } from '../utils/date';
import { getBedTypeInfo } from '../utils/bedType';
import type { PrintQueueItem, PrintQueueBulkUpdate, Permission } from '../api/client';
import { Card } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { PrintModal } from '../components/PrintModal';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { QueueStatsBar } from '../components/QueueStatsBar';
import { CompactHistoryRow } from '../components/CompactHistoryRow';
import { QueueTimelineView } from '../components/QueueTimelineView';

function formatWeight(g: number, useKg = false): string {
  if (useKg && g >= 1000) return `${(g / 1000).toFixed(1)}kg`;
  return `${Math.round(g)}g`;
}

function StatusBadge({ status, waitingReason, printerState, t }: { status: PrintQueueItem['status']; waitingReason?: string | null; printerState?: string | null; t: (key: string) => string }) {
  // Special case: pending with waiting_reason shows as "Waiting"
  if (status === 'pending' && waitingReason) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border text-purple-400 bg-purple-400/10 border-purple-400/20">
        <Clock className="w-3.5 h-3.5" />
        {t('queue.status.waiting')}
      </span>
    );
  }

  // Special case: printing but printer is paused
  if (status === 'printing' && printerState === 'PAUSE') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border text-yellow-400 bg-yellow-400/10 border-yellow-400/20">
        <Pause className="w-3.5 h-3.5" />
        {t('queue.status.paused')}
      </span>
    );
  }

  const config = {
    pending: { icon: Clock, color: 'text-status-warning bg-status-warning/10 border-status-warning/20', label: t('queue.status.pending') },
    printing: { icon: Play, color: 'text-blue-400 bg-blue-400/10 border-blue-400/20', label: t('queue.status.printing') },
    completed: { icon: CheckCircle, color: 'text-status-ok bg-status-ok/10 border-status-ok/20', label: t('queue.status.completed') },
    failed: { icon: XCircle, color: 'text-status-error bg-status-error/10 border-status-error/20', label: t('queue.status.failed') },
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

// Bulk edit modal for multiple queue items
function BulkEditModal({
  selectedCount,
  printers,
  onSave,
  onClose,
  isSaving,
  canControlPrinter,
  t,
}: {
  selectedCount: number;
  printers: { id: number; name: string; nozzle_count?: number }[];
  onSave: (data: Partial<PrintQueueBulkUpdate>) => void;
  onClose: () => void;
  isSaving: boolean;
  canControlPrinter: boolean;
  t: (key: string, options?: Record<string, unknown>) => string;
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
  const [nozzleOffsetCali, setNozzleOffsetCali] = useState<boolean | 'unchanged'>('unchanged');

  // Show the dual-nozzle-only toggle when the user has at least one
  // dual-nozzle printer registered (H2D/H2D Pro/H2C/X2D). Single-nozzle
  // queues never see it — the MQTT layer ignores the field anyway.
  const hasDualNozzlePrinter = printers.some(p => p.nozzle_count === 2);

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
    if (nozzleOffsetCali !== 'unchanged') data.nozzle_offset_cali = nozzleOffsetCali;
    onSave(data);
  };

  const hasChanges = printerId !== 'unchanged' || manualStart !== 'unchanged' || autoOffAfter !== 'unchanged' ||
    requirePreviousSuccess !== 'unchanged' || bedLevelling !== 'unchanged' || flowCali !== 'unchanged' ||
    vibrationCali !== 'unchanged' || layerInspect !== 'unchanged' || timelapse !== 'unchanged' || useAms !== 'unchanged' ||
    nozzleOffsetCali !== 'unchanged';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
      <div className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">
            {t('queue.bulkEdit.title', { count: selectedCount })}
          </h2>
          <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded">
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <p className="text-sm text-bambu-gray">
            {t('queue.bulkEdit.description')}
          </p>

          {/* Printer Assignment */}
          <div>
            <label className="block text-sm font-medium text-white mb-2">{t('queue.bulkEdit.printer')}</label>
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
              <option value="unchanged">{t('queue.bulkEdit.noChange')}</option>
              <option value="null">{t('queue.filter.unassigned')}</option>
              {printers.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          {/* Queue Options */}
          <div>
            <label className="block text-sm font-medium text-white mb-2">{t('queue.bulkEdit.queueOptions')}</label>
            <div className="space-y-2">
              <TriStateToggle label={t('queue.bulkEdit.staged')} value={manualStart} onChange={setManualStart} t={t} />
              <TriStateToggle label={t('queue.bulkEdit.autoPowerOff')} value={autoOffAfter} onChange={setAutoOffAfter} disabled={!canControlPrinter} t={t} />
              <TriStateToggle label={t('queue.bulkEdit.requirePrevious')} value={requirePreviousSuccess} onChange={setRequirePreviousSuccess} t={t} />
            </div>
          </div>

          {/* Print Options */}
          <div>
            <label className="block text-sm font-medium text-white mb-2">{t('queue.bulkEdit.printOptions')}</label>
            <div className="space-y-2">
              <TriStateToggle label={t('queue.bulkEdit.bedLevelling')} value={bedLevelling} onChange={setBedLevelling} t={t} />
              <TriStateToggle label={t('queue.bulkEdit.flowCalibration')} value={flowCali} onChange={setFlowCali} t={t} />
              <TriStateToggle label={t('queue.bulkEdit.vibrationCalibration')} value={vibrationCali} onChange={setVibrationCali} t={t} />
              <TriStateToggle label={t('queue.bulkEdit.layerInspection')} value={layerInspect} onChange={setLayerInspect} t={t} />
              <TriStateToggle label={t('queue.bulkEdit.timelapse')} value={timelapse} onChange={setTimelapse} t={t} />
              <TriStateToggle label={t('queue.bulkEdit.useAms')} value={useAms} onChange={setUseAms} t={t} />
              {hasDualNozzlePrinter && (
                <TriStateToggle label={t('queue.bulkEdit.nozzleOffsetCali')} value={nozzleOffsetCali} onChange={setNozzleOffsetCali} t={t} />
              )}
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-3 p-4 border-t border-bambu-dark-tertiary">
          <Button variant="secondary" onClick={onClose}>{t('common.cancel')}</Button>
          <Button
            onClick={handleSave}
            disabled={!hasChanges || isSaving}
          >
            {isSaving ? t('common.saving') : t('queue.bulkEdit.applyChanges')}
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
  disabled,
  t,
}: {
  label: string;
  value: boolean | 'unchanged';
  onChange: (val: boolean | 'unchanged') => void;
  disabled?: boolean;
  t: (key: string) => string;
}) {
  return (
    <div className={`flex items-center justify-between py-1 ${disabled ? 'opacity-50' : ''}`}>
      <span className="text-sm text-bambu-gray">{label}</span>
      <div className="flex items-center gap-1 bg-bambu-dark rounded-lg p-0.5">
        <button
          onClick={() => onChange('unchanged')}
          disabled={disabled}
          className={`px-2 py-1 text-xs rounded ${value === 'unchanged' ? 'bg-bambu-dark-tertiary text-white' : 'text-bambu-gray hover:text-white'} disabled:cursor-not-allowed`}
        >
          —
        </button>
        <button
          onClick={() => onChange(false)}
          disabled={disabled}
          className={`px-2 py-1 text-xs rounded ${value === false ? 'bg-red-500/20 text-red-400' : 'text-bambu-gray hover:text-white'} disabled:cursor-not-allowed`}
        >
          {t('common.off')}
        </button>
        <button
          onClick={() => onChange(true)}
          disabled={disabled}
          className={`px-2 py-1 text-xs rounded ${value === true ? 'bg-bambu-green/20 text-bambu-green' : 'text-bambu-gray hover:text-white'} disabled:cursor-not-allowed`}
        >
          {t('common.on')}
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
  hasPermission,
  canModify,
  printerState,
  t,
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
  printerState?: string | null;
  t: (key: string, options?: Record<string, unknown>) => string;
}) {
  // Fetch printer status every 30 seconds while printing to monitor progress
  const { data: status } = useQuery({
    queryKey: ['printerStatus', item.printer_id],
    queryFn: () => api.getPrinterStatus(item.printer_id!),
    refetchInterval: 30000,
    enabled: item.printer_id != null && printerState === 'printing',
  });

  // Determine if we're printing a library file
  const isLibraryFile = !!item.library_file_id && !item.archive_id;
  // Fetch archive plate details. Skip when the linked archive has been
  // soft-deleted (#1348 follow-up): its 3MF is gone from disk so the
  // /plates endpoint just 404-storms the queue page.
  const { data: archivePlatesData } = useQuery({
    queryKey: ['archive-plates', item.archive_id],
    queryFn: () => api.getArchivePlates(item.archive_id!),
    enabled: !!item.archive_id && !isLibraryFile && !item.archive_deleted,
  });

  // Fetch library file plate details
  const { data: libraryPlatesData } = useQuery({
    queryKey: ['library-file-plates', item.library_file_id],
    queryFn: () => api.getLibraryFilePlates(item.library_file_id!),
    enabled: isLibraryFile && !!item.library_file_id,
  });

  // Combine plates data from either source
  const platesData = isLibraryFile ? libraryPlatesData : archivePlatesData;
  const plates = platesData?.plates ?? [];

  const canReorder = hasPermission('queue:reorder');
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id, disabled: item.status !== 'pending' || !canReorder });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const isPrinting = item.status === 'printing';
  const isPending = item.status === 'pending';
  const isHistory = ['completed', 'failed', 'skipped', 'cancelled'].includes(item.status);

  const isMobileSelectable = isPending && onToggleSelect;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`
        group relative bg-bambu-dark-secondary rounded-xl border transition-all duration-200
        border-l-[3px] ${
          isPrinting ? 'border-l-blue-500' :
          isPending ? 'border-l-yellow-500' :
          item.status === 'completed' ? 'border-l-emerald-500' :
          item.status === 'failed' ? 'border-l-red-500' :
          'border-l-gray-500'
        }
        ${isDragging ? 'opacity-50 scale-[1.02] shadow-xl z-50' : ''}
        ${isPrinting ? 'border-blue-500/30 bg-gradient-to-r from-blue-500/5 to-transparent' : ''}
        ${isSelected && isMobileSelectable ? 'sm:border-bambu-dark-tertiary border-bambu-green/40' : ''}
        ${!isSelected && !isPrinting ? 'border-bambu-dark-tertiary hover:border-bambu-dark-tertiary/80' : ''}
        ${isMobileSelectable ? 'sm:cursor-default' : ''}
      `}
      onClick={isMobileSelectable ? () => {
        if (window.innerWidth < 640) onToggleSelect();
      } : undefined}
    >
      {/* Mobile selected left accent bar */}
      {isMobileSelectable && isSelected && (
        <div className="sm:hidden absolute left-0 top-3 bottom-3 w-1 rounded-full bg-bambu-green" />
      )}

      <div className="flex items-start sm:items-center gap-2 sm:gap-4 p-3 sm:p-4">
        {/* Mobile selection indicator — left accent bar only, no tick */}

        {/* Selection checkbox for pending items - hidden on mobile, tap card instead */}
        {isPending && onToggleSelect && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggleSelect();
            }}
            className={`hidden sm:flex items-center justify-center w-6 h-6 rounded border transition-colors shrink-0 ${
              isSelected
                ? 'bg-bambu-green border-bambu-green text-white'
                : 'border-white/30 bg-black/30 hover:border-bambu-green/50'
            }`}
          >
            {isSelected && <Check className="w-4 h-4" />}
          </button>
        )}

        {/* Drag handle or position number - hidden on mobile */}
        {isPending ? (
          <div
            {...attributes}
            {...listeners}
            className="hidden sm:flex items-center justify-center w-8 h-8 rounded-lg bg-bambu-dark cursor-grab active:cursor-grabbing hover:bg-bambu-dark-tertiary transition-colors touch-manipulation shrink-0"
          >
            <GripVertical className="w-4 h-4 text-bambu-gray" />
          </div>
        ) : position !== undefined ? (
          <div className="hidden sm:flex items-center justify-center w-8 h-8 rounded-lg bg-bambu-dark text-bambu-gray text-sm font-medium shrink-0">
            #{position}
          </div>
        ) : (
          <div className="hidden sm:block w-8 shrink-0" />
        )}

        {/* Thumbnail - use plate-specific thumbnail if plate_id is set */}
        <div className="w-10 h-10 sm:w-14 sm:h-14 flex-shrink-0 bg-bambu-dark rounded-lg overflow-hidden">
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
              <Layers className="w-5 h-5 sm:w-6 sm:h-6" />
            </div>
          )}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <p className="text-sm sm:text-base text-white font-medium truncate">
              {item.archive_name || item.library_file_name || `File #${item.archive_id || item.library_file_id}`}
              {(platesData?.is_multi_plate ?? false) && item.plate_id !== undefined && item.plate_id !== null && ` • ${plates.find(plate => plate.index === item.plate_id)?.name || t('queue.plateNumber', { index: item.plate_id })}`}
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
            {item.batch_name && (
              <span className="flex-shrink-0 px-1.5 py-0.5 text-[10px] sm:text-xs bg-cyan-500/20 text-cyan-300 rounded border border-cyan-500/30">
                {item.batch_name}
              </span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs sm:text-sm text-bambu-gray">
            <span className={`flex items-center gap-1 sm:gap-1.5 ${item.printer_id === null && !item.target_model ? 'text-orange-400' : ''} ${item.target_model && !item.printer_id ? 'text-blue-400' : ''}`}>
              <Printer className="w-3 h-3 sm:w-3.5 sm:h-3.5" />
              <span className="truncate max-w-[120px] sm:max-w-none">
              {item.target_model && !item.printer_id
                ? `${t('queue.filter.any')} ${item.target_model}${item.target_location ? ` @ ${item.target_location}` : ''}${item.required_filament_types?.length ? ` (${item.required_filament_types.join(', ')})` : ''}`
                : item.printer_id === null
                  ? t('queue.filter.unassigned')
                  : (item.printer_name || `${t('common.printer')} #${item.printer_id}`)}
              </span>
            </span>
            {item.print_time_seconds && (
              <span className="flex items-center gap-1 sm:gap-1.5">
                <Timer className="w-3 h-3 sm:w-3.5 sm:h-3.5" />
                {formatDuration(item.print_time_seconds)}
              </span>
            )}
            {item.filament_used_grams && (
              <span className="flex items-center gap-1 sm:gap-1.5">
                <Weight className="w-3 h-3 sm:w-3.5 sm:h-3.5" />
                {formatWeight(item.filament_used_grams)}
              </span>
            )}
            {(() => {
              // Build plate badge so the user knows which plate to mount before
              // walking to the printer (#1281). Hidden when the 3MF doesn't
              // carry curr_bed_type or the slicer used an unknown label.
              const bed = getBedTypeInfo(item.bed_type);
              if (!bed) return null;
              return (
                <span className="flex items-center gap-1 sm:gap-1.5" title={bed.label}>
                  <img src={bed.icon} alt="" className="w-3.5 h-3.5 sm:w-4 sm:h-4 object-contain" />
                  <span className="truncate max-w-[120px]">{bed.label}</span>
                </span>
              );
            })()}
            {item.created_by_username && (
              <span className="hidden sm:flex items-center gap-1.5" title={t('queue.addedBy', { name: item.created_by_username })}>
                <User className="w-3.5 h-3.5" />
                {item.created_by_username}
              </span>
            )}
            {isPending && !item.manual_start && (
              <span className="flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                {item.scheduled_time
                  ? ((parseUTCDate(item.scheduled_time)?.getTime() ?? 0) - Date.now() < -60000
                      ? t?.('queue.time.overdue') ?? 'Overdue'
                      : formatRelativeTime(item.scheduled_time, timeFormat, t))
                  : t?.('queue.time.asap') ?? 'ASAP'}
              </span>
            )}
          </div>

          {/* Options badges */}
          <div className="flex flex-wrap items-center gap-1.5 sm:gap-2 mt-1.5 sm:mt-2">
            {item.manual_start && (
              <span className="text-[10px] sm:text-xs px-1.5 sm:px-2 py-0.5 bg-purple-500/10 text-purple-400 rounded-full border border-purple-500/20 flex items-center gap-1">
                <Hand className="w-2.5 h-2.5 sm:w-3 sm:h-3" />
                {t('queue.badges.staged')}
              </span>
            )}
            {item.require_previous_success && (
              <span className="text-[10px] sm:text-xs px-1.5 sm:px-2 py-0.5 bg-orange-500/10 text-orange-400 rounded-full border border-orange-500/20">
                {t('queue.badges.requiresPrevious')}
              </span>
            )}
            {item.auto_off_after && (
              <span className="text-[10px] sm:text-xs px-1.5 sm:px-2 py-0.5 bg-blue-500/10 text-blue-400 rounded-full border border-blue-500/20 flex items-center gap-1">
                <Power className="w-2.5 h-2.5 sm:w-3 sm:h-3" />
                {t('queue.badges.autoPowerOff')}
              </span>
            )}
            {item.gcode_injection && (
              <span className="text-[10px] sm:text-xs px-1.5 sm:px-2 py-0.5 bg-emerald-500/10 text-emerald-400 rounded-full border border-emerald-500/20 flex items-center gap-1">
                <Code className="w-2.5 h-2.5 sm:w-3 sm:h-3" />
                {t('queue.badges.gcodeInjection')}
              </span>
            )}
          </div>

          {/* Progress bar for printing items - TODO: integrate with WebSocket */}
          {isPrinting && status && (() => {
            // Gate progress/remaining/layer on printer actually running this print.
            // Between dispatch and RUNNING transition (H2D/P1 MQTT lag), status.progress
            // is stale from the previous print — showing 100% then snapping back to 0%
            // once the new print starts. Only trust these fields when state is active.
            const isActive = status.state === 'RUNNING' || status.state === 'PAUSE';
            const progress = isActive ? (status.progress || 0) : 0;
            const remaining = isActive ? status.remaining_time : null;
            const layerNum = isActive ? status.layer_num : null;
            const totalLayers = isActive ? status.total_layers : null;
            return (
              <div className="mt-2 sm:mt-3">
                <div className="flex items-center justify-between text-xs sm:text-sm">
                  <div className="flex-1 bg-bambu-dark-tertiary rounded-full h-1.5 sm:h-2 mr-3">
                    <div
                      className="bg-bambu-green h-1.5 sm:h-2 rounded-full transition-all"
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                  <span className="text-white">{Math.round(progress)}%</span>
                </div>
                <div className="flex flex-wrap items-center gap-2 sm:gap-3 mt-1.5 sm:mt-2 text-[10px] sm:text-xs text-bambu-gray">
                  {remaining != null && remaining > 0 && (
                    <>
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {formatDuration(remaining * 60)}
                      </span>
                      <span className="text-bambu-green font-medium" title={t('printers.estimatedCompletion')}>
                        ETA {formatETA(remaining, timeFormat, t)}
                      </span>
                    </>
                  )}
                  {layerNum != null && totalLayers != null && totalLayers > 0 && (
                    <span className="flex items-center gap-1">
                      <Layers className="w-3 h-3" />
                      {layerNum}/{totalLayers}
                    </span>
                  )}
                </div>
              </div>
            );
          })()}

          {/* Waiting reason for model-based assignments */}
          {item.waiting_reason && item.status === 'pending' && (
            <p className="text-[10px] sm:text-xs text-purple-400 mt-1.5 sm:mt-2 flex items-start gap-1">
              <AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />
              <span>{item.waiting_reason}</span>
            </p>
          )}

          {/* Filament-short flag from the dispatch pre-flight (#1496). */}
          {item.filament_short && item.status === 'pending' && (
            <p
              className="text-[10px] sm:text-xs text-yellow-400 mt-1.5 sm:mt-2 flex items-start gap-1"
              title={t('queue.filamentShort.rowTooltip')}
            >
              <AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />
              <span>{t('queue.filamentShort.rowBadge')}</span>
            </p>
          )}

          {/* Error message */}
          {item.error_message && (
            <p className="text-[10px] sm:text-xs text-red-400 mt-1.5 sm:mt-2 flex items-center gap-1">
              <AlertCircle className="w-3 h-3" />
              {item.error_message}
            </p>
          )}
        </div>

        {/* Status badge + Actions */}
        <div className="flex flex-col sm:flex-row items-end sm:items-center gap-2 sm:gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
          <StatusBadge status={item.status} waitingReason={item.waiting_reason} printerState={printerState} t={t} />

          <div className="flex items-center gap-0.5 sm:gap-1">
            {isPrinting && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onStop}
                disabled={!hasPermission('printers:control')}
                title={!hasPermission('printers:control') ? t('queue.permissions.noStopPrint') : t('queue.actions.stopPrint')}
                className="text-red-400 hover:text-red-300 hover:bg-red-500/10 p-1.5 sm:p-2"
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
                    title={!hasPermission('printers:control') ? t('queue.permissions.noStartPrint') : t('queue.actions.startPrint')}
                    className="text-bambu-green hover:text-bambu-green-light hover:bg-bambu-green/10 p-1.5 sm:p-2"
                  >
                    <Play className="w-4 h-4" />
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onEdit}
                  disabled={!canModify('queue', 'update', item.created_by_id)}
                  title={!canModify('queue', 'update', item.created_by_id) ? t('queue.permissions.noEdit') : t('common.edit')}
                  className="p-1.5 sm:p-2"
                >
                  <Pencil className="w-4 h-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onCancel}
                  disabled={!canModify('queue', 'delete', item.created_by_id)}
                  title={!canModify('queue', 'delete', item.created_by_id) ? t('queue.permissions.noCancel') : t('common.cancel')}
                  className="text-red-400 hover:text-red-300 hover:bg-red-500/10 p-1.5 sm:p-2"
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
                  title={!hasPermission('queue:create') ? t('queue.permissions.noRequeue') : t('queue.actions.requeue')}
                  className="text-bambu-green hover:text-bambu-green/80 hover:bg-bambu-green/10 p-1.5 sm:p-2"
                >
                  <RefreshCw className="w-4 h-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onRemove}
                  disabled={!canModify('queue', 'delete', item.created_by_id)}
                  title={!canModify('queue', 'delete', item.created_by_id) ? t('queue.permissions.noRemove') : t('common.remove')}
                  className="p-1.5 sm:p-2"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

type QueueRow =
  | { kind: 'item'; item: PrintQueueItem }
  | { kind: 'batch'; batchId: number; batchName: string; items: PrintQueueItem[] };

interface QueueRowRenderProps {
  row: QueueRow;
  collapsed: boolean;
  onToggleBatch?: () => void;
  onUngroup?: () => void;
  setEditItem: (item: PrintQueueItem) => void;
  setConfirmAction: (a: { type: 'cancel' | 'remove' | 'stop'; item: PrintQueueItem }) => void;
  startMutation: { mutate: (vars: { id: number; skipFilamentCheck?: boolean }) => void };
  selectedItems: number[];
  handleToggleSelect: (id: number) => void;
  timeFormat: TimeFormat;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  hasPermission: (p: any) => boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  canModify: (resource: any, action: any, createdById?: number | null) => boolean;
  t: (key: string, options?: Record<string, unknown>) => string;
  aggregateForRows: (rows: QueueRow[]) => { count: number; time: number; weight: number };
}

/** Renders either a single item or a collapsible batch group containing N
 *  sibling items. The batch parent shows aggregate stats; children render
 *  with the existing SortableQueueItem (only draggable inside the batch). */
function QueueRowRender(props: QueueRowRenderProps) {
  const {
    row,
    setEditItem,
    setConfirmAction,
    startMutation,
    selectedItems,
    handleToggleSelect,
    timeFormat,
    hasPermission,
    canModify,
    t,
  } = props;

  if (row.kind === 'item') {
    return (
      <SortableQueueItem
        item={row.item}
        onEdit={() => setEditItem(row.item)}
        onCancel={() => setConfirmAction({ type: 'cancel', item: row.item })}
        onRemove={() => {}}
        onStop={() => {}}
        onRequeue={() => {}}
        onStart={() => startMutation.mutate({ id: row.item.id })}
        timeFormat={timeFormat}
        isSelected={selectedItems.includes(row.item.id)}
        onToggleSelect={() => handleToggleSelect(row.item.id)}
        hasPermission={hasPermission}
        canModify={canModify}
        t={t}
      />
    );
  }

  return <SortableBatchRow {...props} />;
}

/** Batch parent header registered with dnd-kit so the whole group can be
 *  reordered as one unit. Drag handle lives in the header itself; children
 *  remain individually draggable while expanded for within-batch reorder. */
function SortableBatchRow({
  row,
  collapsed,
  onToggleBatch,
  onUngroup,
  setEditItem,
  setConfirmAction,
  startMutation,
  selectedItems,
  handleToggleSelect,
  timeFormat,
  hasPermission,
  canModify,
  t,
  aggregateForRows,
}: QueueRowRenderProps) {
  // Dispatcher (QueueRowRender) only mounts this with row.kind === 'batch';
  // narrow up-front so the hook below can reference batchId unconditionally.
  const batchRow = row as Extract<QueueRow, { kind: 'batch' }>;
  const canReorder = hasPermission('queue:reorder');
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: `batch-${batchRow.batchId}`, disabled: !canReorder });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const agg = aggregateForRows([batchRow]);
  const allChildIds = batchRow.items.map((i) => i.id);
  const allSelected = allChildIds.length > 0 && allChildIds.every((id) => selectedItems.includes(id));
  // Status rollup: worst-of-children (failed > printing > pending).
  const childStatuses = new Set(batchRow.items.map((i) => i.status));
  // We never put non-pending into a batch grouping but render defensively.
  const rollupStatus: PrintQueueItem['status'] = childStatuses.has('failed')
    ? 'failed'
    : childStatuses.has('printing')
      ? 'printing'
      : 'pending';
  const pendingChildren = batchRow.items.filter((i) => i.status === 'pending').length;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`bg-bambu-dark-secondary rounded-xl border border-l-[3px] border-l-cyan-400 border-bambu-dark-tertiary overflow-hidden ${
        isDragging ? 'opacity-50 scale-[1.01] shadow-xl z-50' : ''
      }`}
    >
      {/* Parent header */}
      <div className="flex items-center gap-2 sm:gap-3 p-3 sm:p-4">
        <button
          onClick={(e) => {
            e.stopPropagation();
            allChildIds.forEach((id) => {
              if (allSelected && selectedItems.includes(id)) {
                handleToggleSelect(id);
              } else if (!allSelected && !selectedItems.includes(id)) {
                handleToggleSelect(id);
              }
            });
          }}
          className={`hidden sm:flex items-center justify-center w-6 h-6 rounded border transition-colors shrink-0 ${
            allSelected
              ? 'bg-bambu-green border-bambu-green text-white'
              : 'border-white/30 bg-black/30 hover:border-bambu-green/50'
          }`}
          title={allSelected ? t('queue.bulkEdit.deselectAll') : t('queue.bulkEdit.selectAll')}
        >
          {allSelected && <Check className="w-4 h-4" />}
        </button>
        {canReorder && (
          <div
            {...attributes}
            {...listeners}
            className="hidden sm:flex items-center justify-center w-8 h-8 rounded-lg bg-bambu-dark cursor-grab active:cursor-grabbing hover:bg-bambu-dark-tertiary transition-colors touch-manipulation shrink-0"
            title={t('queue.batch.dragGroup', { defaultValue: 'Drag group' })}
          >
            <GripVertical className="w-4 h-4 text-bambu-gray" />
          </div>
        )}
        <button
          onClick={onToggleBatch}
          className="flex items-center justify-center w-8 h-8 rounded-lg bg-bambu-dark hover:bg-bambu-dark-tertiary transition-colors shrink-0"
          title={collapsed ? t('queue.batch.expand') : t('queue.batch.collapse')}
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4 text-bambu-gray" />
          ) : (
            <ChevronDown className="w-4 h-4 text-bambu-gray" />
          )}
        </button>
        <div className="w-10 h-10 sm:w-12 sm:h-12 flex-shrink-0 bg-bambu-dark rounded-lg flex items-center justify-center">
          {collapsed ? (
            <Package className="w-5 h-5 text-cyan-300" />
          ) : (
            <PackageOpen className="w-5 h-5 text-cyan-300" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <p className="text-sm sm:text-base text-white font-medium truncate">{batchRow.batchName}</p>
            <span className="flex-shrink-0 px-1.5 py-0.5 text-[10px] sm:text-xs bg-cyan-500/15 text-cyan-300 rounded border border-cyan-500/30">
              {t('queue.batch.label', { count: agg.count })}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs sm:text-sm text-bambu-gray">
            {agg.time > 0 && (
              <span className="flex items-center gap-1 sm:gap-1.5">
                <Timer className="w-3 h-3 sm:w-3.5 sm:h-3.5" />
                {formatDuration(agg.time)}
              </span>
            )}
            {agg.weight > 0 && (
              <span className="flex items-center gap-1 sm:gap-1.5">
                <Weight className="w-3 h-3 sm:w-3.5 sm:h-3.5" />
                {formatWeight(agg.weight)}
              </span>
            )}
            {pendingChildren > 0 && rollupStatus === 'pending' && (
              <span className="flex items-center gap-1 sm:gap-1.5 text-yellow-400">
                <Clock className="w-3 h-3 sm:w-3.5 sm:h-3.5" />
                {t('queue.batch.pendingCount', { count: pendingChildren })}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {onUngroup && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onUngroup}
              title={t('queue.batch.ungroup')}
              className="text-cyan-300 hover:text-cyan-200 hover:bg-cyan-500/10 p-1.5 sm:p-2"
            >
              <Ungroup className="w-4 h-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Children (only when expanded) */}
      {!collapsed && (
        <div className="border-t border-bambu-dark-tertiary bg-black/20 p-2 sm:p-3 space-y-2">
          {batchRow.items.map((child) => (
            <SortableQueueItem
              key={child.id}
              item={child}
              onEdit={() => setEditItem(child)}
              onCancel={() => setConfirmAction({ type: 'cancel', item: child })}
              onRemove={() => {}}
              onStop={() => {}}
              onRequeue={() => {}}
              onStart={() => startMutation.mutate({ id: child.id })}
              timeFormat={timeFormat}
              isSelected={selectedItems.includes(child.id)}
              onToggleSelect={() => handleToggleSelect(child.id)}
              hasPermission={hasPermission}
              canModify={canModify}
              t={t}
            />
          ))}
        </div>
      )}
    </div>
  );
}

type HistoryRow =
  | { kind: 'item'; item: PrintQueueItem }
  | { kind: 'batch'; batchId: number; batchName: string; items: PrintQueueItem[] };

interface HistorySectionProps {
  items: PrintQueueItem[];
  collapsed: boolean;
  sortBy: 'date' | 'name' | 'printer';
  sortAsc: boolean;
  onSortByChange: (v: 'date' | 'name' | 'printer') => void;
  onSortAscToggle: () => void;
  onRemove: (item: PrintQueueItem) => void;
  onRequeue: (item: PrintQueueItem) => void;
  timeFormat: TimeFormat;
  batchCollapsed: Record<number, boolean>;
  toggleBatchCollapsed: (id: number) => void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  hasPermission: (p: any) => boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  canModify: (resource: any, action: any, createdById?: number | null) => boolean;
  t: (key: string, options?: Record<string, unknown>) => string;
}

function HistorySection({
  items,
  sortBy,
  sortAsc,
  onSortByChange,
  onSortAscToggle,
  onRemove,
  onRequeue,
  timeFormat,
  batchCollapsed,
  toggleBatchCollapsed,
  hasPermission,
  canModify,
  t,
}: HistorySectionProps) {
  if (items.length === 0) {
    return (
      <Card className="p-12 text-center border-dashed">
        <ListOrdered className="w-16 h-16 text-bambu-gray mx-auto mb-4 opacity-50" />
        <h3 className="text-xl font-medium text-white mb-2">{t('queue.history.emptyTitle')}</h3>
        <p className="text-bambu-gray max-w-md mx-auto">{t('queue.history.emptyDescription')}</p>
      </Card>
    );
  }

  // Group siblings sharing a batch_id into a single collapsible row.
  // First-seen order is preserved for batches; items keep their sorted
  // position from the parent's sort selector.
  const rows: HistoryRow[] = [];
  const seenBatches = new Set<number>();
  for (const item of items.slice(0, 50)) {
    if (item.batch_id != null) {
      if (seenBatches.has(item.batch_id)) continue;
      seenBatches.add(item.batch_id);
      const siblings = items.filter((s) => s.batch_id === item.batch_id);
      rows.push({
        kind: 'batch',
        batchId: item.batch_id,
        batchName: item.batch_name || t('queue.batch.defaultName'),
        items: siblings,
      });
    } else {
      rows.push({ kind: 'item', item });
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3 sm:mb-4">
        <h2 className="text-base sm:text-lg font-semibold text-white flex items-center gap-2">
          {t('queue.sections.history')}
          <span className="text-xs sm:text-sm font-normal text-bambu-gray">
            ({t('queue.itemCount', { count: items.length })})
          </span>
        </h2>
        <div className="flex items-center gap-2">
          <select
            className="px-2 sm:px-3 py-1.5 text-xs sm:text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            value={sortBy}
            onChange={(e) => onSortByChange(e.target.value as 'date' | 'name' | 'printer')}
          >
            <option value="date">{t('queue.sort.byDate')}</option>
            <option value="name">{t('queue.sort.byName')}</option>
            <option value="printer">{t('queue.sort.byPrinter')}</option>
          </select>
          <Button
            variant="ghost"
            size="sm"
            onClick={onSortAscToggle}
            title={sortAsc ? t('queue.sort.ascendingOldest') : t('queue.sort.descendingNewest')}
            className="px-2"
          >
            {sortAsc ? <ArrowUp className="w-4 h-4" /> : <ArrowDown className="w-4 h-4" />}
          </Button>
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
        {rows.map((row) => {
          if (row.kind === 'item') {
            return (
              <CompactHistoryRow
                key={row.item.id}
                item={row.item}
                onRemove={() => onRemove(row.item)}
                onRequeue={() => onRequeue(row.item)}
                timeFormat={timeFormat}
                hasPermission={hasPermission}
                canModify={canModify}
                t={t}
              />
            );
          }
          // Batch group — spans the full grid width so it visually anchors
          // its children below it. The children themselves render in the
          // same responsive grid pattern inside the expanded body.
          const collapsed = batchCollapsed[row.batchId] ?? true;
          const completed = row.items.filter((i) => i.status === 'completed').length;
          const failed = row.items.filter((i) => i.status === 'failed').length;
          const skipped = row.items.filter((i) => i.status === 'skipped').length;
          const cancelled = row.items.filter((i) => i.status === 'cancelled').length;
          const latest = row.items
            .map((i) => i.completed_at || i.created_at)
            .filter((v): v is string => !!v)
            .sort()
            .at(-1);
          return (
            <div
              key={`batch-${row.batchId}`}
              className="md:col-span-2 xl:col-span-3 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary border-l-[3px] border-l-cyan-400"
            >
              <button
                onClick={() => toggleBatchCollapsed(row.batchId)}
                className="w-full flex items-center gap-2 sm:gap-3 px-3 py-2 hover:bg-bambu-dark/30 transition-colors text-left"
                title={collapsed ? t('queue.batch.expand') : t('queue.batch.collapse')}
              >
                {collapsed ? (
                  <ChevronRight className="w-4 h-4 text-bambu-gray shrink-0" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-bambu-gray shrink-0" />
                )}
                {collapsed ? (
                  <Package className="w-5 h-5 text-cyan-300 shrink-0" />
                ) : (
                  <PackageOpen className="w-5 h-5 text-cyan-300 shrink-0" />
                )}
                <span className="text-sm text-white font-medium truncate min-w-0 flex-1">
                  {row.batchName}
                </span>
                <div className="flex items-center gap-2 text-xs text-bambu-gray shrink-0">
                  {completed > 0 && (
                    <span className="flex items-center gap-1 text-emerald-400">
                      <CheckCircle className="w-3 h-3" />
                      {completed}
                    </span>
                  )}
                  {failed > 0 && (
                    <span className="flex items-center gap-1 text-red-400">
                      <XCircle className="w-3 h-3" />
                      {failed}
                    </span>
                  )}
                  {skipped > 0 && (
                    <span className="flex items-center gap-1 text-orange-400">
                      <SkipForward className="w-3 h-3" />
                      {skipped}
                    </span>
                  )}
                  {cancelled > 0 && (
                    <span className="flex items-center gap-1 text-gray-400">
                      <Ban className="w-3 h-3" />
                      {cancelled}
                    </span>
                  )}
                  <span title={latest ?? undefined}>
                    {latest ? formatRelativeTime(latest, timeFormat, t) : null}
                  </span>
                </div>
              </button>
              {!collapsed && (
                <div className="border-t border-bambu-dark-tertiary bg-black/20 p-2 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
                  {row.items.map((child) => (
                    <CompactHistoryRow
                      key={child.id}
                      item={child}
                      onRemove={() => onRemove(child)}
                      onRequeue={() => onRequeue(child)}
                      timeFormat={timeFormat}
                      hasPermission={hasPermission}
                      canModify={canModify}
                      t={t}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}
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
  const [selectedItems, setSelectedItems] = useState<number[]>([]);
  const [showBulkEditModal, setShowBulkEditModal] = useState(false);
  // #1818: per-printer Resume-after-failure confirm modal. Tracks which
  // printer's gate the user is about to clear; null when no modal is open.
  const [resumeConfirm, setResumeConfirm] = useState<{
    printerId: number;
    printerName: string;
    skippedCount: number;
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
  // historyCollapsed legacy state retained only for localStorage migration; the
  // History tab renders unconditionally so this no longer drives the UI.
  // Tabbed page structure: Active queue stays as the main view; History
  // and Timeline split off. Persists per-user via localStorage.
  const [activeTab, setActiveTab] = useState<'queue' | 'history' | 'timeline'>(() => {
    const saved = localStorage.getItem('queue.activeTab');
    if (saved === 'history' || saved === 'timeline') return saved;
    return 'queue';
  });
  // Active-tab layout toggle. "position" = today's flat list; "printer"
  // groups items under per-printer section headers with aggregate stats.
  const [activeLayout, setActiveLayout] = useState<'position' | 'printer'>(() => {
    const saved = localStorage.getItem('queue.activeLayout');
    return saved === 'printer' ? 'printer' : 'position';
  });
  // Per-batch collapse state, keyed by batch_id. Default = collapsed
  // (matches the SimplyPrint/Files convention — show the rollup first).
  const [batchCollapsed, setBatchCollapsed] = useState<Record<number, boolean>>(() => {
    try {
      const saved = localStorage.getItem('queue.batchCollapsed');
      return saved ? JSON.parse(saved) : {};
    } catch {
      return {};
    }
  });
  // Multi-drag bookkeeping for DragOverlay. Numeric for single items, string
  // `batch-<id>` when a whole group is being dragged.
  const [activeDragId, setActiveDragId] = useState<number | string | null>(null);
  // "Group as batch" modal.
  const [groupBatchModal, setGroupBatchModal] = useState(false);
  // Ungroup confirm.
  const [ungroupBatchId, setUngroupBatchId] = useState<number | null>(null);

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

  useEffect(() => {
    localStorage.setItem('queue.activeTab', activeTab);
  }, [activeTab]);

  useEffect(() => {
    localStorage.setItem('queue.activeLayout', activeLayout);
  }, [activeLayout]);

  useEffect(() => {
    localStorage.setItem('queue.batchCollapsed', JSON.stringify(batchCollapsed));
  }, [batchCollapsed]);

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

  const sjfMutation = useMutation({
    mutationFn: (enabled: boolean) => api.updateSettings({ queue_shortest_first: enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
    },
  });

  const cancelMutation = useMutation({
    mutationFn: (id: number) => api.cancelQueueItem(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('queue.toast.cancelled'));
    },
    onError: () => showToast(t('queue.toast.cancelFailed'), 'error'),
  });

  const removeMutation = useMutation({
    mutationFn: (id: number) => api.removeFromQueue(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('queue.toast.removed'));
    },
    onError: () => showToast(t('queue.toast.removeFailed'), 'error'),
  });

  const stopMutation = useMutation({
    mutationFn: (id: number) => api.stopQueueItem(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('queue.toast.stopped'));
    },
    onError: () => showToast(t('queue.toast.stopFailed'), 'error'),
  });

  // Filament-deficit confirmation state (#1496). When the backend returns
  // 409 with `code=insufficient_filament` we stash the deficit + item id
  // here; the modal at the bottom of the page reads it and the "Print
  // Anyway" path re-issues the start with `skipFilamentCheck=true`.
  const [filamentShortConfirm, setFilamentShortConfirm] = useState<{
    itemId: number;
    deficit: Array<{
      slot_id: number;
      required_grams: number;
      remaining_grams: number | null;
      filament_type?: string | null;
    }>;
  } | null>(null);

  const startMutation = useMutation({
    mutationFn: ({ id, skipFilamentCheck }: { id: number; skipFilamentCheck?: boolean }) =>
      api.startQueueItem(id, { skipFilamentCheck }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('queue.toast.released'));
      setFilamentShortConfirm(null);
    },
    onError: (error: unknown, variables) => {
      if (error instanceof ApiError && error.status === 409 && error.code === 'insufficient_filament') {
        const deficitRaw = (error.detail?.deficit ?? []) as Array<{
          slot_id: number;
          required_grams: number;
          remaining_grams: number | null;
          filament_type?: string | null;
        }>;
        setFilamentShortConfirm({ itemId: variables.id, deficit: deficitRaw });
        return;
      }
      showToast(t('queue.toast.startFailed'), 'error');
    },
  });

  const reorderMutation = useMutation({
    mutationFn: (items: { id: number; position: number }[]) => api.reorderQueue(items),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
    },
    onError: () => showToast(t('queue.toast.reorderFailed'), 'error'),
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
      showToast(t('queue.toast.historyCleared', { count }));
    },
    onError: () => showToast(t('queue.toast.clearHistoryFailed'), 'error'),
  });

  const bulkUpdateMutation = useMutation({
    mutationFn: (data: PrintQueueBulkUpdate) => api.bulkUpdateQueue(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      setSelectedItems([]);
      setShowBulkEditModal(false);
      showToast(result.message);
    },
    onError: () => showToast(t('queue.toast.updateFailed'), 'error'),
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
      showToast(t('queue.toast.bulkCancelled', { count }));
    },
    onError: () => showToast(t('queue.toast.bulkCancelFailed'), 'error'),
  });

  const resumeAfterFailureMutation = useMutation({
    mutationFn: (printerId: number) => api.resumeQueueAfterFailure(printerId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      setResumeConfirm(null);
      showToast(
        t('queue.toast.resumedAfterFailure', {
          restored: result.restored,
          acknowledged: result.acknowledged,
        }),
      );
    },
    onError: () => showToast(t('queue.toast.resumeAfterFailureFailed'), 'error'),
  });

  const createBatchMutation = useMutation({
    mutationFn: (data: { name: string; item_ids: number[] }) => api.createBatch(data),
    onSuccess: (batch) => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      setSelectedItems([]);
      setGroupBatchModal(false);
      // New batches start expanded so the user sees what they just grouped.
      setBatchCollapsed((prev) => ({ ...prev, [batch.id]: false }));
      showToast(t('queue.toast.batchCreated', { name: batch.name }));
    },
    onError: () => showToast(t('queue.toast.batchCreateFailed'), 'error'),
  });

  const ungroupBatchMutation = useMutation({
    mutationFn: (id: number) => api.ungroupBatch(id),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      setUngroupBatchId(null);
      showToast(t('queue.toast.batchUngrouped', { count: result.ungrouped_count }));
    },
    onError: () => showToast(t('queue.toast.batchUngroupFailed'), 'error'),
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
      const time = parseUTCDate(item.scheduled_time)?.getTime() ?? 0;
      // Placeholder dates (> 6 months out) are treated as ASAP
      const sixMonthsFromNow = Date.now() + (180 * 24 * 60 * 60 * 1000);
      return time > sixMonthsFromNow ? 0 : time;
    };

    // When SJF is enabled, override sort to match scheduler order
    if (settings?.queue_shortest_first) {
      return [...items].sort((a, b) => {
        // Group by printer first (nulls = model-based, grouped by target_model)
        const aPrinter = a.printer_id ?? -(a.target_model?.charCodeAt(0) ?? 0);
        const bPrinter = b.printer_id ?? -(b.target_model?.charCodeAt(0) ?? 0);
        if (aPrinter !== bPrinter) return aPrinter - bPrinter;
        // Within same printer/model: jumped items first (starvation guard)
        const aJumped = a.been_jumped ? 1 : 0;
        const bJumped = b.been_jumped ? 1 : 0;
        if (aJumped !== bJumped) return bJumped - aJumped;
        // Shortest print time next (nulls last)
        const aTime = a.print_time_seconds ?? Infinity;
        const bTime = b.print_time_seconds ?? Infinity;
        if (aTime !== bTime) return aTime - bTime;
        // Position as tiebreaker
        return a.position - b.position;
      });
    }

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
  }, [queue, pendingSortBy, pendingSortAsc, matchesLocationFilter, filterLocation, settings?.queue_shortest_first]);

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

  // Get unique printer IDs from active items to fetch their statuses
  const activePrinterIds = useMemo(() => {
    const ids = new Set<number>();
    activeItems.forEach(item => {
      if (item.printer_id) ids.add(item.printer_id);
    });
    return Array.from(ids);
  }, [activeItems]);

  // Fetch printer statuses for printers with active jobs
  const printerStatusQueries = useQueries({
    queries: activePrinterIds.map(printerId => ({
      queryKey: ['printerStatus', printerId],
      queryFn: () => api.getPrinterStatus(printerId),
      refetchInterval: 5000,
    })),
  });

  // Build a map of printer_id -> state for quick lookup
  const printerStateMap = useMemo(() => {
    const map: Record<number, string | null> = {};
    activePrinterIds.forEach((printerId, index) => {
      const result = printerStatusQueries[index];
      if (result?.data?.state) {
        map[printerId] = result.data.state;
      }
    });
    return map;
  }, [activePrinterIds, printerStatusQueries]);

  // Build a map of printer_id -> full status for timeline view
  const printerStatusMap = useMemo(() => {
    const map: Record<number, { progress?: number; remaining_time?: number; state?: string }> = {};
    activePrinterIds.forEach((printerId, index) => {
      const result = printerStatusQueries[index];
      if (result?.data) {
        map[printerId] = {
          progress: result.data.progress ?? undefined,
          remaining_time: result.data.remaining_time ?? undefined,
          state: result.data.state ?? undefined,
        };
      }
    });
    return map;
  }, [activePrinterIds, printerStatusQueries]);

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
        cmp = (parseUTCDate(b.completed_at || b.created_at)?.getTime() ?? 0) - (parseUTCDate(a.completed_at || a.created_at)?.getTime() ?? 0);
      }
      return historySortAsc ? -cmp : cmp;
    });
  }, [queue, historySortBy, historySortAsc, matchesLocationFilter, filterLocation]);

  // Calculate total queue time
  const totalQueueTime = useMemo(() => {
    return pendingItems.reduce((acc, item) => acc + (item.print_time_seconds || 0), 0);
  }, [pendingItems]);

  // Calculate total material weight
  const totalWeight = useMemo(() => {
    return pendingItems.reduce((acc, item) => acc + (item.filament_used_grams || 0), 0);
  }, [pendingItems]);

  const handleDragStart = (event: DragStartEvent) => {
    const id = event.active.id;
    setActiveDragId(typeof id === 'number' || typeof id === 'string' ? id : null);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveDragId(null);
    if (!over || active.id === over.id) return;

    // Resolve dragged source → movingIds (preserving order from pendingItems).
    //   - `batch-<id>`: every child of that batch, in their current order
    //   - selected + dragged is one of them: contiguous multi-drag block
    //   - otherwise: single row
    let movingIds: number[];
    const activeId = active.id;
    if (typeof activeId === 'string' && activeId.startsWith('batch-')) {
      const batchId = Number(activeId.slice('batch-'.length));
      movingIds = pendingItems.filter((i) => i.batch_id === batchId).map((i) => i.id);
    } else {
      const draggedId = activeId as number;
      movingIds = selectedItems.includes(draggedId) && selectedItems.length > 1
        ? selectedItems.slice().sort((a, b) => {
            const ai = pendingItems.findIndex((i) => i.id === a);
            const bi = pendingItems.findIndex((i) => i.id === b);
            return ai - bi;
          })
        : [draggedId];
    }
    if (movingIds.length === 0) return;

    // Resolve drop target → index inside pendingItems. A `batch-<id>` drop
    // target anchors at the batch's first child, so dropping above another
    // batch lands the moving block immediately before it.
    let overIndex: number;
    const overId = over.id;
    if (typeof overId === 'string' && overId.startsWith('batch-')) {
      const overBatchId = Number(overId.slice('batch-'.length));
      overIndex = pendingItems.findIndex((i) => i.batch_id === overBatchId);
    } else {
      overIndex = pendingItems.findIndex((i) => i.id === overId);
    }
    if (overIndex === -1) return;

    // Remove the moving items, then re-insert at overIndex (adjusted).
    const overAnchor = pendingItems[overIndex];
    const remaining = pendingItems.filter((i) => !movingIds.includes(i.id));
    let insertAt = remaining.findIndex((i) => i.id === overAnchor.id);
    if (insertAt === -1) insertAt = overIndex;
    // If dragging downward across the drop target, insert AFTER it; upward
    // = before. dnd-kit's `over` is the row under the pointer, not the gap.
    const firstMovingIndex = pendingItems.findIndex((i) => i.id === movingIds[0]);
    if (firstMovingIndex < overIndex) insertAt += 1;
    const reordered = [
      ...remaining.slice(0, insertAt),
      ...movingIds
        .map((id) => pendingItems.find((i) => i.id === id))
        .filter((x): x is PrintQueueItem => !!x),
      ...remaining.slice(insertAt),
    ];

    const updates = reordered.map((item, index) => ({
      id: item.id,
      position: index + 1,
    }));
    reorderMutation.mutate(updates);
  };

  // Group pending items by batch_id. Items with batch_id null render as
  // standalone rows; items sharing a batch_id render as a collapsible
  // group keyed by that id. Items inside a group keep their original
  // relative order from pendingItems.
  const groupedRows = useMemo<QueueRow[]>(() => {
    const rows: QueueRow[] = [];
    const seenBatches = new Set<number>();
    for (const item of pendingItems) {
      if (item.batch_id != null) {
        if (seenBatches.has(item.batch_id)) continue;
        seenBatches.add(item.batch_id);
        const siblings = pendingItems.filter((s) => s.batch_id === item.batch_id);
        rows.push({
          kind: 'batch',
          batchId: item.batch_id,
          batchName: item.batch_name || t('queue.batch.defaultName'),
          items: siblings,
        });
      } else {
        rows.push({ kind: 'item', item });
      }
    }
    return rows;
  }, [pendingItems, t]);

  // SortableContext ID list.
  // - Standalone pending items: their numeric id.
  // - Batch parents: the synthetic `batch-<id>` string, always present so the
  //   group itself is draggable and acts as a drop target whether collapsed
  //   or expanded.
  // - Expanded batch children: their numeric id, so within-batch reorder
  //   keeps working. Collapsed children are detached from the DOM and
  //   intentionally omitted to keep dnd-kit's collision resolver clean.
  const sortableIds = useMemo<(number | string)[]>(() => {
    const ids: (number | string)[] = [];
    for (const row of groupedRows) {
      if (row.kind === 'item') {
        ids.push(row.item.id);
      } else {
        ids.push(`batch-${row.batchId}`);
        const collapsed = batchCollapsed[row.batchId] ?? true;
        if (!collapsed) {
          for (const child of row.items) ids.push(child.id);
        }
      }
    }
    return ids;
  }, [groupedRows, batchCollapsed]);

  // Items already in a batch can't be grouped; "Group as batch" only shows
  // when 2+ ungrouped items are selected.
  const canGroupSelected = useMemo(() => {
    if (selectedItems.length < 2) return false;
    return selectedItems.every((id) => {
      const item = pendingItems.find((p) => p.id === id);
      return item && item.batch_id == null;
    });
  }, [selectedItems, pendingItems]);

  const toggleBatchCollapsed = (id: number) => {
    setBatchCollapsed((prev) => ({ ...prev, [id]: !(prev[id] ?? true) }));
  };

  // Group by printer view. Items are bucketed by printer_id (null = model
  // assignment or unassigned, keyed by target_model or "unassigned").
  type PrinterBucket = {
    key: string;
    printerId: number | null;
    targetModel: string | null;
    label: string;
    isUnassigned: boolean;
    rows: QueueRow[];
  };

  const printerBuckets = useMemo<PrinterBucket[]>(() => {
    const buckets = new Map<string, PrinterBucket>();
    const bucketForItem = (item: PrintQueueItem): { key: string; label: string; printerId: number | null; targetModel: string | null; isUnassigned: boolean } => {
      if (item.printer_id) {
        return {
          key: `printer:${item.printer_id}`,
          label: item.printer_name || `Printer #${item.printer_id}`,
          printerId: item.printer_id,
          targetModel: null,
          isUnassigned: false,
        };
      }
      if (item.target_model) {
        return {
          key: `model:${item.target_model}`,
          label: `${t('queue.filter.any')} ${item.target_model}`,
          printerId: null,
          targetModel: item.target_model,
          isUnassigned: false,
        };
      }
      return {
        key: 'unassigned',
        label: t('queue.filter.unassigned'),
        printerId: null,
        targetModel: null,
        isUnassigned: true,
      };
    };

    for (const row of groupedRows) {
      const representative = row.kind === 'item' ? row.item : row.items[0];
      if (!representative) continue;
      const meta = bucketForItem(representative);
      if (!buckets.has(meta.key)) {
        buckets.set(meta.key, { ...meta, rows: [] });
      }
      buckets.get(meta.key)!.rows.push(row);
    }
    return Array.from(buckets.values()).sort((a, b) => {
      if (a.isUnassigned && !b.isUnassigned) return 1;
      if (!a.isUnassigned && b.isUnassigned) return -1;
      return a.label.localeCompare(b.label);
    });
  }, [groupedRows, t]);

  // #1818: printers whose queue is gated by a prior failure that's poisoning
  // downstream `require_previous_success` items. We surface a per-printer
  // Resume banner above the active queue so the user can clear the gate +
  // restore the skipped jobs in one click, without re-queuing each one.
  // Detection key: skipped + the scheduler's exact gate string. Other skip
  // reasons (filament deficit, etc.) get their own UX and stay untouched.
  const gateBlockedPrinters = useMemo<
    Array<{ printerId: number; printerName: string; skippedCount: number }>
  >(() => {
    const counts = new Map<number, { name: string; count: number }>();
    queue?.forEach((item) => {
      if (
        item.status === 'skipped' &&
        item.error_message === 'Previous print failed or was aborted' &&
        item.printer_id
      ) {
        const existing = counts.get(item.printer_id);
        if (existing) {
          existing.count += 1;
        } else {
          counts.set(item.printer_id, {
            name: item.printer_name || `Printer #${item.printer_id}`,
            count: 1,
          });
        }
      }
    });
    return Array.from(counts.entries())
      .map(([printerId, { name, count }]) => ({
        printerId,
        printerName: name,
        skippedCount: count,
      }))
      .sort((a, b) => a.printerName.localeCompare(b.printerName));
  }, [queue]);

  const aggregateForRows = (rows: QueueRow[]) => {
    let count = 0;
    let time = 0;
    let weight = 0;
    for (const row of rows) {
      const items = row.kind === 'item' ? [row.item] : row.items;
      for (const item of items) {
        count += 1;
        time += item.print_time_seconds || 0;
        weight += item.filament_used_grams || 0;
      }
    }
    return { count, time, weight };
  };

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <ListOrdered className="w-7 h-7 text-bambu-green" />
            {t('queue.title')}
          </h1>
          <p className="text-bambu-gray mt-1">{t('queue.subtitle')}</p>
        </div>
      </div>

      {/* Tab strip — Active queue is the main view; History and Timeline
          live in their own tabs so the queue page stays focused. */}
      <div className="flex gap-1 border-b border-bambu-dark-tertiary mb-6 overflow-x-auto">
        {([
          { id: 'queue' as const, label: t('queue.tabs.queue'), icon: Clock, count: pendingItems.length + activeItems.length },
          { id: 'history' as const, label: t('queue.tabs.history'), icon: ListOrdered, count: historyItems.length },
          { id: 'timeline' as const, label: t('queue.tabs.timeline'), icon: GanttChart, count: null as number | null },
        ]).map(({ id, label, icon: Icon, count }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`px-4 py-2.5 text-sm flex items-center gap-2 border-b-2 -mb-px transition-colors whitespace-nowrap ${
              activeTab === id
                ? 'text-white border-bambu-green font-medium'
                : 'text-bambu-gray border-transparent hover:text-white'
            }`}
          >
            <Icon className="w-4 h-4" />
            {label}
            {count !== null && count > 0 && (
              <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                activeTab === id ? 'bg-bambu-green/20 text-bambu-green' : 'bg-bambu-dark-tertiary text-bambu-gray'
              }`}>
                {count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Summary Stats */}
      <QueueStatsBar
        activeCount={activeItems.length}
        pendingCount={pendingItems.length}
        totalTime={totalQueueTime}
        totalWeight={totalWeight}
        historyCount={historyItems.length}
        t={t}
      />

      {/* #1818: Resume-after-failure banner. One row per printer whose queue
          is gated by a prior failed/aborted print. Visible regardless of
          tab/layout so the user can clear the gate without hunting for
          skipped items. Hidden entirely when no gates are active. */}
      {activeTab === 'queue' && gateBlockedPrinters.length > 0 && hasPermission('queue:update_all' as Permission) && (
        <div className="mb-4 space-y-2">
          {gateBlockedPrinters.map(({ printerId, printerName, skippedCount }) => (
            <div
              key={printerId}
              className="flex items-center gap-3 px-4 py-3 bg-orange-500/10 border border-orange-500/30 rounded-lg"
            >
              <AlertCircle className="w-5 h-5 text-orange-400 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm text-orange-200">
                  {t('queue.resumeAfterFailure.banner', {
                    printer: printerName,
                    count: skippedCount,
                  })}
                </div>
                <div className="text-xs text-orange-200/70 mt-0.5">
                  {t('queue.resumeAfterFailure.bannerHint')}
                </div>
              </div>
              <button
                onClick={() =>
                  setResumeConfirm({ printerId, printerName, skippedCount })
                }
                className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-500/20 hover:bg-orange-500/30 text-orange-100 text-sm rounded-md border border-orange-500/40 transition-colors flex-shrink-0"
              >
                <PlayCircle className="w-4 h-4" />
                {t('queue.resumeAfterFailure.button')}
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 sm:gap-4 mb-6">
        <select
          className="px-2 sm:px-3 py-2 text-sm sm:text-base bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none min-w-0 flex-1 sm:flex-none"
          value={filterPrinter === -1 ? 'unassigned' : (filterPrinter || '')}
          onChange={(e) => {
            const val = e.target.value;
            if (val === 'unassigned') setFilterPrinter(-1);
            else if (val === '') setFilterPrinter(null);
            else setFilterPrinter(Number(val));
          }}
        >
          <option value="">{t('queue.filter.allPrinters')}</option>
          <option value="unassigned">{t('queue.filter.unassigned')}</option>
          {printers?.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>

        <select
          className="px-2 sm:px-3 py-2 text-sm sm:text-base bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none min-w-0 flex-1 sm:flex-none"
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="">{t('queue.filter.allStatus')}</option>
          <option value="pending">{t('queue.status.pending')}</option>
          <option value="printing">{t('queue.status.printing')}</option>
          <option value="completed">{t('queue.status.completed')}</option>
          <option value="failed">{t('queue.status.failed')}</option>
          <option value="skipped">{t('queue.status.skipped')}</option>
          <option value="cancelled">{t('queue.status.cancelled')}</option>
        </select>

        {uniqueLocations.length > 0 && (
          <select
            className="px-2 sm:px-3 py-2 text-sm sm:text-base bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none min-w-0 flex-1 sm:flex-none"
            value={filterLocation}
            onChange={(e) => setFilterLocation(e.target.value)}
          >
            <option value="">{t('queue.filter.allLocations')}</option>
            {uniqueLocations.map((loc) => (
              <option key={loc} value={loc}>{loc}</option>
            ))}
          </select>
        )}

        <div className="hidden sm:block flex-1" />

        {activeTab === 'history' && historyItems.length > 0 && (
          <Button
            className="w-full sm:w-auto"
            variant="secondary"
            size="sm"
            onClick={() => setShowClearHistoryConfirm(true)}
            disabled={!hasPermission('queue:delete_all')}
            title={!hasPermission('queue:delete_all') ? t('queue.permissions.noClearHistory') : undefined}
          >
            <Trash2 className="w-4 h-4" />
            {t('queue.clearHistory')}
          </Button>
        )}
      </div>

      {/* Queue-tab controls: layout toggle (Position / Printer) + SJF.
          Hidden on History/Timeline tabs since they don't apply. */}
      {activeTab === 'queue' && (
        <div className="flex flex-wrap items-center gap-3 mb-6">
          <div className="inline-flex items-center bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-0.5">
            <button
              className={`px-3 py-1.5 text-xs sm:text-sm rounded-md transition-colors flex items-center gap-1.5 ${
                activeLayout === 'position' ? 'bg-bambu-dark-tertiary text-white' : 'text-bambu-gray hover:text-white'
              }`}
              onClick={() => setActiveLayout('position')}
              title={t('queue.layout.flatList')}
            >
              <List className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">{t('queue.layout.flatList')}</span>
            </button>
            <button
              className={`px-3 py-1.5 text-xs sm:text-sm rounded-md transition-colors flex items-center gap-1.5 ${
                activeLayout === 'printer' ? 'bg-bambu-dark-tertiary text-white' : 'text-bambu-gray hover:text-white'
              }`}
              onClick={() => setActiveLayout('printer')}
              title={t('queue.layout.groupByPrinter')}
            >
              <Printer className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">{t('queue.layout.byPrinter')}</span>
            </button>
          </div>
          <button
            onClick={() => {
              const newValue = !(settings?.queue_shortest_first ?? false);
              sjfMutation.mutate(newValue);
            }}
            className={`flex items-center gap-1 px-2 py-1.5 text-xs rounded-lg border transition-colors ${
              settings?.queue_shortest_first
                ? 'bg-bambu-green/20 border-bambu-green text-bambu-green'
                : 'bg-bambu-dark-secondary border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray'
            }`}
            title={t('queue.sjf.tooltip', 'Shortest Job First — scheduler prioritizes shorter prints')}
          >
            <Snail className="w-4 h-4" />
            <span className="hidden sm:inline">{t('queue.sjf.label', 'SJF')}</span>
            <span className={`w-1.5 h-1.5 rounded-full ${settings?.queue_shortest_first ? 'bg-bambu-green' : 'bg-bambu-gray'}`} />
          </button>
        </div>
      )}

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
      ) : activeTab === 'timeline' ? (
        <QueueTimelineView
          queueItems={queue || []}
          printers={printers || []}
          printerStatuses={printerStatusMap}
          onItemClick={(item) => {
            if (['completed', 'failed', 'skipped', 'cancelled'].includes(item.status)) {
              setRequeueItem(item);
            } else if (item.status === 'pending') {
              setEditItem(item);
            } else if (item.status === 'printing') {
              setConfirmAction({ type: 'stop', item });
            }
          }}
          t={t}
        />
      ) : activeTab === 'history' ? (
        <HistorySection
          items={historyItems}
          collapsed={false}
          sortBy={historySortBy}
          sortAsc={historySortAsc}
          onSortByChange={setHistorySortBy}
          onSortAscToggle={() => setHistorySortAsc(!historySortAsc)}
          onRemove={(item) => setConfirmAction({ type: 'remove', item })}
          onRequeue={setRequeueItem}
          timeFormat={timeFormat}
          batchCollapsed={batchCollapsed}
          toggleBatchCollapsed={toggleBatchCollapsed}
          hasPermission={hasPermission}
          canModify={canModify}
          t={t}
        />
      ) : (
        <div className="space-y-6 sm:space-y-8">
          {/* Active Prints */}
          {activeItems.length > 0 && (
            <div>
              <h2 className="text-base sm:text-lg font-semibold text-white mb-3 sm:mb-4 flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
                {t('queue.sections.currentlyPrinting')}
              </h2>
              <div className="space-y-2 sm:space-y-3">
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
                    printerState={item.printer_id ? printerStateMap[item.printer_id] : null}
                    t={t}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Pending Queue */}
          {pendingItems.length > 0 && (
            <div>
              <div className="flex flex-wrap items-center justify-between gap-2 mb-3 sm:mb-4">
                <h2 className="text-base sm:text-lg font-semibold text-white flex items-center gap-2">
                  <Clock className="w-4 h-4 sm:w-5 sm:h-5 text-yellow-400" />
                  {t('queue.sections.queued')}
                  <span className="text-xs sm:text-sm font-normal text-bambu-gray">
                    ({t('queue.itemCount', { count: pendingItems.length })})
                  </span>
                  <span className="hidden sm:inline text-xs text-bambu-gray ml-2" title={t('queue.reorderHint')}>
                    {t('queue.dragToReorder')}
                  </span>
                </h2>
                <div className="flex items-center gap-2">
                  <select
                    className="px-2 sm:px-3 py-1.5 text-xs sm:text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
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
                    title={pendingSortAsc ? t('common.ascending') : t('common.descending')}
                    className="px-2"
                  >
                    {pendingSortAsc ? <ArrowUp className="w-4 h-4" /> : <ArrowDown className="w-4 h-4" />}
                  </Button>
                </div>
              </div>

              {/* Bulk action toolbar (now with "Group as batch") */}
              <div className="flex flex-wrap items-center gap-2 sm:gap-3 mb-3 sm:mb-4 p-2 sm:p-3 bg-bambu-dark rounded-lg">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleSelectAll}
                  className="flex items-center gap-1.5 sm:gap-2 text-xs sm:text-sm"
                >
                  {selectedItems.length === pendingItems.length && pendingItems.length > 0 ? (
                    <CheckSquare className="w-4 h-4 text-bambu-green" />
                  ) : (
                    <Square className="w-4 h-4" />
                  )}
                  {selectedItems.length === pendingItems.length && pendingItems.length > 0 ? t('queue.bulkEdit.deselectAll') : t('queue.bulkEdit.selectAll')}
                </Button>
                {selectedItems.length > 0 && (
                  <>
                    <span className="text-xs sm:text-sm text-bambu-gray">
                      {t('queue.bulkEdit.selected', { count: selectedItems.length })}
                    </span>
                    <div className="hidden sm:block h-4 w-px bg-bambu-dark-tertiary" />
                    {canGroupSelected && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setGroupBatchModal(true)}
                        className="flex items-center gap-1.5 sm:gap-2 text-xs sm:text-sm text-cyan-300 hover:text-cyan-200"
                        title={t('queue.batch.groupAsBatch')}
                      >
                        <Package className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                        <span className="hidden sm:inline">{t('queue.batch.groupAsBatch')}</span>
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setShowBulkEditModal(true)}
                      className="flex items-center gap-1.5 sm:gap-2 text-xs sm:text-sm text-bambu-green hover:text-bambu-green-light"
                      disabled={!hasAnyPermission('queue:update_own', 'queue:update_all')}
                      title={!hasAnyPermission('queue:update_own', 'queue:update_all') ? t('queue.permissions.noEditItems') : t('queue.bulkEdit.editSelected')}
                    >
                      <Pencil className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                      <span className="hidden sm:inline">{t('queue.bulkEdit.editSelected')}</span>
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => bulkCancelMutation.mutate(selectedItems)}
                      className="flex items-center gap-1.5 sm:gap-2 text-xs sm:text-sm text-red-400 hover:text-red-300"
                      disabled={bulkCancelMutation.isPending || !hasAnyPermission('queue:delete_own', 'queue:delete_all')}
                      title={!hasAnyPermission('queue:delete_own', 'queue:delete_all') ? t('queue.permissions.noCancelItems') : t('queue.bulkEdit.cancelSelected')}
                    >
                      <X className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                      <span className="hidden sm:inline">{t('queue.bulkEdit.cancelSelected')}</span>
                    </Button>
                  </>
                )}
              </div>

              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragStart={handleDragStart}
                onDragEnd={handleDragEnd}
              >
                <SortableContext
                  items={sortableIds}
                  strategy={verticalListSortingStrategy}
                >
                  {activeLayout === 'position' ? (
                    <div className="space-y-2 sm:space-y-3">
                      {groupedRows.map((row) => (
                        <QueueRowRender
                          key={row.kind === 'item' ? `item-${row.item.id}` : `batch-${row.batchId}`}
                          row={row}
                          collapsed={row.kind === 'batch' ? (batchCollapsed[row.batchId] ?? true) : false}
                          onToggleBatch={row.kind === 'batch' ? () => toggleBatchCollapsed(row.batchId) : undefined}
                          onUngroup={row.kind === 'batch' ? () => setUngroupBatchId(row.batchId) : undefined}
                          setEditItem={setEditItem}
                          setConfirmAction={setConfirmAction}
                          startMutation={startMutation}
                          selectedItems={selectedItems}
                          handleToggleSelect={handleToggleSelect}
                          timeFormat={timeFormat}
                          hasPermission={hasPermission}
                          canModify={canModify}
                          t={t}
                          aggregateForRows={aggregateForRows}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {printerBuckets.map((bucket) => {
                        const agg = aggregateForRows(bucket.rows);
                        return (
                          <div key={bucket.key}>
                            <div className="flex items-center gap-3 px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-t-lg">
                              <Printer className={`w-4 h-4 ${bucket.isUnassigned ? 'text-orange-400' : bucket.targetModel ? 'text-blue-400' : 'text-bambu-green'}`} />
                              <span className="font-semibold text-white text-sm">{bucket.label}</span>
                              <span className="text-xs text-bambu-gray flex flex-wrap gap-x-3">
                                <span>{t('queue.itemCount', { count: agg.count })}</span>
                                {agg.time > 0 && <span>{formatDuration(agg.time)}</span>}
                                {agg.weight > 0 && <span>{formatWeight(agg.weight)}</span>}
                              </span>
                            </div>
                            <div className="bg-bambu-dark/40 border border-t-0 border-bambu-dark-tertiary rounded-b-lg p-2 space-y-2">
                              {bucket.rows.map((row) => (
                                <QueueRowRender
                                  key={row.kind === 'item' ? `item-${row.item.id}` : `batch-${row.batchId}`}
                                  row={row}
                                  collapsed={row.kind === 'batch' ? (batchCollapsed[row.batchId] ?? true) : false}
                                  onToggleBatch={row.kind === 'batch' ? () => toggleBatchCollapsed(row.batchId) : undefined}
                                  onUngroup={row.kind === 'batch' ? () => setUngroupBatchId(row.batchId) : undefined}
                                  setEditItem={setEditItem}
                                  setConfirmAction={setConfirmAction}
                                  startMutation={startMutation}
                                  selectedItems={selectedItems}
                                  handleToggleSelect={handleToggleSelect}
                                  timeFormat={timeFormat}
                                  hasPermission={hasPermission}
                                  canModify={canModify}
                                  t={t}
                                  aggregateForRows={aggregateForRows}
                                />
                              ))}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </SortableContext>
                <DragOverlay>
                  {(() => {
                    if (activeDragId === null) return null;
                    // Batch drag — show the group ghost with copy count.
                    if (typeof activeDragId === 'string' && activeDragId.startsWith('batch-')) {
                      const batchId = Number(activeDragId.slice('batch-'.length));
                      const siblings = pendingItems.filter((i) => i.batch_id === batchId);
                      if (siblings.length === 0) return null;
                      const name = siblings[0].batch_name || t('queue.batch.defaultName');
                      return (
                        <div className="flex items-center gap-3 px-3 py-2 bg-bambu-dark-secondary border-2 border-cyan-400 rounded-lg shadow-2xl">
                          <Package className="w-4 h-4 text-cyan-300" />
                          <span className="text-sm text-white font-medium">
                            {t('queue.dragGhost.batch', {
                              defaultValue: '{{name}} ({{count}} copies)',
                              name,
                              count: siblings.length,
                            })}
                          </span>
                        </div>
                      );
                    }
                    // Multi-row drag — show the N-item ghost.
                    if (typeof activeDragId === 'number' && selectedItems.includes(activeDragId) && selectedItems.length > 1) {
                      return (
                        <div className="flex items-center gap-3 px-3 py-2 bg-bambu-dark-secondary border-2 border-cyan-400 rounded-lg shadow-2xl">
                          <Package className="w-4 h-4 text-cyan-300" />
                          <span className="text-sm text-white font-medium">
                            {t('queue.dragGhost.multiCount', { count: selectedItems.length })}
                          </span>
                        </div>
                      );
                    }
                    return null;
                  })()}
                </DragOverlay>
              </DndContext>
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
          mode="create"
          archiveId={requeueItem.archive_id ?? undefined}
          libraryFileId={requeueItem.library_file_id ?? undefined}
          archiveName={requeueItem.archive_name || requeueItem.library_file_name || `File #${requeueItem.archive_id || requeueItem.library_file_id}`}
          onClose={() => setRequeueItem(null)}
        />
      )}

      {/* Confirm Action Modal */}
      {filamentShortConfirm && (
        <ConfirmModal
          title={t('queue.filamentShort.confirmTitle')}
          message={
            t('queue.filamentShort.confirmIntro') + '\n\n' +
            filamentShortConfirm.deficit
              .map((d) =>
                t('queue.filamentShort.lineItem', {
                  slot: d.slot_id,
                  required: Math.round(d.required_grams),
                  remaining:
                    d.remaining_grams == null
                      ? t('queue.filamentShort.unknown')
                      : Math.round(d.remaining_grams),
                }),
              )
              .join('\n')
          }
          confirmText={t('queue.filamentShort.printAnyway')}
          variant="warning"
          onConfirm={() => {
            startMutation.mutate({ id: filamentShortConfirm.itemId, skipFilamentCheck: true });
          }}
          onCancel={() => setFilamentShortConfirm(null)}
        />
      )}

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
            t('common.remove')
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

      {/* #1818: Resume-after-failure confirm */}
      {resumeConfirm && (
        <ConfirmModal
          title={t('queue.resumeAfterFailure.confirmTitle')}
          message={t('queue.resumeAfterFailure.confirmMessage', {
            printer: resumeConfirm.printerName,
            count: resumeConfirm.skippedCount,
          })}
          confirmText={t('queue.resumeAfterFailure.button')}
          variant="warning"
          onConfirm={() => resumeAfterFailureMutation.mutate(resumeConfirm.printerId)}
          onCancel={() => setResumeConfirm(null)}
        />
      )}

      {/* Clear History Confirm Modal */}
      {showClearHistoryConfirm && (
        <ConfirmModal
          title={t('queue.confirm.clearHistoryTitle')}
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

      {/* Bulk Edit Modal */}
      {showBulkEditModal && (
        <BulkEditModal
          selectedCount={selectedItems.length}
          printers={printers?.map(p => ({ id: p.id, name: p.name, nozzle_count: p.nozzle_count })) || []}
          onSave={(data) => {
            if (Object.keys(data).length > 0) {
              bulkUpdateMutation.mutate({ item_ids: selectedItems, ...data });
            }
          }}
          onClose={() => setShowBulkEditModal(false)}
          isSaving={bulkUpdateMutation.isPending}
          canControlPrinter={hasPermission('printers:control')}
          t={t}
        />
      )}

      {/* Group as batch modal — name prompt */}
      {groupBatchModal && (
        <GroupBatchModal
          itemCount={selectedItems.length}
          isSaving={createBatchMutation.isPending}
          defaultName={(() => {
            // Suggest a name derived from the first selected item's source.
            const first = pendingItems.find((i) => selectedItems.includes(i.id));
            const raw = first?.archive_name || first?.library_file_name || '';
            const cleaned = raw.replace(/\.gcode\.3mf$/i, '').replace(/\.3mf$/i, '');
            return cleaned ? `${cleaned}` : t('queue.batch.defaultName');
          })()}
          onSave={(name) => createBatchMutation.mutate({ name, item_ids: selectedItems })}
          onClose={() => setGroupBatchModal(false)}
          t={t}
        />
      )}

      {/* Ungroup batch confirm */}
      {ungroupBatchId !== null && (
        <ConfirmModal
          title={t('queue.batch.ungroupConfirmTitle')}
          message={t('queue.batch.ungroupConfirmMessage')}
          confirmText={t('queue.batch.ungroup')}
          variant="warning"
          onConfirm={() => ungroupBatchMutation.mutate(ungroupBatchId)}
          onCancel={() => setUngroupBatchId(null)}
        />
      )}
    </div>
  );
}

interface GroupBatchModalProps {
  itemCount: number;
  defaultName: string;
  isSaving: boolean;
  onSave: (name: string) => void;
  onClose: () => void;
  t: (key: string, options?: Record<string, unknown>) => string;
}

function GroupBatchModal({ itemCount, defaultName, isSaving, onSave, onClose, t }: GroupBatchModalProps) {
  const [name, setName] = useState(defaultName);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl p-6 w-full max-w-md">
        <h3 className="text-lg font-semibold text-white mb-2 flex items-center gap-2">
          <Package className="w-5 h-5 text-cyan-300" />
          {t('queue.batch.groupAsBatch')}
        </h3>
        <p className="text-sm text-bambu-gray mb-4">
          {t('queue.batch.groupAsBatchDescription', { count: itemCount })}
        </p>
        <label className="block text-sm font-medium text-white mb-2">
          {t('queue.batch.nameLabel')}
        </label>
        <input
          type="text"
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t('queue.batch.namePlaceholder')}
          maxLength={120}
          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none mb-5"
        />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={isSaving}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              const trimmed = name.trim();
              if (trimmed) onSave(trimmed);
            }}
            disabled={isSaving || !name.trim()}
          >
            {isSaving ? t('common.saving') : t('queue.batch.create')}
          </Button>
        </div>
      </div>
    </div>
  );
}
