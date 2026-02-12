import { useState, useRef, useEffect, useMemo } from 'react';
import { Scale } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { AdditionalSectionProps } from './types';

function SpoolWeightPicker({
  catalog,
  value,
  onChange,
}: {
  catalog: { id: number; name: string; weight: number }[];
  value: number;
  onChange: (weight: number) => void;
}) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const filtered = useMemo(() => {
    if (!search) return catalog;
    const s = search.toLowerCase();
    return catalog.filter(e =>
      e.name.toLowerCase().includes(s) ||
      e.weight.toString().includes(s),
    );
  }, [catalog, search]);

  // Display value: show catalog name if selected, or the weight
  const displayValue = useMemo(() => {
    if (isOpen) return search;
    if (selectedId) {
      const entry = catalog.find(e => e.id === selectedId);
      if (entry) return entry.name;
    }
    const match = catalog.find(e => e.weight === value);
    if (match) return match.name;
    return '';
  }, [isOpen, search, selectedId, catalog, value]);

  return (
    <div>
      <label className="block text-sm font-medium text-bambu-gray mb-1">
        <span className="flex items-center gap-2">
          <Scale className="w-3.5 h-3.5 text-bambu-gray" />
          {t('inventory.coreWeight')}
        </span>
      </label>
      <div className="flex gap-2 items-center">
        <div className="flex-1 min-w-0 relative" ref={dropdownRef}>
          <input
            ref={inputRef}
            type="text"
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
            placeholder={t('inventory.searchSpoolWeight')}
            value={displayValue}
            onFocus={() => {
              setIsOpen(true);
              setSearch('');
            }}
            onChange={(e) => {
              setSearch(e.target.value);
              setIsOpen(true);
            }}
          />
          {isOpen && (
            <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-64 overflow-y-auto">
              {filtered.length === 0 ? (
                <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</div>
              ) : (
                filtered.map(entry => (
                  <button
                    key={entry.id}
                    type="button"
                    className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex justify-between items-center ${
                      (selectedId ? entry.id === selectedId : entry.weight === value)
                        ? 'bg-bambu-green/10 text-bambu-green'
                        : 'text-white'
                    }`}
                    onClick={() => {
                      setSelectedId(entry.id);
                      onChange(entry.weight);
                      setIsOpen(false);
                      setSearch('');
                    }}
                  >
                    <span className="truncate">{entry.name}</span>
                    <span className="font-mono text-xs text-bambu-gray ml-2 shrink-0">{entry.weight}g</span>
                  </button>
                ))
              )}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <input
            type="number"
            className="w-16 px-2 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm text-center font-mono focus:outline-none focus:border-bambu-green"
            value={value}
            min={0}
            max={2000}
            onChange={(e) => {
              const val = parseInt(e.target.value);
              if (!isNaN(val) && val >= 0) onChange(val);
            }}
          />
          <span className="text-bambu-gray text-sm">g</span>
        </div>
      </div>
    </div>
  );
}

export function AdditionalSection({
  formData,
  updateField,
  spoolCatalog,
}: AdditionalSectionProps) {
  const { t } = useTranslation();

  return (
    <div className="space-y-4">
      {/* Empty Spool Weight */}
      <SpoolWeightPicker
        catalog={spoolCatalog}
        value={formData.core_weight}
        onChange={(weight) => updateField('core_weight', weight)}
      />

      {/* Note */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.note')}</label>
        <textarea
          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green resize-none min-h-[80px]"
          placeholder={t('inventory.notePlaceholder')}
          value={formData.note}
          onChange={(e) => updateField('note', e.target.value)}
        />
      </div>
    </div>
  );
}
