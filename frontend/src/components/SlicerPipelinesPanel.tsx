import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Check, Edit2, Loader2, Printer as PrinterIcon, Search, Trash2, Workflow, X } from 'lucide-react';
import {
  api,
  type PipelineRun,
  type PresetRef,
  type PresetSource,
  type Printer as PrinterType,
  type SlicerPipeline,
  type UnifiedPresetsResponse,
} from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { useToast } from '../contexts/ToastContext';

// Resolve a PresetRef back to its pretty name via the unified-presets listing.
// Returns null when the ref no longer points at a known preset — render a
// "deleted" badge in that case so users can see what to fix.
function resolveName(presets: UnifiedPresetsResponse | undefined, slot: 'printer' | 'process' | 'filament', ref: PresetRef): string | null {
  if (!presets) return null;
  const list = presets[ref.source]?.[slot] ?? [];
  const hit = list.find((p) => p.id === ref.id);
  return hit ? hit.name : null;
}

const SOURCE_LABEL: Record<PresetSource, string> = {
  orca_cloud: 'Orca Cloud',
  cloud: 'Bambu Cloud',
  local: 'Imported',
  standard: 'Standard',
};

export function SlicerPipelinesPanel() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const { data: list, isLoading, error } = useQuery({
    queryKey: ['slicer-pipelines'],
    queryFn: () => api.listSlicerPipelines(),
  });

  // The unified presets endpoint is the source of pretty names for each
  // PresetRef. Same listing the SliceModal pulls — reused here to avoid a
  // second round-trip to the slicer registry.
  const { data: presets } = useQuery({
    queryKey: ['slicer-presets'],
    queryFn: () => api.getSlicerPresets(),
  });

  // Printers list for the target picker (PR B).
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      name,
      description,
      target_printer_id,
      target_kind,
      target_model_class,
      fanout_strategy,
    }: {
      id: number;
      name?: string;
      description?: string | null;
      target_printer_id?: number | null;
      target_kind?: 'specific_printer' | 'printer_class';
      target_model_class?: string | null;
      fanout_strategy?: 'max_parallel' | 'fill_one_first' | 'round_robin';
    }) =>
      api.updateSlicerPipeline(id, {
        name,
        description,
        target_printer_id,
        target_kind,
        target_model_class,
        fanout_strategy,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['slicer-pipelines'] });
      showToast(t('settings.pipelines.toast.saved', 'Pipeline saved'), 'success');
    },
    onError: (err: Error) => {
      showToast(err.message || t('settings.pipelines.toast.saveFailed', 'Save failed'), 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteSlicerPipeline(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['slicer-pipelines'] });
      showToast(t('settings.pipelines.toast.deleted', 'Pipeline deleted'), 'success');
    },
    onError: (err: Error) => {
      showToast(err.message || t('settings.pipelines.toast.deleteFailed', 'Delete failed'), 'error');
    },
  });

  // Panel-level search + filter (#1425 PR C polish). Filters by pipeline name
  // (case-insensitive substring) and by target — the dropdown lists every
  // distinct target in use across the saved pipelines so operators can jump
  // straight to "show me everything for X1C #2" or "everything for the H2D
  // class". State is local — list is small enough that re-rendering on every
  // keystroke is fine.
  const [searchTerm, setSearchTerm] = useState('');
  // Encoded target filter value: '' = all, 'none' = no target set,
  // 'p:<printer_id>' = specific printer, 'c:<model_class>' = printer class.
  const [targetFilter, setTargetFilter] = useState<string>('');

  const allPipelines = useMemo(() => list?.pipelines ?? [], [list?.pipelines]);

  // Build the dropdown's options from the targets actually in use. Only
  // printers / classes that at least one pipeline points at appear — keeps
  // the dropdown short and meaningful for installs with many printers but
  // few pipelines.
  const targetOptions = useMemo(() => {
    const printerIds = new Set<number>();
    const classes = new Set<string>();
    let anyWithoutTarget = false;
    for (const p of allPipelines) {
      if (p.target_kind === 'printer_class' && p.target_model_class) {
        classes.add(p.target_model_class);
      } else if (p.target_printer_id) {
        printerIds.add(p.target_printer_id);
      } else {
        anyWithoutTarget = true;
      }
    }
    return {
      printers: (printers ?? []).filter((pr) => printerIds.has(pr.id)),
      classes: Array.from(classes).sort(),
      anyWithoutTarget,
    };
  }, [allPipelines, printers]);

  const pipelines = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    return allPipelines.filter((p) => {
      if (term && !p.name.toLowerCase().includes(term)) return false;
      if (targetFilter === 'none') {
        const hasTarget = p.target_kind === 'printer_class'
          ? !!p.target_model_class
          : p.target_printer_id !== null;
        if (hasTarget) return false;
      } else if (targetFilter.startsWith('p:')) {
        const wantId = parseInt(targetFilter.slice(2), 10);
        if (p.target_kind === 'printer_class' || p.target_printer_id !== wantId) return false;
      } else if (targetFilter.startsWith('c:')) {
        const wantClass = targetFilter.slice(2);
        if (p.target_kind !== 'printer_class' || p.target_model_class !== wantClass) return false;
      }
      return true;
    });
  }, [allPipelines, searchTerm, targetFilter]);

  return (
    <Card>
      <CardHeader>
        <h3 className="text-base font-semibold text-white flex items-center gap-2">
          <Workflow className="w-4 h-4 text-bambu-green" />
          {t('settings.pipelines.title', 'Slicer Pipelines')}
        </h3>
        <p className="text-xs text-bambu-gray mt-1">
          {t(
            'settings.pipelines.subtitle',
            'Reusable preset bundles (printer + process + filaments + bed type). Save one from the Slice dialog and apply it with a single click on the next file.',
          )}
        </p>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <div className="flex items-center gap-2 text-sm text-bambu-gray">
            <Loader2 className="w-4 h-4 animate-spin" />
            {t('settings.pipelines.loading', 'Loading pipelines…')}
          </div>
        )}
        {error && (
          <div className="text-sm text-red-700 dark:text-red-400">
            {t('settings.pipelines.loadError', 'Could not load pipelines.')}
          </div>
        )}
        {/* Search + target-type filter. Only render when there are pipelines
            to filter; the empty-state hint reads better without controls. */}
        {!isLoading && !error && allPipelines.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <div className="relative flex-1 min-w-[12rem]">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-bambu-gray pointer-events-none" />
              <input
                type="search"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder={t('settings.pipelines.searchPlaceholder', 'Search pipelines…')}
                aria-label={t('settings.pipelines.searchPlaceholder', 'Search pipelines…')}
                className="w-full pl-7 pr-2 py-1 text-xs bg-bambu-dark border border-bambu-dark-tertiary rounded text-white"
              />
            </div>
            <select
              value={targetFilter}
              onChange={(e) => setTargetFilter(e.target.value)}
              aria-label={t('settings.pipelines.filterTarget', 'Filter by target')}
              className="text-xs px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white"
            >
              <option value="">
                {t('settings.pipelines.filter.all', 'All targets')}
              </option>
              {targetOptions.printers.length > 0 && (
                <optgroup label={t('settings.pipelines.field.targetKindSpecific', 'Specific printer')}>
                  {targetOptions.printers.map((p) => (
                    <option key={`p-${p.id}`} value={`p:${p.id}`}>
                      {p.name}
                    </option>
                  ))}
                </optgroup>
              )}
              {targetOptions.classes.length > 0 && (
                <optgroup label={t('settings.pipelines.field.targetKindClass', 'Printer class')}>
                  {targetOptions.classes.map((c) => (
                    <option key={`c-${c}`} value={`c:${c}`}>
                      {t('library.runWithPipeline.classTarget', 'Any {{model}}', { model: c })}
                    </option>
                  ))}
                </optgroup>
              )}
              {targetOptions.anyWithoutTarget && (
                <option value="none">
                  {t('settings.pipelines.filter.noTarget', 'No target set')}
                </option>
              )}
            </select>
            {(searchTerm || targetFilter) && (
              <span className="text-xs text-bambu-gray">
                {t('settings.pipelines.filter.count', '{{shown}} / {{total}}', {
                  shown: pipelines.length,
                  total: allPipelines.length,
                })}
              </span>
            )}
          </div>
        )}
        {!isLoading && !error && allPipelines.length === 0 && (
          <div className="text-sm text-bambu-gray space-y-2">
            <p>{t('settings.pipelines.empty.title', 'No pipelines yet.')}</p>
            <p>
              {t(
                'settings.pipelines.empty.howto',
                'Open the Slice dialog for any file, pick your printer / process / filaments / bed type, then click "Save as pipeline". Your saved pipelines will appear here.',
              )}
            </p>
          </div>
        )}
        {!isLoading && !error && allPipelines.length > 0 && pipelines.length === 0 && (
          <p className="text-sm text-bambu-gray">
            {t('settings.pipelines.filter.noMatches', 'No pipelines match the current filters.')}
          </p>
        )}
        {!isLoading && !error && pipelines.length > 0 && (
          <div className="space-y-2">
            {pipelines.map((p) => (
              <PipelineRow
                key={p.id}
                pipeline={p}
                presets={presets}
                printers={printers ?? []}
                onSave={(payload) => updateMutation.mutate({ id: p.id, ...payload })}
                onDelete={() => {
                  if (confirm(t('settings.pipelines.confirmDelete', 'Delete this pipeline? This cannot be undone.'))) {
                    deleteMutation.mutate(p.id);
                  }
                }}
                saving={updateMutation.isPending}
                deleting={deleteMutation.isPending}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PipelineRow({
  pipeline,
  presets,
  printers,
  onSave,
  onDelete,
  saving,
  deleting,
}: {
  pipeline: SlicerPipeline;
  presets: UnifiedPresetsResponse | undefined;
  printers: PrinterType[];
  onSave: (payload: {
    name?: string;
    description?: string | null;
    target_printer_id?: number | null;
    target_kind?: 'specific_printer' | 'printer_class';
    target_model_class?: string | null;
    fanout_strategy?: 'max_parallel' | 'fill_one_first' | 'round_robin';
  }) => void;
  onDelete: () => void;
  saving: boolean;
  deleting: boolean;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(pipeline.name);
  const [draftDescription, setDraftDescription] = useState(pipeline.description ?? '');
  const [draftTargetPrinterId, setDraftTargetPrinterId] = useState<number | null>(
    pipeline.target_printer_id,
  );
  // PR C: target kind, model class, and fanout strategy.
  const [draftTargetKind, setDraftTargetKind] = useState<'specific_printer' | 'printer_class'>(
    pipeline.target_kind === 'printer_class' ? 'printer_class' : 'specific_printer',
  );
  const [draftTargetModelClass, setDraftTargetModelClass] = useState<string>(
    pipeline.target_model_class ?? '',
  );
  const [draftFanout, setDraftFanout] = useState<'max_parallel' | 'fill_one_first' | 'round_robin'>(
    pipeline.fanout_strategy ?? 'max_parallel',
  );
  // Installed model classes — derived from the loaded printers list so the
  // dropdown only offers models the user actually has. Same data the row
  // header uses, no second fetch.
  const installedModels = Array.from(
    new Set(printers.map((p) => p.model).filter((m): m is string => !!m)),
  ).sort();

  // Recent runs for the inline last-run summary. ``enabled: editing === false``
  // avoids re-querying every keystroke while the editor is open.
  const { data: runsList } = useQuery({
    queryKey: ['pipeline-runs', pipeline.id],
    queryFn: () => api.listPipelineRuns(pipeline.id, 1),
    enabled: !editing,
    refetchInterval: 15_000,
  });
  const lastRun: PipelineRun | undefined = runsList?.runs?.[0];

  const printerName = resolveName(presets, 'printer', pipeline.printer_preset);
  const processName = resolveName(presets, 'process', pipeline.process_preset);
  const filamentResolutions = pipeline.filament_presets.map((f) => resolveName(presets, 'filament', f));
  // Collapse identical filaments into a single "All N slots" line — most
  // production pipelines load the same filament into every AMS slot, and
  // listing the same line three times is just noise. Compares raw preset
  // refs (source + id) rather than resolved names so the dedup is correct
  // even when ``presets`` hasn't loaded yet.
  const filamentsAllIdentical =
    pipeline.filament_presets.length > 1 &&
    pipeline.filament_presets.every(
      (f) =>
        f.source === pipeline.filament_presets[0].source &&
        f.id === pipeline.filament_presets[0].id,
    );

  const hasStaleRef =
    presets !== undefined &&
    (printerName === null || processName === null || filamentResolutions.some((n) => n === null));
  const targetPrinter = pipeline.target_printer_id
    ? printers.find((p) => p.id === pipeline.target_printer_id)
    : undefined;
  const isClassTargeting = pipeline.target_kind === 'printer_class';
  const needsTarget = isClassTargeting
    ? !pipeline.target_model_class
    : pipeline.target_printer_id === null;

  const handleSave = () => {
    const trimmedName = draftName.trim();
    if (!trimmedName) return;
    onSave({
      name: trimmedName,
      description: draftDescription.trim() || null,
      target_kind: draftTargetKind,
      // Backend treats 0 as "clear"; null in TS maps to that intent.
      target_printer_id:
        draftTargetKind === 'specific_printer' ? (draftTargetPrinterId ?? 0) : 0,
      target_model_class:
        draftTargetKind === 'printer_class' ? (draftTargetModelClass || null) : null,
      fanout_strategy: draftFanout,
    });
    setEditing(false);
  };

  const handleCancel = () => {
    setDraftName(pipeline.name);
    setDraftDescription(pipeline.description ?? '');
    setDraftTargetPrinterId(pipeline.target_printer_id);
    setDraftTargetKind(pipeline.target_kind === 'printer_class' ? 'printer_class' : 'specific_printer');
    setDraftTargetModelClass(pipeline.target_model_class ?? '');
    setDraftFanout(pipeline.fanout_strategy ?? 'max_parallel');
    setEditing(false);
  };

  return (
    <div className="rounded-md border border-bambu-dark-tertiary bg-bambu-dark/40 px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {editing ? (
            <div className="space-y-2">
              <input
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                aria-label={t('settings.pipelines.field.name', 'Pipeline name')}
                placeholder={t('settings.pipelines.field.name', 'Pipeline name')}
                className="w-full px-2 py-1 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded text-white"
              />
              <textarea
                value={draftDescription}
                onChange={(e) => setDraftDescription(e.target.value)}
                aria-label={t('settings.pipelines.field.description', 'Description')}
                placeholder={t('settings.pipelines.field.description', 'Description')}
                rows={2}
                className="w-full px-2 py-1 text-xs bg-bambu-dark border border-bambu-dark-tertiary rounded text-white"
              />
              {/* PR C — target-kind radio (Specific printer / Printer class)
                  drives whether the printer dropdown or the class picker is
                  active. Both fields are kept on state so toggling back and
                  forth doesn't lose the user's previous pick. */}
              <div>
                <label className="text-xs text-bambu-gray block mb-1">
                  {t('settings.pipelines.field.targetKind', 'Target type')}
                </label>
                <div className="flex gap-3 text-xs">
                  <label className="flex items-center gap-1 text-white">
                    <input
                      type="radio"
                      name={`target-kind-${pipeline.id}`}
                      value="specific_printer"
                      checked={draftTargetKind === 'specific_printer'}
                      onChange={() => setDraftTargetKind('specific_printer')}
                      aria-label={t('settings.pipelines.field.targetKindSpecific', 'Specific printer')}
                    />
                    {t('settings.pipelines.field.targetKindSpecific', 'Specific printer')}
                  </label>
                  <label className="flex items-center gap-1 text-white">
                    <input
                      type="radio"
                      name={`target-kind-${pipeline.id}`}
                      value="printer_class"
                      checked={draftTargetKind === 'printer_class'}
                      onChange={() => setDraftTargetKind('printer_class')}
                      aria-label={t('settings.pipelines.field.targetKindClass', 'Printer class')}
                    />
                    {t('settings.pipelines.field.targetKindClass', 'Printer class')}
                  </label>
                </div>
              </div>

              {draftTargetKind === 'specific_printer' ? (
                <div>
                  <label className="text-xs text-bambu-gray block mb-1">
                    {t('settings.pipelines.field.targetPrinter', 'Target printer')}
                  </label>
                  <select
                    value={draftTargetPrinterId ?? ''}
                    onChange={(e) =>
                      setDraftTargetPrinterId(e.target.value ? parseInt(e.target.value, 10) : null)
                    }
                    aria-label={t('settings.pipelines.field.targetPrinter', 'Target printer')}
                    className="w-full px-2 py-1 text-xs bg-bambu-dark border border-bambu-dark-tertiary rounded text-white"
                  >
                    <option value="">
                      {t('settings.pipelines.field.noTarget', '— No target —')}
                    </option>
                    {printers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <div className="space-y-2">
                  <div>
                    <label className="text-xs text-bambu-gray block mb-1">
                      {t('settings.pipelines.field.targetModelClass', 'Printer model')}
                    </label>
                    <select
                      value={draftTargetModelClass}
                      onChange={(e) => setDraftTargetModelClass(e.target.value)}
                      aria-label={t('settings.pipelines.field.targetModelClass', 'Printer model')}
                      className="w-full px-2 py-1 text-xs bg-bambu-dark border border-bambu-dark-tertiary rounded text-white"
                    >
                      <option value="">
                        {t('settings.pipelines.field.noTarget', '— No target —')}
                      </option>
                      {installedModels.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs text-bambu-gray block mb-1">
                      {t('settings.pipelines.field.fanoutStrategy', 'Fanout strategy')}
                    </label>
                    <select
                      value={draftFanout}
                      onChange={(e) =>
                        setDraftFanout(e.target.value as 'max_parallel' | 'fill_one_first' | 'round_robin')
                      }
                      aria-label={t('settings.pipelines.field.fanoutStrategy', 'Fanout strategy')}
                      className="w-full px-2 py-1 text-xs bg-bambu-dark border border-bambu-dark-tertiary rounded text-white"
                    >
                      <option value="max_parallel">
                        {t('settings.pipelines.field.fanout.max_parallel', 'Max parallel — distribute across any idle matching printer')}
                      </option>
                      <option value="round_robin">
                        {t('settings.pipelines.field.fanout.round_robin', 'Round robin — cycle through eligible printers')}
                      </option>
                      <option value="fill_one_first">
                        {t('settings.pipelines.field.fanout.fill_one_first', 'Fill one first — pin all copies to one printer')}
                      </option>
                    </select>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <>
              {/* Header: name + inline target chip (PR C polish). The target
                  context — specific printer name OR class+strategy — is the
                  thing the operator most needs to read at a glance, so it
                  rides up here next to the title instead of buried below. */}
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <h4 className="text-sm font-medium text-white truncate">{pipeline.name}</h4>
                <span
                  className={`text-xs px-1.5 py-0.5 rounded inline-flex items-center gap-1 ${
                    needsTarget
                      ? 'bg-amber-100 dark:bg-amber-500/15 text-amber-700 dark:text-amber-400'
                      : 'bg-bambu-dark-tertiary text-bambu-gray'
                  }`}
                >
                  <PrinterIcon className="w-3 h-3" />
                  {needsTarget ? (
                    t('settings.pipelines.noTargetHint', 'Set a target printer to run this')
                  ) : isClassTargeting ? (
                    <>
                      {t('library.runWithPipeline.classTarget', 'Any {{model}}', {
                        model: pipeline.target_model_class,
                      })}
                      {pipeline.fanout_strategy && (
                        <span className="text-bambu-gray/60">
                          {' · '}
                          {t(
                            `settings.pipelines.field.fanoutShort.${pipeline.fanout_strategy}`,
                            pipeline.fanout_strategy,
                          )}
                        </span>
                      )}
                    </>
                  ) : (
                    targetPrinter?.name ?? ''
                  )}
                </span>
              </div>
              {pipeline.description && (
                <p className="text-xs text-bambu-gray mt-0.5">{pipeline.description}</p>
              )}
            </>
          )}
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {editing ? (
            <>
              <button
                onClick={handleSave}
                disabled={saving || !draftName.trim()}
                aria-label={t('settings.pipelines.action.save', 'Save')}
                className="p-1.5 text-bambu-green hover:bg-bambu-dark-tertiary rounded disabled:opacity-50"
              >
                <Check className="w-4 h-4" />
              </button>
              <button
                onClick={handleCancel}
                aria-label={t('settings.pipelines.action.cancel', 'Cancel')}
                className="p-1.5 text-bambu-gray hover:bg-bambu-dark-tertiary rounded"
              >
                <X className="w-4 h-4" />
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => setEditing(true)}
                aria-label={t('settings.pipelines.action.rename', 'Rename')}
                className="p-1.5 text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary rounded"
              >
                <Edit2 className="w-4 h-4" />
              </button>
              <button
                onClick={onDelete}
                disabled={deleting}
                aria-label={t('settings.pipelines.action.delete', 'Delete')}
                className="p-1.5 text-bambu-gray hover:text-red-600 dark:hover:text-red-400 hover:bg-bambu-dark-tertiary rounded disabled:opacity-50"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </>
          )}
        </div>
      </div>

      {!editing && (
        <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-2 text-xs">
          {/* Profiles group — printer / process / bed. These travel together
              because they describe the slicer profile bundle that produces a
              single gcode. The full preset name (including the BambuStudio
              ``@BBL <model>`` suffix) is shown verbatim so the user can match
              it 1:1 against what they see in the slicer. */}
          <div className="space-y-0.5">
            <div className="text-[10px] uppercase tracking-wide text-bambu-gray/60">
              {t('settings.pipelines.group.profiles', 'Profiles')}
            </div>
            <PresetLine
              label={t('settings.pipelines.slot.printer', 'Printer')}
              ref={pipeline.printer_preset}
              name={printerName}
            />
            <PresetLine
              label={t('settings.pipelines.slot.process', 'Process')}
              ref={pipeline.process_preset}
              name={processName}
            />
            {pipeline.bed_type && (
              <div className="text-bambu-gray">
                <span className="font-medium text-bambu-gray/80">
                  {t('settings.pipelines.slot.bed', 'Bed')}:
                </span>{' '}
                <span className="text-white">{pipeline.bed_type}</span>
              </div>
            )}
          </div>
          {/* Filaments group — one per AMS slot. When every slot is the same
              filament (the common single-color production-batch case) we
              collapse them into a single ``All 4 slots: PLA Basic`` line. */}
          <div className="space-y-0.5">
            <div className="text-[10px] uppercase tracking-wide text-bambu-gray/60">
              {t('settings.pipelines.group.filaments', 'Filaments')}
              {pipeline.filament_presets.length > 1 && (
                <span className="text-bambu-gray/60 normal-case ml-1">
                  ({pipeline.filament_presets.length})
                </span>
              )}
            </div>
            {filamentsAllIdentical ? (
              <PresetLine
                label={t('settings.pipelines.slot.filamentAll', 'All {{n}} slots', {
                  n: pipeline.filament_presets.length,
                })}
                ref={pipeline.filament_presets[0]}
                name={filamentResolutions[0]}
              />
            ) : (
              pipeline.filament_presets.map((f, i) => (
                <PresetLine
                  key={i}
                  label={
                    pipeline.filament_presets.length > 1
                      ? t('settings.pipelines.slot.filamentN', 'Filament {{n}}', { n: i + 1 })
                      : t('settings.pipelines.slot.filament', 'Filament')
                  }
                  ref={f}
                  name={filamentResolutions[i]}
                />
              ))
            )}
          </div>
        </div>
      )}

      {!editing && lastRun && (
        <div className="mt-1.5 text-xs text-bambu-gray flex items-center gap-1">
          <span className="font-medium text-bambu-gray/80">
            {t('settings.pipelines.runs.lastRun', 'Last run')}:
          </span>{' '}
          <RunStatusBadge status={lastRun.status} />
          {lastRun.created_at && (
            <span className="text-bambu-gray/60">
              · {new Date(lastRun.created_at).toLocaleString()}
            </span>
          )}
        </div>
      )}

      {needsTarget && !editing && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="w-3.5 h-3.5" />
          {t(
            'settings.pipelines.noTargetWarning',
            'Set a target printer before running this pipeline.',
          )}
        </div>
      )}

      {hasStaleRef && !editing && (
        <div className="mt-2 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="w-3.5 h-3.5" />
          {t(
            'settings.pipelines.staleWarning',
            'One or more referenced presets no longer exist. Re-save this pipeline from the Slice dialog to fix.',
          )}
        </div>
      )}
    </div>
  );
}

function RunStatusBadge({ status }: { status: PipelineRun['status'] }) {
  const { t } = useTranslation();
  const colourClass: Record<PipelineRun['status'], string> = {
    queued: 'text-bambu-gray',
    slicing: 'text-blue-700 dark:text-blue-400',
    dispatching: 'text-blue-700 dark:text-blue-400',
    in_progress: 'text-bambu-green',
    completed: 'text-bambu-green',
    failed: 'text-red-700 dark:text-red-400',
    partial_failure: 'text-amber-700 dark:text-amber-400',
    cancelled: 'text-bambu-gray',
  };
  return (
    <span className={colourClass[status]}>
      {t(`settings.pipelines.runs.status.${status}`, status)}
    </span>
  );
}

function PresetLine({
  label,
  ref,
  name,
}: {
  label: string;
  ref: PresetRef;
  name: string | null;
}) {
  return (
    <div className="text-bambu-gray truncate">
      <span className="font-medium text-bambu-gray/80">{label}:</span>{' '}
      {name ? (
        <span className="text-white">{name}</span>
      ) : (
        <span className="text-amber-700 dark:text-amber-400">[{SOURCE_LABEL[ref.source]} #{ref.id}]</span>
      )}
    </div>
  );
}
