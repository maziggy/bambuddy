import { useEffect, useMemo, useState } from 'react';
import DOMPurify from 'dompurify';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertCircle, ArrowRight, Check, ChevronLeft, ChevronRight, Download, ExternalLink, FolderOpen, Globe, Images, Loader2, Trash2, X } from 'lucide-react';

import {
  api,
  type MakerworldImportResponse,
  type MakerworldRecentImport,
  type MakerworldResolvedModel,
} from '../api/client';
import { openInSlicer, type SlicerType } from '../utils/slicer';
import { Button } from '../components/Button';
import { Card, CardContent, CardHeader } from '../components/Card';
import { ConfirmModal } from '../components/ConfirmModal';
import { SliceModal, type SliceSource } from '../components/SliceModal';
import { Cog } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';

// MakerWorld's API payloads are passed through as opaque dicts; these helpers
// pull known fields out in a type-safe way so a missing/renamed field shows
// up as an empty string rather than crashing the render.
function pickString(obj: Record<string, unknown> | undefined, key: string): string {
  const value = obj?.[key];
  return typeof value === 'string' ? value : '';
}

// Rewrite MakerWorld CDN URLs inside HTML content (design summary, etc.) to
// use Bambuddy's thumbnail proxy. MakerWorld summaries are authored HTML and
// commonly contain ``<img src="https://makerworld.bblmw.com/...">`` tags;
// Bambuddy's img-src CSP only allows ``'self' data: blob:``, so these would
// otherwise be blocked. Pairs with ``proxyCdn`` below for explicit <img>
// renders.
function proxyCdnUrlsInHtml(html: string): string {
  return html.replace(
    /(https?:\/\/(?:makerworld|public-cdn)\.bblmw\.com\/[^\s"']+)/gi,
    (match) => `/api/v1/makerworld/thumbnail?url=${encodeURIComponent(match)}`,
  );
}

// MakerWorld CDN images can't be hotlinked — Bambuddy's img-src CSP blocks
// external hosts. Route them through the /makerworld/thumbnail proxy.
// Empty string in → empty string out so the ``{coverUrl && ...}`` checks
// in the render keep short-circuiting.
function proxyCdn(url: string): string {
  if (!url) return '';
  if (!/^https?:\/\/(makerworld|public-cdn)\.bblmw\.com\//i.test(url)) return url;
  return `/api/v1/makerworld/thumbnail?url=${encodeURIComponent(url)}`;
}
function pickNumber(obj: Record<string, unknown> | undefined, key: string): number | null {
  const value = obj?.[key];
  return typeof value === 'number' ? value : null;
}
function pickObject(obj: Record<string, unknown> | undefined, key: string): Record<string, unknown> | undefined {
  const value = obj?.[key];
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

// Depth-first flatten of the library folder tree so it can be rendered in a
// single <select>. Each entry carries its ``depth`` so the UI can indent the
// option label.
type FlatFolder = { folder: import('../api/client').LibraryFolderTree; depth: number };
function flattenFolderTree(
  tree: import('../api/client').LibraryFolderTree,
  depth = 0,
  out: FlatFolder[] = [],
): FlatFolder[] {
  out.push({ folder: tree, depth });
  for (const child of tree.children ?? []) {
    flattenFolderTree(child, depth + 1, out);
  }
  return out;
}

// Time-based phase heuristic for the import progress indicator. The backend
// does the work as one synchronous HTTP request (no streaming progress), so
// we guess the phase from elapsed wall-clock time. These numbers reflect
// typical 3MF downloads (5–30 s total, dominated by the S3 GET):
//   0–1 s:  metadata fetch (fast, just the iot-service + design lookups)
//   1–<end> s: downloading the 3MF bytes
//   The last moment also flashes "Saving…" but we can't actually observe
//   the save step on the wire, so we let the download phase run until the
//   mutation resolves.
function phaseLabelForElapsed(elapsedSec: number, t: (k: string) => string): string {
  if (elapsedSec < 1) return t('makerworld.phaseResolving');
  return t('makerworld.phaseDownloading');
}

function useElapsedSeconds(active: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!active) {
      setElapsed(0);
      return;
    }
    const start = Date.now();
    const tick = () => setElapsed(Math.floor((Date.now() - start) / 1000));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return elapsed;
}

export function MakerworldPage() {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const canImport = hasPermission('makerworld:import');

  const [urlInput, setUrlInput] = useState('');
  const [resolved, setResolved] = useState<MakerworldResolvedModel | null>(null);
  // Selected target folder. ``null`` means "let the backend use the default
  // MakerWorld folder" (auto-created if missing). Any other value is the id
  // of a user-selected folder; external read-only folders are filtered out
  // of the picker because the backend rejects those with 403.
  const [selectedFolderId, setSelectedFolderId] = useState<number | null>(null);
  // Bulk-import progress. ``null`` when idle; ``{current, total}`` while
  // the "Import all" button is walking through ``instances[]``.
  const [bulkProgress, setBulkProgress] = useState<{ current: number; total: number } | null>(null);
  // Pending delete confirmation. ``null`` when no modal is open; otherwise
  // carries the ids/filename needed to run the delete when the user confirms.
  // Kept separate from the mutation state so the modal renders as soon as the
  // user clicks the trash icon, not only while the request is in flight.
  const [pendingDelete, setPendingDelete] = useState<
    | { libraryFileId: number; profileId: number; filename: string }
    | null
  >(null);
  // Lightbox state for the image gallery. When ``null`` the lightbox is closed.
  // ``images`` is the set of {name, url} captured at click-time (we don't mutate
  // it while the lightbox is open, so navigation is stable even if the underlying
  // instance array changes underneath).
  const [lightbox, setLightbox] = useState<
    | { images: Array<{ name: string; url: string }>; index: number }
    | null
  >(null);
  // Which URL the current ``resolved`` state was fetched for. When the user
  // edits ``urlInput`` away from this, we clear ``resolved`` — otherwise the
  // stale preview stays on screen and the Import button would submit the
  // *previous* model_id, dedupe'ing against the wrong row.
  const [resolvedForUrl, setResolvedForUrl] = useState<string>('');
  // All successful imports done during this resolved-model session, keyed
  // by the plate's ``profileId``. Used to render inline 'View in Library'
  // / 'Open in slicer' buttons directly on each imported plate row so the
  // user sees the follow-up actions right where they clicked (instead of
  // having to scroll back to a top-of-page card). Cleared when the user
  // resolves a fresh URL or edits the pasted URL.
  const [importsByProfile, setImportsByProfile] = useState<
    Record<number, MakerworldImportResponse>
  >({});

  const statusQuery = useQuery({
    queryKey: ['makerworld-status'],
    queryFn: () => api.getMakerworldStatus(),
  });

  const foldersQuery = useQuery({
    queryKey: ['library-folders'],
    queryFn: () => api.getLibraryFolders(),
  });

  const recentQuery = useQuery({
    queryKey: ['makerworld-recent-imports'],
    queryFn: () => api.getMakerworldRecentImports(10),
  });

  const settingsQuery = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.getSettings(),
  });
  // MakerWorld plates are unsliced project files — they can't be sent
  // directly to a printer. The "slice in slicer" action below imports the
  // 3MF and hands it to the user's configured slicer; from there the
  // slicer's own "send to printer" flow takes over.
  const preferredSlicer: SlicerType = settingsQuery.data?.preferred_slicer || 'bambu_studio';
  const preferredSlicerName =
    preferredSlicer === 'orcaslicer' ? 'OrcaSlicer' : 'Bambu Studio';
  const useSlicerApi = settingsQuery.data?.use_slicer_api ?? false;

  // Slice-via-API modal source. When set, the SliceModal is shown for the
  // referenced library file; it covers MakerWorld's "Slice in <Slicer>" /
  // "Open in Slicer" actions whenever the user has Use Slicer API enabled.
  const [sliceModalSource, setSliceModalSource] = useState<SliceSource | null>(null);

  const openSliceForLibraryFile = (libraryFileId: number, filename: string) => {
    setSliceModalSource({ kind: 'libraryFile', id: libraryFileId, filename });
  };

  const resolveMutation = useMutation({
    mutationFn: (url: string) => api.resolveMakerworldUrl(url),
    onSuccess: (data, url) => {
      setResolved(data);
      setResolvedForUrl(url);
      // Fresh resolve — clear any success card from a previous model.
      setImportsByProfile({});
    },
    onError: (err: Error) => showToast(err.message || t('makerworld.errors.resolveFailed'), 'error'),
  });

  // URL-change detection: if the user edits the URL input away from what
  // ``resolved`` was fetched for, drop the stale preview so they can't
  // accidentally import the previous model. Whitespace-only differences
  // don't count.
  useEffect(() => {
    if (resolved !== null && urlInput.trim() !== resolvedForUrl.trim()) {
      setResolved(null);
      setResolvedForUrl('');
      setImportsByProfile({});
    }
  }, [urlInput, resolved, resolvedForUrl]);

  const importMutation = useMutation({
    mutationFn: ({ instanceId, profileId }: { instanceId: number; profileId: number | null }) =>
      api.importMakerworldInstance(resolved?.model_id ?? 0, instanceId, profileId, selectedFolderId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      // Backend auto-creates a "MakerWorld" folder on first import; refresh
      // the folder tree so users see it without having to reload the page.
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      // Track by profile_id so each plate's row can render its own inline
      // follow-up buttons even after multiple imports in the same session.
      if (data.profile_id) {
        setImportsByProfile((prev) => ({ ...prev, [data.profile_id!]: data }));
      }
      showToast(
        data.was_existing ? t('makerworld.alreadyInLibrary') : t('makerworld.importSuccess', { filename: data.filename }),
        'success',
      );
    },
    onError: (err: Error) => showToast(err.message || t('makerworld.errors.downloadFailed'), 'error'),
  });

  // "Print Now" is a two-step mutation: import to library, then open the
  // existing PrintModal. We chain manually rather than composing mutations
  // so the modal gets the library_file_id the moment it lands.
  // Per-plate delete: removes a previously-imported plate from the library
  // (file + DB row). Used by the inline trash-icon button on imported plates
  // so users can quickly undo an accidental import without navigating to
  // File Manager. ``profileId`` is only used for local state cleanup.
  const deleteImportMutation = useMutation({
    mutationFn: ({ libraryFileId }: { libraryFileId: number; profileId: number }) =>
      api.deleteLibraryFile(libraryFileId),
    onSuccess: (_data, { profileId }) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      queryClient.invalidateQueries({ queryKey: ['makerworld-recent-imports'] });
      setImportsByProfile((prev) => {
        const next = { ...prev };
        delete next[profileId];
        return next;
      });
      setPendingDelete(null);
      showToast(t('makerworld.importDeleted'), 'success');
    },
    onError: (err: Error) => {
      setPendingDelete(null);
      showToast(err.message || t('makerworld.errors.deleteFailed'), 'error');
    },
  });

  // "Slice in BambuStudio / OrcaSlicer" — imports the plate then hands the
  // file off to the configured slicer. MakerWorld plates are unsliced source
  // files, so we can't send them straight to the printer; the slicer is the
  // user's actual "I want to print this" destination. Mirrors MakerWorld's
  // own "Download and Open" button behaviour.
  const sliceMutation = useMutation({
    mutationFn: ({ instanceId, profileId }: { instanceId: number; profileId: number | null }) =>
      api.importMakerworldInstance(resolved?.model_id ?? 0, instanceId, profileId, selectedFolderId),
    onSuccess: async (data: MakerworldImportResponse) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      queryClient.invalidateQueries({ queryKey: ['makerworld-recent-imports'] });
      if (data.profile_id) {
        setImportsByProfile((prev) => ({ ...prev, [data.profile_id!]: data }));
      }
      // After import, branch on the user's slicer-API preference: API mode
      // opens the in-app SliceModal; URI mode hands the file off to the
      // local slicer GUI (the historical behavior).
      if (useSlicerApi) {
        openSliceForLibraryFile(data.library_file_id, data.filename);
      } else {
        await handleOpenInSlicer(data.library_file_id, data.filename, preferredSlicer);
      }
    },
    onError: (err: Error) => showToast(err.message || t('makerworld.errors.downloadFailed'), 'error'),
  });

  // Tick while an import is in-flight so we can show "Downloading… (12 s)"
  // instead of a bare spinner. Only one import runs at a time (bulk is
  // sequential), so a single counter covers both the per-row button label
  // and the bulk-import progress label.
  const importElapsed = useElapsedSeconds(importMutation.isPending || sliceMutation.isPending);
  const importPhaseLabel = phaseLabelForElapsed(importElapsed, t);

  const handleResolve = (e?: React.FormEvent) => {
    e?.preventDefault();
    const trimmed = urlInput.trim();
    if (!trimmed) return;
    resolveMutation.mutate(trimmed);
  };

  // Keyboard navigation for the lightbox (Escape closes, arrows navigate).
  useEffect(() => {
    if (!lightbox) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setLightbox(null);
      else if (e.key === 'ArrowLeft') {
        setLightbox((prev) => (prev && prev.index > 0 ? { ...prev, index: prev.index - 1 } : prev));
      } else if (e.key === 'ArrowRight') {
        setLightbox((prev) =>
          prev && prev.index < prev.images.length - 1 ? { ...prev, index: prev.index + 1 } : prev,
        );
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [lightbox]);

  // Extract the gallery images for a plate. MakerWorld returns an ``instance.pictures``
  // array of {name, url, isRealLifePhoto}; falls back to the single ``cover`` URL
  // when pictures is empty so the lightbox still shows something.
  const getInstanceImages = (inst: Record<string, unknown>): Array<{ name: string; url: string }> => {
    const pictures = Array.isArray(inst['pictures']) ? (inst['pictures'] as unknown[]) : [];
    const fromPictures = pictures
      .filter((p): p is Record<string, unknown> => p !== null && typeof p === 'object')
      .map((p) => ({ name: pickString(p, 'name') || 'image', url: pickString(p, 'url') }))
      .filter((p) => p.url);
    if (fromPictures.length > 0) return fromPictures;
    const cover = pickString(inst, 'cover');
    return cover ? [{ name: 'cover', url: cover }] : [];
  };

  // "Import all plates" — walks through ``instances[]`` sequentially (not
  // in parallel) so we don't hammer the Bambu API. Skips plates that have
  // already been imported in this session. On per-plate failure, shows the
  // error toast but continues with the next plate (partial success is
  // better than a whole-batch abort).
  const handleImportAll = async () => {
    if (!resolved) return;
    const plates = resolved.instances.filter((inst) => {
      const pid = pickNumber(inst, 'profileId');
      return pid !== null && !importsByProfile[pid];
    });
    if (plates.length === 0) return;

    setBulkProgress({ current: 0, total: plates.length });
    try {
      for (let i = 0; i < plates.length; i += 1) {
        const inst = plates[i];
        const instanceId = pickNumber(inst, 'id');
        const profileId = pickNumber(inst, 'profileId');
        if (instanceId === null || profileId === null) continue;
        setBulkProgress({ current: i + 1, total: plates.length });
        try {
          await importMutation.mutateAsync({ instanceId, profileId });
        } catch {
          // Per-plate failure already surfaces a toast via ``onError``; we
          // just continue so a flaky single profile doesn't kill the batch.
        }
      }
    } finally {
      setBulkProgress(null);
    }
  };

  const handleOpenInSlicer = async (
    fileId: number,
    filename: string,
    slicer: 'bambu_studio' | 'orcaslicer',
  ) => {
    // Slicer protocol handlers can't send Authorization headers, so we mint a
    // short-lived single-use path-embedded token and hand the slicer that URL
    // instead of the auth-gated /download endpoint. Mirrors ArchivesPage's
    // ``openInSlicerWithToken`` pattern.
    try {
      const { token } = await api.createLibrarySlicerToken(fileId);
      const path = api.getLibrarySlicerDownloadUrl(fileId, token, filename);
      openInSlicer(`${window.location.origin}${path}`, slicer);
    } catch {
      // Auth-disabled fallback — the plain download URL is already public
      // in that case.
      const path = api.getLibraryFileDownloadUrl(fileId);
      openInSlicer(`${window.location.origin}${path}`, slicer);
    }
  };

  const design = resolved?.design;
  const creator = pickObject(design, 'designCreator');
  const instances = resolved?.instances ?? [];
  const alreadyImported = (resolved?.already_imported_library_ids.length ?? 0) > 0;

  const hasToken = statusQuery.data?.has_cloud_token ?? false;
  // Only block Print Now / Import actions on an import-capable login.
  // Browse/resolve works anonymously.
  const canDownload = statusQuery.data?.can_download ?? false;

  const coverUrl = useMemo(() => pickString(design, 'coverUrl'), [design]);
  const title = pickString(design, 'title');
  const summaryHtml = pickString(design, 'summary');
  const license = pickString(design, 'license');
  const downloadCount = pickNumber(design, 'downloadCount');

  return (
    <div className="p-6 max-w-screen-2xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <Globe className="w-7 h-7 text-brand-500" />
        <h1 className="text-2xl font-bold">{t('makerworld.title')}</h1>
      </div>

      <p className="text-sm text-gray-600 dark:text-gray-400">
        {t('makerworld.description')}
      </p>

      {/* Two-column layout: main flow on the left, sticky "Recent imports"
          sidebar on the right at lg+. Collapses to single column on narrow
          screens (tablet/phone), with the sidebar tucked below the main flow. */}
      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <div className="space-y-6 min-w-0">
      {!hasToken && (
        <Card className="border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20">
          <CardContent>
            <div className="flex items-start gap-3 py-2">
              <AlertCircle className="w-5 h-5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
              <div className="text-sm">
                <p className="font-medium text-amber-900 dark:text-amber-100">
                  {t('makerworld.signInRequiredTitle')}
                </p>
                <p className="text-amber-800 dark:text-amber-200 mt-1">
                  {t('makerworld.signInRequiredBody')}{' '}
                  <Link to="/settings?tab=cloud" className="underline">
                    {t('makerworld.openCloudSettings')}
                  </Link>
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <h2 className="text-lg font-semibold">{t('makerworld.pasteUrlHeader')}</h2>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleResolve} className="flex gap-2">
            <input
              type="text"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              placeholder={t('makerworld.pasteUrlPlaceholder')}
              className="flex-1 min-w-0 px-3 py-2 border rounded bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-700"
              autoComplete="off"
            />
            <Button
              type="submit"
              variant="primary"
              disabled={!urlInput.trim() || resolveMutation.isPending}
            >
              {resolveMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <ArrowRight className="w-4 h-4" />
              )}
              <span className="ml-2">{t('makerworld.resolveButton')}</span>
            </Button>
          </form>
        </CardContent>
      </Card>

      {resolved && (
        <Card>
          <CardContent>
            <div className="flex gap-4 py-2">
              {coverUrl && (
                <img
                  src={proxyCdn(coverUrl)}
                  alt={title}
                  className="w-32 h-32 object-cover rounded"
                  loading="lazy"
                />
              )}
              <div className="flex-1 min-w-0">
                <h3 className="text-xl font-semibold truncate">{title || t('makerworld.untitledModel')}</h3>
                {creator && (
                  <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                    {t('makerworld.byCreator', { name: pickString(creator, 'name') })}
                  </p>
                )}
                <div className="flex flex-wrap gap-3 mt-2 text-xs text-gray-500 dark:text-gray-400">
                  {downloadCount !== null && (
                    <span>{t('makerworld.downloadsCount', { count: downloadCount })}</span>
                  )}
                  {license && <span>{t('makerworld.licensePrefix')}: {license}</span>}
                  {alreadyImported && (
                    <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                      <Check className="w-3 h-3" /> {t('makerworld.alreadyImported')}
                    </span>
                  )}
                </div>
                {summaryHtml && (
                  <div
                    className="mt-3 text-sm prose prose-sm max-w-none dark:prose-invert line-clamp-3"
                    // Two-stage processing:
                    //   1. ``proxyCdnUrlsInHtml`` rewrites <img src="…bblmw.com…">
                    //      so CSP allows the image load.
                    //   2. ``DOMPurify.sanitize`` strips scripts, event handlers,
                    //      javascript: URLs, and other XSS vectors. MakerWorld
                    //      summaries are user-authored and cannot be trusted.
                    dangerouslySetInnerHTML={{
                      __html: DOMPurify.sanitize(proxyCdnUrlsInHtml(summaryHtml)),
                    }}
                  />
                )}
                {resolved && (
                  <a
                    href={`https://makerworld.com/models/${resolved.model_id}${resolved.profile_id ? `#profileId-${resolved.profile_id}` : ''}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-3 inline-flex items-center gap-1 text-xs text-brand-500 hover:underline"
                  >
                    <ExternalLink className="w-3 h-3" /> {t('makerworld.openOnMakerworld')}
                  </a>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {resolved && instances.length > 0 && (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-semibold">{t('makerworld.platesHeader', { count: instances.length })}</h2>
              <div className="flex flex-wrap items-center gap-2">
                <label className="text-xs text-gray-600 dark:text-gray-400">
                  {t('makerworld.importTo')}
                </label>
                <select
                  value={selectedFolderId ?? ''}
                  onChange={(e) => setSelectedFolderId(e.target.value ? Number(e.target.value) : null)}
                  className="text-sm px-2 py-1 border rounded bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-700"
                  disabled={bulkProgress !== null}
                >
                  <option value="">{t('makerworld.folderAuto')}</option>
                  {(foldersQuery.data ?? [])
                    .filter((f) => !(f.is_external && f.external_readonly))
                    .flatMap((f) => flattenFolderTree(f))
                    .map(({ folder, depth }) => (
                      <option key={folder.id} value={folder.id}>
                        {`${'— '.repeat(depth)}${folder.name}`}
                      </option>
                    ))}
                </select>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={
                    !canImport ||
                    !canDownload ||
                    bulkProgress !== null ||
                    importMutation.isPending ||
                    sliceMutation.isPending
                  }
                  onClick={handleImportAll}
                >
                  {bulkProgress !== null ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span className="ml-2">
                        {t('makerworld.importAllProgress', { current: bulkProgress.current, total: bulkProgress.total })}
                        {importElapsed > 0 && ` · ${importPhaseLabel} · ${importElapsed}s`}
                      </span>
                    </>
                  ) : (
                    <>
                      <Download className="w-4 h-4" />
                      <span className="ml-2">{t('makerworld.importAll')}</span>
                    </>
                  )}
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3">
              {instances.map((inst, idx) => {
                const instanceId = pickNumber(inst, 'id');
                const profileId = pickNumber(inst, 'profileId');
                const instanceTitle = pickString(inst, 'title');
                const cover = pickString(inst, 'cover');
                const materialCnt = pickNumber(inst, 'materialCnt');
                const needAms = inst?.['needAms'] === true;
                const downloadsOnInstance = pickNumber(inst, 'downloadCount');
                if (instanceId == null) return null;
                const isImporting = importMutation.isPending && importMutation.variables?.instanceId === instanceId;
                const isPrinting = sliceMutation.isPending && sliceMutation.variables?.instanceId === instanceId;
                const imported = profileId !== null ? importsByProfile[profileId] : undefined;
                return (
                  <div
                    key={instanceId}
                    className="flex flex-col gap-2 p-3 border rounded border-gray-200 dark:border-gray-700"
                  >
                    <div className="flex gap-3 items-center">
                      {(() => {
                        const gallery = getInstanceImages(inst);
                        const canOpen = gallery.length > 0;
                        return (
                          <button
                            type="button"
                            disabled={!canOpen}
                            onClick={() => canOpen && setLightbox({ images: gallery, index: 0 })}
                            className="relative w-16 h-16 shrink-0 rounded overflow-hidden group"
                            aria-label={t('makerworld.openGallery')}
                          >
                            {cover ? (
                              <img
                                src={proxyCdn(cover)}
                                alt=""
                                className="w-16 h-16 object-cover"
                                loading="lazy"
                              />
                            ) : (
                              <div className="w-16 h-16 bg-gray-100 dark:bg-gray-800" />
                            )}
                            {gallery.length > 1 && (
                              <span className="absolute bottom-0.5 right-0.5 bg-black/70 text-white text-[10px] px-1.5 py-0.5 rounded flex items-center gap-1">
                                <Images className="w-2.5 h-2.5" />
                                {gallery.length}
                              </span>
                            )}
                          </button>
                        );
                      })()}
                      <div className="flex-1 min-w-0">
                        <p className="font-medium truncate">
                          {instanceTitle || t('makerworld.plateDefaultName', { n: idx + 1 })}
                        </p>
                        <div className="flex flex-wrap gap-3 text-xs text-gray-500 dark:text-gray-400 mt-1">
                          {materialCnt !== null && (
                            <span>{t('makerworld.materialCount', { count: materialCnt })}</span>
                          )}
                          {needAms && <span>{t('makerworld.amsRequired')}</span>}
                          {downloadsOnInstance !== null && (
                            <span>{t('makerworld.downloadsCount', { count: downloadsOnInstance })}</span>
                          )}
                        </div>
                      </div>
                      <div className="flex gap-2 shrink-0">
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={!canImport || !canDownload || isImporting || isPrinting || bulkProgress !== null}
                          onClick={() => importMutation.mutate({ instanceId, profileId })}
                          title={!canDownload ? t('makerworld.signInRequiredTitle') : undefined}
                        >
                          {isImporting ? (
                            <>
                              <Loader2 className="w-4 h-4 animate-spin" />
                              <span className="ml-2">
                                {importPhaseLabel}
                                {importElapsed > 0 && ` · ${importElapsed}s`}
                              </span>
                            </>
                          ) : (
                            <>
                              <Download className="w-4 h-4" />
                              <span className="ml-2">{t('makerworld.importToLibrary')}</span>
                            </>
                          )}
                        </Button>
                        <Button
                          variant="primary"
                          size="sm"
                          disabled={!canImport || !canDownload || isImporting || isPrinting || bulkProgress !== null}
                          onClick={() => sliceMutation.mutate({ instanceId, profileId })}
                          title={!canDownload ? t('makerworld.signInRequiredTitle') : undefined}
                        >
                          {isPrinting ? (
                            <>
                              <Loader2 className="w-4 h-4 animate-spin" />
                              <span className="ml-2">
                                {importPhaseLabel}
                                {importElapsed > 0 && ` · ${importElapsed}s`}
                              </span>
                            </>
                          ) : (
                            <>
                              <ExternalLink className="w-4 h-4" />
                              <span className="ml-2">
                                {t('makerworld.sliceIn', { slicer: preferredSlicerName })}
                              </span>
                            </>
                          )}
                        </Button>
                      </div>
                    </div>
                    {imported && (
                      <div className="flex items-center gap-2 pl-20 text-xs">
                        <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400 shrink-0" />
                        <span className="text-emerald-700 dark:text-emerald-300">
                          {imported.was_existing
                            ? t('makerworld.lastImportAlreadyInLibrary')
                            : t('makerworld.lastImportSuccess')}
                        </span>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            const target = imported.folder_id
                              ? `/files?folder=${imported.folder_id}`
                              : '/files';
                            window.location.assign(target);
                          }}
                        >
                          <FolderOpen className="w-3.5 h-3.5" />
                          <span className="ml-1.5">{t('makerworld.viewInLibrary')}</span>
                        </Button>
                        {useSlicerApi ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => openSliceForLibraryFile(imported.library_file_id, imported.filename)}
                          >
                            <Cog className="w-3.5 h-3.5" />
                            <span className="ml-1.5">{t('slice.action', 'Slice')}</span>
                          </Button>
                        ) : (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                handleOpenInSlicer(imported.library_file_id, imported.filename, 'bambu_studio')
                              }
                            >
                              <ExternalLink className="w-3.5 h-3.5" />
                              <span className="ml-1.5">{t('makerworld.openInBambuStudio')}</span>
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                handleOpenInSlicer(imported.library_file_id, imported.filename, 'orcaslicer')
                              }
                            >
                              <ExternalLink className="w-3.5 h-3.5" />
                              <span className="ml-1.5">{t('makerworld.openInOrcaSlicer')}</span>
                            </Button>
                          </>
                        )}
                        <div className="ml-auto">
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={
                              deleteImportMutation.isPending &&
                              deleteImportMutation.variables?.profileId === profileId
                            }
                            onClick={() => {
                              if (profileId === null) return;
                              setPendingDelete({
                                libraryFileId: imported.library_file_id,
                                profileId,
                                filename: imported.filename,
                              });
                            }}
                            title={t('makerworld.deleteImport')}
                          >
                            {deleteImportMutation.isPending &&
                            deleteImportMutation.variables?.profileId === profileId ? (
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            ) : (
                              <Trash2 className="w-3.5 h-3.5 text-red-500" />
                            )}
                          </Button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

        </div>

        {/* Right column — Recent imports sidebar. Sticky at lg+ so it stays
            reachable while browsing long plate lists. Vertical list here,
            not the horizontal scroll we used in the bottom-of-page layout. */}
        <aside className="lg:sticky lg:top-6 lg:self-start min-w-0">
          {recentQuery.data && recentQuery.data.length > 0 && (
            <Card>
              <CardHeader>
                <h2 className="text-base font-semibold">{t('makerworld.recentImportsHeader')}</h2>
              </CardHeader>
              <CardContent>
                <div className="flex flex-col gap-2 max-h-[28rem] overflow-y-auto -mx-2 px-2">
                  {recentQuery.data.map((item: MakerworldRecentImport) => (
                    <div
                      key={item.library_file_id}
                      className="flex gap-2 p-2 border rounded border-gray-200 dark:border-gray-700"
                    >
                      {item.thumbnail_path ? (
                        <img
                          src={api.getLibraryFileThumbnailUrl(item.library_file_id)}
                          alt=""
                          className="w-12 h-12 shrink-0 object-cover rounded bg-gray-100 dark:bg-gray-800"
                          loading="lazy"
                        />
                      ) : (
                        <div className="w-12 h-12 shrink-0 rounded bg-gray-100 dark:bg-gray-800" />
                      )}
                      <div className="flex-1 min-w-0 flex flex-col gap-1">
                        <p className="text-xs font-medium truncate" title={item.filename}>
                          {item.filename}
                        </p>
                        <div className="flex gap-0.5">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              const target = item.folder_id
                                ? `/files?folder=${item.folder_id}`
                                : '/files';
                              window.location.assign(target);
                            }}
                            title={t('makerworld.viewInLibrary')}
                          >
                            <FolderOpen className="w-3.5 h-3.5" />
                          </Button>
                          {useSlicerApi ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                openSliceForLibraryFile(item.library_file_id, item.filename)
                              }
                              title={t('slice.action', 'Slice')}
                            >
                              <Cog className="w-3.5 h-3.5" />
                            </Button>
                          ) : (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                handleOpenInSlicer(item.library_file_id, item.filename, 'bambu_studio')
                              }
                              title={t('makerworld.openInBambuStudio')}
                            >
                              <ExternalLink className="w-3.5 h-3.5" />
                            </Button>
                          )}
                          {item.source_url && (
                            <a
                              href={item.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center justify-center h-7 w-7 rounded text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                              title={t('makerworld.openOnMakerworld')}
                            >
                              <Globe className="w-3.5 h-3.5" />
                            </a>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </aside>
      </div>

      <p className="text-xs text-gray-500 dark:text-gray-400 pt-4 border-t border-gray-200 dark:border-gray-700">
        {t('makerworld.disclaimer')}
      </p>

      {pendingDelete && (
        <ConfirmModal
          title={t('makerworld.deleteImport')}
          message={t('makerworld.confirmDelete', { filename: pendingDelete.filename })}
          confirmText={t('makerworld.deleteImport')}
          variant="danger"
          isLoading={deleteImportMutation.isPending}
          loadingText={t('makerworld.importDeleting')}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() =>
            deleteImportMutation.mutate({
              libraryFileId: pendingDelete.libraryFileId,
              profileId: pendingDelete.profileId,
            })
          }
        />
      )}

      {sliceModalSource && (
        <SliceModal
          source={sliceModalSource}
          onClose={() => setSliceModalSource(null)}
        />
      )}

      {lightbox && (
        <div
          className="fixed inset-0 bg-black/90 flex items-center justify-center z-50"
          onClick={() => setLightbox(null)}
          role="dialog"
          aria-modal="true"
        >
          <button
            type="button"
            className="absolute top-4 right-4 p-2 bg-white/10 hover:bg-white/20 rounded-full text-white"
            onClick={(e) => {
              e.stopPropagation();
              setLightbox(null);
            }}
            aria-label={t('common.close', 'Close')}
          >
            <X className="w-5 h-5" />
          </button>
          {lightbox.images.length > 1 && (
            <>
              <button
                type="button"
                className="absolute left-4 p-2 bg-white/10 hover:bg-white/20 rounded-full text-white disabled:opacity-30"
                disabled={lightbox.index === 0}
                onClick={(e) => {
                  e.stopPropagation();
                  setLightbox((prev) => (prev ? { ...prev, index: Math.max(0, prev.index - 1) } : prev));
                }}
                aria-label={t('makerworld.galleryPrev')}
              >
                <ChevronLeft className="w-6 h-6" />
              </button>
              <button
                type="button"
                className="absolute right-4 p-2 bg-white/10 hover:bg-white/20 rounded-full text-white disabled:opacity-30"
                disabled={lightbox.index >= lightbox.images.length - 1}
                onClick={(e) => {
                  e.stopPropagation();
                  setLightbox((prev) =>
                    prev ? { ...prev, index: Math.min(prev.images.length - 1, prev.index + 1) } : prev,
                  );
                }}
                aria-label={t('makerworld.galleryNext')}
              >
                <ChevronRight className="w-6 h-6" />
              </button>
            </>
          )}
          <img
            src={proxyCdn(lightbox.images[lightbox.index].url)}
            alt={lightbox.images[lightbox.index].name}
            className="max-w-[90vw] max-h-[90vh] object-contain"
            onClick={(e) => e.stopPropagation()}
          />
          {lightbox.images.length > 1 && (
            <div className="absolute bottom-6 left-1/2 -translate-x-1/2 text-white bg-black/60 px-3 py-1 rounded text-xs">
              {lightbox.index + 1} / {lightbox.images.length}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
