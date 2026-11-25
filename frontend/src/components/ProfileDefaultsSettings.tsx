import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ChevronRight, Loader2, Search, Sparkles, Trash2, Wand2 } from 'lucide-react';
import { api } from '../api/client';
import type { ProfileDefault } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';

/**
 * Settings > Filament > Profile defaults (#1326): what new spools of each
 * product start with. A default is saved from a spool's Printers tab ("Save as
 * default"), which is also how one is changed: this card lists them, applies
 * one to the spools already in stock, and deletes one. There can be one per
 * product and colour, so each is a single line and its profiles fold out.
 */
export function ProfileDefaultsSettings() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState<ProfileDefault | null>(null);
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

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

  // One request per default, one after the other. The order does not matter: each
  // spool resolves its own colour's values, and nothing a spool has is overwritten.
  const applyAllMutation = useMutation({
    mutationFn: async (rows: ProfileDefault[]) => {
      let updated = 0;
      let failed = 0;
      for (const row of rows) {
        try {
          updated += (await api.applyProfileDefault(row.id, spoolmanMode)).updated;
        } catch {
          failed += 1;
        }
      }
      return { updated, failed };
    },
    onSuccess: async ({ updated, failed }) => {
      if (failed > 0) showToast(t('inventory.profileDefaults.applyAllFailed', { failed, updated }), 'error');
      else showToast(t('inventory.profileDefaults.applied', { count: updated }), 'success');
      await queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      await queryClient.invalidateQueries({ queryKey: ['spoolman-inventory-spools'] });
    },
  });
  const applying = applyMutation.isPending || applyAllMutation.isPending;

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
  const colourOf = (row: ProfileDefault) => row.color_name || t('inventory.profileDefaults.allColours');

  // A list that can hold one row per product and colour in stock: sorted, searchable,
  // one line each with the details folded away.
  const query = search.trim().toLowerCase();
  const rows = defaults
    .map((row) => ({ row, product: productOf(row), colour: colourOf(row) }))
    .filter(
      ({ row, product, colour }) =>
        !query ||
        `${product} ${colour} ${row.slicer_filament_name || row.slicer_filament || ''}`.toLowerCase().includes(query),
    )
    .sort((a, b) => a.product.localeCompare(b.product) || a.colour.localeCompare(b.colour));

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <Card id="card-profiledefaults">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-bambu-green" />
            {t('inventory.profileDefaults.title')}
            {defaults.length > 0 && <span className="text-sm font-normal text-bambu-gray">({defaults.length})</span>}
          </h2>
          {defaults.length > 0 && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => applyAllMutation.mutate(defaults)}
              // Not before the inventory mode is known: it picks the spools written to.
              disabled={applying || spoolmanSettings === undefined}
              title={t('inventory.profileDefaults.applyAllHelp')}
            >
              {applyAllMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
              {t('inventory.profileDefaults.applyAll')}
            </Button>
          )}
        </div>
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
          <>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
              <input
                type="text"
                className="w-full pl-10 pr-3 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
                placeholder={t('inventory.profileDefaults.search')}
                aria-label={t('common.search')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <ul className="max-h-[480px] overflow-y-auto border border-bambu-dark-tertiary rounded-lg divide-y divide-bambu-dark-tertiary">
              {rows.length === 0 ? (
                <li className="px-3 py-4 text-center text-sm text-bambu-gray">{t('inventory.profileDefaults.noMatch')}</li>
              ) : (
                rows.map(({ row, product, colour }) => {
                  const open = expanded.has(row.id);
                  const preset = row.slicer_filament_name || row.slicer_filament;
                  return (
                    <li key={row.id} className="text-sm">
                      <div className="flex items-center gap-2 px-2 py-1.5 hover:bg-bambu-dark">
                        <button
                          type="button"
                          onClick={() => toggle(row.id)}
                          aria-expanded={open}
                          aria-label={open ? t('common.collapse') : t('common.expand')}
                          className="flex min-w-0 flex-1 items-center gap-2 text-left"
                        >
                          <ChevronRight
                            className={`w-4 h-4 shrink-0 text-bambu-gray transition-transform ${open ? 'rotate-90' : ''}`}
                          />
                          <span className="truncate text-white">{product}</span>
                          <span className="shrink-0 text-xs text-bambu-gray">{colour}</span>
                          {preset && (
                            <span className="hidden min-w-0 truncate text-xs text-bambu-gray md:inline">· {preset}</span>
                          )}
                          <span className="ml-auto hidden shrink-0 gap-1 text-xs text-bambu-gray sm:flex">
                            {row.presets.length > 0 && (
                              <span className="rounded bg-bambu-dark-tertiary px-1.5">
                                {t('inventory.profileDefaults.presetsCount', { count: row.presets.length })}
                              </span>
                            )}
                            {row.k_profiles.length > 0 && (
                              <span className="rounded bg-bambu-dark-tertiary px-1.5">
                                {t('inventory.profileDefaults.kProfilesCount', { count: row.k_profiles.length })}
                              </span>
                            )}
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => applyMutation.mutate(row)}
                          // Not before the inventory mode is known: it picks the spools written to.
                          disabled={applying || spoolmanSettings === undefined}
                          title={`${t('inventory.profileDefaults.apply')}: ${t('inventory.profileDefaults.applyHelp')}`}
                          aria-label={t('inventory.profileDefaults.apply')}
                          className="shrink-0 rounded p-1.5 text-bambu-gray hover:bg-bambu-dark-tertiary hover:text-bambu-green disabled:opacity-50"
                        >
                          {applyMutation.isPending && applyMutation.variables?.id === row.id ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Wand2 className="w-4 h-4" />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => setDeleting(row)}
                          title={t('common.delete')}
                          aria-label={t('common.delete')}
                          className="shrink-0 rounded p-1.5 text-bambu-gray hover:bg-red-500/20 hover:text-red-400"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                      {open && (
                        <ul className="space-y-0.5 pb-2 pl-8 pr-3 text-xs text-bambu-gray">
                          {preset && (
                            <li>
                              {t('inventory.filamentPreset')}: {preset}
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
                      )}
                    </li>
                  );
                })
              )}
            </ul>
          </>
        )}
      </CardContent>
      {deleting && (
        <ConfirmModal
          title={t('inventory.profileDefaults.deleteTitle')}
          message={t('inventory.profileDefaults.deleteMessage', {
            product: `${productOf(deleting)} ${colourOf(deleting)}`,
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
