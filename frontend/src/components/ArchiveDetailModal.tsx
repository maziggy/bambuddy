import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Camera,
  ChevronDown,
  ChevronRight,
  Cog,
  Download,
  ExternalLink,
  Film,
  Loader2,
  MoreHorizontal,
  Package,
  Pencil,
  Printer,
  StickyNote,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
} from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { PrintLogEntry } from '../api/client';
import type { PlateMetadata } from '../types/plates';
import { Button } from './Button';
import { Card, CardContent } from './Card';
import { ConfirmModal } from './ConfirmModal';
import { ConfirmOutcomeDialog } from './ConfirmOutcomeDialog';
import { ContextMenu, type ContextMenuItem } from './ContextMenu';
import { EditArchiveModal } from './EditArchiveModal';
import { PhotoGalleryModal } from './PhotoGalleryModal';
import { PrintModal } from './PrintModal';
import { SliceModal } from './SliceModal';
import { ThumbnailHoverPreview } from './ThumbnailHoverPreview';
import { TimelapseViewer } from './TimelapseViewer';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useUiSettings } from '../hooks/useUiSettings';
import { getCurrencySymbol } from '../utils/currency';
import { formatDateTime, formatDuration, type TimeFormat } from '../utils/date';
import { formatFileSize } from '../utils/file';
import { invalidateArchiveAndProjectViews } from '../utils/projectQueries';
import { isSlicedFile, openInSlicerWithToken, resolveDesktopSlicer } from '../utils/slicer';
import { verdictSourceKey } from '../utils/verdictSource';

const RUN_STATUS_CLASSES: Record<string, string> = {
  completed: 'bg-bambu-green/20 text-bambu-green',
  failed: 'bg-red-500/20 text-red-700 dark:text-red-400',
  aborted: 'bg-red-500/20 text-red-700 dark:text-red-400',
  printing: 'bg-blue-500/20 text-blue-700 dark:text-blue-400',
};

const LEGACY_PRINTED = new Set(['completed', 'failed', 'aborted', 'cancelled', 'stopped']);
const FAILED = new Set(['failed', 'aborted']);

// The prints table from xl up, with and without its plate column; narrower
// screens stack each row's values instead.
const ROW_COLUMNS_WITH_PLATE =
  'xl:grid xl:grid-cols-[minmax(130px,1.1fr)_minmax(60px,0.5fr)_minmax(90px,0.9fr)_minmax(120px,1fr)_minmax(150px,1.3fr)_minmax(110px,1fr)_minmax(60px,0.5fr)_minmax(140px,1.4fr)]';
const ROW_COLUMNS =
  'xl:grid xl:grid-cols-[minmax(130px,1.1fr)_minmax(90px,0.9fr)_minmax(120px,1fr)_minmax(150px,1.3fr)_minmax(110px,1fr)_minmax(60px,0.5fr)_minmax(140px,1.4fr)]';

/** A print's finish photos and video: its own, or the archive's it shares. */
interface RunMedia {
  photos: string[];
  video: string | null;
}

type Media =
  | { kind: 'video'; src: string; title: string }
  | { kind: 'photos'; photos: string[]; title: string };

/** How far a measured print time ran from its estimate, as "+4%" / "-6%". */
function estimateDelta(seconds: number, estimate: number): string {
  const delta = Math.round(((seconds - estimate) / estimate) * 100);
  return `${delta > 0 ? '+' : ''}${delta}%`;
}

/**
 * One archive: the file and every print of it (archive redesign P4). The
 * header and the file section hold what belongs to the file (its estimate,
 * plates, tags, note); each print below holds its own outcome, verdict,
 * figures, note and media. A caller limited to some printers sees only their
 * prints of it.
 *
 * Shown over the archives list at `/archives/<id>`, so a link to an archive
 * (or to one print of it, `?run=`) still opens it; closes on Escape or a
 * click outside it.
 */
