import { useState, useMemo, useEffect, useRef } from 'react';
import { ChevronLeft, ChevronRight, Clock, Layers, Printer as PrinterIcon } from 'lucide-react';
import { formatDuration, parseUTCDate } from '../utils/date';
import type { PrintQueueItem, Printer } from '../api/client';
import { api } from '../api/client';
import { Button } from './Button';

/** Gantt-style 24h-rolling timeline. One horizontal swimlane per printer
 *  (plus one per active target_model and one for unassigned items). Each
 *  pending or printing job is rendered as a colored bar positioned by its
 *  predicted start time, width = predicted duration. A vertical NOW line
 *  marks current time. Hover a bar for details, click to edit/stop. */

const HOUR_MS = 60 * 60 * 1000;
const RANGE_HOURS = 24;
const RANGE_MS = RANGE_HOURS * HOUR_MS;
// Minimum bar width — short prints (a few minutes) would otherwise render as
// 1-2px slivers and be unclickable. 32px keeps them at thumb size.
const MIN_BAR_PX = 32;
// Lane height for the bar row + label.
const LANE_BAR_HEIGHT_PX = 40;

interface ScheduleEvent {
  item: PrintQueueItem;
  estimatedStart: Date;
  estimatedEnd: Date;
  progress?: number;
  type: 'printing' | 'queued';
}

interface QueueTimelineViewProps {
  queueItems: PrintQueueItem[];
  printers: Printer[];
  printerStatuses: Record<number, { progress?: number; remaining_time?: number; state?: string }>;
  onItemClick: (item: PrintQueueItem) => void;
  t: (key: string, options?: Record<string, unknown>) => string;
}

interface LaneDescriptor {
  key: string;
  label: string;
  /** null for model-based / unassigned lanes. */
  printerId: number | null;
  /** Set for model-based lanes (`Any X1C`). */
  targetModel: string | null;
}

function formatHour(date: Date): string {
  return date.toLocaleTimeString(undefined, { hour: 'numeric' });
}

