import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  Filter,
  Loader2,
  Printer as PrinterIcon,
  RefreshCw,
  RotateCcw,
  Trash2,
  Workflow,
  X,
} from 'lucide-react';
import { api, type PipelineRun, type Printer, type SlicerPipeline } from '../api/client';
import { useToast } from '../contexts/ToastContext';

type DropdownOption = { value: string; label: string; group?: string };

const STATUSES = [
  '',
  'queued',
  'slicing',
  'dispatching',
  'in_progress',
  'completed',
  'partial_failure',
  'failed',
  'cancelled',
] as const;

const PAGE_LIMIT = 25;

// Dashboard for Slicer Pipeline runs (#1425 PR C).
// Renders the content of the Pipelines tab on the Print Queue page. Lists
// every run across every pipeline with status + pipeline filters and
// pagination; each row expands to show per-copy status; in-flight runs get a
// Cancel button, partial-failure runs get a Retry-failed button. Designed to
// embed inside QueuePage's tab strip — no page wrapper, no top-level title
// (the tab strip provides both).
export function PipelineRunsView() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [statusFilter, setStatusFilter] = useState<string>('');
  const [pipelineFilter, setPipelineFilter] = useState<number | null>(null);
  // Target filter — same encoded shape as SlicerPipelinesPanel: '' = all,
  // 'p:<printer_id>' = specific printer, 'c:<model_class>' = printer class.
  const [targetFilter, setTargetFilter] = useState<string>('');
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [showClearConfirm, setShowClearConfirm] = useState(false);

  const { data: pipelines } = useQuery({
    queryKey: ['slicer-pipelines'],
    queryFn: () => api.listSlicerPipelines(),
  });
  // Used to resolve target_printer_id → printer name on each row so
  // specific-printer runs show "H2D #1" instead of just an id.
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
  });
  const printersById: Record<number, Printer> = (printers ?? []).reduce(
    (acc, p) => {
      acc[p.id] = p;
      return acc;
    },
    {} as Record<number, Printer>,
  );

  const targetPrinterId = targetFilter.startsWith('p:')
    ? parseInt(targetFilter.slice(2), 10)
    : undefined;
  const targetModelClass = targetFilter.startsWith('c:')
    ? targetFilter.slice(2)
    : undefined;

  const { data: runsList, isLoading } = useQuery({
    queryKey: ['pipeline-runs-all', statusFilter, pipelineFilter, offset, targetFilter],
    queryFn: () =>
      api.listAllPipelineRuns({
        limit: PAGE_LIMIT,
        offset,
        pipelineId: pipelineFilter ?? undefined,
        status: statusFilter || undefined,
        targetPrinterId,
        targetModelClass,
      }),
    refetchInterval: 15_000,
  });

  const cancelMutation = useMutation({
    mutationFn: (runId: number) => api.cancelPipelineRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pipeline-runs-all'] });
      showToast(t('pipelineRuns.toast.cancelled', 'Run cancelled'), 'success');
    },
    onError: (err: Error) =>
      showToast(err.message || t('pipelineRuns.toast.cancelFailed', 'Cancel failed'), 'error'),
  });

  const retryMutation = useMutation({
    mutationFn: (runId: number) => api.retryFailedPipelineRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pipeline-runs-all'] });
      showToast(t('pipelineRuns.toast.retryStarted', 'Retry started'), 'success');
    },
    onError: (err: Error) =>
      showToast(err.message || t('pipelineRuns.toast.retryFailed', 'Retry failed'), 'error'),
  });

  const clearMutation = useMutation({
    mutationFn: () => api.clearTerminalPipelineRuns(),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['pipeline-runs-all'] });
      setShowClearConfirm(false);
      setOffset(0);
      showToast(
        t('pipelineRuns.toast.cleared', '{{n}} runs cleared', { n: result.deleted }),
        'success',
      );
    },
    onError: (err: Error) =>
      showToast(err.message || t('pipelineRuns.toast.clearFailed', 'Clear failed'), 'error'),
  });

  const runs = runsList?.runs ?? [];
  const total = runsList?.total ?? 0;
  const pipelinesById: Record<number, SlicerPipeline> = (pipelines?.pipelines ?? []).reduce(
    (acc, p) => {
      acc[p.id] = p;
      return acc;
    },
    {} as Record<number, SlicerPipeline>,
  );

  const toggle = (runId: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
  };

  const hasFilter = !!(statusFilter || pipelineFilter !== null || targetFilter);

  // Build the target dropdown's options from the pipelines actually in use.
  // Only printers / classes that at least one saved pipeline points at appear
  // — keeps the dropdown short and meaningful.
  const targetOptions = (() => {
    const printerIds = new Set<number>();
    const classes = new Set<string>();
    for (const p of pipelines?.pipelines ?? []) {
      if (p.target_kind === 'printer_class' && p.target_model_class) {
        classes.add(p.target_model_class);
      } else if (p.target_printer_id) {
        printerIds.add(p.target_printer_id);
      }
    }
    return {
      printers: (printers ?? []).filter((pr) => printerIds.has(pr.id)),
      classes: Array.from(classes).sort(),
    };
  })();

  return (
    <div>
      {/* Filter row — bare dropdowns with placeholder labels and an inline
          refresh button. The previous version had ``Pipeline:`` / ``Status:``
          labels next to dropdowns that already declared the same thing, so the
          row read as repetitive. */}
      <div className="flex flex-wrap items-center gap-2 mb-4 text-sm">
        <button
          type="button"
          onClick={() => queryClient.invalidateQueries({ queryKey: ['pipeline-runs-all'] })}
          aria-label={t('common.refresh', 'Refresh')}
          title={t('common.refresh', 'Refresh')}
          className="p-1.5 text-bambu-gray hover:text-white border border-bambu-dark-tertiary rounded"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
        <FilterDropdown
          icon={<Workflow className="w-3.5 h-3.5 text-bambu-gray" />}
          ariaLabel={t('pipelineRuns.filter.pipeline', 'Pipeline')}
          value={pipelineFilter === null ? '' : String(pipelineFilter)}
          onChange={(v) => {
            setOffset(0);
            setPipelineFilter(v ? parseInt(v, 10) : null);
          }}
          options={[
            { value: '', label: t('pipelineRuns.filter.allPipelines', 'All pipelines') },
            ...((pipelines?.pipelines ?? []).map((p) => ({
              value: String(p.id),
              label: p.name,
            })) as DropdownOption[]),
          ]}
        />
        <FilterDropdown
          icon={<Filter className="w-3.5 h-3.5 text-bambu-gray" />}
          ariaLabel={t('pipelineRuns.filter.status', 'Status')}
          value={statusFilter}
          onChange={(v) => {
            setOffset(0);
            setStatusFilter(v);
          }}
          options={STATUSES.map((s) => ({
            value: s,
            label:
              s === ''
                ? t('pipelineRuns.filter.allStatus', 'All statuses')
                : t(`settings.pipelines.runs.status.${s}`, s),
          }))}
        />
        {/* Target filter: built from targets actually in use across saved
            pipelines so the dropdown stays short. Mirrors the SlicerPipelinesPanel
            target picker. */}
        {(targetOptions.printers.length > 0 || targetOptions.classes.length > 0) && (
          <FilterDropdown
            icon={<PrinterIcon className="w-3.5 h-3.5 text-bambu-gray" />}
            ariaLabel={t('pipelineRuns.filter.target', 'Target')}
            value={targetFilter}
            onChange={(v) => {
              setOffset(0);
              setTargetFilter(v);
            }}
            options={[
              { value: '', label: t('pipelineRuns.filter.allTargets', 'All targets') },
              ...targetOptions.printers.map((p) => ({
                value: `p:${p.id}`,
                label: p.name,
                group: t('settings.pipelines.field.targetKindSpecific', 'Specific printer'),
              })),
              ...targetOptions.classes.map((c) => ({
                value: `c:${c}`,
                label: t('library.runWithPipeline.classTarget', 'Any {{model}}', { model: c }),
                group: t('settings.pipelines.field.targetKindClass', 'Printer class'),
              })),
            ]}
          />
        )}
        {hasFilter && (
          <button
            type="button"
            onClick={() => {
              setStatusFilter('');
              setPipelineFilter(null);
              setTargetFilter('');
              setOffset(0);
            }}
            className="text-xs text-bambu-gray hover:text-white"
          >
            {t('pipelineRuns.filter.clear', 'Clear filters')}
          </button>
        )}
        <div className="ml-auto flex items-center gap-2 text-xs text-bambu-gray">
          {!isLoading && total > 0 && (
            <span>{t('pipelineRuns.totalCount', '{{n}} run', { n: total, count: total })}</span>
          )}
          {/* Clear logs — opens a confirmation modal; only enabled when there
              are terminal runs that the endpoint could actually delete. */}
          <button
            type="button"
            onClick={() => setShowClearConfirm(true)}
            disabled={total === 0}
            className="flex items-center gap-1 px-2 py-1 text-red-700 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Trash2 className="w-3 h-3" />
            {t('pipelineRuns.clearLog', 'Clear log')}
          </button>
        </div>
      </div>

      {/* Clear-log confirmation modal. Only deletes terminal runs (completed
          / failed / cancelled / partial_failure); in-flight runs are
          preserved so an active batch isn't accidentally torched. */}
      {showClearConfirm && (
        <div
          className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
          onClick={() => setShowClearConfirm(false)}
          role="dialog"
          aria-modal="true"
        >
          <div
            className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-2xl w-full max-w-md p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-white flex items-center gap-2">
              <Trash2 className="w-4 h-4 text-red-600 dark:text-red-400" />
              {t('pipelineRuns.clearConfirmTitle', 'Clear log?')}
            </h3>
            <p className="text-sm text-bambu-gray mt-2">
              {t(
                'pipelineRuns.clearConfirmBody',
                'Delete every completed, failed, cancelled, and partial-failure pipeline run? In-flight runs are kept. This cannot be undone.',
              )}
            </p>
            <div className="flex items-center justify-end gap-2 mt-4">
              <button
                type="button"
                onClick={() => setShowClearConfirm(false)}
                disabled={clearMutation.isPending}
                className="px-3 py-1.5 text-sm text-bambu-gray hover:text-white"
              >
                {t('common.cancel', 'Cancel')}
              </button>
              <button
                type="button"
                onClick={() => clearMutation.mutate()}
                disabled={clearMutation.isPending}
                className="px-3 py-1.5 text-sm bg-red-500 hover:bg-red-600 text-white rounded disabled:opacity-50 flex items-center gap-1"
              >
                {clearMutation.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                {t('pipelineRuns.clearConfirmAction', 'Clear')}
              </button>
            </div>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="flex items-center gap-2 text-bambu-gray">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t('pipelineRuns.loading', 'Loading…')}
        </div>
      )}
      {!isLoading && runs.length === 0 && (
        <p className="text-sm text-bambu-gray">
          {hasFilter
            ? t('pipelineRuns.filter.noMatches', 'No runs match the current filters.')
            : t('pipelineRuns.empty', 'No pipeline runs yet.')}
        </p>
      )}

      {!isLoading && runs.length > 0 && (
        <div className="space-y-1.5">
          {runs.map((run) => (
            <RunRow
              key={run.id}
              run={run}
              pipeline={run.pipeline_id ? pipelinesById[run.pipeline_id] : undefined}
              printersById={printersById}
              expanded={expanded.has(run.id)}
              onToggle={() => toggle(run.id)}
              onCancel={() => cancelMutation.mutate(run.id)}
              onRetry={() => retryMutation.mutate(run.id)}
              cancelling={cancelMutation.isPending}
              retrying={retryMutation.isPending}
            />
          ))}
        </div>
      )}

      {!isLoading && total > PAGE_LIMIT && (
        <div className="flex items-center justify-between mt-4 text-sm">
          <button
            type="button"
            onClick={() => setOffset(Math.max(0, offset - PAGE_LIMIT))}
            disabled={offset === 0}
            className="px-3 py-1.5 rounded border border-bambu-dark-tertiary disabled:opacity-50 text-bambu-gray hover:text-white"
          >
            {t('common.previous', 'Previous')}
          </button>
          <span className="text-bambu-gray text-xs">
            {t('pipelineRuns.pagination', '{{start}}–{{end}} of {{total}}', {
              start: offset + 1,
              end: Math.min(offset + PAGE_LIMIT, total),
              total,
            })}
          </span>
          <button
            type="button"
            onClick={() => setOffset(offset + PAGE_LIMIT)}
            disabled={offset + PAGE_LIMIT >= total}
            className="px-3 py-1.5 rounded border border-bambu-dark-tertiary disabled:opacity-50 text-bambu-gray hover:text-white"
          >
            {t('common.next', 'Next')}
          </button>
        </div>
      )}
    </div>
  );
}

