import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Layers, Loader2, Plus, X } from 'lucide-react';
import { api } from '../api/client';
import type { BuildPlateConfig, PlatePatternInfo, PlateTypeInfo } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { Card, CardContent, CardHeader } from './Card';
import { ConfirmModal } from './ConfirmModal';
import { BUILD_PLATES_QUERY_KEY, baseTypeIcon } from '../utils/buildPlates';

interface BuildPlateSettingsProps {
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
}

type Draft = { types: string[]; patterns: { base_type: string; name: string }[] };

function toDraft(config: BuildPlateConfig): Draft {
  return {
    types: config.types.filter((type) => type.owned).map((type) => type.key),
    patterns: config.patterns.map((p) => ({ base_type: p.base_type, name: p.name })),
  };
}

const inUse = (plate: { installed_on: number[]; waiting_jobs: number }) =>
  plate.installed_on.length > 0 || plate.waiting_jobs > 0;

/** Text field and button that add a pattern under one plate type. */
function AddPattern({
  suggestions,
  onAdd,
  disabled,
}: {
  suggestions: string[];
  onAdd: (name: string) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const listId = `plate-pattern-suggestions-${suggestions.length}`;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-1 text-xs text-bambu-green hover:underline"
      >
        <Plus className="w-3.5 h-3.5" />
        {t('settings.buildPlates.addPattern')}
      </button>
    );
  }
  const submit = () => {
    const value = name.trim();
    if (value) onAdd(value);
    setName('');
    setOpen(false);
  };
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <input
        autoFocus
        list={listId}
        value={name}
        maxLength={64}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}
        placeholder={t('settings.buildPlates.patternPlaceholder')}
        aria-label={t('settings.buildPlates.addPattern')}
        className="min-w-0 flex-1 px-2 py-1 text-xs bg-bambu-dark border border-bambu-dark-tertiary rounded text-white focus:border-bambu-green focus:outline-none"
      />
      <datalist id={listId}>
        {suggestions.map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
      <button
        type="submit"
        disabled={disabled || !name.trim()}
        className="px-2 py-1 text-xs rounded bg-bambu-green text-white disabled:opacity-50"
      >
        {t('settings.buildPlates.patternAdd')}
      </button>
    </form>
  );
}

/**
 * Settings → Workflow → Build Plates (#1306).
 *
 * Opt-in: the toggle is an ordinary app setting saved with the rest of the page.
 * Below it, the user ticks the plate types they own and adds patterns (such as
 * 3D Effect sheets) under them. That list saves as it changes, like the other
 * catalog editors, and only narrows what the plate pickers offer.
 */
