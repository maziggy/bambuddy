import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import {
  ArrowLeft,
  Box,
  Check,
  ChevronLeft,
  ChevronRight,
  Cog,
  Download,
  ExternalLink,
  FolderOpen,
  Loader2,
  Search,
  Settings as SettingsIcon,
} from 'lucide-react';

import {
  api,
  ApiError,
  type LibraryFolderTree,
  type ManyfoldFile,
  type ManyfoldModel,
} from '../api/client';
import { Button } from './Button';
import { Card, CardContent, CardHeader } from './Card';
import { ConfirmModal } from './ConfirmModal';
import { SliceModal, type SliceSource } from './SliceModal';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { isSliceableLibraryFile } from '../utils/libraryFiles';
import { openInSlicer, resolveDesktopSlicer, type SlicerType } from '../utils/slicer';
import { useUiSettings } from '../hooks/useUiSettings';

const MANYFOLD_ERROR_CODES = new Set([
  'manyfold_not_configured',
  'manyfold_credentials',
  'manyfold_scope',
  'manyfold_rate_limited',
  'manyfold_unreachable',
  'manyfold_forbidden',
  'manyfold_not_found',
  'manyfold_too_large',
  'manyfold_not_importable',
  'manyfold_bad_url',
  'manyfold_secret_required',
  'manyfold_failed',
]);

/** The backend names each Manyfold failure with a code; show our own text for it. */
function errorText(err: unknown, t: TFunction): string {
  if (err instanceof ApiError && err.code && MANYFOLD_ERROR_CODES.has(err.code)) {
    return t(`manyfold.errors.${err.code}`);
  }
  return err instanceof Error && err.message ? err.message : t('manyfold.errors.manyfold_failed');
}

function fileTypeLabel(mime: string): string {
  const subtype = mime.split('/')[1] ?? '';
  return (subtype.replace(/^x-/, '').split(/[+.;]/)[0] || '?').toUpperCase();
}

type FlatFolder = { folder: LibraryFolderTree; depth: number };
function flattenFolderTree(tree: LibraryFolderTree, depth = 0, out: FlatFolder[] = []): FlatFolder[] {
  out.push({ folder: tree, depth });
  for (const child of tree.children ?? []) flattenFolderTree(child, depth + 1, out);
  return out;
}

/** A model's preview from Manyfold, or a placeholder when it has none. */
function ManyfoldPreview({ modelId, className }: { modelId: string; className?: string }) {
  const { data: blob, isLoading } = useQuery({
    queryKey: ['manyfold-preview', modelId],
    queryFn: () => api.getManyfoldPreview(modelId),
    staleTime: Infinity,
    retry: false,
  });
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) {
      setSrc(null);
      return;
    }
    const url = URL.createObjectURL(blob);
    setSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [blob]);

  return (
    <div className={`flex items-center justify-center bg-bambu-dark overflow-hidden ${className ?? ''}`}>
      {src ? (
        <img src={src} alt="" className="w-full h-full object-contain" />
      ) : isLoading ? (
        <Loader2 className="w-6 h-6 text-bambu-gray animate-spin" />
      ) : (
        <Box className="w-10 h-10 text-bambu-gray/50" aria-hidden />
      )}
    </div>
  );
}

