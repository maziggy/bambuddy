import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/** Shown instead of the login page while a stored sign-in can't be checked
 *  because Bambuddy doesn't answer; AuthContext keeps retrying (#3360). */
export function AuthRetrying() {
  const { t } = useTranslation();
  return (
    <div role="status" className="flex min-h-screen flex-col items-center justify-center gap-3 bg-bambu-dark text-bambu-gray">
      <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      <span>{t('login.retrying', "Can't reach Bambuddy. Retrying…")}</span>
    </div>
  );
}
