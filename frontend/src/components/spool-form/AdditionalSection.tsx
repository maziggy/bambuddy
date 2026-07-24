import { useState, useRef, useEffect, useMemo } from 'react';
import { Scale } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useToast } from '../../contexts/ToastContext';
import type { AdditionalSectionProps } from './types';

function SpoolWeightPicker({
  catalog,
  value,
  onChange,
  catalogId,
  onCatalogIdChange,
}: {
  catalog: { id: number; name: string; weight: number }[];
  value: number;
  onChange: (weight: number) => void;
  catalogId: number | null;
  onCatalogIdChange: (id: number | null) => void;
}) {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
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

  // When value changes, auto-select if there's only one matching entry or keep selection if it still matches
  useEffect(() => {
    // If no catalog loaded yet, skip matching logic
    if (catalog.length === 0) {
      return;
    }

    const matches = catalog.filter(e => e.weight === value);

    // If currently selected entry still matches the weight, keep it selected
    if (catalogId) {
      const selected = catalog.find(e => e.id === catalogId);
      if (selected && selected.weight === value) {
        return; // Keep current selection
      }
    }

    // If exactly one match, auto-select it
    if (matches.length === 1) {
      onCatalogIdChange(matches[0].id);
    } else if (matches.length === 0) {
      // No matches, clear selection to prevent stale catalog ID
      if (catalogId !== null) {
        onCatalogIdChange(null);
      }
    }
    // If multiple matches, don't auto-select - let user choose
  }, [value, catalog, catalogId, onCatalogIdChange]);

  const filtered = useMemo(() => {
    if (!search) return catalog;
    const s = search.toLowerCase();
    return catalog.filter(e =>
      e.name.toLowerCase().includes(s) ||
      e.weight.toString().includes(s),
    );
  }, [catalog, search]);

  // Find all entries matching the current weight
  const matchingEntries = useMemo(() => {
    return catalog.filter(e => e.weight === value);
  }, [catalog, value]);

  // Display value: show catalog name if selected by ID, otherwise show first match
  const displayValue = useMemo(() => {
    if (isOpen) return search;

    // If a catalog ID is explicitly selected, use that
    if (catalogId) {
      const entry = catalog.find(e => e.id === catalogId);
      if (entry) return entry.name;
    }

    // Otherwise, show the first matching entry as a suggestion
    if (matchingEntries.length > 0) {
      return matchingEntries[0].name;
    }

    // Leave empty if there are no matches
    return '';
  }, [isOpen, search, catalogId, catalog, matchingEntries]);

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
                      (catalogId ? entry.id === catalogId : entry.weight === value)
                        ? 'bg-bambu-green/10 text-bambu-green'
                        : 'text-white'
                    }`}
                    onClick={() => {
                      onCatalogIdChange(entry.id);
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
  currencySymbol,
  availableCategories,
  availableLocations = [],
  onCreateLocation,
  globalLowStockThreshold,
  spoolmanMode = false,
  linkedCodes = [],
}: AdditionalSectionProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [measuredInput, setMeasuredInput] = useState('');
  const [isMeasuredFocused, setIsMeasuredFocused] = useState(false);
  const [remainingInput, setRemainingInput] = useState('');
  const [isRemainingFocused, setIsRemainingFocused] = useState(false);
  const [newLocationName, setNewLocationName] = useState('');
  const [creatingLocation, setCreatingLocation] = useState(false);

  const remainingWeight = Math.max(0, formData.label_weight - formData.weight_used);
  const measuredDefault = formData.core_weight + remainingWeight;

  useEffect(() => {
    if (!isMeasuredFocused) {
      setMeasuredInput(String(measuredDefault));
    }
  }, [isMeasuredFocused, measuredDefault]);

  useEffect(() => {
    if (!isRemainingFocused) {
      setRemainingInput(String(remainingWeight));
    }
  }, [isRemainingFocused, remainingWeight]);

  return (
    <div className="space-y-4">
      {/* Empty Spool Weight — hidden in Spoolman mode (managed per filament type in Spoolman) */}
      {spoolmanMode ? (
        <p className="text-xs text-bambu-gray px-1">{t('inventory.spoolWeightManagedBySpoolman')}</p>
      ) : (
        <SpoolWeightPicker
          catalog={spoolCatalog}
          value={formData.core_weight}
          onChange={(weight) => updateField('core_weight', weight)}
          catalogId={formData.core_weight_catalog_id}
          onCatalogIdChange={(id) => updateField('core_weight_catalog_id', id)}
        />
      )}

      {/* Current Weight (remaining filament) */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.currentWeight')}</label>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <input
              type="number"
              value={remainingInput}
              min={0}
              max={formData.label_weight}
              onFocus={() => setIsRemainingFocused(true)}
              onChange={(e) => {
                setRemainingInput(e.target.value);
              }}
              onBlur={() => {
                setIsRemainingFocused(false);
                const raw = remainingInput.trim();
                const remaining = Number(raw);
                if (!raw || !Number.isFinite(remaining) || remaining < 0 || remaining > formData.label_weight) {
                  setRemainingInput(String(remainingWeight));
                  return;
                }
                const rounded = Math.round(remaining);
                updateField('weight_used', Math.max(0, formData.label_weight - rounded));
                setRemainingInput(String(rounded));
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

      {/* Cost per kg */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.costPerKg', 'Cost per kg')}</label>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-bambu-gray text-sm pointer-events-none">{currencySymbol}</span>
            <input
              type="number"
              value={formData.cost_per_kg ?? ''}
              min={0}
              step={0.01}
              placeholder="0.00"
              onChange={(e) => {
                const value = e.target.value === '' ? null : parseFloat(e.target.value);
                updateField('cost_per_kg', value);
              }}
              style={{ paddingLeft: `${Math.max(2, currencySymbol.length * 0.6 + 1)}rem` }}
              className="w-full py-2 pr-3 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
            />
          </div>
        </div>
      </div>

      {/* Category (#729) */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1" htmlFor="spool-category">
          {t('inventory.category')}
        </label>
        <input
          id="spool-category"
          type="text"
          list="spool-category-options"
          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
          placeholder={t('inventory.categoryPlaceholder')}
          value={formData.category}
          maxLength={50}
          onChange={(e) => updateField('category', e.target.value)}
        />
        {availableCategories.length > 0 && (
          <datalist id="spool-category-options">
            {availableCategories.map((c) => <option key={c} value={c} />)}
          </datalist>
        )}
      </div>

      {/* Per-spool low-stock threshold override (#729) */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1" htmlFor="spool-low-stock-threshold">
          {t('inventory.lowStockThresholdOverride')}
        </label>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <input
              id="spool-low-stock-threshold"
              type="number"
              className="w-full px-3 py-2 pr-8 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
              placeholder={String(globalLowStockThreshold)}
              value={formData.low_stock_threshold_pct ?? ''}
              min={1}
              max={99}
              step={1}
              onChange={(e) => {
                const raw = e.target.value;
                if (raw === '') {
                  updateField('low_stock_threshold_pct', null);
                  return;
                }
                const n = Number(raw);
                if (Number.isFinite(n)) {
                  updateField('low_stock_threshold_pct', Math.min(99, Math.max(1, Math.round(n))));
                }
              }}
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-bambu-gray pointer-events-none">%</span>
          </div>
        </div>
        <p className="text-xs text-bambu-gray mt-1">
          {t('inventory.lowStockThresholdOverrideHelp', { global: globalLowStockThreshold })}
        </p>
      </div>

      {/* Barcode — auto-filled by Scan to Add (scan/photo/manual, matched from
          inventory or the Open Filament Database), or entered here manually
          to teach the native lookup a mapping ahead of time. */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1" htmlFor="spool-barcode">
          {t('inventory.barcode', 'Barcode')}
        </label>
        <input
          id="spool-barcode"
          type="text"
          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm font-mono placeholder:text-bambu-gray/50 placeholder:font-sans focus:outline-none focus:border-bambu-green"
          placeholder={t('inventory.barcodePlaceholder', 'e.g. 6938936716785')}
          value={formData.barcode}
          maxLength={64}
          onChange={(e) => updateField('barcode', e.target.value)}
        />
        <p className="text-xs text-bambu-gray mt-1">
          {t('inventory.barcodeHelp', 'Filled in automatically by Scan Barcode. Enter one manually so a future scan of the same code matches this spool instantly.')}
        </p>
        {linkedCodes.length > 0 && (
          <p className="text-xs text-bambu-gray mt-1 font-mono">
            {t('inventory.linkedCodesLabel', 'Also matches: {{codes}}', {
              codes: linkedCodes
                .map((c) => (c.is_refill ? `${c.code} (${t('inventory.linkedCodesRefill', 'refill')})` : c.code))
                .join(', '),
            })}
          </p>
        )}
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

      {/* Storage Location */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1" htmlFor="spool-storage-location">
          {t('inventory.storageLocation')}
        </label>
        <select
          id="spool-storage-location"
          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
          value={formData.location_id ?? ''}
          onChange={(e) => {
            const raw = e.target.value;
            if (!raw) {
              updateField('location_id', null);
              return;
            }
            const id = Number(raw);
            updateField('location_id', id);
          }}
        >
          <option value="">{t('inventory.storageLocationNone')}</option>
          {availableLocations.map((loc) => (
            <option key={loc.id} value={loc.id}>{loc.name}</option>
          ))}
        </select>
        {onCreateLocation && (
          <div className="mt-2 flex gap-2">
            <input
              type="text"
              maxLength={255}
              className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
              placeholder={t('locations.createPlaceholder')}
              value={newLocationName}
              onChange={(e) => setNewLocationName(e.target.value)}
            />
            <button
              type="button"
              className="px-3 py-2 text-sm rounded-lg bg-bambu-dark-tertiary text-white hover:bg-bambu-gray-dark disabled:opacity-50"
              disabled={!newLocationName.trim() || creatingLocation}
              onClick={async () => {
                const trimmed = newLocationName.trim();
                if (!trimmed || !onCreateLocation) return;
                setCreatingLocation(true);
                try {
                  const created = await onCreateLocation(trimmed);
                  if (created) {
                    updateField('location_id', created.id);
                    setNewLocationName('');
                  }
                } finally {
                  setCreatingLocation(false);
                }
              }}
            >
              {t('locations.addShort')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
