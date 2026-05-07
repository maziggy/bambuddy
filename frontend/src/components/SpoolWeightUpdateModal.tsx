import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent } from './Card';
import { Button } from './Button';

interface SpoolWeightUpdateModalProps {
  isOpen: boolean;
  filamentName: string;
  oldWeight: number | null;
  newWeight: number;
  onConfirm: (keepExisting: boolean) => void;
  onClose: () => void;
}

export function SpoolWeightUpdateModal({
  isOpen,
  filamentName,
  oldWeight,
  newWeight,
  onConfirm,
  onClose,
}: SpoolWeightUpdateModalProps) {
  const { t } = useTranslation();
  const [keepExisting, setKeepExisting] = useState(false);

  useEffect(() => {
    if (isOpen) setKeepExisting(false);
  }, [isOpen]);

  if (!isOpen) return null;

  const oldWeightLabel = oldWeight !== null ? `${oldWeight}g` : '—';
  const newWeightLabel = `${newWeight}g`;

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <Card className="w-full max-w-md" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent className="p-6">
          <h3 className="text-lg font-semibold text-white mb-1">
            {t('settings.catalog.updateSpoolWeight')}
          </h3>
          <p className="text-bambu-gray text-sm mb-4">
            {filamentName}: {oldWeightLabel} → {newWeightLabel}
          </p>

          <div className="space-y-3">
            <label className="flex items-start gap-3 cursor-pointer p-3 rounded-lg border border-bambu-dark-tertiary hover:bg-bambu-dark transition-colors">
              <input
                type="radio"
                name="weight-update-mode"
                checked={!keepExisting}
                onChange={() => setKeepExisting(false)}
                className="mt-1 accent-bambu-green"
              />
              <div>
                <div className="text-sm font-medium text-white">
                  {t('settings.catalog.applyToAllSpools')}
                </div>
                <div className="text-xs text-bambu-gray mt-0.5">
                  {t('settings.catalog.applyToAllSpoolsDesc')}
                </div>
              </div>
            </label>

            <label className="flex items-start gap-3 cursor-pointer p-3 rounded-lg border border-bambu-dark-tertiary hover:bg-bambu-dark transition-colors">
              <input
                type="radio"
                name="weight-update-mode"
                checked={keepExisting}
                onChange={() => setKeepExisting(true)}
                className="mt-1 accent-bambu-green"
              />
              <div>
                <div className="text-sm font-medium text-white">
                  {t('settings.catalog.keepExistingSpoolWeight')}
                </div>
                <div className="text-xs text-bambu-gray mt-0.5">
                  {t('settings.catalog.keepExistingSpoolWeightDesc')}
                </div>
              </div>
            </label>
          </div>

          <div className="flex gap-3 mt-6">
            <Button variant="secondary" onClick={onClose} className="flex-1">
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => onConfirm(keepExisting)}
              className="flex-1 bg-bambu-green hover:bg-bambu-green-dark"
            >
              {t('common.confirm')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
