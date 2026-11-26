import { chunkForPrint } from './labelPrintJobs';
import {
  DEFAULT_ROLL_LENGTH_MM,
  DEFAULT_SPOOL_LABEL_FIELDS,
  ROLL_MAX_LENGTH_MM,
  ROLL_MIN_LENGTH_MM,
  SPOOL_LABEL_FIELDS,
  type DirectPrintLabelsRequest,
  type SpoolLabelField,
} from '../api/client';

// Where the labels dialog keeps its options (shared with the dialog, #3612).
export const LABEL_PREFS_STORAGE_KEY = 'bambuddy-label-print-options';
// "Print label after saving" in the Add spool dialog, remembered per device.
export const AUTO_PRINT_LABEL_KEY = 'bambuddy.autoPrintLabel';
export const DEFAULT_ROLL_TAPE = '62';

export function clampRollLength(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_ROLL_LENGTH_MM;
  return Math.min(ROLL_MAX_LENGTH_MM, Math.max(ROLL_MIN_LENGTH_MM, value));
}

export function loadAutoPrintLabel(): boolean {
  try {
    return localStorage.getItem(AUTO_PRINT_LABEL_KEY) === '1';
  } catch {
    return false;
  }
}

export function saveAutoPrintLabel(on: boolean): void {
  try {
    localStorage.setItem(AUTO_PRINT_LABEL_KEY, on ? '1' : '0');
  } catch {
    // Storage disabled: the choice just isn't remembered.
  }
}

/**
 * The request for labels printed after a spool is saved (#3612): the roll
 * fields and length remembered by the labels dialog (whichever template it
 * showed last; the length only when it was set for the configured tape), else
 * the roll with the configured tape, 40 mm and the default fields.
 */
function autoPrintRequest(spoolIds: number[], configuredTape: string): DirectPrintLabelsRequest {
  let fields: SpoolLabelField[] = DEFAULT_SPOOL_LABEL_FIELDS;
  let length = DEFAULT_ROLL_LENGTH_MM;
  try {
    const raw = JSON.parse(localStorage.getItem(LABEL_PREFS_STORAGE_KEY) ?? 'null') as Record<string, unknown> | null;
    // Roll fields and length are kept apart from the template last used, so
    // they apply even when the dialog last showed a sheet format.
    if (raw) {
      const stored = (raw.fields as Record<string, unknown> | undefined)?.roll;
      if (Array.isArray(stored)) {
        fields = stored.filter((f): f is SpoolLabelField => (SPOOL_LABEL_FIELDS as readonly string[]).includes(f));
      }
      if ((!raw.rollTape || raw.rollTape === configuredTape) && typeof raw.rollLength === 'number') {
        length = clampRollLength(raw.rollLength);
      }
    }
  } catch {
    // Unreadable options: the defaults print.
  }
  return {
    spool_ids: spoolIds,
    template: 'roll',
    roll: { tape: configuredTape, length_mm: length },
    monochrome: true,
    fields,
  };
}

/** The same labels as requests that each stay within the server's caps per print. */
export function autoPrintRequests(spoolIds: number[], configuredTape: string): DirectPrintLabelsRequest[] {
  const whole = autoPrintRequest(spoolIds, configuredTape);
  const length = whole.roll?.length_mm ?? DEFAULT_ROLL_LENGTH_MM;
  return chunkForPrint(spoolIds, length).map((ids) => ({ ...whole, spool_ids: ids }));
}
