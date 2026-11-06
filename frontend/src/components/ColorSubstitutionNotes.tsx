import { AlertCircle } from 'lucide-react';
import type { ColorSubstitution } from '../api/client';
import { getColorName } from '../utils/colors';

/**
 * One line per slot sent with a spool of another colour than the file asks
 * for (#3279). Shared by the queue row and the compact history row, so a job
 * says the same thing while it prints and once it is done.
 */
export function ColorSubstitutionNotes({
  substitutions,
  t,
  className,
}: {
  substitutions: ColorSubstitution[] | null | undefined;
  t: (key: string, options?: Record<string, unknown>) => string;
  className: string;
}) {
  if (!substitutions || substitutions.length === 0) return null;
  const label = (hex: string, type: string) => `${getColorName(hex, type)} (${hex})`;
  return (
    <>
      {substitutions.map((sub) => (
        <p
          key={`color-sub-${sub.slot_id}`}
          className={`${className} flex items-start gap-1 text-amber-700 dark:text-amber-400`}
          title={t('queue.colorSubstitution.rowTooltip')}
        >
          <AlertCircle className="w-3 h-3 mt-0.5 flex-shrink-0" />
          <span>
            {t('queue.colorSubstitution.rowBadge', {
              slot: sub.slot_id,
              got: label(sub.got, sub.type),
              wanted: label(sub.wanted, sub.type),
            })}
          </span>
        </p>
      ))}
    </>
  );
}
