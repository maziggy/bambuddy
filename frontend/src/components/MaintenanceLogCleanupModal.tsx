import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ConfirmModal } from './ConfirmModal';

// Must match the bounds of older_than_days in backend/app/api/routes/maintenance.py.
const MIN_DAYS = 1;
const MAX_DAYS = 36500;

interface MaintenanceLogCleanupModalProps {
  /** The printer whose log is cleaned up; none for every printer the user can see. */
  printerName?: string;
  isLoading: boolean;
  onConfirm: (olderThanDays: number) => void;
  onCancel: () => void;
}

/** Asks how old maintenance log entries must be to be deleted, and confirms it. */
export function MaintenanceLogCleanupModal({ printerName, isLoading, onConfirm, onCancel }: MaintenanceLogCleanupModalProps) {
  const { t } = useTranslation();
  const [days, setDays] = useState('365');
  const parsed = Number(days);
  const valid = days.trim() !== '' && Number.isInteger(parsed) && parsed >= MIN_DAYS && parsed <= MAX_DAYS;

  return (
    <ConfirmModal
      title={t('maintenance.cleanUpTitle')}
      message={
        printerName === undefined
          ? t('maintenance.cleanUpMessageAll')
          : t('maintenance.cleanUpMessage', { printer: printerName })
      }
      confirmText={t('common.delete')}
      variant="danger"
      overlayZIndex="z-[60]"
      isLoading={isLoading}
      confirmDisabled={!valid}
      onConfirm={() => {
        if (valid) onConfirm(parsed);
      }}
      onCancel={onCancel}
    >
      <label htmlFor="maintenance-cleanup-days" className="block text-sm text-bambu-gray mb-1">
        {t('maintenance.cleanUpOlderThan')}
      </label>
      <input
        id="maintenance-cleanup-days"
        type="number"
        inputMode="numeric"
        min={MIN_DAYS}
        max={MAX_DAYS}
        step={1}
        value={days}
        onChange={(e) => setDays(e.target.value)}
        className="w-32 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
        autoFocus
      />
    </ConfirmModal>
  );
}
