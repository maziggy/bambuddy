import { useState, useRef, useEffect, useMemo } from 'react';
import { Search, Loader2, ChevronDown, Cloud, CloudOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { FilamentSectionProps, FilamentOption } from './types';
import { MATERIALS, KNOWN_VARIANTS } from './constants';
import { parsePresetName } from './utils';

export function FilamentSection({
  formData,
  updateField,
  cloudAuthenticated,
  loadingCloudPresets,
  presetInputValue,
  setPresetInputValue,
  selectedPresetOption,
  filamentOptions,
  availableBrands,
}: FilamentSectionProps) {
  const { t } = useTranslation();
  const [presetDropdownOpen, setPresetDropdownOpen] = useState(false);
  const [brandDropdownOpen, setBrandDropdownOpen] = useState(false);
  const [subtypeDropdownOpen, setSubtypeDropdownOpen] = useState(false);
  const [brandSearch, setBrandSearch] = useState('');
  const [subtypeSearch, setSubtypeSearch] = useState('');
  const presetRef = useRef<HTMLDivElement>(null);
  const brandRef = useRef<HTMLDivElement>(null);
  const subtypeRef = useRef<HTMLDivElement>(null);

  // Close dropdowns on outside click
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (presetRef.current && !presetRef.current.contains(e.target as Node)) {
        setPresetDropdownOpen(false);
      }
      if (brandRef.current && !brandRef.current.contains(e.target as Node)) {
        setBrandDropdownOpen(false);
      }
      if (subtypeRef.current && !subtypeRef.current.contains(e.target as Node)) {
        setSubtypeDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Filtered presets based on search
  const filteredPresets = useMemo(() => {
    if (!presetInputValue) return filamentOptions;
    const search = presetInputValue.toLowerCase();
    return filamentOptions.filter(o =>
      o.displayName.toLowerCase().includes(search) ||
      o.code.toLowerCase().includes(search),
    );
  }, [filamentOptions, presetInputValue]);

  // Filtered brands
  const filteredBrands = useMemo(() => {
    if (!brandSearch) return availableBrands;
    const search = brandSearch.toLowerCase();
    return availableBrands.filter(b => b.toLowerCase().includes(search));
  }, [availableBrands, brandSearch]);

  const filteredVariants = useMemo(() => {
    if (!subtypeSearch) return KNOWN_VARIANTS;
    const search = subtypeSearch.toLowerCase();
    return KNOWN_VARIANTS.filter(v => v.toLowerCase().includes(search));
  }, [subtypeSearch]);

  // Handle preset selection
  const handlePresetSelect = (option: FilamentOption) => {
    updateField('slicer_filament', option.code);
    setPresetInputValue(option.displayName);
    setPresetDropdownOpen(false);

    // Auto-fill material, brand, subtype from preset name
    const parsed = parsePresetName(option.name);
    if (parsed.material) updateField('material', parsed.material);
    if (parsed.brand) updateField('brand', parsed.brand);
    if (parsed.variant) updateField('subtype', parsed.variant);
  };

  return (
    <div className="space-y-4">
      {/* Cloud status indicator */}
      <div className="flex items-center gap-2 text-xs text-bambu-gray">
        {loadingCloudPresets ? (
          <><Loader2 className="w-3 h-3 animate-spin" /> {t('inventory.loadingPresets')}</>
        ) : cloudAuthenticated ? (
          <><Cloud className="w-3 h-3 text-bambu-green" /> {t('inventory.cloudConnected')}</>
        ) : (
          <><CloudOff className="w-3 h-3" /> {t('inventory.cloudNotConnected')}</>
        )}
      </div>

      {/* Slicer Preset (autocomplete) */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">
          {t('inventory.slicerPreset')} *
        </label>
        <div className="relative" ref={presetRef}>
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
          <input
            type="text"
            className="w-full pl-9 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
            placeholder={t('inventory.searchPresets')}
            value={presetInputValue}
            onChange={(e) => {
              setPresetInputValue(e.target.value);
              setPresetDropdownOpen(true);
            }}
            onFocus={() => {
              setPresetDropdownOpen(true);
              setPresetInputValue('');
            }}
          />
          {presetDropdownOpen && (
            <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-64 overflow-y-auto">
              {filteredPresets.length === 0 ? (
                <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noPresetsFound')}</div>
              ) : (
                filteredPresets.map(option => (
                  <button
                    key={option.code}
                    type="button"
                    className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex justify-between items-center ${
                      selectedPresetOption?.code === option.code
                        ? 'bg-bambu-green/10 text-bambu-green'
                        : 'text-white'
                    }`}
                    onClick={() => handlePresetSelect(option)}
                  >
                    <span className="truncate">{option.displayName}</span>
                    <span className="font-mono text-xs text-bambu-gray ml-2 shrink-0">{option.code}</span>
                  </button>
                ))
              )}
            </div>
          )}
        </div>
        {selectedPresetOption && (
          <div className="mt-1 text-xs text-bambu-gray">
            {t('inventory.selectedPreset')}: <span className="font-mono text-bambu-green">{selectedPresetOption.code}</span>
          </div>
        )}
      </div>

      {/* Material */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.material')} *</label>
        <select
          value={formData.material}
          onChange={(e) => updateField('material', e.target.value)}
          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
        >
          <option value="">{t('inventory.selectMaterial')}</option>
          {MATERIALS.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </div>

      {/* Brand (dropdown with search) */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.brand')}</label>
        <div className="relative" ref={brandRef}>
          <input
            type="text"
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
            placeholder={t('inventory.searchBrand')}
            value={brandDropdownOpen ? brandSearch : formData.brand}
            onChange={(e) => {
              setBrandSearch(e.target.value);
              setBrandDropdownOpen(true);
            }}
            onFocus={() => {
              setBrandDropdownOpen(true);
              setBrandSearch('');
            }}
          />
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
          {brandDropdownOpen && (
            <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-48 overflow-y-auto">
              {filteredBrands.length === 0 ? (
                <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</div>
              ) : (
                filteredBrands.map(brand => (
                  <button
                    key={brand}
                    type="button"
                    className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
                      formData.brand === brand ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
                    }`}
                    onClick={() => {
                      updateField('brand', brand);
                      setBrandDropdownOpen(false);
                      setBrandSearch('');
                    }}
                  >
                    {brand}
                  </button>
                ))
              )}
              {/* Allow custom brand */}
              {brandSearch && !filteredBrands.includes(brandSearch) && (
                <button
                  type="button"
                  className="w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary text-bambu-green border-t border-bambu-dark-tertiary"
                  onClick={() => {
                    updateField('brand', brandSearch);
                    setBrandDropdownOpen(false);
                    setBrandSearch('');
                  }}
                >
                  {t('inventory.useCustomBrand', { brand: brandSearch })}
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Variant / Subtype */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.subtype')}</label>
        <div className="relative" ref={subtypeRef}>
          <input
            type="text"
            value={subtypeDropdownOpen ? subtypeSearch : formData.subtype}
            onChange={(e) => {
              setSubtypeSearch(e.target.value);
              setSubtypeDropdownOpen(true);
            }}
            onFocus={() => {
              setSubtypeDropdownOpen(true);
              setSubtypeSearch('');
            }}
            placeholder="Basic, Matte, Silk..."
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
          />
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
          {subtypeDropdownOpen && (
            <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-48 overflow-y-auto">
              {filteredVariants.length === 0 ? (
                <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</div>
              ) : (
                filteredVariants.map(variant => (
                  <button
                    key={variant}
                    type="button"
                    className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
                      formData.subtype === variant ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
                    }`}
                    onClick={() => {
                      updateField('subtype', variant);
                      setSubtypeDropdownOpen(false);
                      setSubtypeSearch('');
                    }}
                  >
                    {variant}
                  </button>
                ))
              )}
              {subtypeSearch && !KNOWN_VARIANTS.some(v => v.toLowerCase() === subtypeSearch.toLowerCase().trim()) && (
                <button
                  type="button"
                  className="w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary text-bambu-green border-t border-bambu-dark-tertiary"
                  onClick={() => {
                    updateField('subtype', subtypeSearch);
                    setSubtypeDropdownOpen(false);
                    setSubtypeSearch('');
                  }}
                >
                  {t('inventory.useCustomBrand', { brand: subtypeSearch })}
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Label Weight */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.labelWeight')}</label>
        <div className="relative">
          <input
            type="number"
            value={formData.label_weight}
            onChange={(e) => updateField('label_weight', parseInt(e.target.value) || 0)}
            className="w-full px-3 py-2 pr-7 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
          />
          <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-bambu-gray">g</span>
        </div>
      </div>
    </div>
  );
}
