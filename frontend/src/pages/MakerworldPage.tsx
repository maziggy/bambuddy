import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertCircle, ArrowRight, Check, Download, ExternalLink, Globe, Loader2, Printer } from 'lucide-react';

import {
  api,
  type MakerworldImportResponse,
  type MakerworldResolvedModel,
} from '../api/client';
import { Button } from '../components/Button';
import { Card, CardContent, CardHeader } from '../components/Card';
import { PrintModal } from '../components/PrintModal';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';

// MakerWorld's API payloads are passed through as opaque dicts; these helpers
// pull known fields out in a type-safe way so a missing/renamed field shows
// up as an empty string rather than crashing the render.
function pickString(obj: Record<string, unknown> | undefined, key: string): string {
  const value = obj?.[key];
  return typeof value === 'string' ? value : '';
}

// MakerWorld CDN images can't be hotlinked — Bambuddy's img-src CSP blocks
// external hosts. Route them through the /makerworld/thumbnail proxy.
// Empty string in → empty string out so the ``{coverUrl && ...}`` checks
// in the render keep short-circuiting.
function proxyCdn(url: string): string {
  if (!url) return '';
  if (!/^https?:\/\/(makerworld|public-cdn)\.bblmw?\.com\//i.test(url)) return url;
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

export function MakerworldPage() {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const canImport = hasPermission('makerworld:import');

  const [urlInput, setUrlInput] = useState('');
  const [resolved, setResolved] = useState<MakerworldResolvedModel | null>(null);
  // Holds the library_file_id to hand to PrintModal when the user clicks
  // "Print Now". Kept independent of the resolved model because the modal
  // lives in a sibling render branch.
  const [printLibraryFileId, setPrintLibraryFileId] = useState<number | null>(null);
  const [printFilename, setPrintFilename] = useState<string>('');

  const statusQuery = useQuery({
    queryKey: ['makerworld-status'],
    queryFn: () => api.getMakerworldStatus(),
  });

  const resolveMutation = useMutation({
    mutationFn: (url: string) => api.resolveMakerworldUrl(url),
    onSuccess: (data) => setResolved(data),
    onError: (err: Error) => showToast(err.message || t('makerworld.errors.resolveFailed'), 'error'),
  });

  const importMutation = useMutation({
    mutationFn: (instanceId: number) => api.importMakerworldInstance(instanceId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
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
  const printNowMutation = useMutation({
    mutationFn: (instanceId: number) => api.importMakerworldInstance(instanceId),
    onSuccess: (data: MakerworldImportResponse) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      setPrintLibraryFileId(data.library_file_id);
      setPrintFilename(data.filename);
    },
    onError: (err: Error) => showToast(err.message || t('makerworld.errors.downloadFailed'), 'error'),
  });

  const handleResolve = (e?: React.FormEvent) => {
    e?.preventDefault();
    const trimmed = urlInput.trim();
    if (!trimmed) return;
    resolveMutation.mutate(trimmed);
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
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <div className="flex items-center gap-3">
        <Globe className="w-7 h-7 text-brand-500" />
        <h1 className="text-2xl font-bold">{t('makerworld.title')}</h1>
      </div>

      <p className="text-sm text-gray-600 dark:text-gray-400">
        {t('makerworld.description')}
      </p>

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
                    // MakerWorld returns sanitised HTML summaries; line-clamp keeps it bounded
                    dangerouslySetInnerHTML={{ __html: summaryHtml }}
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
            <h2 className="text-lg font-semibold">{t('makerworld.platesHeader', { count: instances.length })}</h2>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3">
              {instances.map((inst, idx) => {
                const instanceId = pickNumber(inst, 'id');
                const instanceTitle = pickString(inst, 'title');
                const cover = pickString(inst, 'cover');
                const materialCnt = pickNumber(inst, 'materialCnt');
                const needAms = inst?.['needAms'] === true;
                const downloadsOnInstance = pickNumber(inst, 'downloadCount');
                if (instanceId == null) return null;
                const isImporting = importMutation.isPending && importMutation.variables === instanceId;
                const isPrinting = printNowMutation.isPending && printNowMutation.variables === instanceId;
                return (
                  <div
                    key={instanceId}
                    className="flex gap-3 items-center p-3 border rounded border-gray-200 dark:border-gray-700"
                  >
                    {cover ? (
                      <img src={proxyCdn(cover)} alt="" className="w-16 h-16 object-cover rounded" loading="lazy" />
                    ) : (
                      <div className="w-16 h-16 rounded bg-gray-100 dark:bg-gray-800" />
                    )}
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
                        disabled={!canImport || !canDownload || isImporting || isPrinting}
                        onClick={() => importMutation.mutate(instanceId)}
                        title={!canDownload ? t('makerworld.signInRequiredTitle') : undefined}
                      >
                        {isImporting ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Download className="w-4 h-4" />
                        )}
                        <span className="ml-2">{t('makerworld.importToLibrary')}</span>
                      </Button>
                      <Button
                        variant="primary"
                        size="sm"
                        disabled={!canImport || !canDownload || isImporting || isPrinting}
                        onClick={() => printNowMutation.mutate(instanceId)}
                        title={!canDownload ? t('makerworld.signInRequiredTitle') : undefined}
                      >
                        {isPrinting ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Printer className="w-4 h-4" />
                        )}
                        <span className="ml-2">{t('makerworld.printNow')}</span>
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      <p className="text-xs text-gray-500 dark:text-gray-400 pt-4 border-t border-gray-200 dark:border-gray-700">
        {t('makerworld.disclaimer')}
      </p>

      {printLibraryFileId !== null && (
        <PrintModal
          mode="reprint"
          libraryFileId={printLibraryFileId}
          archiveName={printFilename || 'MakerWorld model'}
          onClose={() => setPrintLibraryFileId(null)}
          onSuccess={() => {
            setPrintLibraryFileId(null);
            queryClient.invalidateQueries({ queryKey: ['library-files'] });
            queryClient.invalidateQueries({ queryKey: ['archives'] });
          }}
        />
      )}
    </div>
  );
}
