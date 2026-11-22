import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { SpoolLinkPicker } from './SpoolLinkPicker';
import { getSwatchStyle } from '../utils/colors';
import { linkChoices } from '../utils/rfidTagLink';
import type { UnknownSpoolPrompt } from '../hooks/useUnknownTagPrompt';

interface UnknownSpoolModalProps {
  prompt: UnknownSpoolPrompt | null;
  isPending: boolean;
  /** Built-in inventory: the tag can be linked to a spool already in it (#3658). */
  canLink?: boolean;
  onConfirm: () => void;
  onLink?: (spoolId: number) => void;
  onCancel: () => void;
}

export function UnknownSpoolModal({ prompt, isPending, canLink = false, onConfirm, onLink, onCancel }: UnknownSpoolModalProps) {
  const { t } = useTranslation();
  const linkable = canLink && !!onLink;
  const severalMatch = (prompt?.candidate_spool_ids.length ?? 0) > 0;
  // Several matching spools: which one it is, is the question, so the list
  // is open from the start. Otherwise linking is one option of two.
  const [showPicker, setShowPicker] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    setShowPicker(false);
    setSelectedId(null);
  }, [prompt]);

  const pickerOpen = linkable && (showPicker || severalMatch);
  const { data: spools, isLoading } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(true),
    enabled: pickerOpen,
  });
  const choices = useMemo(
    () =>
      linkChoices(spools, {
        material: prompt?.material,
        colorHex: prompt?.color_hex,
        candidateIds: prompt?.candidate_spool_ids,
      }),
    [spools, prompt],
  );

  useEffect(() => {
    if (!prompt) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isPending) onCancel();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [prompt, isPending, onCancel]);

  if (!prompt) return null;

  const location = `${prompt.printer_name} • ${prompt.ams_label} • ${prompt.slot_label}`;
  const swatchStyle = prompt.color_hex ? getSwatchStyle(prompt.color_hex) : undefined;
  const message = linkable && severalMatch
    ? t('inventory.rfidLink.severalMatch', { location })
    : t('inventory.unknownSpoolMessage', { location });

  let linkLabel = t('inventory.rfidLink.linkExisting');
  if (pickerOpen) linkLabel = selectedId ? t('inventory.rfidLink.linkTo', { id: selectedId }) : t('inventory.rfidLink.link');

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50" onClick={isPending ? undefined : onCancel}>
      <Card
        className={`w-full ${pickerOpen ? 'max-w-lg' : 'max-w-md'}`}
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={t('inventory.unknownSpoolTitle')}
      >
        <CardContent className="p-6">
          <div className="flex items-start gap-4">
            <div className="p-2 rounded-full bg-bambu-dark text-bambu-green">
              <AlertTriangle className="w-6 h-6" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-lg font-semibold text-white mb-2">{t('inventory.unknownSpoolTitle')}</h3>
              <p className="text-bambu-gray text-sm whitespace-pre-line">{message}</p>
              <div className="mt-4 space-y-4">
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
                {pickerOpen && (
                  <SpoolLinkPicker
                    choices={choices}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    material={prompt.material}
                    isLoading={isLoading}
                    disabled={isPending}
                  />
                )}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-3 mt-6">
            <Button variant="secondary" onClick={onCancel} className="flex-1" disabled={isPending}>
              {t('common.cancel')}
            </Button>
            <Button
              variant={pickerOpen ? 'secondary' : 'primary'}
              onClick={onConfirm}
              className="flex-1 whitespace-nowrap"
              disabled={isPending}
            >
              {isPending && !linkable ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('inventory.addToInventoryPending')}
                </>
              ) : pickerOpen ? (
                t('inventory.rfidLink.addAsNew')
              ) : (
                t('inventory.addToInventory')
              )}
            </Button>
            {linkable && (
              <Button
                onClick={() => {
                  if (!pickerOpen) setShowPicker(true);
                  else if (selectedId) onLink?.(selectedId);
                }}
                className="flex-1 whitespace-nowrap"
                disabled={isPending || (pickerOpen && !selectedId)}
              >
                {isPending && <Loader2 className="w-4 h-4 animate-spin" />}
                {linkLabel}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
