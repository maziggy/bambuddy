import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Package, Trash2, Upload } from 'lucide-react';
import { api, type SlicerBundle } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';

// Settings panel for managing BambuStudio "Printer Preset Bundles"
// (.bbscfg) on the slicer sidecar. Sits below the slicer-API URL panel
// in SettingsPage and is hidden when use_slicer_api is off — without a
// configured sidecar there's nowhere to upload bundles to.
//
// Backend wiring: backend/app/api/routes/slicer_presets.py exposes
// /api/v1/slicer/bundles (POST/GET/DELETE). The list call returns []
// when no sidecar is configured, so an empty render is the natural
// "first-run" state for users who haven't enabled the sidecar yet.
export function SlicerBundlesPanel() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pendingDelete, setPendingDelete] = useState<SlicerBundle | null>(null);

  const { data: bundles, isLoading } = useQuery({
    queryKey: ['slicer-bundles'],
    queryFn: api.listSlicerBundles,
  });

  const importMutation = useMutation({
    mutationFn: (file: File) => api.importSlicerBundle(file),
    onSuccess: (bundle) => {
      queryClient.invalidateQueries({ queryKey: ['slicer-bundles'] });
      showToast(
        t('settings.slicerBundles.uploadSuccess', {
          defaultValue: 'Imported {{name}}',
          name: bundle.printer_preset_name,
        }),
        'success',
      );
      // Reset the file input so the same file can be re-selected after a
      // failed retry. (Without this, a second click on the same file
      // doesn't trigger onChange and looks like the panel is broken.)
      if (fileInputRef.current) fileInputRef.current.value = '';
    },
    onError: (err: Error) => {
      showToast(
        t('settings.slicerBundles.uploadError', {
          defaultValue: 'Bundle upload failed: {{message}}',
          message: err.message,
        }),
        'error',
      );
      if (fileInputRef.current) fileInputRef.current.value = '';
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (bundleId: string) => api.deleteSlicerBundle(bundleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['slicer-bundles'] });
      setPendingDelete(null);
      showToast(
        t('settings.slicerBundles.deleteSuccess', {
          defaultValue: 'Bundle removed',
        }),
        'success',
      );
    },
    onError: (err: Error) => {
      showToast(
        t('settings.slicerBundles.deleteError', {
          defaultValue: 'Bundle delete failed: {{message}}',
          message: err.message,
        }),
        'error',
      );
      setPendingDelete(null);
    },
  });

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    importMutation.mutate(file);
  };

  return (
    <Card>
      <CardHeader>
        <h3 className="text-base font-semibold text-white flex items-center gap-2">
          <Package className="w-4 h-4 text-bambu-green" />
          {t('settings.slicerBundles.title', { defaultValue: 'Slicer Bundles' })}
        </h3>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-bambu-gray">
          {t('settings.slicerBundles.description', {
            defaultValue:
              'Import a Printer Preset Bundle (.bbscfg) exported from BambuStudio (File → Export → Export Preset Bundle → "Printer preset bundle"). Once imported, slice requests can pick presets from the bundle by name without re-uploading the JSON profile triplet.',
          })}
        </p>

        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".bbscfg,.zip,application/zip"
            onChange={handleFileChange}
            className="hidden"
            disabled={importMutation.isPending}
          />
          <Button
            variant="primary"
            onClick={() => fileInputRef.current?.click()}
            disabled={importMutation.isPending}
          >
            {importMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('settings.slicerBundles.uploading', { defaultValue: 'Uploading…' })}
              </>
            ) : (
              <>
                <Upload className="w-4 h-4" />
                {t('settings.slicerBundles.uploadButton', { defaultValue: 'Upload bundle' })}
              </>
            )}
          </Button>
        </div>

        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-bambu-gray">
            <Loader2 className="w-4 h-4 animate-spin" />
            {t('settings.slicerBundles.loading', { defaultValue: 'Loading bundles…' })}
          </div>
        ) : bundles && bundles.length > 0 ? (
          <ul className="divide-y divide-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-lg">
            {bundles.map((b) => (
              <li
                key={b.id}
                className="flex items-center justify-between px-3 py-2 hover:bg-bambu-dark-tertiary/30"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-white truncate">{b.printer_preset_name}</p>
                  <p className="text-xs text-bambu-gray mt-0.5">
                    {t('settings.slicerBundles.summary', {
                      defaultValue:
                        '{{processCount}} process · {{filamentCount}} filament presets',
                      processCount: b.process.length,
                      filamentCount: b.filament.length,
                    })}
                    {b.version && ` · v${b.version}`}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setPendingDelete(b)}
                  disabled={deleteMutation.isPending}
                  className="ml-3 p-1.5 text-bambu-gray hover:text-red-400 disabled:opacity-40"
                  aria-label={t('settings.slicerBundles.delete', { defaultValue: 'Delete' })}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-bambu-gray italic">
            {t('settings.slicerBundles.empty', {
              defaultValue: 'No bundles imported yet.',
            })}
          </p>
        )}
      </CardContent>

      {pendingDelete && (
        <ConfirmModal
          title={t('settings.slicerBundles.confirmDeleteTitle', {
            defaultValue: 'Remove this bundle?',
          })}
          message={t('settings.slicerBundles.confirmDeleteMessage', {
            defaultValue:
              'Slice requests that reference "{{name}}" will fail until the bundle is re-imported.',
            name: pendingDelete.printer_preset_name,
          })}
          confirmText={t('common.delete', { defaultValue: 'Delete' })}
          variant="danger"
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(pendingDelete.id)}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </Card>
  );
}
