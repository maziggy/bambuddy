import { useState, useEffect, useMemo } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, Save, Beaker, Palette } from 'lucide-react';
import { api } from '../api/client';
import type { InventorySpool, SlicerSetting, SpoolCatalogEntry, LocalPreset } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import type { SpoolFormData, PrinterWithCalibrations, ColorPreset } from './spool-form/types';
import { defaultFormData, validateForm } from './spool-form/types';
import { buildFilamentOptions, extractBrandsFromPresets, findPresetOption, loadRecentColors, saveRecentColor } from './spool-form/utils';
import { FilamentSection } from './spool-form/FilamentSection';
import { ColorSection } from './spool-form/ColorSection';
import { AdditionalSection } from './spool-form/AdditionalSection';
import { PAProfileSection } from './spool-form/PAProfileSection';
import { SpoolUsageHistory } from './SpoolUsageHistory';

type TabId = 'filament' | 'pa-profile';

interface SpoolFormModalProps {
  isOpen: boolean;
  onClose: () => void;
  spool?: InventorySpool | null;
  printersWithCalibrations?: PrinterWithCalibrations[];
}

export function SpoolFormModal({ isOpen, onClose, spool, printersWithCalibrations = [] }: SpoolFormModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const isEditing = !!spool;

  // Form state
  const [formData, setFormData] = useState<SpoolFormData>(defaultFormData);
  const [errors, setErrors] = useState<Partial<Record<keyof SpoolFormData, string>>>({});
  const [activeTab, setActiveTab] = useState<TabId>('filament');

  // Cloud presets
  const [cloudAuthenticated, setCloudAuthenticated] = useState(false);
  const [loadingCloudPresets, setLoadingCloudPresets] = useState(false);
  const [cloudPresets, setCloudPresets] = useState<SlicerSetting[]>([]);
  const [presetInputValue, setPresetInputValue] = useState('');

  // Spool catalog
  const [spoolCatalog, setSpoolCatalog] = useState<SpoolCatalogEntry[]>([]);

  // Local presets (OrcaSlicer imports)
  const [localPresets, setLocalPresets] = useState<LocalPreset[]>([]);

  // Color catalog
  const [colorCatalog, setColorCatalog] = useState<{ manufacturer: string; color_name: string; hex_color: string; material: string | null }[]>([]);

  // Color state
  const [recentColors, setRecentColors] = useState<ColorPreset[]>([]);

  // PA Profile state
  const [fetchedCalibrations, setFetchedCalibrations] = useState<PrinterWithCalibrations[]>([]);
  const [selectedProfiles, setSelectedProfiles] = useState<Set<string>>(new Set());
  const [expandedPrinters, setExpandedPrinters] = useState<Set<string>>(new Set());

  // Use prop if provided, otherwise use self-fetched data
  const resolvedCalibrations = printersWithCalibrations.length > 0
    ? printersWithCalibrations
    : fetchedCalibrations;

  // Count selected PA profiles for tab badge
  const selectedProfileCount = useMemo(() => {
    return selectedProfiles.size;
  }, [selectedProfiles]);

  // Load recent colors on mount
  useEffect(() => {
    setRecentColors(loadRecentColors());
  }, []);

  // Fetch cloud presets and catalog when modal opens
  useEffect(() => {
    if (isOpen) {
      const fetchData = async () => {
        setLoadingCloudPresets(true);
        try {
          const status = await api.getCloudStatus();
          setCloudAuthenticated(status.is_authenticated);
          if (status.is_authenticated) {
            const presets = await api.getFilamentPresets();
            setCloudPresets(presets);
          }
        } catch (e) {
          console.error('Failed to fetch cloud presets:', e);
          setCloudAuthenticated(false);
        } finally {
          setLoadingCloudPresets(false);
        }
      };
      fetchData();
      api.getSpoolCatalog().then(setSpoolCatalog).catch(console.error);
      api.getColorCatalog().then(setColorCatalog).catch(console.error);
      api.getLocalPresets().then(r => setLocalPresets(r.filament)).catch(console.error);

      // Fetch printer calibrations if not provided via props
      if (printersWithCalibrations.length === 0) {
        (async () => {
          try {
            const printers = await api.getPrinters();
            const statuses = await Promise.all(
              printers.map(p => api.getPrinterStatus(p.id).catch(() => null)),
            );
            const results: PrinterWithCalibrations[] = [];
            for (let i = 0; i < printers.length; i++) {
              const printer = printers[i];
              const status = statuses[i];
              const connected = status?.connected ?? false;
              let calibrations: PrinterWithCalibrations['calibrations'] = [];
              if (connected) {
                try {
                  const kRes = await api.getKProfiles(printer.id);
                  calibrations = kRes.profiles.map(p => ({
                    cali_idx: p.slot_id,
                    filament_id: p.filament_id,
                    setting_id: p.setting_id || '',
                    name: p.name,
                    k_value: parseFloat(p.k_value) || 0,
                    n_coef: parseFloat(p.n_coef) || 0,
                    extruder_id: p.extruder_id,
                    nozzle_diameter: p.nozzle_diameter,
                  }));
                } catch {
                  // Printer may not support K-profiles
                }
              }
              results.push({ printer: { ...printer, connected }, calibrations });
            }
            setFetchedCalibrations(results);
          } catch (e) {
            console.error('Failed to fetch printer calibrations:', e);
          }
        })();
      }
    }
  }, [isOpen, printersWithCalibrations.length]);

  // Build filament options: cloud → local → fallback
  const filamentOptions = useMemo(
    () => buildFilamentOptions(cloudPresets, new Set(), localPresets),
    [cloudPresets, localPresets],
  );

  // Extract brands from presets
  const availableBrands = useMemo(
    () => extractBrandsFromPresets(cloudPresets, localPresets),
    [cloudPresets, localPresets],
  );

  // Find selected preset option
  const selectedPresetOption = useMemo(
    () => findPresetOption(formData.slicer_filament, filamentOptions),
    [formData.slicer_filament, filamentOptions],
  );

  // Reset form when modal opens/closes or spool changes
  useEffect(() => {
    if (isOpen) {
      if (spool) {
        setFormData({
          material: spool.material || '',
          subtype: spool.subtype || '',
          brand: spool.brand || '',
          color_name: spool.color_name || '',
          rgba: spool.rgba || '808080FF',
          label_weight: spool.label_weight || 1000,
          core_weight: spool.core_weight || 250,
          weight_used: spool.weight_used || 0,
          slicer_filament: spool.slicer_filament || '',
          note: spool.note || '',
        });
        setPresetInputValue(spool.slicer_filament_name || spool.slicer_filament || '');

        // Load K-profiles for this spool
        if (spool.k_profiles && spool.k_profiles.length > 0) {
          const profileKeys = new Set<string>();
          for (const p of spool.k_profiles) {
            if (p.cali_idx !== null && p.cali_idx !== undefined) {
              profileKeys.add(`${p.printer_id}:${p.cali_idx}:${p.extruder ?? 'null'}`);
            }
          }
          setSelectedProfiles(profileKeys);
        } else {
          setSelectedProfiles(new Set());
        }
      } else {
        setFormData(defaultFormData);
        setPresetInputValue('');
        setSelectedProfiles(new Set());
      }
      setErrors({});
      setActiveTab('filament');
    }
  }, [isOpen, spool]);

  // Expand all printers in PA profile section when calibrations are available
  useEffect(() => {
    if (isOpen && resolvedCalibrations.length > 0) {
      setExpandedPrinters(new Set(resolvedCalibrations.map(p => String(p.printer.id))));
    }
  }, [isOpen, resolvedCalibrations]);

  // Update field helper
  const updateField = <K extends keyof SpoolFormData>(key: K, value: SpoolFormData[K]) => {
    setFormData(prev => ({ ...prev, [key]: value }));
    if (errors[key]) {
      setErrors(prev => ({ ...prev, [key]: undefined }));
    }
  };

  // Handle color selection
  const handleColorUsed = (color: ColorPreset) => {
    setRecentColors(prev => saveRecentColor(color, prev));
  };

  // Mutations
  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.createSpool(data as Parameters<typeof api.createSpool>[0]),
    onSuccess: async (newSpool) => {
      // Save K-profiles if any selected
      if (selectedProfiles.size > 0 && newSpool?.id) {
        await saveKProfiles(newSpool.id);
      }
      await queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      showToast(t('inventory.spoolCreated'), 'success');
      onClose();
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const updateMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api.updateSpool(spool!.id, data as Parameters<typeof api.updateSpool>[1]),
    onSuccess: async () => {
      // Save K-profiles
      if (spool?.id) {
        await saveKProfiles(spool.id);
      }
      await queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      showToast(t('inventory.spoolUpdated'), 'success');
      onClose();
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // Save K-profiles for selected calibrations
  const saveKProfiles = async (spoolId: number) => {
    if (selectedProfiles.size === 0) {
      // Clear existing K-profiles
      try {
        await api.saveSpoolKProfiles(spoolId, []);
      } catch {
        // Ignore
      }
      return;
    }

    const profiles = [];
    for (const key of selectedProfiles) {
      const [printerIdStr, caliIdxStr, extruderStr] = key.split(':');
      const printerId = parseInt(printerIdStr);
      const caliIdx = parseInt(caliIdxStr);
      const extruder = extruderStr === 'null' ? 0 : parseInt(extruderStr);

      // Find the matching calibration
      const pc = resolvedCalibrations.find(p => p.printer.id === printerId);
      if (pc) {
        const cal = pc.calibrations.find(c => c.cali_idx === caliIdx);
        if (cal) {
          profiles.push({
            printer_id: printerId,
            extruder,
            nozzle_diameter: cal.nozzle_diameter || '0.4',
            k_value: cal.k_value,
            name: cal.name || null,
            cali_idx: cal.cali_idx,
            setting_id: cal.setting_id || null,
          });
        }
      }
    }

    if (profiles.length > 0) {
      try {
        await api.saveSpoolKProfiles(spoolId, profiles);
      } catch (e) {
        console.error('Failed to save K-profiles:', e);
      }
    }
  };

  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSubmit = () => {
    const validation = validateForm(formData);
    if (!validation.isValid) {
      setErrors(validation.errors);
      // Switch to filament tab if there are errors there
      if (validation.errors.slicer_filament || validation.errors.material) {
        setActiveTab('filament');
      }
      return;
    }

    // Find preset name from selected option
    const presetName = selectedPresetOption?.displayName || presetInputValue || null;

    const data: Record<string, unknown> = {
      material: formData.material,
      subtype: formData.subtype || null,
      brand: formData.brand || null,
      color_name: formData.color_name || null,
      rgba: formData.rgba || null,
      label_weight: formData.label_weight,
      core_weight: formData.core_weight,
      weight_used: formData.weight_used,
      slicer_filament: formData.slicer_filament || null,
      slicer_filament_name: presetName,
      nozzle_temp_min: null,
      nozzle_temp_max: null,
      note: formData.note || null,
    };

    if (isEditing) {
      updateMutation.mutate(data);
    } else {
      createMutation.mutate(data);
    }
  };

  const isPending = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative w-full max-w-lg mx-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary flex-shrink-0">
          <h2 className="text-lg font-semibold text-white">
            {isEditing ? t('inventory.editSpool') : t('inventory.addSpool')}
          </h2>
          <button
            onClick={onClose}
            className="p-1 text-bambu-gray hover:text-white rounded transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-bambu-dark-tertiary flex-shrink-0">
          <button
            onClick={() => setActiveTab('filament')}
            className={`flex-1 px-4 py-2.5 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${
              activeTab === 'filament'
                ? 'text-bambu-green border-b-2 border-bambu-green'
                : 'text-bambu-gray hover:text-white'
            }`}
          >
            <Palette className="w-4 h-4" />
            {t('inventory.filamentInfoTab')}
          </button>
          <button
            onClick={() => setActiveTab('pa-profile')}
            className={`flex-1 px-4 py-2.5 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${
              activeTab === 'pa-profile'
                ? 'text-bambu-green border-b-2 border-bambu-green'
                : 'text-bambu-gray hover:text-white'
            }`}
          >
            <Beaker className="w-4 h-4" />
            {t('inventory.paProfileTab')}
            {selectedProfileCount > 0 && (
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-bambu-green/20 text-bambu-green">
                {selectedProfileCount}
              </span>
            )}
          </button>
        </div>

        {/* Content */}
        <div className="p-4 overflow-y-auto flex-1">
          {activeTab === 'filament' ? (
            <div className="space-y-6">
              {/* Filament Info Section */}
              <div>
                <h3 className="text-sm font-semibold text-bambu-gray uppercase tracking-wide mb-3">
                  {t('inventory.filamentInfo')}
                </h3>
                <FilamentSection
                  formData={formData}
                  updateField={updateField}
                  cloudAuthenticated={cloudAuthenticated}
                  loadingCloudPresets={loadingCloudPresets}
                  presetInputValue={presetInputValue}
                  setPresetInputValue={setPresetInputValue}
                  selectedPresetOption={selectedPresetOption}
                  filamentOptions={filamentOptions}
                  availableBrands={availableBrands}
                />
                {errors.slicer_filament && (
                  <p className="mt-1 text-xs text-red-400">{errors.slicer_filament}</p>
                )}
                {errors.material && (
                  <p className="mt-1 text-xs text-red-400">{errors.material}</p>
                )}
              </div>

              {/* Color Section */}
              <div>
                <h3 className="text-sm font-semibold text-bambu-gray uppercase tracking-wide mb-3">
                  {t('inventory.color')}
                </h3>
                <ColorSection
                  formData={formData}
                  updateField={updateField}
                  recentColors={recentColors}
                  onColorUsed={handleColorUsed}
                  catalogColors={colorCatalog}
                />
              </div>

              {/* Additional Section */}
              <div>
                <h3 className="text-sm font-semibold text-bambu-gray uppercase tracking-wide mb-3">
                  {t('inventory.additional')}
                </h3>
                <AdditionalSection
                  formData={formData}
                  updateField={updateField}
                  spoolCatalog={spoolCatalog}
                />
              </div>

              {/* Usage History (only when editing) */}
              {isEditing && spool && (
                <div>
                  <SpoolUsageHistory spoolId={spool.id} />
                </div>
              )}
            </div>
          ) : (
            <PAProfileSection
              formData={formData}
              updateField={updateField}
              printersWithCalibrations={resolvedCalibrations}
              selectedProfiles={selectedProfiles}
              setSelectedProfiles={setSelectedProfiles}
              expandedPrinters={expandedPrinters}
              setExpandedPrinters={setExpandedPrinters}
            />
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 p-4 border-t border-bambu-dark-tertiary flex-shrink-0">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={isPending}
          >
            {isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('common.saving')}
              </>
            ) : (
              <>
                <Save className="w-4 h-4" />
                {isEditing ? t('common.save') : t('inventory.addSpool')}
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