/** URL, client ID and secret of the Manyfold OAuth application. */
function ManyfoldConnectionCard({ onDone }: { onDone?: () => void }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const configQuery = useQuery({ queryKey: ['manyfold-config'], queryFn: () => api.getManyfoldConfig() });
  const [url, setUrl] = useState('');
  const [clientId, setClientId] = useState('');
  const [secret, setSecret] = useState('');
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  useEffect(() => {
    if (configQuery.data) {
      setUrl(configQuery.data.url);
      setClientId(configQuery.data.client_id);
    }
  }, [configQuery.data]);

  const hasStoredSecret = configQuery.data?.has_client_secret ?? false;
  const input = { url: url.trim(), client_id: clientId.trim(), client_secret: secret.trim() || undefined };
  const complete = !!input.url && !!input.client_id && (!!input.client_secret || hasStoredSecret);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['manyfold-config'] });
    queryClient.invalidateQueries({ queryKey: ['manyfold-status'] });
    queryClient.invalidateQueries({ queryKey: ['manyfold-models'] });
    queryClient.invalidateQueries({ queryKey: ['manyfold-model'] });
    queryClient.invalidateQueries({ queryKey: ['manyfold-preview'] });
  };

  const testMutation = useMutation({
    mutationFn: () => api.testManyfoldConfig(input),
    onSuccess: (data) => setTestResult({ ok: true, text: t('manyfold.testOk', { count: data.model_count }) }),
    onError: (err) => setTestResult({ ok: false, text: errorText(err, t) }),
  });

  const saveMutation = useMutation({
    mutationFn: () => api.updateManyfoldConfig(input),
    onSuccess: () => {
      setSecret('');
      refresh();
      showToast(t('settings.toast.settingsSaved'), 'success');
      onDone?.();
    },
    onError: (err) => showToast(errorText(err, t), 'error'),
  });

  const disconnectMutation = useMutation({
    mutationFn: () => api.deleteManyfoldConfig(),
    onSuccess: () => {
      setConfirmDisconnect(false);
      setUrl('');
      setClientId('');
      setSecret('');
      setTestResult(null);
      refresh();
      showToast(t('manyfold.disconnected'), 'success');
    },
    onError: (err) => {
      setConfirmDisconnect(false);
      showToast(errorText(err, t), 'error');
    },
  });

  const fieldClass =
    'w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none';

  return (
    <Card>
      <CardHeader>
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <SettingsIcon className="w-5 h-5 text-bambu-green" />
          {t('manyfold.connectionTitle')}
        </h2>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-bambu-gray">{t('manyfold.connectionHelp')}</p>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (complete) saveMutation.mutate();
          }}
        >
          <div>
            <label htmlFor="manyfold-url" className="block text-sm text-bambu-gray mb-1">
              {t('manyfold.url')}
            </label>
            <input
              id="manyfold-url"
              type="url"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setTestResult(null);
              }}
              placeholder={t('manyfold.urlPlaceholder')}
              className={fieldClass}
              autoComplete="off"
            />
          </div>
          <div>
            <label htmlFor="manyfold-client-id" className="block text-sm text-bambu-gray mb-1">
              {t('manyfold.clientId')}
            </label>
            <input
              id="manyfold-client-id"
              type="text"
              value={clientId}
              onChange={(e) => {
                setClientId(e.target.value);
                setTestResult(null);
              }}
              className={fieldClass}
              autoComplete="off"
            />
          </div>
          <div>
            <label htmlFor="manyfold-client-secret" className="block text-sm text-bambu-gray mb-1">
              {t('manyfold.clientSecret')}
            </label>
            <input
              id="manyfold-client-secret"
              type="password"
              value={secret}
              onChange={(e) => {
                setSecret(e.target.value);
                setTestResult(null);
              }}
              placeholder={hasStoredSecret ? t('manyfold.clientSecretStored') : ''}
              className={fieldClass}
              autoComplete="new-password"
            />
          </div>
          {testResult && (
            <p
              role="status"
              className={`text-sm ${testResult.ok ? 'text-bambu-green' : 'text-red-400'}`}
            >
              {testResult.text}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!complete || saveMutation.isPending}>
              {saveMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {t('common.save')}
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={!complete || testMutation.isPending}
              onClick={() => testMutation.mutate()}
            >
              {testMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {t('manyfold.test')}
            </Button>
            {configQuery.data?.configured && (
              <Button type="button" variant="danger" onClick={() => setConfirmDisconnect(true)}>
                {t('manyfold.disconnect')}
              </Button>
            )}
            {onDone && configQuery.data?.configured && (
              <Button type="button" variant="ghost" onClick={onDone}>
                {t('common.cancel')}
              </Button>
            )}
          </div>
        </form>
      </CardContent>
      {confirmDisconnect && (
        <ConfirmModal
          title={t('manyfold.disconnectTitle')}
          message={t('manyfold.disconnectBody')}
          confirmText={t('manyfold.disconnect')}
          variant="danger"
          isLoading={disconnectMutation.isPending}
          onCancel={() => setConfirmDisconnect(false)}
          onConfirm={() => disconnectMutation.mutate()}
        />
      )}
    </Card>
  );
}

/** The library folder imports land in; empty means the "Manyfold" folder. */
function ImportFolderSelect({
  value,
  onChange,
  disabled,
}: {
  value: number | null;
  onChange: (folderId: number | null) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const foldersQuery = useQuery({ queryKey: ['library-folders'], queryFn: () => api.getLibraryFolders() });
  const options = (foldersQuery.data ?? [])
    .filter((f) => !(f.is_external && f.external_readonly))
    .flatMap((f) => flattenFolderTree(f));
  return (
    <label className="flex items-center gap-2 text-sm text-bambu-gray">
      {t('manyfold.importTo')}
      <select
        value={value ?? ''}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
        className="px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm"
      >
        <option value="">{t('manyfold.folderAuto')}</option>
        {options.map(({ folder, depth }) => (
          // Listed for the tree's shape; only own and shared folders take imports (#3201).
          <option key={folder.id} value={folder.id} disabled={folder.can_write === false}>
            {`${'— '.repeat(depth)}${folder.name}`}
          </option>
        ))}
      </select>
    </label>
  );
}

type BulkItem = { modelId: string; fileId: string; name: string };

/** The first few names, then how many more. */
function listNames(names: string[]): string {
  return names.length > 3 ? `${names.slice(0, 3).join(', ')} +${names.length - 3}` : names.join(', ');
}

/**
 * Imports several files one after another, not in parallel, to go easy on
 * Manyfold and the disk. A file that fails doesn't stop the rest; one summary
 * at the end says what happened.
 */
function useManyfoldBulkImport() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState<{ current: number; total: number } | null>(null);

  const run = async (items: BulkItem[], folderId: number | null) => {
    if (items.length === 0) {
      showToast(t('manyfold.bulkNothing'), 'info');
      return;
    }
    let imported = 0;
    let existing = 0;
    const failed: string[] = [];
    let firstError: unknown = null;
    try {
      for (let i = 0; i < items.length; i += 1) {
        setProgress({ current: i + 1, total: items.length });
        try {
          const result = await api.importManyfoldFile(items[i].modelId, items[i].fileId, folderId);
          if (result.was_existing) existing += 1;
          else imported += 1;
        } catch (err) {
          failed.push(items[i].name);
          firstError ??= err;
        }
      }
    } finally {
      setProgress(null);
      queryClient.invalidateQueries({ queryKey: ['manyfold-model'] });
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
    }
    showToast(
      t('manyfold.bulkDone', { imported, existing, failed: failed.length }),
      failed.length ? 'warning' : 'success',
    );
    if (failed.length) {
      showToast(t('manyfold.bulkFailed', { names: listNames(failed), reason: errorText(firstError, t) }), 'error');
    }
  };

  return { run, progress, busy: progress !== null };
}

/** One model: its details and files, each importable into the library. */
function ManyfoldModelView({
  modelId,
  onBack,
  onSlice,
}: {
  modelId: string;
  onBack: () => void;
  onSlice: (libraryFileId: number, filename: string) => void;
}) {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const canImport = hasPermission('manyfold:import');
  const [folderId, setFolderId] = useState<number | null>(null);
  const [importingFileId, setImportingFileId] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const bulk = useManyfoldBulkImport();

  const modelQuery = useQuery({
    queryKey: ['manyfold-model', modelId],
    queryFn: () => api.getManyfoldModel(modelId),
  });
  const settingsQuery = useUiSettings();
  const useSlicerApi = settingsQuery.data?.use_slicer_api ?? false;
  const desktopSlicer: SlicerType = resolveDesktopSlicer(
    settingsQuery.data?.open_in_slicer,
    settingsQuery.data?.preferred_slicer,
  );

  const importMutation = useMutation({
    mutationFn: (file: ManyfoldFile) => api.importManyfoldFile(modelId, file.id, folderId),
    onMutate: (file) => setImportingFileId(file.id),
    onSettled: () => setImportingFileId(null),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['manyfold-model', modelId] });
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      showToast(
        data.was_existing ? t('manyfold.alreadyInLibrary') : t('manyfold.imported', { filename: data.filename }),
        'success',
      );
    },
    onError: (err) => showToast(errorText(err, t), 'error'),
  });

  if (modelQuery.isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }
  if (modelQuery.isError || !modelQuery.data) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="w-4 h-4" />
          {t('manyfold.back')}
        </Button>
        <p className="text-red-400 text-sm">{errorText(modelQuery.error, t)}</p>
      </div>
    );
  }

  const model: ManyfoldModel = modelQuery.data;
  // Files that can still be imported: printable and not in the library yet.
  const candidates = model.files.filter((f) => f.importable && !f.library_file);
  const selected = candidates.filter((f) => checked.has(f.id));
  const allSelected = candidates.length > 0 && selected.length === candidates.length;
  const busy = bulk.busy || importMutation.isPending;
  const importFiles = async (files: ManyfoldFile[]) => {
    await bulk.run(
      files.map((f) => ({ modelId: model.id, fileId: f.id, name: f.name })),
      folderId,
    );
    setChecked(new Set());
  };

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack}>
        <ArrowLeft className="w-4 h-4" />
        {t('manyfold.back')}
      </Button>
      <Card>
        <CardContent>
          <div className="flex flex-col md:flex-row gap-6 py-2">
            <ManyfoldPreview modelId={model.id} className="w-full md:w-72 aspect-square rounded-lg shrink-0" />
            <div className="min-w-0 flex-1 space-y-3">
              <div>
                <h2 className="text-xl font-semibold text-white break-words">{model.name}</h2>
                {model.caption && <p className="text-bambu-gray mt-1">{model.caption}</p>}
              </div>
              {model.description && (
                <p className="text-sm text-bambu-gray whitespace-pre-line break-words">{model.description}</p>
              )}
              {model.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {model.tags.map((tag) => (
                    <span key={tag} className="px-2 py-0.5 rounded-full bg-bambu-dark-tertiary text-xs text-white">
                      {tag}
                    </span>
                  ))}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                {model.license && (
                  <span className="text-bambu-gray">
                    {t('manyfold.license')}: <span className="text-white">{model.license}</span>
                  </span>
                )}
                <a
                  href={model.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-bambu-green hover:underline"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  {t('manyfold.openInManyfold')}
                </a>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-lg font-semibold text-white">{t('manyfold.files')}</h3>
            {canImport && <ImportFolderSelect value={folderId} onChange={setFolderId} disabled={busy} />}
          </div>
          {canImport && candidates.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 mt-3">
              <label className="flex items-center gap-2 text-sm text-white">
                <input
                  type="checkbox"
                  checked={allSelected}
                  ref={(el) => {
                    if (el) el.indeterminate = selected.length > 0 && !allSelected;
                  }}
                  disabled={busy}
                  onChange={() => setChecked(allSelected ? new Set() : new Set(candidates.map((f) => f.id)))}
                  className="accent-bambu-green"
                />
                {t('manyfold.selectAll')}
              </label>
              {bulk.progress ? (
                <span className="inline-flex items-center gap-2 text-sm text-bambu-gray">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('manyfold.importProgress', bulk.progress)}
                </span>
              ) : (
                <>
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={busy || selected.length === 0}
                    onClick={() => importFiles(selected)}
                  >
                    <Download className="w-3.5 h-3.5" />
                    {t('manyfold.importSelected', { count: selected.length })}
                  </Button>
                  <Button variant="secondary" size="sm" disabled={busy} onClick={() => importFiles(candidates)}>
                    {t('manyfold.importAll')}
                  </Button>
                </>
              )}
            </div>
          )}
        </CardHeader>
        <CardContent>
          {model.files.length === 0 ? (
            <p className="text-sm text-bambu-gray">{t('manyfold.noFiles')}</p>
          ) : (
            <ul className="divide-y divide-bambu-dark-tertiary">
              {model.files.map((file) => {
                const imported = file.library_file;
                const sliceable =
                  imported !== null && isSliceableLibraryFile({ filename: imported.filename }, useSlicerApi, desktopSlicer);
                return (
                  <li key={file.id} className="flex flex-wrap items-center gap-3 py-2">
                    {canImport && candidates.length > 0 && (
                      <span className="w-4 shrink-0 flex">
                        {file.importable && !imported && (
                          <input
                            type="checkbox"
                            aria-label={t('manyfold.selectFile', { name: file.name })}
                            checked={checked.has(file.id)}
                            disabled={busy}
                            onChange={() =>
                              setChecked((prev) => {
                                const next = new Set(prev);
                                if (next.has(file.id)) next.delete(file.id);
                                else next.add(file.id);
                                return next;
                              })
                            }
                            className="accent-bambu-green"
                          />
                        )}
                      </span>
                    )}
                    <span className="px-1.5 py-0.5 rounded bg-bambu-dark-tertiary text-[11px] font-mono text-bambu-gray shrink-0">
                      {fileTypeLabel(file.mime)}
                    </span>
                    <span className="text-sm text-white min-w-0 flex-1 break-words">{file.name}</span>
                    <div className="flex flex-wrap items-center gap-2">
                      {!file.importable ? (
                        <span className="text-xs text-bambu-gray">{t('manyfold.notImportable')}</span>
                      ) : imported ? (
                        <>
                          <span className="inline-flex items-center gap-1 text-xs text-bambu-green">
                            <Check className="w-3.5 h-3.5" />
                            {t('manyfold.inLibrary')}
                          </span>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => navigate(imported.folder_id ? `/files?folder=${imported.folder_id}` : '/files')}
                          >
                            <FolderOpen className="w-3.5 h-3.5" />
                            {t('manyfold.showInLibrary')}
                          </Button>
                          {sliceable && (
                            <Button variant="ghost" size="sm" onClick={() => onSlice(imported.id, imported.filename)}>
                              <Cog className="w-3.5 h-3.5" />
                              {t('slice.action', 'Slice')}
                            </Button>
                          )}
                        </>
                      ) : canImport ? (
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={busy}
                          onClick={() => importMutation.mutate(file)}
                        >
                          {importingFileId === file.id ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <Download className="w-3.5 h-3.5" />
                          )}
                          {importingFileId === file.id ? t('manyfold.importing') : t('manyfold.import')}
                        </Button>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** The Manyfold tab of the Model Sources page (#1471). */
export function ManyfoldTab() {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const canConfigure = hasPermission('settings:update');
  const [editingConnection, setEditingConnection] = useState(false);
  const [queryInput, setQueryInput] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [sliceSource, setSliceSource] = useState<SliceSource | null>(null);
  // Models ticked in the grid, kept across pages and searches (id -> name).
  const [picked, setPicked] = useState<Map<string, string>>(new Map());
  const [gridFolderId, setGridFolderId] = useState<number | null>(null);
  const [collecting, setCollecting] = useState<{ current: number; total: number } | null>(null);
  const canImport = hasPermission('manyfold:import');
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const gridBulk = useManyfoldBulkImport();
  const gridBusy = collecting !== null || gridBulk.busy;
  const gridProgress = collecting
    ? t('manyfold.collectProgress', collecting)
    : gridBulk.progress
      ? t('manyfold.importProgress', gridBulk.progress)
      : null;

  const togglePicked = (id: string, name: string) =>
    setPicked((prev) => {
      const next = new Map(prev);
      if (next.has(id)) next.delete(id);
      else next.set(id, name);
      return next;
    });

  // Every printable file of the ticked models that isn't in the library yet.
  // The models are read one after another first, then their files imported.
  const importPicked = async () => {
    const models = [...picked.keys()];
    const items: BulkItem[] = [];
    const unreadable: string[] = [];
    try {
      for (let i = 0; i < models.length; i += 1) {
        setCollecting({ current: i + 1, total: models.length });
        try {
          const model = await queryClient.fetchQuery({
            queryKey: ['manyfold-model', models[i]],
            queryFn: () => api.getManyfoldModel(models[i]),
          });
          for (const f of model.files) {
            if (f.importable && !f.library_file) items.push({ modelId: model.id, fileId: f.id, name: `${model.name}: ${f.name}` });
          }
        } catch {
          unreadable.push(picked.get(models[i]) ?? models[i]);
        }
      }
    } finally {
      setCollecting(null);
    }
    if (unreadable.length) showToast(t('manyfold.modelsUnreadable', { names: listNames(unreadable) }), 'error');
    await gridBulk.run(items, gridFolderId);
    setPicked(new Map());
  };

  const settingsQuery = useUiSettings();
  const statusQuery = useQuery({ queryKey: ['manyfold-status'], queryFn: () => api.getManyfoldStatus() });
  const configured = statusQuery.data?.configured ?? false;
  const modelsQuery = useQuery({
    queryKey: ['manyfold-models', query, page],
    queryFn: () => api.listManyfoldModels(query, page),
    enabled: configured,
    placeholderData: (previous) => previous,
  });

  // Slicing follows the same preference the MakerWorld tab and the File
  // Manager use: the in-app slicer when Use Slicer API is on, otherwise a
  // handoff to the desktop slicer.
  const handleSlice = async (libraryFileId: number, filename: string) => {
    if (settingsQuery.data?.use_slicer_api) {
      setSliceSource({ kind: 'libraryFile', id: libraryFileId, filename });
      return;
    }
    const slicer = resolveDesktopSlicer(settingsQuery.data?.open_in_slicer, settingsQuery.data?.preferred_slicer);
    try {
      const { token } = await api.createLibrarySlicerToken(libraryFileId);
      openInSlicer(`${window.location.origin}${api.getLibrarySlicerDownloadUrl(libraryFileId, token, filename)}`, slicer);
    } catch {
      // Auth disabled: the plain download URL is public then.
      openInSlicer(`${window.location.origin}${api.getLibraryFileDownloadUrl(libraryFileId)}`, slicer);
    }
  };

  if (statusQuery.isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  if (!configured || editingConnection) {
    return (
      <div className="space-y-6">
        <p className="text-bambu-gray">{t('manyfold.description')}</p>
        {canConfigure ? (
          <ManyfoldConnectionCard onDone={configured ? () => setEditingConnection(false) : undefined} />
        ) : (
          <Card>
            <CardContent>
              <div className="py-2">
                <p className="font-medium text-white">{t('manyfold.notConnectedTitle')}</p>
                <p className="text-sm text-bambu-gray mt-1">{t('manyfold.notConnectedBody')}</p>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    );
  }

  const listing = modelsQuery.data;

  return (
    <div className="space-y-6">
      {selectedModelId ? (
        <ManyfoldModelView
          modelId={selectedModelId}
          onBack={() => setSelectedModelId(null)}
          onSlice={handleSlice}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <p className="text-bambu-gray flex-1 min-w-[16rem]">{t('manyfold.description')}</p>
            {canConfigure && (
              <Button variant="ghost" size="sm" onClick={() => setEditingConnection(true)}>
                <SettingsIcon className="w-4 h-4" />
                {t('manyfold.editConnection')}
              </Button>
            )}
          </div>
          <form
            className="flex gap-2"
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              setQuery(queryInput.trim());
              setPage(1);
            }}
          >
            <div className="relative flex-1">
              <Search className="w-4 h-4 text-bambu-gray absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="search"
                value={queryInput}
                onChange={(e) => setQueryInput(e.target.value)}
                placeholder={t('manyfold.searchPlaceholder')}
                aria-label={t('manyfold.searchPlaceholder')}
                className="w-full pl-9 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
              />
            </div>
            <Button type="submit">{t('common.search')}</Button>
          </form>

          {modelsQuery.isError ? (
            <p className="text-sm text-red-400">{errorText(modelsQuery.error, t)}</p>
          ) : !listing ? (
            <div className="flex justify-center py-12">
              <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
            </div>
          ) : listing.models.length === 0 ? (
            <p className="text-sm text-bambu-gray">{t('manyfold.noModels')}</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-sm text-bambu-gray">{t('manyfold.modelCount', { count: listing.total })}</p>
                {canImport && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={gridBusy}
                    onClick={() =>
                      setPicked((prev) => {
                        const next = new Map(prev);
                        for (const m of listing.models) next.set(m.id, m.name);
                        return next;
                      })
                    }
                  >
                    {t('manyfold.selectPage')}
                  </Button>
                )}
              </div>
              {canImport && picked.size > 0 && (
                <div className="sticky top-0 z-10 flex flex-wrap items-center gap-3 rounded-lg border border-bambu-green/40 bg-bambu-dark-secondary px-4 py-3">
                  <span className="text-sm text-white">{t('manyfold.modelsSelected', { count: picked.size })}</span>
                  <ImportFolderSelect value={gridFolderId} onChange={setGridFolderId} disabled={gridBusy} />
                  {gridProgress ? (
                    <span className="inline-flex items-center gap-2 text-sm text-bambu-gray">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {gridProgress}
                    </span>
                  ) : (
                    <>
                      <Button variant="primary" size="sm" onClick={importPicked}>
                        <Download className="w-3.5 h-3.5" />
                        {t('manyfold.importModels')}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setPicked(new Map())}>
                        {t('manyfold.clearSelection')}
                      </Button>
                    </>
                  )}
                </div>
              )}
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-4">
                {listing.models.map((model) => {
                  const isPicked = picked.has(model.id);
                  return (
                    <div
                      key={model.id}
                      className={`relative rounded-lg border bg-bambu-dark-secondary overflow-hidden transition-colors ${
                        isPicked ? 'border-bambu-green ring-1 ring-bambu-green' : 'border-bambu-dark-tertiary hover:border-bambu-green'
                      }`}
                    >
                      {/* Locked while the ticked models import, so a file can't be imported twice at once from the model view. */}
                      <button
                        type="button"
                        onClick={() => setSelectedModelId(model.id)}
                        disabled={gridBusy}
                        className="block w-full text-left disabled:cursor-wait"
                      >
                        <ManyfoldPreview modelId={model.id} className="aspect-square" />
                        <span className="block px-3 py-2 text-sm text-white truncate" title={model.name}>
                          {model.name}
                        </span>
                      </button>
                      {canImport && (
                        <label className="absolute top-2 left-2 flex items-center justify-center w-7 h-7 rounded bg-black/60 cursor-pointer">
                          <input
                            type="checkbox"
                            aria-label={t('manyfold.selectModel', { name: model.name })}
                            checked={isPicked}
                            disabled={gridBusy}
                            onChange={() => togglePicked(model.id, model.name)}
                            className="accent-bambu-green w-4 h-4"
                          />
                        </label>
                      )}
                    </div>
                  );
                })}
              </div>
              {(listing.has_previous || listing.has_next) && (
                <div className="flex items-center justify-center gap-3">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!listing.has_previous || modelsQuery.isFetching}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    <ChevronLeft className="w-4 h-4" />
                    {t('manyfold.previous')}
                  </Button>
                  <span className="text-sm text-bambu-gray">{t('manyfold.page', { page: listing.page })}</span>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!listing.has_next || modelsQuery.isFetching}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    {t('manyfold.next')}
                    <ChevronRight className="w-4 h-4" />
                  </Button>
                </div>
              )}
            </>
          )}
        </>
      )}
      {sliceSource && <SliceModal source={sliceSource} onClose={() => setSliceSource(null)} />}
    </div>
  );
}
