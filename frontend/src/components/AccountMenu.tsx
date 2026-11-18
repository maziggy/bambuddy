import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Key, MonitorSmartphone, UserRound } from 'lucide-react';

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
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; bottom: number } | null>(null);

  const close = () => setPosition(null);

  const toggle = () => {
    if (position) {
      close();
      return;
    }
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - MENU_WIDTH - 8)),
      bottom: window.innerHeight - rect.top + 4,
    });
  };

  useEffect(() => {
    if (!position) return;
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !buttonRef.current?.contains(target)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [position]);

  const item =
    'w-full flex items-center gap-2 px-3 py-2 text-sm text-left text-bambu-gray-light hover:text-white hover:bg-bambu-dark-tertiary transition-colors';

  return (
    <>
      <button
        ref={buttonRef}
        onClick={toggle}
        className={`p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors ${
          position ? 'text-bambu-green' : 'text-bambu-gray-light hover:text-white'
        }`}
        title={t('accountMenu.title')}
        aria-label={t('accountMenu.title')}
        aria-haspopup="menu"
        aria-expanded={!!position}
      >
        <UserRound className="w-5 h-5" />
      </button>
      {position &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            style={{ position: 'fixed', left: position.left, bottom: position.bottom, width: MENU_WIDTH }}
            className="z-[1000] bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl overflow-hidden"
          >
            <div className="px-3 py-2 text-xs text-bambu-gray border-b border-bambu-dark-tertiary truncate">{username}</div>
            <button
              role="menuitem"
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
              role="menuitem"
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
