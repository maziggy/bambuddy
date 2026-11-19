import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Key, MonitorSmartphone, UserRound } from 'lucide-react';
import { LanguageSelect } from './LanguageSelect';
import { useFooterPopover } from '../hooks/useFooterPopover';

const MENU_WIDTH = 220;

/**
 * The signed-in user's own things, from the sidebar footer: change password
 * and Connect slicer (#3602). One icon where the password key was, so the
 * footer row keeps its width.
 *
 * Rendered in a portal: the collapsed sidebar's footer scrolls, which would
 * clip a menu inside it.
 */
export function AccountMenu({ username, onChangePassword }: { username: string; onChangePassword: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // Opens with the language select focused (#3623).
  const { buttonRef, menuRef, isOpen, style, toggle, close } = useFooterPopover(MENU_WIDTH);

  const item =
    'w-full flex items-center gap-2 px-3 py-2 text-sm text-left text-bambu-gray-light hover:text-white hover:bg-bambu-dark-tertiary transition-colors';

  return (
    <>
      <button
        ref={buttonRef}
        onClick={toggle}
        className={`p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors ${
          isOpen ? 'text-bambu-green' : 'text-bambu-gray-light hover:text-white'
        }`}
        title={t('accountMenu.title')}
        aria-label={t('accountMenu.title')}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
      >
        <UserRound className="w-5 h-5" />
      </button>
      {isOpen &&
        createPortal(
          <div
            ref={menuRef}
            role="dialog"
            aria-label={t('accountMenu.title')}
            style={style}
            className="z-[1000] bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl overflow-hidden"
          >
            <div className="px-3 py-2 text-xs text-bambu-gray border-b border-bambu-dark-tertiary truncate">{username}</div>
            {/* Per-browser interface language, no permission needed (#3623). */}
            <LanguageSelect id="account-menu-language" className="px-3 py-2 border-b border-bambu-dark-tertiary" />
            <button
              className={item}
              onClick={() => {
                close();
                onChangePassword();
              }}
            >
              <Key className="w-4 h-4" />
              {t('changePassword.title')}
            </button>
            <button
              className={item}
              onClick={() => {
                close();
                navigate('/connect-slicer');
              }}
            >
              <MonitorSmartphone className="w-4 h-4" />
              {t('connectSlicer.title')}
            </button>
          </div>,
          document.body,
        )}
    </>
  );
}
