import type { ElementType } from 'react';
import { useTranslation } from 'react-i18next';
import { XCircle, AlertTriangle, CheckCircle2, ExternalLink, Wrench, ServerCog, Bug } from 'lucide-react';
import type { LogFinding, LogFindingCategory, SystemHealthResult } from '../api/client';

const WIKI_TROUBLESHOOTING = 'https://wiki.bambuddy.cool/reference/troubleshooting/';

const CATEGORY_META: Record<LogFindingCategory, { icon: ElementType; badgeClass: string }> = {
  layer8: { icon: Wrench, badgeClass: 'bg-bambu-green/15 text-bambu-green border-bambu-green/30' },
  environment: { icon: ServerCog, badgeClass: 'bg-amber-100 dark:bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-300 dark:border-amber-500/30' },
  bug: { icon: Bug, badgeClass: 'bg-red-100 dark:bg-red-500/15 text-red-700 dark:text-red-300 border-red-300 dark:border-red-500/30' },
};

/**
 * One detected log-health finding. Cause/fix/name text is rendered from i18n
 * keyed by signature_id; an unknown signature (frontend older than backend)
 * still renders gracefully via the defaultValue fallbacks.
 */
function FindingCard({ finding }: { finding: LogFinding }) {
  const { t } = useTranslation();
  const id = finding.signature_id;
  const name = t(`systemHealth.signature.${id}.name`, { defaultValue: id });
  const cause = t(`systemHealth.signature.${id}.cause`, { defaultValue: '' });
  const fix = t(`systemHealth.signature.${id}.fix`, { defaultValue: '' });
  const meta = CATEGORY_META[finding.category] ?? CATEGORY_META.bug;
  const CategoryIcon = meta.icon;
  const SeverityIcon = finding.severity === 'error' ? XCircle : AlertTriangle;
  const severityColor = finding.severity === 'error' ? 'text-red-600 dark:text-red-400' : 'text-amber-600 dark:text-amber-400';

  return (
    <div className="bg-bambu-dark rounded-lg border border-bambu-dark-tertiary p-4 space-y-2">
      <div className="flex items-start gap-3">
        <SeverityIcon className={`w-5 h-5 flex-shrink-0 mt-0.5 ${severityColor}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-white">{name}</span>
            <span
              className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border ${meta.badgeClass}`}
            >
              <CategoryIcon className="w-3 h-3" />
              {t(`systemHealth.category.${finding.category}`)}
            </span>
          </div>
          {cause && <p className="text-xs text-bambu-gray mt-1">{cause}</p>}
        </div>
      </div>

      {fix && (
        <div className="text-xs text-white/90 bg-bambu-dark-secondary rounded px-3 py-2">
          <span className="text-bambu-green font-medium">{t('systemHealth.fixLabel')}</span> {fix}
        </div>
      )}

      <div className="text-xs font-mono text-bambu-gray/70 bg-bambu-dark-secondary rounded px-3 py-2 break-all">
        {finding.sample}
      </div>

      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-xs text-bambu-gray">
          {t('systemHealth.occurrences', { times: finding.count, lastSeen: finding.last_seen })}
        </span>
        <a
          href={`${WIKI_TROUBLESHOOTING}#${finding.wiki_anchor}`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-bambu-green hover:underline"
        >
          {t('systemHealth.learnMore')}
          <ExternalLink className="w-3 h-3" />
        </a>
      </div>
    </div>
  );
}

/**
 * Presentational panel for a log-health scan result. Shared by the System page
 * section and the bug-report bubble so both surfaces look identical.
 */
export function SystemHealthPanel({ result }: { result: SystemHealthResult }) {
  const { t } = useTranslation();

  if (!result.log_available) {
    return (
      <div className="rounded-lg bg-amber-50 dark:bg-amber-500/10 border border-amber-300 dark:border-amber-500/30 px-4 py-3 text-sm text-amber-700 dark:text-amber-300">
        {t('systemHealth.logUnavailable')}
      </div>
    );
  }

  if (result.findings.length === 0) {
    return (
      <div className="rounded-lg bg-bambu-green/10 border border-bambu-green/30 px-4 py-3 text-sm text-bambu-green flex items-center gap-2">
        <CheckCircle2 className="w-5 h-5 flex-shrink-0" />
        <span>{t('systemHealth.clean', { times: result.scanned_entries })}</span>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {result.findings.map((finding) => (
        <FindingCard key={finding.signature_id} finding={finding} />
      ))}
    </div>
  );
}
