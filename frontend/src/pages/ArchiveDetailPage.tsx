import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  ArrowLeft,
  Box,
  Camera,
  ChevronRight,
  Clock,
  Coins,
  Download,
  Film,
  Layers,
  Loader2,
  Package,
  Pencil,
  Printer,
  StickyNote,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Zap,
} from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { PrintLogEntry } from '../api/client';
import { Button } from '../components/Button';
import { Card, CardContent } from '../components/Card';
import { ConfirmModal } from '../components/ConfirmModal';
import { ConfirmOutcomeDialog } from '../components/ConfirmOutcomeDialog';
import { EditArchiveModal } from '../components/EditArchiveModal';
import { PhotoGalleryModal } from '../components/PhotoGalleryModal';
import { PrintModal } from '../components/PrintModal';
import { TimelapseViewer } from '../components/TimelapseViewer';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useUiSettings } from '../hooks/useUiSettings';
import { getCurrencySymbol } from '../utils/currency';
import { formatDateTime, formatDuration, type TimeFormat } from '../utils/date';
import { invalidateArchiveAndProjectViews } from '../utils/projectQueries';
import { verdictSourceKey } from '../utils/verdictSource';

const RUN_STATUS_CLASSES: Record<string, string> = {
  completed: 'bg-bambu-green/20 text-bambu-green',
  failed: 'bg-red-500/20 text-red-700 dark:text-red-400',
  aborted: 'bg-red-500/20 text-red-700 dark:text-red-400',
  printing: 'bg-blue-500/20 text-blue-700 dark:text-blue-400',
};

const LEGACY_PRINTED = new Set(['completed', 'failed', 'aborted', 'cancelled', 'stopped']);

type Media =
  | { kind: 'video'; src: string; title: string }
  | { kind: 'photos'; photos: string[]; title: string };

/**
 * One archive: the file and every print of it (archive redesign P4). The
 * header holds what belongs to the file (its estimate, plates, tags, note);
 * each print below holds its own outcome, verdict, figures, note and media.
 * A caller limited to some printers sees only their prints of it.
 */
