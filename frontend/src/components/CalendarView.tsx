import { useState, useMemo } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import type { Archive } from '../api/client';
import { api } from '../api/client';
import { parseUTCDate } from '../utils/date';

interface CalendarViewProps {
  archives: Archive[];
  onArchiveClick?: (archive: Archive) => void;
  highlightedArchiveId?: number | null;
}

function getDaysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

function getFirstDayOfMonth(year: number, month: number): number {
  return new Date(year, month, 1).getDay();
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'
];

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

export function CalendarView({ archives, onArchiveClick, highlightedArchiveId }: CalendarViewProps) {
  const today = new Date();
  const [currentMonth, setCurrentMonth] = useState(today.getMonth());
  const [currentYear, setCurrentYear] = useState(today.getFullYear());
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedArchiveId, setSelectedArchiveId] = useState<number | null>(null);

  // Group archives by date (using local timezone from UTC timestamps)
  const archivesByDate = useMemo(() => {
    const map = new Map<string, Archive[]>();
    archives.forEach(archive => {
      const date = parseUTCDate(archive.completed_at || archive.created_at) || new Date();
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
      const existing = map.get(key) || [];
      existing.push(archive);
      map.set(key, existing);
    });
    return map;
  }, [archives]);

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

  const selectedArchives = selectedDate ? archivesByDate.get(selectedDate) || [] : [];

  // Clear selected archive when date changes
  const handleDateSelect = (dateKey: string | null) => {
    if (dateKey !== selectedDate) {
      setSelectedArchiveId(null);
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
          >
            <ChevronLeft className="w-5 h-5 text-bambu-gray" />
          </button>
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-white">
              {MONTH_NAMES[currentMonth]} {currentYear}
            </h2>
            <button
              onClick={goToToday}
              className="px-2 py-1 text-xs bg-bambu-dark-tertiary hover:bg-bambu-green/20 text-bambu-gray hover:text-white rounded transition-colors"
            >
              Today
            </button>
          </div>
          <button
            onClick={nextMonth}
            className="p-2 hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
          >
            <ChevronRight className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        {/* Day headers */}
        <div className="grid grid-cols-7 gap-1 mb-1">
          {DAY_NAMES.map(day => (
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
            const dayArchives = archivesByDate.get(dateKey) || [];
            const hasArchives = dayArchives.length > 0;
            const isToday = day === today.getDate() && currentMonth === today.getMonth() && currentYear === today.getFullYear();
            const isSelected = dateKey === selectedDate;
            const successCount = dayArchives.filter(a => a.status === 'completed').length;
            const failedCount = dayArchives.filter(a => a.status === 'failed').length;

            return (
              <button
                key={day}
                onClick={() => handleDateSelect(isSelected ? null : dateKey)}
                className={`aspect-square rounded-lg p-1 flex flex-col items-center justify-center transition-colors relative ${
                  isSelected
                    ? 'bg-bambu-green text-white'
                    : isToday
                    ? 'bg-bambu-green/20 text-white ring-2 ring-bambu-green'
                    : hasArchives
                    ? 'bg-bambu-dark-tertiary hover:bg-bambu-dark-tertiary/70 text-white'
                    : 'hover:bg-bambu-dark-tertiary/50 text-bambu-gray'
                }`}
              >
                <span className={`text-sm font-medium ${isToday && !isSelected ? 'text-bambu-green' : ''}`}>
                  {day}
                </span>
                {hasArchives && (
                  <div className="absolute bottom-1 left-1/2 -translate-x-1/2 flex items-center gap-1">
                    <div className={`w-2 h-2 rounded-full ${
                      failedCount > 0 && successCount === 0
                        ? 'bg-red-400'
                        : failedCount > 0
                        ? 'bg-yellow-400'
                        : 'bg-green-400'
                    }`} />
                    <span className="text-xs font-medium">{dayArchives.length}</span>
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
              <div className="text-2xl font-bold text-white">
                {archives.filter(a => {
                  const d = new Date(a.completed_at || a.created_at);
                  return d.getMonth() === currentMonth && d.getFullYear() === currentYear;
                }).length}
              </div>
              <div className="text-xs text-bambu-gray">Prints this month</div>
            </div>
            <div>
              <div className="text-2xl font-bold text-green-400">
                {archives.filter(a => {
                  const d = new Date(a.completed_at || a.created_at);
                  return d.getMonth() === currentMonth && d.getFullYear() === currentYear && a.status === 'completed';
                }).length}
              </div>
              <div className="text-xs text-bambu-gray">Successful</div>
            </div>
            <div>
              <div className="text-2xl font-bold text-red-400">
                {archives.filter(a => {
                  const d = new Date(a.completed_at || a.created_at);
                  return d.getMonth() === currentMonth && d.getFullYear() === currentYear && a.status === 'failed';
                }).length}
              </div>
              <div className="text-xs text-bambu-gray">Failed</div>
            </div>
          </div>
        </div>
      </div>

      {/* Selected day details */}
      <div className="lg:w-80 bg-bambu-dark rounded-xl p-4">
        {selectedDate ? (
          <>
            <h3 className="text-sm font-medium text-bambu-gray mb-3">
              {new Date(selectedDate + 'T12:00:00').toLocaleDateString('en-US', {
                weekday: 'long',
                month: 'long',
                day: 'numeric',
                year: 'numeric'
              })}
            </h3>
            {selectedArchives.length > 0 ? (
              <div className="calendar-scroll space-y-2 max-h-96 overflow-y-auto">
                {selectedArchives.map(archive => {
                  const isHighlighted = archive.id === selectedArchiveId || archive.id === highlightedArchiveId;
                  return (
                  <button
                    key={archive.id}
                    onClick={() => {
                      setSelectedArchiveId(archive.id);
                      onArchiveClick?.(archive);
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
                        <span className={archive.status === 'failed' ? 'text-red-400' : 'text-green-400'}>
                          {archive.status === 'failed' ? 'Failed' : 'Completed'}
                        </span>
                        {archive.filament_color && (
                          <div className="flex gap-0.5">
                            {archive.filament_color.split(',').map((color, i) => (
                              <div
                                key={i}
                                className="w-3 h-3 rounded-full border border-white/20"
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
              <p className="text-sm text-bambu-gray">No prints on this day</p>
            )}
          </>
        ) : (
          <div className="text-center py-8">
            <p className="text-sm text-bambu-gray">Select a day to see prints</p>
          </div>
        )}
      </div>
    </div>
  );
}
