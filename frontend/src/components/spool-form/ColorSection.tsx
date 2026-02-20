import { useState, useMemo } from 'react';
import { Search, Clock, ChevronDown, ChevronUp } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ColorSectionProps, CatalogDisplayColor } from './types';
import { QUICK_COLORS, ALL_COLORS } from './constants';

export function ColorSection({
  formData,
  updateField,
  recentColors,
  onColorUsed,
  catalogColors,
}: ColorSectionProps) {
  const { t } = useTranslation();
  const [showAllColors, setShowAllColors] = useState(false);
  const [colorSearch, setColorSearch] = useState('');

  // Current hex without # prefix
  const currentHex = formData.rgba.replace('#', '').substring(0, 6);

  const isSelected = (hex: string) => {
    return currentHex.toUpperCase() === hex.toUpperCase();
  };

  const selectColor = (hex: string, name: string) => {
    // Store as RRGGBBAA (with FF alpha)
    updateField('rgba', hex.toUpperCase() + 'FF');
    updateField('color_name', name);
    onColorUsed({ name, hex });
  };

  // Filter catalog colors by the selected brand + material + subtype
  // Brand matching is word-based: "mz - Bambu" matches "Bambu Lab" because both contain "Bambu"
  // Material matching: try exact "PETG Basic" first, fall back to base material "PETG" prefix
  const matchedCatalogColors = useMemo<CatalogDisplayColor[]>(() => {
    if (catalogColors.length === 0) return [];
    const brand = formData.brand?.trim();
    const material = formData.material?.toLowerCase().trim();
    const subtype = formData.subtype?.toLowerCase().trim();
    if (!brand && !material) return [];

    // Split brand into words (>= 2 chars) for word-based matching
    const brandWords = brand
      ? brand.toLowerCase().split(/[\s\-_]+/).filter(w => w.length >= 2)
      : [];

    const brandMatches = (manufacturer: string) => {
      if (brandWords.length === 0) return true; // no brand filter
      const mfrLower = manufacturer.toLowerCase();
      // Any significant brand word found in manufacturer name
      return brandWords.some(w => mfrLower.includes(w));
    };

    // If only brand is provided, return all colors for that manufacturer
    if (brand && !material) {
      const byBrand = catalogColors.filter(c => brandMatches(c.manufacturer));
      if (byBrand.length > 0) {
        return byBrand.map(c => ({
          name: c.color_name,
          hex: c.hex_color.replace('#', '').substring(0, 6),
          manufacturer: c.manufacturer,
          material: typeof c.material === 'string' ? c.material : undefined,
        }));
      }
    }

    // Build the combined material+subtype string to match catalog entries
    const fullMaterial = material && subtype ? `${material} ${subtype}` : '';

    // First pass: try exact fullMaterial match (e.g. "PETG Basic")
    if (fullMaterial) {
      const exact = catalogColors.filter(c =>
        brandMatches(c.manufacturer) &&
        c.material?.toLowerCase() === fullMaterial,
      );
      if (exact.length > 0) {
        return exact.map(c => ({
          name: c.color_name,
          hex: c.hex_color.replace('#', '').substring(0, 6),
          manufacturer: c.manufacturer,
          material: typeof c.material === 'string' ? c.material : undefined,
        }));
      }
      // Try without trailing "+" (e.g. "PLA Silk+" -> "PLA Silk")
      const normalized = fullMaterial.replace(/\+$/, '');
      if (normalized !== fullMaterial) {
        const normMatch = catalogColors.filter(c =>
          brandMatches(c.manufacturer) &&
          c.material?.toLowerCase() === normalized,
        );
        if (normMatch.length > 0) {
          return normMatch.map(c => ({
            name: c.color_name,
            hex: c.hex_color.replace('#', '').substring(0, 6),
            manufacturer: c.manufacturer,
            material: typeof c.material === 'string' ? c.material : undefined,
          }));
        }
      }
    }

    // Second pass: match base material prefix (e.g. "PETG" matches "PETG Basic", "PETG-HF")
    if (material) {
      const byMaterial = catalogColors.filter(c =>
        brandMatches(c.manufacturer) &&
        (!c.material || c.material.toLowerCase().startsWith(material)),
      );
      if (byMaterial.length > 0) {
        return byMaterial.map(c => ({
          name: c.color_name,
          hex: c.hex_color.replace('#', '').substring(0, 6),
          manufacturer: c.manufacturer,
          material: typeof c.material === 'string' ? c.material : undefined,
        }));
      }
    }

    return [];
  }, [catalogColors, formData.brand, formData.material, formData.subtype]);

  const catalogSearchResults = useMemo<CatalogDisplayColor[]>(() => {
    if (!colorSearch) return matchedCatalogColors;
    if (matchedCatalogColors.length === 0) return [];
    const q = colorSearch.toLowerCase();
    const matches = matchedCatalogColors.filter(c =>
      c.name.toLowerCase().includes(q) ||
      (c.manufacturer?.toLowerCase().includes(q) ?? false) ||
      (c.material?.toLowerCase().includes(q) ?? false),
    );
    return matches;
  }, [colorSearch, matchedCatalogColors]);

  // Only show catalog section if there are matched catalog colors
  const showCatalogSection = matchedCatalogColors.length > 0;

  // Fallback hardcoded colors for search/expand
  const filteredFallbackColors = useMemo(() => {
    if (colorSearch) {
      return ALL_COLORS.filter(c =>
        c.name.toLowerCase().includes(colorSearch.toLowerCase()),
      );
    }
    return showAllColors ? ALL_COLORS : QUICK_COLORS;
  }, [colorSearch, showAllColors]);

  return (
    <div className="space-y-3">
      {/* Color preview banner */}
      <div
        className="h-10 rounded-lg border border-bambu-dark-tertiary"
        style={{ backgroundColor: `#${currentHex}` }}
      />

      {/* Recently Used Colors */}
      {recentColors.length > 0 && (
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 text-xs text-bambu-gray shrink-0">
            <Clock className="w-3 h-3" />
            <span>{t('inventory.recentColors')}</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {recentColors.map(color => (
              <button
                key={color.hex}
                type="button"
                onClick={() => selectColor(color.hex, color.name)}
                className={`w-6 h-6 rounded border-2 transition-all hover:scale-110 ${
                  isSelected(color.hex)
                    ? 'border-bambu-green ring-1 ring-bambu-green/30 scale-110'
                    : 'border-bambu-dark-tertiary'
                }`}
                style={{ backgroundColor: `#${color.hex}` }}
                title={color.name}
              />
            ))}
          </div>
        </div>
      )}

      {/* Color Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
        <input
          type="text"
          className="w-full pl-9 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
          placeholder={t('inventory.searchColors')}
          value={colorSearch}
          onChange={(e) => setColorSearch(e.target.value)}
        />
      </div>

      {/* Color Swatches */}
      {showCatalogSection ? (
        /* Catalog colors matching selected brand/material */
        <div className="space-y-1.5">
          <span className="text-xs text-bambu-gray">
            {colorSearch ? t('inventory.searchResults') : `${formData.brand}${formData.material ? ` ${formData.material}` : ''}`}
          </span>
          <div className="flex flex-wrap gap-1.5">
            {catalogSearchResults.map(color => (
              <button
                key={`${color.hex}-${color.name}-${color.manufacturer ?? ''}`}
                type="button"
                onClick={() => selectColor(color.hex, color.name)}
                className={`w-6 h-6 rounded border-2 transition-all hover:scale-110 hover:z-20 relative group ${
                  isSelected(color.hex)
                    ? 'border-bambu-green ring-1 ring-bambu-green/30 scale-110'
                    : 'border-bambu-dark-tertiary'
                }`}
                style={{ backgroundColor: `#${color.hex}` }}
                title={
                  color.manufacturer && color.material
                    ? `${color.name} (${color.manufacturer} — ${color.material})`
                    : color.manufacturer
                    ? `${color.name} (${color.manufacturer})`
                    : color.name
                }
              >
                <span className="absolute -bottom-7 left-1/2 -translate-x-1/2 px-2 py-0.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-20 shadow-lg text-white">
                  {color.manufacturer && color.material
                    ? `${color.name} (${color.manufacturer} — ${color.material})`
                    : color.manufacturer
                    ? `${color.name} (${color.manufacturer})`
                    : color.name}
                </span>
              </button>
            ))}
            {catalogSearchResults.length === 0 && (
              <p className="text-sm text-bambu-gray py-1">{t('inventory.noColorsFound')}</p>
            )}
          </div>
        </div>
      ) : (
        /* Fallback: hardcoded color palette (no brand/material selected or no catalog matches) */
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs text-bambu-gray">
            <span>{colorSearch ? t('inventory.searchResults') : (showAllColors ? t('inventory.allColors') : t('inventory.commonColors'))}</span>
            {!colorSearch && (
              <button
                type="button"
                onClick={() => setShowAllColors(!showAllColors)}
                className="flex items-center gap-1 hover:text-white transition-colors"
              >
                {showAllColors ? (
                  <>{t('inventory.showLess')} <ChevronUp className="w-3 h-3" /></>
                ) : (
                  <>{t('inventory.showAll')} <ChevronDown className="w-3 h-3" /></>
                )}
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {filteredFallbackColors.map(color => (
              <button
                key={color.hex}
                type="button"
                onClick={() => selectColor(color.hex, color.name)}
                className={`w-6 h-6 rounded border-2 transition-all hover:scale-110 hover:z-20 relative group ${
                  isSelected(color.hex)
                    ? 'border-bambu-green ring-1 ring-bambu-green/30 scale-110'
                    : 'border-bambu-dark-tertiary'
                }`}
                style={{ backgroundColor: `#${color.hex}` }}
                title={color.name}
              >
                <span className="absolute -bottom-7 left-1/2 -translate-x-1/2 px-2 py-0.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-20 shadow-lg text-white">
                  {color.name}
                </span>
              </button>
            ))}
            {filteredFallbackColors.length === 0 && (
              <p className="text-sm text-bambu-gray py-1">{t('inventory.noColorsFound')}</p>
            )}
          </div>
        </div>
      )}

      {/* Manual Color Input */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.colorName')}</label>
          <input
            type="text"
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
            placeholder={t('inventory.colorNamePlaceholder')}
            value={formData.color_name}
            onChange={(e) => updateField('color_name', e.target.value)}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.hexColor')}</label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-bambu-gray">#</span>
              <input
                type="text"
                className="w-full pl-7 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm font-mono uppercase focus:outline-none focus:border-bambu-green"
                placeholder="RRGGBB"
                value={currentHex.toUpperCase()}
                onChange={(e) => {
                  const val = e.target.value.replace('#', '').replace(/[^0-9A-Fa-f]/g, '');
                  if (val.length <= 8) updateField('rgba', val.toUpperCase() + (val.length <= 6 ? 'FF' : ''));
                }}
              />
            </div>
            <input
              type="color"
              className="w-11 h-[38px] rounded-lg cursor-pointer border border-bambu-dark-tertiary shrink-0 bg-transparent"
              value={`#${currentHex}`}
              onChange={(e) => {
                const hex = e.target.value.replace('#', '').toUpperCase();
                updateField('rgba', hex + 'FF');
              }}
              title={t('inventory.pickColor')}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