export function ArchiveDetailModal({ archiveId, onClose }: { archiveId: number; onClose: () => void }) {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const highlightRun = Number(searchParams.get('run')) || null;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission, canModify } = useAuth();
  const { data: settings } = useUiSettings();
  const timeFormat: TimeFormat = settings?.time_format || 'system';
  const currency = getCurrencySymbol(settings?.currency || 'USD');
  const useSlicerApi = settings?.use_slicer_api ?? false;

  const [showEdit, setShowEdit] = useState(false);
  // The plate a reprint starts on: the archive's, a plate card's or a print's.
  const [reprintPlate, setReprintPlate] = useState<number | null | undefined>(undefined);
  const [showSlice, setShowSlice] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [deletePurgeStats, setDeletePurgeStats] = useState(false);
  const [confirmRun, setConfirmRun] = useState<number | null>(null);
  const [media, setMedia] = useState<Media | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const [plateFilter, setPlateFilter] = useState<number | null>(null);
  const [openRun, setOpenRun] = useState<number | null>(highlightRun);

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

  // Escape closes the archive, unless it closes a dialog opened from it first.
  const childOpen =
    showEdit || reprintPlate !== undefined || showSlice || showDelete || confirmRun != null || media != null || menu != null;
  useEffect(() => {
    if (childOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      // In a field (a note being written) Escape leaves the field only.
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable)) {
        return;
      }
      onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [childOpen, onClose]);
  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  // A merged archive's id opens the archive it went into (archive redesign
  // P2): the address follows, so its prints and media load from that one.
  useEffect(() => {
    if (archive && archive.id !== archiveId) {
      navigate(`/archives/${archive.id}${highlightRun ? `?run=${highlightRun}` : ''}`, { replace: true });
    }
  }, [archive, archiveId, highlightRun, navigate]);

  const runs = useMemo(() => runsData?.items ?? [], [runsData]);
  const plates = useMemo(() => (platesData?.is_multi_plate ? platesData.plates : []), [platesData]);
  const showPlate = plates.length > 1 || runs.some((r) => r.plate_id != null && r.plate_id > 1);
  const shownRuns = plateFilter == null ? runs : runs.filter((r) => r.plate_id === plateFilter);

  const printsRef = useRef<HTMLElement | null>(null);
  const highlightRef = useRef<HTMLDivElement | null>(null);
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
      onClose();
    },
    onError: () => showToast(t('archives.toast.failedDeleteArchive'), 'error'),
  });

  if (archiveQuery.isLoading) {
    return (
      <ModalShell onClose={onClose} label={t('nav.archives')}>
        <div className="flex justify-center p-16">
          <Loader2 className="w-8 h-8 text-bambu-gray animate-spin" />
        </div>
      </ModalShell>
    );
  }
  if (!archive) {
    // Missing, or not the caller's: the API answers 404 for both. Anything
    // else (a server error, a lost connection) can be tried again.
    const missing = archiveQuery.error instanceof ApiError && archiveQuery.error.status === 404;
    return (
      <ModalShell onClose={onClose} label={t('nav.archives')}>
        <div className="py-12 flex flex-col items-center gap-3 text-bambu-gray">
          <p>{missing || !archiveQuery.error ? t('archivePage.notFound') : t('archivePage.loadFailed')}</p>
          {!missing && archiveQuery.error && (
            <Button variant="secondary" onClick={() => archiveQuery.refetch()}>
              {t('archivePage.retry')}
            </Button>
          )}
        </div>
      </ModalShell>
    );
  }

  const name = archive.print_name || archive.filename;
  const canUpdate = canModify('archives', 'update', archive.created_by_id);
  const canDelete = canModify('archives', 'delete', archive.created_by_id);
  // Only a sliced file prints; a source file is sliced first, as on its card.
  const printable = isSlicedFile(archive);
  // Why printing is off, as the archive card says it.
  const printBlocked = !archive.file_path
    ? t('archives.card.noFileForReprint')
    : !hasPermission('queue:create')
      ? t('archives.permission.noAddToQueue')
      : !canModify('archives', 'reprint', archive.created_by_id)
        ? t('archives.permission.noReprint')
        : undefined;
  const canReprint = printBlocked === undefined;
  // The sliced copy is printable as the user's own, so it needs the right to reprint.
  const sliceBlocked = !archive.file_path
    ? t('archives.card.noFileForReprint')
    : useSlicerApi && !canModify('archives', 'reprint', archive.created_by_id)
      ? t('archives.permission.noReprint')
      : undefined;
  // A video no print of the list holds is of one logged before prints kept
  // their own: the file's, offered in the actions menu.
  const fileVideo = archive.timelapse_path && !runs.some((r) => r.timelapse_path === archive.timelapse_path);
  // A print logged before prints kept their own photos (no list of its own)
  // shares the archive's that no print holds; without a video of its own it
  // shares the archive's video as well.
  const ownPhotos = new Set(runs.flatMap((r) => r.photos ?? []));
  const sharedPhotos = (archive.photos ?? []).filter((photo) => !ownPhotos.has(photo));
  const mediaOf = (run: PrintLogEntry): RunMedia => ({
    photos: run.photos ?? sharedPhotos,
    video: run.timelapse_path
      ? api.getArchiveRunTimelapse(archive.id, run.id)
      : run.photos == null && fileVideo
        ? api.getArchiveTimelapse(archive.id)
        : null,
  });
  const tags = archive.tags?.split(',').map((tag) => tag.trim()).filter(Boolean) ?? [];
  const failedCount = archive.failed_run_count ?? 0;
  const goodCount = archive.successful_run_count ?? 0;
  const estimate = [
    archive.print_time_seconds ? formatDuration(archive.print_time_seconds) : null,
    archive.filament_used_grams ? `${archive.filament_used_grams.toFixed(1)} g` : null,
  ]
    .filter(Boolean)
    .join(' · ');
  const subtitle = [
    archive.print_name && archive.filename !== archive.print_name ? archive.filename : null,
    archive.sliced_for_model ? t('archivePage.slicedFor', { model: archive.sliced_for_model }) : null,
    archive.nozzle_diameter ? `${archive.nozzle_diameter} mm` : null,
    archive.bed_type,
  ].filter(Boolean);

  const slice = () => {
    if (useSlicerApi) {
      setShowSlice(true);
    } else {
      openInSlicerWithToken(
        archive.id,
        name || 'model',
        'file',
        resolveDesktopSlicer(settings?.open_in_slicer, settings?.preferred_slicer),
      );
    }
  };

  const menuItems: ContextMenuItem[] = [
    {
      label: t('archivePage.preview'),
      icon: <Box className="w-4 h-4" />,
      onClick: () => navigate(`/gcode-viewer?archive=${archive.id}`),
    },
    {
      label: t('archivePage.download'),
      icon: <Download className="w-4 h-4" />,
      disabled: !hasPermission('archives:read'),
      onClick: () => {
        api.downloadArchive(archive.id, `${name}.3mf`).catch(() => {
          showToast(t('archivePage.downloadFailed'), 'error');
        });
      },
    },
    ...(fileVideo
      ? [
          {
            label: t('archivePage.video'),
            icon: <Film className="w-4 h-4" />,
            onClick: () =>
              setMedia({ kind: 'video', src: api.getArchiveTimelapse(archive.id), title: t('archives.modal.timelapse', { name }) }),
          },
        ]
      : []),
    ...(archive.photos && archive.photos.length > 0
      ? [
          {
            label: t('archivePage.allPhotos', { count: archive.photos.length }),
            icon: <Camera className="w-4 h-4" />,
            onClick: () => setMedia({ kind: 'photos', photos: archive.photos!, title: name }),
          },
        ]
      : []),
    { label: '', divider: true, onClick: () => {} },
    {
      label: t('archivePage.delete'),
      icon: <Trash2 className="w-4 h-4" />,
      danger: true,
      disabled: !canDelete,
      title: !canDelete ? t('archives.permission.noDelete') : undefined,
      onClick: () => setShowDelete(true),
    },
  ];

  const fileFacts = [
    archive.layer_height ? { label: t('archivePage.layerHeight'), value: `${archive.layer_height} mm` } : null,
    archive.nozzle_diameter ? { label: t('archivePage.nozzle'), value: `${archive.nozzle_diameter} mm` } : null,
    archive.bed_type ? { label: t('archivePage.bedType'), value: archive.bed_type } : null,
    archive.sliced_for_model ? { label: t('archivePage.slicedForLabel'), value: archive.sliced_for_model } : null,
    archive.total_layers ? { label: t('archivePage.layers'), value: String(archive.total_layers) } : null,
    archive.file_size ? { label: t('archivePage.size'), value: formatFileSize(archive.file_size) } : null,
    { label: t('archivePage.archived'), value: formatDateTime(archive.created_at, timeFormat) },
  ].filter((f): f is { label: string; value: string } => f !== null);

  return (
    <ModalShell onClose={onClose} label={name}>
      <header className="flex flex-col md:flex-row md:flex-wrap gap-6 items-start pr-10" data-testid="archive-header">
        <ThumbnailHoverPreview
          src={archive.thumbnail_path ? api.getArchiveThumbnail(archive.id) : null}
          size={480}
          className="w-full sm:w-56 aspect-[4/3] rounded-lg bg-bambu-dark overflow-hidden flex items-center justify-center shrink-0"
        >
          {archive.thumbnail_path ? (
            <img src={api.getArchiveThumbnail(archive.id)} alt={name} className="w-full h-full object-contain" />
          ) : (
            <Package className="w-16 h-16 text-bambu-gray/50" />
          )}
        </ThumbnailHoverPreview>
        <div className="flex-[999_1_24rem] min-w-0 space-y-3">
          <h2 id="archive-modal-title" className="text-2xl font-bold text-white break-words min-w-0">
            {name}
          </h2>
          {(subtitle.length > 0 || archive.project_name) && (
            <p className="text-sm text-bambu-gray break-words">
              {subtitle.join(' · ')}
              {archive.project_name && archive.project_id != null && (
                <>
                  {subtitle.length > 0 && ' · '}
                  {t('archivePage.project')}{' '}
                  <Link to={`/projects/${archive.project_id}`} className="text-bambu-green hover:underline">
                    {archive.project_name}
                  </Link>
                </>
              )}
            </p>
          )}
          <div className="flex flex-wrap gap-2 text-sm">
            {plates.length > 1 && <Chip>{t('archivePage.platesCount', { count: plates.length })}</Chip>}
            <Chip>{t('archivePage.printsCount', { count: archive.run_count ?? 0 })}</Chip>
            {goodCount > 0 && (
              <Chip className="bg-bambu-green/20 text-bambu-green">
                {t('archivePage.goodCount', { count: goodCount })}
              </Chip>
            )}
            {failedCount > 0 && (
              <Chip className="bg-red-500/20 text-red-700 dark:text-red-400">
                {t('archivePage.failedCount', { count: failedCount })}
              </Chip>
            )}
            {estimate && <Chip>{t('archivePage.estimate', { value: estimate })}</Chip>}
          </div>
          {archive.notes && (
            <div className="flex gap-2 text-sm text-bambu-gray-light bg-bambu-dark rounded-lg p-3">
              <StickyNote className="w-4 h-4 shrink-0 mt-0.5 text-bambu-gray" />
              <p className="whitespace-pre-wrap break-words">{archive.notes}</p>
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {printable && (
            <Button onClick={() => setReprintPlate(archive.plate_id)} disabled={!canReprint} title={printBlocked}>
              <Printer className="w-4 h-4" />
              {t('archivePage.print')}
            </Button>
          )}
          <Button
            variant={printable ? 'secondary' : 'primary'}
            onClick={slice}
            disabled={sliceBlocked !== undefined}
            title={sliceBlocked}
          >
            {useSlicerApi ? <Cog className="w-4 h-4" /> : <ExternalLink className="w-4 h-4" />}
            {t('archivePage.slice')}
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
          <button
            ref={menuButtonRef}
            type="button"
            aria-label={t('archivePage.more')}
            aria-haspopup="menu"
            aria-expanded={menu != null}
            onClick={() => {
              if (menu) {
                setMenu(null);
                return;
              }
              const rect = menuButtonRef.current?.getBoundingClientRect();
              setMenu({ x: rect ? rect.left : 0, y: rect ? rect.bottom + 4 : 0 });
            }}
            className="inline-flex items-center justify-center w-11 min-h-[44px] md:min-h-0 md:h-9 rounded-lg bg-bambu-dark-tertiary hover:bg-bambu-gray-dark text-white transition-colors focus:outline-none focus:ring-2 focus:ring-bambu-gray"
          >
            <MoreHorizontal className="w-4 h-4" />
          </button>
          {/* Next to its button, so Tab moves from the button into it. */}
          {menu && (
            <ContextMenu
              x={menu.x}
              y={menu.y}
              items={menuItems}
              anchorRef={menuButtonRef}
              onClose={() => {
                setMenu(null);
                menuButtonRef.current?.focus();
              }}
            />
          )}
        </div>
      </header>

      <Section title={t('archivePage.fileTitle')}>
        <Card>
          <CardContent className="p-4">
            <dl className="grid grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-3 text-sm">
              {fileFacts.map((fact) => (
                <div key={fact.label} className="min-w-0">
                  <dt className="text-bambu-gray">{fact.label}</dt>
                  <dd className="text-white truncate">{fact.value}</dd>
                </div>
              ))}
              {archive.makerworld_url && (
                <div className="min-w-0">
                  <dt className="text-bambu-gray">{t('archivePage.source')}</dt>
                  <dd>
                    <a href={archive.makerworld_url} target="_blank" rel="noopener noreferrer" className="text-bambu-green hover:underline">
                      MakerWorld
                    </a>
                  </dd>
                </div>
              )}
              {tags.length > 0 && (
                <div className="min-w-0">
                  <dt className="text-bambu-gray">{t('archivePage.tags')}</dt>
                  <dd className="flex flex-wrap gap-1 mt-0.5">
                    {tags.map((tag) => (
                      <span key={tag} className="text-xs px-2 py-0.5 rounded bg-bambu-dark text-bambu-gray-light">
                        {tag}
                      </span>
                    ))}
                  </dd>
                </div>
              )}
            </dl>
          </CardContent>
        </Card>
      </Section>

      {plates.length > 1 && (
        <Section title={t('archivePage.plates')}>
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,20rem),1fr))] gap-4">
            {plates.map((plate) => (
              <PlateCard
                key={plate.index}
                archiveId={archive.id}
                plate={plate}
                runs={runs.filter((r) => r.plate_id === plate.index)}
                printable={printable}
                printBlocked={printBlocked}
                onMedia={setMedia}
                mediaOf={mediaOf}
                name={name}
                onPrint={() => setReprintPlate(plate.index)}
                onShowPrints={() => {
                  setPlateFilter(plate.index);
                  printsRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
                }}
              />
            ))}
          </ul>
        </Section>
      )}

      <section ref={printsRef} className="space-y-3 scroll-mt-4">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="mr-3 text-sm font-semibold uppercase tracking-wider text-bambu-gray">{t('archivePage.printsTitle')}</h3>
          {plates.length > 1 &&
            [null, ...plates.map((p) => p.index)].map((index) => (
              <button
                key={index ?? 'all'}
                type="button"
                aria-pressed={plateFilter === index}
                onClick={() => setPlateFilter(index)}
                className={`h-9 px-3.5 rounded-full border text-sm transition-colors ${
                  plateFilter === index
                    ? 'border-bambu-green bg-bambu-green/20 text-bambu-green'
                    : 'border-bambu-dark-tertiary bg-bambu-dark-secondary text-white hover:border-bambu-gray'
                }`}
              >
                {index == null ? t('archivePage.allPlates') : t('archives.card.plateNumber', { index })}
              </button>
            ))}
        </div>
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
        ) : shownRuns.length === 0 ? (
          <p className="text-sm text-bambu-gray italic">{t('archivePage.noPrintsOfPlate')}</p>
        ) : (
          <div className="rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary">
            <div>
              <div
                className={`hidden ${showPlate ? ROW_COLUMNS_WITH_PLATE : ROW_COLUMNS} gap-3 px-4 py-3 text-xs uppercase tracking-wide text-bambu-gray border-b border-bambu-dark-tertiary`}
              >
                <span>{t('archivePage.colDate')}</span>
                {showPlate && <span>{t('archivePage.colPlate')}</span>}
                <span>{t('archivePage.colPrinter')}</span>
                <span>{t('archivePage.colResult')}</span>
                <span>{t('archivePage.colTime')}</span>
                <span>{t('archivePage.colGrams')}</span>
                <span>{t('archivePage.colCost')}</span>
                <span>{t('archivePage.colFilament')}</span>
              </div>
              {shownRuns.map((run) => (
                <RunRow
                  key={run.id}
                  run={run}
                  archiveId={archive.id}
                  name={name}
                  showPlate={showPlate}
                  open={openRun === run.id}
                  onToggle={() => setOpenRun(openRun === run.id ? null : run.id)}
                  highlighted={run.id === highlightRun}
                  itemRef={run.id === highlightRun ? highlightRef : undefined}
                  timeFormat={timeFormat}
                  currency={currency}
                  canAnswer={canUpdate}
                  printable={printable}
                  printBlocked={printBlocked}
                  media={mediaOf(run)}
                  onConfirm={() => setConfirmRun(run.id)}
                  onReprint={() => setReprintPlate(run.plate_id ?? archive.plate_id)}
                  onMedia={setMedia}
                />
              ))}
            </div>
          </div>
        )}
      </section>

      {showEdit && <EditArchiveModal archive={archive} onClose={() => setShowEdit(false)} />}
      {reprintPlate !== undefined && (
        <PrintModal
          mode="create"
          archiveId={archive.id}
          archiveName={name}
          preselectPlateId={reprintPlate}
          onClose={() => setReprintPlate(undefined)}
        />
      )}
      {showSlice && (
        <SliceModal source={{ kind: 'archive', id: archive.id, filename: name || 'model' }} onClose={() => setShowSlice(false)} />
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
    </ModalShell>
  );
}

