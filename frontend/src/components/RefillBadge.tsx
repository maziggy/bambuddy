import { useTranslation } from 'react-i18next';

/**
 * "Refill pack" badge — marks a spool-less refill coil (Bambu's refill SKU).
 * One control so wording and styling stay consistent everywhere it appears:
 * a neutral informational blue and a noun phrase, so it can't be read as a
 * "you should refill this" warning (the old amber REFILL badge could).
 */
export function RefillBadge({ className }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-sky-100 dark:bg-sky-500/20 text-sky-700 dark:text-sky-300 ${className ?? ''}`}
    >
      {t('common.refillBadge', 'Refill pack')}
    </span>
  );
}
