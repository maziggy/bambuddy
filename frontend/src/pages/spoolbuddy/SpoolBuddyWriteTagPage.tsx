import { useState, useEffect, useCallback, useMemo } from 'react';
import { useOutletContext } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { DiagnosticModal } from '../../components/spoolbuddy/DiagnosticModal';
import type { SpoolBuddyOutletContext } from '../../components/spoolbuddy/SpoolBuddyLayout';
import {
  api,
  spoolbuddyApi,
  type InventorySpool,
  type LocalPreset,
  type SlicerSetting,
  type SpoolCatalogEntry,
} from '../../api/client';
import { getCurrencySymbol } from '../../utils/currency';
import { FilamentSection } from '../../components/spool-form/FilamentSection';
import { ColorSection } from '../../components/spool-form/ColorSection';
import { AdditionalSection } from '../../components/spool-form/AdditionalSection';
import { PAProfileSection } from '../../components/spool-form/PAProfileSection';
import type { ColorPreset, PrinterWithCalibrations, SpoolFormData } from '../../components/spool-form/types';
import { defaultFormData, validateForm } from '../../components/spool-form/types';
import {
  buildFilamentOptions,
  extractBrandsFromPresets,
  findPresetOption,
  loadRecentColors,
  parsePresetName,
  saveRecentColor,
} from '../../components/spool-form/utils';
import { MATERIALS } from '../../components/spool-form/constants';

type Tab = 'existing' | 'new' | 'replace';
type WriteStatus = 'idle' | 'selected' | 'writing' | 'success' | 'error';
const SIMPLE_COMMON_MATERIALS = ['PLA', 'PETG', 'ABS', 'ASA', 'TPU', 'PA', 'PC', 'PVA', 'HIPS'];