/** The overlay and panel; a click on the overlay outside the panel closes it. */
function ModalShell({ onClose, label, children }: { onClose: () => void; label: string; children: React.ReactNode }) {
  const { t } = useTranslation();
  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center p-2 sm:p-4 overflow-y-auto"
      onMouseDown={(e) => {
        // The overlay scrolls a tall archive: a press on its scrollbar
        // (right of its content box) scrolls rather than closes.
        if (e.target === e.currentTarget && e.clientX < e.currentTarget.clientWidth) onClose();
      }}
      data-testid="archive-modal-backdrop"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={label}
        className="relative w-full max-w-[1600px] min-w-0 my-auto bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl p-4 md:p-6 space-y-7"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label={t('archivePage.close')}
          className="absolute top-3 right-3 p-2 rounded-lg text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary transition-colors"
        >
          <X className="w-5 h-5" />
        </button>
        {children}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-bambu-gray">{title}</h3>
      {children}
    </section>
  );
}

function Chip({ className = 'bg-bambu-dark text-bambu-gray-light', children }: { className?: string; children: React.ReactNode }) {
  return <span className={`px-2.5 py-1 rounded-full ${className}`}>{children}</span>;
}

function PlateCard({
  archiveId,
  plate,
  runs,
  printable,
  printBlocked,
  onMedia,
  mediaOf,
  name,
  onPrint,
  onShowPrints,
}: {
  archiveId: number;
  plate: PlateMetadata;
  runs: PrintLogEntry[];
  printable: boolean;
  printBlocked?: string;
  onMedia: (media: Media) => void;
  mediaOf: (run: PrintLogEntry) => RunMedia;
  name: string;
  onPrint: () => void;
  onShowPrints: () => void;
}) {
  const { t } = useTranslation();
  // Named as its plate buttons and its prints' plate column name it; the
  // plate's own name defaults to its first object, which the list shows.
  const label = t('archives.card.plateNumber', { index: plate.index });
  const contents = plate.objects.length > 0 ? plate.objects.join(', ') : plate.name;
  // Its prints' finish photos, newest first, and the newest print's video.
  const photos = [...new Set(runs.flatMap((r) => mediaOf(r).photos))];
  const video = runs.map((r) => mediaOf(r).video).find(Boolean);
  const good = runs.filter((r) => r.status === 'completed').length;
  const failed = runs.filter((r) => FAILED.has(r.status)).length;
  // The newest finished print with a measured time, against this plate's estimate.
  const last = runs.find((r) => r.status === 'completed' && r.duration_seconds && !r.duration_unmeasured);
  const lastDelta =
    last?.duration_seconds && plate.print_time_seconds ? estimateDelta(last.duration_seconds, plate.print_time_seconds) : null;
  const estimate = [
    plate.print_time_seconds ? formatDuration(plate.print_time_seconds) : null,
    plate.filament_used_grams ? `${plate.filament_used_grams.toFixed(1)} g` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <li data-testid={`archive-plate-${plate.index}`}>
      <Card className="h-full">
        <CardContent className="p-4 flex gap-4">
          <div className="flex flex-col gap-2 shrink-0">
            <ThumbnailHoverPreview
              src={plate.has_thumbnail ? api.getArchivePlateThumbnail(archiveId, plate.index) : null}
              size={400}
              className="w-28 h-28 rounded-lg bg-bambu-dark overflow-hidden flex items-center justify-center"
            >
              {plate.has_thumbnail ? (
                <img src={api.getArchivePlateThumbnail(archiveId, plate.index)} alt={label} className="w-full h-full object-contain" />
              ) : (
                <Package className="w-8 h-8 text-bambu-gray/50" />
              )}
            </ThumbnailHoverPreview>
            {photos.length > 0 && (
              <button
                type="button"
                onClick={() => onMedia({ kind: 'photos', photos, title: name })}
                className="relative w-28 h-20 rounded-lg overflow-hidden bg-bambu-dark-tertiary"
                aria-label={t('archivePage.photos', { count: photos.length })}
              >
                <img src={api.getArchivePhotoUrl(archiveId, photos[0])} alt="" className="w-full h-full object-cover" />
                {photos.length > 1 && (
                  <span className="absolute bottom-1 right-1 text-xs px-1.5 rounded bg-black/70 text-white">{photos.length}</span>
                )}
              </button>
            )}
          </div>
          <div className="flex-1 min-w-0 space-y-1.5 text-sm">
            <p className="font-semibold text-white truncate" title={contents ?? undefined}>
              {label}
              {contents && <span className="font-normal text-bambu-gray"> · {contents}</span>}
            </p>
            {estimate && <p className="text-bambu-gray">{t('archivePage.plateEstimate', { value: estimate })}</p>}
            {plate.filaments.length > 0 && (
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-bambu-gray">
                {plate.filaments.map((f) => (
                  <span key={f.slot_id} className="flex items-center gap-1">
                    <span
                      className="w-3 h-3 rounded-full border border-white/20"
                      style={{ backgroundColor: f.color || undefined }}
                      aria-hidden="true"
                    />
                    {f.type}
                  </span>
                ))}
              </p>
            )}
            <p className="flex flex-wrap gap-x-3 text-bambu-gray">
              <span className="text-bambu-green">{t('archivePage.goodCount', { count: good })}</span>
              <span className="text-red-700 dark:text-red-400">{t('archivePage.failedCount', { count: failed })}</span>
              {lastDelta && <span>{t('archivePage.lastVsEstimate', { value: lastDelta })}</span>}
            </p>
            <div className="flex flex-wrap gap-2 pt-1">
              {video && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    onMedia({
                      kind: 'video',
                      src: video,
                      title: t('archives.modal.timelapse', { name }),
                    })
                  }
                >
                  <Film className="w-3.5 h-3.5" />
                  {t('archivePage.video')}
                </Button>
              )}
              {printable && (
                <Button size="sm" onClick={onPrint} disabled={printBlocked !== undefined} title={printBlocked}>
                  <Printer className="w-3.5 h-3.5" />
                  {t('archivePage.printThisPlate')}
                </Button>
              )}
              <Button size="sm" variant="secondary" onClick={onShowPrints} disabled={runs.length === 0}>
                {t('archivePage.showPlatePrints')}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </li>
  );
}

