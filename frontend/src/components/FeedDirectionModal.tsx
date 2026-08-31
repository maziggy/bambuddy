import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import type { ExtruderSlot } from '../api/client';

// Hotend ids as the firmware numbers them. Mirrors BambuStudio's
// MAIN_EXTRUDER_ID / DEPUTY_EXTRUDER_ID and `fts_routing.py` on the backend.
const RIGHT_EXTRUDER = 0;
const LEFT_EXTRUDER = 1;

interface FeedDirectionModalProps {
  // Human-readable name of the slot being loaded, e.g. "AMS-A 3".
  slotLabel: string;
  // The slot's own coordinates, used to spot the hotend already holding it.
  amsId: number;
  slotId: number;
  extruderSlots: Record<string, ExtruderSlot>;
  isLoading?: boolean;
  onConfirm: (extruderId: number) => void;
  onCancel: () => void;
}

/**
 * Asks which hotend to feed a slot into, for printers with a Filament Track
 * Switch fitted.
 *
 * Without a switch each AMS is wired to one hotend and the firmware works the
 * target out for itself, so the load command carries no hotend at all. With one
 * fitted, every AMS is bound to a switch *inlet* instead and either hotend is
 * reachable — the firmware then has nothing to infer from and drops a command
 * that does not name one. BambuStudio asks the same question in the same place
 * (`FeedDirectionDialog`), including leaving Confirm disabled until a side is
 * picked, so there is no default to accidentally act on.
 */
export function FeedDirectionModal({
  slotLabel,
  amsId,
  slotId,
  extruderSlots,
  isLoading = false,
  onConfirm,
  onCancel,
}: FeedDirectionModalProps) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<number | null>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isLoading) onCancel();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onCancel, isLoading]);

  // A hotend already fed from this exact slot cannot be loaded from it again.
  const holdsThisSlot = (extruderId: number) => {
    const slot = extruderSlots[String(extruderId)];
    return slot?.ams_id === amsId && slot?.slot_id === slotId;
  };

  const options = [
    { extruderId: LEFT_EXTRUDER, label: t('printers.ams.feedLeft'), taken: holdsThisSlot(LEFT_EXTRUDER) },
    { extruderId: RIGHT_EXTRUDER, label: t('printers.ams.feedRight'), taken: holdsThisSlot(RIGHT_EXTRUDER) },
  ];
  const selectedIsTaken = options.some(o => o.extruderId === selected && o.taken);

  // Status keeps arriving while the dialog is open, so a hotend can become the
  // one holding this slot after it was picked — someone loading it from the
  // printer's own screen. Drop the selection rather than leave Confirm armed on
  // an option that has since been disabled.
  useEffect(() => {
    if (selectedIsTaken) setSelected(null);
  }, [selectedIsTaken]);

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50"
      onClick={isLoading ? undefined : onCancel}
    >
      <Card className="w-full max-w-md" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent className="p-6">
          <h3 className="text-lg font-semibold text-white mb-2">
            {t('printers.ams.feedTitle', { slot: slotLabel })}
          </h3>
          <p className="text-bambu-gray text-sm">{t('printers.ams.feedPrompt')}</p>

          <div className="grid grid-cols-2 gap-3 mt-4">
            {options.map(({ extruderId, label, taken }) => {
              const isSelected = selected === extruderId;
              return (
                <button
                  key={extruderId}
                  type="button"
                  onClick={() => setSelected(extruderId)}
                  disabled={taken || isLoading}
                  title={taken ? t('printers.ams.feedAlreadyLoaded') : undefined}
                  className={`p-3 rounded-lg border text-sm transition-colors ${
                    taken
                      ? 'border-transparent bg-bambu-dark text-bambu-gray/50 cursor-not-allowed'
                      : isSelected
                        ? 'border-bambu-green/50 bg-bambu-green/10 text-white'
                        : 'border-transparent bg-bambu-dark text-white hover:bg-bambu-dark-tertiary'
                  }`}
                >
                  <div className="font-medium">{label}</div>
                  {taken && (
                    <div className="text-xs mt-1">{t('printers.ams.feedAlreadyLoaded')}</div>
                  )}
                </button>
              );
            })}
          </div>

          <div className="flex gap-3 mt-6">
            <Button variant="secondary" onClick={onCancel} className="flex-1" disabled={isLoading}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => selected !== null && onConfirm(selected)}
              className="flex-1"
              disabled={selected === null || isLoading}
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  {t('common.loading')}
                </>
              ) : (
                t('common.confirm')
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
