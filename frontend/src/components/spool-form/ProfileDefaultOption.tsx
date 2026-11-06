import { Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export type ProfileDefaultScope = 'all' | 'colour';

interface ProfileDefaultOptionProps {
  /** "Bambu Lab PLA Basic": the product the default would be for. */
  product: string;
  colorName: string;
  /** Whether the tab was filled in from an existing default. */
  prefilled: boolean;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  scope: ProfileDefaultScope;
  onScopeChange: (scope: ProfileDefaultScope) => void;
  /** No material yet: there is no product to save a default for. */
  disabled: boolean;
}

/**
 * Below the Printers tab (#1326): says when the tab came from the product's
 * profile default, and offers to save what the tab holds as that default.
 *
 * "All colours" is preselected: it is what saves the work on the first spool
 * of every colour, while a colour of its own is for one calibrated apart.
 */
export function ProfileDefaultOption({
  product,
  colorName,
  prefilled,
  checked,
  onCheckedChange,
  scope,
  onScopeChange,
  disabled,
}: ProfileDefaultOptionProps) {
  const { t } = useTranslation();
  const colour = colorName.trim();

  return (
    <div className="mt-3 p-3 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg space-y-2">
      {prefilled && (
        <p className="flex items-center gap-1.5 text-xs text-bambu-green">
          <Sparkles className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          {t('inventory.profileDefaults.prefilled', { product })}
        </p>
      )}
      <label className={`flex items-center gap-2 text-sm ${disabled ? 'text-bambu-gray' : 'text-white cursor-pointer'}`}>
        <input
          type="checkbox"
          checked={checked && !disabled}
          disabled={disabled}
          onChange={(e) => onCheckedChange(e.target.checked)}
          className="w-4 h-4 accent-bambu-green"
        />
        {disabled
          ? t('inventory.profileDefaults.saveNeedsMaterial')
          : t('inventory.profileDefaults.saveAsDefault', { product })}
      </label>
      {checked && !disabled && (
        <div role="radiogroup" aria-label={t('inventory.profileDefaults.scope')} className="flex flex-wrap gap-4 pl-6">
          <label className="flex items-center gap-2 text-sm text-white cursor-pointer">
            <input
              type="radio"
              name="profile-default-scope"
              checked={scope === 'all' || !colour}
              onChange={() => onScopeChange('all')}
              className="accent-bambu-green"
            />
            {t('inventory.profileDefaults.allColours')}
          </label>
          <label className={`flex items-center gap-2 text-sm ${colour ? 'text-white cursor-pointer' : 'text-bambu-gray'}`}>
            <input
              type="radio"
              name="profile-default-scope"
              checked={scope === 'colour' && !!colour}
              disabled={!colour}
              onChange={() => onScopeChange('colour')}
              className="accent-bambu-green"
            />
            {colour
              ? t('inventory.profileDefaults.colourOnly', { colour })
              : t('inventory.profileDefaults.colourOnlyNeedsColour', { tab: t('inventory.colorAndCostTab') })}
          </label>
        </div>
      )}
    </div>
  );
}
