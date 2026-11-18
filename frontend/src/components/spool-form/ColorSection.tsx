import { useState, useMemo, useEffect, useRef } from 'react';
import { Search, Clock, ChevronDown, ChevronUp, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ColorSectionProps, CatalogDisplayColor } from './types';
import { QUICK_COLORS, ALL_COLORS } from './constants';
import { FilamentSwatch } from '../FilamentSwatch';
import { buildFilamentBackground, FILAMENT_EFFECT_OPTIONS } from '../filamentSwatchHelpers';
import { getSwatchStyle } from '../../utils/colors';
import { matchCatalogColors, allBrandCatalogColors } from './catalogColors';

/** Parse user paste from 3dfilamentprofiles.com etc.: split on commas/whitespace,
 *  drop the leading `#`, accept 6/8-char hex, lowercase. Returns null when no
 *  valid stops are found. Mirrors the server-side validator output. */
function normalizeExtraColorsInput(raw: string): { value: string; invalid: string[] } {
  const tokens = raw
    .split(/[\s,]+/)
    .map((t) => t.trim().replace(/^#/, ''))
    .filter(Boolean);
  const valid: string[] = [];
  const invalid: string[] = [];
  for (const tok of tokens) {
    if ((tok.length === 6 || tok.length === 8) && /^[0-9a-fA-F]+$/.test(tok)) {
      valid.push(tok.toLowerCase());
    } else {
      invalid.push(tok);
    }
  }
  return { value: valid.join(','), invalid };
}

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

  // Current rgba in canonical 8-char uppercase form (RRGGBBAA). Used both for
  // preset selection matching and to derive the 6-char hex shown in the
  // native picker / draft input. #1545: alpha is tracked end-to-end so the
  // `Clear` preset (00000000) stays distinct from black (000000FF).
  const currentRgba = (formData.rgba.replace('#', '') + 'FF').substring(0, 8).toUpperCase();
  const currentHex = currentRgba.substring(0, 6);
  const currentAlpha = currentRgba.substring(6, 8);
  const isTransparent = currentAlpha === '00';

  // Draft state for the manual hex input. Decoupled from `formData.rgba` so
  // mid-typing values (1–7 chars) don't trigger the immediate auto-pad
  // that used to drop every keystroke past the first (#1407). The draft is
  // committed to `formData.rgba` once it reaches 6 or 8 chars, or on blur
  // (where shorter drafts are padded with `0` so the backend never sees a
  // malformed rgba — preserves the #1055 invariant). The useEffect re-syncs
  // the draft whenever an external action (color picker, swatch click,
  // edit-mode load) changes the rgba.
  const [hexDraft, setHexDraft] = useState(isTransparent ? currentRgba : currentHex);
  useEffect(() => {
    setHexDraft(isTransparent ? currentRgba : currentHex);
  }, [currentHex, currentRgba, isTransparent]);

  // Match presets on the full rgba so 'Clear' (00000000) doesn't collide with
  // 'Black' (000000FF). A 6-char preset hex is treated as alpha=FF.
  const isSelected = (hex: string) => {
    const presetRgba = (hex.replace('#', '') + 'FF').substring(0, 8).toUpperCase();
    return currentRgba === presetRgba;
  };

  // Visual style for a swatch button — delegates to the shared helper so the
  // `Clear` preset (alpha=00) renders as a checkerboard everywhere the same
  // way instead of vanishing under a transparent backgroundColor (#1545).
  const swatchStyle = (hex: string) => getSwatchStyle(hex);

  const selectColor = (
    hex: string,
    name: string,
    // #1340: catalog entries carry an optional gradient + effect. Pass them in
    // (even as empty strings) to overwrite the spool's existing values — the
    // catalog entry is a complete preset, the user explicitly chose its look.
    // Pass `undefined` (the default, used by recent/fallback swatches) to
    // leave any existing gradient/effect untouched — those buttons are plain
    // hex pickers, not full presets.
    extraColors?: string | null,
    effectType?: string | null,
  ) => {
    // Store as RRGGBBAA. 6-char presets get alpha=FF appended; 8-char presets
    // (e.g. the `Clear` swatch, 00000000) are preserved as-is so the
    // transparency reaches the backend (#1545).
    const cleaned = hex.replace('#', '').toUpperCase();
    const rgba = cleaned.length === 8 ? cleaned : cleaned + 'FF';
    updateField('rgba', rgba);
    updateField('color_name', name);
    if (extraColors !== undefined) {
      const next = extraColors ?? '';
      setExtraColorsDraft(next);
      setExtraColorsErrors([]);
      lastCommittedExtraColorsRef.current = next;
      updateField('extra_colors', next);
    }
    if (effectType !== undefined) {
      updateField('effect_type', effectType ?? '');
    }
    onColorUsed({ name, hex });
  };

  // Filter catalog colors by the selected brand + material + subtype (see catalogColors.ts)
  const matchedCatalogColors = useMemo<CatalogDisplayColor[]>(
    () => matchCatalogColors(catalogColors, formData.brand, formData.material, formData.subtype),
    [catalogColors, formData.brand, formData.material, formData.subtype],
  );

  // #3619: "Show all colours" widens the list to every catalog colour of the brand
  // (matching ones first). Only offered when that adds colours.
  const [showAllCatalog, setShowAllCatalog] = useState(false);
  const allBrandColors = useMemo<CatalogDisplayColor[]>(
    () => allBrandCatalogColors(catalogColors, formData.brand, matchedCatalogColors),
    [catalogColors, formData.brand, matchedCatalogColors],
  );
  const canShowAllCatalog = allBrandColors.length > matchedCatalogColors.length;
  const showingAllCatalog = showAllCatalog && canShowAllCatalog;
  const visibleCatalogColors = showingAllCatalog ? allBrandColors : matchedCatalogColors;
  // A new product starts with its own colours again.
  useEffect(() => {
    setShowAllCatalog(false);
  }, [formData.brand, formData.material]);

  const catalogSearchResults = useMemo<CatalogDisplayColor[]>(() => {
    if (!colorSearch) return visibleCatalogColors;
    if (visibleCatalogColors.length === 0) return [];
    const q = colorSearch.toLowerCase();
    const matches = visibleCatalogColors.filter(c =>
      c.name.toLowerCase().includes(q) ||
      (c.manufacturer?.toLowerCase().includes(q) ?? false) ||
      (c.material?.toLowerCase().includes(q) ?? false),
    );
    return matches;
  }, [colorSearch, visibleCatalogColors]);

  // Show the catalog section for matched colours, or when the user asked for all of the
  // brand's colours although none matches the material (#3619).
  const showCatalogSection = matchedCatalogColors.length > 0 || showingAllCatalog;

  // Fallback hardcoded colors for search/expand
  const filteredFallbackColors = useMemo(() => {
    if (colorSearch) {
      return ALL_COLORS.filter(c =>
        c.name.toLowerCase().includes(colorSearch.toLowerCase()),
      );
    }
    return showAllColors ? ALL_COLORS : QUICK_COLORS;
  }, [colorSearch, showAllColors]);

  // #1154: editable buffer for the multi-colour paste field. We keep the raw
  // text the user typed/pasted so they can still see invalid tokens — only
  // commit the canonical form to formData on blur or when valid.
  const [extraColorsDraft, setExtraColorsDraft] = useState<string>(formData.extra_colors);
  const [extraColorsErrors, setExtraColorsErrors] = useState<string[]>([]);

  // #1154 follow-up: when the modal opens to edit an existing spool, the
  // parent's ``setFormData(...)`` lands in a useEffect AFTER ColorSection
  // already mounted with the default-empty formData. Without resyncing,
  // ``extraColorsDraft`` stays at the initial '' and the field appears
  // empty even though the spool has saved colours (visible as the gradient
  // banner above). Track our own commits via a ref so external formData
  // updates resync the draft without clobbering live user typing.
  const lastCommittedExtraColorsRef = useRef<string>(formData.extra_colors);
  useEffect(() => {
    if (formData.extra_colors !== lastCommittedExtraColorsRef.current) {
      setExtraColorsDraft(formData.extra_colors);
      setExtraColorsErrors([]);
      lastCommittedExtraColorsRef.current = formData.extra_colors;
    }
  }, [formData.extra_colors]);
  const previewBackground = useMemo(
    () =>
      buildFilamentBackground({
        rgba: formData.rgba,
        extraColors: formData.extra_colors,
        effectType: formData.effect_type,
        subtype: formData.subtype,
        effectSize: 'bar',
      }),
    [formData.rgba, formData.extra_colors, formData.effect_type, formData.subtype],
  );

  const commitExtraColors = (text: string) => {
    setExtraColorsDraft(text);
    if (!text.trim()) {
      setExtraColorsErrors([]);
      lastCommittedExtraColorsRef.current = '';
      updateField('extra_colors', '');
      return;
    }
    const { value, invalid } = normalizeExtraColorsInput(text);
    setExtraColorsErrors(invalid);
    lastCommittedExtraColorsRef.current = value;
    updateField('extra_colors', value);
  };

  return (
    <div className="space-y-3">
      {/* Color preview banner — shows gradient + effect overlay. */}
      <div
        className="h-10 rounded-lg border border-bambu-dark-tertiary"
        style={previewBackground}
        data-testid="color-preview-banner"
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
                style={swatchStyle(color.hex)}
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
          <div className="flex items-center justify-between text-xs text-bambu-gray">
            <span>
              {colorSearch
                ? t('inventory.searchResults')
                : showingAllCatalog
                  ? t('inventory.catalogColors.allBrand', { brand: formData.brand.trim() })
                  : `${formData.brand}${formData.material ? ` ${formData.material}` : ''}`}
            </span>
            {!colorSearch && canShowAllCatalog && (
              <button
                type="button"
                onClick={() => setShowAllCatalog(!showAllCatalog)}
                aria-expanded={showingAllCatalog}
                className="flex items-center gap-1 hover:text-white transition-colors"
                data-testid="catalog-colors-toggle"
              >
                {showAllCatalog ? (
                  <>{matchedCatalogColors.length > 0 ? t('inventory.catalogColors.showMatching') : t('inventory.showLess')} <ChevronUp className="w-3 h-3" /></>
                ) : (
                  <>{t('inventory.catalogColors.showAll')} <ChevronDown className="w-3 h-3" /></>
                )}
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {catalogSearchResults.map(color => (
              <button
                key={`${color.hex}-${color.name}-${color.manufacturer ?? ''}`}
                type="button"
                onClick={() => selectColor(color.hex, color.name, color.extra_colors, color.effect_type)}
                className={`w-6 h-6 rounded border-2 transition-all hover:scale-110 hover:z-20 relative group ${
                  isSelected(color.hex)
                    ? 'border-bambu-green ring-1 ring-bambu-green/30 scale-110'
                    : 'border-bambu-dark-tertiary'
                }`}
                style={swatchStyle(color.hex)}
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
              <span className="flex items-center gap-3">
                {/* No colour matches the material, but the brand has catalog colours (#3619). */}
                {canShowAllCatalog && (
                  <button
                    type="button"
                    onClick={() => setShowAllCatalog(true)}
                    aria-expanded={false}
                    className="flex items-center gap-1 hover:text-white transition-colors"
                    data-testid="catalog-colors-toggle"
                  >
                    {t('inventory.catalogColors.allBrand', { brand: formData.brand.trim() })} <ChevronDown className="w-3 h-3" />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setShowAllColors(!showAllColors)}
                  aria-expanded={showAllColors}
                  className="flex items-center gap-1 hover:text-white transition-colors"
                >
                  {showAllColors ? (
                    <>{t('inventory.showLess')} <ChevronUp className="w-3 h-3" /></>
                  ) : (
                    <>{t('inventory.showAll')} <ChevronDown className="w-3 h-3" /></>
                  )}
                </button>
              </span>
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
                style={swatchStyle(color.hex)}
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
                value={hexDraft.toUpperCase()}
                onChange={(e) => {
                  // Sanitize: drop `#`, non-hex chars, uppercase. Accept up to
                  // 8 chars so power users can type an alpha byte (e.g.
                  // `00000000` for transparent / `00FF00FF` for opaque) —
                  // #1545. The Clear preset button covers the common case; the
                  // input handles arbitrary alphas. Draft holds 0–8 chars; we
                  // commit when the value is a complete 6-char (alpha defaults
                  // to FF) or 8-char hex, preserving the #1055/#1407
                  // invariants.
                  const sanitized = e.target.value
                    .replace('#', '')
                    .replace(/[^0-9A-Fa-f]/g, '')
                    .toUpperCase();
                  const next = sanitized.length > 8 ? sanitized.substring(0, 8) : sanitized;
                  setHexDraft(next);
                  if (next.length === 6) {
                    updateField('rgba', next + 'FF');
                  } else if (next.length === 8) {
                    updateField('rgba', next);
                  }
                }}
                onBlur={() => {
                  // User left the field with a partial value — pad RGB to 6
                  // chars (alpha defaults to FF) and commit so the form state
                  // always carries a valid rgba when submitted. 7-char drafts
                  // (RGB + 1 alpha digit) pad the alpha nibble to 0 to reach
                  // a complete 8-char hex.
                  if (hexDraft.length === 7) {
                    const padded = hexDraft + '0';
                    setHexDraft(padded);
                    updateField('rgba', padded);
                  } else if (hexDraft.length > 0 && hexDraft.length < 6) {
                    const padded = hexDraft.padEnd(6, '0');
                    setHexDraft(padded);
                    updateField('rgba', padded + 'FF');
                  }
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

      {/* #1154: Multi-colour gradient stops + visual effect. Optional —
          empty values keep the spool rendering as a solid swatch. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2 border-t border-bambu-dark-tertiary/50">
        <div>
          <label className="block text-sm font-medium text-bambu-gray mb-1">
            {t('inventory.extraColorsLabel')}
          </label>
          <input
            type="text"
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm font-mono placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
            placeholder={t('inventory.extraColorsPlaceholder')}
            value={extraColorsDraft}
            onChange={(e) => commitExtraColors(e.target.value)}
            data-testid="extra-colors-input"
          />
          {extraColorsErrors.length > 0 && (
            <p className="text-xs text-red-700 dark:text-red-400 mt-1">
              {t('inventory.extraColorsInvalid', { tokens: extraColorsErrors.join(', ') })}
            </p>
          )}
          {!extraColorsErrors.length && (
            <p className="text-xs text-bambu-gray/70 mt-1">{t('inventory.extraColorsHint')}</p>
          )}
        </div>
        <div>
          <label className="block text-sm font-medium text-bambu-gray mb-1 flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5" />
            {t('inventory.colorEffectLabel')}
          </label>
          <div className="flex gap-2 items-stretch">
            <select
              className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
              value={formData.effect_type}
              onChange={(e) => updateField('effect_type', e.target.value)}
              data-testid="effect-type-select"
            >
              {FILAMENT_EFFECT_OPTIONS.map((opt) => (
                <option key={opt.value || 'none'} value={opt.value}>
                  {t(opt.labelKey)}
                </option>
              ))}
            </select>
            <FilamentSwatch
              rgba={formData.rgba}
              extraColors={formData.extra_colors}
              effectType={formData.effect_type}
              subtype={formData.subtype}
              effectSize="preview"
              className="w-10 h-10"
              shape="square"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
