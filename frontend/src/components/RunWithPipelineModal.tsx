import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Cog, Loader2, Play, Printer as PrinterIcon, X } from 'lucide-react';
import {
  api,
  type PipelineEligibilityReport,
  type Printer as PrinterType,
  type SlicerPipeline,
} from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useSliceJobTracker } from '../contexts/SliceJobTrackerContext';

// Same source-kind shape SliceModal uses, so the same library-file vs archive
// distinction flows through eligibility-check, run dispatch, AND the progress
// toast tracker.
export type RunPipelineSource =
  | { kind: 'libraryFile'; id: number; filename: string }
  | { kind: 'archive'; id: number; filename: string };

export interface RunWithPipelineModalProps {
  source: RunPipelineSource;
  onClose: () => void;
}

// Two-step modal. Step 1: pick a pipeline. Step 2: confirm eligibility
// (skipped when ok=true) and run. Lives in two views in the same modal so
// the user keeps context — most production runs hit the green path and
// never see step 2.
export function RunWithPipelineModal({ source, onClose }: RunWithPipelineModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [picked, setPicked] = useState<SlicerPipeline | null>(null);
  const [report, setReport] = useState<PipelineEligibilityReport | null>(null);
  const [copies, setCopies] = useState<number>(1);
  const { trackJob } = useSliceJobTracker();

  const { data: list, isLoading: pipelinesLoading } = useQuery({
    queryKey: ['slicer-pipelines'],
    queryFn: () => api.listSlicerPipelines(),
  });
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
  });
  // Cap from settings (PR C). Falls back to 50 when the fetch is in-flight or
  // missing — same default the backend writes.
  const { data: settings } = useQuery({
    queryKey: ['app-settings'],
    queryFn: () => api.getSettings(),
  });
  const maxCopies = settings?.pipeline_max_copies ?? 50;

  const sourceRef = { kind: source.kind, id: source.id } as const;
  const checkMutation = useMutation({
    mutationFn: (pipelineId: number) =>
      api.checkPipelineEligibility(pipelineId, sourceRef),
  });

  const runMutation = useMutation({
    mutationFn: ({ pipelineId, force }: { pipelineId: number; force: boolean }) =>
      api.runPipeline(pipelineId, sourceRef, force, copies),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ['slicer-pipelines'] });
      queryClient.invalidateQueries({ queryKey: ['pipeline-runs'] });
      // Hand the slice job off to the existing tracker so the same persistent
      // progress toast renders for pipeline runs as for manual SliceModal
      // slices — no separate notification surface.
      if (run.slice_job_id) {
        trackJob(run.slice_job_id, source.kind, source.filename);
      }
      showToast(t('library.runWithPipeline.toast.started', 'Pipeline run started'), 'success');
      onClose();
    },
    onError: (err: Error) => {
      showToast(err.message || t('library.runWithPipeline.toast.failed', 'Could not start run'), 'error');
    },
  });

  const pipelines = list?.pipelines ?? [];
  const printerById: Record<number, PrinterType> = (printers ?? []).reduce((acc, p) => {
    acc[p.id] = p;
    return acc;
  }, {} as Record<number, PrinterType>);

  const handlePick = async (pipeline: SlicerPipeline) => {
    const hasTarget =
      pipeline.target_printer_id ||
      (pipeline.target_kind === 'printer_class' && pipeline.target_model_class);
    if (!hasTarget) {
      showToast(
        t('library.runWithPipeline.noTargetMessage', 'This pipeline has no target printer set. Open it in Settings to pick one.'),
        'error',
      );
      return;
    }
    setPicked(pipeline);
    try {
      const result = await checkMutation.mutateAsync(pipeline.id);
      setReport(result);
      if (result.ok) {
        runMutation.mutate({ pipelineId: pipeline.id, force: false });
      }
    } catch {
      // Network error — keep the user on step 1 so they can retry.
      setPicked(null);
    }
  };

  const handleConfirm = () => {
    if (!picked) return;
    runMutation.mutate({ pipelineId: picked.id, force: true });
  };

  const handleBack = () => {
    setPicked(null);
    setReport(null);
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={t('library.runWithPipeline.modalTitle', 'Run with pipeline')}
    >
      <div
        className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-2xl w-full max-w-md max-h-[80vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-bambu-dark-tertiary">
          <h3 className="text-sm font-semibold text-white flex items-center gap-2">
            <Play className="w-4 h-4 text-bambu-green" />
            {picked && report
              ? t('library.runWithPipeline.confirmTitle', 'Confirm run')
              : t('library.runWithPipeline.modalTitle', 'Run with pipeline')}
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('common.close', 'Close')}
            className="text-bambu-gray hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {picked && report ? (
            <ConfirmStep
              pipeline={picked}
              report={report}
              source={source}
              onBack={handleBack}
              onConfirm={handleConfirm}
              running={runMutation.isPending}
            />
          ) : (
            <PickStep
              source={source}
              pipelines={pipelines}
              printerById={printerById}
              loading={pipelinesLoading || checkMutation.isPending}
              onPick={handlePick}
              copies={copies}
              maxCopies={maxCopies}
              onCopiesChange={setCopies}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function PickStep({
  source,
  pipelines,
  printerById,
  loading,
  onPick,
  copies,
  maxCopies,
  onCopiesChange,
}: {
  source: { filename: string };
  pipelines: SlicerPipeline[];
  printerById: Record<number, { name: string }>;
  loading: boolean;
  onPick: (p: SlicerPipeline) => void;
  copies: number;
  maxCopies: number;
  onCopiesChange: (n: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      <p className="text-xs text-bambu-gray">
        {t('library.runWithPipeline.sourceHint', 'Source')}:{' '}
        <span className="text-white">{source.filename}</span>
      </p>
      <div className="flex items-center gap-2">
        <label className="text-xs text-bambu-gray" htmlFor="run-pipeline-copies">
          {t('library.runWithPipeline.copies', 'Copies')}:
        </label>
        <input
          id="run-pipeline-copies"
          type="number"
          min={1}
          max={maxCopies}
          value={copies}
          onChange={(e) => {
            const n = parseInt(e.target.value, 10);
            if (Number.isNaN(n)) return;
            onCopiesChange(Math.max(1, Math.min(maxCopies, n)));
          }}
          aria-label={t('library.runWithPipeline.copies', 'Copies')}
          className="w-20 px-2 py-1 text-xs bg-bambu-dark border border-bambu-dark-tertiary rounded text-white"
        />
        <span className="text-xs text-bambu-gray/60">
          {t('library.runWithPipeline.copiesHint', 'max {{n}}', { n: maxCopies })}
        </span>
      </div>
      {loading && (
        <div className="flex items-center gap-2 text-sm text-bambu-gray">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t('library.runWithPipeline.loading', 'Loading…')}
        </div>
      )}
      {!loading && pipelines.length === 0 && (
        <p className="text-sm text-bambu-gray">
          {t(
            'library.runWithPipeline.empty',
            'No pipelines saved yet. Open the Slice dialog and click "Save as pipeline" to create one.',
          )}
        </p>
      )}
      {!loading && pipelines.length > 0 && (
        <ul className="space-y-1.5" aria-label={t('library.runWithPipeline.pipelineListAria', 'Available pipelines')}>
          {pipelines.map((p) => {
            const isClass = p.target_kind === 'printer_class';
            const targetName = p.target_printer_id ? printerById[p.target_printer_id]?.name : null;
            const classLabel = isClass && p.target_model_class
              ? t('library.runWithPipeline.classTarget', 'Any {{model}}', { model: p.target_model_class })
              : null;
            const hasTarget = !!(p.target_printer_id || (isClass && p.target_model_class));
            return (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => onPick(p)}
                  disabled={!hasTarget}
                  className="w-full text-left px-3 py-2 rounded-md border border-bambu-dark-tertiary bg-bambu-dark/40 hover:bg-bambu-dark-tertiary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <Cog className="w-3.5 h-3.5 text-bambu-green flex-shrink-0" />
                    <span className="text-sm font-medium text-white truncate">{p.name}</span>
                  </div>
                  <div className="mt-1 text-xs text-bambu-gray flex items-center gap-1">
                    <PrinterIcon className="w-3 h-3" />
                    {classLabel ? (
                      <span>{classLabel}</span>
                    ) : targetName ? (
                      <span>{targetName}</span>
                    ) : (
                      <span className="text-amber-700 dark:text-amber-400">
                        {t('library.runWithPipeline.noTarget', 'No target printer set')}
                      </span>
                    )}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

function ConfirmStep({
  pipeline,
  report,
  source,
  onBack,
  onConfirm,
  running,
}: {
  pipeline: SlicerPipeline;
  report: PipelineEligibilityReport;
  source: { filename: string };
  onBack: () => void;
  onConfirm: () => void;
  running: boolean;
}) {
  const { t } = useTranslation();

  return (
    <>
      <div className="text-xs text-bambu-gray">
        <p>
          {t('library.runWithPipeline.confirmIntro', 'Pre-flight found issues with this run')}:
        </p>
        <p className="mt-1">
          <span className="text-bambu-gray/70">{t('library.runWithPipeline.sourceHint', 'Source')}: </span>
          <span className="text-white">{source.filename}</span>
        </p>
        <p>
          <span className="text-bambu-gray/70">{t('library.runWithPipeline.pipelineHint', 'Pipeline')}: </span>
          <span className="text-white">{pipeline.name}</span>
        </p>
        {report.target_printer_name && (
          <p>
            <span className="text-bambu-gray/70">{t('library.runWithPipeline.targetHint', 'Target')}: </span>
            <span className="text-white">{report.target_printer_name}</span>
          </p>
        )}
      </div>

      <ul className="space-y-1.5">
        {report.issues.map((issue, idx) => (
          <li key={idx} className="flex items-start gap-2 text-xs">
            <AlertTriangle className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
            <span className="text-bambu-gray">
              <IssueText issue={issue} />
            </span>
          </li>
        ))}
      </ul>

      <div className="flex items-center justify-end gap-2 pt-2 border-t border-bambu-dark-tertiary">
        <button
          type="button"
          onClick={onBack}
          disabled={running}
          className="px-3 py-1.5 text-xs text-bambu-gray hover:text-white"
        >
          {t('common.back', 'Back')}
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={running}
          className="px-3 py-1.5 text-xs bg-amber-500 hover:bg-amber-600 text-white rounded disabled:opacity-50 flex items-center gap-1"
        >
          {running ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <Play className="w-3 h-3" />
          )}
          {t('library.runWithPipeline.runAnyway', 'Run anyway')}
        </button>
      </div>
    </>
  );
}

function IssueText({ issue }: { issue: PipelineEligibilityReport['issues'][number] }) {
  const { t } = useTranslation();
  switch (issue.kind) {
    case 'printer_not_set':
      return <>{t('library.runWithPipeline.issue.printerNotSet', 'No target printer set on this pipeline.')}</>;
    case 'printer_not_found':
      return <>{t('library.runWithPipeline.issue.printerNotFound', 'Target printer no longer exists.')}</>;
    case 'printer_disabled':
      return <>{t('library.runWithPipeline.issue.printerDisabled', 'Target printer is disabled.')}</>;
    case 'printer_offline':
      return <>{t('library.runWithPipeline.issue.printerOffline', 'Target printer is offline.')}</>;
    case 'filament_type_mismatch':
      return (
        <>
          {t('library.runWithPipeline.issue.filamentType', 'Filament slot {{slot}}: expected {{expected}}, AMS has {{actual}}', {
            slot: (issue.slot_index ?? 0) + 1,
            expected: issue.expected ?? '?',
            actual: issue.actual ?? '?',
          })}
        </>
      );
    case 'filament_color_mismatch':
      return (
        <>
          {t('library.runWithPipeline.issue.filamentColor', 'Filament slot {{slot}}: colour differs (expected {{expected}}, AMS has {{actual}})', {
            slot: (issue.slot_index ?? 0) + 1,
            expected: issue.expected ?? '?',
            actual: issue.actual ?? '?',
          })}
        </>
      );
    case 'ams_slot_missing':
      return (
        <>
          {t('library.runWithPipeline.issue.amsSlotMissing', 'AMS slot {{slot}} not available on this printer', {
            slot: (issue.slot_index ?? 0) + 1,
          })}
        </>
      );
    case 'filament_unverified':
      return (
        <>
          {t('library.runWithPipeline.issue.filamentUnverified', 'Filament slot {{slot}} comes from a cloud / standard preset and could not be statically verified.', {
            slot: (issue.slot_index ?? 0) + 1,
          })}
        </>
      );
    default:
      return <>{issue.kind}</>;
  }
}
