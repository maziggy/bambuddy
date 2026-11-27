import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { ListChecks, X } from 'lucide-react';
import { useQueueTodo } from '../hooks/useQueueTodo';
import { useIsSidebarCompact } from '../hooks/useIsSidebarCompact';
import { QueueTodoList } from './QueueTodoList';

// Nav button with the count of open "to do" entries and the panel listing
// them (#3615). Full-width bottom sheet on phones; from 768 px a floating
// panel, anchored top-right under the compact header or beside the sidebar.
// Renders nothing while the list is empty.
export function QueueTodoLauncher({
  className = '',
  sidebarExpanded = false,
}: {
  className?: string;
  /** The sidebar is open (w-64) rather than the icon rail (w-16); the panel sits beside it. */
  sidebarExpanded?: boolean;
}) {
  const { t } = useTranslation();
  const { data } = useQueueTodo();
  const [open, setOpen] = useState(false);
  const compactHeader = useIsSidebarCompact();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const count = data?.count ?? 0;

  // An emptied list closes the panel, so it cannot reopen by itself later.
  useEffect(() => {
    if (count === 0) setOpen(false);
  }, [count]);

  useEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      trigger?.focus();
    };
  }, [open]);

  if (count === 0) return null;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
        className={`relative p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white ${className}`}
        title={t('queueTodo.title')}
        aria-label={t('queueTodo.badgeLabel', { count })}
      >
        <ListChecks className="w-5 h-5" />
        <span
          data-testid="queue-todo-badge"
          className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 flex items-center justify-center text-[10px] font-bold rounded-full bg-yellow-500 text-black"
        >
          {count > 99 ? '99+' : count}
        </span>
      </button>
      {open && createPortal(
        <>
          <div className="fixed inset-0 z-[60] bg-black/50" onClick={() => setOpen(false)} />
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t('queueTodo.title')}
            className={`fixed z-[61] inset-x-0 bottom-0 max-h-[85vh] rounded-t-2xl md:inset-x-auto md:w-[28rem] md:max-w-[calc(100vw-2rem)] md:max-h-[80vh] md:rounded-2xl bg-bambu-dark border border-bambu-dark-tertiary shadow-xl flex flex-col ${
              compactHeader ? 'md:bottom-auto md:top-16 md:right-4' : sidebarExpanded ? 'md:bottom-4 md:left-[17rem]' : 'md:bottom-4 md:left-[5rem]'
            }`}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-bambu-dark-tertiary">
              <h2 className="text-base font-semibold text-white">{t('queueTodo.title')}</h2>
              <button
                ref={closeRef}
                type="button"
                onClick={() => setOpen(false)}
                className="p-2 -mr-2 rounded-lg hover:bg-bambu-dark-tertiary text-bambu-gray-light"
                aria-label={t('common.close')}
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="overflow-y-auto p-4 min-w-0">
              <QueueTodoList todo={data} onNavigate={() => setOpen(false)} overlayZIndex="z-[70]" />
            </div>
          </div>
        </>,
        document.body,
      )}
    </>
  );
}
