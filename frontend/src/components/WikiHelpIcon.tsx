import { HelpCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface WikiHelpIconProps {
  /** Relative wiki path, e.g. `features/queue`. Trailing slash is added. */
  path: string;
}

const WIKI_BASE = 'https://wiki.bambuddy.cool';

/**
 * Small `?` icon button that opens the matching wiki page in a new tab.
 * Shipped per docs/onboarding-tour-plan.md Appendix G — the original idea was
 * an in-app iframe modal, but the wiki sets X-Frame-Options DENY and most
 * MkDocs themes do not behave inside an iframe, so we open a new tab and let
 * the browser handle it.
 */
export function WikiHelpIcon({ path }: WikiHelpIconProps) {
  const { t } = useTranslation();
  const label = t('onboarding.helpIcon.openWiki');
  return (
    <a
      href={`${WIKI_BASE}/${path}/`}
      target="_blank"
      rel="noopener noreferrer"
      className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white inline-flex items-center justify-center"
      title={label}
      aria-label={label}
    >
      <HelpCircle className="w-5 h-5" />
    </a>
  );
}
