import { useEffect, useRef, useState } from 'react';
import { Plus, Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export interface PickablePrinter {
  id: number;
  name: string;
  location?: string | null;
}

interface PrinterSearchPickerProps {
  /** Printers that can still be picked. */
  printers: PickablePrinter[];
  onPick: (printer: PickablePrinter) => void;
  /** Text of the button that opens the picker. */
  label: string;
  disabled?: boolean;
}

/**
 * A button that opens a searchable list of printers and hands back the one
 * clicked. Made for farms: with 120 printers a plain select is a long scroll,
 * so the list filters by name and location as you type.
 */
export function PrinterSearchPicker({ printers, onPick, label, disabled }: PrinterSearchPickerProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  const needle = query.trim().toLowerCase();
  const matches = printers.filter(p =>
    !needle || p.name.toLowerCase().includes(needle) || (p.location ?? '').toLowerCase().includes(needle),
  );

  const close = () => {
    setOpen(false);
    setQuery('');
  };

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        disabled={disabled || printers.length === 0}
        aria-expanded={open}
        className="inline-flex items-center gap-1 px-2 py-1 rounded border border-dashed border-bambu-dark-tertiary text-xs text-bambu-gray hover:text-white hover:border-bambu-green disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <Plus className="w-3.5 h-3.5" />
        {label}
      </button>
      {open && (
        <div
          className="absolute left-0 z-20 mt-1 w-64 max-w-[calc(100vw-2rem)] rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary shadow-lg"
          onKeyDown={(e) => {
            if (e.key === 'Escape') close();
          }}
        >
          <div className="relative p-2 border-b border-bambu-dark-tertiary">
            <Search className="w-3.5 h-3.5 text-bambu-gray absolute left-4 top-1/2 -translate-y-1/2" />
            <input
              type="search"
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('settings.printerPickerSearch')}
              aria-label={t('settings.printerPickerSearch')}
              className="w-full pl-7 pr-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
            />
          </div>
          <ul className="max-h-60 overflow-y-auto py-1" role="listbox" aria-label={label}>
            {matches.length === 0 && (
              <li className="px-3 py-2 text-xs text-bambu-gray italic">{t('settings.printerPickerNoMatch')}</li>
            )}
            {matches.map(p => (
              <li key={p.id} role="option" aria-selected={false}>
                <button
                  type="button"
                  onClick={() => {
                    onPick(p);
                    close();
                  }}
                  className="w-full text-left px-3 py-1.5 text-sm text-white hover:bg-bambu-dark-tertiary"
                >
                  <span className="block truncate">{p.name}</span>
                  {p.location && <span className="block truncate text-[11px] text-bambu-gray">{p.location}</span>}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
