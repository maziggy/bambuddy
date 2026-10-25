/**
 * The answer of a K-profile write: `confirmed` is false when the printer did
 * not acknowledge it in time. Firmware that predates the acknowledgement never
 * sends one, so that is not a refusal, but it is not "saved" either.
 */
export interface KProfileWriteResult {
  success: boolean;
  confirmed?: boolean;
  message: string;
}

/** Whether the printer left this write unacknowledged. */
export function writeUnconfirmed(result: KProfileWriteResult): boolean {
  return result.confirmed === false;
}

/** The toast for a write the printer accepted, confirmed or not. */
export function kprofileWriteToast(
  result: KProfileWriteResult,
  confirmedKey: string,
): { key: string; type: 'success' | 'warning' } {
  return writeUnconfirmed(result)
    ? { key: 'kProfiles.toast.writeUnconfirmed', type: 'warning' }
    : { key: confirmedKey, type: 'success' };
}
