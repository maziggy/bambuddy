/**
 * Reading the scheduler's `waiting_reason` (#3074).
 *
 * The backend writes one sentence per held queue item and encodes one bit in
 * its shape: a reason made only of `Busy: ...` clauses means the job starts by
 * itself once a printer frees up, and anything else means somebody has to do
 * something — load filament, switch a printer on, confirm a plate. The
 * scheduler uses that bit to decide whether a hold is worth a notification
 * (`PrintScheduler._is_busy_only`), and the UI needs the same distinction to
 * decide whether a held item still belongs on a forecast.
 *
 * Kept in one place on this side too, so the two halves of the contract are one
 * grep apart.
 */

/** Does this reason mean "waiting its turn", rather than "waiting for you"? */
export function isBusyOnlyWaitingReason(reason: string | null | undefined): boolean {
  if (!reason) return false;
  return reason
    .split(' | ')
    .map(part => part.trim())
    .every(part => part.startsWith('Busy:'));
}
