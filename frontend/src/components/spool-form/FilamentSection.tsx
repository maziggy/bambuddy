import { useState, useRef, useEffect, useMemo } from 'react';
import { Search, Loader2, ChevronDown, Cloud, CloudOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { FilamentSectionProps, FilamentOption } from './types';
import { KNOWN_VARIANTS } from './constants';
import { parsePresetName } from './utils';

// The identity fields a slicer preset can auto-fill.
type PresetFilledField = 'material' | 'brand' | 'subtype';

// Split a dropdown's options into the ones the catalog/presets pair with the
// other field's current value and everything else (#1905). Suggestions are only
// a sort order — nothing is ever hidden, so an unusual-but-real combination
// (Elegoo ASA) stays one click away.
function splitSuggested(options: string[], suggested: string[]): { top: string[]; rest: string[] } {
  if (suggested.length === 0) return { top: [], rest: options };
  const keys = new Set(suggested.map(s => s.toLowerCase()));
  return {
    top: options.filter(o => keys.has(o.toLowerCase())),
    rest: options.filter(o => !keys.has(o.toLowerCase())),
  };
}

function GroupHeading({ label }: { label: string }) {
  return (
    <div className="px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-bambu-gray/70 bg-bambu-dark-tertiary/40">
      {label}
    </div>
  );
}

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
  availableMaterials,
  suggestedBrands,
  suggestedMaterials,
  quickAdd,
  detailsRequired,
  quantity,
  onQuantityChange,
  errors,
}: FilamentSectionProps) {
  const { t } = useTranslation();
  const [presetDropdownOpen, setPresetDropdownOpen] = useState(false);
  const [brandDropdownOpen, setBrandDropdownOpen] = useState(false);
  const [subtypeDropdownOpen, setSubtypeDropdownOpen] = useState(false);
  const [materialDropdownOpen, setMaterialDropdownOpen] = useState(false);
  const [brandSearch, setBrandSearch] = useState('');
  const [subtypeSearch, setSubtypeSearch] = useState('');
  const [materialSearch, setMaterialSearch] = useState('');
  const [labelInput, setLabelInput] = useState(String(formData.label_weight));
  const [isLabelFocused, setIsLabelFocused] = useState(false);
  // Which identity fields the currently selected preset filled in (#1905).
  // Anything outside this set was set by the user (or loaded from the spool
  // being edited) and must survive picking a preset.
  const [presetFilled, setPresetFilled] = useState<Set<PresetFilledField>>(new Set());
  const presetRef = useRef<HTMLDivElement>(null);
  const brandRef = useRef<HTMLDivElement>(null);
  const subtypeRef = useRef<HTMLDivElement>(null);
  const materialRef = useRef<HTMLDivElement>(null);

  // Close dropdowns on outside click
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (presetRef.current && !presetRef.current.contains(e.target as Node)) {
        setPresetDropdownOpen(false);
      }
      if (materialRef.current && !materialRef.current.contains(e.target as Node)) {
        setMaterialDropdownOpen(false);
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
    const filtered = availableBrands.filter(b => b.toLowerCase().includes(search));
    // Sort: exact match first, then others
    return filtered.sort((a, b) => {
      const aExact = a.toLowerCase() === search;
      const bExact = b.toLowerCase() === search;
      if (aExact && !bExact) return -1;
      if (!aExact && bExact) return 1;
      return a.localeCompare(b);
    });
  }, [availableBrands, brandSearch]);

  const filteredVariants = useMemo(() => {
    if (!subtypeSearch) return KNOWN_VARIANTS;
    const search = subtypeSearch.toLowerCase();
    return KNOWN_VARIANTS.filter(v => v.toLowerCase().includes(search));
  }, [subtypeSearch]);

  const filteredMaterials = useMemo(() => {
    if (!materialSearch) return availableMaterials;
    const search = materialSearch.toLowerCase();
    const filtered = availableMaterials.filter(m => m.toLowerCase().includes(search));
    // Sort: exact match first, then others
    return filtered.sort((a, b) => {
      const aExact = a.toLowerCase() === search;
      const bExact = b.toLowerCase() === search;
      if (aExact && !bExact) return -1;
      if (!aExact && bExact) return 1;
      return a.localeCompare(b);
    });
  }, [materialSearch, availableMaterials]);

  const brandGroups = useMemo(
    () => splitSuggested(filteredBrands, suggestedBrands),
    [filteredBrands, suggestedBrands],
  );

  const materialGroups = useMemo(
    () => splitSuggested(filteredMaterials, suggestedMaterials),
    [filteredMaterials, suggestedMaterials],
  );

  useEffect(() => {
    if (!isLabelFocused) {
      setLabelInput(String(formData.label_weight));
    }
  }, [formData.label_weight, isLabelFocused]);

  // Handle preset selection
  const handlePresetSelect = (option: FilamentOption) => {
    updateField('slicer_filament', option.code);
    setPresetInputValue(option.displayName);
    setPresetDropdownOpen(false);

    // Auto-fill material, brand and subtype from the preset name — but only
    // where the value is still empty or was itself put there by the previously
    // selected preset (#1905). Overwriting unconditionally silently rewrote the
    // spool's own manufacturer ("Elegoo" → "Generic") whenever the user was
    // forced to assign a preset, so the spool vanished from where they filed it.
    // Switching between presets still replaces what the earlier one filled in.
    const parsed = parsePresetName(option.name);
    const filled = new Set<PresetFilledField>();
    const entries: [PresetFilledField, string][] = [
      ['material', parsed.material],
      ['brand', parsed.brand],
      ['subtype', parsed.variant],
    ];
    for (const [field, value] of entries) {
      if (!value) continue;
      if (formData[field] && !presetFilled.has(field)) continue;
      updateField(field, value);
      filled.add(field);
    }
    setPresetFilled(filled);
  };

  // A manual edit to an identity field takes it back out of the preset's hands.
  const updateIdentityField = (field: PresetFilledField, value: string) => {
    if (presetFilled.has(field)) {
      setPresetFilled(prev => {
        const next = new Set(prev);
        next.delete(field);
        return next;
      });
    }
    updateField(field, value);
  };

  const renderBrandOption = (brand: string) => (
    <button
      key={brand}
      type="button"
      className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
        formData.brand === brand ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
      }`}
      onClick={() => {
        updateIdentityField('brand', brand);
        setBrandDropdownOpen(false);
        setBrandSearch('');
      }}
    >
      {brand}
    </button>
  );

  const renderMaterialOption = (material: string) => (
    <button
      key={material}
      type="button"
      className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
        formData.material === material ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
      }`}
      onClick={() => {
        updateIdentityField('material', material);
        setMaterialDropdownOpen(false);
        setMaterialSearch('');
      }}
    >
      {material}
    </button>
  );

  return (
    <div className="space-y-4">
      {/* Cloud status indicator */}
      {!quickAdd && (
        <div className="flex items-center gap-2 text-xs text-bambu-gray">
          {loadingCloudPresets ? (
            <><Loader2 className="w-3 h-3 animate-spin" /> {t('inventory.loadingPresets')}</>
          ) : cloudAuthenticated ? (
            <><Cloud className="w-3 h-3 text-bambu-green" /> {t('inventory.cloudConnected')}</>
          ) : (
            <><CloudOff className="w-3 h-3" /> {t('inventory.cloudNotConnected')}</>
          )}
        </div>
      )}

      {/* Slicer Preset (autocomplete) — hidden in quick-add mode */}
      {!quickAdd && (
        <div>
          <label className="block text-sm font-medium text-bambu-gray mb-1">
            {t('inventory.slicerPreset')}{detailsRequired && ' *'}
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
                      key={`${option.code}::${option.name}`}
                      type="button"
                      className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary truncate ${
                        selectedPresetOption?.code === option.code
                          ? 'bg-bambu-green/10 text-bambu-green'
                          : 'text-white'
                      }`}
                      onClick={() => handlePresetSelect(option)}
                    >
                      {option.displayName}
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
          {errors?.slicer_filament && (
            <p className="mt-1 text-xs text-red-700 dark:text-red-400">{errors.slicer_filament}</p>
          )}
        </div>
      )}

      {/* Material */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.material')} *</label>
        <div className="relative" ref={materialRef}>
          <input
            type="text"
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
            placeholder={t('inventory.selectMaterial')}
            value={materialDropdownOpen ? materialSearch : formData.material}
            onChange={(e) => {
              setMaterialSearch(e.target.value);
              setMaterialDropdownOpen(true);
            }}
            onFocus={() => {
              setMaterialDropdownOpen(true);
              setMaterialSearch('');
            }}
          />
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
          {materialDropdownOpen && (
            <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-48 overflow-y-auto">
              {filteredMaterials.length === 0 ? (
                <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</div>
              ) : (
                <>
                  {materialGroups.top.length > 0 && (
                    <>
                      <GroupHeading label={t('inventory.suggestedOptions')} />
                      {materialGroups.top.map(renderMaterialOption)}
                    </>
                  )}
                  {materialGroups.rest.length > 0 && (
                    <>
                      {materialGroups.top.length > 0 && <GroupHeading label={t('inventory.allOptions')} />}
                      {materialGroups.rest.map(renderMaterialOption)}
                    </>
                  )}
                </>
              )}
              {/* Allow custom material */}
              {materialSearch && !filteredMaterials.includes(materialSearch) && (
                <button
                  type="button"
                  className="w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary text-bambu-green border-t border-bambu-dark-tertiary"
                  onClick={() => {
                    updateIdentityField('material', materialSearch);
                    setMaterialDropdownOpen(false);
                    setMaterialSearch('');
                  }}
                >
                  {t('inventory.useCustomMaterial', { material: materialSearch })}
                </button>
              )}
            </div>
          )}
        </div>
        {errors?.material && (
          <p className="mt-1 text-xs text-red-700 dark:text-red-400">{errors.material}</p>
        )}
      </div>

      {/* Brand (dropdown with search) */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">
          {t('inventory.brand')}{detailsRequired && ' *'}
        </label>
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
                  <>
                    {brandGroups.top.length > 0 && (
                      <>
                        <GroupHeading label={t('inventory.suggestedOptions')} />
                        {brandGroups.top.map(renderBrandOption)}
                      </>
                    )}
                    {brandGroups.rest.length > 0 && (
                      <>
                        {brandGroups.top.length > 0 && <GroupHeading label={t('inventory.allOptions')} />}
                        {brandGroups.rest.map(renderBrandOption)}
                      </>
                    )}
                  </>
                )}
                {/* Allow custom brand */}
                {brandSearch && !filteredBrands.includes(brandSearch) && (
                  <button
                    type="button"
                    className="w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary text-bambu-green border-t border-bambu-dark-tertiary"
                    onClick={() => {
                      updateIdentityField('brand', brandSearch);
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
          {errors?.brand && (
            <p className="mt-1 text-xs text-red-700 dark:text-red-400">{errors.brand}</p>
          )}
      </div>

      {/* Variant / Subtype */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">
          {t('inventory.subtype')}{detailsRequired && ' *'}
        </label>
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
                        updateIdentityField('subtype', variant);
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
                      updateIdentityField('subtype', subtypeSearch);
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
          {errors?.subtype && (
            <p className="mt-1 text-xs text-red-700 dark:text-red-400">{errors.subtype}</p>
          )}
      </div>

      {/* Label Weight */}
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.labelWeight')}</label>
        <div className="relative">
          <input
            type="number"
            className="w-full px-3 py-2 pr-7 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
            value={labelInput}
            min={0}
            onFocus={() => setIsLabelFocused(true)}
            onChange={(e) => setLabelInput(e.target.value)}
            onBlur={() => {
              setIsLabelFocused(false);
              const raw = labelInput.trim();
              const next = Number(raw);
              if (!raw || !Number.isFinite(next) || next < 0) {
                setLabelInput(String(formData.label_weight));
                return;
              }
              const rounded = Math.round(next);
              updateField('label_weight', rounded);
              setLabelInput(String(rounded));
            }}
          />
          <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-bambu-gray">g</span>
        </div>
      </div>

      {/* Quantity — only in quick-add mode */}
      {quickAdd && (
        <div>
          <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.quantity')}</label>
          <input
            type="number"
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
            value={quantity}
            min={1}
            max={100}
            onChange={(e) => {
              const val = Math.max(1, Math.min(100, parseInt(e.target.value) || 1));
              onQuantityChange(val);
            }}
          />
        </div>
      )}

    </div>
  );
}
