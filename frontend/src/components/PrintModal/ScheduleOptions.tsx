import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Calendar, Clock, Hand, Power, Layers, Code, ListOrdered } from 'lucide-react';
import type { ScheduleOptionsProps, ScheduleType } from './types';
import {
  formatDateInput,
  formatTimeInput,
  getDatePlaceholder,
  getTimePlaceholder,
  toDateTimeLocalValue,
} from '../../utils/date';
import { NumberInput } from '../NumberInput';

/**
 * Schedule options component for queue items.
 * Includes schedule type (ASAP/Queue/Schedule), datetime picker,
 * and options for require previous success and auto power off.
 */
export function ScheduleOptionsPanel({
  options,
  onChange,
  schedule,
  canControlPrinter = true,
  showStagger = false,
  printerCount = 0,
  hasGcodeSnippets = false,
  needsReview = false,
}: ScheduleOptionsProps) {
  const { t } = useTranslation();
  const { dateText, timeText, dateFormat, timeFormat, past, dateValid, timeValid, setFields } = schedule;
  const hiddenInputRef = useRef<HTMLInputElement>(null);

  const handleScheduleTypeChange = (scheduleType: ScheduleType) => {
    let scheduledTime = options.scheduledTime;
    if (scheduleType === 'scheduled' && !scheduledTime) {
      const nextHour = new Date();
      nextHour.setHours(nextHour.getHours() + 1, 0, 0, 0);
      scheduledTime = toDateTimeLocalValue(nextHour);
    }
    onChange({
      ...options,
      scheduledTime,
      scheduleType,
      requireManualStart: scheduleType === 'queue' ? options.requireManualStart : false,
    });
  };

  const handleCalendarChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    if (!value) {
      setFields('', '');
      return;
    }
    const [day, clock] = value.split('T');
    const date = new Date(day + 'T12:00');
    // Format the clock independently so even a nonexistent local hour reaches validation.
    const time = new Date('2000-01-01T' + clock);
    setFields(formatDateInput(date, dateFormat), formatTimeInput(time, timeFormat));
  };

  const openCalendar = () => {
    hiddenInputRef.current?.showPicker();
  };

  return (
    <div className="space-y-4">
      {/* Schedule type */}
      <div>
        <label className="block text-sm text-bambu-gray mb-2">{t('printModal.whenToPrint')}</label>
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
            {t('printModal.asap')}
          </button>
          <button
            type="button"
            className={`flex-1 px-2 py-2 rounded-lg border text-sm flex items-center justify-center gap-1.5 transition-colors ${
              options.scheduleType === 'queue'
                ? 'bg-bambu-green border-bambu-green text-white'
                : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
            }`}
            onClick={() => handleScheduleTypeChange('queue')}
          >
            <ListOrdered className="w-4 h-4" />
            {t('printModal.queue')}
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
            {t('printModal.schedule')}
          </button>
        </div>
      </div>

      {/* Scheduled time input */}
      {options.scheduleType === 'scheduled' && (
        <div>
          <label className="block text-sm text-bambu-gray mb-1">{t('printModal.dateTime')}</label>
          <div className="flex gap-2">
            {/* Date input */}
            <div className="flex-1 relative">
              <input
                type="text"
                className={`w-full px-3 py-2 pr-10 bg-bambu-dark border rounded-lg text-white focus:outline-none ${
                  dateValid && !past
                    ? 'border-bambu-dark-tertiary focus:border-bambu-green'
                    : 'border-red-500'
                }`}
                aria-invalid={!dateValid || past}
                value={dateText}
                onChange={(e) => setFields(e.target.value, timeText)}
                placeholder={getDatePlaceholder(dateFormat)}
              />
              <button
                type="button"
                onClick={openCalendar}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
                title={t('printModal.openCalendar')}
              >
                <Calendar className="w-4 h-4" />
              </button>
              {/* Hidden datetime-local anchored here so the native picker opens near the date field */}
              <input
                ref={hiddenInputRef}
                type="datetime-local"
                className="absolute top-0 left-0 w-0 h-0 opacity-0 pointer-events-none"
                value={Number.isFinite(schedule.start) ? toDateTimeLocalValue(new Date(schedule.start)) : ''}
                onChange={handleCalendarChange}
                tabIndex={-1}
              />
            </div>
            {/* Time input */}
            <div className="w-32">
              <input
                type="text"
                className={`w-full px-3 py-2 bg-bambu-dark border rounded-lg text-white focus:outline-none ${
                  timeValid && !past
                    ? 'border-bambu-dark-tertiary focus:border-bambu-green'
                    : 'border-red-500'
                }`}
                aria-invalid={!timeValid || past}
                value={timeText}
                onChange={(e) => setFields(dateText, e.target.value)}
                placeholder={getTimePlaceholder(timeFormat)}
              />
            </div>
          </div>
          {(!dateValid || !timeValid || past) && (
            <p role="alert" className="mt-1 text-xs text-red-700 dark:text-red-400">
              {t(past ? 'printModal.scheduleInPast' : 'printModal.invalidDateTime')}
            </p>
          )}
        </div>
      )}

      {/* Their jobs always wait, so the checkbox would only mislead (#1620) */}
      {needsReview && (
        <p className="flex items-start gap-1.5 text-sm text-bambu-gray">
          <Hand className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          {t('printModal.awaitingReviewNote')}
        </p>
      )}

      {/* Manual start */}
      {!needsReview && options.scheduleType === 'queue' && (
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="requireManualStart"
            checked={options.requireManualStart}
            onChange={(e) => onChange({ ...options, requireManualStart: e.target.checked })}
            className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
          />
          <label htmlFor="requireManualStart" className="text-sm flex items-center gap-1 text-bambu-gray">
            <Hand className="w-3.5 h-3.5" />
            {t('printModal.requireManualStart')}
          </label>
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
          {t('printModal.requirePreviousSuccess')}
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
          {t('printModal.autoOffAfter')}
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
      {showStagger && options.scheduleType !== 'queue' && (
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
                  <NumberInput
                    min={1}
                    max={printerCount}
                    value={options.staggerGroupSize}
                    onChange={(v) => onChange({ ...options, staggerGroupSize: v })}
                    fallback={1}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
                  />
                </div>
                <div className="flex-1">
                  <label className="block text-xs text-bambu-gray mb-1">{t('printModal.staggerInterval', 'Interval (min)')}</label>
                  <NumberInput
                    min={1}
                    max={60}
                    value={options.staggerIntervalMinutes}
                    onChange={(v) => onChange({ ...options, staggerIntervalMinutes: v })}
                    fallback={1}
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
          ? t('printModal.helpAsap')
          : options.scheduleType === 'scheduled'
          ? t('printModal.helpSchedule')
          : t('printModal.helpQueue')}
      </p>
    </div>
  );
}
