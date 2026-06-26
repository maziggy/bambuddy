import {
  CheckCircle,
  XCircle,
  SkipForward,
  Ban,
  RefreshCw,
  Trash2,
  Printer,
  Timer,
  Layers,
  User,
  AlertCircle,
  Weight,
} from 'lucide-react';
import { api } from '../api/client';
import { type TimeFormat, formatDuration, formatRelativeTime } from '../utils/date';
import type { PrintQueueItem, Permission } from '../api/client';
import { Button } from './Button';

const STATUS_CONFIG = {
  completed: { icon: CheckCircle, color: 'text-emerald-400', border: 'border-l-emerald-500' },
  failed: { icon: XCircle, color: 'text-red-400', border: 'border-l-red-500' },
  skipped: { icon: SkipForward, color: 'text-orange-400', border: 'border-l-gray-500' },
  cancelled: { icon: Ban, color: 'text-gray-400', border: 'border-l-gray-500' },
} as const;

/** Bambu encodes "no filament" as transparent/zeroed RGBA. The slicer's
 *  filament_color is a hex string like "RRGGBBAA" or "RRGGBB"; treat all-zero
 *  / unparsable as no swatch. */
function normalizeFilamentColor(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const clean = raw.startsWith('#') ? raw.slice(1) : raw;
  if (/^0{6,8}$/.test(clean)) return null;
  if (!/^[0-9a-fA-F]{6,8}$/.test(clean)) return null;
  return `#${clean.slice(0, 6)}`;
}

export function CompactHistoryRow({
  item,
  onRequeue,
  onRemove,
  timeFormat = 'system',
  hasPermission,
  canModify,
  t,
}: {
  item: PrintQueueItem;
  onRequeue: () => void;
  onRemove: () => void;
  timeFormat?: TimeFormat;
  hasPermission: (permission: Permission) => boolean;
  canModify: (resource: 'queue' | 'archives' | 'library', action: 'update' | 'delete' | 'reprint', createdById: number | null | undefined) => boolean;
  t: (key: string, options?: Record<string, unknown>) => string;
}) {
  const config = STATUS_CONFIG[item.status as keyof typeof STATUS_CONFIG] || STATUS_CONFIG.cancelled;
  const StatusIcon = config.icon;
  const displayName = item.archive_name || item.library_file_name || `File #${item.archive_id || item.library_file_id}`;

  const thumbnailUrl = item.archive_thumbnail
    ? api.getArchiveThumbnail(item.archive_id!)
    : item.library_file_thumbnail
      ? api.getLibraryFileThumbnailUrl(item.library_file_id!)
      : null;

  const completedTime = item.completed_at || item.created_at;
  const filamentColor = normalizeFilamentColor(item.filament_color);
  const filamentMass = item.filament_used_grams ? Math.round(item.filament_used_grams) : null;
  // Failed and skipped prints carry the diagnostic in error_message; surface
  // it inline so the user doesn't have to reopen the row to see why.
  const showErrorMessage = !!item.error_message
    && (item.status === 'failed' || item.status === 'skipped');

  return (
    <div className={`px-3 py-2 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary border-l-[3px] ${config.border}`}>
      {/* Top row — status / thumb / name / time / actions */}
      <div className="flex items-center gap-2 sm:gap-3">
        <StatusIcon className={`w-4 h-4 shrink-0 ${config.color}`} />

        <div className="relative shrink-0 history-thumb-hover">
          <div className="w-8 h-8 bg-bambu-dark rounded overflow-hidden">
            {thumbnailUrl ? (
              <img src={thumbnailUrl} alt="" className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full flex items-center justify-center text-bambu-gray">
                <Layers className="w-4 h-4" />
              </div>
            )}
          </div>
          {/* Hover preview — desktop only via CSS @media. Positioned to the
              right of the thumbnail; the parent card has no overflow:hidden
              so the popup escapes its rounded border. pointer-events-none so
              it doesn't interfere with clicks on rows below. */}
          {thumbnailUrl && (
            <div className="history-thumb-preview absolute z-[60] left-full top-0 ml-2 w-48 h-48 pointer-events-none opacity-0 transition-opacity">
              <img
                src={thumbnailUrl}
                alt=""
                className="w-full h-full object-cover rounded-lg shadow-2xl border-2 border-bambu-dark-tertiary bg-bambu-dark"
              />
            </div>
          )}
        </div>

        <span className="text-sm text-white font-medium truncate min-w-0 flex-1">
          {displayName}
        </span>

        <span
          className="text-xs text-bambu-gray shrink-0"
          title={completedTime ?? undefined}
        >
          {formatRelativeTime(completedTime, timeFormat, t)}
        </span>

        <div className="flex items-center gap-0.5 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={onRequeue}
            disabled={!hasPermission('queue:create')}
            title={!hasPermission('queue:create') ? t('queue.permissions.noRequeue') : t('queue.actions.requeue')}
            className="text-bambu-green hover:text-bambu-green/80 hover:bg-bambu-green/10 p-1.5"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onRemove}
            disabled={!canModify('queue', 'delete', item.created_by_id)}
            title={!canModify('queue', 'delete', item.created_by_id) ? t('queue.permissions.noRemove') : t('common.remove')}
            className="p-1.5"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {/* Meta row — printer / filament / duration / user. Indented under the
          thumbnail so it lines up with the name. */}
      <div className="mt-1 ml-[3.25rem] flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-bambu-gray">
        {item.printer_name && (
          <span className="flex items-center gap-1 shrink-0">
            <Printer className="w-3 h-3" />
            <span className="truncate max-w-[100px] sm:max-w-[140px]">{item.printer_name}</span>
          </span>
        )}
        {(filamentColor || filamentMass) && (
          <span className="flex items-center gap-1 shrink-0">
            {filamentColor ? (
              <span
                className="inline-block w-2.5 h-2.5 rounded-full border border-white/15"
                style={{ backgroundColor: filamentColor }}
                aria-hidden
              />
            ) : (
              <Weight className="w-3 h-3" />
            )}
            <span className="truncate">
              {filamentMass ? `${filamentMass}g` : null}
              {filamentMass && item.filament_type ? ` ${item.filament_type}` : null}
              {!filamentMass && item.filament_type ? item.filament_type : null}
            </span>
          </span>
        )}
        {item.print_time_seconds && (
          <span className="flex items-center gap-1 shrink-0">
            <Timer className="w-3 h-3" />
            {formatDuration(item.print_time_seconds)}
          </span>
        )}
        {item.created_by_username && (
          <span
            className="flex items-center gap-1 shrink-0"
            title={t('queue.addedBy', { name: item.created_by_username })}
          >
            <User className="w-3 h-3" />
            <span className="truncate max-w-[120px]">{item.created_by_username}</span>
          </span>
        )}
      </div>

      {/* Error message — only rendered on failed/skipped rows. */}
      {showErrorMessage && (
        <div className="mt-1 ml-[3.25rem] flex items-start gap-1 text-xs text-red-400">
          <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" />
          <span className="break-words">{item.error_message}</span>
        </div>
      )}
    </div>
  );
}
