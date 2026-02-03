import { useState, useEffect, useRef } from 'react';
import { Calendar, Clock, Hand, Power } from 'lucide-react';
import type { ScheduleOptionsProps, ScheduleType } from './types';
import {
  formatDateInput,
  formatTimeInput,
  parseDateInput,
  parseTimeInput,
  getDatePlaceholder,
  getTimePlaceholder,
  toDateTimeLocalValue,
  type DateFormat,
  type TimeFormat,
} from '../../utils/date';

/**
 * Schedule options component for queue items.
 * Includes schedule type (ASAP/Scheduled/Queue Only), datetime picker,
 * and options for require previous success and auto power off.
 */
export function ScheduleOptionsPanel({
  options,
  onChange,
  dateFormat = 'system',
  timeFormat = 'system',
}: ScheduleOptionsProps) {
  const [dateValue, setDateValue] = useState('');
  const [timeValue, setTimeValue] = useState('');
  const [isDateValid, setIsDateValid] = useState(true);
  const [isTimeValid, setIsTimeValid] = useState(true);
  const hiddenInputRef = useRef<HTMLInputElement>(null);
  const isInitializedRef = useRef(false);

  // Initialize or sync from options.scheduledTime
  useEffect(() => {
    if (options.scheduleType !== 'scheduled') {
      isInitializedRef.current = false;
      return;
    }

    // Initialize with default time (now + 1 hour) or from existing value
    if (!isInitializedRef.current) {
      isInitializedRef.current = true;
      let date: Date;

      if (options.scheduledTime) {
        date = new Date(options.scheduledTime);
        if (isNaN(date.getTime())) {
          date = new Date();
          date.setHours(date.getHours() + 1, 0, 0, 0);
        }
      } else {
        date = new Date();
        date.setHours(date.getHours() + 1, 0, 0, 0);
        // Set initial value
        onChange({ ...options, scheduledTime: toDateTimeLocalValue(date) });
      }

      setDateValue(formatDateInput(date, dateFormat as DateFormat));
      setTimeValue(formatTimeInput(date, timeFormat as TimeFormat));
      setIsDateValid(true);
      setIsTimeValid(true);
    }
  }, [options.scheduleType, options.scheduledTime, dateFormat, timeFormat, onChange, options]);

  const handleScheduleTypeChange = (scheduleType: ScheduleType) => {
    onChange({ ...options, scheduleType });
  };

  const updateScheduledTime = (newDateValue: string, newTimeValue: string) => {
    const parsedDate = parseDateInput(newDateValue, dateFormat as DateFormat);
    const parsedTime = parseTimeInput(newTimeValue);

    setIsDateValid(!!parsedDate);
    setIsTimeValid(!!parsedTime);

    if (parsedDate && parsedTime) {
      parsedDate.setHours(parsedTime.hours, parsedTime.minutes, 0, 0);
      const now = new Date();
      if (parsedDate > now) {
        onChange({ ...options, scheduledTime: toDateTimeLocalValue(parsedDate) });
      }
    }
  };

  const handleDateChange = (value: string) => {
    setDateValue(value);
    updateScheduledTime(value, timeValue);
  };

  const handleTimeChange = (value: string) => {
    setTimeValue(value);
    updateScheduledTime(dateValue, value);
  };

  // Handle calendar picker selection
  const handleCalendarChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    if (value) {
      const date = new Date(value);
      if (!isNaN(date.getTime())) {
        setDateValue(formatDateInput(date, dateFormat as DateFormat));
        setTimeValue(formatTimeInput(date, timeFormat as TimeFormat));
        setIsDateValid(true);
        setIsTimeValid(true);
        onChange({ ...options, scheduledTime: value });
      }
    }
  };

  const openCalendar = () => {
    hiddenInputRef.current?.showPicker();
  };

  return (
    <div className="space-y-4">
      {/* Schedule type */}
      <div>
        <label className="block text-sm text-bambu-gray mb-2">When to print</label>
        <div className="flex gap-2">
          <button
            type="button"
            className={`flex-1 px-2 py-2 rounded-lg border text-sm flex items-center justify-center gap-1.5 transition-colors ${
              options.scheduleType === 'asap'
                ? 'bg-bambu-green border-bambu-green text-white'
                : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
            }`}
            onClick={() => handleScheduleTypeChange('asap')}
          >
            <Clock className="w-4 h-4" />
            ASAP
          </button>
          <button
            type="button"
            className={`flex-1 px-2 py-2 rounded-lg border text-sm flex items-center justify-center gap-1.5 transition-colors ${
              options.scheduleType === 'scheduled'
                ? 'bg-bambu-green border-bambu-green text-white'
                : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
            }`}
            onClick={() => handleScheduleTypeChange('scheduled')}
          >
            <Calendar className="w-4 h-4" />
            Scheduled
          </button>
          <button
            type="button"
            className={`flex-1 px-2 py-2 rounded-lg border text-sm flex items-center justify-center gap-1.5 transition-colors ${
              options.scheduleType === 'manual'
                ? 'bg-bambu-green border-bambu-green text-white'
                : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
            }`}
            onClick={() => handleScheduleTypeChange('manual')}
          >
            <Hand className="w-4 h-4" />
            Queue Only
          </button>
        </div>
      </div>

      {/* Scheduled time input */}
      {options.scheduleType === 'scheduled' && (
        <div>
          <label className="block text-sm text-bambu-gray mb-1">Date & Time</label>
          <div className="flex gap-2">
            {/* Date input */}
            <div className="flex-1 relative">
              <input
                type="text"
                className={`w-full px-3 py-2 pr-10 bg-bambu-dark border rounded-lg text-white focus:outline-none ${
                  isDateValid
                    ? 'border-bambu-dark-tertiary focus:border-bambu-green'
                    : 'border-red-500'
                }`}
                value={dateValue}
                onChange={(e) => handleDateChange(e.target.value)}
                placeholder={getDatePlaceholder(dateFormat as DateFormat)}
              />
              <button
                type="button"
                onClick={openCalendar}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
                title="Open calendar"
              >
                <Calendar className="w-4 h-4" />
              </button>
            </div>
            {/* Time input */}
            <div className="w-32">
              <input
                type="text"
                className={`w-full px-3 py-2 bg-bambu-dark border rounded-lg text-white focus:outline-none ${
                  isTimeValid
                    ? 'border-bambu-dark-tertiary focus:border-bambu-green'
                    : 'border-red-500'
                }`}
                value={timeValue}
                onChange={(e) => handleTimeChange(e.target.value)}
                placeholder={getTimePlaceholder(timeFormat as TimeFormat)}
              />
            </div>
          </div>
          {/* Hidden datetime-local for calendar picker */}
          <input
            ref={hiddenInputRef}
            type="datetime-local"
            className="sr-only"
            value={options.scheduledTime}
            onChange={handleCalendarChange}
            tabIndex={-1}
          />
          {(!isDateValid || !isTimeValid) && (
            <p className="mt-1 text-xs text-red-400">
              Please enter a valid date and time
            </p>
          )}
        </div>
      )}

      {/* Require previous success */}
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="requirePrevious"
          checked={options.requirePreviousSuccess}
          onChange={(e) => onChange({ ...options, requirePreviousSuccess: e.target.checked })}
          className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
        />
        <label htmlFor="requirePrevious" className="text-sm text-bambu-gray">
          Only start if previous print succeeded
        </label>
      </div>

      {/* Auto power off */}
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="autoOffAfter"
          checked={options.autoOffAfter}
          onChange={(e) => onChange({ ...options, autoOffAfter: e.target.checked })}
          className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
        />
        <label htmlFor="autoOffAfter" className="text-sm text-bambu-gray flex items-center gap-1">
          <Power className="w-3.5 h-3.5" />
          Power off printer when done
        </label>
      </div>

      {/* Help text */}
      <p className="text-xs text-bambu-gray">
        {options.scheduleType === 'asap'
          ? 'Print will start as soon as the printer is idle.'
          : options.scheduleType === 'scheduled'
          ? 'Print will start at the scheduled time if the printer is idle. If busy, it will wait until the printer becomes available.'
          : "Print will be staged but won't start automatically. Use the Start button to release it to the queue."}
      </p>
    </div>
  );
}
