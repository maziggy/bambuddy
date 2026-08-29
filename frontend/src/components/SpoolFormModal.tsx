import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, Save, Beaker, Palette, Zap, Tag, Unlink } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { InventorySpool, SlicerSetting, SpoolCatalogEntry, LocalPreset, BuiltinFilament, SpoolmanBulkCreateResult, SpoolFilamentPresetInput, SpoolKProfileInput, SpoolmanFilamentEntry } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import type {
  CalibrationProfile,
  ColorPreset,
  PresetChoice,
  PrinterWithCalibrations,
  SpoolFormData,
  SpoolFormMode,
} from './spool-form/types';
import { defaultFormData, validateForm, SPOOLMAN_LINKED_FIELDS } from './spool-form/types';
import { buildFilamentOptions, extractBrandsFromPresets, fetchPrinterCalibrations, findPresetOption, hotendKey, loadRecentColors, pairedOptions, parsePresetKey, parsePresetName, presetKey, saveRecentColor, withCurrentValue } from './spool-form/utils';
import { MATERIALS } from './spool-form/constants';
import { FilamentSection } from './spool-form/FilamentSection';
import { ColorSection } from './spool-form/ColorSection';
import { AdditionalSection } from './spool-form/AdditionalSection';
import { SpoolmanFilamentPicker } from './spool-form/SpoolmanFilamentPicker';
import { PrinterProfilesSection } from './spool-form/PrinterProfilesSection';
import { normaliseFlow } from '../utils/nozzleFlow';
import { SpoolUsageHistory } from './SpoolUsageHistory';
import {
  invalidateInventoryLocations,
  invalidateSpoolAndLocationQueries,
} from '../utils/inventoryQueries';

type TabId = 'filament' | 'appearance' | 'printers';

const CLEAR_TAG_PAYLOAD = { tag_uid: null, tray_uuid: null, tag_type: null, data_origin: null };

export type { SpoolFormMode };

interface SpoolFormModalProps {
  isOpen: boolean;
  onClose: () => void;
  spool?: InventorySpool | null;
  mode: SpoolFormMode;
  printersWithCalibrations?: PrinterWithCalibrations[];
  currencySymbol: string;
  onSpoolsCreated?: (spools: InventorySpool[]) => void;
  /** When true, CRUD operations target the Spoolman inventory proxy endpoints. */
  spoolmanMode?: boolean;
  /** Query key to invalidate after mutations (differs for Spoolman vs local). */
  spoolsQueryKey?: string[];
}

