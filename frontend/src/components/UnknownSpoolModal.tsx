import { useTranslation } from 'react-i18next';
import { ConfirmModal } from './ConfirmModal';
import { getSwatchStyle } from '../utils/colors';
import type { UnknownSpoolPrompt } from '../hooks/useUnknownTagPrompt';

interface UnknownSpoolModalProps {
  prompt: UnknownSpoolPrompt | null;
  isPending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function UnknownSpoolModal({ prompt, isPending, onConfirm, onCancel }: UnknownSpoolModalProps) {
  const { t } = useTranslation();
  if (!prompt) return null;

  const location = `${prompt.printer_name} • ${prompt.ams_label} • ${prompt.slot_label}`;
  const swatchStyle = prompt.color_hex ? getSwatchStyle(prompt.color_hex) : undefined;

  return (
    <ConfirmModal
      title={t('inventory.unknownSpoolTitle')}
      message={t('inventory.unknownSpoolMessage', { location })}
      confirmText={t('inventory.addToInventory')}
      cancelText={t('common.cancel')}
      variant="default"
      isLoading={isPending}
      loadingText={t('inventory.addToInventoryPending')}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <div className="flex items-center gap-3 p-3 rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary">
        {swatchStyle && (
          <div
            className="w-8 h-8 rounded-full border border-black/20 flex-shrink-0"
            style={swatchStyle}
            aria-label={prompt.color_hex ?? undefined}
          />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-white text-sm font-medium truncate">
            {prompt.brand ? `${prompt.brand} ${prompt.material ?? ''}`.trim() : prompt.material ?? '—'}
          </p>
          {prompt.color_hex && (
            <p className="text-xs text-bambu-gray font-mono uppercase">#{prompt.color_hex.replace(/^#/, '').slice(0, 6)}</p>
          )}
        </div>
      </div>
    </ConfirmModal>
  );
}
