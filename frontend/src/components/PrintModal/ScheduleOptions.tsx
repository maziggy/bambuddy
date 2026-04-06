import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Calendar, Clock, Hand, Power, Layers, Code } from 'lucide-react';
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
  canControlPrinter = true,
  showStagger = false,
  printerCount = 0,
  hasGcodeSnippets = false,
}: ScheduleOptionsProps) {
  const { t } = useTranslation();
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
              {/* Hidden datetime-local anchored here so the native picker opens near the date field */}
              <input
                ref={hiddenInputRef}
                type="datetime-local"
                className="absolute top-0 left-0 w-0 h-0 opacity-0 pointer-events-none"
                value={options.scheduledTime}
                onChange={handleCalendarChange}
                tabIndex={-1}
              />
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
          disabled={!canControlPrinter}
          className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green disabled:opacity-50"
        />
        <label htmlFor="autoOffAfter" className={`text-sm flex items-center gap-1 ${canControlPrinter ? 'text-bambu-gray' : 'text-bambu-gray/50'}`}>
          <Power className="w-3.5 h-3.5" />
          Power off printer when done
        </label>
      </div>

      {/* G-code injection */}
      {hasGcodeSnippets && (
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="gcodeInjection"
            checked={options.gcodeInjection}
            onChange={(e) => onChange({ ...options, gcodeInjection: e.target.checked })}
            className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
          />
          <label htmlFor="gcodeInjection" className="text-sm flex items-center gap-1 text-bambu-gray">
            <Code className="w-3.5 h-3.5" />
            {t('printModal.gcodeInjection', 'Inject auto-print G-code')}
          </label>
        </div>
      )}

      {/* Stagger start */}
      {showStagger && options.scheduleType !== 'manual' && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="staggerEnabled"
              checked={options.staggerEnabled}
              onChange={(e) => onChange({ ...options, staggerEnabled: e.target.checked })}
              className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
            />
            <label htmlFor="staggerEnabled" className="text-sm flex items-center gap-1 text-bambu-gray">
              <Layers className="w-3.5 h-3.5" />
              {t('printModal.staggerPrinterStarts', 'Stagger printer starts')}
            </label>
          </div>

          {options.staggerEnabled && (
            <div className="ml-6 space-y-3">
              <div className="flex gap-3">
                <div className="flex-1">
                  <label className="block text-xs text-bambu-gray mb-1">{t('printModal.staggerGroupSize', 'Group size')}</label>
                  <input
                    type="number"
                    min={1}
                    max={printerCount}
                    value={options.staggerGroupSize}
                    onChange={(e) => onChange({ ...options, staggerGroupSize: Math.max(1, parseInt(e.target.value) || 1) })}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
                  />
                </div>
                <div className="flex-1">
                  <label className="block text-xs text-bambu-gray mb-1">{t('printModal.staggerInterval', 'Interval (min)')}</label>
                  <input
                    type="number"
                    min={1}
                    max={60}
                    value={options.staggerIntervalMinutes}
                    onChange={(e) => onChange({ ...options, staggerIntervalMinutes: Math.max(1, parseInt(e.target.value) || 1) })}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
                  />
                </div>
              </div>
              {printerCount > 0 && (() => {
                const groupCount = Math.ceil(printerCount / options.staggerGroupSize);
                const lastGroupSize = printerCount % options.staggerGroupSize;
                const totalMinutes = (groupCount - 1) * options.staggerIntervalMinutes;
                return (
                  <p className="text-xs text-bambu-gray">
                    {t('printModal.staggerPreview', '{{printers}} printers → {{groups}} groups of {{size}}, starting every {{interval}} min', {
                      printers: printerCount,
                      groups: groupCount,
                      size: options.staggerGroupSize,
                      interval: options.staggerIntervalMinutes,
                    })}
                    {lastGroupSize !== 0 && options.staggerGroupSize < printerCount
                      ? ` (${t('printModal.staggerLastGroup', 'last group: {{count}}', { count: lastGroupSize })})`
                      : ''}
                    {groupCount > 1
                      ? ` (${t('printModal.staggerTotal', 'total: {{minutes}} min', { minutes: totalMinutes })})`
                      : ''}
                  </p>
                );
              })()}
            </div>
          )}
        </div>
      )}

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