export function SpoolBuddyWriteTagPage() {
  const { t } = useTranslation();
  const { sbState } = useOutletContext<SpoolBuddyOutletContext>();

  const [activeTab, setActiveTab] = useState<Tab>('existing');
  const [selectedSpool, setSelectedSpool] = useState<InventorySpool | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [writeStatus, setWriteStatus] = useState<WriteStatus>('idle');
  const [writeMessage, setWriteMessage] = useState('');
  const [untagging, setUntagging] = useState(false);
  const [tagOnReader, setTagOnReader] = useState(false);
  const [tagUid, setTagUid] = useState<string | null>(null);
  const [diagnosticOpen, setDiagnosticOpen] = useState<'scale' | 'nfc' | null>(null);

  const { data: spools = [], refetch: refetchSpools } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(false),
    refetchInterval: 10000,
  });

  const { data: devices = [] } = useQuery({
    queryKey: ['spoolbuddy-devices'],
    queryFn: () => spoolbuddyApi.getDevices(),
    refetchInterval: 5000,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const device = devices[0];
  const deviceOnline = sbState.deviceOnline;
  const currencySymbol = getCurrencySymbol(settings?.currency || 'USD');

  // Filter spools based on tab
  const filteredSpools = useMemo(() => {
    let list: InventorySpool[];
    if (activeTab === 'existing') {
      list = spools.filter(s => !s.tag_uid && !s.archived_at);
    } else if (activeTab === 'replace') {
      list = spools.filter(s => (s.tag_uid || s.tray_uuid) && !s.archived_at);
    } else {
      return [];
    }

    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      list = list.filter(s =>
        (s.material?.toLowerCase().includes(q)) ||
        (s.color_name?.toLowerCase().includes(q)) ||
        (s.brand?.toLowerCase().includes(q)) ||
        (s.subtype?.toLowerCase().includes(q))
      );
    }

    return list;
  }, [spools, activeTab, searchQuery]);

  // Listen for tag events
  const handleUnknownTag = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    const sak = detail.sak ?? detail.data?.sak;
    if (sak === 0x00) {
      setTagOnReader(true);
      setTagUid(detail.tag_uid ?? detail.data?.tag_uid ?? null);
    }
  }, []);

  const handleTagMatched = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    // Tag is on the reader — could be used for replace flow
    setTagOnReader(true);
    setTagUid(detail.tag_uid ?? detail.data?.tag_uid ?? null);
  }, []);

  const handleTagRemoved = useCallback(() => {
    setTagOnReader(false);
    setTagUid(null);
  }, []);

  const handleTagWritten = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    if (detail.spool_id === selectedSpool?.id || detail.data?.spool_id === selectedSpool?.id) {
      setWriteStatus('success');
      setWriteMessage(t('spoolbuddy.writeTag.writeSuccess', 'Tag written successfully!'));
      refetchSpools();
      setTimeout(() => {
        setWriteStatus('idle');
        setSelectedSpool(null);
        setWriteMessage('');
      }, 5000);
    }
  }, [selectedSpool, t, refetchSpools]);

  const handleWriteFailed = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    if (detail.spool_id === selectedSpool?.id || detail.data?.spool_id === selectedSpool?.id) {
      setWriteStatus('error');
      setWriteMessage(detail.message ?? detail.data?.message ?? t('spoolbuddy.writeTag.writeFailed', 'Write failed'));
    }
  }, [selectedSpool, t]);

  useEffect(() => {
    window.addEventListener('spoolbuddy-unknown-tag', handleUnknownTag);
    window.addEventListener('spoolbuddy-tag-matched', handleTagMatched);
    window.addEventListener('spoolbuddy-tag-removed', handleTagRemoved);
    window.addEventListener('spoolbuddy-tag-written', handleTagWritten);
    window.addEventListener('spoolbuddy-tag-write-failed', handleWriteFailed);
    return () => {
      window.removeEventListener('spoolbuddy-unknown-tag', handleUnknownTag);
      window.removeEventListener('spoolbuddy-tag-matched', handleTagMatched);
      window.removeEventListener('spoolbuddy-tag-removed', handleTagRemoved);
      window.removeEventListener('spoolbuddy-tag-written', handleTagWritten);
      window.removeEventListener('spoolbuddy-tag-write-failed', handleWriteFailed);
    };
  }, [handleUnknownTag, handleTagMatched, handleTagRemoved, handleTagWritten, handleWriteFailed]);

  // Clear selection when switching tabs
  useEffect(() => {
    setSelectedSpool(null);
    setWriteStatus('idle');
    setWriteMessage('');
    setSearchQuery('');
  }, [activeTab]);

  const handleWriteTag = async () => {
    if (!selectedSpool || !device) return;
    setWriteStatus('writing');
    setWriteMessage(t('spoolbuddy.writeTag.waiting', 'Waiting for SpoolBuddy...'));
    try {
      await spoolbuddyApi.writeTag(device.device_id, selectedSpool.id);
    } catch {
      setWriteStatus('error');
      setWriteMessage(t('spoolbuddy.writeTag.queueFailed', 'Failed to queue write command'));
    }
  };

  const handleCancelWrite = async () => {
    if (!device) return;
    try {
      await spoolbuddyApi.cancelWrite(device.device_id);
    } catch { /* ignore */ }
    setWriteStatus('idle');
    setWriteMessage('');
  };

  const handleUntagSpool = async () => {
    if (!selectedSpool || !isReplaceTagged(selectedSpool)) return;
    setUntagging(true);
    setWriteStatus('idle');
    setWriteMessage('');
    try {
      await api.linkTagToSpool(selectedSpool.id, {
        tag_uid: '',
        tray_uuid: '',
        data_origin: 'manual',
      });
      await refetchSpools();
      setSelectedSpool(null);
      setWriteStatus('success');
      setWriteMessage(t('spoolbuddy.writeTag.untagSuccess', 'Tag removed from spool'));
      setTimeout(() => {
        setWriteStatus('idle');
        setWriteMessage('');
      }, 2500);
    } catch {
      setWriteStatus('error');
      setWriteMessage(t('spoolbuddy.writeTag.untagFailed', 'Failed to remove tag from spool'));
    } finally {
      setUntagging(false);
    }
  };

  const handleSpoolCreated = useCallback((createdSpool: InventorySpool) => {
    setSelectedSpool(createdSpool);
    setWriteStatus('idle');
    setWriteMessage('');
    void refetchSpools();
  }, [refetchSpools]);

  const canWrite = selectedSpool && deviceOnline && writeStatus !== 'writing' && writeStatus !== 'success';

  const handleOpenNfcDiagnostics = useCallback(() => {
    setDiagnosticOpen('nfc');
  }, []);

  const handleOpenScaleDiagnostics = useCallback(() => {
    setDiagnosticOpen('scale');
  }, []);

  return (
    <div className="flex flex-col h-full">
      {diagnosticOpen && (
        <DiagnosticModal
          type={diagnosticOpen}
          onClose={() => setDiagnosticOpen(null)}
        />
      )}

      {/* Tab bar */}
      <div className="flex border-b border-bambu-dark-tertiary shrink-0">
        {([
          { key: 'existing' as Tab, label: t('spoolbuddy.writeTag.tabExisting', 'Existing Spool') },
          { key: 'new' as Tab, label: t('spoolbuddy.writeTag.tabNew', 'New Spool') },
          { key: 'replace' as Tab, label: t('spoolbuddy.writeTag.tabReplace', 'Replace Tag') },
        ]).map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex-1 py-3 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'text-bambu-green border-b-2 border-bambu-green bg-bambu-dark'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-bambu-dark-tertiary'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Main content: two columns */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left panel — spool list or form */}
        <div className="flex-1 flex flex-col overflow-hidden border-r border-bambu-dark-tertiary">
          {activeTab === 'new' ? (
            <NewSpoolTouchForm
              currencySymbol={currencySymbol}
              onCreated={handleSpoolCreated}
              selectedSpool={selectedSpool}
              t={t}
            />
          ) : (
            <>
              {/* Search */}
              <div className="p-3 shrink-0">
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t('spoolbuddy.writeTag.searchPlaceholder', 'Search by material, color, brand...')}
                  className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-bambu-green"
                />
              </div>

              {/* Spool list */}
              <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-2">
                {filteredSpools.length === 0 ? (
                  <div className="text-center text-zinc-500 py-8 text-sm">
                    {activeTab === 'existing'
                      ? t('spoolbuddy.writeTag.noUntaggedSpools', 'No spools without tags')
                      : t('spoolbuddy.writeTag.noTaggedSpools', 'No spools with tags')}
                  </div>
                ) : (
                  filteredSpools.map(spool => (
                    <SpoolListItem
                      key={spool.id}
                      spool={spool}
                      selected={selectedSpool?.id === spool.id}
                      showTag={activeTab === 'replace'}
                      onClick={() => {
                        setSelectedSpool(spool);
                        setWriteStatus('idle');
                        setWriteMessage('');
                      }}
                    />
                  ))
                )}
              </div>
            </>
          )}
        </div>

        {/* Right panel — NFC status + write action */}
        <div className="w-[340px] flex flex-col items-center justify-center p-6 shrink-0">
          <NfcStatusPanel
            writeStatus={writeStatus}
            writeMessage={writeMessage}
            selectedSpool={selectedSpool}
            tagOnReader={tagOnReader}
            tagUid={tagUid}
            deviceOnline={deviceOnline}
            canWrite={!!canWrite}
            isReplace={activeTab === 'replace'}
            canUntag={activeTab === 'replace' && !!selectedSpool && isReplaceTagged(selectedSpool)}
            untagging={untagging}
            onWrite={handleWriteTag}
            onUntag={handleUntagSpool}
            onCancel={handleCancelWrite}
            onRetry={() => { setWriteStatus('idle'); setWriteMessage(''); }}
            onOpenNfcDiagnostics={handleOpenNfcDiagnostics}
            onOpenScaleDiagnostics={handleOpenScaleDiagnostics}
            t={t}
          />
        </div>
      </div>
    </div>
  );
}

