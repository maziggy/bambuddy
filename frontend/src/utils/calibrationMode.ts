import type { CalibrationMode } from '../api/client';

/** Display order for the off/auto/on segmented controls. */
export const CALIBRATION_MODES: CalibrationMode[] = ['off', 'auto', 'on'];

/**
 * Active-button classes per calibration mode. Each state gets its own colour so
 * the selected value is legible at a glance rather than every choice reading as
 * the same "on" green: Off = red (never), Auto = blue (printer decides),
 * On = green (force every print).
 */
export const CALIBRATION_MODE_ACTIVE: Record<CalibrationMode, string> = {
  off: 'bg-red-500 text-white',
  auto: 'bg-blue-500 text-white',
  on: 'bg-bambu-green text-white',
};

/** Inactive-button classes shared by every mode. */
export const CALIBRATION_MODE_INACTIVE = 'bg-bambu-dark-tertiary text-bambu-gray hover:text-white';