function RunRow({
  run,
  pipeline,
  printersById,
  expanded,
  onToggle,
  onCancel,
  onRetry,
  cancelling,
  retrying,
}: {
  run: PipelineRun;
  pipeline: SlicerPipeline | undefined;
  printersById: Record<number, Printer>;
  expanded: boolean;
  onToggle: () => void;
  onCancel: () => void;
  onRetry: () => void;
  cancelling: boolean;
  retrying: boolean;
}) {
  const { t } = useTranslation();
  const inFlight = ['queued', 'slicing', 'dispatching', 'in_progress'].includes(run.status);
  const partial = run.status === 'partial_failure' || run.status === 'failed';
  const userCancelled = run.status === 'cancelled' && run.error_message === 'Cancelled by user';

  // Target chip — class targeting reads "Any X1C", specific-printer reads the
  // printer's actual name (resolved from the printersById map). The chip is
  // only rendered when we have a target to show; an unbound pipeline simply
  // omits it.
  const targetLabel = run.target_kind === 'printer_class' && run.target_model_class
    ? t('library.runWithPipeline.classTarget', 'Any {{model}}', { model: run.target_model_class })
    : run.target_printer_id && printersById[run.target_printer_id]
      ? printersById[run.target_printer_id].name
      : null;

  return (
    <div className="rounded-md border border-bambu-dark-tertiary bg-bambu-dark/40 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={onToggle}
          aria-label={expanded ? t('common.collapse', 'Collapse') : t('common.expand', 'Expand')}
          aria-expanded={expanded}
          className="text-bambu-gray hover:text-white flex-shrink-0"
        >
          {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>
        <div className="flex-1 min-w-0">
          {/* Top line: run number · pipeline name · status badge. The status
              chip is bigger and more saturated than the previous version so
              it actually pops at a glance — finding "what failed" was hard
              when all chips read the same washed-out grey. */}
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
            <span className="font-medium text-white">
              #{run.id}
            </span>
            <span className="text-white truncate">
              {pipeline?.name ?? run.pipeline_name ?? '—'}
            </span>
            <RunStatusChip status={run.status} />
            {targetLabel && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-bambu-dark-tertiary text-bambu-gray flex items-center gap-1">
                <PrinterIcon className="w-3 h-3" />
                {targetLabel}
              </span>
            )}
            {run.parent_run_id && (
              <span className="text-xs text-bambu-gray/70 italic">
                {t('pipelineRuns.retryOf', 'retry of #{{n}}', { n: run.parent_run_id })}
              </span>
            )}
          </div>
          {/* Second line: source filename — give it its own row so long
              titles don't crowd the metadata. */}
          {run.source_filename && (
            <div className="mt-0.5 flex items-center gap-1.5 text-xs text-bambu-gray min-w-0">
              <FileText className="w-3 h-3 flex-shrink-0" />
              <span className="truncate" title={run.source_filename}>
                {run.source_filename}
              </span>
            </div>
          )}
          {/* Third line: timestamp + roll-up counts. Greyed out so the eye
              jumps to the title + chip first. */}
          <div className="mt-0.5 text-xs text-bambu-gray/70 flex flex-wrap items-center gap-x-2">
            <span>{new Date(run.created_at).toLocaleString()}</span>
            {run.copies > 1 && (
              <>
                <span className="text-bambu-gray/40">·</span>
                <span>{t('pipelineRuns.copies', '{{n}} copies', { n: run.copies })}</span>
              </>
            )}
            {(run.copies_completed > 0 || run.copies_failed > 0 || run.copies_cancelled > 0) && (
              <>
                <span className="text-bambu-gray/40">·</span>
                <span>
                  <span className="text-bambu-green">{run.copies_completed}</span>
                  /{run.copies}
                </span>
                {run.copies_failed > 0 && (
                  <>
                    <span className="text-bambu-gray/40">·</span>
                    <span className="text-red-700 dark:text-red-400">
                      {t('pipelineRuns.failedCount', '{{n}} failed', { n: run.copies_failed })}
                    </span>
                  </>
                )}
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {inFlight && (
            <button
              type="button"
              onClick={onCancel}
              disabled={cancelling}
              aria-label={t('common.cancel', 'Cancel')}
              className="text-xs px-2 py-1 text-red-700 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 rounded disabled:opacity-50 flex items-center gap-1"
            >
              <X className="w-3 h-3" />
              {t('common.cancel', 'Cancel')}
            </button>
          )}
          {partial && (
            <button
              type="button"
              onClick={onRetry}
              disabled={retrying}
              aria-label={t('pipelineRuns.retryFailed', 'Retry failed')}
              className="text-xs px-2 py-1 text-bambu-green hover:bg-bambu-green/10 rounded disabled:opacity-50 flex items-center gap-1"
            >
              <RotateCcw className="w-3 h-3" />
              {t('pipelineRuns.retryFailed', 'Retry failed')}
            </button>
          )}
        </div>
      </div>
      {expanded && (
        <div className="border-t border-bambu-dark-tertiary px-3 py-2 bg-bambu-dark/30">
          <div className="space-y-1.5">
            {run.jobs.map((job) => (
              <div key={job.id} className="flex items-center gap-2 text-xs">
                <span className="text-bambu-gray/60 w-14 flex-shrink-0">
                  {t('pipelineRuns.copyN', 'Copy {{n}}', { n: job.copy_index + 1 })}
                </span>
                <JobStatusChip status={job.status} />
                {job.assigned_printer_name && (
                  <span className="text-bambu-gray flex items-center gap-1 truncate">
                    <PrinterIcon className="w-3 h-3 text-bambu-gray/60" />
                    <span className="text-white truncate">{job.assigned_printer_name}</span>
                  </span>
                )}
                {job.error_message && (
                  <span className="text-red-700 dark:text-red-400 truncate" title={job.error_message}>
                    {job.error_message}
                  </span>
                )}
              </div>
            ))}
          </div>
          {run.error_message && !userCancelled && (
            <div className="text-xs text-red-700 dark:text-red-400 mt-2 pt-2 border-t border-bambu-dark-tertiary">
              {run.error_message}
            </div>
          )}
          {userCancelled && (
            <div className="text-xs text-bambu-gray/70 mt-2 pt-2 border-t border-bambu-dark-tertiary italic">
              {t('pipelineRuns.cancelledByUser', 'Cancelled by user')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// High-contrast badges on solid Tailwind palette colours. The previous
// version used ``bg-bambu-gray/25 text-bambu-gray`` etc. — same hue for
// background AND text — which made every chip look washed-out grey
// regardless of state. These use saturated 700-tier backgrounds with bright
// 100-tier text, so each state reads at a glance.
function RunStatusChip({ status }: { status: PipelineRun['status'] }) {
  const { t } = useTranslation();
  const colours: Record<PipelineRun['status'], string> = {
    queued: 'bg-slate-700 text-slate-200',
    slicing: 'bg-sky-700 text-sky-100',
    dispatching: 'bg-sky-700 text-sky-100',
    in_progress: 'bg-emerald-700 text-emerald-100',
    completed: 'bg-emerald-700 text-emerald-100',
    failed: 'bg-red-700 text-red-100',
    partial_failure: 'bg-amber-700 text-amber-100',
    cancelled: 'bg-rose-900 text-rose-200',
  };
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase font-semibold tracking-wide whitespace-nowrap ${colours[status]}`}>
      {t(`settings.pipelines.runs.status.${status}`, status)}
    </span>
  );
}

// Custom dropdown — bambu-themed replacement for the native browser
// `<select>` element. Same value/onChange contract; supports optional
// `group` per option to render section headers (replaces <optgroup>).
// Closes on outside click and Escape.
function FilterDropdown({
  value,
  onChange,
  options,
  icon,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  options: DropdownOption[];
  icon: ReactNode;
  ariaLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleMouseDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  const selected = options.find((o) => o.value === value);

  // Group consecutive options that share the same `group` so the menu can
  // render one header per group (mirrors the <optgroup> shape).
  const grouped: { group: string | undefined; options: DropdownOption[] }[] = [];
  for (const opt of options) {
    const last = grouped[grouped.length - 1];
    if (last && last.group === opt.group) last.options.push(opt);
    else grouped.push({ group: opt.group, options: [opt] });
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-1.5 px-2 py-1 border border-bambu-dark-tertiary rounded bg-bambu-dark/40 text-xs text-white hover:border-bambu-gray/60 focus:outline-none focus:ring-1 focus:ring-bambu-green/40"
      >
        {icon}
        <span className="truncate max-w-[14rem]">{selected?.label ?? ''}</span>
        <ChevronDown
          className={`w-3 h-3 text-bambu-gray transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open && (
        <div
          role="listbox"
          className="absolute left-0 top-full mt-1 z-30 min-w-full max-h-72 overflow-auto rounded border border-bambu-dark-tertiary bg-bambu-dark-secondary shadow-xl py-1"
        >
          {grouped.map((g, gi) => (
            <div key={gi}>
              {g.group && (
                <div className="px-2 pt-1.5 pb-0.5 text-[10px] uppercase tracking-wider text-bambu-gray/60">
                  {g.group}
                </div>
              )}
              {g.options.map((opt) => {
                const isSelected = opt.value === value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => {
                      onChange(opt.value);
                      setOpen(false);
                    }}
                    className={`flex w-full items-center gap-1.5 text-left px-2 py-1 text-xs whitespace-nowrap ${
                      isSelected
                        ? 'bg-bambu-dark-tertiary text-white'
                        : 'text-bambu-gray hover:bg-bambu-dark-tertiary/60 hover:text-white'
                    }`}
                  >
                    <Check
                      className={`w-3 h-3 flex-shrink-0 ${isSelected ? 'opacity-100' : 'opacity-0'}`}
                    />
                    <span className="truncate">{opt.label}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function JobStatusChip({ status }: { status: PipelineRun['jobs'][number]['status'] }) {
  const { t } = useTranslation();
  const colours: Record<PipelineRun['jobs'][number]['status'], string> = {
    pending: 'bg-slate-700 text-slate-200',
    awaiting_printer: 'bg-sky-700 text-sky-100',
    queued: 'bg-sky-700 text-sky-100',
    printing: 'bg-emerald-700 text-emerald-100',
    completed: 'bg-emerald-700 text-emerald-100',
    failed: 'bg-red-700 text-red-100',
    cancelled: 'bg-rose-900 text-rose-200',
  };
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase font-semibold tracking-wide ${colours[status]}`}>
      {t(`pipelineRuns.jobStatus.${status}`, status)}
    </span>
  );
}