function formatTooltipTime(date: Date): string {
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

export function QueueTimelineView({
  queueItems,
  printers,
  printerStatuses,
  onItemClick,
  t,
}: QueueTimelineViewProps) {
  // Tick "now" every minute so the NOW line and ETA labels stay live.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const interval = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(interval);
  }, []);

  // User can shift the window forward/back in 12h steps. Default = current
  // time, so the timeline reads "next 24h from now."
  const [windowOffsetMs, setWindowOffsetMs] = useState(0);

  // Round the window start down to the previous full hour so the axis ticks
  // land on whole hours.
  const rangeStartMs = useMemo(() => {
    const target = now.getTime() + windowOffsetMs;
    return Math.floor(target / HOUR_MS) * HOUR_MS;
  }, [now, windowOffsetMs]);
  const rangeEndMs = rangeStartMs + RANGE_MS;
  const nowMs = now.getTime();

  // Build schedule events. Only committed schedules are rendered:
  //  • currently printing → always
  //  • pending with explicit scheduled_time → at that time
  //  • pending ASAP that chain behind a print actually running on the same
  //    lane → forecast
  // Staged (manual_start) and waiting (waiting_reason) items are not on the
  // timeline because they won't auto-dispatch — they'd be misleading bars.
  // Idle-printer ASAP queues also stay off until something starts on them.
  const events = useMemo<ScheduleEvent[]>(() => {
    const result: ScheduleEvent[] = [];
    const pendingByLaneKey = new Map<string, PrintQueueItem[]>();
    // Lanes that have an active print right now — only these qualify for
    // ASAP chain forecasting.
    const lanesWithActive = new Set<string>();
    // Chain-end timestamp per lane (where the next pending item's bar starts).
    const chainEndByLane = new Map<string, number>();

    const laneKeyOf = (item: PrintQueueItem): string => {
      if (item.printer_id != null) return `printer:${item.printer_id}`;
      if (item.target_model) return `model:${item.target_model}`;
      return 'unassigned';
    };

    for (const item of queueItems) {
      if (item.status === 'printing') {
        const status = item.printer_id != null ? printerStatuses[item.printer_id] : undefined;
        const start = parseUTCDate(item.started_at) || new Date();
        let endTime: Date;
        if (status?.remaining_time != null && status.remaining_time > 0) {
          endTime = new Date(nowMs + status.remaining_time * 60 * 1000);
        } else if (item.print_time_seconds) {
          const progress = status?.progress || 0;
          const remainingFraction = Math.max(0, 1 - progress / 100);
          endTime = new Date(nowMs + item.print_time_seconds * remainingFraction * 1000);
        } else {
          endTime = new Date(nowMs + HOUR_MS);
        }
        result.push({
          item,
          estimatedStart: start,
          estimatedEnd: endTime,
          progress: status?.progress ?? undefined,
          type: 'printing',
        });
        const lk = laneKeyOf(item);
        lanesWithActive.add(lk);
        chainEndByLane.set(lk, Math.max(chainEndByLane.get(lk) ?? nowMs, endTime.getTime()));
      } else if (item.status === 'pending') {
        // Skip un-committed pending shapes — staged items and waiting items
        // won't auto-dispatch, so a bar would lie.
        if (item.manual_start) continue;
        if (item.waiting_reason) continue;
        const lk = laneKeyOf(item);
        if (!pendingByLaneKey.has(lk)) pendingByLaneKey.set(lk, []);
        pendingByLaneKey.get(lk)!.push(item);
      }
    }

    const sixMonthsFromNow = Date.now() + 180 * 24 * HOUR_MS;
    for (const [lk, items] of pendingByLaneKey) {
      items.sort((a, b) => a.position - b.position);
      const hasActive = lanesWithActive.has(lk);
      // A lane is timelineable when EITHER it has an active print (chain
      // forecast off its end) OR its first pending item is scheduled (a
      // committed anchor exists). Otherwise every chained ASAP item is just
      // a guess — drop the whole lane to keep the view honest.
      const firstScheduled = items[0] ? parseUTCDate(items[0].scheduled_time) : null;
      const firstScheduledOk = firstScheduled && firstScheduled.getTime() <= sixMonthsFromNow;
      if (!hasActive && !firstScheduledOk) continue;

      let chainEnd = chainEndByLane.get(lk) ?? nowMs;
      for (const item of items) {
        const scheduled = parseUTCDate(item.scheduled_time);
        if (scheduled && scheduled.getTime() <= sixMonthsFromNow) {
          chainEnd = Math.max(chainEnd, scheduled.getTime());
        }
        const duration = (item.print_time_seconds || 3600) * 1000;
        result.push({
          item,
          estimatedStart: new Date(chainEnd),
          estimatedEnd: new Date(chainEnd + duration),
          type: 'queued',
        });
        chainEnd += duration;
      }
    }
    return result;
  }, [queueItems, printerStatuses, nowMs]);

  // Lanes: every printer + every distinct target_model with queue activity
  // + an "unassigned" lane if needed. Printers that have NO events queued
  // still get a lane so users see idle capacity.
  const lanes = useMemo<LaneDescriptor[]>(() => {
    const list: LaneDescriptor[] = [];
    for (const p of printers) {
      list.push({
        key: `printer:${p.id}`,
        label: p.name,
        printerId: p.id,
        targetModel: null,
      });
    }
    const modelLanesAdded = new Set<string>();
    let hasUnassigned = false;
    for (const ev of events) {
      if (ev.item.printer_id != null) continue;
      if (ev.item.target_model) {
        const k = `model:${ev.item.target_model}`;
        if (!modelLanesAdded.has(k)) {
          modelLanesAdded.add(k);
          list.push({
            key: k,
            label: `${t('queue.filter.any')} ${ev.item.target_model}`,
            printerId: null,
            targetModel: ev.item.target_model,
          });
        }
      } else {
        hasUnassigned = true;
      }
    }
    if (hasUnassigned) {
      list.push({
        key: 'unassigned',
        label: t('queue.filter.unassigned'),
        printerId: null,
        targetModel: null,
      });
    }
    return list;
  }, [printers, events, t]);

  const eventsByLane = useMemo(() => {
    const map = new Map<string, ScheduleEvent[]>();
    for (const ev of events) {
      let key: string;
      if (ev.item.printer_id != null) key = `printer:${ev.item.printer_id}`;
      else if (ev.item.target_model) key = `model:${ev.item.target_model}`;
      else key = 'unassigned';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(ev);
    }
    return map;
  }, [events]);

  const hourTicks = useMemo(() => {
    const ticks: { ms: number; pct: number; label: string }[] = [];
    for (let h = 0; h <= RANGE_HOURS; h += 2) {
      const ms = rangeStartMs + h * HOUR_MS;
      ticks.push({
        ms,
        pct: (h / RANGE_HOURS) * 100,
        label: formatHour(new Date(ms)),
      });
    }
    return ticks;
  }, [rangeStartMs]);

  const nowPct = ((nowMs - rangeStartMs) / RANGE_MS) * 100;
  const nowInView = nowPct >= 0 && nowPct <= 100;

  // Aggregated "all done by" across the entire (un-windowed) event set.
  const allDoneBy = useMemo(() => {
    let latest = 0;
    for (const ev of events) latest = Math.max(latest, ev.estimatedEnd.getTime());
    return latest > 0 ? new Date(latest) : null;
  }, [events]);

  const trackRef = useRef<HTMLDivElement | null>(null);

  return (
    <div>
      {/* Window controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setWindowOffsetMs((v) => v - 12 * HOUR_MS)}
            className="p-1.5"
            title={t('queue.timeline.window.back12h')}
          >
            <ChevronLeft className="w-4 h-4" />
          </Button>
          <span className="text-sm font-medium text-white min-w-[180px] text-center">
            {new Date(rangeStartMs).toLocaleString(undefined, {
              weekday: 'short',
              month: 'short',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
            })}
            {' → '}
            {new Date(rangeEndMs).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setWindowOffsetMs((v) => v + 12 * HOUR_MS)}
            className="p-1.5"
            title={t('queue.timeline.window.forward12h')}
          >
            <ChevronRight className="w-4 h-4" />
          </Button>
          {windowOffsetMs !== 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setWindowOffsetMs(0)}
              className="text-xs text-bambu-green"
            >
              {t('queue.timeline.window.now')}
            </Button>
          )}
        </div>
        {allDoneBy && (
          <span className="text-xs text-bambu-gray flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5" />
            {t('queue.timeline.allDoneBy', {
              time: allDoneBy.toLocaleString(undefined, {
                weekday: 'short',
                hour: '2-digit',
                minute: '2-digit',
              }),
            })}
          </span>
        )}
      </div>

      {/* Empty-state notice when the fleet is idle and no queued item is
          committed (no scheduled_time / no active print to chain off).
          Without this, users see striped lanes with no bars and assume the
          timeline is broken — common confusion from the GHSA-r2qv era. */}
      {lanes.length > 0 && events.length === 0 && (
        <div className="mb-4 p-3 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark/40 text-xs text-bambu-gray">
          {t('queue.timeline.nothingCommitted')}
        </div>
      )}

      {lanes.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-bambu-gray">
          <Layers className="w-12 h-12 mb-3 opacity-30" />
          <p className="text-sm">{t('queue.timeline.noData')}</p>
        </div>
      ) : (
        <div className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary overflow-hidden">
          {/* Hour axis */}
          <div className="flex border-b border-bambu-dark-tertiary">
            <div className="w-32 sm:w-40 shrink-0 px-3 py-2 text-xs font-medium text-bambu-gray border-r border-bambu-dark-tertiary">
              {t('queue.timeline.printerColumnHeader')}
            </div>
            <div className="relative flex-1 h-9">
              {hourTicks.map((tick) => (
                <div
                  key={tick.ms}
                  className="absolute top-0 bottom-0 border-l border-bambu-dark-tertiary/40 text-[10px] sm:text-xs text-bambu-gray pl-1 flex items-center"
                  style={{ left: `${tick.pct}%` }}
                >
                  {tick.label}
                </div>
              ))}
            </div>
          </div>

          {/* Lanes */}
          <div ref={trackRef} className="relative">
            {lanes.map((lane) => {
              const laneEvents = eventsByLane.get(lane.key) ?? [];
              return (
                <div key={lane.key} className="flex border-b border-bambu-dark-tertiary/40 last:border-b-0">
                  <div className="w-32 sm:w-40 shrink-0 px-3 py-3 border-r border-bambu-dark-tertiary flex items-center gap-2">
                    <PrinterIcon className={`w-3.5 h-3.5 shrink-0 ${
                      lane.printerId == null && lane.targetModel == null
                        ? 'text-orange-400'
                        : lane.targetModel
                          ? 'text-blue-400'
                          : 'text-bambu-green'
                    }`} />
                    <span className="text-sm text-white truncate">{lane.label}</span>
                  </div>
                  <div
                    className="relative flex-1"
                    style={{ height: LANE_BAR_HEIGHT_PX + 16 }}
                  >
                    {/* Hour grid lines */}
                    {hourTicks.map((tick) => (
                      <div
                        key={tick.ms}
                        className="absolute top-0 bottom-0 border-l border-bambu-dark-tertiary/30"
                        style={{ left: `${tick.pct}%` }}
                      />
                    ))}

                    {/* Idle background — diagonal stripes hint that the lane
                        is sittable. Rendered behind bars so they overlay it. */}
                    <div
                      className="absolute inset-0 opacity-30"
                      style={{
                        backgroundImage:
                          'repeating-linear-gradient(45deg, transparent, transparent 6px, rgba(255,255,255,0.04) 6px, rgba(255,255,255,0.04) 12px)',
                      }}
                      aria-hidden
                    />

                    {/* Job bars */}
                    {laneEvents.map((ev) => {
                      // Clip to the visible window.
                      const startMs = Math.max(rangeStartMs, ev.estimatedStart.getTime());
                      const endMs = Math.min(rangeEndMs, ev.estimatedEnd.getTime());
                      if (endMs <= rangeStartMs || startMs >= rangeEndMs) return null;
                      const leftPct = ((startMs - rangeStartMs) / RANGE_MS) * 100;
                      const widthPct = ((endMs - startMs) / RANGE_MS) * 100;
                      const displayName = ev.item.archive_name
                        || ev.item.library_file_name
                        || `#${ev.item.id}`;
                      const thumbnailUrl = ev.item.archive_thumbnail
                        ? api.getArchiveThumbnail(ev.item.archive_id!)
                        : ev.item.library_file_thumbnail
                          ? api.getLibraryFileThumbnailUrl(ev.item.library_file_id!)
                          : null;
                      const isPrinting = ev.type === 'printing';
                      const isBatched = ev.item.batch_id != null;
                      const tooltipParts = [
                        displayName,
                        `${formatTooltipTime(ev.estimatedStart)} → ${formatTooltipTime(ev.estimatedEnd)}`,
                        ev.item.print_time_seconds ? formatDuration(ev.item.print_time_seconds) : null,
                        isPrinting && ev.progress != null ? `${Math.round(ev.progress)}%` : null,
                        ev.item.batch_name ? `batch: ${ev.item.batch_name}` : null,
                      ].filter(Boolean).join(' · ');
                      return (
                        <button
                          key={ev.item.id}
                          onClick={() => onItemClick(ev.item)}
                          title={tooltipParts}
                          className={`absolute rounded-md transition-all hover:brightness-110 hover:z-10 overflow-hidden flex items-center gap-1.5 px-1.5 text-left ${
                            isPrinting
                              ? 'bg-blue-500/30 border border-blue-400/60'
                              : isBatched
                                ? 'bg-cyan-500/20 border border-cyan-400/50'
                                : 'bg-bambu-green/20 border border-bambu-green/40'
                          }`}
                          style={{
                            left: `${leftPct}%`,
                            width: `max(${MIN_BAR_PX}px, ${widthPct}%)`,
                            top: 8,
                            height: LANE_BAR_HEIGHT_PX,
                          }}
                        >
                          {thumbnailUrl && (
                            <img
                              src={thumbnailUrl}
                              alt=""
                              className="w-7 h-7 rounded object-cover shrink-0 bg-bambu-dark"
                            />
                          )}
                          <div className="min-w-0 flex-1">
                            <div className="text-xs text-white font-medium truncate leading-tight">
                              {displayName}
                            </div>
                            <div className="text-[10px] text-bambu-gray truncate leading-tight">
                              {ev.item.print_time_seconds ? formatDuration(ev.item.print_time_seconds) : ''}
                              {isPrinting && ev.progress != null ? ` · ${Math.round(ev.progress)}%` : ''}
                            </div>
                          </div>
                          {isPrinting && ev.progress != null && (
                            <div
                              className="absolute bottom-0 left-0 h-0.5 bg-blue-300"
                              style={{ width: `${ev.progress}%` }}
                              aria-hidden
                            />
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}

            {/* NOW line — drawn on top of all lanes. Use the same
                label-column offset (w-32 sm:w-40) as the lanes so the line
                aligns exactly with the time track. */}
            {nowInView && (
              <div className="absolute top-0 bottom-0 pointer-events-none z-20 flex inset-x-0">
                <div className="w-32 sm:w-40 shrink-0" />
                <div className="relative flex-1">
                  <div
                    className="absolute top-0 bottom-0 w-0.5 bg-red-400 shadow-[0_0_8px_rgba(248,113,113,0.6)]"
                    style={{ left: `${nowPct}%` }}
                  >
                    <div className="absolute -top-1 -left-1 w-2.5 h-2.5 bg-red-400 rounded-full" />
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
