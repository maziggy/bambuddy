import { useState, useRef, useEffect, useMemo } from 'react';
import { Scale } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useToast } from '../../contexts/ToastContext';
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
  const { showToast } = useToast();
  const [measuredInput, setMeasuredInput] = useState('');
  const [isMeasuredFocused, setIsMeasuredFocused] = useState(false);

  const remainingWeight = Math.max(0, formData.label_weight - formData.weight_used);
  const measuredDefault = formData.core_weight + remainingWeight;

  useEffect(() => {
    if (!isMeasuredFocused) {
      setMeasuredInput(String(measuredDefault));
    }
  }, [isMeasuredFocused, measuredDefault]);

  return (
    <div className="space-y-4">
      {/* Empty Spool Weight */}
      <SpoolWeightPicker
        catalog={spoolCatalog}
        value={formData.core_weight}
        onChange={(weight) => updateField('core_weight', weight)}
      />

      {/* Current Weight (remaining filament) */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.currentWeight')}</label>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <input
              type="number"
              value={remainingWeight}
              min={0}
              max={formData.label_weight}
              onChange={(e) => {
                const remaining = parseInt(e.target.value) || 0;
                updateField('weight_used', Math.max(0, formData.label_weight - remaining));
              }}
              className="w-full px-3 py-2 pr-7 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
            />
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-bambu-gray">g</span>
          </div>
          <span className="text-xs text-bambu-gray shrink-0">/ {formData.label_weight}g</span>
        </div>
      </div>

      {/* Measured Weight (empty spool + remaining filament) */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.measuredWeight')}</label>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <input
              type="number"
              value={measuredInput}
              min={0}
              onFocus={() => setIsMeasuredFocused(true)}
              onChange={(e) => {
                setMeasuredInput(e.target.value);
              }}
              onBlur={() => {
                setIsMeasuredFocused(false);
                const raw = measuredInput.trim();
                const measured = Number(raw);
                const minAllowed = formData.core_weight;
                const maxAllowed = formData.core_weight + formData.label_weight;

                if (!raw || !Number.isFinite(measured) || measured < minAllowed || measured > maxAllowed) {
                  showToast(t('inventory.measuredWeightError', { min: minAllowed, max: maxAllowed }), 'error');
                  setMeasuredInput(String(measuredDefault));
                  return;
                }

                const rounded = Math.round(measured);
                const remaining = Math.max(0, Math.min(formData.label_weight, rounded - formData.core_weight));
                updateField('weight_used', Math.max(0, formData.label_weight - remaining));
                setMeasuredInput(String(rounded));
              }}
              className="w-full px-3 py-2 pr-7 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
            />
            <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-bambu-gray">g</span>
          </div>
          <span className="text-xs text-bambu-gray shrink-0">/ {formData.core_weight + formData.label_weight}g</span>
        </div>
      </div>

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
