import { useState, useRef, useEffect, useMemo } from 'react';
import { ChevronDown, Loader2, Package } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { SpoolmanFilamentEntry } from '../../api/client';

interface SpoolmanFilamentPickerProps {
  filaments: SpoolmanFilamentEntry[];
  isLoading: boolean;
  selectedId: number | null;
  onSelect: (filament: SpoolmanFilamentEntry) => void;
}

export function SpoolmanFilamentPicker({
  filaments,
  isLoading,
  selectedId,
  onSelect,
}: SpoolmanFilamentPickerProps) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const selected = useMemo(
    () => filaments.find((f) => f.id === selectedId) ?? null,
    [filaments, selectedId]
  );

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setSearch('');
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return filaments;
    return filaments.filter(
      (f) =>
        f.name.toLowerCase().includes(q) ||
        (f.material?.toLowerCase().includes(q) ?? false) ||
        (f.vendor?.name.toLowerCase().includes(q) ?? false) ||
        (f.color_name?.toLowerCase().includes(q) ?? false)
    );
  }, [filaments, search]);

  useEffect(() => {
    if (isOpen) inputRef.current?.focus();
  }, [isOpen]);

  const handleOpen = () => {
    setIsOpen(true);
    setSearch('');
  };

  const handleSelect = (filament: SpoolmanFilamentEntry) => {
    onSelect(filament);
    setIsOpen(false);
    setSearch('');
  };

  const colorStyle = (hex: string | null): string =>
    hex ? `#${hex.replace('#', '')}` : '#808080';

  return (
    <div ref={containerRef} className="relative">
      <label className="block text-sm font-medium text-bambu-gray mb-1">
        {t('inventory.pickFromSpoolmanCatalog')}
      </label>

      {/* Trigger button */}
      <button
        type="button"
        onClick={handleOpen}
        className="w-full flex items-center gap-2 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-left focus:outline-none focus:border-bambu-green hover:border-bambu-gray transition-colors"
      >
        {isLoading ? (
          <Loader2 className="w-4 h-4 text-bambu-gray animate-spin shrink-0" />
        ) : selected ? (
          <>
            <span
              className="w-4 h-4 rounded-full shrink-0 border border-white/20"
              style={{ backgroundColor: colorStyle(selected.color_hex) }}
              aria-label={t('inventory.spoolmanFilamentColorSwatch')}
            />
            <span className="text-white text-sm truncate flex-1">
              {selected.vendor?.name ? `${selected.vendor.name} — ` : ''}
              {selected.name}
            </span>
          </>
        ) : (
          <>
            <Package className="w-4 h-4 text-bambu-gray shrink-0" />
            <span className="text-bambu-gray text-sm flex-1">
              {t('inventory.spoolmanFilamentCatalog')}
            </span>
          </>
        )}
        <ChevronDown className="w-4 h-4 text-bambu-gray shrink-0 ml-auto" />
      </button>

      {/* Dropdown */}
      {isOpen && (
        <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl">
          {/* Search */}
          <div className="p-2 border-b border-bambu-dark-tertiary">
            <input
              ref={inputRef}
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('inventory.pickFromSpoolmanCatalog')}
              className="w-full px-2 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
            />
          </div>

          {/* Items */}
          <ul className="max-h-56 overflow-y-auto py-1">
            {isLoading ? (
              <li className="flex items-center justify-center gap-2 py-4 text-bambu-gray text-sm">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>{t('common.loading', 'Loading…')}</span>
              </li>
            ) : filtered.length === 0 ? (
              <li className="py-4 text-center text-bambu-gray text-sm">
                {t('inventory.noSpoolmanFilaments')}
              </li>
            ) : (
              filtered.map((f) => (
                <li key={f.id}>
                  <button
                    type="button"
                    onClick={() => handleSelect(f)}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-bambu-dark-tertiary transition-colors ${
                      f.id === selectedId ? 'bg-bambu-dark-tertiary' : ''
                    }`}
                  >
                    <span
                      className="w-4 h-4 rounded-full shrink-0 border border-white/20"
                      style={{ backgroundColor: colorStyle(f.color_hex) }}
                      aria-label={t('inventory.spoolmanFilamentColorSwatch')}
                    />
                    <span className="flex-1 min-w-0">
                      <span className="block text-white text-sm truncate">
                        {f.vendor?.name ? `${f.vendor.name} — ` : ''}
                        {f.name}
                      </span>
                      <span className="block text-bambu-gray text-xs truncate">
                        {[f.material, f.color_name].filter(Boolean).join(' · ')}
                        {f.weight ? ` · ${f.weight}g` : ''}
                      </span>
                    </span>
                  </button>
                </li>
              ))
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
