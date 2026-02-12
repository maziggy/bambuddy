import { useState, useMemo } from 'react';
import { Search, Clock, ChevronDown, ChevronUp } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ColorSectionProps } from './types';
import { QUICK_COLORS, ALL_COLORS } from './constants';

export function ColorSection({
  formData,
  updateField,
  recentColors,
  onColorUsed,
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

  // Colors to show based on search/expand state
  const filteredColors = useMemo(() => {
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

      {/* Color Swatches Grid */}
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
          {filteredColors.map(color => (
            <button
              key={color.hex}
              type="button"
              onClick={() => selectColor(color.hex, color.name)}
              className={`w-6 h-6 rounded border-2 transition-all hover:scale-110 relative group ${
                isSelected(color.hex)
                  ? 'border-bambu-green ring-1 ring-bambu-green/30 scale-110'
                  : 'border-bambu-dark-tertiary'
              }`}
              style={{ backgroundColor: `#${color.hex}` }}
              title={color.name}
            >
              <span className="absolute -bottom-7 left-1/2 -translate-x-1/2 px-2 py-0.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-xs whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10 shadow-lg text-white">
                {color.name}
              </span>
            </button>
          ))}
          {filteredColors.length === 0 && (
            <p className="text-sm text-bambu-gray py-1">{t('inventory.noColorsFound')}</p>
          )}
        </div>
      </div>

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