export function SpoolFormModal({
  isOpen,
  onClose,
  spool,
  mode,
  printersWithCalibrations = [],
  currencySymbol,
  onSpoolsCreated,
  spoolmanMode = false,
  spoolsQueryKey = ['inventory-spools'],
}: SpoolFormModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const refreshSpoolQueries = () =>
    invalidateSpoolAndLocationQueries(queryClient, spoolsQueryKey);

  const isEditing = mode === 'edit';
  const isCopying = mode === 'copy';

  // Form state
  const [formData, setFormData] = useState<SpoolFormData>(defaultFormData);
  const [errors, setErrors] = useState<Partial<Record<keyof SpoolFormData, string>>>({});
  const [activeTab, setActiveTab] = useState<TabId>('filament');
  const [weightTouched, setWeightTouched] = useState(false);
  const [locationIdTouched, setLocationIdTouched] = useState(false);
  const [quickAdd, setQuickAdd] = useState(false);
  const [quantity, setQuantity] = useState(1);

  // Cloud presets
  const [cloudAuthenticated, setCloudAuthenticated] = useState(false);
  const [loadingCloudPresets, setLoadingCloudPresets] = useState(false);
  const [cloudPresets, setCloudPresets] = useState<SlicerSetting[]>([]);
  const [orcaSettingIds, setOrcaSettingIds] = useState<Set<string>>(new Set());
  const [presetInputValue, setPresetInputValue] = useState('');

  // Spool catalog
  const [spoolCatalog, setSpoolCatalog] = useState<SpoolCatalogEntry[]>([]);
  const [storageLocations, setStorageLocations] = useState<{ id: number; name: string }[]>([]);

  // Local presets (OrcaSlicer imports)
  const [localPresets, setLocalPresets] = useState<LocalPreset[]>([]);

  // Built-in filaments (static fallback)
  const [builtinFilaments, setBuiltinFilaments] = useState<BuiltinFilament[]>([]);

  // Color catalog
  const [colorCatalog, setColorCatalog] = useState<{
    manufacturer: string;
    color_name: string;
    hex_color: string;
    material: string | null;
    // #1340: gradient + effect carried from the catalog entry through to the
    // color picker so they're applied alongside hex + name on selection.
    extra_colors?: string | null;
    effect_type?: string | null;
  }[]>([]);

  // Color state
  const [recentColors, setRecentColors] = useState<ColorPreset[]>([]);

  // PA Profile state
  const [fetchedCalibrations, setFetchedCalibrations] = useState<PrinterWithCalibrations[]>([]);
  // Whether the calibration fetch above is still running. Without it the
  // Printers tab renders its "no printers configured" empty state while the
  // printers are still being asked -- which reads as a wrong answer rather
  // than as a wait, and the fetch is several round trips per machine.
  const [loadingCalibrations, setLoadingCalibrations] = useState(false);
  // One K profile per hotend, keyed `printerId:extruder:diameter`. A Map rather
  // than a Set of composite keys because the Printers tab presents each hotend
  // as a single-choice dropdown -- the shape makes "two profiles for one
  // hotend" unrepresentable instead of relying on eviction logic to prevent it.
  const [selectedProfiles, setSelectedProfiles] = useState<Map<string, CalibrationProfile>>(new Map());
  // Per-printer-model preset overrides, keyed by `presetKey(model, diameter)`.
  // Only the models the user has actually overridden are present; an absent
  // entry means "inherit this spool's own preset", which is exactly what the
  // backend cascade does with a missing row.
  const [modelPresets, setModelPresets] = useState<Map<string, PresetChoice>>(new Map());
  const [selectedGroupId, setSelectedGroupId] = useState<string>('');

  // Use prop if provided, otherwise use self-fetched data
  const resolvedCalibrations = printersWithCalibrations.length > 0
    ? printersWithCalibrations
    : fetchedCalibrations;

  // Tab badge: everything the user has configured under Printers, K profiles
  // and preset overrides alike, since both live on that tab now.
  const selectedProfileCount = selectedProfiles.size + modelPresets.size;

  // Fetch Spoolman filament catalog when in Spoolman mode
  // retry:false — Spoolman may be intentionally disabled (400); don't flood the server
  const { data: spoolmanFilaments = [], isLoading: isLoadingFilaments, error: filamentsError } = useQuery<SpoolmanFilamentEntry[], Error>({
    queryKey: ['spoolman-inventory-filaments'],
    queryFn: () => api.getSpoolmanInventoryFilaments(),
    enabled: spoolmanMode && isOpen,
    staleTime: 60_000,
    retry: false,
  });

  // Load recent colors on mount
  useEffect(() => {
    setRecentColors(loadRecentColors());
  }, []);

  // Fetch cloud presets and catalog when modal opens. Fetches Bambu Cloud
  // and Orca Cloud in parallel; merges Orca filaments into ``cloudPresets``
  // since ``OrcaProfileMeta`` is structurally identical to ``SlicerSetting``
  // (same fields, same semantics). ``cloudAuthenticated`` flips on if either
  // cloud is connected — the UI only uses it to gate "no cloud" hints.
  useEffect(() => {
    // ``cancelled`` gates every state setter so a fetch that resolves AFTER
    // the modal closes / unmounts can't fire setState on a torn-down
    // component. Without this guard the parallel Promise.allSettled chain
    // can still hit ``setLoadingCloudPresets(false)`` in its ``finally``
    // after vitest has dismantled the JSDOM window — surfaced as an
    // "Unhandled Rejection: window is not defined" in CI runs.
    let cancelled = false;
    if (isOpen) {
      const fetchData = async () => {
        setLoadingCloudPresets(true);
        try {
          const [bambuResult, orcaResult] = await Promise.allSettled([
            (async () => {
              const status = await api.getCloudStatus();
              if (!status.is_authenticated) return { connected: false, presets: [] as SlicerSetting[] };
              const presets = await api.getFilamentPresets();
              return { connected: true, presets };
            })(),
            (async () => {
              const status = await api.orcaCloudStatus();
              if (!status.connected) return { connected: false, presets: [] as SlicerSetting[] };
              const list = await api.orcaCloudListProfiles();
              // OrcaProfileMeta is structurally identical to SlicerSetting.
              return { connected: true, presets: list.filament as unknown as SlicerSetting[] };
            })(),
          ]);
          if (cancelled) return;
          const bambuConnected = bambuResult.status === 'fulfilled' && bambuResult.value.connected;
          const orcaConnected = orcaResult.status === 'fulfilled' && orcaResult.value.connected;
          const bambuPresets = bambuResult.status === 'fulfilled' ? bambuResult.value.presets : [];
          const orcaPresets = orcaResult.status === 'fulfilled' ? orcaResult.value.presets : [];
          setCloudAuthenticated(bambuConnected || orcaConnected);
          setCloudPresets([...bambuPresets, ...orcaPresets]);
          // The two clouds are merged into one list, so remember which ids came
          // from Orca -- it is the only way the origin badge can tell them
          // apart afterwards.
          setOrcaSettingIds(new Set(orcaPresets.map(p => p.setting_id)));
        } catch (e) {
          if (cancelled) return;
          console.error('Failed to fetch cloud presets:', e);
          setCloudAuthenticated(false);
        } finally {
          if (!cancelled) setLoadingCloudPresets(false);
        }
      };
      fetchData();
      if (!spoolmanMode) {
        api.getSpoolCatalog().then(setSpoolCatalog).catch(console.error);
      }
      api.getColorCatalog().then(setColorCatalog).catch(console.error);
      api.getLocalPresets().then(r => setLocalPresets(r.filament)).catch(console.error);
      api.getBuiltinFilaments().then(setBuiltinFilaments).catch(console.error);
      api.getLocations().then((locs) => setStorageLocations(locs.map((l) => ({ id: l.id, name: l.name })))).catch(console.error);

      // Fetch printer calibrations if not provided via props
      if (printersWithCalibrations.length === 0) {
        (async () => {
          setLoadingCalibrations(true);
          try {
            const printers = await api.getPrinters();
            const statuses = await Promise.all(
              printers.map(p => api.getPrinterStatus(p.id).catch(() => null)),
            );
            // Printers in parallel, diameters within a printer in series.
            // Separate machines are separate MQTT connections and do not
            // interfere; it is one printer's own firmware that drops a
            // concurrent burst of calibration requests (see
            // fetchPrinterCalibrations). Walking the fleet one machine at a
            // time made the whole tab wait for the sum of every printer.
            const results = await Promise.all(
              printers.map(async (printer, i) => {
                const status = statuses[i];
                const connected = status?.connected ?? false;
                let calibrations: PrinterWithCalibrations['calibrations'] = [];
                if (connected) {
                  // Across every nozzle size, so a profile for a size that is
                  // not currently fitted is still offered (#2618 fetched only
                  // the fitted ones).
                  calibrations = await fetchPrinterCalibrations(printer.id, status);
                }
                // Keep the reported nozzle hardware: the Printers tab lists a
                // model's installed diameters from it. Read as a set of
                // diameters only -- never indexed by extruder.
                return { printer: { ...printer, connected }, calibrations, nozzles: status?.nozzles };
              }),
            );
            if (!cancelled) setFetchedCalibrations(results);
          } catch (e) {
            console.error('Failed to fetch printer calibrations:', e);
          } finally {
            if (!cancelled) setLoadingCalibrations(false);
          }
        })();
      }
    }
    // The effect intentionally depends only on `isOpen` (and the prop-side
    // calibration count) — re-running on every spoolmanMode toggle would
    // race the in-flight async fetches with unmount/teardown and emit
    // "test environment was torn down" errors in vitest. spoolmanMode only
    // gates a single fetch (getSpoolCatalog) which is cheap enough to skip
    // when the modal opens in Spoolman mode.
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, printersWithCalibrations.length]);

  // Build filament options: cloud → local → fallback
  const filamentOptions = useMemo(
    () => buildFilamentOptions(cloudPresets, new Set(), localPresets, builtinFilaments, orcaSettingIds),
    [cloudPresets, localPresets, builtinFilaments, orcaSettingIds],
  );

  // Extract brands from presets
  const baseAvailableBrands = useMemo(() => {
    const presetBrands = extractBrandsFromPresets(cloudPresets, localPresets);
    const catalogBrands = colorCatalog
      .map(entry => entry.manufacturer?.trim())
      .filter((brand): brand is string => !!brand);
    const brandSet = new Set<string>([...presetBrands, ...catalogBrands]);
    return Array.from(brandSet).sort((a, b) => a.localeCompare(b));
  }, [cloudPresets, localPresets, colorCatalog]);

  const baseAvailableMaterials = useMemo(() => {
    const catalogMaterials = colorCatalog
      .map(entry => entry.material?.trim())
      .filter((material): material is string => !!material);
    const materialSet = new Set<string>([...MATERIALS, ...catalogMaterials]);
    return Array.from(materialSet).sort((a, b) => a.localeCompare(b));
  }, [colorCatalog]);

  const brandMaterialPairs = useMemo(() => {
    const pairs: Array<{ brand: string; material: string }> = [];

    for (const entry of colorCatalog) {
      const brand = entry.manufacturer?.trim();
      const material = entry.material?.trim();
      if (brand && material) pairs.push({ brand, material });
    }

    for (const preset of cloudPresets) {
      const parsed = parsePresetName(preset.name);
      if (parsed.brand && parsed.material) {
        pairs.push({ brand: parsed.brand, material: parsed.material });
      }
    }

    for (const preset of localPresets) {
      const parsed = parsePresetName(preset.name);
      const brand = preset.filament_vendor?.trim() || parsed.brand;
      const material = parsed.material;
      if (brand && material) {
        pairs.push({ brand, material });
      }
    }

    return pairs;
  }, [cloudPresets, colorCatalog, localPresets]);

  const brandToMaterials = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const pair of brandMaterialPairs) {
      const brandKey = pair.brand.toLowerCase();
      const materialKey = pair.material.toLowerCase();
      if (!map.has(brandKey)) map.set(brandKey, new Set());
      map.get(brandKey)!.add(materialKey);
    }
    return map;
  }, [brandMaterialPairs]);

  const materialToBrands = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const pair of brandMaterialPairs) {
      const brandKey = pair.brand.toLowerCase();
      const materialKey = pair.material.toLowerCase();
      if (!map.has(materialKey)) map.set(materialKey, new Set());
      map.get(materialKey)!.add(brandKey);
    }
    return map;
  }, [brandMaterialPairs]);

  // #1905: the brand and material dropdowns used to be filtered down to the
  // pairs seen in the color catalog / slicer presets, which hid perfectly valid
  // combinations — "Elegoo" exists (as a PLA brand) but vanished from the list
  // once ASA was selected, making the entry look impossible. Both lists now
  // always offer everything we know about, plus whatever the spool already has
  // stored (a custom brand saved earlier was missing from its own dropdown).
  // The pairing knowledge survives as `suggestedBrands`/`suggestedMaterials`,
  // which the dropdowns sort to the top instead of filtering by.
  const availableBrands = useMemo(
    () => withCurrentValue(baseAvailableBrands, formData.brand),
    [baseAvailableBrands, formData.brand],
  );

  const availableMaterials = useMemo(
    () => withCurrentValue(baseAvailableMaterials, formData.material),
    [baseAvailableMaterials, formData.material],
  );

  const suggestedBrands = useMemo(
    () => pairedOptions(availableBrands, formData.material, materialToBrands),
    [availableBrands, formData.material, materialToBrands],
  );

  const suggestedMaterials = useMemo(
    () => pairedOptions(availableMaterials, formData.brand, brandToMaterials),
    [availableMaterials, formData.brand, brandToMaterials],
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
        // Legacy rows may carry a malformed rgba (e.g. the 7-char 'FFFFFFF'
        // from #1055 before the create/update pattern was enforced). The
        // backend SpoolUpdate schema rejects non-8-char hex on PATCH, so
        // re-submitting a malformed value would 422 every edit on that spool
        // — even edits that don't touch color. Normalize on load: any value
        // that isn't exactly 8 hex chars falls back to the default, so the
        // user can save unrelated fields (weight, material, note) without
        // first being forced to fix a color they may not even be aware is
        // broken. Saving also purges the bad value from the DB.
        const validRgba = spool.rgba && /^[0-9A-Fa-f]{8}$/.test(spool.rgba) ? spool.rgba : '808080FF';
        setFormData({
          material: spool.material || '',
          subtype: spool.subtype || '',
          brand: spool.brand || '',
          // #1319: leave color_name blank when the backend reports it was
          // synthesised from subtype — otherwise the form would round-trip
          // the synth value to Spoolman on save as if the user had set it,
          // which is what produced the "color reverts to subtype" symptom.
          color_name: spool.color_name_is_synthesized ? '' : (spool.color_name || ''),
          rgba: validRgba,
          extra_colors: spool.extra_colors || '',
          effect_type: spool.effect_type || '',
          label_weight: spool.label_weight || 1000,
          core_weight: spool.core_weight || 250,
          core_weight_catalog_id: spool.core_weight_catalog_id ?? null,
          weight_used: isCopying ? 0 : spool.weight_used || 0,
          slicer_filament: spool.slicer_filament || '',
          note: spool.note || '',
          cost_per_kg: spool.cost_per_kg ?? null,
          category: spool.category || '',
          low_stock_threshold_pct: spool.low_stock_threshold_pct ?? null,
          location_id: spool.location_id ?? null,
          spoolman_filament_id: null,
        });
        setPresetInputValue(spool.slicer_filament_name || spool.slicer_filament || '');

        // Load K-profiles for this spool. The stored row carries everything
        // the picker needs to show the selection before the printer answers,
        // so an offline printer still renders what was chosen for it.
        if (spool.k_profiles && spool.k_profiles.length > 0) {
          const chosen = new Map<string, CalibrationProfile>();
          for (const p of spool.k_profiles) {
            if (p.cali_idx === null || p.cali_idx === undefined) continue;
            const diameter = (p.nozzle_diameter || '').trim() || '0.4';
            const extruder = p.extruder ?? 0;
            chosen.set(hotendKey(p.printer_id, extruder, diameter), {
              cali_idx: p.cali_idx,
              filament_id: '',
              setting_id: p.setting_id || '',
              name: p.name || '',
              k_value: p.k_value,
              n_coef: 0,
              extruder_id: extruder,
              nozzle_diameter: diameter,
              // Stored as the bare flow code; the picker re-derives its label
              // from the same field it reads off a live profile.
              nozzle_id: p.nozzle_type || '',
            });
          }
          setSelectedProfiles(chosen);
        } else {
          setSelectedProfiles(new Map());
        }
      } else {
        setFormData(defaultFormData);
        setPresetInputValue('');
        setSelectedProfiles(new Map());
      }
      // Reset on every open, not just the create path (#1905). The modal keeps
      // its state while closed, and the Quick Add toggle only renders in create
      // mode — so quick-adding a spool and then opening Edit left the edit form
      // stuck in quick-add layout (no preset field, no PA-profile tab) with no
      // control to switch back.
      setQuickAdd(false);
      setQuantity(1);
      setErrors({});
      setActiveTab('filament');
      setSelectedGroupId('');
      // Cleared on every open, both branches: the modal keeps its state while
      // closed, so editing spool B after spool A would otherwise show (and
      // save) A's per-model overrides on B. Refilled by the fetch below.
      setModelPresets(new Map());
      setWeightTouched(false);
      setLocationIdTouched(false);
    }
  }, [isOpen, spool, mode, isCopying]);

  // Load this spool's per-printer-model preset overrides. Fetched rather than
  // read off the spool: they are deliberately not embedded in the spool
  // response, which the inventory list returns once per spool the user owns.
  // Copying a spool copies its overrides -- they describe the filament on the
  // spool, which is what a copy has too.
  useEffect(() => {
    if (!isOpen || !spool) return;
    let cancelled = false;
    const load = spoolmanMode ? api.getSpoolmanFilamentPresets : api.getSpoolFilamentPresets;
    load(spool.id)
      .then(rows => {
        if (cancelled) return;
        const next = new Map<string, PresetChoice>();
        for (const row of rows) {
          next.set(presetKey(row.printer_model, row.nozzle_diameter || ''), {
            code: row.slicer_filament || '',
            name: row.slicer_filament_name || '',
          });
        }
        setModelPresets(next);
      })
      .catch(e => {
        // Non-fatal: the tab still works, it just starts with nothing
        // overridden. Saving from that state WOULD clear the stored rows, so
        // say so rather than letting the user save over what they cannot see.
        if (cancelled) return;
        console.error('Failed to load filament preset overrides:', e);
        showToast(t('inventory.filamentPresetsLoadFailed'), 'warning');
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, spool?.id, spoolmanMode]);

  // Legacy rows may have storage_location text but no location_id yet — link when catalog loads.
  useEffect(() => {
    if (!isOpen || !spool || locationIdTouched || formData.location_id != null) return;
    const legacy = spool.storage_location?.trim();
    if (!legacy || storageLocations.length === 0) return;
    const match = storageLocations.find((l) => l.name.toLowerCase() === legacy.toLowerCase());
    if (match) {
      setFormData((prev) => (prev.location_id === match.id ? prev : { ...prev, location_id: match.id }));
    }
  }, [isOpen, spool, storageLocations, formData.location_id, locationIdTouched]);


  // Update field helper
  const updateField = <K extends keyof SpoolFormData>(key: K, value: SpoolFormData[K]) => {
    const isLinkedField = SPOOLMAN_LINKED_FIELDS.has(key);
    if (spoolmanMode && isLinkedField && formData.spoolman_filament_id !== null) {
      showToast(t('inventory.spoolmanFilamentUnlinked'), 'info');
    }
    setFormData(prev => ({
      ...prev,
      [key]: value,
      ...(spoolmanMode && isLinkedField && prev.spoolman_filament_id !== null
        ? { spoolman_filament_id: null }
        : {}),
    }));
    if (key === 'weight_used') setWeightTouched(true);
    if (key === 'location_id') setLocationIdTouched(true);
    if (errors[key]) {
      setErrors(prev => ({ ...prev, [key]: undefined }));
    }
  };

  // Prefill form from a Spoolman filament catalog entry
  // subtype extraction mirrors _spoolman_helpers.py logic
  const handleFilamentSelect = (filament: SpoolmanFilamentEntry) => {
    const material = filament.material || '';
    const name = filament.name || '';
    const subtype = material && name.startsWith(material) ? name.slice(material.length).trim() : name;
    const rawHex = (filament.color_hex ?? '').replace('#', '').toUpperCase();
    // Guard against short/malformed hex values — 6 chars (RRGGBB), or 8 when the
    // filament is translucent and carries its own alpha (#2912). Rejecting 8 here
    // prefilled a clear filament picked from the Spoolman catalogue as 808080FF.
    const colorHex = /^[0-9A-F]{6}(?:[0-9A-F]{2})?$/.test(rawHex) ? rawHex : '808080';
    const prefillRgba = colorHex.length === 8 ? colorHex : `${colorHex}FF`;
    setFormData(prev => ({
      ...prev,
      spoolman_filament_id: filament.id,
      material,
      subtype,
      brand: filament.vendor?.name || '',
      rgba: prefillRgba,
      color_name: filament.color_name || '',
      label_weight: filament.weight ?? prev.label_weight,
    }));
    showToast(t('inventory.spoolmanFilamentSelected'), 'success');
  };

  // Handle color selection
  const handleColorUsed = (color: ColorPreset) => {
    setRecentColors(prev => saveRecentColor(color, prev));
  };

  // Mutations – dispatch to Spoolman proxy or local inventory based on mode
  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      spoolmanMode
        ? api.createSpoolmanInventorySpool(data as Parameters<typeof api.createSpoolmanInventorySpool>[0])
        : api.createSpool(data as Parameters<typeof api.createSpool>[0]),
    onSuccess: async (newSpool) => {
      if (newSpool?.id) {
        const ok = await savePrinterProfiles(newSpool.id);
        if (!ok) return;
      }
      await refreshSpoolQueries();
      if (onSpoolsCreated) onSpoolsCreated([newSpool]);
      showToast(t('inventory.spoolCreated'), 'success');
      onClose();
    },
    onError: (error: Error) => {
      if (error instanceof ApiError && error.status === 503) {
        showToast(t('inventory.spoolmanUnreachable'), 'error');
      } else {
        showToast(t('inventory.saveFailed'), 'error');
      }
    },
  });

  const bulkCreateMutation = useMutation<
    SpoolmanBulkCreateResult | InventorySpool[],
    Error,
    { data: Record<string, unknown>; qty: number }
  >({
    mutationFn: ({ data, qty }) =>
      spoolmanMode
        ? api.bulkCreateSpoolmanInventorySpools(data as Parameters<typeof api.bulkCreateSpoolmanInventorySpools>[0], qty)
        : api.bulkCreateSpools(data as Parameters<typeof api.bulkCreateSpools>[0], qty),
    onSuccess: async (result) => {
      // Spoolman bulk-create returns SpoolmanBulkCreateResult (207); local returns InventorySpool[].
      // Cast via unknown to satisfy strict TypeScript — the runtime shape is guaranteed by
      // the duck-type check ('created' in result) before any property access.
      const spoolmanResult = (spoolmanMode && 'created' in result)
        ? (result as unknown as SpoolmanBulkCreateResult)
        : null;
      const createdSpools: InventorySpool[] = spoolmanResult
        ? spoolmanResult.created
        : (result as InventorySpool[]);

      // Bulk create: every copy gets the same profiles and overrides. Skipped
      // entirely when the user configured neither, so a plain bulk add does
      // not fire two writes per spool.
      if (selectedProfiles.size > 0 || modelPresets.size > 0) {
        for (const s of createdSpools) {
          await savePrinterProfiles(s.id);
        }
      }
      await refreshSpoolQueries();
      if (onSpoolsCreated) onSpoolsCreated(createdSpools);
      if (spoolmanResult && spoolmanResult.failed_count > 0) {
        showToast(
          t('inventory.spoolsPartiallyCreated', {
            created: createdSpools.length,
            total: spoolmanResult.requested_count,
          }),
          'warning',
        );
      } else {
        showToast(t('inventory.spoolsCreated', { count: createdSpools.length }), 'success');
      }
      onClose();
    },
    onError: (error: Error) => {
      if (error instanceof ApiError && error.status === 503) {
        showToast(t('inventory.spoolmanUnreachable'), 'error');
      } else {
        showToast(t('inventory.saveFailed'), 'error');
      }
    },
  });

  const updateMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      spoolmanMode
        ? api.updateSpoolmanInventorySpool(spool!.id, data as Parameters<typeof api.updateSpoolmanInventorySpool>[1])
        : api.updateSpool(spool!.id, data as Parameters<typeof api.updateSpool>[1]),
    onSuccess: async () => {
      if (spool?.id) {
        const ok = await savePrinterProfiles(spool.id);
        if (!ok) return;
      }
      await refreshSpoolQueries();
      showToast(t('inventory.spoolUpdated'), 'success');
      onClose();
    },
    onError: (error: Error) => {
      if (error instanceof ApiError && error.status === 503) {
        showToast(t('inventory.spoolmanUnreachable'), 'error');
      } else {
        showToast(t('inventory.saveFailed'), 'error');
      }
    },
  });

  const deleteTagMutation = useMutation({
    mutationFn: () => {
      if (spoolmanMode) {
        return api.updateSpoolmanInventorySpool(spool!.id, CLEAR_TAG_PAYLOAD as Parameters<typeof api.updateSpoolmanInventorySpool>[1]);
      }
      return api.updateSpool(spool!.id, CLEAR_TAG_PAYLOAD as Parameters<typeof api.updateSpool>[1]);
    },
    onSuccess: async () => {
      await refreshSpoolQueries();
      showToast(t('inventory.rfidCleared', 'RFID tag cleared'), 'success');
      onClose();
    },
    onError: (error: Error) => {
      if (error instanceof ApiError && error.status === 503) {
        showToast(t('inventory.spoolmanUnreachable'), 'error');
      } else {
        showToast(t('inventory.tagClearFailed'), 'error');
      }
    },
  });

  // Fetch assignment for this spool (to show Unassign button). In Spoolman mode
  // the slot assignment lives in the spoolman_slot_assignments table keyed by
  // spoolman_spool_id, not in the legacy spool_assignments table — #1336 was the
  // resulting "Unassign button is always disabled" report.
  const { data: assignments } = useQuery({
    queryKey: ['spool-assignments'],
    queryFn: () => api.getAssignments(),
    enabled: isOpen && isEditing && !spoolmanMode,
  });
  const { data: spoolmanSlotAssignments } = useQuery({
    queryKey: ['spoolman-slot-assignments-all'],
    queryFn: () => api.getSpoolmanSlotAssignments(),
    enabled: isOpen && isEditing && spoolmanMode,
  });
  const spoolAssignment = (() => {
    if (!spool) return undefined;
    if (spoolmanMode) {
      return spoolmanSlotAssignments?.find(a => a.spoolman_spool_id === spool.id);
    }
    return assignments?.find(a => a.spool_id === spool.id);
  })();

  // Read inventory + settings caches (already populated by InventoryPage) to
  // drive the category autocomplete and low-stock-threshold placeholder. #729
  const { data: allSpools } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(true),
    enabled: isOpen,
  });
  const { data: settingsForForm } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
    enabled: isOpen,
  });

  // Backend Bambu printer-model registry, so the Printers tab can read the
  // model out of a preset name and offer each model only its own presets. The
  // same query key and staleTime the Configure AMS Slot modal uses -- the
  // registry only changes across backend releases, so this is a cache hit
  // whenever that modal has been opened.
  const { data: printerModelsData } = useQuery({
    queryKey: ['slicerPrinterModels'],
    queryFn: api.getSlicerPrinterModels,
    enabled: isOpen,
    staleTime: Infinity,
  });
  const availableCategories = (() => {
    const set = new Set<string>();
    for (const s of allSpools ?? []) {
      const c = s.category?.trim();
      if (c) set.add(c);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  })();
  const globalLowStockThreshold = settingsForForm?.low_stock_threshold ?? 20;

  const unassignMutation = useMutation({
    mutationFn: async () => {
      if (!spoolAssignment) throw new Error('No assignment');
      if (spoolmanMode) {
        if (!spool) throw new Error('No spool');
        await api.unassignSpoolmanSlot(spool.id);
        return;
      }
      await api.unassignSpool(spoolAssignment.printer_id, spoolAssignment.ams_id, spoolAssignment.tray_id);
    },
    onSuccess: async () => {
      if (spoolmanMode) {
        await queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments-all'] });
        await queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments'] });
      } else {
        await queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
      }
      showToast(t('inventory.unassignSuccess', 'Spool unassigned'), 'success');
      onClose();
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // Save everything the Printers tab holds: one K profile per hotend and the
  // per-printer-model preset overrides. Returns false if either write failed,
  // which keeps the modal open so the user does not lose what they picked.
  const savePrinterProfiles = async (spoolId: number): Promise<boolean> => {
    const saveKApi = spoolmanMode ? api.saveSpoolmanKProfiles : api.saveSpoolKProfiles;
    const savePresetApi = spoolmanMode ? api.saveSpoolmanFilamentPresets : api.saveSpoolFilamentPresets;

    // The selection Map is keyed by hotend and holds the calibration itself,
    // so nothing has to be resolved back out of the printer's live list. That
    // also fixes a real defect in the old key-based lookup: it matched a
    // calibration by cali_idx alone, and cali_idx is numbered PER NOZZLE --
    // on a dual-nozzle printer it could resolve the other hotend's entry and
    // persist that entry's K value and diameter.
    const profiles: SpoolKProfileInput[] = [];
    for (const [key, cal] of selectedProfiles) {
      const [printerIdStr, extruderStr, diameter] = key.split(':');
      profiles.push({
        printer_id: parseInt(printerIdStr),
        extruder: parseInt(extruderStr),
        nozzle_diameter: diameter || '0.4',
        // The flow the profile was measured on, when the printer declares one.
        // Null where it does not (an X1C declares none on any profile), which
        // the backend reads as "applies to whatever is fitted" -- the same rule
        // that keeps every profile stored before this working.
        nozzle_type: normaliseFlow(cal.nozzle_id),
        k_value: cal.k_value,
        name: cal.name || null,
        cali_idx: cal.cali_idx,
        setting_id: cal.setting_id || null,
      });
    }

    const presets: SpoolFilamentPresetInput[] = [];
    for (const [key, choice] of modelPresets) {
      const { model, diameter } = parsePresetKey(key);
      if (!model) continue;
      presets.push({
        printer_model: model,
        nozzle_diameter: diameter,
        slicer_filament: choice.code || null,
        slicer_filament_name: choice.name || null,
      });
    }

    // Both are full replacements, so both run even when empty -- that is how
    // the user clears the last profile or the last override.
    try {
      await saveKApi(spoolId, profiles);
    } catch (e) {
      console.error('Failed to save K-profiles:', e);
      showToast(t('inventory.kProfileSaveFailed'), 'warning');
      return false;
    }

    try {
      await savePresetApi(spoolId, presets);
    } catch (e) {
      console.error('Failed to save filament preset overrides:', e);
      showToast(t('inventory.filamentPresetSaveFailed'), 'warning');
      return false;
    }

    return true;
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
    const validation = validateForm(formData, quickAdd, spoolmanMode, mode);
    if (!validation.isValid) {
      setErrors(validation.errors);
      if (validation.errors.slicer_filament || validation.errors.material || validation.errors.brand || validation.errors.subtype) {
        setActiveTab('filament');
      }
      return;
    }

    // Find preset name from selected option
    const presetName = selectedPresetOption?.displayName || presetInputValue || null;

    const data: Record<string, unknown> = {
      material: formData.material || null,
      subtype: formData.subtype || null,
      brand: formData.brand || null,
      color_name: formData.color_name || null,
      rgba: formData.rgba || null,
      extra_colors: formData.extra_colors || null,
      effect_type: formData.effect_type || null,
      label_weight: formData.label_weight,
      ...(spoolmanMode ? {} : { core_weight: formData.core_weight, core_weight_catalog_id: formData.core_weight_catalog_id }),
      slicer_filament: formData.slicer_filament || null,
      slicer_filament_name: presetName,
      nozzle_temp_min: null,
      nozzle_temp_max: null,
      note: formData.note || null,
      cost_per_kg: formData.cost_per_kg,
      category: formData.category.trim() || null,
      low_stock_threshold_pct: formData.low_stock_threshold_pct,
      ...(spoolmanMode ? { spoolman_filament_id: formData.spoolman_filament_id } : {}),
    };

    // Only send weight_used when creating or when explicitly changed by the user.
    // This prevents stale cached values from overwriting usage-tracker data.
    if (!isEditing || weightTouched) {
      data.weight_used = formData.weight_used;
    }

    // Only send location_id when creating or when explicitly changed by the user.
    // Backend derives storage_location; omitting on untouched edit avoids stale overwrites.
    if (!isEditing || locationIdTouched) {
      data.location_id = formData.location_id;
    }

    if (isEditing) {
      updateMutation.mutate(data);
    } else if (quantity > 1) {
      bulkCreateMutation.mutate({ data, qty: quantity });
    } else {
      createMutation.mutate(data);
    }
  };

  const isPending = createMutation.isPending || bulkCreateMutation.isPending || updateMutation.isPending || deleteTagMutation.isPending || unassignMutation.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Wider than the old max-w-xl: the Printers tab is a model list beside
          a detail pane holding a preset row per nozzle size and a hotend-by-
          size grid, which needs room for both. Held constant across tabs
          rather than sized per tab -- a modal that resizes as you switch tabs
          reads as a layout bug. */}
      <div className="relative w-full max-w-5xl mx-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary flex-shrink-0">
          <h2 className="text-lg font-semibold text-white flex items-baseline gap-2">
            {isEditing ? t('inventory.editSpool') : isCopying ? t('inventory.copySpool') : t('inventory.addSpool')}
            {isEditing && spool && (
              <span className="text-sm font-mono text-bambu-gray">#{spool.id}</span>
            )}
          </h2>
          <button
            onClick={onClose}
            className="p-1 text-bambu-gray hover:text-white rounded transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Quick Add toggle — only in create mode (not edit, not copy).
            In copy mode the modal title is the singular "Copy Spool", so the
            quantity-driven bulkCreateMutation path would silently produce N
            copies under a misleading title — keep this toggle out of that
            mode entirely. */}
        {mode === 'create' && (
          <div className="flex items-center justify-between px-4 py-2 border-b border-bambu-dark-tertiary flex-shrink-0">
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-amber-600 dark:text-amber-400" />
              <span className="text-sm text-white">{t('inventory.quickAdd')}</span>
            </div>
            <button
              type="button"
              onClick={() => {
                setQuickAdd(!quickAdd);
                if (!quickAdd) setActiveTab('filament');
              }}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                quickAdd ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
              }`}
            >
              <span
                className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
                  quickAdd ? 'translate-x-4' : 'translate-x-0.5'
                }`}
              />
            </button>
          </div>
        )}

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
            <Beaker className="w-4 h-4" />
            {t('inventory.filamentInfoTab')}
          </button>
          {!quickAdd && (
            <button
              onClick={() => setActiveTab('appearance')}
              className={`flex-1 px-4 py-2.5 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${
                activeTab === 'appearance'
                  ? 'text-bambu-green border-b-2 border-bambu-green'
                  : 'text-bambu-gray hover:text-white'
              }`}
            >
              <Palette className="w-4 h-4" />
              {t('inventory.colorAndCostTab')}
            </button>
          )}
          {!quickAdd && (
            <button
              onClick={() => setActiveTab('printers')}
              className={`flex-1 px-4 py-2.5 text-sm font-medium flex items-center justify-center gap-2 transition-colors ${
                activeTab === 'printers'
                  ? 'text-bambu-green border-b-2 border-bambu-green'
                  : 'text-bambu-gray hover:text-white'
              }`}
            >
              <Zap className="w-4 h-4" />
              {t('inventory.printersTab')}
              {selectedProfileCount > 0 && (
                <span className="text-xs px-1.5 py-0.5 rounded-full bg-bambu-green/20 text-bambu-green">
                  {selectedProfileCount}
                </span>
              )}
            </button>
          )}
        </div>

        {/* Content */}
        <div className="p-4 overflow-y-auto flex-1" style={{ scrollbarGutter: 'stable' }}>
          {activeTab === 'filament' ? (
            <div className="space-y-6">
              {/* Spoolman Filament Catalog Picker — only when creating a spool in Spoolman mode */}
              {spoolmanMode && !isEditing && (
                <div>
                  {filamentsError ? (
                    <p className="text-sm text-red-700 dark:text-red-400 px-1">{t('inventory.spoolmanCatalogLoadFailed')}</p>
                  ) : (
                    <SpoolmanFilamentPicker
                      filaments={spoolmanFilaments}
                      isLoading={isLoadingFilaments}
                      selectedId={formData.spoolman_filament_id}
                      onSelect={handleFilamentSelect}
                    />
                  )}
                </div>
              )}

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
                  availableMaterials={availableMaterials}
                  suggestedBrands={suggestedBrands}
                  suggestedMaterials={suggestedMaterials}
                  quickAdd={quickAdd}
                  detailsRequired={!quickAdd && !spoolmanMode && mode === 'create'}
                  quantity={quantity}
                  onQuantityChange={setQuantity}
                  errors={errors}
                />
              </div>

            </div>
          ) : activeTab === 'appearance' ? (
            <div className="space-y-6">
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
                  currencySymbol={currencySymbol}
                  availableCategories={availableCategories}
                  availableLocations={storageLocations}
                  onCreateLocation={async (name) => {
                    try {
                      const created = await api.createLocation({ name });
                      setStorageLocations((prev) => [...prev, { id: created.id, name: created.name }].sort((a, b) => a.name.localeCompare(b.name)));
                      await invalidateInventoryLocations(queryClient);
                      return { id: created.id, name: created.name };
                    } catch (e) {
                      // Surface the backend's actual error so the user can
                      // distinguish 409 duplicate / 400 validation / 500 from
                      // a generic "save failed" message.
                      console.error(e);
                      const message = e instanceof Error ? e.message : t('locations.saveFailed');
                      showToast(message || t('locations.saveFailed'), 'error');
                      return null;
                    }
                  }}
                  globalLowStockThreshold={globalLowStockThreshold}
                  spoolmanMode={spoolmanMode}
                />
              </div>

              {/* Usage History (only when editing internal inventory; Spoolman tracks its own) */}
              {isEditing && spool && !spoolmanMode && (
                <div>
                  <SpoolUsageHistory spoolId={spool.id} />
                </div>
              )}
            </div>
          ) : (
            <PrinterProfilesSection
              formData={formData}
              printersWithCalibrations={resolvedCalibrations}
              filamentOptions={filamentOptions}
              modelPresets={modelPresets}
              setModelPresets={setModelPresets}
              selectedProfiles={selectedProfiles}
              setSelectedProfiles={setSelectedProfiles}
              selectedGroupId={selectedGroupId}
              setSelectedGroupId={setSelectedGroupId}
              printerModels={printerModelsData}
              isLoading={loadingCalibrations}
            />
          )}
        </div>

        {/* Footer */}
        <div className="flex gap-2 p-4 border-t border-bambu-dark-tertiary flex-shrink-0">
          {isEditing && (
            <div className="flex gap-2 mr-auto">
              <Button
                variant="secondary"
                onClick={() => deleteTagMutation.mutate()}
                disabled={isPending || !spool?.tag_uid}
              >
                <Tag className="w-4 h-4" />
                {t('inventory.clearRfid', 'Clear RFID Tag')}
              </Button>
              <Button
                variant="secondary"
                onClick={() => unassignMutation.mutate()}
                disabled={isPending || !spoolAssignment}
              >
                <Unlink className="w-4 h-4" />
                {t('inventory.unassignSpool', 'Unassign')}
              </Button>
            </div>
          )}
          <div className="flex gap-2 ml-auto">
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
                {isEditing ? t('common.save') : isCopying ? t('inventory.copySpool') : t('inventory.addSpool')}
              </>
            )}
          </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