function isReplaceTagged(spool: InventorySpool): boolean {
  return !!(spool.tag_uid || spool.tray_uuid);
}

// --- Spool list item ---
function SpoolListItem({ spool, selected, showTag, onClick }: {
  spool: InventorySpool;
  selected: boolean;
  showTag: boolean;
  onClick: () => void;
}) {
  const color = spool.rgba ? `#${spool.rgba.slice(0, 6)}` : '#666';
  const remaining = Math.max(0, spool.label_weight - spool.weight_used);
  const pct = spool.label_weight > 0 ? Math.round((remaining / spool.label_weight) * 100) : 0;

  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 p-3 rounded-lg text-left transition-colors ${
        selected
          ? 'bg-bambu-green/15 border border-bambu-green/50'
          : 'bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary border border-transparent'
      }`}
    >
      {/* Color dot */}
      <div
        className="w-8 h-8 rounded-full shrink-0 border border-white/10"
        style={{ backgroundColor: color }}
      />

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-white truncate">
            {spool.brand ? `${spool.brand} ` : ''}{spool.material}{spool.subtype ? ` ${spool.subtype}` : ''}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          {spool.color_name && <span>{spool.color_name}</span>}
          <span>{remaining}g / {spool.label_weight}g ({pct}%)</span>
        </div>
        {showTag && spool.tag_uid && (
          <div className="text-xs text-zinc-500 mt-0.5 font-mono">{spool.tag_uid}</div>
        )}
      </div>

      {/* Check mark when selected */}
      {selected && (
        <svg className="w-5 h-5 text-bambu-green shrink-0" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
        </svg>
      )}
    </button>
  );
}

type NewSpoolSubTab = 'filament' | 'pa-profile';
type NewSpoolViewMode = 'simple' | 'full';

// --- New spool touch form (mirrors Add Spool fields/options in kiosk-friendly layout) ---
function NewSpoolTouchForm({ currencySymbol, onCreated, selectedSpool, t }: {
  currencySymbol: string;
  onCreated: (spool: InventorySpool) => void;
  selectedSpool: InventorySpool | null;
  t: (key: string, fallback: string) => string;
}) {
  const [viewMode, setViewMode] = useState<NewSpoolViewMode>('simple');
  const [activeSubTab, setActiveSubTab] = useState<NewSpoolSubTab>('filament');
  const [formData, setFormData] = useState<SpoolFormData>(defaultFormData);
  const [errors, setErrors] = useState<Partial<Record<keyof SpoolFormData, string>>>({});
  const [quickAdd, setQuickAdd] = useState(false);
  const [quantity, setQuantity] = useState(1);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [cloudAuthenticated, setCloudAuthenticated] = useState(false);
  const [loadingCloudPresets, setLoadingCloudPresets] = useState(false);
  const [cloudPresets, setCloudPresets] = useState<SlicerSetting[]>([]);
  const [localPresets, setLocalPresets] = useState<LocalPreset[]>([]);
  const [spoolCatalog, setSpoolCatalog] = useState<SpoolCatalogEntry[]>([]);
  const [colorCatalog, setColorCatalog] = useState<
    { manufacturer: string; color_name: string; hex_color: string; material: string | null }[]
  >([]);
  const [presetInputValue, setPresetInputValue] = useState('');
  const [recentColors, setRecentColors] = useState<ColorPreset[]>([]);

  const [printersWithCalibrations, setPrintersWithCalibrations] = useState<PrinterWithCalibrations[]>([]);
  const [selectedProfiles, setSelectedProfiles] = useState<Set<string>>(new Set());
  const [expandedPrinters, setExpandedPrinters] = useState<Set<string>>(new Set());

  useEffect(() => {
    setRecentColors(loadRecentColors());
  }, []);

  useEffect(() => {
    const fetchData = async () => {
      setLoadingCloudPresets(true);
      try {
        const status = await api.getCloudStatus();
        setCloudAuthenticated(status.is_authenticated);
        if (status.is_authenticated) {
          const presets = await api.getFilamentPresets();
          setCloudPresets(presets);
        }
      } catch {
        setCloudAuthenticated(false);
      } finally {
        setLoadingCloudPresets(false);
      }

      api.getSpoolCatalog().then(setSpoolCatalog).catch(() => undefined);
      api.getColorCatalog().then(setColorCatalog).catch(() => undefined);
      api.getLocalPresets().then(r => setLocalPresets(r.filament)).catch(() => undefined);

      try {
        const printers = await api.getPrinters();
        const statuses = await Promise.all(printers.map(p => api.getPrinterStatus(p.id).catch(() => null)));
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
              // ignore per-printer unsupported profile endpoints
            }
          }
          results.push({ printer: { ...printer, connected }, calibrations });
        }
        setPrintersWithCalibrations(results);
      } catch {
        // ignore calibration loading errors on kiosk form
      }
    };

    fetchData();
  }, []);

  useEffect(() => {
    if (printersWithCalibrations.length > 0) {
      setExpandedPrinters(new Set(printersWithCalibrations.map(p => String(p.printer.id))));
    }
  }, [printersWithCalibrations]);

  const filamentOptions = useMemo(
    () => buildFilamentOptions(cloudPresets, new Set(), localPresets),
    [cloudPresets, localPresets],
  );

  const selectedPresetOption = useMemo(
    () => findPresetOption(formData.slicer_filament, filamentOptions),
    [formData.slicer_filament, filamentOptions],
  );

  const baseAvailableBrands = useMemo(() => {
    const presetBrands = extractBrandsFromPresets(cloudPresets, localPresets);
    const catalogBrands = colorCatalog
      .map(entry => entry.manufacturer?.trim())
      .filter((brand): brand is string => !!brand);
    return Array.from(new Set<string>([...presetBrands, ...catalogBrands])).sort((a, b) => a.localeCompare(b));
  }, [cloudPresets, localPresets, colorCatalog]);

  const baseAvailableMaterials = useMemo(() => {
    const catalogMaterials = colorCatalog
      .map(entry => entry.material?.trim())
      .filter((material): material is string => !!material);
    return Array.from(new Set<string>([...MATERIALS, ...catalogMaterials])).sort((a, b) => a.localeCompare(b));
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
      if (parsed.brand && parsed.material) pairs.push({ brand: parsed.brand, material: parsed.material });
    }
    for (const preset of localPresets) {
      const parsed = parsePresetName(preset.name);
      const brand = preset.filament_vendor?.trim() || parsed.brand;
      const material = parsed.material;
      if (brand && material) pairs.push({ brand, material });
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

  const availableBrands = useMemo(() => {
    if (!formData.material) return baseAvailableBrands;
    const materialKey = formData.material.toLowerCase();
    const brandKeys = materialToBrands.get(materialKey);
    if (!brandKeys || brandKeys.size === 0) return baseAvailableBrands;
    return baseAvailableBrands.filter(brand => brandKeys.has(brand.toLowerCase()));
  }, [baseAvailableBrands, formData.material, materialToBrands]);

  const availableMaterials = useMemo(() => {
    if (!formData.brand) return baseAvailableMaterials;
    const brandKey = formData.brand.toLowerCase();
    const materialKeys = brandToMaterials.get(brandKey);
    if (!materialKeys || materialKeys.size === 0) return baseAvailableMaterials;
    return baseAvailableMaterials.filter(material => materialKeys.has(material.toLowerCase()));
  }, [baseAvailableMaterials, formData.brand, brandToMaterials]);

  const updateField = <K extends keyof SpoolFormData>(key: K, value: SpoolFormData[K]) => {
    setFormData(prev => ({ ...prev, [key]: value }));
    if (errors[key]) {
      setErrors(prev => ({ ...prev, [key]: undefined }));
    }
  };

  const handleColorUsed = (color: ColorPreset) => {
    setRecentColors(prev => saveRecentColor(color, prev));
  };

  const saveKProfiles = async (spoolId: number) => {
    if (selectedProfiles.size === 0) {
      try {
        await api.saveSpoolKProfiles(spoolId, []);
      } catch {
        // ignore
      }
      return;
    }

    const profiles = [];
    for (const key of selectedProfiles) {
      const [printerIdStr, caliIdxStr, extruderStr] = key.split(':');
      const printerId = parseInt(printerIdStr);
      const caliIdx = parseInt(caliIdxStr);
      const extruder = extruderStr === 'null' ? 0 : parseInt(extruderStr);

      const pc = printersWithCalibrations.find(p => p.printer.id === printerId);
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
      await api.saveSpoolKProfiles(spoolId, profiles);
    }
  };

  const handleCreate = async () => {
    setCreateError(null);
    const validation = validateForm(formData, viewMode === 'simple' ? true : quickAdd);
    if (!validation.isValid) {
      setErrors(validation.errors);
      setActiveSubTab('filament');
      return;
    }

    const presetName = selectedPresetOption?.displayName || presetInputValue || null;
    const payload = {
      material: formData.material,
      subtype: formData.subtype || null,
      brand: formData.brand || null,
      color_name: formData.color_name || null,
      rgba: formData.rgba || null,
      label_weight: formData.label_weight,
      core_weight: formData.core_weight,
      core_weight_catalog_id: formData.core_weight_catalog_id,
      weight_used: formData.weight_used,
      slicer_filament: formData.slicer_filament || null,
      slicer_filament_name: presetName,
      nozzle_temp_min: null,
      nozzle_temp_max: null,
      note: formData.note || null,
      cost_per_kg: formData.cost_per_kg,
    };

    setCreating(true);
    try {
      if (quantity > 1) {
        const created = await api.bulkCreateSpools(payload, quantity);
        for (const spool of created) {
          await saveKProfiles(spool.id);
        }
        if (created.length > 0) onCreated(created[0]);
      } else {
        const created = await api.createSpool(payload);
        await saveKProfiles(created.id);
        onCreated(created);
      }
    } catch {
      setCreateError(t('spoolbuddy.writeTag.createFailed', 'Failed to create spool'));
    } finally {
      setCreating(false);
    }
  };

  const simpleColorHex = `#${(formData.rgba || '808080FF').slice(0, 6)}`;

  return (
    <div className="p-3 space-y-3 overflow-y-auto h-full">
      <div className="flex items-center justify-between px-2 py-2 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary">
        <span className="text-sm text-zinc-200">{t('spoolbuddy.writeTag.viewMode', 'View')}</span>
        <div className="flex rounded-lg overflow-hidden border border-bambu-dark-tertiary">
          <button
            type="button"
            onClick={() => setViewMode('simple')}
            className={`px-3 py-1.5 text-xs font-medium ${
              viewMode === 'simple' ? 'bg-bambu-green/20 text-bambu-green' : 'bg-bambu-dark text-zinc-400'
            }`}
          >
            {t('spoolbuddy.writeTag.simpleView', 'Simple')}
          </button>
          <button
            type="button"
            onClick={() => setViewMode('full')}
            className={`px-3 py-1.5 text-xs font-medium ${
              viewMode === 'full' ? 'bg-bambu-green/20 text-bambu-green' : 'bg-bambu-dark text-zinc-400'
            }`}
          >
            {t('spoolbuddy.writeTag.fullView', 'Full')}
          </button>
        </div>
      </div>

      {viewMode === 'simple' ? (
        selectedSpool ? (
          <div className="flex flex-col items-center justify-center h-full p-6 text-center bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
            <div
              className="w-12 h-12 rounded-full mb-4 border border-white/10"
              style={{ backgroundColor: selectedSpool.rgba ? `#${selectedSpool.rgba.slice(0, 6)}` : '#666' }}
            />
            <p className="text-white font-medium">
              {selectedSpool.brand ? `${selectedSpool.brand} ` : ''}{selectedSpool.material}
            </p>
            {selectedSpool.color_name && <p className="text-zinc-400 text-sm">{selectedSpool.color_name}</p>}
            <p className="text-zinc-500 text-xs mt-1">{selectedSpool.label_weight}g</p>
            <p className="text-bambu-green text-sm mt-4">{t('spoolbuddy.writeTag.spoolCreated', 'Spool created! Ready to write.')}</p>
          </div>
        ) : (
          <div className="p-4 space-y-4 overflow-y-auto bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
            <div>
              <label className="block text-xs text-zinc-400 mb-1">{t('spoolbuddy.writeTag.material', 'Material')}</label>
              <select
                value={formData.material}
                onChange={(e) => updateField('material', e.target.value)}
                className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-sm text-white focus:outline-none focus:border-bambu-green"
              >
                {SIMPLE_COMMON_MATERIALS.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>

            <div className="flex gap-3">
              <div className="flex-1">
                <label className="block text-xs text-zinc-400 mb-1">{t('spoolbuddy.writeTag.colorName', 'Color Name')}</label>
                <input
                  type="text"
                  value={formData.color_name}
                  onChange={(e) => updateField('color_name', e.target.value)}
                  placeholder="Jade White"
                  className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-bambu-green"
                />
              </div>
              <div>
                <label className="block text-xs text-zinc-400 mb-1">{t('spoolbuddy.writeTag.color', 'Color')}</label>
                <input
                  type="color"
                  value={simpleColorHex}
                  onChange={(e) => updateField('rgba', e.target.value.replace('#', '').toUpperCase() + 'FF')}
                  className="w-10 h-9 bg-transparent border border-bambu-dark-tertiary rounded cursor-pointer"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs text-zinc-400 mb-1">{t('spoolbuddy.writeTag.brand', 'Brand')}</label>
              <input
                type="text"
                value={formData.brand}
                onChange={(e) => updateField('brand', e.target.value)}
                placeholder="Polymaker"
                className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-bambu-green"
              />
            </div>

            <div>
              <label className="block text-xs text-zinc-400 mb-1">{t('spoolbuddy.writeTag.weight', 'Weight (g)')}</label>
              <input
                type="number"
                value={formData.label_weight}
                onChange={(e) => updateField('label_weight', parseInt(e.target.value) || 0)}
                min={0}
                max={10000}
                className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-sm text-white focus:outline-none focus:border-bambu-green"
              />
            </div>

            <button
              onClick={handleCreate}
              disabled={creating || !formData.material}
              className="w-full py-2.5 bg-bambu-green hover:bg-bambu-green/80 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded transition-colors"
            >
              {creating ? t('spoolbuddy.writeTag.creating', 'Creating...') : t('spoolbuddy.writeTag.createSpool', 'Create Spool')}
            </button>
          </div>
        )
      ) : (
        <>
      <div className="flex items-center justify-between px-2 py-2 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary">
        <span className="text-sm text-zinc-200">{t('inventory.quickAdd')}</span>
        <button
          type="button"
          onClick={() => setQuickAdd((prev) => !prev)}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
            quickAdd ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
          }`}
        >
          <span className={`inline-block h-4.5 w-4.5 rounded-full bg-white transition-transform ${quickAdd ? 'translate-x-6' : 'translate-x-1'}`} />
        </button>
      </div>

      <div className="flex border border-bambu-dark-tertiary rounded-lg overflow-hidden">
        <button
          onClick={() => setActiveSubTab('filament')}
          className={`flex-1 py-2.5 text-sm font-medium ${
            activeSubTab === 'filament' ? 'bg-bambu-green/15 text-bambu-green' : 'bg-bambu-dark-secondary text-zinc-400'
          }`}
        >
          {t('inventory.filamentInfoTab')}
        </button>
        {!quickAdd && (
          <button
            onClick={() => setActiveSubTab('pa-profile')}
            className={`flex-1 py-2.5 text-sm font-medium ${
              activeSubTab === 'pa-profile' ? 'bg-bambu-green/15 text-bambu-green' : 'bg-bambu-dark-secondary text-zinc-400'
            }`}
          >
            {t('inventory.paProfileTab')}
          </button>
        )}
      </div>

      <div className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-3">
        {activeSubTab === 'filament' ? (
          <div className="space-y-4">
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
              quickAdd={quickAdd}
              quantity={quantity}
              onQuantityChange={setQuantity}
              errors={errors}
            />

            <ColorSection
              formData={formData}
              updateField={updateField}
              recentColors={recentColors}
              onColorUsed={handleColorUsed}
              catalogColors={colorCatalog}
            />

            <AdditionalSection
              formData={formData}
              updateField={updateField}
              spoolCatalog={spoolCatalog}
              currencySymbol={currencySymbol}
            />
          </div>
        ) : (
          <PAProfileSection
            formData={formData}
            updateField={updateField}
            printersWithCalibrations={printersWithCalibrations}
            selectedProfiles={selectedProfiles}
            setSelectedProfiles={setSelectedProfiles}
            expandedPrinters={expandedPrinters}
            setExpandedPrinters={setExpandedPrinters}
          />
        )}
      </div>
        </>
      )}

      {createError && (
        <div className="text-sm text-red-400 bg-red-900/20 border border-red-900/40 rounded-lg px-3 py-2">
          {createError}
        </div>
      )}

      {viewMode === 'full' && (
        <button
          onClick={handleCreate}
          disabled={creating}
          className="w-full py-3 bg-bambu-green hover:bg-bambu-green/80 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded transition-colors"
        >
          {creating ? t('spoolbuddy.writeTag.creating', 'Creating...') : t('spoolbuddy.writeTag.createSpool', 'Create Spool')}
        </button>
      )}

      {viewMode === 'full' && selectedSpool && (
        <div className="flex flex-col items-center justify-center p-4 text-center bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
          <div
            className="w-12 h-12 rounded-full mb-4 border border-white/10"
            style={{ backgroundColor: selectedSpool.rgba ? `#${selectedSpool.rgba.slice(0, 6)}` : '#666' }}
          />
          <p className="text-white font-medium">
            {selectedSpool.brand ? `${selectedSpool.brand} ` : ''}{selectedSpool.material}
          </p>
          {selectedSpool.color_name && <p className="text-zinc-400 text-sm">{selectedSpool.color_name}</p>}
          <p className="text-zinc-500 text-xs mt-1">{selectedSpool.label_weight}g</p>
          <p className="text-bambu-green text-sm mt-4">{t('spoolbuddy.writeTag.spoolCreated', 'Spool created! Ready to write.')}</p>
        </div>
      )}
    </div>
  );
}

