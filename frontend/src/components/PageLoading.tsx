import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/** Shown while a page's file loads (#3175). */
export function PageLoading({ fullScreen = false }: { fullScreen?: boolean }) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      aria-label={t('common.loading')}
      className={`flex items-center justify-center ${fullScreen ? 'min-h-screen' : 'py-24'}`}
    >
      <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
    </div>
  );
}
