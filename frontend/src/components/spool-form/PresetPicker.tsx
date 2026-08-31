import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Search, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { FilamentOption, FilamentOptionSource } from './types';

/**
 * Pick one filament preset, showing where each preset came from.
 *
 * A native `<select>` cannot carry the origin badge, and the origin is not
 * decoration: the same filament exists as a Bambu Cloud preset, an Orca Cloud
 * profile, a locally imported one and a built-in, and which one you pick
 * decides what actually reaches the printer. The Configure AMS Slot modal has
 * shown these badges for that reason since #1623; this is the same wording and
 * the same colours, so the two screens read alike.
 *
 * Deliberately not a shared "Select" component: it is a listbox of buttons
 * because each row is a name plus a badge, and it filters as you type because
 * a cloud account with a few hundred presets is ordinary.
 */

const BADGE_CLASSES: Record<FilamentOptionSource, string> = {
  local: 'bg-green-100 dark:bg-green-500/20 text-green-700 dark:text-green-400',
  orca_cloud: 'bg-purple-100 dark:bg-purple-500/20 text-purple-700 dark:text-purple-400',
  cloud: 'bg-bambu-blue/20 text-bambu-blue',
  builtin: 'bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400',
};

export function PresetSourceBadge({ source }: { source: FilamentOptionSource }) {
  const { t } = useTranslation();
  // The same four labels the Configure AMS Slot modal uses, by the same keys --
  // one wording per source across the app rather than a second set to keep in
  // step.
  const label = {
    local: t('profiles.localProfiles.badge'),
    orca_cloud: t('configureAmsSlot.orcaCloud'),
    cloud: t('configureAmsSlot.bambuCloud'),
    builtin: t('configureAmsSlot.builtin'),
  }[source];

  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap ${BADGE_CLASSES[source]}`}>
      {label}
    </span>
  );
}

interface PresetPickerProps {
  /** Currently chosen preset code, or '' for "inherit". */
  value: string;
  options: FilamentOption[];
  /** What the empty choice reads as, e.g. "Use the spool's preset". */
  inheritLabel: string;
  onChange: (option: FilamentOption | null) => void;
  disabled?: boolean;
  ariaLabel: string;
}

export function PresetPicker({
  value,
  options,
  inheritLabel,
  onChange,
  disabled = false,
  ariaLabel,
}: PresetPickerProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);

  const selected = options.find(o => o.code === value);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    // Escape closes this control WITHOUT closing the modal around it, which is
    // what a bare keydown listener on the document would otherwise let happen.
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open]);

  const needle = query.trim().toLowerCase();
  const shown = needle
    ? options.filter(o => o.displayName.toLowerCase().includes(needle))
    : options;

  const choose = (option: FilamentOption | null) => {
    onChange(option);
    setOpen(false);
    setQuery('');
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-left focus:outline-none focus:border-bambu-green disabled:opacity-50"
      >
        <span className={`flex-1 min-w-0 truncate ${selected ? 'text-white' : 'text-bambu-gray italic'}`}>
          {selected ? selected.displayName : inheritLabel}
        </span>
        {selected && <PresetSourceBadge source={selected.source} />}
        <ChevronDown className="w-3.5 h-3.5 text-bambu-gray shrink-0" />
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl">
          <div className="p-2 border-b border-bambu-dark-tertiary">
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-bambu-gray absolute left-2 top-1/2 -translate-y-1/2" />
              <input
                autoFocus
                type="text"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder={t('inventory.searchPresets')}
                className="w-full pl-7 pr-7 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded-md text-sm text-white placeholder:text-bambu-gray/60 focus:outline-none focus:border-bambu-green"
              />
              {query && (
                <button
                  type="button"
                  aria-label={t('common.clear')}
                  onClick={() => setQuery('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>

          <div className="max-h-56 overflow-y-auto p-1" role="listbox" aria-label={ariaLabel}>
            <button
              type="button"
              role="option"
              aria-selected={!selected}
              onClick={() => choose(null)}
              className={`w-full text-left px-2 py-1.5 rounded-md text-sm italic ${
                selected ? 'text-bambu-gray hover:bg-bambu-dark' : 'bg-bambu-green/15 text-bambu-green'
              }`}
            >
              {inheritLabel}
            </button>
            {shown.length === 0 ? (
              <p className="px-2 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</p>
            ) : (
              shown.map(option => (
                <button
                  key={option.code}
                  type="button"
                  role="option"
                  aria-selected={option.code === value}
                  onClick={() => choose(option)}
                  className={`w-full flex items-center gap-2 text-left px-2 py-1.5 rounded-md text-sm ${
                    option.code === value
                      ? 'bg-bambu-green/15 text-bambu-green'
                      : 'text-white hover:bg-bambu-dark'
                  }`}
                >
                  <span className="flex-1 min-w-0 truncate" title={option.displayName}>
                    {option.displayName}
                  </span>
                  <PresetSourceBadge source={option.source} />
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
