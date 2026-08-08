import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  Info,
  Loader2,
  Palette,
  RotateCcw,
  Settings as SettingsIcon,
  Thermometer,
  X,
} from 'lucide-react';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { Toggle } from './Toggle';
import { ConfirmModal } from './ConfirmModal';
import {
  api,
  type RestoreCategory,
  type GitHubRestoreParams,
  type GitHubRestoreResponse,
} from '../api/client';
import type { TFunction } from 'i18next';

interface GitHubRestoreModalProps {
  onClose: () => void;
}

/**
 * Render a server-supplied translation code, falling back to its English text.
 *
 * The restore endpoints describe every note and preview caveat as a `code` plus
 * typed `params`, and carry the English rendering along as `message`. That is
 * the same contract `backup.pathCheck` already uses one card down in
 * GitHubBackupSettings — including the `defaultValue` arm, which is what keeps a
 * newer backend's unfamiliar code readable instead of printing the raw key.
 */
function translateCoded(
  t: TFunction,
  group: 'notes' | 'details',
  code: string | null | undefined,
  params: GitHubRestoreParams | undefined,
  fallback: string | null
): string | null {
  if (!code) return fallback;
  return t(`backup.restoreFromGit.${group}.${code}`, {
    ...(params ?? {}),
    defaultValue: fallback ?? code,
  });
}

interface CategoryMeta {
  id: RestoreCategory;
  labelKey: string;
  icon: React.ReactNode;
}

// Order mirrors the order the backend applies them in. Labels reuse the keys
// the backup checkbox group already ships in all locales.
const CATEGORIES: CategoryMeta[] = [
  { id: 'archives', labelKey: 'backup.printArchives', icon: <Archive className="w-4 h-4" /> },
  { id: 'spools', labelKey: 'backup.spoolInventory', icon: <Palette className="w-4 h-4" /> },
  { id: 'settings', labelKey: 'backup.appSettings', icon: <SettingsIcon className="w-4 h-4" /> },
  { id: 'kprofiles', labelKey: 'backup.kProfiles', icon: <Thermometer className="w-4 h-4" /> },
];

const CATEGORY_LABEL_KEYS: Record<string, string> = Object.fromEntries(
  CATEGORIES.map((c) => [c.id, c.labelKey])
);

const LATEST = 'HEAD';

