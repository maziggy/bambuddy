// Shared shape + class mapping for the Obico AI failure-detection surfaces
// (the printer card badge and the detail modal), so the two cannot disagree
// about what a given backend class means.

export type AiDetectionClass = 'failure' | 'warning' | 'safe' | 'error' | 'unknown' | 'idle';

export interface AiDetection {
  class: string;
  frame_count: number;
  score: number;
  // Why the most recent poll produced no verdict. null when the last poll
  // succeeded, and also when the viewer lacks settings:read — the backend
  // withholds the reason (it can name configured URLs) but still sends the
  // 'error' class, because "your print is not being watched" is not
  // configuration.
  error?: string | null;
}

/**
 * Canonical display class for a printer's detection state.
 *
 * `undefined` means the printer has no monitored print right now -> 'idle'.
 *
 * Anything unrecognised falls back to 'unknown', deliberately NOT to 'safe'.
 * Collapsing every non-failure/warning class into a green "Safe" badge is
 * exactly what made a printer whose detection had never once succeeded look
 * identical to a healthy one (#2952).
 */
export function aiDetectionClass(detection?: AiDetection): AiDetectionClass {
  if (!detection) return 'idle';
  switch (detection.class) {
    case 'failure':
    case 'warning':
    case 'safe':
    case 'error':
      return detection.class;
    default:
      return 'unknown';
  }
}

/** True when the class represents an actual verdict, so score/frames mean something. */
export function hasVerdict(cls: AiDetectionClass): boolean {
  return cls === 'failure' || cls === 'warning' || cls === 'safe';
}
