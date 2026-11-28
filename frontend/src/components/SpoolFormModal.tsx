import { useState, useEffect, useMemo, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, Save, Beaker, Palette, Zap, Tag, Link2, Unlink, Printer } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { InventorySpool, Supplier, SlicerSetting, SpoolCatalogEntry, LocalPreset, BuiltinFilament, SpoolmanBulkCreateResult, SpoolFilamentPresetInput, SpoolKProfileInput, SpoolmanFilamentEntry, ResolvedProfileDefault, ThreeDfpFilament } from '../api/client';
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
import { buildFilamentOptions, extractBrandsFromPresets, fetchPrinterCalibrations, findPresetOption, loadLastSpool, loadRecentColors, pairedOptions, parsePresetKey, parsePresetName, presetKey, saveLastSpool, saveRecentColor, selectionFromKProfiles, withCurrentValue } from './spool-form/utils';
import type { LastSpool } from './spool-form/utils';
import { MATERIALS } from './spool-form/constants';
import { FilamentSection } from './spool-form/FilamentSection';
import { ColorSection } from './spool-form/ColorSection';
import { AdditionalSection } from './spool-form/AdditionalSection';
import { SupplierSection, type SupplierLinkDraft } from './spool-form/SupplierSection';
import { SpoolmanFilamentPicker } from './spool-form/SpoolmanFilamentPicker';
import { ThreeDfpSection } from './spool-form/ThreeDfpSection';
import { PrinterProfilesSection } from './spool-form/PrinterProfilesSection';
import { ProfileDefaultOption, type ProfileDefaultScope } from './spool-form/ProfileDefaultOption';
import { normaliseFlow } from '../utils/nozzleFlow';
import { parseUTCDate, toDateTimeLocalValue } from '../utils/date';
import { SpoolUsageHistory } from './SpoolUsageHistory';
import { AssignToAmsModal } from './spoolbuddy/AssignToAmsModal';
import { useAuth } from '../contexts/AuthContext';
import { hasRfidTag } from '../utils/rfidTagLink';
import { LinkTagModal, type LinkTagTarget } from './LinkTagModal';
import {
  inventorySuppliersQueryKey,
  invalidateInventoryLocations,
  invalidateSpoolAndLocationQueries,
} from '../utils/inventoryQueries';
import { printLabelsAfterSave as printSavedSpoolLabels } from '../utils/labelPrintJobs';
import { isLabelPrinterConfigured, useUiSettings } from '../hooks/useUiSettings';
import { autoPrintRequests, loadAutoPrintLabel, saveAutoPrintLabel } from '../utils/labelPrint';

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
  const { hasPermission } = useAuth();

  const refreshSpoolQueries = () =>
    invalidateSpoolAndLocationQueries(queryClient, spoolsQueryKey);

  const isEditing = mode === 'edit';
  const isCopying = mode === 'copy';
  const isCreating = !isEditing && !isCopying;

  // Form state
  const [formData, setFormData] = useState<SpoolFormData>(defaultFormData);
  const formDataRef = useRef(formData);
  formDataRef.current = formData;
  const [errors, setErrors] = useState<Partial<Record<keyof SpoolFormData, string>>>({});
  const [activeTab, setActiveTab] = useState<TabId>('filament');
  const [weightTouched, setWeightTouched] = useState(false);
  // Keyed on core_weight, not core_weight_catalog_id: SpoolWeightPicker selects
  // a catalogue entry by itself on mount when one matches the current weight,
  // so the id changes on forms nobody has touched. Both real user actions go
  // through core_weight.
  const [coreWeightTouched, setCoreWeightTouched] = useState(false);
  const [locationIdTouched, setLocationIdTouched] = useState(false);
  // Sent only when changed, so saving an unrelated edit cannot put back a
  // drying date that an AMS run stamped while the form was open (#2863).
  const [lastDriedTouched, setLastDriedTouched] = useState(false);
  // Supplier assignments (#2988). Held outside SpoolFormData — they are
  // relational and saved through their own replace-all endpoint. An untouched
  // create does not send them, so it keeps the backend's inherited
  // assignments instead of wiping them with an empty list; a copy always
  // sends them, see saveSupplierLinks.
  const [supplierLinks, setSupplierLinks] = useState<SupplierLinkDraft[]>([]);
  const [supplierLinksTouched, setSupplierLinksTouched] = useState(false);
  // The 3D Filament Profiles filament the spool is linked to (#2981). Loaded
  // with its own request, saved after the spool like the supplier links.
  const [threedfpFilamentId, setThreedfpFilamentId] = useState<number | null>(null);
  const [threedfpTouched, setThreedfpTouched] = useState(false);
  const threedfpTouchedRef = useRef(false);
  const [quickAdd, setQuickAdd] = useState(false);
  const [quantity, setQuantity] = useState(1);
  const [showAssignModal, setShowAssignModal] = useState(false);
  // "Print label after saving" (#3612): remembered per device, offered only
  // with a label printer set up and with inventory:update, which printing
  // needs. The choice is remembered per device, not per user, so without the
  // permission it is not followed either.
  const [autoPrintLabel, setAutoPrintLabel] = useState(loadAutoPrintLabel);
  const { data: labelSettings } = useUiSettings({ enabled: isOpen });
  const canPrintLabels = isLabelPrinterConfigured(labelSettings) && hasPermission('inventory:update');
  const printLabelsAfterSave = canPrintLabels && autoPrintLabel && !isEditing;

  // One label per created spool (#3612). Not awaited by the create flow: the
  // dialog closes at once and the print job is followed by a module-level
  // poller that toasts the result. A failure only warns, the spools are saved.
  const printCreatedLabels = (spoolIds: number[]) => {
    if (!printLabelsAfterSave || spoolIds.length === 0) return;
    const tape = labelSettings?.label_printer_tape || '50';
    printSavedSpoolLabels(spoolmanMode ? 'spoolman' : 'inventory', autoPrintRequests(spoolIds, tape), { t, showToast });
  };
  // Giving this spool's tag to the spool it belongs to (#3658).
  const [moveTarget, setMoveTarget] = useState<LinkTagTarget | null>(null);
  // Start from the last created spool (#3619). `lastApplied` drives the hint;
  // `pendingLast` holds the remembered values that point at other records
  // (location, empty-spool entry, suppliers). They are put
  // on the form only once the matching list has loaded and still contains the
  // record, so a deleted one never reaches the request.
  const [lastApplied, setLastApplied] = useState<string | null>(null);
  const [pendingLast, setPendingLast] = useState<LastSpool | null>(null);
  // The chips came from memory and the user has not touched them: a create
  // saves them only when the backend's inheritance attached nothing.
  const [suppliersFromMemory, setSuppliersFromMemory] = useState(false);
  const [knownSuppliers, setKnownSuppliers] = useState<Supplier[] | null>(null);
  // A remembered preset the user has not changed, and the product it belonged to.
  // Material the remembered values will put on the form; the profile-default effect waits for it (#3619).
  const awaitingRemembered = useRef<string | null>(null);
  const rememberedPresetRef = useRef<{ brand: string; material: string; subtype: string; code: string } | null>(null);
  // Spoolman mode: the tare on the form came from memory and the user has not set one
  // in this form. Picking a Spoolman filament releases it, see handleFilamentSelect.
  const tareFromMemory = useRef(false);
  const [locationsLoaded, setLocationsLoaded] = useState(false);
  const [catalogLoaded, setCatalogLoaded] = useState(false);

  const [movedTag, setMovedTag] = useState(false);
  useEffect(() => {
    setMoveTarget(null);
    setMovedTag(false);
    setShowAssignModal(false);
  }, [isOpen, spool?.id]);

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
  // Profile defaults (#1326). Whether the user changed anything on the
  // Printers tab: an untouched create does not save the tab, so it keeps the
  // profile default the backend applies instead of wiping it with an empty
  // list -- the same rule as the supplier assignments.
  const [printerProfilesTouched, setPrinterProfilesTouched] = useState(false);
  // The tab (and an empty preset field) was filled in from the product's default.
  const [prefilledFromDefault, setPrefilledFromDefault] = useState(false);
  // What the default put in, per hotend and per (model, nozzle), so the next
  // product's default can take back what the user left as it was.
  const kFromDefault = useRef<Map<string, CalibrationProfile>>(new Map());
  const presetsFromDefault = useRef<Map<string, PresetChoice>>(new Map());
  // Hotends and (model, nozzle) keys the user set or cleared on the tab: theirs,
  // so a later default never puts back what they removed.
  const userKKeys = useRef<Set<string>>(new Set());
  const userPresetKeys = useRef<Set<string>>(new Set());
  // A ref, not state: the lookup's answer arrives after the render that asked
  // for it, and must see a preset the user picked in the meantime.
  const presetFromDefault = useRef(false);
  const [saveAsDefault, setSaveAsDefault] = useState(false);
  const [defaultScope, setDefaultScope] = useState<ProfileDefaultScope>('all');
  // The Printers tab's own setters mark it touched; the loaders here use the raw ones.
  const markChanged = <V,>(prev: Map<string, V>, next: Map<string, V>, keys: Set<string>) => {
    for (const key of new Set([...prev.keys(), ...next.keys()])) {
      if (prev.get(key) !== next.get(key)) keys.add(key);
    }
  };
  const editModelPresets: typeof setModelPresets = (value) => {
    setPrinterProfilesTouched(true);
    setModelPresets(prev => {
      const next = typeof value === 'function' ? value(prev) : value;
      markChanged(prev, next, userPresetKeys.current);
      return next;
    });
  };
  const editSelectedProfiles: typeof setSelectedProfiles = (value) => {
    setPrinterProfilesTouched(true);
    setSelectedProfiles(prev => {
      const next = typeof value === 'function' ? value(prev) : value;
      markChanged(prev, next, userKKeys.current);
      return next;
    });
  };

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
      // Fetched in Spoolman mode too: the empty spool weight picker is shown
      // there now, and its catalogue is Bambuddy's own either way (#2908).
      setLocationsLoaded(false);
      setCatalogLoaded(false);
      api.getSpoolCatalog().then((c) => { setSpoolCatalog(c); setCatalogLoaded(true); }).catch(console.error);
      api.getColorCatalog().then(setColorCatalog).catch(console.error);
      api.getLocalPresets().then(r => setLocalPresets(r.filament)).catch(console.error);
      api.getBuiltinFilaments().then(setBuiltinFilaments).catch(console.error);
      api.getLocations().then((locs) => {
        setStorageLocations(locs.map((l) => ({ id: l.id, name: l.name })));
        setLocationsLoaded(true);
      }).catch(console.error);

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
    // Depends only on `isOpen` (and the prop-side calibration count). It used
    // to read spoolmanMode for the catalogue fetch and left it out of the deps
    // on purpose -- re-running on every toggle raced the in-flight fetches with
    // unmount and emitted "test environment was torn down" errors in vitest.
    // It no longer reads it, so the deps are complete as written.
    return () => {
      cancelled = true;
    };
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

  const editingKey = useRef<string | null>(null);
  // A tag move refreshes this spool. Keep edits made in the same open dialog.
  useEffect(() => {
    if (!isOpen) editingKey.current = null;
    if (isOpen) {
      const key = mode + "-" + spool?.id;
      if (editingKey.current === key) return;
      editingKey.current = key;
      let rememberedTare = false;
      let rememberedPresetFromDefault = false;
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
          // ?? not ||: 0 g is a real tare (a spool-less coil) and must load as 0.
          core_weight: spool.core_weight ?? 250,
          core_weight_catalog_id: spool.core_weight_catalog_id ?? null,
          weight_used: isCopying ? 0 : spool.weight_used || 0,
          slicer_filament: spool.slicer_filament || '',
          note: spool.note || '',
          cost_per_kg: spool.cost_per_kg ?? null,
          category: spool.category || '',
          low_stock_threshold_pct: spool.low_stock_threshold_pct ?? null,
          material_number: spool.material_number || '',
          location_id: spool.location_id ?? null,
          last_dried_at: (() => {
            const dried = isCopying ? null : parseUTCDate(spool.last_dried_at);
            return dried ? toDateTimeLocalValue(dried) : '';
          })(),
          spoolman_filament_id: null,
        });
        setPresetInputValue(spool.slicer_filament_name || spool.slicer_filament || '');

        // Load K-profiles for this spool.
        setSelectedProfiles(selectionFromKProfiles(spool.k_profiles ?? []));
        // Supplier assignments (#2988) — copied on copy: they describe where
        // the product is bought, which is what a copy shares.
        setSupplierLinks(
          (spool.suppliers ?? []).map((link) => ({
            supplier_id: link.supplier_id,
            supplier_name: link.supplier_name,
            supplier_article_number: link.supplier_article_number ?? '',
            quoted_price_per_kg: link.quoted_price_per_kg,
            // Where a COPY was bought is unknown — only a real edit keeps it.
            is_purchase_source: isCopying ? false : link.is_purchase_source,
          }))
        );
      } else {
        // Create: start from the last created spool (#3619). Not for edit or
        // copy, which carry the spool they were opened on.
        const last = isCreating ? loadLastSpool(spoolmanMode ? 'spoolman' : 'builtin') : null;
        setFormData(last ? {
          ...defaultFormData,
          brand: last.brand ?? '',
          material: last.material ?? '',
          subtype: last.subtype ?? '',
          slicer_filament: last.slicer_filament ?? '',
          label_weight: last.label_weight ?? defaultFormData.label_weight,
          core_weight: last.core_weight ?? defaultFormData.core_weight,
          cost_per_kg: last.cost_per_kg ?? null,
          category: last.category ?? '',
        } : defaultFormData);
        setPresetInputValue(last ? last.slicer_filament_name || last.slicer_filament || '' : '');
        setSelectedProfiles(new Map());
        setSupplierLinks([]);
        const name = last ? [last.brand, last.material, last.subtype].filter(Boolean).join(' ') : '';
        rememberedTare = last?.core_weight !== undefined;
        // An empty name still shows the hint, with a generic label.
        setLastApplied(last ? name : null);
        setPendingLast(last);
        setKnownSuppliers(null);
        setSuppliersFromMemory(false);
        rememberedPresetFromDefault = last?.preset_from_default === true;
        awaitingRemembered.current = last ? (last.material ?? '').trim() : null;
        rememberedPresetRef.current = last?.slicer_filament && !rememberedPresetFromDefault
          ? { brand: last.brand ?? '', material: last.material ?? '', subtype: last.subtype ?? '', code: last.slicer_filament }
          : null;
      }
      if (spool || !isCreating) {
        setLastApplied(null);
        setPendingLast(null);
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
      setLastDriedTouched(false);
      // A copy of a Spoolman spool with its own tare carries that tare, as it
      // would any other field shown in the form; one that inherits keeps
      // inheriting. Only Spoolman spools report the flag (#2908).
      // A remembered tare on a Spoolman create is sent like a chosen one.
      setCoreWeightTouched((isCopying && spool?.core_weight_is_inherited === false) || (spoolmanMode && rememberedTare));
      tareFromMemory.current = spoolmanMode && rememberedTare;
      setLocationIdTouched(false);
      setSupplierLinksTouched(false);
      setPrinterProfilesTouched(false);
      setPrefilledFromDefault(false);
      kFromDefault.current = new Map();
      presetsFromDefault.current = new Map();
      userKKeys.current = new Set();
      userPresetKeys.current = new Set();
      // A remembered preset that a profile default filled stays replaceable by the next product's default (#3619).
      presetFromDefault.current = rememberedPresetFromDefault;
      setSaveAsDefault(false);
      setDefaultScope('all');
    } else {
      // Closed: nothing remembered may linger into the next open or leak onto another form.
      setLastApplied(null);
      setPendingLast(null);
      setSuppliersFromMemory(false);
      rememberedPresetRef.current = null;
      awaitingRemembered.current = null;
      tareFromMemory.current = false;
    }
  }, [isOpen, spool, mode, isCopying, isEditing, isCreating, spoolmanMode]);

  // Suppliers are only needed to validate a remembered assignment (#3619).
  // Always a fresh fetch: a cached list could still hold a deleted supplier.
  const hasPendingSuppliers = !!pendingLast?.supplier_ids?.length;
  useEffect(() => {
    if (!isOpen || !isCreating || !hasPendingSuppliers) return;
    let cancelled = false;
    queryClient
      .fetchQuery({ queryKey: inventorySuppliersQueryKey, queryFn: api.getSuppliers, staleTime: 0 })
      .then((list) => { if (!cancelled) setKnownSuppliers(list); })
      .catch(console.error);
    return () => { cancelled = true; };
  }, [isOpen, isCreating, hasPendingSuppliers, queryClient]);

  // Put the remembered records onto the form once their lists are loaded and
  // only if they still exist (#3619). Whatever the user already changed wins.
  useEffect(() => {
    if (!isOpen || !isCreating || !pendingLast) return;
    const rest: LastSpool = { ...pendingLast };
    let changed = false;
    const done = <K extends keyof LastSpool>(key: K) => { delete rest[key]; changed = true; };
    const current = formDataRef.current;

    if (rest.location_id != null && locationsLoaded) {
      const id = rest.location_id;
      if (!locationIdTouched && current.location_id == null && storageLocations.some((l) => l.id === id)) {
        setFormData((prev) => (prev.location_id == null ? { ...prev, location_id: id } : prev));
      }
      done('location_id');
    }
    if (rest.core_weight_catalog_id != null && catalogLoaded) {
      const id = rest.core_weight_catalog_id;
      const entry = spoolCatalog.find((c) => c.id === id);
      // Only when it still describes the remembered tare, else the weight picker would swap it anyway.
      if (entry && entry.weight === current.core_weight && current.core_weight_catalog_id == null) {
        setFormData((prev) => (prev.core_weight_catalog_id == null ? { ...prev, core_weight_catalog_id: id } : prev));
      }
      done('core_weight_catalog_id');
    }
    if (rest.supplier_ids && knownSuppliers) {
      if (!supplierLinksTouched) {
        const links = rest.supplier_ids
          .map((id) => knownSuppliers.find((s) => s.id === id))
          .filter((s): s is NonNullable<typeof s> => !!s)
          .map((s) => ({
            supplier_id: s.id,
            supplier_name: s.name,
            supplier_article_number: '',
            quoted_price_per_kg: null,
            is_purchase_source: false,
          }));
        if (links.length > 0) {
          setSupplierLinks(links);
          // Not "touched": an untouched create keeps the backend's inheritance, see saveSupplierLinks.
          setSuppliersFromMemory(true);
        }
      }
      done('supplier_ids');
    }
    if (changed) setPendingLast(rest);
  }, [isOpen, isCreating, pendingLast, locationsLoaded, catalogLoaded, storageLocations, spoolCatalog, knownSuppliers,
    locationIdTouched, supplierLinksTouched]);

  // "Clear" on the last-spool hint: back to an empty create form.
  const clearLastSpool = () => {
    setFormData(defaultFormData);
    setPresetInputValue('');
    setSupplierLinks([]);
    setSupplierLinksTouched(false);
    setSuppliersFromMemory(false);
    rememberedPresetRef.current = null;
    setLocationIdTouched(false);
    setCoreWeightTouched(false);
    tareFromMemory.current = false;
    setPendingLast(null);
    setLastApplied(null);
    presetFromDefault.current = false;
  };

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

  // The spool's 3DFP link (#2981). A copy keeps it: it names the product.
  useEffect(() => {
    if (!isOpen) return;
    setThreedfpFilamentId(null);
    setThreedfpTouched(false);
    threedfpTouchedRef.current = false;
    if (!spool) return;
    let cancelled = false;
    api
      .getThreedfpSpoolLink(spoolmanMode ? 'spoolman' : 'inventory', spool.id)
      .then((link) => {
        // A lookup made before this answered is the user's choice.
        if (!cancelled && !threedfpTouchedRef.current) setThreedfpFilamentId(link.filament_id);
      })
      .catch((err) => console.error('Failed to load the 3D Filament Profiles link:', err));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, spool?.id, spoolmanMode]);

  // A new spool starts from its product's profile default (#1326): fill the
  // Printers tab, and an empty preset field, with what the create will apply,
  // so the user sees it and can change it before saving. Only for a fresh
  // create -- an edit shows the spool's own profiles and a copy the copied
  // ones. What the user set wins: the default only fills hotends and models
  // left empty, so a touched tab still shows (and saves) the rest of it.
  // Follows the product as it is typed: what one product's default put in and
  // the user left alone gives way to the next product's.
  useEffect(() => {
    if (!isOpen || !isCreating) return;
    const material = formData.material.trim();
    // This effect runs in the same commit as the reset above, with the form
    // still holding the previous material: looking that up would wipe a
    // remembered preset. Wait until the remembered values are on the form.
    if (awaitingRemembered.current !== null) {
      if (material !== awaitingRemembered.current) return;
      awaitingRemembered.current = null;
    }
    let cancelled = false;
    const apply = (resolved: ResolvedProfileDefault | null) => {
      if (cancelled) return;
      const kRows = selectionFromKProfiles(resolved?.k_profiles ?? []);
      // Read before the updaters, which React may run twice.
      const previousK = kFromDefault.current;
      const previousPresets = presetsFromDefault.current;
      setSelectedProfiles(prev => {
        const next = new Map(prev);
        for (const [key, value] of previousK) if (next.get(key) === value) next.delete(key);
        const filled = new Map<string, CalibrationProfile>();
        for (const [key, value] of kRows) {
          if (next.has(key) || userKKeys.current.has(key)) continue;
          next.set(key, value);
          filled.set(key, value);
        }
        kFromDefault.current = filled;
        return next;
      });
      const presetRows = new Map<string, PresetChoice>();
      for (const row of resolved?.presets ?? []) {
        presetRows.set(presetKey(row.printer_model, row.nozzle_diameter || ''), {
          code: row.slicer_filament || '',
          name: row.slicer_filament_name || '',
        });
      }
      setModelPresets(prev => {
        const next = new Map(prev);
        for (const [key, value] of previousPresets) if (next.get(key) === value) next.delete(key);
        const filled = new Map<string, PresetChoice>();
        for (const [key, value] of presetRows) {
          if (next.has(key) || userPresetKeys.current.has(key)) continue;
          next.set(key, value);
          filled.set(key, value);
        }
        presetsFromDefault.current = filled;
        return next;
      });
      setPrefilledFromDefault(kRows.size > 0 || presetRows.size > 0 || !!resolved?.slicer_filament);
      // A remembered preset nobody touched belongs to the remembered product: once brand,
      // material or subtype (profile defaults are keyed on all three) moved away from it,
      // it gives way to the new product's default like a default-filled one would (#3619).
      const remembered = rememberedPresetRef.current;
      if (remembered) {
        const now = formDataRef.current;
        const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();
        // An empty field is the render before the remembered values landed, not a user's choice.
        if (now.slicer_filament === remembered.code) {
          if (!same(now.brand, remembered.brand) || !same(now.material, remembered.material)
            || !same(now.subtype, remembered.subtype)) {
            presetFromDefault.current = true;
            rememberedPresetRef.current = null;
          }
        } else if (now.slicer_filament) {
          rememberedPresetRef.current = null;
        }
      }
      // The current preset, not the one of the render that asked.
      if (resolved?.slicer_filament && (!formDataRef.current.slicer_filament || presetFromDefault.current)) {
        setFormData(prev => ({ ...prev, slicer_filament: resolved.slicer_filament || '' }));
        setPresetInputValue(resolved.slicer_filament_name || resolved.slicer_filament);
        presetFromDefault.current = true;
      } else if (presetFromDefault.current) {
        // The previous product's preset; this one's default has none.
        setFormData(prev => ({ ...prev, slicer_filament: '' }));
        setPresetInputValue('');
        presetFromDefault.current = false;
      }
    };
    if (!material) {
      apply(null);
      return;
    }
    // Typed field by field: ask once the typing stops.
    const timer = setTimeout(() => {
      api
        .resolveProfileDefault({
          brand: formData.brand.trim(),
          material,
          subtype: formData.subtype.trim(),
          color_name: formData.color_name.trim(),
        })
        .then(apply)
        .catch(e => {
          // Non-fatal: the create still applies the default on the server.
          if (!cancelled) console.error('Failed to look up the profile default:', e);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [isOpen, isCreating, formData.brand, formData.material, formData.subtype, formData.color_name]);

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
    if (key === 'core_weight') {
      setCoreWeightTouched(true);
      // Set in this form: the user's, whatever filament is picked afterwards.
      tareFromMemory.current = false;
    }
    if (key === 'location_id') setLocationIdTouched(true);
    if (key === 'last_dried_at') setLastDriedTouched(true);
    // A preset the user picked is theirs: the next product's default leaves it alone.
    if (key === 'slicer_filament') {
      presetFromDefault.current = false;
      rememberedPresetRef.current = null;
    }
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
    // A tare that only came from memory (#3619) belonged to the last spool. Sent with
    // the create it would override the picked filament's own tare (#2908), so it is
    // released: the form is back to an untouched tare, which is not sent. One the
    // user set in this form stays.
    const releaseTare = tareFromMemory.current;
    if (releaseTare) {
      tareFromMemory.current = false;
      setCoreWeightTouched(false);
    }
    setFormData(prev => ({
      ...prev,
      spoolman_filament_id: filament.id,
      material,
      subtype,
      brand: filament.vendor?.name || '',
      rgba: prefillRgba,
      color_name: filament.color_name || '',
      label_weight: filament.weight ?? prev.label_weight,
      ...(releaseTare
        ? { core_weight: defaultFormData.core_weight, core_weight_catalog_id: defaultFormData.core_weight_catalog_id }
        : {}),
    }));
    showToast(t('inventory.spoolmanFilamentSelected'), 'success');
  };

  const linkThreedfp = (filamentId: number | null) => {
    threedfpTouchedRef.current = true;
    setThreedfpTouched(true);
    setThreedfpFilamentId(filamentId);
  };

  // Fill the form from a 3D Filament Profiles filament (#2981) and link the
  // spool to it. The preset is only filled when empty: for brands other than
  // Bambu Lab, 3DFP gives the generic preset (GFL99 for PLA), and a preset the
  // user chose is more specific than that.
  const applyThreedfp = (filament: ThreeDfpFilament) => {
    const presetId = filament.bambu_filament_id;
    const fillPreset = !!presetId && !formData.slicer_filament;
    if (spoolmanMode && formData.spoolman_filament_id !== null) {
      showToast(t('inventory.spoolmanFilamentUnlinked'), 'info');
    }
    setFormData((prev) => ({
      ...prev,
      ...(filament.brand_name ? { brand: filament.brand_name } : {}),
      ...(filament.material ? { material: filament.material } : {}),
      subtype: filament.material_type ?? '',
      ...(filament.color ? { color_name: filament.color } : {}),
      ...(filament.rgb ? { rgba: `${filament.rgb.slice(1).toUpperCase()}FF`, extra_colors: '', effect_type: '' } : {}),
      ...(filament.nominal_weight ? { label_weight: Math.round(filament.nominal_weight) } : {}),
      ...(filament.spool_weight ? { core_weight: Math.round(filament.spool_weight), core_weight_catalog_id: null } : {}),
      ...(fillPreset ? { slicer_filament: presetId } : {}),
      // A different product than the Spoolman catalogue entry picked before.
      ...(spoolmanMode ? { spoolman_filament_id: null } : {}),
    }));
    if (filament.spool_weight) {
      setCoreWeightTouched(true);
      // 3DFP's tare, no longer the remembered one (#3619): a Spoolman filament picked later keeps it.
      tareFromMemory.current = false;
    }
    if (fillPreset) {
      presetFromDefault.current = false;
      setPresetInputValue(builtinFilaments.find((b) => b.filament_id === presetId)?.name ?? presetId);
    }
    setErrors({});
    linkThreedfp(filament.id);
    showToast(t('inventory.threedfp.applied'), 'success');
  };

  // Handle color selection
  const handleColorUsed = (color: ColorPreset) => {
    setRecentColors(prev => saveRecentColor(color, prev));
  };

  // Remember the spool just created for the next create form (#3619).
  const rememberSpool = (created?: { suppliers?: { supplier_id: number }[] } | null) => {
    if (!isCreating) return;
    const f = formDataRef.current;
    // What the spool really has: where the chips are not saved (see shouldSaveSupplierLinks),
    // it keeps the suppliers the backend's inheritance attached, so those are remembered
    // and the next form shows them instead of chips that were skipped.
    const inheritedSupplierIds = shouldSaveSupplierLinks(created)
      ? []
      : (created?.suppliers ?? []).map((l) => l.supplier_id);
    saveLastSpool(spoolmanMode ? 'spoolman' : 'builtin', {
      brand: f.brand,
      material: f.material,
      subtype: f.subtype,
      slicer_filament: f.slicer_filament,
      slicer_filament_name: f.slicer_filament ? selectedPresetOption?.displayName || presetInputValue : '',
      label_weight: f.label_weight,
      // A Spoolman tare the user never set is inherited from the filament: not carried.
      core_weight: spoolmanMode && !coreWeightTouched ? undefined : f.core_weight,
      // Records whose list had not loaded when the form was submitted were never
      // validated or applied: keep what was remembered instead of erasing it.
      core_weight_catalog_id: f.core_weight_catalog_id
        ?? (pendingLast?.core_weight === f.core_weight ? pendingLast?.core_weight_catalog_id : null)
        ?? null,
      location_id: f.location_id ?? pendingLast?.location_id ?? null,
      cost_per_kg: f.cost_per_kg,
      category: f.category.trim(),
      supplier_ids: inheritedSupplierIds.length > 0
        ? inheritedSupplierIds
        : !supplierLinksTouched && pendingLast?.supplier_ids
          ? pendingLast.supplier_ids
          : supplierLinks.map((l) => l.supplier_id),
      preset_from_default: !!f.slicer_filament && presetFromDefault.current,
    });
  };

  // Mutations – dispatch to Spoolman proxy or local inventory based on mode
  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      spoolmanMode
        ? api.createSpoolmanInventorySpool(data as Parameters<typeof api.createSpoolmanInventorySpool>[0])
        : api.createSpool(data as Parameters<typeof api.createSpool>[0]),
    onSuccess: async (newSpool) => {
      rememberSpool(newSpool);
      if (newSpool?.id) {
        const ok = await savePrinterProfiles(newSpool.id);
        if (!ok) return;
        const suppliersOk = await saveSupplierLinks(newSpool.id, shouldSaveSupplierLinks(newSpool));
        if (!suppliersOk) return;
        if (!(await saveThreedfpLink(newSpool.id))) return;
      }
      await saveProfileDefault();
      await refreshSpoolQueries();
      if (onSpoolsCreated) onSpoolsCreated([newSpool]);
      showToast(t('inventory.spoolCreated'), 'success');
      if (newSpool?.id) printCreatedLabels([newSpool.id]);
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
      // Every copy has the same suppliers: the first one stands for all.
      if (createdSpools.length > 0) rememberSpool(createdSpools[0]);

      // Bulk create: every copy gets the same profiles and overrides. A no-op
      // when the user left the Printers tab alone (savePrinterProfiles), so a
      // plain bulk add neither fires two writes per spool nor clears the
      // profile default the backend gave every copy (#1326).
      for (const s of createdSpools) {
        await savePrinterProfiles(s.id);
      }
      // Every copy of a bulk add shares the same supplier assignments (#2988).
      const saveLinks = shouldSaveSupplierLinks(createdSpools[0]);
      if (saveLinks) {
        for (const s of createdSpools) {
          await saveSupplierLinks(s.id, true);
        }
      }
      // Every copy is the same product, so every copy gets the 3DFP link.
      for (const s of createdSpools) {
        await saveThreedfpLink(s.id);
      }
      await saveProfileDefault();
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
      printCreatedLabels(createdSpools.map((s) => s.id));
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
        const suppliersOk = await saveSupplierLinks(spool.id, shouldSaveSupplierLinks());
        if (!suppliersOk) return;
        if (!(await saveThreedfpLink(spool.id))) return;
      }
      await saveProfileDefault();
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
  const { data: assignments, isLoading: assignmentsLoading, isSuccess: assignmentsReady } = useQuery({
    queryKey: ['spool-assignments'],
    queryFn: () => api.getAssignments(),
    enabled: isOpen && isEditing && !spoolmanMode,
  });
  const { data: spoolmanSlotAssignments, isLoading: spoolmanAssignmentsLoading } = useQuery({
    queryKey: ['spoolman-slot-assignments', 'all'],
    queryFn: () => api.getSpoolmanSlotAssignments(),
    enabled: isOpen && isEditing && spoolmanMode,
  });
  const heldSlots = assignments?.filter(a => a.spool_id === spool?.id) ?? [];
  const spoolAssignment = (() => {
    if (!spool) return undefined;
    if (spoolmanMode) {
      return spoolmanSlotAssignments?.find(a => a.spoolman_spool_id === spool.id);
    }
    return heldSlots[0];
  })();
  const assignmentLoading = spoolmanMode ? spoolmanAssignmentsLoading : assignmentsLoading;

  // Read inventory + settings caches (already populated by InventoryPage) to
  // drive the category autocomplete and low-stock-threshold placeholder. #729
  const { data: allSpools } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(true),
    enabled: isOpen,
  });
  const { data: settingsForForm } = useUiSettings({ enabled: isOpen });

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
  // Autocomplete for the internal material number (#2870), mirroring the
  // category datalist above.
  const availableMaterialNumbers = (() => {
    const set = new Set<string>();
    for (const s of allSpools ?? []) {
      const n = s.material_number?.trim();
      if (n) set.add(n);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
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
        await queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments', 'all'] });
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

  // Supplier assignments (#2988): replace-all save.
  //
  // Skipped on an untouched create so the backend's inheritance can fill the
  // new spool in — an empty list would wipe what it just attached. A COPY is
  // the opposite case and always saves: the dialog seeded the chips from the
  // spool being copied and showed them, so they have to be what the copy
  // gets. Inheritance cannot stand in for that — it keys on the (material,
  // subtype, brand, color_name) tuple and so resolves to the NEWEST spool of
  // the product rather than the one on screen, and in Spoolman mode there is
  // no inheritance at all.
  //
  // Remembered chips (#3619) are not a touch: they are saved only when the
  // create response shows that inheritance attached nothing (Spoolman mode has
  // no inheritance), so they never overwrite what the backend inherited. What
  // the spool got instead is what rememberSpool stores for the next form.
  const shouldSaveSupplierLinks = (created?: { suppliers?: unknown[] } | null) =>
    supplierLinksTouched
    || isCopying
    || (isCreating && suppliersFromMemory && supplierLinks.length > 0
      && (spoolmanMode || !created?.suppliers?.length));

  const saveSupplierLinks = async (spoolId: number, shouldSave: boolean): Promise<boolean> => {
    if (!shouldSave) return true;
    // Spoolman parity (#2988): the assignment rows live Bambuddy-side either
    // way; only the endpoint differs (twin table keyed by the remote id).
    const save = spoolmanMode ? api.setSpoolmanSpoolSuppliers : api.setSpoolSuppliers;
    try {
      await save(
        spoolId,
        supplierLinks.map((link) => ({
          supplier_id: link.supplier_id,
          supplier_article_number: link.supplier_article_number.trim() || null,
          quoted_price_per_kg: link.quoted_price_per_kg,
          is_purchase_source: link.is_purchase_source,
        })),
      );
      return true;
    } catch (err) {
      console.error('SpoolFormModal.saveSupplierLinks failed:', err);
      showToast(t('inventory.suppliers.saveFailed'), 'error');
      return false;
    }
  };

  // The 3DFP link (#2981): saved when changed, and on a copy, which carries
  // the link of the spool it was copied from.
  const shouldSaveThreedfpLink = threedfpTouched || (isCopying && threedfpFilamentId !== null);

  const saveThreedfpLink = async (spoolId: number): Promise<boolean> => {
    if (!shouldSaveThreedfpLink) return true;
    // Nothing to remove on a spool that was just created.
    if (!isEditing && threedfpFilamentId === null) return true;
    try {
      await api.setThreedfpSpoolLink(spoolmanMode ? 'spoolman' : 'inventory', spoolId, threedfpFilamentId);
      return true;
    } catch (err) {
      console.error('SpoolFormModal.saveThreedfpLink failed:', err);
      showToast(t('inventory.threedfp.linkSaveFailed'), 'error');
      return false;
    }
  };

  // Save everything the Printers tab holds: one K profile per hotend and the
  // per-printer-model preset overrides. Returns false if either write failed,
  // which keeps the modal open so the user does not lose what they picked.
  // On a create, only once the user changed something there: an untouched
  // tab holds nothing, or the profile default the backend applies itself.
  const shouldSavePrinterProfiles = isEditing || isCopying || printerProfilesTouched;

  const buildPrinterProfiles = (): { profiles: SpoolKProfileInput[]; presets: SpoolFilamentPresetInput[] } => {
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

    return { profiles, presets };
  };

  const savePrinterProfiles = async (spoolId: number): Promise<boolean> => {
    if (!shouldSavePrinterProfiles) return true;
    const saveKApi = spoolmanMode ? api.saveSpoolmanKProfiles : api.saveSpoolKProfiles;
    const savePresetApi = spoolmanMode ? api.saveSpoolmanFilamentPresets : api.saveSpoolFilamentPresets;
    const { profiles, presets } = buildPrinterProfiles();

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

  const profileDefaultProduct = [formData.brand, formData.material, formData.subtype]
    .map(part => part.trim())
    .filter(Boolean)
    .join(' ');

  // "Save as default" on the Printers tab (#1326): the tab and the spool's own
  // preset become what new spools of this product start with. After the
  // spool is saved, so a refused spool save leaves the default alone; a failed
  // default save only warns -- the spool is saved either way.
  const saveProfileDefault = async (): Promise<void> => {
    if (!saveAsDefault || !formData.material.trim()) return;
    const { profiles, presets } = buildPrinterProfiles();
    const slicerFilament = formData.slicer_filament || null;
    try {
      await api.saveProfileDefault({
        brand: formData.brand.trim(),
        material: formData.material.trim(),
        subtype: formData.subtype.trim(),
        // "Only <colour>" needs a colour; without one the tick box shows, and
        // this saves, the default for all colours.
        color_name: defaultScope === 'colour' ? formData.color_name.trim() : '',
        slicer_filament: slicerFilament,
        slicer_filament_name: slicerFilament ? selectedPresetOption?.displayName || presetInputValue || null : null,
        presets,
        k_profiles: profiles,
      });
      await queryClient.invalidateQueries({ queryKey: ['profile-defaults'] });
    } catch (e) {
      console.error('Failed to save the profile default:', e);
      showToast(t('inventory.profileDefaults.saveFailed'), 'warning');
    }
  };

  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      // The nested AMS assignment dialog owns Escape while it is open. Both
      // listeners live on document, so propagation control in the child would
      // not prevent this editor handler from running for the same key press.
      if (e.key === 'Escape' && !showAssignModal && !moveTarget) onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose, showAssignModal, moveTarget]);

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

    // The chosen preset's name; none without a preset (#3596).
    const presetName = formData.slicer_filament
      ? selectedPresetOption?.displayName || presetInputValue || null
      : null;

    const data: Record<string, unknown> = {
      material: formData.material || null,
      subtype: formData.subtype || null,
      brand: formData.brand || null,
      color_name: formData.color_name || null,
      rgba: formData.rgba || null,
      extra_colors: formData.extra_colors || null,
      effect_type: formData.effect_type || null,
      label_weight: formData.label_weight,
      // In Spoolman mode the picker opens on the tare the spool resolves to,
      // which is the filament type's unless the spool has its own. Sending it
      // untouched would copy that inherited value onto the spool and stop it
      // following the filament, so only a value the user set goes out (#2908).
      // The catalogue id has no field on the Spoolman side.
      ...(spoolmanMode
        ? coreWeightTouched
          ? { core_weight: formData.core_weight }
          : {}
        : { core_weight: formData.core_weight, core_weight_catalog_id: formData.core_weight_catalog_id }),
      slicer_filament: formData.slicer_filament || null,
      slicer_filament_name: presetName,
      // The dialog has no field for the nozzle range, and an update sending
      // null wiped the one a tag, the API or an import had set (#3768). An
      // edit leaves it out and keeps it; a copy takes the source spool's.
      // Spoolman keeps it on the filament, which a copy shares anyway.
      ...(isCopying && spool && !spoolmanMode
        ? { nozzle_temp_min: spool.nozzle_temp_min, nozzle_temp_max: spool.nozzle_temp_max }
        : {}),
      note: formData.note || null,
      cost_per_kg: formData.cost_per_kg,
      category: formData.category.trim() || null,
      low_stock_threshold_pct: formData.low_stock_threshold_pct,
      material_number: formData.material_number.trim() || null,
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

    if (isEditing && lastDriedTouched) {
      data.last_dried_at = formData.last_dried_at ? new Date(formData.last_dried_at).toISOString() : null;
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
    <>
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

        {/* Print a label per new spool (#3612); only with a label printer set up
            and the permission to print. */}
        {!isEditing && canPrintLabels && (
          <label className="flex items-center gap-2 px-4 py-2 border-b border-bambu-dark-tertiary flex-shrink-0 cursor-pointer">
            <input
              type="checkbox"
              data-testid="auto-print-label"
              checked={autoPrintLabel}
              onChange={(e) => {
                setAutoPrintLabel(e.target.checked);
                saveAutoPrintLabel(e.target.checked);
              }}
              className="accent-bambu-green"
            />
            <Printer className="w-4 h-4 text-bambu-gray" />
            <span className="text-sm text-white">{t('inventory.printLabelAfterSave', 'Print label after saving')}</span>
          </label>
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
              {lastApplied !== null && isCreating && (
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary text-sm text-bambu-gray">
                  <span className="min-w-0 break-words">{lastApplied ? t('inventory.lastUsed.hint', { name: lastApplied }) : t('inventory.lastUsed.hintPlain')}</span>
                  <span aria-hidden="true">·</span>
                  <button
                    type="button"
                    onClick={clearLastSpool}
                    className="text-bambu-green hover:underline"
                  >
                    {t('inventory.lastUsed.clear')}
                  </button>
                </div>
              )}
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

              <ThreeDfpSection
                filamentId={threedfpFilamentId}
                onApply={applyThreedfp}
                onUnlink={() => linkThreedfp(null)}
              />

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
                  splitPresetProductLine={isCreating}
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
                  availableMaterialNumbers={availableMaterialNumbers}
                  availableLocations={storageLocations}
                  showLastDried={isEditing}
                  brand={formData.brand}
                  catalogColors={colorCatalog}
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

              {/* Suppliers (#2988) — both inventories: the assignments live
                  Bambuddy-side either way (Spoolman's vendor is the
                  manufacturer, not the seller), so the same section renders
                  in Spoolman mode and saves to the twin endpoint. */}
              <div>
                <SupplierSection
                  links={supplierLinks}
                  onChange={(next) => {
                    setSupplierLinks(next);
                    setSupplierLinksTouched(true);
                  }}
                  currencySymbol={currencySymbol}
                />
              </div>

              {/* Usage History: in Spoolman mode, the prints Bambuddy charged to the spool (#3407) */}
              {isEditing && spool && (
                <div>
                  <SpoolUsageHistory spoolId={spool.id} spoolman={spoolmanMode} createdAt={spool.created_at} />
                </div>
              )}
            </div>
          ) : (
            <PrinterProfilesSection
              formData={formData}
              printersWithCalibrations={resolvedCalibrations}
              filamentOptions={filamentOptions}
              modelPresets={modelPresets}
              setModelPresets={editModelPresets}
              selectedProfiles={selectedProfiles}
              setSelectedProfiles={editSelectedProfiles}
              selectedGroupId={selectedGroupId}
              setSelectedGroupId={setSelectedGroupId}
              printerModels={printerModelsData}
              isLoading={loadingCalibrations}
            />
          )}
          {activeTab === 'printers' && (
            <ProfileDefaultOption
              product={profileDefaultProduct}
              colorName={formData.color_name}
              prefilled={prefilledFromDefault}
              checked={saveAsDefault}
              onCheckedChange={setSaveAsDefault}
              scope={defaultScope}
              onScopeChange={setDefaultScope}
              disabled={!formData.material.trim()}
            />
          )}
        </div>

        {/* Footer */}
        <div className="flex flex-wrap gap-2 p-4 border-t border-bambu-dark-tertiary flex-shrink-0">
          {isEditing && (
            <div className="flex flex-wrap gap-2 mr-auto">
              {/* Either identifier counts as "tagged". A Bambu Lab spool is
                  linked by its 32-char tray UUID and carries no tag_uid at all
                  -- in Spoolman mode that is every Bambu spool, because
                  _map_spoolman_spool splits extra.tag by length -- so gating on
                  tag_uid alone left this permanently greyed out for them, while
                  the Tag ID column beside it showed the UUID and the payload
                  below already cleared both fields (#3109). */}
              <Button
                variant="secondary"
                onClick={() => deleteTagMutation.mutate()}
                disabled={isPending || movedTag || !(spool?.tag_uid || spool?.tray_uuid)}
              >
                <Tag className="w-4 h-4" />
                {t('inventory.clearRfid', 'Clear RFID Tag')}
              </Button>
              {!spoolmanMode && settingsForForm?.spoolman_enabled === false && !movedTag && hasRfidTag(spool) &&
                hasPermission('inventory:read') && hasPermission('inventory:update') && hasPermission('inventory:view_assignments') && (
                <Button variant="secondary" onClick={() => setMoveTarget({ holder: spool, assignments: heldSlots })}
                  disabled={isPending || !assignmentsReady}>
                  <Tag className="w-4 h-4" />
                  {t('inventory.rfidLink.moveTagEllipsis')}
                </Button>
              )}
              {spoolAssignment ? (
                <Button
                  variant="secondary"
                  onClick={() => unassignMutation.mutate()}
                  disabled={isPending || assignmentLoading}
                >
                  <Unlink className="w-4 h-4" />
                  {t('inventory.unassignSpool', 'Unassign')}
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  onClick={() => setShowAssignModal(true)}
                  disabled={isPending || assignmentLoading}
                >
                  <Link2 className="w-4 h-4" />
                  {t('inventory.assignSpool', 'Assign Spool')}
                </Button>
              )}
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
    {isEditing && spool && (
      <AssignToAmsModal
        isOpen={showAssignModal}
        onClose={() => setShowAssignModal(false)}
        spool={spool}
        printerId={null}
        variant="dialog"
        spoolmanMode={spoolmanMode}
      />
    )}
    {moveTarget && !spoolmanMode && (
      <LinkTagModal
        target={moveTarget}
        onClose={() => setMoveTarget(null)}
        onLinked={() => setMovedTag(true)}
      />
    )}
    </>
  );
}