export function BuildPlateSettings({ enabled, onToggle }: BuildPlateSettingsProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const { data: config, isLoading } = useQuery({
    queryKey: BUILD_PLATES_QUERY_KEY,
    queryFn: api.getBuildPlates,
    enabled,
  });

  const [toUntick, setToUntick] = useState<PlateTypeInfo | null>(null);
  const [toRemove, setToRemove] = useState<PlatePatternInfo | null>(null);
  const [showResetNote, setShowResetNote] = useState(false);

  const saveMutation = useMutation({
    mutationFn: (draft: Draft) => api.saveBuildPlates(draft),
    onSuccess: (saved) => {
      queryClient.setQueryData(BUILD_PLATES_QUERY_KEY, saved);
      // Removing a pattern can change printers and queued jobs.
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      queryClient.invalidateQueries({ queryKey: ['queue'] });
    },
    onError: (e: Error) => showToast(e.message || t('settings.buildPlates.saveFailed'), 'error'),
  });

  const save = (change: (draft: Draft) => Draft) => {
    if (config) saveMutation.mutate(change(toDraft(config)));
  };
  const setOwned = (key: string, owned: boolean) =>
    save((d) => ({ ...d, types: owned ? [...d.types, key] : d.types.filter((k) => k !== key) }));
  const addPattern = (baseType: string, name: string) =>
    save((d) => ({ ...d, patterns: [...d.patterns, { base_type: baseType, name }] }));
  const removePattern = (pattern: PlatePatternInfo) =>
    save((d) => ({
      ...d,
      patterns: d.patterns.filter((p) => !(p.base_type === pattern.base_type && p.name === pattern.name)),
    }));

  return (
    <Card>
      <CardHeader>
        <h3 className="text-base font-semibold text-white flex items-center gap-2" id="card-build-plates">
          <Layers className="w-4 h-4 text-bambu-green" />
          {t('settings.buildPlates.title')}
        </h3>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <label className="block text-sm text-white">{t('settings.buildPlates.enable')}</label>
            <p className="text-xs text-bambu-gray mt-0.5">{t('settings.buildPlates.enableDescription')}</p>
          </div>
          <label className="relative inline-flex items-center cursor-pointer shrink-0">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => {
                // Switching back on clears every printer's recorded plate on
                // save (they may have been swapped while untracked); say so.
                setShowResetNote(e.target.checked && !enabled);
                onToggle(e.target.checked);
              }}
              className="sr-only peer"
              aria-label={t('settings.buildPlates.enable')}
            />
            <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
          </label>
        </div>

        {enabled && showResetNote && (
          <p className="text-xs text-amber-600 dark:text-amber-400">{t('settings.buildPlates.reEnabledNote')}</p>
        )}

        {enabled && (
          <div className="space-y-2">
            <div>
              <p className="text-sm text-white">{t('settings.buildPlates.ownedTitle')}</p>
              <p className="text-xs text-bambu-gray mt-0.5">{t('settings.buildPlates.ownedDescription')}</p>
            </div>
            {isLoading || !config ? (
              <div className="flex justify-center py-4">
                <Loader2 className="w-5 h-5 animate-spin text-bambu-gray" />
              </div>
            ) : (
              <ul className="divide-y divide-bambu-dark-tertiary rounded-lg border border-bambu-dark-tertiary">
                {config.types.map((type) => {
                  const icon = baseTypeIcon(type.key);
                  const patterns = config.patterns.filter((p) => p.base_type === type.key);
                  return (
                    <li key={type.key} className="px-3 py-2 space-y-2">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={type.owned}
                          disabled={saveMutation.isPending}
                          onChange={(e) => {
                            if (!e.target.checked && inUse(type)) setToUntick(type);
                            else setOwned(type.key, e.target.checked);
                          }}
                          className="accent-bambu-green"
                        />
                        {icon && <img src={icon} alt="" className="w-5 h-5 object-contain" />}
                        <span className="text-sm text-white">{type.label}</span>
                      </label>
                      {type.owned && (
                        <div className="pl-6 flex flex-wrap items-center gap-2">
                          {patterns.map((pattern) => (
                            <span
                              key={pattern.name}
                              className="flex items-center gap-1 rounded-full bg-bambu-dark-tertiary pl-2 pr-1 py-0.5 text-xs text-white"
                            >
                              {pattern.name}
                              <button
                                type="button"
                                disabled={saveMutation.isPending}
                                onClick={() => (inUse(pattern) ? setToRemove(pattern) : removePattern(pattern))}
                                aria-label={t('settings.buildPlates.removePattern', { name: pattern.label })}
                                className="text-bambu-gray hover:text-red-400"
                              >
                                <X className="w-3.5 h-3.5" />
                              </button>
                            </span>
                          ))}
                          <AddPattern
                            // The suggestions are the Bambu 3D Effect sheets, all sliced as Smooth PEI.
                            suggestions={type.key === 'smooth_pei' ? config.pattern_suggestions : []}
                            onAdd={(name) => addPattern(type.key, name)}
                            disabled={saveMutation.isPending}
                          />
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        {toUntick && (
          <ConfirmModal
            title={t('settings.buildPlates.untickTitle')}
            message={t('settings.buildPlates.untickMessage', {
              name: toUntick.label,
              printers: toUntick.installed_on.length,
              jobs: toUntick.waiting_jobs,
            })}
            confirmText={t('settings.buildPlates.untickConfirm')}
            variant="warning"
            onConfirm={() => {
              setOwned(toUntick.key, false);
              setToUntick(null);
            }}
            onCancel={() => setToUntick(null)}
          />
        )}
        {toRemove && (
          <ConfirmModal
            title={t('settings.buildPlates.removePatternTitle')}
            message={t('settings.buildPlates.removePatternMessage', {
              name: toRemove.label,
              printers: toRemove.installed_on.length,
              jobs: toRemove.waiting_jobs,
            })}
            confirmText={t('settings.buildPlates.removePatternConfirm')}
            variant="danger"
            onConfirm={() => {
              removePattern(toRemove);
              setToRemove(null);
            }}
            onCancel={() => setToRemove(null)}
          />
        )}
      </CardContent>
    </Card>
  );
}