// --- NFC status panel ---
function NfcStatusPanel({ writeStatus, writeMessage, selectedSpool, tagOnReader, tagUid, deviceOnline, canWrite, isReplace, canUntag, untagging, onWrite, onUntag, onCancel, onRetry, onOpenNfcDiagnostics, onOpenScaleDiagnostics, t }: {
  writeStatus: WriteStatus;
  writeMessage: string;
  selectedSpool: InventorySpool | null;
  tagOnReader: boolean;
  tagUid: string | null;
  deviceOnline: boolean;
  canWrite: boolean;
  isReplace: boolean;
  canUntag: boolean;
  untagging: boolean;
  onWrite: () => void;
  onUntag: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onOpenNfcDiagnostics: () => void;
  onOpenScaleDiagnostics: () => void;
  t: (key: string, fallback: string) => string;
}) {
  // Success state
  if (writeStatus === 'success') {
    return (
      <div className="flex flex-col items-center text-center space-y-4">
        <div className="w-16 h-16 rounded-full bg-green-500/20 flex items-center justify-center">
          <svg className="w-8 h-8 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <p className="text-green-400 font-medium">{writeMessage}</p>
        {selectedSpool && (
          <p className="text-zinc-400 text-sm">
            {selectedSpool.brand ? `${selectedSpool.brand} ` : ''}{selectedSpool.material}
            {selectedSpool.color_name ? ` - ${selectedSpool.color_name}` : ''}
          </p>
        )}
      </div>
    );
  }

  // Error state
  if (writeStatus === 'error') {
    return (
      <div className="flex flex-col items-center text-center space-y-4">
        <div className="w-16 h-16 rounded-full bg-red-500/20 flex items-center justify-center">
          <svg className="w-8 h-8 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </div>
        <p className="text-red-400 font-medium">{writeMessage}</p>
        <button
          onClick={onRetry}
          className="px-4 py-2 bg-bambu-dark-tertiary hover:bg-bambu-dark-secondary text-white text-sm rounded transition-colors"
        >
          {t('spoolbuddy.writeTag.tryAgain', 'Try Again')}
        </button>
      </div>
    );
  }

  // Writing state
  if (writeStatus === 'writing') {
    return (
      <div className="flex flex-col items-center text-center space-y-4">
        <div className="relative w-16 h-16">
          <div className="absolute inset-0 rounded-full border-2 border-bambu-green/30 animate-ping" />
          <div className="absolute inset-2 rounded-full border-2 border-bambu-green/50 animate-pulse" />
          <div className="absolute inset-0 flex items-center justify-center">
            <NfcIcon className="w-8 h-8 text-bambu-green" />
          </div>
        </div>
        <p className="text-bambu-green font-medium">{t('spoolbuddy.writeTag.writing', 'Writing tag...')}</p>
        <p className="text-zinc-500 text-xs">{writeMessage}</p>
        <button
          onClick={onCancel}
          className="px-4 py-2 bg-bambu-dark-tertiary hover:bg-bambu-dark-secondary text-zinc-400 text-sm rounded transition-colors"
        >
          {t('spoolbuddy.writeTag.cancel', 'Cancel')}
        </button>
      </div>
    );
  }

  // Device offline
  if (!deviceOnline) {
    return (
      <div className="flex flex-col items-center text-center space-y-3">
        <NfcIcon className="w-12 h-12 text-zinc-600" />
        <p className="text-zinc-500 text-sm">{t('spoolbuddy.writeTag.deviceOffline', 'SpoolBuddy is offline')}</p>
      </div>
    );
  }

  // No spool selected
  if (!selectedSpool) {
    return (
      <div className="flex flex-col items-center text-center space-y-3">
        <NfcIcon className="w-12 h-12 text-zinc-600" />
        <p className="text-zinc-400 text-sm">{t('spoolbuddy.writeTag.selectSpool', 'Select a spool, then place a blank NTAG on the reader')}</p>
      </div>
    );
  }

  // Spool selected — show summary + write button
  const spoolColor = selectedSpool.rgba ? `#${selectedSpool.rgba.slice(0, 6)}` : '#666';

  return (
    <div className="flex flex-col items-center text-center space-y-4 w-full">
      {/* NFC indicator */}
      <div className="relative w-16 h-16">
        {tagOnReader ? (
          <>
            <div className="absolute inset-0 rounded-full bg-bambu-green/10" />
            <div className="absolute inset-0 flex items-center justify-center">
              <NfcIcon className="w-8 h-8 text-bambu-green" />
            </div>
          </>
        ) : (
          <>
            <div className="absolute inset-0 rounded-full border-2 border-zinc-600 animate-pulse" />
            <div className="absolute inset-0 flex items-center justify-center">
              <NfcIcon className="w-8 h-8 text-zinc-500" />
            </div>
          </>
        )}
      </div>

      {tagOnReader ? (
        <div className="space-y-1">
          <p className="text-bambu-green text-sm font-medium">{t('spoolbuddy.writeTag.tagReady', 'Tag detected — ready to write')}</p>
          {tagUid && <p className="text-zinc-500 text-xs font-mono">{tagUid}</p>}
        </div>
      ) : (
        <p className="text-zinc-400 text-sm">{t('spoolbuddy.writeTag.placeTag', 'Place an NTAG on the reader')}</p>
      )}

      {/* Selected spool summary */}
      <div className="w-full bg-bambu-dark-secondary rounded-lg p-3 space-y-2">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full border border-white/10 shrink-0" style={{ backgroundColor: spoolColor }} />
          <div className="text-left min-w-0">
            <p className="text-white text-sm font-medium truncate">
              {selectedSpool.brand ? `${selectedSpool.brand} ` : ''}{selectedSpool.material}
            </p>
            {selectedSpool.color_name && <p className="text-zinc-400 text-xs">{selectedSpool.color_name}</p>}
          </div>
        </div>
        <div className="text-xs text-zinc-500">{selectedSpool.label_weight}g</div>
      </div>

      {/* Replace warning */}
      {isReplace && selectedSpool.tag_uid && (
        <p className="text-yellow-500/80 text-xs">
          {t('spoolbuddy.writeTag.replaceWarning', 'Old tag will be unlinked. New tag will replace it.')}
        </p>
      )}

      {/* Write button */}
      <button
        onClick={onWrite}
        disabled={!canWrite}
        className="w-full py-3 bg-bambu-green hover:bg-bambu-green/80 disabled:opacity-40 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors text-sm"
      >
        {isReplace
          ? t('spoolbuddy.writeTag.replaceTag', 'Replace Tag')
          : t('spoolbuddy.writeTag.writeTag', 'Write Tag')}
      </button>

      {isReplace && canUntag && (
        <button
          onClick={onUntag}
          disabled={untagging || writeStatus === 'writing'}
          className="w-full py-2.5 bg-bambu-dark-tertiary hover:bg-bambu-dark-secondary disabled:opacity-40 disabled:cursor-not-allowed text-zinc-200 rounded-lg transition-colors text-sm"
        >
          {untagging
            ? t('spoolbuddy.writeTag.untagging', 'Removing tag...')
            : t('spoolbuddy.writeTag.untagSpool', 'Untag Selected Spool')}
        </button>
      )}

      {/* Diagnostic buttons */}
      <div className="w-full flex gap-2 pt-2">
        <button
          onClick={onOpenNfcDiagnostics}
          className="flex-1 py-2 px-2 bg-bambu-dark-tertiary hover:bg-bambu-dark-secondary text-zinc-300 text-xs rounded transition-colors"
        >
          {t('spoolbuddy.writeTag.nfcDiagnostics', 'NFC Diag')}
        </button>
        <button
          onClick={onOpenScaleDiagnostics}
          className="flex-1 py-2 px-2 bg-bambu-dark-tertiary hover:bg-bambu-dark-secondary text-zinc-300 text-xs rounded transition-colors"
        >
          {t('spoolbuddy.writeTag.scaleDiagnostics', 'Scale Diag')}
        </button>
      </div>
    </div>
  );
}

// --- NFC icon ---
function NfcIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.288 15.038a5.25 5.25 0 017.424 0M5.106 11.856c3.807-3.808 9.98-3.808 13.788 0M1.924 8.674c5.565-5.565 14.587-5.565 20.152 0" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M12.53 18.22l-.53.53-.53-.53a.75.75 0 011.06 0z" />
    </svg>
  );
}
