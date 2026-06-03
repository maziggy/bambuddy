import type { PrinterStatus } from '../api/client';

/** True when manual motion, homing, and extrusion must be disabled. */
export function usePrinterMotionDisabled(status: PrinterStatus | null | undefined): boolean {
  return status?.state === 'RUNNING' || status?.state === 'PAUSE';
}
