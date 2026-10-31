import { useTranslation } from 'react-i18next';
import { AlertTriangle } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';

export function OctoEverywhereLimitNotice() {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();

  if (!hasPermission('settings:read')) return null;

  return (
    <div role="alert" className="flex items-start gap-2 p-3 bg-amber-50 dark:bg-amber-900/30 border border-amber-300 dark:border-amber-700 rounded text-sm text-amber-800 dark:text-amber-200">
      <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
      <div>
        <p>{t('octoeverywhere.limitReached')}</p>
        <a
          href="https://octoeverywhere.com/gadgetapi"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block mt-1 underline hover:no-underline"
        >
          {t('octoeverywhere.setupBilling')}
        </a>
      </div>
    </div>
  );
}