export function GitHubRestoreModal({ onClose }: GitHubRestoreModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [selectedRef, setSelectedRef] = useState<string>(LATEST);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [overwriteExisting, setOverwriteExisting] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [result, setResult] = useState<GitHubRestoreResponse | null>(null);

  const commitsQuery = useQuery({
    queryKey: ['github-backup-commits'],
    queryFn: () => api.getGitHubBackupCommits(20),
  });

  const previewQuery = useQuery({
    queryKey: ['github-restore-preview', selectedRef],
    queryFn: () => api.getGitHubRestorePreview(selectedRef),
  });

  // Restore the exact commit the preview described, not the ref that was asked
  // for. They differ for the default "Latest backup" selection, which posts the
  // symbolic 'HEAD' and lets the backend re-resolve it — so a backup landing
  // between preview and restore would silently restore a different commit than
  // the one whose contents the user just approved.
  const resolvedRef = previewQuery.data?.success ? previewQuery.data.ref : selectedRef;

  const availability = useMemo(() => {
    const map: Record<string, { available: boolean; itemCount: number; detail: string | null }> = {};
    previewQuery.data?.categories?.forEach((c) => {
      map[c.category] = {
        available: c.available,
        itemCount: c.item_count,
        detail: translateCoded(t, 'details', c.detail_code, c.detail_params, c.detail),
      };
    });
    return map;
  }, [previewQuery.data, t]);

  // What a Restore click would actually send. `selected` on its own is not that:
  // it survives a commit switch by design (the pruning effect below only runs
  // once the new preview lands), so between picking a commit and its preview
  // resolving, `selected` still describes the *previous* commit while the
  // checkbox list is replaced by a spinner. Counting it raw put "2 selected"
  // and an enabled Restore button under that spinner, and clicking restored the
  // new commit with the old commit's categories — none of which the user had
  // seen an item count for. Gating on availability, exactly as the checkboxes
  // do, empties the list until the preview says otherwise, which also disables
  // the button.
  const selectedCategories = useMemo(
    () => CATEGORIES.filter((c) => selected[c.id] && availability[c.id]?.available).map((c) => c.id),
    [selected, availability]
  );
  const selectedCount = selectedCategories.length;

  // Overwrite-off tells the user that existing entries stay as they are, and for
  // three of the four categories it keeps that promise. K-profiles cannot:
  // _restore_kprofiles takes no overwrite flag, because writing a slot is always
  // an overwrite on the printer — resolving the live cali_idx and publishing
  // extrusion_cali_set replaces whatever calibration that slot holds. The
  // backend does say so, but as a note in the result panel, i.e. after the MQTT
  // send has already happened and cannot be taken back. So the one screen that
  // explains overwrite-off has to carry the exception too, before the click.
  const warnKprofilesOverwrite = !overwriteExisting && selectedCategories.includes('kprofiles');

  const restoreMutation = useMutation({
    mutationFn: () =>
      api.restoreFromGitHub({
        ref: resolvedRef,
        categories: selectedCategories,
        overwrite_existing: overwriteExisting,
      }),
    onSuccess: (data) => {
      setShowConfirm(false);
      // The endpoint answers 200 for a refused or failed restore too, with
      // `success: false` — and two of those are ordinary conditions, not
      // errors: another restore already running, and a backup being mid-flight.
      // Nothing was written for either, so they keep the form and show the red
      // block below; rendering the result panel for them put a green tick, no
      // tally at all and a "reload so the restored data appears" hint above a
      // message saying nothing had been restored.
      //
      // A failure that got as far as writing is the opposite case. Categories
      // commit as each one finishes, so a non-empty `results` names the ones
      // that are on disk — and the form over the top of them would be the same
      // "nothing was restored" misreading, this time with the data actually in.
      // So the panel is what wrote, not what succeeded.
      const wroteSomething = Object.keys(data.results ?? {}).length > 0;
      if (data.success || wroteSomething) {
        setResult(data);
        // A restore rewrites rows these caches hold. ['settings'] is one of
        // them: until #2716 was fixed on dev, invalidating it made
        // SettingsPage's debounced auto-save write the pre-restore form state
        // straight back over the restore, so this modal skipped it and pinned
        // the cache instead. That page now reconciles a moved server snapshot
        // field by field, so the restore no longer needs an exception.
        queryClient.invalidateQueries({ queryKey: ['spools'] });
        queryClient.invalidateQueries({ queryKey: ['archives'] });
        queryClient.invalidateQueries({ queryKey: ['settings'] });
      }
      // A failure that got as far as resolving the commit still writes a log row
      // (status "failed"), so refresh the history and status either way.
      queryClient.invalidateQueries({ queryKey: ['github-backup-logs'] });
      queryClient.invalidateQueries({ queryKey: ['github-backup-status'] });
    },
    onError: () => setShowConfirm(false),
  });

  const isRestoring = restoreMutation.isPending;

  // A settings restore rewrites rows the whole app reads, and not all of them
  // through a query this modal can invalidate. The interface language is applied
  // by i18n.changeLanguage, called only from the SettingsPage dropdown and the
  // appliance-locale bootstrap; the auth state comes from AuthProvider's
  // mount-time getAuthStatus, not from ['settings'] at all. So every exit path
  // after a settings restore reloads rather than just closing.
  const settingsRestored = Boolean(result && 'settings' in result.results);
  const closeModal = useCallback(() => {
    if (settingsRestored) {
      window.location.reload();
      return;
    }
    onClose();
  }, [settingsRestored, onClose]);

  // Close on Escape, except while a restore is in flight.
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isRestoring && !showConfirm) closeModal();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [closeModal, isRestoring, showConfirm]);

  // Interrupting a restore mid-flight can leave a partly-applied category.
  useEffect(() => {
    if (!isRestoring) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isRestoring]);

  // Selecting a category that isn't in the newly-picked commit would send a
  // request the backend rejects, so drop those whenever the preview changes.
  useEffect(() => {
    if (!previewQuery.data) return;
    setSelected((prev) => {
      const next: Record<string, boolean> = {};
      CATEGORIES.forEach((c) => {
        next[c.id] = Boolean(prev[c.id]) && Boolean(availability[c.id]?.available);
      });
      return next;
    });
  }, [previewQuery.data, availability]);

  const commits = commitsQuery.data?.commits ?? [];

  const formatCommitLabel = (sha: string, message: string, date: string) => {
    const firstLine = (message || '').split('\n')[0];
    const when = date ? new Date(date).toLocaleString() : '';
    return `${sha.slice(0, 7)} — ${when}${firstLine ? ` — ${firstLine}` : ''}`;
  };

  // Two ways these can fail, and both have to reach the user. A provider-side
  // failure (bad token, repo unreachable) answers 200 with `success: false` and
  // a message. A rejected *request* — a 401/403 once the session expires with
  // the modal open, a 500, the network dropping — throws in `request()`, so
  // `data` is undefined: reading the message off `data` alone left the picker
  // holding only "Latest" and every category greyed out by an empty availability
  // map, with nothing on screen saying why.
  const queryError = (query: { isError: boolean; error: unknown }) =>
    query.isError ? (query.error as Error)?.message || t('backup.restoreFromGit.loadFailed') : null;

  const previewError =
    queryError(previewQuery) ??
    (previewQuery.data && !previewQuery.data.success ? previewQuery.data.message : null);
  const commitsError =
    queryError(commitsQuery) ??
    (commitsQuery.data && !commitsQuery.data.success ? commitsQuery.data.message : null);

  return (
    <>
      <div
        className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
        onClick={isRestoring ? undefined : closeModal}
      >
        <Card className="w-full max-w-lg" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
          <CardContent className="p-0">
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-full bg-bambu-green/20 text-bambu-green">
                  <RotateCcw className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white">{t('backup.restoreFromGit.title')}</h3>
                  <p className="text-sm text-bambu-gray">{t('backup.restoreFromGit.subtitle')}</p>
                </div>
              </div>
              <button
                onClick={closeModal}
                disabled={isRestoring}
                aria-label={t('common.close')}
                className="p-2 hover:bg-bambu-dark-tertiary rounded-lg transition-colors disabled:opacity-50"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {result ? (
              /* Result summary */
              <div className="p-4 space-y-3 max-h-[400px] overflow-y-auto">
                {/* A partial restore reaches this panel too — categories commit
                    as they finish, so the tallies below are on disk even though
                    the run did not get through them all. It must not read as a
                    success: the message is the failure, and what follows is what
                    survived it rather than what was asked for. */}
                <div className="flex items-start gap-2 text-sm">
                  {result.success ? (
                    <CheckCircle2 className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                  ) : (
                    <AlertTriangle className="w-4 h-4 text-yellow-500 mt-0.5 flex-shrink-0" />
                  )}
                  <span className="text-white">{result.message}</span>
                </div>
                {!result.success && (
                  <p className="text-xs text-bambu-gray">{t('backup.restoreFromGit.partialHint')}</p>
                )}
                {Object.entries(result.results).map(([name, tally]) => (
                  <div key={name} className="p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-white">
                        {CATEGORY_LABEL_KEYS[name] ? t(CATEGORY_LABEL_KEYS[name]) : name}
                      </span>
                      <span className="text-xs text-bambu-gray">
                        {t('backup.restoreFromGit.tally', {
                          restored: tally.restored,
                          skipped: tally.skipped,
                          failed: tally.failed,
                        })}
                      </span>
                    </div>
                    {tally.notes.length > 0 && (
                      <ul className="mt-2 space-y-1">
                        {tally.notes.map((note) => (
                          // The server dedupes on (code, params), not on code
                          // alone — two printers can both be offline — so the
                          // key has to carry the params too.
                          <li
                            key={`${note.code}:${JSON.stringify(note.params)}`}
                            className="text-xs text-bambu-gray flex items-start gap-1.5"
                          >
                            <Info className="w-3 h-3 mt-0.5 flex-shrink-0" />
                            <span>{translateCoded(t, 'notes', note.code, note.params, note.message)}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
                <div className="p-3 rounded-lg bg-yellow-50 dark:bg-yellow-500/10 border border-yellow-300 dark:border-yellow-500/30">
                  <p className="text-xs text-yellow-700 dark:text-yellow-200">
                    {t('backup.restoreFromGit.reloadHint')}
                  </p>
                </div>
              </div>
            ) : (
              <div className={`p-4 space-y-4 max-h-[400px] overflow-y-auto ${isRestoring ? 'opacity-50 pointer-events-none' : ''}`}>
                {/* A restore that was refused or failed comes back here rather
                    than to the result panel, so keep these above the fold. */}
                {restoreMutation.isError && (
                  <div className="p-3 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-300 dark:border-red-500/30">
                    <p className="text-sm text-red-700 dark:text-red-400">
                      {(restoreMutation.error as Error)?.message || t('backup.restoreFromGit.failed')}
                    </p>
                  </div>
                )}
                {restoreMutation.data && !restoreMutation.data.success && (
                  <div className="p-3 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-300 dark:border-red-500/30">
                    <p className="text-sm text-red-700 dark:text-red-400">{restoreMutation.data.message}</p>
                  </div>
                )}

                {/* Commit picker */}
                <div>
                  <label htmlFor="restore-commit" className="block text-sm font-medium text-white mb-1">
                    {t('backup.restoreFromGit.commitLabel')}
                  </label>
                  <select
                    id="restore-commit"
                    value={selectedRef}
                    onChange={(e) => {
                      setSelectedRef(e.target.value);
                      // Drop the previous attempt's failure banner: it refers to
                      // the commit that was just switched away from. (A *result*
                      // cannot be showing here — the summary replaces this form.)
                      restoreMutation.reset();
                    }}
                    disabled={isRestoring || commitsQuery.isLoading}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
                  >
                    <option value={LATEST}>{t('backup.restoreFromGit.latestCommit')}</option>
                    {commits.map((c) => (
                      <option key={c.sha} value={c.sha}>
                        {formatCommitLabel(c.sha, c.message, c.date)}
                      </option>
                    ))}
                  </select>
                  {commitsError && <p className="mt-1 text-xs text-red-500 dark:text-red-400">{commitsError}</p>}
                </div>

                {/* Category selection */}
                <div>
                  <p className="text-sm font-medium text-white mb-2">{t('backup.restoreFromGit.categoriesLabel')}</p>
                  {previewQuery.isLoading ? (
                    <div className="flex items-center gap-2 text-sm text-bambu-gray p-3">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('backup.restoreFromGit.inspecting')}
                    </div>
                  ) : previewError ? (
                    <div className="p-3 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-300 dark:border-red-500/30">
                      <p className="text-sm text-red-700 dark:text-red-400">{previewError}</p>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {CATEGORIES.map((category) => {
                        const info = availability[category.id];
                        const isAvailable = Boolean(info?.available);
                        const isChecked = Boolean(selected[category.id]) && isAvailable;
                        return (
                          <label
                            key={category.id}
                            className={`flex items-center gap-3 p-3 rounded-lg transition-colors ${
                              isAvailable ? 'cursor-pointer' : 'cursor-not-allowed opacity-50'
                            } ${
                              isChecked
                                ? 'bg-bambu-green/10 border border-bambu-green/30'
                                : 'bg-bambu-dark hover:bg-bambu-dark-tertiary border border-transparent'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={isChecked}
                              disabled={!isAvailable || isRestoring}
                              onChange={() =>
                                setSelected((prev) => ({ ...prev, [category.id]: !prev[category.id] }))
                              }
                              className="w-4 h-4 rounded border-bambu-gray bg-bambu-dark text-bambu-green focus:ring-bambu-green focus:ring-offset-0"
                            />
                            <div className={isChecked ? 'text-bambu-green' : 'text-bambu-gray'}>{category.icon}</div>
                            <div className="flex-1">
                              <div className="text-white text-sm font-medium">
                                {t(category.labelKey)}
                                {isAvailable && info?.itemCount ? (
                                  <span className="ml-2 text-xs text-bambu-gray">
                                    {t('backup.restoreFromGit.itemCount', { count: info.itemCount })}
                                  </span>
                                ) : null}
                              </div>
                              {info?.detail && <div className="text-xs text-bambu-gray">{info.detail}</div>}
                              {category.id === 'kprofiles' && isChecked && warnKprofilesOverwrite && (
                                <div className="text-xs text-yellow-700 dark:text-yellow-200">
                                  {t('backup.restoreFromGit.kprofilesOverwriteCaveat')}
                                </div>
                              )}
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Overwrite toggle */}
                <div className="p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium text-white">{t('backup.restoreFromGit.overwriteLabel')}</p>
                      <p className="text-xs text-bambu-gray">
                        {overwriteExisting
                          ? t('backup.restoreFromGit.overwriteOn')
                          : t('backup.restoreFromGit.overwriteOff')}
                      </p>
                    </div>
                    <Toggle checked={overwriteExisting} onChange={setOverwriteExisting} disabled={isRestoring} />
                  </div>
                </div>

              </div>
            )}

            {/* Footer */}
            <div className="flex items-center justify-between p-4 border-t border-bambu-dark-tertiary">
              {result ? (
                <>
                  <span />
                  <div className="flex gap-3">
                    <Button variant="secondary" onClick={closeModal}>
                      {t('common.close')}
                    </Button>
                    <Button
                      onClick={() => window.location.reload()}
                      className="bg-bambu-green hover:bg-bambu-green-dark"
                    >
                      {t('backup.reloadNow')}
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <span className="text-sm text-bambu-gray">
                    {t('backup.restoreFromGit.selectedCount', { count: selectedCount })}
                  </span>
                  <div className="flex gap-3">
                    <Button variant="secondary" onClick={closeModal} disabled={isRestoring}>
                      {t('common.cancel')}
                    </Button>
                    <Button
                      onClick={() => setShowConfirm(true)}
                      disabled={selectedCount === 0 || isRestoring}
                      className="bg-bambu-green hover:bg-bambu-green-dark disabled:opacity-50 disabled:cursor-not-allowed min-w-[100px]"
                    >
                      {isRestoring ? (
                        <>
                          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                          {t('backup.restoreFromGit.restoring')}
                        </>
                      ) : (
                        <>
                          <RotateCcw className="w-4 h-4 mr-2" />
                          {t('backup.restore')}
                        </>
                      )}
                    </Button>
                  </div>
                </>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {showConfirm && (
        <ConfirmModal
          variant="danger"
          overlayZIndex="z-[110]"
          title={t('backup.restoreFromGit.confirmTitle')}
          message={
            overwriteExisting
              ? t('backup.restoreFromGit.confirmMessageOverwrite')
              : warnKprofilesOverwrite
                ? `${t('backup.restoreFromGit.confirmMessage')} ${t('backup.restoreFromGit.kprofilesOverwriteCaveat')}`
                : t('backup.restoreFromGit.confirmMessage')
          }
          confirmText={t('backup.restore')}
          isLoading={isRestoring}
          loadingText={t('backup.restoreFromGit.restoring')}
          onConfirm={() => restoreMutation.mutate()}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </>
  );
}
