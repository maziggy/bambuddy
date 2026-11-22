import { useTranslation } from 'react-i18next';
import type { InventorySpool } from '../api/client';
import { getSwatchStyle, resolveSpoolColorName } from '../utils/colors';
import type { LinkChoice } from '../utils/rfidTagLink';

interface SpoolLinkPickerProps {
  choices: LinkChoice[];
  selectedId: number | null;
  onSelect: (spoolId: number) => void;
  material?: string | null;
  isLoading?: boolean;
  disabled?: boolean;
}

function spoolName(spool: InventorySpool): string {
  const colour = resolveSpoolColorName(spool.color_name, spool.rgba);
  const name = [spool.brand, spool.material, spool.subtype].filter(Boolean).join(' ');
  return colour ? `${name} - ${colour}` : name;
}

/**
 * The spools an RFID tag can be linked to (#3658), as a list of radio rows:
 * id, name, used weight and note, which is what tells an opened spool from a
 * sealed one of the same colour.
 */
export function SpoolLinkPicker({ choices, selectedId, onSelect, material, isLoading, disabled }: SpoolLinkPickerProps) {
  const { t } = useTranslation();

  if (isLoading) {
    return <p className="text-sm text-bambu-gray">{t('common.loading')}</p>;
  }
  if (choices.length === 0) {
    return (
      <p className="text-sm text-bambu-gray">
        {material ? t('inventory.rfidLink.noSpoolsOf', { material }) : t('inventory.rfidLink.noSpools')}
      </p>
    );
  }

  const matching = choices.filter((c) => c.matches);
  const others = choices.filter((c) => !c.matches);
  const groups = [
    { key: 'matching', label: t('inventory.rfidLink.matchingSpools'), items: matching },
    {
      key: 'others',
      label: material ? t('inventory.rfidLink.otherSpoolsOf', { material }) : t('inventory.rfidLink.otherSpools'),
      items: others,
    },
  ].filter((g) => g.items.length > 0);

  return (
    <div role="radiogroup" aria-label={t('inventory.rfidLink.pickSpool')} className="space-y-3 max-h-72 overflow-y-auto pr-1">
      {groups.map((group) => (
        <div key={group.key} className="space-y-1">
          <p className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">{group.label}</p>
          {group.items.map(({ spool }) => {
            const selected = spool.id === selectedId;
            return (
              <label
                key={spool.id}
                className={`flex items-center gap-2 p-2 rounded-lg border cursor-pointer transition-colors ${
                  selected
                    ? 'border-bambu-green bg-bambu-green/10'
                    : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray'
                } ${disabled ? 'opacity-60 cursor-not-allowed' : ''}`}
              >
                <input
                  type="radio"
                  name="rfid-link-spool"
                  value={spool.id}
                  checked={selected}
                  disabled={disabled}
                  onChange={() => onSelect(spool.id)}
                  className="accent-bambu-green"
                />
                <span
                  className="w-5 h-5 rounded-full border border-black/20 flex-shrink-0"
                  style={getSwatchStyle(spool.rgba)}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline gap-1.5">
                    <span className="text-xs font-mono text-bambu-gray shrink-0">#{spool.id}</span>
                    <span className="text-sm text-white truncate">{spoolName(spool)}</span>
                  </span>
                  <span className="flex items-baseline gap-1.5 text-xs text-bambu-gray min-w-0">
                    <span className="shrink-0">{t('inventory.rfidLink.usedWeight', { grams: Math.round(spool.weight_used || 0) })}</span>
                    {spool.note && <span className="italic truncate">“{spool.note}”</span>}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      ))}
    </div>
  );
}