function RunRow({
  run,
  archiveId,
  name,
  showPlate,
  open,
  onToggle,
  highlighted,
  itemRef,
  timeFormat,
  currency,
  canAnswer,
  printable,
  printBlocked,
  media,
  onConfirm,
  onReprint,
  onMedia,
}: {
  run: PrintLogEntry;
  archiveId: number;
  name: string;
  showPlate: boolean;
  open: boolean;
  onToggle: () => void;
  highlighted: boolean;
  itemRef?: React.Ref<HTMLDivElement>;
  timeFormat: TimeFormat;
  currency: string;
  canAnswer: boolean;
  printable: boolean;
  printBlocked?: string;
  media: RunMedia;
  onConfirm: () => void;
  onReprint: () => void;
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
  const pending = run.status === 'completed' && run.confirm_requested && run.user_verdict == null;
  const detailsId = `archive-run-${run.id}-details`;

  // Next to the slicer's figures for the plate it printed (#3375): a finished
  // print by how far it ran from them, one that stopped by how far it got. A
  // time that was not measured gets no percentage, as in the time accuracy.
  const duration = run.duration_seconds ? `${run.duration_unmeasured ? '~' : ''}${formatDuration(run.duration_seconds)}` : null;
  let time = duration ?? '—';
  if (duration && run.estimate_seconds) {
    const estimate = formatDuration(run.estimate_seconds);
    if (run.status !== 'completed') {
      time = t('archivePage.timeOf', { value: duration, estimate });
    } else if (run.duration_unmeasured) {
      time = t('archivePage.withEstimate', { value: duration, estimate });
    } else {
      time = t('archivePage.timeVsEstimate', {
        value: duration,
        estimate,
        delta: estimateDelta(run.duration_seconds!, run.estimate_seconds),
      });
    }
  }
  const grams =
    run.filament_used_grams != null
      ? run.estimate_grams
        ? t('archivePage.withEstimate', { value: `${run.filament_used_grams.toFixed(1)} g`, estimate: `${run.estimate_grams.toFixed(1)} g` })
        : `${run.filament_used_grams.toFixed(1)} g`
      : '—';
  // Its filament as the print log shows it: a dot per colour, then the types.
  // Its photos and video are in its details.
  const colors = (run.filament_color ?? '').split(',').map((c) => c.trim()).filter(Boolean);
  const types = [...new Set((run.filament_type ?? '').split(',').map((c) => c.trim()).filter(Boolean))];

  return (
    <div
      ref={itemRef}
      data-testid={`archive-run-${run.id}`}
      className={`border-b border-bambu-dark-tertiary last:border-b-0 ${highlighted ? 'ring-2 ring-inset ring-bambu-green' : ''}`}
    >
      <div
        onClick={onToggle}
        className={`flex flex-wrap gap-x-4 gap-y-1.5 ${showPlate ? ROW_COLUMNS_WITH_PLATE : ROW_COLUMNS} xl:gap-3 px-4 py-3 items-center text-sm text-white cursor-pointer ${
          open ? 'bg-bambu-dark-tertiary/60' : 'hover:bg-bambu-dark-tertiary/30'
        }`}
      >
        <span className="min-w-0 basis-full xl:basis-auto">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={detailsId}
            onClick={(e) => {
              e.stopPropagation();
              onToggle();
            }}
            className="flex items-center gap-1.5 text-left font-medium hover:text-bambu-green"
          >
            {open ? <ChevronDown className="w-4 h-4 shrink-0" /> : <ChevronRight className="w-4 h-4 shrink-0" />}
            {formatDateTime(run.started_at || run.created_at, timeFormat)}
          </button>
        </span>
        {showPlate && (
          <span>{run.plate_id != null ? t('archives.card.plateNumber', { index: run.plate_id }) : '—'}</span>
        )}
        <span className="truncate">
          {run.printer_name || t('archivePage.noPrinter')}
        </span>
        <span className="flex flex-wrap items-center gap-1.5">
          <span className={`text-xs px-2 py-0.5 rounded font-medium ${RUN_STATUS_CLASSES[run.status] ?? 'bg-bambu-dark text-bambu-gray'}`}>
            {t(`archives.runLog.status.${run.status}`, { defaultValue: run.status })}
          </span>
          {run.user_verdict === 'good' && (
            <span
              className="text-xs px-1.5 py-0.5 rounded bg-bambu-green/80 text-white flex items-center gap-1"
              title={`${t('archives.card.confirmedGoodTitle')}${verdictHint ? ` — ${t(verdictHint)}` : ''}`}
            >
              <ThumbsUp className="w-3 h-3" />
              {t('archives.card.confirmedGood')}
            </span>
          )}
          {run.user_verdict === 'reject' && (
            <span
              className="text-xs px-1.5 py-0.5 rounded bg-status-error/80 text-white flex items-center gap-1"
              title={`${t('archives.card.rejectedTitle')}${verdictHint ? ` — ${t(verdictHint)}` : ''}`}
            >
              <ThumbsDown className="w-3 h-3" />
              {t('archives.card.rejected')}
            </span>
          )}
          {pending && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onConfirm();
              }}
              disabled={!canAnswer}
              title={!canAnswer ? t('archives.permission.noUpdateArchives') : undefined}
              className="text-xs px-2 py-0.5 rounded bg-amber-500/90 enabled:hover:bg-amber-500 text-black flex items-center gap-1"
            >
              <ThumbsUp className="w-3 h-3" />
              {t('archives.card.confirmPending')}
            </button>
          )}
        </span>
        <span>{time}</span>
        <span>{grams}</span>
        <span>{run.cost != null ? `${currency}${run.cost.toFixed(2)}` : '—'}</span>
        <span className="flex items-center gap-2 min-w-0 text-bambu-gray-light" data-testid="run-filament">
          {colors.length > 0 && (
            <span className="flex items-center gap-0.5 flex-wrap shrink-0">
              {colors.map((color, i) => (
                <span
                  key={i}
                  className="w-3 h-3 rounded-full border border-black/20"
                  style={{ backgroundColor: color.startsWith('#') ? color : undefined }}
                  title={color}
                />
              ))}
            </span>
          )}
          <span className="truncate">{types.join(', ') || (colors.length === 0 ? '—' : '')}</span>
        </span>
      </div>

      {open && (
        <div id={detailsId} className="px-4 pt-2 pb-5 bg-bambu-dark/60 flex flex-wrap gap-6">
          {(media.video || media.photos.length > 0) && (
            <div className="flex flex-wrap gap-2.5">
              {media.photos.slice(0, 3).map((photo) => (
                <button
                  key={photo}
                  type="button"
                  onClick={() => onMedia({ kind: 'photos', photos: media.photos, title: name })}
                  className="w-36 h-24 rounded-lg overflow-hidden bg-bambu-dark-tertiary"
                  aria-label={t('archivePage.photos', { count: media.photos.length })}
                >
                  <img src={api.getArchivePhotoUrl(archiveId, photo)} alt="" className="w-full h-full object-cover" />
                </button>
              ))}
              {media.video && (
                <button
                  type="button"
                  onClick={() => onMedia({ kind: 'video', src: media.video!, title: t('archives.modal.timelapse', { name }) })}
                  className="w-36 h-24 rounded-lg bg-bambu-dark-tertiary hover:bg-bambu-dark-tertiary/70 flex flex-col items-center justify-center gap-1 text-xs text-bambu-gray-light"
                >
                  <Film className="w-5 h-5" />
                  {t('archivePage.video')}
                </button>
              )}
            </div>
          )}

          <dl className="flex-[1_1_22rem] grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 text-sm">
            <dt className="text-bambu-gray">{t('archivePage.startedEnded')}</dt>
            <dd className="text-white">
              {formatDateTime(run.started_at || run.created_at, timeFormat)}
              {run.completed_at && ` – ${formatDateTime(run.completed_at, timeFormat)}`}
            </dd>
            {run.energy_kwh != null && (
              <>
                <dt className="text-bambu-gray">{t('archivePage.energy')}</dt>
                <dd className="text-white">
                  {`${run.energy_kwh.toFixed(2)} kWh`}
                  {run.energy_cost != null && ` · ${currency}${run.energy_cost.toFixed(2)}`}
                </dd>
              </>
            )}
            {run.failure_reason && (
              <>
                <dt className="text-bambu-gray">{t('archivePage.failureReason')}</dt>
                <dd className="text-red-700 dark:text-red-400">
                  {t(`editArchive.failureReasons.${run.failure_reason}`, { defaultValue: run.failure_reason })}
                </dd>
              </>
            )}
            {run.created_by_username && (
              <>
                <dt className="text-bambu-gray">{t('archivePage.startedByLabel')}</dt>
                <dd className="text-white">{run.created_by_username}</dd>
              </>
            )}
            <dt className="text-bambu-gray">{t('archivePage.printNote')}</dt>
            <dd className="text-white">
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
              ) : (
                <p className="whitespace-pre-wrap break-words">{run.notes || '—'}</p>
              )}
            </dd>
          </dl>

          <div className="flex flex-col gap-2">
            {run.status === 'completed' && (
              <Button
                size="sm"
                variant="secondary"
                onClick={onConfirm}
                disabled={!canAnswer}
                title={!canAnswer ? t('archives.permission.noUpdateArchives') : undefined}
              >
                <ThumbsUp className="w-3.5 h-3.5" />
                {run.user_verdict ? t('archivePage.changeOutcome') : t('archivePage.setOutcome')}
              </Button>
            )}
            {!editingNote && canEditNote && (
              <Button size="sm" variant="secondary" onClick={() => setEditingNote(true)}>
                <StickyNote className="w-3.5 h-3.5" />
                {run.notes ? t('archivePage.editNote') : t('archivePage.addNote')}
              </Button>
            )}
            {printable && (
              <Button size="sm" variant="secondary" onClick={onReprint} disabled={printBlocked !== undefined} title={printBlocked}>
                <Printer className="w-3.5 h-3.5" />
                {t('archivePage.reprintThis')}
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
