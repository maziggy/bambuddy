import { useTranslation } from 'react-i18next';
import type { MultiFileMode } from './multiFile';

interface MultiFileModeToggleProps {
  value: MultiFileMode;
  onChange: (next: MultiFileMode) => void;
  /** Set when one model has several files: "Alternatives" is then not offered. */
  blockedModel: string | null;
  /** Neither mode is offered, while the files are being queued: a switch
   *  mid-run could leave the rest of a partly queued run as alternatives. */
  disabled?: boolean;
}

const BUTTON_BASE = 'flex-1 px-3 py-2 rounded-lg border transition-colors text-sm';
const SELECTED = 'border-bambu-green bg-bambu-green/10 text-white';
const IDLE = 'border-bambu-dark-tertiary bg-bambu-dark text-bambu-gray hover:border-bambu-gray';

const buttonClass = (selected: boolean, disabled: boolean) =>
  `${BUTTON_BASE} ${selected ? SELECTED : IDLE} ${
    // An idle choice that can't be made loses its hover too.
    disabled ? `opacity-50 cursor-not-allowed${selected ? '' : ' hover:border-bambu-dark-tertiary'}` : ''
  }`;

/**
 * Chooses what several selected sliced files become (#3462): one job with
 * alternatives (#671) or one queue item per file. Same look as the
 * printer/model toggle in PrinterSelector.
 */
export function MultiFileModeToggle({ value, onChange, blockedModel, disabled = false }: MultiFileModeToggleProps) {
  const { t } = useTranslation();
  const blocked = blockedModel != null;

  return (
    <div className="mb-4">
      <div role="radiogroup" aria-label={t('printModal.multiFile.groupLabel')} className="flex gap-2">
        <button
          type="button"
          role="radio"
          aria-checked={value === 'alternatives'}
          disabled={blocked || disabled}
          onClick={() => onChange('alternatives')}
          className={buttonClass(value === 'alternatives', blocked || disabled)}
        >
          {t('printModal.multiFile.alternatives')}
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={value === 'separate'}
          disabled={disabled}
          onClick={() => onChange('separate')}
          className={buttonClass(value === 'separate', disabled)}
        >
          {t('printModal.multiFile.separate')}
        </button>
      </div>
      <p className="text-xs text-bambu-gray mt-2">
        {blocked
          ? t('printModal.multiFile.alternativesBlocked', { model: blockedModel })
          : value === 'alternatives'
            ? t('printModal.multiFile.alternativesHint')
            : t('printModal.multiFile.separateHint')}
      </p>
    </div>
  );
}
