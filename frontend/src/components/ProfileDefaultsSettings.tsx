import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2, Sparkles, Trash2, Wand2 } from 'lucide-react';
import { api } from '../api/client';
import type { ProfileDefault } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { Button } from './Button';
import { Card, CardContent, CardHeader } from './Card';
import { ConfirmModal } from './ConfirmModal';

/**
 * Settings > Filament > Profile defaults (#1326): what new spools of each
 * product start with. A default is saved from a spool's Printers tab ("Save as
 * default"), which is also how one is changed: this card lists them, applies
 * one to the spools already in stock, and deletes one.
 */
export function ProfileDefaultsSettings() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState<ProfileDefault | null>(null);

  const { data: defaults = [], isLoading } = useQuery({
    queryKey: ['profile-defaults'],
    queryFn: api.getProfileDefaults,
  });
  const { data: printers = [] } = useQuery({ queryKey: ['printers'], queryFn: api.getPrinters });
  // Applying writes to the spools of the inventory in use.
  const { data: spoolmanSettings } = useQuery({
    queryKey: ['spoolman-settings'],
    queryFn: api.getSpoolmanSettings,
    staleTime: 5 * 60 * 1000,
  });
  const spoolmanMode = spoolmanSettings?.spoolman_enabled === 'true' && !!spoolmanSettings?.spoolman_url;

  const printerName = (id: number) => printers.find((p) => p.id === id)?.name ?? `#${id}`;

  const applyMutation = useMutation({
    mutationFn: (row: ProfileDefault) => api.applyProfileDefault(row.id, spoolmanMode),
    onSuccess: async ({ updated }) => {
      showToast(t('inventory.profileDefaults.applied', { count: updated }), 'success');
      await queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      await queryClient.invalidateQueries({ queryKey: ['spoolman-inventory-spools'] });
    },
    onError: () => showToast(t('inventory.profileDefaults.applyFailed'), 'error'),
  });

  const deleteMutation = useMutation({
    mutationFn: (row: ProfileDefault) => api.deleteProfileDefault(row.id),
    onSuccess: async () => {
      setDeleting(null);
      await queryClient.invalidateQueries({ queryKey: ['profile-defaults'] });
    },
    onError: () => {
      setDeleting(null);
      showToast(t('inventory.profileDefaults.deleteFailed'), 'error');
    },
  });

  const productOf = (row: ProfileDefault) =>
    [row.brand, row.material, row.subtype].filter((part) => part.trim()).join(' ');

  return (
    <Card id="card-profiledefaults">
      <CardHeader>
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-bambu-green" />
          {t('inventory.profileDefaults.title')}
        </h2>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-bambu-gray">{t('inventory.profileDefaults.description')}</p>
        {isLoading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="w-5 h-5 animate-spin text-bambu-gray" />
          </div>
        ) : defaults.length === 0 ? (
          <p className="text-sm text-bambu-gray italic">{t('inventory.profileDefaults.empty')}</p>
        ) : (
          <ul className="space-y-2">
            {defaults.map((row) => (
              <li key={row.id} className="p-3 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg">
                <div className="flex flex-wrap items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-white">
                      {productOf(row)}
                      <span className="ml-2 text-xs text-bambu-gray">
                        {row.color_name || t('inventory.profileDefaults.allColours')}
                      </span>
                    </p>
                    <ul className="mt-1 space-y-0.5 text-xs text-bambu-gray">
                      {row.slicer_filament && (
                        <li>
                          {t('inventory.filamentPreset')}: {row.slicer_filament_name || row.slicer_filament}
                        </li>
                      )}
                      {row.presets.map((p) => (
                        <li key={`${p.printer_model}:${p.nozzle_diameter}`}>
                          {p.printer_model}
                          {p.nozzle_diameter ? ` ${p.nozzle_diameter} mm` : ''}: {p.slicer_filament_name || p.slicer_filament}
                        </li>
                      ))}
                      {row.k_profiles.map((k) => (
                        <li key={`${k.printer_id}:${k.extruder}:${k.nozzle_diameter}`}>
                          {`${printerName(k.printer_id)} ${k.nozzle_diameter ?? '0.4'} mm: ${k.name || '-'} K=${k.k_value.toFixed(3)}`}
                          {(k.extruder ?? 0) > 0 ? ` (${t('inventory.leftNozzle')})` : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="flex gap-2 shrink-0">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => applyMutation.mutate(row)}
                      // Not before the inventory mode is known: it picks the spools written to.
                      disabled={applyMutation.isPending || spoolmanSettings === undefined}
                      title={t('inventory.profileDefaults.applyHelp')}
                    >
                      {applyMutation.isPending && applyMutation.variables?.id === row.id ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Wand2 className="w-4 h-4" />
                      )}
                      {t('inventory.profileDefaults.apply')}
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setDeleting(row)}
                      aria-label={t('common.delete')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
      {deleting && (
        <ConfirmModal
          title={t('inventory.profileDefaults.deleteTitle')}
          message={t('inventory.profileDefaults.deleteMessage', {
            product: `${productOf(deleting)} ${deleting.color_name || t('inventory.profileDefaults.allColours')}`,
          })}
          confirmText={t('common.delete')}
          variant="danger"
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleting)}
          onCancel={() => setDeleting(null)}
        />
      )}
    </Card>
  );
}
