import { useMemo, useRef, useState, useEffect } from 'react';

import { localDateKey } from '../utils/date';

interface PrintCalendarProps {
  printDates: string[]; // Array of ISO date strings
  months?: number; // How many months to show (default 3)
}

export function PrintCalendar({ printDates, months = 3 }: PrintCalendarProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(0);

  // Measure container width
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width || 0;
      setContainerWidth(width);
    });

    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const { weeks, monthLabels, printCounts } = useMemo(() => {
    // Count prints per day. Bucket by local-tz YYYY-MM-DD so an evening
    // print in a negative-UTC-offset region (e.g. CDT) doesn't get
    // attributed to "tomorrow's" UTC cell while its label renders as
    // today (#1446).
    const counts: Record<string, number> = {};
    printDates.forEach((date) => {
      const day = localDateKey(date);
      if (day) counts[day] = (counts[day] || 0) + 1;
    });

    // Generate weeks for the last N months
    const today = new Date();
    const startDate = new Date(today);
    startDate.setMonth(startDate.getMonth() - months);
    startDate.setDate(startDate.getDate() - startDate.getDay()); // Start from Sunday

    const weeks: Date[][] = [];
    const monthLabels: { month: string; weekIndex: number }[] = [];
    let currentWeek: Date[] = [];
    let lastMonth = -1;

    const current = new Date(startDate);
    let weekIndex = 0;

    while (current <= today) {
      if (current.getDay() === 0 && currentWeek.length > 0) {
        weeks.push(currentWeek);
        currentWeek = [];
        weekIndex++;
      }

      // Track month labels
      if (current.getMonth() !== lastMonth) {
        monthLabels.push({
          month: current.toLocaleDateString('en-US', { month: 'short' }),
          weekIndex,
        });
        lastMonth = current.getMonth();
      }

      currentWeek.push(new Date(current));
      current.setDate(current.getDate() + 1);
    }

    if (currentWeek.length > 0) {
      weeks.push(currentWeek);
    }

    return { weeks, monthLabels, printCounts: counts };
  }, [printDates, months]);

  const maxCount = Math.max(1, ...Object.values(printCounts));

  const getColor = (count: number) => {
    if (count === 0) return 'bg-bambu-dark';
    const intensity = count / maxCount;
    if (intensity <= 0.25) return 'bg-bambu-green/30';
    if (intensity <= 0.5) return 'bg-bambu-green/50';
    if (intensity <= 0.75) return 'bg-bambu-green/75';
    return 'bg-bambu-green';
  };

  const dayLabels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  // Calculate cell size based on container width
  const numWeeks = weeks.length;
  const dayLabelWidth = 32; // Space for day labels (Mon, Wed, Fri)
  const gap = 2; // Gap between cells
  const availableWidth = containerWidth - dayLabelWidth - 16; // 16px padding
  const calculatedCellSize = numWeeks > 0 ? Math.floor((availableWidth - (numWeeks - 1) * gap) / numWeeks) : 12;

  // Clamp cell size between 8 and 20 pixels
  const cellSize = Math.max(8, Math.min(20, calculatedCellSize));
  const fontSize = cellSize <= 10 ? 10 : 12;

  return (
    <div ref={containerRef} className="w-full flex justify-center">
      {containerWidth > 0 && (
        <div>
          {/* Month labels */}
          <div className="flex mb-1" style={{ marginLeft: dayLabelWidth + 4 }}>
            {monthLabels.map(({ month, weekIndex }, i) => (
              <div
                key={i}
                className="text-bambu-gray"
                style={{
                  fontSize,
                  marginLeft: i === 0 ? 0 : `${(weekIndex - (monthLabels[i - 1]?.weekIndex || 0)) * (cellSize + gap) - 24}px`,
                }}
              >
                {month}
              </div>
            ))}
          </div>

          <div className="flex" style={{ gap }}>
            {/* Day labels */}
            <div className="flex flex-col" style={{ gap, marginRight: 4, width: dayLabelWidth }}>
              {dayLabels.map((day, i) => (
                <div
                  key={day}
                  className="text-bambu-gray flex items-center"
                  style={{
                    width: dayLabelWidth,
                    height: cellSize,
                    fontSize,
                    visibility: i % 2 === 1 ? 'visible' : 'hidden',
                  }}
                >
                  {day}
                </div>
              ))}
            </div>

            {/* Calendar grid */}
            {weeks.map((week, weekIdx) => (
              <div key={weekIdx} className="flex flex-col" style={{ gap }}>
                {[0, 1, 2, 3, 4, 5, 6].map((dayOfWeek) => {
                  const day = week.find((d) => d.getDay() === dayOfWeek);
                  if (!day) {
                    return (
                      <div
                        key={dayOfWeek}
                        style={{ width: cellSize, height: cellSize }}
                      />
                    );
                  }

                  // Keep lookup + "today" comparison on the same local-tz
                  // axis as the buckets — toISOString() would shift these
                  // cells to UTC and break the join (#1446).
                  const dateStr = localDateKey(day);
                  const count = printCounts[dateStr] || 0;
                  const isToday = dateStr === localDateKey(new Date());

                  return (
                    <div
                      key={dayOfWeek}
                      className={`rounded-sm ${getColor(count)} ${isToday ? 'ring-1 ring-white' : ''}`}
                      style={{ width: cellSize, height: cellSize }}
                      title={`${day.toLocaleDateString()}: ${count} print${count !== 1 ? 's' : ''}`}
                    />
                  );
                })}
              </div>
            ))}
          </div>

          {/* Legend */}
          <div className="flex items-center gap-2 mt-3 text-bambu-gray" style={{ fontSize }}>
            <span>Less</span>
            <div className="flex" style={{ gap }}>
              <div className="rounded-sm bg-bambu-dark" style={{ width: cellSize, height: cellSize }} />
              <div className="rounded-sm bg-bambu-green/30" style={{ width: cellSize, height: cellSize }} />
              <div className="rounded-sm bg-bambu-green/50" style={{ width: cellSize, height: cellSize }} />
              <div className="rounded-sm bg-bambu-green/75" style={{ width: cellSize, height: cellSize }} />
              <div className="rounded-sm bg-bambu-green" style={{ width: cellSize, height: cellSize }} />
            </div>
            <span>More</span>
          </div>
        </div>
      )}
    </div>
  );
}
