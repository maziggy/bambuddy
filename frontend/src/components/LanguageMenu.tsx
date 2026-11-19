import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Globe } from 'lucide-react';
import { LanguageSelect } from './LanguageSelect';

const MENU_WIDTH = 220;

/**
 * Language button for the sidebar footer when authentication is off (#3623):
 * there is no account menu then, but the interface language is still a
 * per-browser choice. Rendered in a portal, like AccountMenu, so the
 * scrolling footer cannot clip it.
 */
export function LanguageMenu() {
  const { t } = useTranslation();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; bottom: number } | null>(null);

  const close = (restoreFocus = false) => {
    setPosition(null);
    if (restoreFocus) buttonRef.current?.focus();
  };

  const toggle = () => {
    if (position) {
      close(true);
      return;
    }
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - MENU_WIDTH - 8)),
      bottom: window.innerHeight - rect.top + 4,
    });
  };

  // The dialog opens with the language select focused.
  const isOpen = !!position;
  useEffect(() => {
    if (isOpen) menuRef.current?.querySelector('select')?.focus();
  }, [isOpen]);

  useEffect(() => {
    if (!position) return;
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !buttonRef.current?.contains(target)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close(true);
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [position]);

  return (
    <>
      <button
        ref={buttonRef}
        onClick={toggle}
        className={`p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors ${
          position ? 'text-bambu-green' : 'text-bambu-gray-light hover:text-white'
        }`}
        title={t('languageMenu.label')}
        aria-label={t('languageMenu.label')}
        aria-haspopup="dialog"
        aria-expanded={!!position}
      >
        <Globe className="w-5 h-5" />
      </button>
      {position &&
        createPortal(
          <div
            ref={menuRef}
            role="dialog"
            aria-label={t('languageMenu.label')}
            style={{ position: 'fixed', left: position.left, bottom: position.bottom, width: MENU_WIDTH }}
            className="z-[1000] bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl p-3"
          >
            <LanguageSelect id="language-menu-select" />
          </div>,
          document.body,
        )}
    </>
  );
}
