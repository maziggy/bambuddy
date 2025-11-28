import { useMemo } from 'react';

interface PrintCalendarProps {
  printDates: string[]; // Array of ISO date strings
  months?: number; // How many months to show (default 3)
}

export function PrintCalendar({ printDates, months = 3 }: PrintCalendarProps) {
  const { weeks, monthLabels, printCounts } = useMemo(() => {
    // Count prints per day
    const counts: Record<string, number> = {};
    printDates.forEach((date) => {
      const day = date.split('T')[0];
      counts[day] = (counts[day] || 0) + 1;
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

  return (
    <div className="overflow-x-auto">
      {/* Month labels */}
      <div className="flex mb-1 ml-8">
        {monthLabels.map(({ month, weekIndex }, i) => (
          <div
            key={i}
            className="text-xs text-bambu-gray"
            style={{ marginLeft: i === 0 ? 0 : `${(weekIndex - (monthLabels[i - 1]?.weekIndex || 0)) * 14 - 24}px` }}
          >
            {month}
          </div>
        ))}
      </div>

      <div className="flex gap-0.5">
        {/* Day labels */}
        <div className="flex flex-col gap-0.5 mr-1">
          {dayLabels.map((day, i) => (
            <div
              key={day}
              className="h-3 text-xs text-bambu-gray flex items-center"
              style={{ visibility: i % 2 === 1 ? 'visible' : 'hidden' }}
            >
              {day}
            </div>
          ))}
        </div>

        {/* Calendar grid */}
        {weeks.map((week, weekIndex) => (
          <div key={weekIndex} className="flex flex-col gap-0.5">
            {[0, 1, 2, 3, 4, 5, 6].map((dayOfWeek) => {
              const day = week.find((d) => d.getDay() === dayOfWeek);
              if (!day) {
                return <div key={dayOfWeek} className="w-3 h-3" />;
              }

              const dateStr = day.toISOString().split('T')[0];
              const count = printCounts[dateStr] || 0;
              const isToday = dateStr === new Date().toISOString().split('T')[0];

              return (
                <div
                  key={dayOfWeek}
                  className={`w-3 h-3 rounded-sm ${getColor(count)} ${isToday ? 'ring-1 ring-white' : ''}`}
                  title={`${day.toLocaleDateString()}: ${count} print${count !== 1 ? 's' : ''}`}
                />
              );
            })}
          </div>
        ))}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-2 mt-3 text-xs text-bambu-gray">
        <span>Less</span>
        <div className="flex gap-0.5">
          <div className="w-3 h-3 rounded-sm bg-bambu-dark" />
          <div className="w-3 h-3 rounded-sm bg-bambu-green/30" />
          <div className="w-3 h-3 rounded-sm bg-bambu-green/50" />
          <div className="w-3 h-3 rounded-sm bg-bambu-green/75" />
          <div className="w-3 h-3 rounded-sm bg-bambu-green" />
        </div>
        <span>More</span>
      </div>
    </div>
  );
}
