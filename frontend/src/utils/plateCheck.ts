import type { TFunction } from 'i18next';

const PLATE_CHECK_FAILURE_REASONS = [
  'no_camera_frame',
  'decode_failed',
  'analysis_error',
  'opencv_missing',
  'not_calibrated',
] as const;

/** Why a plate check could not run, in the reader's language. */
export function plateCheckFailureText(t: TFunction, reason: string | null | undefined): string {
  const known = (PLATE_CHECK_FAILURE_REASONS as readonly string[]).includes(reason ?? '');
  return t(`printers.plateDetection.unavailableReasons.${known ? reason : 'error'}`);
}
