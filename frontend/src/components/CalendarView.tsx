import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';
import type { Archive, PrintLogEntry } from '../api/client';
import { api } from '../api/client';
import { parseUTCDate } from '../utils/date';

interface CalendarViewProps {
  // The archives the page's filters let through: their prints are shown.
  archives: Archive[];
  // Only the prints on this printer, as the page's printer filter.
  printerId?: number | null;
  onPrintClick?: (archiveId: number, runId: number | null) => void;
  highlightedArchiveId?: number | null;
}

// One print on the calendar: a run, or an archive printed before runs were
// logged, which stands for its one print.
interface CalendarPrint {
  key: string;
  archive: Archive;
  runId: number | null;
  status: string;
  date: Date;
  colors: string | null;
}

const PRINTED = new Set(['completed', 'failed', 'aborted', 'cancelled', 'stopped']);
const FAILED = new Set(['failed', 'aborted']);
const PAGE = 500;

function getDaysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

function getFirstDayOfMonth(year: number, month: number): number {
  return new Date(year, month, 1).getDay();
}

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

// Every print that ended in the month, as the archive list's visibility has
// it, page by page.
async function fetchMonthRuns(year: number, month: number, printerId?: number | null): Promise<PrintLogEntry[]> {
  const dateFrom = dayKey(new Date(year, month, 1));
  const dateTo = dayKey(new Date(year, month + 1, 0));
  const runs: PrintLogEntry[] = [];
  for (let offset = 0; ; offset += PAGE) {
    const page = await api.getArchivePrints({ dateFrom, dateTo, printerId: printerId || undefined, limit: PAGE, offset });
    runs.push(...page.items);
    if (page.items.length < PAGE || runs.length >= page.total) return runs;
  }
}

/**
 * A month of prints (archive redesign P4): each print of an archive on the
 * day it ended, so a file printed three times shows three times. A click
 * opens the archive's page at that print.
 */