export function ArchiveDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const archiveId = Number(id);
  const [searchParams] = useSearchParams();
  const highlightRun = Number(searchParams.get('run')) || null;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission, canModify } = useAuth();
  const { data: settings } = useUiSettings();
  const timeFormat: TimeFormat = settings?.time_format || 'system';
  const currency = getCurrencySymbol(settings?.currency || 'USD');

  const [showEdit, setShowEdit] = useState(false);
  const [showReprint, setShowReprint] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [deletePurgeStats, setDeletePurgeStats] = useState(false);
  const [confirmRun, setConfirmRun] = useState<number | null>(null);
  const [media, setMedia] = useState<Media | null>(null);

  const archiveQuery = useQuery({
    queryKey: ['archive', archiveId],
    queryFn: () => api.getArchive(archiveId),
    enabled: Number.isFinite(archiveId),
    retry: false,
  });
  const archive = archiveQuery.data;
  const { data: runsData, isLoading: runsLoading } = useQuery({
    queryKey: ['archive-runs', archiveId],
    queryFn: () => api.getArchiveRuns(archiveId),
    enabled: archive != null && archive.id === archiveId,
  });
  const { data: platesData } = useQuery({
    queryKey: ['archive-plates', archiveId],
    queryFn: () => api.getArchivePlates(archiveId),
    enabled: archive != null && archive.id === archiveId,
    staleTime: 5 * 60 * 1000,
  });
  const deleteImpact = useQuery({
    queryKey: ['archive', archiveId, 'delete-impact'],
    queryFn: () => api.getArchiveDeleteImpact(archiveId),
    enabled: showDelete,
    staleTime: 0,
  });

  // A merged archive's id opens the archive it went into (archive redesign
  // P2): the address follows, so its prints and media load from that one.
  useEffect(() => {
    if (archive && archive.id !== archiveId) {
      navigate(`/archives/${archive.id}${highlightRun ? `?run=${highlightRun}` : ''}`, { replace: true });
    }
  }, [archive, archiveId, highlightRun, navigate]);

  const runs = useMemo(() => runsData?.items ?? [], [runsData]);
  const plates = platesData?.is_multi_plate ? platesData.plates : [];
  const printsPerPlate = useMemo(() => {
    const counts = new Map<number, number>();
    for (const run of runs) {
      if (run.plate_id != null) counts.set(run.plate_id, (counts.get(run.plate_id) ?? 0) + 1);
    }
    return counts;
  }, [runs]);

  const highlightRef = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    if (highlightRun && runs.some((r) => r.id === highlightRun)) {
      highlightRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    }
  }, [highlightRun, runs]);

  const deleteMutation = useMutation({
    mutationFn: (purgeStats: boolean) => api.deleteArchive(archiveId, purgeStats),
    onSuccess: () => {
      invalidateArchiveAndProjectViews(queryClient);
      showToast(t('archives.toast.archiveDeleted'));
      navigate('/archives');
    },
    onError: () => showToast(t('archives.toast.failedDeleteArchive'), 'error'),
  });

  if (archiveQuery.isLoading) {
    return (
      <div className="flex justify-center p-16">
        <Loader2 className="w-8 h-8 text-bambu-gray animate-spin" />
      </div>
    );
  }
  if (!archive) {
    // Missing, or not the caller's: the API answers 404 for both. Anything
    // else (a server error, a lost connection) can be tried again.
    const missing = archiveQuery.error instanceof ApiError && archiveQuery.error.status === 404;
    return (
      <div className="p-4 md:p-8">
        <Link to="/archives" className="text-sm text-bambu-gray hover:text-white">
          {t('nav.archives')}
        </Link>
        <div className="mt-8 flex flex-col items-center gap-3 text-bambu-gray">
          <p>{missing || !archiveQuery.error ? t('archivePage.notFound') : t('archivePage.loadFailed')}</p>
          {!missing && archiveQuery.error && (
            <Button variant="secondary" onClick={() => archiveQuery.refetch()}>
              {t('archivePage.retry')}
            </Button>
          )}
        </div>
      </div>
    );
  }

  const name = archive.print_name || archive.filename;
  const canUpdate = canModify('archives', 'update', archive.created_by_id);
  const canReprint = !!archive.file_path && hasPermission('queue:create') && canModify('archives', 'reprint', archive.created_by_id);
  const canDelete = canModify('archives', 'delete', archive.created_by_id);
  // A video no print of the list holds is of one logged before prints kept
  // their own: the file's, shown in the header.
  const fileVideo = archive.timelapse_path && !runs.some((r) => r.timelapse_path === archive.timelapse_path);
  const tags = archive.tags?.split(',').map((tag) => tag.trim()).filter(Boolean) ?? [];

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center gap-2 text-sm text-bambu-gray min-w-0">
        <Link to="/archives" className="hover:text-white transition-colors">
          {t('nav.archives')}
        </Link>
        <ChevronRight className="w-4 h-4 shrink-0" />
        <span className="text-white truncate">{name}</span>
      </div>

      <Card>
        <CardContent className="p-4 md:p-6">
          <div className="flex flex-col md:flex-row gap-6">
            <div className="w-full md:w-60 shrink-0">
              <div className="aspect-square rounded-lg bg-bambu-dark overflow-hidden flex items-center justify-center">
                {archive.thumbnail_path ? (
                  <img src={api.getArchiveThumbnail(archive.id)} alt={name} className="w-full h-full object-contain" />
                ) : (
                  <Package className="w-16 h-16 text-bambu-gray/50" />
                )}
              </div>
            </div>
            <div className="flex-1 min-w-0 space-y-4">
              <div className="flex items-start gap-3">
                <button
                  onClick={() => navigate('/archives')}
                  className="p-2 rounded-lg bg-bambu-dark hover:bg-bambu-dark-tertiary transition-colors shrink-0"
                  aria-label={t('archivePage.back')}
                >
                  <ArrowLeft className="w-5 h-5 text-bambu-gray" />
                </button>
                <div className="min-w-0">
                  <h1 className="text-2xl font-bold text-white break-words">{name}</h1>
                  {archive.print_name && archive.filename !== archive.print_name && (
                    <p className="text-sm text-bambu-gray break-all">{archive.filename}</p>
                  )}
                </div>
              </div>

              {(archive.project_name || tags.length > 0) && (
                <div className="flex flex-wrap gap-1.5">
                  {archive.project_name && archive.project_id != null && (
                    <Link
                      to={`/projects/${archive.project_id}`}
                      className="text-xs px-2 py-0.5 rounded bg-bambu-green/20 text-bambu-green hover:bg-bambu-green/30"
                    >
                      {archive.project_name}
                    </Link>
                  )}
                  {tags.map((tag) => (
                    <span key={tag} className="text-xs px-2 py-0.5 rounded bg-bambu-dark text-bambu-gray-light">
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                <Fact icon={Clock} label={t('archivePage.estimatedTime')} value={archive.print_time_seconds ? formatDuration(archive.print_time_seconds) : '—'} />
                <Fact icon={Package} label={t('archivePage.estimatedFilament')} value={archive.filament_used_grams ? `${archive.filament_used_grams.toFixed(1)} g` : '—'} />
                <Fact icon={Layers} label={t('archivePage.layers')} value={archive.total_layers ? String(archive.total_layers) : '—'} />
                <Fact
                  icon={Printer}
                  label={t('archivePage.prints')}
                  value={t('archivePage.printsValue', {
                    count: archive.run_count,
                    successful: archive.successful_run_count,
                    failed: archive.failed_run_count,
                  })}
                />
              </dl>
              <p className="text-xs text-bambu-gray">
                {t('archivePage.archivedAt', { date: formatDateTime(archive.created_at, timeFormat) })}
              </p>

              {archive.notes && (
                <div className="flex gap-2 text-sm text-bambu-gray-light bg-bambu-dark rounded-lg p-3">
                  <StickyNote className="w-4 h-4 shrink-0 mt-0.5 text-bambu-gray" />
                  <p className="whitespace-pre-wrap break-words">{archive.notes}</p>
                </div>
              )}

              <div className="flex flex-wrap gap-2">
                <Button
                  onClick={() => setShowReprint(true)}
                  disabled={!canReprint}
                  title={!canReprint ? t('archives.permission.noReprint') : undefined}
                >
                  <Printer className="w-4 h-4" />
                  {t('archivePage.reprint')}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => setShowEdit(true)}
                  disabled={!canUpdate}
                  title={!canUpdate ? t('archives.permission.noUpdateArchives') : undefined}
                >
                  <Pencil className="w-4 h-4" />
                  {t('archivePage.edit')}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => navigate(`/gcode-viewer?archive=${archive.id}`)}
                >
                  <Box className="w-4 h-4" />
                  {t('archivePage.preview')}
                </Button>
                <Button
                  variant="secondary"
                  disabled={!hasPermission('archives:read')}
                  onClick={() => {
                    api.downloadArchive(archive.id, `${name}.3mf`).catch(() => {
                      showToast(t('archivePage.downloadFailed'), 'error');
                    });
                  }}
                >
                  <Download className="w-4 h-4" />
                  {t('archivePage.download')}
                </Button>
                {fileVideo && (
                  <Button
                    variant="secondary"
                    onClick={() =>
                      setMedia({ kind: 'video', src: api.getArchiveTimelapse(archive.id), title: t('archives.modal.timelapse', { name }) })
                    }
                  >
                    <Film className="w-4 h-4" />
                    {t('archivePage.video')}
                  </Button>
                )}
                {archive.photos && archive.photos.length > 0 && (
                  <Button
                    variant="secondary"
                    onClick={() => setMedia({ kind: 'photos', photos: archive.photos!, title: name })}
                  >
                    <Camera className="w-4 h-4" />
                    {t('archivePage.allPhotos', { count: archive.photos.length })}
                  </Button>
                )}
                <Button
                  variant="danger"
                  onClick={() => setShowDelete(true)}
                  disabled={!canDelete}
                  title={!canDelete ? t('archives.permission.noDelete') : undefined}
                >
                  <Trash2 className="w-4 h-4" />
                  {t('archivePage.delete')}
                </Button>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {plates.length > 1 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold text-white">{t('archivePage.plates')}</h2>
          <ul className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            {plates.map((plate) => (
              <li key={plate.index}>
                <Card>
                  <CardContent className="p-3 space-y-2">
                    <div className="aspect-square rounded bg-bambu-dark overflow-hidden flex items-center justify-center">
                      {plate.has_thumbnail ? (
                        <img
                          src={api.getArchivePlateThumbnail(archive.id, plate.index)}
                          alt={plate.name || t('archives.card.plateNumber', { index: plate.index })}
                          className="w-full h-full object-contain"
                        />
                      ) : (
                        <Package className="w-8 h-8 text-bambu-gray/50" />
                      )}
                    </div>
                    <p className="text-sm text-white truncate">
                      {plate.name || t('archives.card.plateNumber', { index: plate.index })}
                    </p>
                    <p className="text-xs text-bambu-gray">
                      {[
                        plate.print_time_seconds ? formatDuration(plate.print_time_seconds) : null,
                        plate.filament_used_grams ? `${plate.filament_used_grams.toFixed(1)} g` : null,
                      ]
                        .filter(Boolean)
                        .join(' · ') || '—'}
                    </p>
                    <p className="text-xs text-bambu-gray">
                      {t('archivePage.platePrinted', { count: printsPerPlate.get(plate.index) ?? 0 })}
                    </p>
                  </CardContent>
                </Card>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-white">{t('archivePage.printsTitle')}</h2>
        {runsLoading ? (
          <div className="flex justify-center py-6">
            <Loader2 className="w-6 h-6 text-bambu-gray animate-spin" />
          </div>
        ) : runs.length === 0 ? (
          <p className="text-sm text-bambu-gray italic">
            {/* Printed before each print was logged: its one print is the
                archive's own outcome. */}
            {LEGACY_PRINTED.has(archive.status)
              ? t('archivePage.printedBeforeLog', {
                  status: t(`archives.runLog.status.${archive.status}`, { defaultValue: archive.status }),
                })
              : t('archivePage.noPrints')}
          </p>
        ) : (
          <ul className="space-y-3">
            {runs.map((run) => (
              <RunItem
                key={run.id}
                run={run}
                archiveId={archive.id}
                name={name}
                showPlate={plates.length > 1 || runs.some((r) => r.plate_id != null && r.plate_id > 1)}
                highlighted={run.id === highlightRun}
                itemRef={run.id === highlightRun ? highlightRef : undefined}
                timeFormat={timeFormat}
                currency={currency}
                canAnswer={canUpdate}
                onConfirm={() => setConfirmRun(run.id)}
                onMedia={setMedia}
              />
            ))}
          </ul>
        )}
      </section>

      {showEdit && <EditArchiveModal archive={archive} onClose={() => setShowEdit(false)} />}
      {showReprint && (
        <PrintModal
          mode="create"
          archiveId={archive.id}
          archiveName={name}
          preselectPlateId={archive.plate_id}
          onClose={() => setShowReprint(false)}
        />
      )}
      {confirmRun != null && (
        <ConfirmOutcomeDialog
          archiveId={archive.id}
          runId={confirmRun}
          onClose={() => {
            setConfirmRun(null);
            queryClient.invalidateQueries({ queryKey: ['archive-runs', archive.id] });
          }}
        />
      )}
      {media?.kind === 'video' && (
        <TimelapseViewer
          src={media.src}
          title={media.title}
          downloadFilename={`${name}_timelapse.mp4`}
          onClose={() => setMedia(null)}
        />
      )}
      {media?.kind === 'photos' && (
        <PhotoGalleryModal
          archiveId={archive.id}
          archiveName={media.title}
          photos={media.photos}
          onClose={() => setMedia(null)}
        />
      )}
      {showDelete && (
        <ConfirmModal
          title={t('archives.modal.deleteArchive')}
          message={t('archives.modal.deleteConfirm', { name })}
          confirmText={t('archives.modal.deleteButton')}
          variant="danger"
          confirmDisabled={(deleteImpact.data?.currently_printing ?? 0) > 0}
          onConfirm={() => {
            deleteMutation.mutate(deletePurgeStats);
            setShowDelete(false);
            setDeletePurgeStats(false);
          }}
          onCancel={() => {
            setShowDelete(false);
            setDeletePurgeStats(false);
          }}
        >
          {(deleteImpact.data?.related_queue_items ?? 0) > 0 && (
            <div
              className={
                (deleteImpact.data?.currently_printing ?? 0) > 0
                  ? 'text-sm text-red-600 dark:text-red-400 mb-2'
                  : 'text-sm text-amber-600 dark:text-amber-400 mb-2'
              }
            >
              {(deleteImpact.data?.currently_printing ?? 0) > 0
                ? t('archives.modal.deleteBlockedByPrinting', { count: deleteImpact.data!.currently_printing })
                : t('archives.modal.deleteQueueItemsWarning', { count: deleteImpact.data!.related_queue_items })}
            </div>
          )}
          <label className="flex items-start gap-2 cursor-pointer text-sm text-bambu-gray">
            <input
              type="checkbox"
              className="mt-0.5 accent-red-500"
              checked={deletePurgeStats}
              onChange={(e) => setDeletePurgeStats(e.target.checked)}
            />
            <span>{t('archives.modal.deletePurgeStats')}</span>
          </label>
        </ConfirmModal>
      )}
    </div>
  );
}

function Fact({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: string }) {
  return (
    <div className="bg-bambu-dark rounded-lg p-3 min-w-0">
      <dt className="flex items-center gap-1.5 text-xs text-bambu-gray">
        <Icon className="w-3.5 h-3.5" />
        {label}
      </dt>
      <dd className="mt-1 text-white font-medium truncate">{value}</dd>
    </div>
  );
}

function RunItem({
  run,
  archiveId,
  name,
  showPlate,
  highlighted,
  itemRef,
  timeFormat,
  currency,
  canAnswer,
  onConfirm,
  onMedia,
}: {
  run: PrintLogEntry;
  archiveId: number;
  name: string;
  showPlate: boolean;
  highlighted: boolean;
  itemRef?: React.Ref<HTMLLIElement>;
  timeFormat: TimeFormat;
  currency: string;
  canAnswer: boolean;
  onConfirm: () => void;
  onMedia: (media: Media) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { canModify } = useAuth();
  const [editingNote, setEditingNote] = useState(false);
  const [note, setNote] = useState(run.notes ?? '');
  const canEditNote = canModify('archives', 'update', run.created_by_id);

  const noteMutation = useMutation({
    mutationFn: (notes: string) => api.updatePrintLogEntry(run.id, { notes }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archive-runs', archiveId] });
      queryClient.invalidateQueries({ queryKey: ['print-log'] });
      setEditingNote(false);
      showToast(t('archivePage.noteSaved'));
    },
    onError: () => showToast(t('archivePage.noteFailed'), 'error'),
  });

  const verdictHint = verdictSourceKey(run.user_verdict_source);
  // Next to the slicer's figures for the plate it printed (#3375).
  const withEstimate = (value: string, estimate: string | null) =>
    estimate ? t('archivePage.withEstimate', { value, estimate }) : value;
  const figures = [
    run.duration_seconds
      ? {
          icon: Clock,
          text: withEstimate(
            `${run.duration_unmeasured ? '~' : ''}${formatDuration(run.duration_seconds)}`,
            run.estimate_seconds ? formatDuration(run.estimate_seconds) : null,
          ),
        }
      : null,
    run.filament_used_grams != null
      ? {
          icon: Package,
          text: withEstimate(
            `${run.filament_used_grams.toFixed(1)} g`,
            run.estimate_grams ? `${run.estimate_grams.toFixed(1)} g` : null,
          ),
        }
      : null,
    run.cost != null ? { icon: Coins, text: `${currency}${run.cost.toFixed(2)}` } : null,
    run.energy_kwh != null ? { icon: Zap, text: `${run.energy_kwh.toFixed(2)} kWh` } : null,
  ].filter((f): f is { icon: typeof Clock; text: string } => f !== null);
  const pending = run.status === 'completed' && run.confirm_requested && run.user_verdict == null;

  return (
    <li ref={itemRef} data-testid={`archive-run-${run.id}`}>
      <Card className={highlighted ? 'ring-2 ring-bambu-green' : undefined}>
        <CardContent className="p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <span className="text-sm text-white font-medium">
              {formatDateTime(run.started_at || run.created_at, timeFormat)}
            </span>
            <span className={`text-xs px-2 py-0.5 rounded font-medium ${RUN_STATUS_CLASSES[run.status] ?? 'bg-bambu-dark text-bambu-gray'}`}>
              {t(`archives.runLog.status.${run.status}`, { defaultValue: run.status })}
            </span>
            {run.failure_reason && (
              <span className="text-xs text-red-700 dark:text-red-400">
                {t(`editArchive.failureReasons.${run.failure_reason}`, { defaultValue: run.failure_reason })}
              </span>
            )}
            {run.user_verdict === 'good' && (
              <span
                className="text-xs px-2 py-0.5 rounded bg-bambu-green/80 text-white flex items-center gap-1"
                title={`${t('archives.card.confirmedGoodTitle')}${verdictHint ? ` — ${t(verdictHint)}` : ''}`}
              >
                <ThumbsUp className="w-3 h-3" />
                {t('archives.card.confirmedGood')}
              </span>
            )}
            {run.user_verdict === 'reject' && (
              <span
                className="text-xs px-2 py-0.5 rounded bg-status-error/80 text-white flex items-center gap-1"
                title={`${t('archives.card.rejectedTitle')}${verdictHint ? ` — ${t(verdictHint)}` : ''}`}
              >
                <ThumbsDown className="w-3 h-3" />
                {t('archives.card.rejected')}
              </span>
            )}
            {run.status === 'completed' && (
              <button
                onClick={onConfirm}
                disabled={!canAnswer}
                className={
                  pending
                    ? 'text-xs px-2 py-0.5 rounded bg-amber-500/90 enabled:hover:bg-amber-500 text-black flex items-center gap-1'
                    : 'text-xs text-bambu-gray enabled:hover:text-white underline-offset-2 enabled:hover:underline'
                }
                title={!canAnswer ? t('archives.permission.noUpdateArchives') : undefined}
              >
                {pending ? (
                  <>
                    <ThumbsUp className="w-3 h-3" />
                    {t('archives.card.confirmPending')}
                  </>
                ) : run.user_verdict ? (
                  t('archivePage.changeOutcome')
                ) : (
                  t('archivePage.setOutcome')
                )}
              </button>
            )}
          </div>

          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-bambu-gray">
            <span className="flex items-center gap-1">
              <Printer className="w-3.5 h-3.5" />
              {run.printer_name || t('archivePage.noPrinter')}
            </span>
            {showPlate && run.plate_id != null && (
              <span className="flex items-center gap-1">
                <Layers className="w-3.5 h-3.5" />
                {t('archives.card.plateNumber', { index: run.plate_id })}
              </span>
            )}
            {figures.map(({ icon: Icon, text }) => (
              <span key={text} className="flex items-center gap-1">
                <Icon className="w-3.5 h-3.5" />
                {text}
              </span>
            ))}
            {run.created_by_username && <span>{t('archivePage.startedBy', { name: run.created_by_username })}</span>}
          </div>

          {editingNote ? (
            <div className="space-y-2">
              <label className="sr-only" htmlFor={`run-note-${run.id}`}>
                {t('archivePage.printNote')}
              </label>
              <textarea
                id={`run-note-${run.id}`}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={3}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-white focus:border-bambu-green focus:outline-none"
                placeholder={t('archivePage.notePlaceholder')}
              />
              <div className="flex gap-2">
                <Button size="sm" onClick={() => noteMutation.mutate(note)} disabled={noteMutation.isPending}>
                  {t('common.save')}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    setNote(run.notes ?? '');
                    setEditingNote(false);
                  }}
                >
                  {t('common.cancel')}
                </Button>
              </div>
            </div>
          ) : run.notes ? (
            <div className="flex gap-2 text-sm text-bambu-gray-light">
              <StickyNote className="w-4 h-4 shrink-0 mt-0.5 text-bambu-gray" />
              <p className="whitespace-pre-wrap break-words flex-1">{run.notes}</p>
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            {run.timelapse_path && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() =>
                  onMedia({ kind: 'video', src: api.getArchiveRunTimelapse(archiveId, run.id), title: t('archives.modal.timelapse', { name }) })
                }
              >
                <Film className="w-3.5 h-3.5" />
                {t('archivePage.video')}
              </Button>
            )}
            {run.photos && run.photos.length > 0 && (
              <Button size="sm" variant="secondary" onClick={() => onMedia({ kind: 'photos', photos: run.photos!, title: name })}>
                <Camera className="w-3.5 h-3.5" />
                {t('archivePage.photos', { count: run.photos.length })}
              </Button>
            )}
            {!editingNote && canEditNote && (
              <Button size="sm" variant="secondary" onClick={() => setEditingNote(true)}>
                <StickyNote className="w-3.5 h-3.5" />
                {run.notes ? t('archivePage.editNote') : t('archivePage.addNote')}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </li>
  );
}
