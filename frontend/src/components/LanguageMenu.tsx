import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Globe } from 'lucide-react';
import { LanguageSelect } from './LanguageSelect';
import { useFooterPopover } from '../hooks/useFooterPopover';

const MENU_WIDTH = 220;

/**
 * Language button for the sidebar footer when authentication is off (#3623):
 * there is no account menu then, but the interface language is still a
 * per-browser choice. Rendered in a portal, like AccountMenu, so the
 * scrolling footer cannot clip it.
 */
export function LanguageMenu() {
  const { t } = useTranslation();
  const { buttonRef, menuRef, isOpen, style, toggle } = useFooterPopover(MENU_WIDTH);

  return (
    <>
      <button
        ref={buttonRef}
        onClick={toggle}
        className={`p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors ${
          isOpen ? 'text-bambu-green' : 'text-bambu-gray-light hover:text-white'
        }`}
        title={t('languageMenu.label')}
        aria-label={t('languageMenu.label')}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
      >
        <Globe className="w-5 h-5" />
      </button>
      {isOpen &&
        createPortal(
          <div
            ref={menuRef}
            role="dialog"
            aria-label={t('languageMenu.label')}
            style={style}
            className="z-[1000] bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl p-3"
          >
            <LanguageSelect id="language-menu-select" />
          </div>,
          document.body,
        )}
    </>
  );
}