export function CalendarView({ archives, printerId, onPrintClick, highlightedArchiveId }: CalendarViewProps) {
  const { t, i18n } = useTranslation();
  const today = new Date();
  const [currentMonth, setCurrentMonth] = useState(today.getMonth());
  const [currentYear, setCurrentYear] = useState(today.getFullYear());
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const { data: runs, isLoading } = useQuery({
    queryKey: ['calendar-runs', currentYear, currentMonth, printerId ?? null],
    queryFn: () => fetchMonthRuns(currentYear, currentMonth, printerId),
  });

  const prints = useMemo(() => {
    const byId = new Map(archives.map((a) => [a.id, a]));
    const list: CalendarPrint[] = [];
    for (const run of runs ?? []) {
      const archive = run.archive_id != null ? byId.get(run.archive_id) : undefined;
      const date = parseUTCDate(run.completed_at || run.created_at);
      if (!archive || !date) continue;
      list.push({
        key: `run-${run.id}`,
        archive,
        runId: run.id,
        status: run.status,
        date,
        colors: run.filament_color || archive.filament_color,
      });
    }
    for (const archive of archives) {
      if (archive.run_count > 0 || !PRINTED.has(archive.status)) continue;
      const date = parseUTCDate(archive.completed_at || archive.created_at);
      if (!date) continue;
      list.push({ key: `archive-${archive.id}`, archive, runId: null, status: archive.status, date, colors: archive.filament_color });
    }
    return list
      .filter((p) => p.date.getMonth() === currentMonth && p.date.getFullYear() === currentYear)
      .sort((a, b) => a.date.getTime() - b.date.getTime());
  }, [archives, runs, currentMonth, currentYear]);

  const printsByDate = useMemo(() => {
    const map = new Map<string, CalendarPrint[]>();
    for (const print of prints) {
      const key = dayKey(print.date);
      map.set(key, [...(map.get(key) ?? []), print]);
    }
    return map;
  }, [prints]);

  const monthTitle = new Date(currentYear, currentMonth, 1).toLocaleDateString(i18n.language, { month: 'long', year: 'numeric' });
  // 2023-01-01 was a Sunday: the calendar's weeks start on Sundays.
  const dayNames = Array.from({ length: 7 }, (_, i) =>
    new Date(2023, 0, 1 + i).toLocaleDateString(i18n.language, { weekday: 'short' })
  );

  const daysInMonth = getDaysInMonth(currentYear, currentMonth);
  const firstDay = getFirstDayOfMonth(currentYear, currentMonth);

  const prevMonth = () => {
    if (currentMonth === 0) {
      setCurrentMonth(11);
      setCurrentYear(currentYear - 1);
    } else {
      setCurrentMonth(currentMonth - 1);
    }
  };

  const nextMonth = () => {
    if (currentMonth === 11) {
      setCurrentMonth(0);
      setCurrentYear(currentYear + 1);
    } else {
      setCurrentMonth(currentMonth + 1);
    }
  };

  const goToToday = () => {
    setCurrentMonth(today.getMonth());
    setCurrentYear(today.getFullYear());
  };

  // Build calendar grid
  const calendarDays: (number | null)[] = [];
  for (let i = 0; i < firstDay; i++) {
    calendarDays.push(null);
  }
  for (let day = 1; day <= daysInMonth; day++) {
    calendarDays.push(day);
  }

  const selectedPrints = selectedDate ? printsByDate.get(selectedDate) || [] : [];

  const handleDateSelect = (dateKey: string | null) => {
    if (dateKey !== selectedDate) {
      setSelectedKey(null);
    }
    setSelectedDate(dateKey);
  };

  return (
    <div className="flex flex-col lg:flex-row gap-6">
      {/* Calendar */}
      <div className="flex-1">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <button
            onClick={prevMonth}
            className="p-2 hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
            aria-label={t('archives.calendar.previousMonth')}
          >
            <ChevronLeft className="w-5 h-5 text-bambu-gray" />
          </button>
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-white">{monthTitle}</h2>
            <button
              onClick={goToToday}
              className="px-2 py-1 text-xs bg-bambu-dark-tertiary hover:bg-bambu-green/20 text-bambu-gray hover:text-white rounded transition-colors"
            >
              {t('archives.calendar.today')}
            </button>
            {isLoading && <Loader2 className="w-4 h-4 text-bambu-gray animate-spin" />}
          </div>
          <button
            onClick={nextMonth}
            className="p-2 hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
            aria-label={t('archives.calendar.nextMonth')}
          >
            <ChevronRight className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        {/* Day headers */}
        <div className="grid grid-cols-7 gap-1 mb-1">
          {dayNames.map((day) => (
            <div key={day} className="text-center text-xs text-bambu-gray py-2">
              {day}
            </div>
          ))}
        </div>

        {/* Calendar grid */}
        <div className="grid grid-cols-7 gap-1">
          {calendarDays.map((day, index) => {
            if (day === null) {
              return <div key={`empty-${index}`} className="aspect-square" />;
            }

            const dateKey = `${currentYear}-${String(currentMonth + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
            const dayPrints = printsByDate.get(dateKey) || [];
            const hasPrints = dayPrints.length > 0;
            const isToday = day === today.getDate() && currentMonth === today.getMonth() && currentYear === today.getFullYear();
            const isSelected = dateKey === selectedDate;
            const successCount = dayPrints.filter((p) => p.status === 'completed').length;
            const failedCount = dayPrints.filter((p) => FAILED.has(p.status)).length;

            return (
              <button
                key={day}
                onClick={() => handleDateSelect(isSelected ? null : dateKey)}
                className={`aspect-square rounded-lg p-1 flex flex-col items-center justify-center transition-colors relative ${
                  isSelected
                    ? 'bg-bambu-green text-white'
                    : isToday
                    ? 'bg-bambu-green/20 text-white ring-2 ring-bambu-green'
                    : hasPrints
                    ? 'bg-bambu-dark-tertiary hover:bg-bambu-dark-tertiary/70 text-white'
                    : 'hover:bg-bambu-dark-tertiary/50 text-bambu-gray'
                }`}
              >
                <span className={`text-sm font-medium ${isToday && !isSelected ? 'text-bambu-green' : ''}`}>
                  {day}
                </span>
                {hasPrints && (
                  <div className="absolute bottom-1 left-1/2 -translate-x-1/2 flex items-center gap-1">
                    <div className={`w-2 h-2 rounded-full ${
                      failedCount > 0 && successCount === 0
                        ? 'bg-red-400'
                        : failedCount > 0
                        ? 'bg-yellow-400'
                        : 'bg-green-400'
                    }`} />
                    <span className="text-xs font-medium">{dayPrints.length}</span>
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* Monthly stats */}
        <div className="mt-4 pt-4 border-t border-bambu-dark-tertiary">
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <div className="text-2xl font-bold text-white">{prints.length}</div>
              <div className="text-xs text-bambu-gray">{t('archives.calendar.printsThisMonth')}</div>
            </div>
            <div>
              <div className="text-2xl font-bold text-green-700 dark:text-green-400">
                {prints.filter((p) => p.status === 'completed').length}
              </div>
              <div className="text-xs text-bambu-gray">{t('archives.calendar.successful')}</div>
            </div>
            <div>
              <div className="text-2xl font-bold text-red-700 dark:text-red-400">
                {prints.filter((p) => FAILED.has(p.status)).length}
              </div>
              <div className="text-xs text-bambu-gray">{t('archives.calendar.failed')}</div>
            </div>
          </div>
        </div>
      </div>

      {/* Selected day details */}
      <div className="lg:w-80 bg-bambu-dark rounded-xl p-4">
        {selectedDate ? (
          <>
            <h3 className="text-sm font-medium text-bambu-gray mb-3">
              {new Date(selectedDate + 'T12:00:00').toLocaleDateString(i18n.language, {
                weekday: 'long',
                month: 'long',
                day: 'numeric',
                year: 'numeric'
              })}
            </h3>
            {selectedPrints.length > 0 ? (
              <div className="calendar-scroll space-y-2 max-h-96 overflow-y-auto">
                {selectedPrints.map((print) => {
                  const { archive } = print;
                  const isHighlighted = print.key === selectedKey || archive.id === highlightedArchiveId;
                  return (
                  <button
                    key={print.key}
                    onClick={() => {
                      setSelectedKey(print.key);
                      onPrintClick?.(archive.id, print.runId);
                    }}
                    className={`w-full flex items-center gap-3 p-2 rounded-lg transition-colors text-left ${
                      !isHighlighted ? 'hover:bg-bambu-dark-tertiary' : ''
                    }`}
                    style={isHighlighted ? { outline: '4px solid #facc15', outlineOffset: '2px' } : undefined}
                  >
                    {archive.thumbnail_path ? (
                      <img
                        src={api.getArchiveThumbnail(archive.id)}
                        alt=""
                        className="w-12 h-12 rounded object-cover"
                      />
                    ) : (
                      <div className="w-12 h-12 rounded bg-bambu-dark-tertiary flex items-center justify-center">
                        <span className="text-xs text-bambu-gray">3MF</span>
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white truncate">
                        {archive.print_name || archive.filename}
                      </p>
                      <div className="flex items-center gap-2 text-xs">
                        <span className={FAILED.has(print.status) ? 'text-red-700 dark:text-red-400' : print.status === 'completed' ? 'text-green-700 dark:text-green-400' : 'text-bambu-gray'}>
                          {t(`archives.runLog.status.${print.status}`, { defaultValue: print.status })}
                        </span>
                        {print.colors && (
                          <div className="flex gap-0.5">
                            {print.colors.split(',').map((color, i) => (
                              <div
                                key={i}
                                className="w-3 h-3 rounded-full border border-black/20"
                                style={{ backgroundColor: color }}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </button>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-bambu-gray">{t('archives.calendar.noPrints')}</p>
            )}
          </>
        ) : (
          <div className="text-center py-8">
            <p className="text-sm text-bambu-gray">{t('archives.calendar.selectDay')}</p>
          </div>
        )}
      </div>
    </div>
  );
}
