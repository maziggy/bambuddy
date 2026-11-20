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

const FILAMENT_PREFIX = 'Waiting for filament: ';
const HAS_PRIORITY_SUFFIX = ' · has priority';
const PRIORITY_IN_SUFFIX = /^(.*) · priority in (\d+) h$/s;

const NO_MATCH_PREFIX = 'No matching material/color. Waiting on ';
// A translatable clause: at the start of a part or after "; " (cross-model
// reasons are "<model>: <reason>" joined by "; "), with an optional model label.
// Its text runs to the next "; <label>: " or the end, because the filament list
// itself is "; "-joined.
const CLAUSE = new RegExp(
  `(^|; )((?:[^;]*?: )?)(${FILAMENT_PREFIX}|${NO_MATCH_PREFIX.replace(/[./]/g, '\\$&')})(.*?)(?=; [^;:|]+: |$)`,
  'gs',
);

const translateClauses = (t: Translate) => (part: string): string =>
  part.replace(CLAUSE, (_m, lead: string, label: string, prefix: string, what: string) =>
    `${lead}${label}${t(prefix === FILAMENT_PREFIX ? 'queue.waiting.filament' : 'queue.waiting.noMatch', { what })}`,
  );

type Translate = (key: string, options?: Record<string, unknown>) => string;

/**
 * Render the scheduler's English `waiting_reason` in the user's language (#3587).
 *
 * Wire format: " | "-separated parts, optionally followed (at the very end of
 * the whole string) by " · priority in N h" or " · has priority". Only the
 * "Waiting for filament: " prefix and that suffix are translated; every other
 * part (Busy, offline, matcher text) stays as the backend wrote it.
 */
export function formatWaitingReason(reason: string, t: Translate): string {
  let body = reason;
  let suffix = '';
  if (body.endsWith(HAS_PRIORITY_SUFFIX)) {
    body = body.slice(0, -HAS_PRIORITY_SUFFIX.length);
    suffix = t('queue.waiting.hasPriority');
  } else {
    const m = PRIORITY_IN_SUFFIX.exec(body);
    if (m) {
      body = m[1];
      suffix = t('queue.waiting.priorityIn', { hours: m[2] });
    }
  }
  const text = body
    .split(' | ')
    .map(translateClauses(t))
    .join(' | ');
  return suffix ? `${text} · ${suffix}` : text;
}
