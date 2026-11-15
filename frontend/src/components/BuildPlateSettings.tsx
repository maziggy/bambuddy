import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Layers, Plus, Trash2, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import type { BuildPlate } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { Card, CardContent, CardHeader } from './Card';
import { ConfirmModal } from './ConfirmModal';
import { BUILD_PLATES_QUERY_KEY, groupPlatesByBaseType, plateImage } from '../utils/buildPlates';
import { PlateImage } from './PlateImage';

interface BuildPlateSettingsProps {
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
}

/** Plate photo, or a generic icon for a plate with no photo and no known type. */
function PlatePhoto({ plate }: { plate: BuildPlate }) {
  if (!plateImage(plate)) {
    return <Layers className="w-8 h-8 text-bambu-gray" />;
  }
  return <PlateImage plate={plate} className="w-full h-full object-cover" />;
}

/**
 * Settings → Queue → Build Plates (#1306).
 *
 * Opt-in: the toggle is an ordinary app setting saved with the rest of the
 * page. The plate list below it saves as the user ticks, like the other
 * catalog editors, so it works whether or not tracking is switched on yet.
 */
export function BuildPlateSettings({ enabled, onToggle }: BuildPlateSettingsProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const { data: plates = [], isLoading } = useQuery({
    queryKey: BUILD_PLATES_QUERY_KEY,
    queryFn: api.getBuildPlates,
    enabled,
  });
  const { data: bedTypes = [] } = useQuery({
    queryKey: ['build-plates', 'bed-types'],
    queryFn: api.getBedTypes,
    enabled,
    staleTime: Infinity,
  });

  const [showAdd, setShowAdd] = useState(false);
  const [name, setName] = useState('');
  const [baseType, setBaseType] = useState('smooth_pei');
  const [pattern, setPattern] = useState('');
  const [image, setImage] = useState('');
  const [toDelete, setToDelete] = useState<BuildPlate | null>(null);
  const [toUntick, setToUntick] = useState<BuildPlate | null>(null);
  const [showResetNote, setShowResetNote] = useState(false);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: BUILD_PLATES_QUERY_KEY });

  const toggleMutation = useMutation({
    mutationFn: ({ id, value }: { id: number; value: boolean }) => api.updateBuildPlate(id, { enabled: value }),
    onSuccess: invalidate,
    onError: (e: Error) => showToast(e.message || t('settings.buildPlates.saveFailed'), 'error'),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      api.createBuildPlate({
        name: name.trim(),
        base_type: baseType,
        pattern: pattern.trim() || null,
        image: image.trim() || null,
      }),
    onSuccess: () => {
      invalidate();
      setShowAdd(false);
      setName('');
      setPattern('');
      setImage('');
      showToast(t('settings.buildPlates.added'), 'success');
    },
    onError: (e: Error) => showToast(e.message || t('settings.buildPlates.saveFailed'), 'error'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteBuildPlate(id),
    onSuccess: () => {
      invalidate();
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      setToDelete(null);
    },
    onError: (e: Error) => showToast(e.message || t('settings.buildPlates.saveFailed'), 'error'),
  });

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
          <>
            <div>
              <p className="text-sm text-white">{t('settings.buildPlates.ownedTitle')}</p>
              <p className="text-xs text-bambu-gray mt-0.5">{t('settings.buildPlates.ownedDescription')}</p>
            </div>

            {isLoading ? (
              <div className="flex justify-center py-4">
                <Loader2 className="w-5 h-5 animate-spin text-bambu-gray" />
              </div>
            ) : (
              groupPlatesByBaseType(plates).map((group) => (
                <div key={group.baseType} className="space-y-2">
                  <p className="text-xs font-medium uppercase tracking-wide text-bambu-gray">{group.label}</p>
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-3">
                    {group.plates.map((plate) => (
                      <label
                        key={plate.id}
                        className={`relative flex flex-col rounded-lg border cursor-pointer overflow-hidden transition-colors ${
                          plate.enabled
                            ? 'border-bambu-green bg-bambu-dark'
                            : 'border-bambu-dark-tertiary bg-bambu-dark/50 opacity-70 hover:opacity-100'
                        }`}
                      >
                        <div className="aspect-[4/3] bg-bambu-dark-tertiary flex items-center justify-center overflow-hidden">
                          <PlatePhoto plate={plate} />
                        </div>
                        <div className="flex items-start gap-2 p-2">
                          <input
                            type="checkbox"
                            checked={plate.enabled}
                            disabled={toggleMutation.isPending}
                            onChange={(e) => {
                              const value = e.target.checked;
                              // Unticking a plate in use hides it from new jobs,
                              // but jobs already waiting for it keep waiting.
                              if (!value && (plate.installed_on.length > 0 || plate.required_by_pending > 0)) {
                                setToUntick(plate);
                              } else {
                                toggleMutation.mutate({ id: plate.id, value });
                              }
                            }}
                            className="mt-0.5 accent-bambu-green"
                          />
                          <div className="min-w-0 flex-1">
                            <p className="text-sm text-white leading-tight break-words">{plate.name}</p>
                            {plate.pattern && <p className="text-xs text-bambu-gray">{plate.pattern}</p>}
                          </div>
                          {!plate.is_builtin && (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.preventDefault();
                                setToDelete(plate);
                              }}
                              className="text-bambu-gray hover:text-red-400"
                              title={t('common.delete')}
                              aria-label={t('common.delete')}
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          )}
                        </div>
                      </label>
                    ))}
                  </div>
                </div>
              ))
            )}

            {showAdd ? (
              <div className="space-y-3 rounded-lg border border-bambu-dark-tertiary p-3">
                <p className="text-sm text-white">{t('settings.buildPlates.addTitle')}</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.buildPlates.name')}</label>
                    <input
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      maxLength={100}
                      placeholder={t('settings.buildPlates.namePlaceholder')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.buildPlates.baseType')}</label>
                    <select
                      value={baseType}
                      onChange={(e) => setBaseType(e.target.value)}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    >
                      {bedTypes.map((bt) => (
                        <option key={bt.key} value={bt.key}>
                          {bt.label}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-bambu-gray mt-1">{t('settings.buildPlates.baseTypeHint')}</p>
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.buildPlates.pattern')}</label>
                    <input
                      value={pattern}
                      onChange={(e) => setPattern(e.target.value)}
                      maxLength={100}
                      placeholder={t('settings.buildPlates.patternPlaceholder')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.buildPlates.image')}</label>
                    <input
                      value={image}
                      onChange={(e) => setImage(e.target.value)}
                      maxLength={500}
                      placeholder="https://…"
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setShowAdd(false)}
                    className="px-3 py-1.5 text-sm text-bambu-gray hover:text-white"
                  >
                    {t('common.cancel')}
                  </button>
                  <button
                    type="button"
                    disabled={!name.trim() || createMutation.isPending}
                    onClick={() => createMutation.mutate()}
                    className="px-3 py-1.5 text-sm rounded-lg bg-bambu-green text-white disabled:opacity-50"
                  >
                    {t('settings.buildPlates.add')}
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setShowAdd(true)}
                className="flex items-center gap-1.5 text-sm text-bambu-green hover:underline"
              >
                <Plus className="w-4 h-4" />
                {t('settings.buildPlates.addCustom')}
              </button>
            )}
          </>
        )}
      </CardContent>

      {toUntick && (
        <ConfirmModal
          title={t('settings.buildPlates.untickTitle')}
          message={t('settings.buildPlates.untickMessage', {
            name: toUntick.name,
            printers: toUntick.installed_on.length,
            jobs: toUntick.required_by_pending,
          })}
          confirmText={t('settings.buildPlates.untickConfirm')}
          variant="warning"
          onConfirm={() => {
            toggleMutation.mutate({ id: toUntick.id, value: false });
            setToUntick(null);
          }}
          onCancel={() => setToUntick(null)}
        />
      )}

      {toDelete && (
        <ConfirmModal
          title={t('settings.buildPlates.deleteTitle')}
          message={t('settings.buildPlates.deleteMessage', { name: toDelete.name })}
          confirmText={t('common.delete')}
          variant="danger"
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(toDelete.id)}
          onCancel={() => setToDelete(null)}
        />
      )}
    </Card>
  );
}
