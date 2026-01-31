import { useTranslation } from 'react-i18next';
import { Calendar, Clock, Hand, Power } from 'lucide-react';
import { getMinDateTime } from '../../utils/amsHelpers';
import type { ScheduleOptionsProps, ScheduleType } from './types';

/**
 * Schedule options component for queue items.
 * Includes schedule type (ASAP/Scheduled/Queue Only), datetime picker,
 * and options for require previous success and auto power off.
 */
export function ScheduleOptionsPanel({ options, onChange }: ScheduleOptionsProps) {
  const { t } = useTranslation();
  const handleScheduleTypeChange = (scheduleType: ScheduleType) => {
    onChange({ ...options, scheduleType });
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
          <input
            type="datetime-local"
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            value={options.scheduledTime}
            onChange={(e) => onChange({ ...options, scheduledTime: e.target.value })}
            min={getMinDateTime()}
            required
          />
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
          {t('printModal.powerOffWhenDone')}
        </label>
      </div>

      {/* Help text */}
      <p className="text-xs text-bambu-gray">
        {options.scheduleType === 'asap'
          ? t('printModal.immediateHelp')
          : options.scheduleType === 'scheduled'
          ? t('printModal.scheduledHelp')
          : t('printModal.stagedHelp')}
      </p>
    </div>
  );
}
