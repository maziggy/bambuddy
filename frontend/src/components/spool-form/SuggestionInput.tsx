import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * A text field with a styled suggestion list, the look of the Material field
 * (a native datalist is avoided in this project).
 *
 * Free text first: whatever is typed is the value at once, so a name that is
 * in no list is kept even if the user goes straight to Save. The list only
 * narrows as they type and fills the field when one is picked.
 */
export function SuggestionInput({
  id,
  value,
  onChange,
  options,
  placeholder,
  maxLength,
}: {
  id: string;
  value: string;
  onChange: (next: string) => void;
  options: string[];
  placeholder?: string;
  maxLength?: number;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // What was typed since the list opened. Opening shows everything, as the
  // Material field does: a field that already holds a name would otherwise
  // list only that name and offer no way to pick another.
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const filtered = useMemo(() => {
    const typed = query.trim().toLowerCase();
    return typed ? options.filter((o) => o.toLowerCase().includes(typed)) : options;
  }, [options, query]);

  return (
    <div className="relative" ref={rootRef}>
      <input
        id={id}
        type="text"
        autoComplete="off"
        className="w-full px-3 py-2 pr-9 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
        placeholder={placeholder}
        value={value}
        maxLength={maxLength}
        onChange={(e) => {
          onChange(e.target.value);
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => {
          setQuery('');
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setOpen(false);
        }}
      />
      <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
      {open && options.length > 0 && (
        <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-48 overflow-y-auto">
          {filtered.length === 0 ? (
            <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</div>
          ) : (
            filtered.map((option) => (
              <button
                key={option}
                type="button"
                className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
                  option === value ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
                }`}
                onClick={() => {
                  onChange(option);
                  setOpen(false);
                }}
              >
                {option}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
