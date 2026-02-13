import { useState, useMemo, useEffect, useCallback } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, Settings2, ChevronDown, CheckCircle2, RotateCcw } from 'lucide-react';
import { api } from '../api/client';
import type { KProfile } from '../api/client';
import { Button } from './Button';

interface SlotInfo {
  amsId: number;
  trayId: number;
  trayCount: number;
  trayType?: string;
  trayColor?: string;
  traySubBrands?: string;
  trayInfoIdx?: string;
}

// Get proper AMS label (handles HT AMS with ID 128+)
function getAmsLabel(amsId: number, trayCount: number): string {
  // External spool
  if (amsId === 255) return 'External';

  let normalizedId: number;
  let isHt = false;

  if (amsId >= 128 && amsId <= 135) {
    // HT AMS range: 128-135 → A-H
    normalizedId = amsId - 128;
    isHt = true;
  } else if (amsId >= 0 && amsId <= 3) {
    // Regular AMS range: 0-3 → A-D
    normalizedId = amsId;
    // Check tray count as secondary indicator
    isHt = trayCount === 1;
  } else {
    // Unknown range - fallback to A
    normalizedId = 0;
  }

  // Cap to valid letter range (A-H)
  normalizedId = Math.max(0, Math.min(normalizedId, 7));
  const letter = String.fromCharCode(65 + normalizedId);

  return isHt ? `HT-${letter}` : `AMS-${letter}`;
}

// Convert setting_id to tray_info_idx (filament_id format)
// Bambu format: setting_id "GFSL05" → tray_info_idx "GFL05"
function convertToTrayInfoIdx(settingId: string): string {
  // Strip version suffix if present (e.g., GFSL05_07 -> GFSL05)
  const baseId = settingId.includes('_') ? settingId.split('_')[0] : settingId;

  // Bambu presets start with "GFS" - remove the 'S' to get filament_id
  if (baseId.startsWith('GFS')) {
    return 'GF' + baseId.slice(3);
  }

  // User presets (PFUS*, PFSP*) - use the base setting_id (without version suffix)
  // This follows the pattern that filament_id and setting_id share the same base ID
  if (baseId.startsWith('PFUS') || baseId.startsWith('PFSP')) {
    return baseId;  // Use base ID without version suffix
  }

  // For other formats, use as-is
  return baseId;
}

interface ConfigureAmsSlotModalProps {
  isOpen: boolean;
  onClose: () => void;
  printerId: number;
  slotInfo: SlotInfo;
  nozzleDiameter?: string;
  onSuccess?: () => void;
}

// Known filament material types
const MATERIAL_TYPES = ['PLA', 'PETG', 'ABS', 'ASA', 'TPU', 'PC', 'PA', 'NYLON', 'PVA', 'HIPS', 'PP', 'PET'];

// Extract filament type from preset name by finding known material type
function parsePresetName(name: string): { material: string; brand: string; variant: string } {
  // Remove printer/nozzle suffix first
  const withoutSuffix = name.replace(/@.+$/, '').trim();

  // Try to find a known material type in the name
  const upperName = withoutSuffix.toUpperCase();
  for (const mat of MATERIAL_TYPES) {
    // Use word boundary to match whole words only
    const regex = new RegExp(`\\b${mat}\\b`, 'i');
    if (regex.test(upperName)) {
      // Found material, extract brand (everything before material) and variant (after)
      const parts = withoutSuffix.split(regex);
      const brand = parts[0]?.trim() || '';
      const variant = parts[1]?.trim() || '';
      return { material: mat, brand, variant };
    }
  }

  // Fallback: assume first word is brand, second is material
  const parts = withoutSuffix.split(/\s+/);
  if (parts.length >= 2) {
    return { material: parts[1], brand: parts[0], variant: parts.slice(2).join(' ') };
  }

  return { material: withoutSuffix, brand: '', variant: '' };
}

// Check if a preset is a user preset (not built-in)
function isUserPreset(settingId: string): boolean {
  // Built-in presets have specific patterns, user presets are UUIDs
  return !settingId.startsWith('GF') && !settingId.startsWith('P1');
}

// Common color name to hex mapping
const COLOR_NAME_MAP: Record<string, string> = {
  // Basic colors
  'white': 'FFFFFF',
  'black': '000000',
  'red': 'FF0000',
  'green': '00FF00',
  'blue': '0000FF',
  'yellow': 'FFFF00',
  'cyan': '00FFFF',
  'magenta': 'FF00FF',
  'orange': 'FFA500',
  'purple': '800080',
  'pink': 'FFC0CB',
  'brown': '8B4513',
  'gray': '808080',
  'grey': '808080',
  // Filament-specific colors
  'jade white': 'FFFEF2',
  'ivory': 'FFFFF0',
  'beige': 'F5F5DC',
  'cream': 'FFFDD0',
  'silver': 'C0C0C0',
  'gold': 'FFD700',
  'bronze': 'CD7F32',
  'copper': 'B87333',
  'navy': '000080',
  'teal': '008080',
  'olive': '808000',
  'maroon': '800000',
  'coral': 'FF7F50',
  'salmon': 'FA8072',
  'lime': '32CD32',
  'mint': '98FF98',
  'forest green': '228B22',
  'sky blue': '87CEEB',
  'royal blue': '4169E1',
  'turquoise': '40E0D0',
  'lavender': 'E6E6FA',
  'violet': 'EE82EE',
  'plum': 'DDA0DD',
  'tan': 'D2B48C',
  'chocolate': 'D2691E',
  'charcoal': '36454F',
  'slate': '708090',
  'transparent': '000000', // Will need special handling
  'natural': 'F5F5DC',
  'wood': 'DEB887',
};

// Quick-select color presets (common filament colors)
// Basic colors shown by default
const QUICK_COLORS_BASIC = [
  { name: 'White', hex: 'FFFFFF' },
  { name: 'Black', hex: '000000' },
  { name: 'Red', hex: 'FF0000' },
  { name: 'Blue', hex: '0000FF' },
  { name: 'Green', hex: '00AA00' },
  { name: 'Yellow', hex: 'FFFF00' },
  { name: 'Orange', hex: 'FFA500' },
  { name: 'Gray', hex: '808080' },
];

// Extended colors shown when expanded
const QUICK_COLORS_EXTENDED = [
  { name: 'Cyan', hex: '00FFFF' },
  { name: 'Magenta', hex: 'FF00FF' },
  { name: 'Purple', hex: '800080' },
  { name: 'Pink', hex: 'FFC0CB' },
  { name: 'Brown', hex: '8B4513' },
  { name: 'Beige', hex: 'F5F5DC' },
  { name: 'Navy', hex: '000080' },
  { name: 'Teal', hex: '008080' },
  { name: 'Lime', hex: '32CD32' },
  { name: 'Gold', hex: 'FFD700' },
  { name: 'Silver', hex: 'C0C0C0' },
  { name: 'Maroon', hex: '800000' },
  { name: 'Olive', hex: '808000' },
  { name: 'Coral', hex: 'FF7F50' },
  { name: 'Salmon', hex: 'FA8072' },
  { name: 'Turquoise', hex: '40E0D0' },
  { name: 'Violet', hex: 'EE82EE' },
  { name: 'Indigo', hex: '4B0082' },
  { name: 'Chocolate', hex: 'D2691E' },
  { name: 'Tan', hex: 'D2B48C' },
  { name: 'Slate', hex: '708090' },
  { name: 'Charcoal', hex: '36454F' },
  { name: 'Ivory', hex: 'FFFFF0' },
  { name: 'Cream', hex: 'FFFDD0' },
];

// Try to convert color name to hex
function colorNameToHex(name: string): string | null {
  const normalized = name.toLowerCase().trim();
  return COLOR_NAME_MAP[normalized] || null;
}

export function ConfigureAmsSlotModal({
  isOpen,
  onClose,
  printerId,
  slotInfo,
  nozzleDiameter = '0.4',
  onSuccess,
}: ConfigureAmsSlotModalProps) {
  const { t } = useTranslation();
  const [selectedPresetId, setSelectedPresetId] = useState<string>('');
  const [selectedKProfile, setSelectedKProfile] = useState<KProfile | null>(null);
  const [colorHex, setColorHex] = useState<string>(''); // Just the 6-char hex, no alpha
  const [colorInput, setColorInput] = useState<string>(''); // User's text input (name or hex)
  const [searchQuery, setSearchQuery] = useState('');
  const [showSuccess, setShowSuccess] = useState(false);
  const [showExtendedColors, setShowExtendedColors] = useState(false);

  // Fetch cloud settings (gracefully handle 401 when logged out)
  const { data: cloudSettings, isLoading: settingsLoading, isError: cloudError } = useQuery({
    queryKey: ['cloudSettings'],
    queryFn: () => api.getCloudSettings(),
    enabled: isOpen,
    retry: false,
  });

  // Fetch local presets
  const { data: localPresets, isLoading: localLoading } = useQuery({
    queryKey: ['localPresets'],
    queryFn: () => api.getLocalPresets(),
    enabled: isOpen,
  });

  // Fetch built-in filament names (static fallback)
  const { data: builtinFilaments, isLoading: builtinLoading } = useQuery({
    queryKey: ['builtinFilaments'],
    queryFn: () => api.getBuiltinFilaments(),
    enabled: isOpen,
    staleTime: Infinity,
  });

  // Fetch K profiles
  const { data: kprofilesData, isLoading: kprofilesLoading } = useQuery({
    queryKey: ['kprofiles', printerId, nozzleDiameter],
    queryFn: () => api.getKProfiles(printerId, nozzleDiameter),
    enabled: isOpen && !!printerId,
  });

  // Configure slot mutation
  const configureMutation = useMutation({
    mutationFn: async () => {
      if (!selectedPresetId) throw new Error('No filament preset selected');

      // Determine preset source
      const isLocal = selectedPresetId.startsWith('local_');
      const isBuiltin = selectedPresetId.startsWith('builtin_');
      const localId = isLocal ? parseInt(selectedPresetId.replace('local_', ''), 10) : null;
      const builtinFilamentId = isBuiltin ? selectedPresetId.replace('builtin_', '') : null;
      const localPreset = isLocal
        ? localPresets?.filament.find(p => p.id === localId)
        : null;
      const builtinPreset = isBuiltin
        ? builtinFilaments?.find(b => b.filament_id === builtinFilamentId)
        : null;

      // Get the selected cloud preset details (null for local/builtin presets)
      const selectedPreset = (!isLocal && !isBuiltin)
        ? cloudSettings?.filament.find(p => p.setting_id === selectedPresetId)
        : null;

      if (!isLocal && !isBuiltin && !selectedPreset) throw new Error('Selected preset not found');
      if (isLocal && !localPreset) throw new Error('Selected local preset not found');
      if (isBuiltin && !builtinPreset) throw new Error('Selected builtin preset not found');

      // Parse the preset name for filament info
      const presetName = isLocal ? localPreset!.name : isBuiltin ? builtinPreset!.name : selectedPreset!.name;
      const parsed = parsePresetName(presetName);

      // Get cali_idx from selected K profile's slot_id (-1 = use default 0.020)
      const caliIdx = selectedKProfile?.slot_id ?? -1;

      // Use custom color if set, otherwise use current slot color or default
      const color = colorHex || slotInfo.trayColor?.slice(0, 6) || 'FFFFFF';

      // Create the tray_sub_brands from preset name (without printer/nozzle suffix)
      const traySubBrands = presetName.replace(/@.+$/, '').trim();

      let trayInfoIdx: string;
      let settingId: string;

      if (isLocal) {
        // Local presets have no Bambu Cloud mapping
        trayInfoIdx = '';
        settingId = '';
      } else if (isBuiltin) {
        // Built-in presets use the filament_id directly as tray_info_idx
        trayInfoIdx = builtinFilamentId!;
        settingId = '';
      } else {
        // Get tray_info_idx: for user presets, fetch detail to get filament_id or derive from base_id
        trayInfoIdx = convertToTrayInfoIdx(selectedPresetId);
        settingId = selectedPresetId;

        // For user presets (not starting with GF), fetch the detail to get the real filament_id
        if (!selectedPresetId.startsWith('GFS')) {
          try {
            const detail = await api.getCloudSettingDetail(selectedPresetId);
            if (detail.filament_id) {
              trayInfoIdx = detail.filament_id;
            } else if (detail.base_id) {
              trayInfoIdx = convertToTrayInfoIdx(detail.base_id);
              console.log(`Derived tray_info_idx from base_id: ${detail.base_id} -> ${trayInfoIdx}`);
            }
          } catch (e) {
            console.warn('Failed to fetch preset detail for filament_id:', e);
          }
        }
      }

      // Default temp range — use local preset core fields if available
      let tempMin = isLocal && localPreset?.nozzle_temp_min ? localPreset.nozzle_temp_min : 190;
      let tempMax = isLocal && localPreset?.nozzle_temp_max ? localPreset.nozzle_temp_max : 230;

      if (!isLocal || isBuiltin || (!localPreset?.nozzle_temp_min && !localPreset?.nozzle_temp_max)) {
        // Fall back to material-based defaults
        const material = (isLocal ? (localPreset?.filament_type || parsed.material) : parsed.material).toUpperCase();
        if (material.includes('PLA')) {
          tempMin = 190;
          tempMax = 230;
        } else if (material.includes('PETG')) {
          tempMin = 220;
          tempMax = 260;
        } else if (material.includes('ABS')) {
          tempMin = 240;
          tempMax = 280;
        } else if (material.includes('ASA')) {
          tempMin = 240;
          tempMax = 280;
        } else if (material.includes('TPU')) {
        tempMin = 200;
        tempMax = 240;
      } else if (material.includes('PC')) {
        tempMin = 260;
        tempMax = 300;
      } else if (material.includes('PA') || material.includes('NYLON')) {
          tempMin = 250;
          tempMax = 290;
        }
      }

      // Parse K value from selected profile
      const kValue = selectedKProfile?.k_value ? parseFloat(selectedKProfile.k_value) : 0;

      // Determine tray_type: use local preset's filament_type or parsed material
      const trayType = isLocal
        ? (localPreset?.filament_type || parsed.material || 'PLA')
        : (parsed.material || 'PLA');

      // Configure the slot via MQTT
      const result = await api.configureAmsSlot(printerId, slotInfo.amsId, slotInfo.trayId, {
        tray_info_idx: trayInfoIdx,
        tray_type: trayType,
        tray_sub_brands: traySubBrands,
        tray_color: color + 'FF', // Add alpha
        nozzle_temp_min: tempMin,
        nozzle_temp_max: tempMax,
        cali_idx: caliIdx,
        nozzle_diameter: nozzleDiameter,
        setting_id: settingId, // Full setting ID for slicer compatibility (empty for local)
        // Pass K profile's filament_id and setting_id for proper linking
        kprofile_filament_id: selectedKProfile?.filament_id,
        kprofile_setting_id: selectedKProfile?.setting_id || undefined,
        // Also pass the K value directly for extrusion_cali_set command
        k_value: kValue,
      });

      // Save the preset mapping so we can display the correct name in the UI
      // This is needed because user presets use filament_id (e.g., P285e239) as tray_info_idx,
      // which can't be resolved to a name via the filamentInfo API
      const mappingPresetId = isLocal ? `local_${localId}` : isBuiltin ? `builtin_${builtinFilamentId}` : selectedPresetId;
      const mappingSource = isLocal ? 'local' : isBuiltin ? 'builtin' : 'cloud';
      try {
        await api.saveSlotPreset(printerId, slotInfo.amsId, slotInfo.trayId, mappingPresetId, traySubBrands, mappingSource);
      } catch (e) {
        console.warn('Failed to save slot preset mapping:', e);
        // Don't fail the whole operation - slot was configured successfully
      }

      return result;
    },
    onSuccess: () => {
      setShowSuccess(true);
      onSuccess?.();
      // Close after showing success briefly
      setTimeout(() => {
        setShowSuccess(false);
        onClose();
      }, 1500);
    },
  });

  // Reset slot mutation
  const resetMutation = useMutation({
    mutationFn: async () => {
      return api.resetAmsSlot(printerId, slotInfo.amsId, slotInfo.trayId);
    },
    onSuccess: () => {
      setShowSuccess(true);
      onSuccess?.();
      setTimeout(() => {
        setShowSuccess(false);
        onClose();
      }, 1500);
    },
  });

  // Unified preset item for the list (cloud + local + builtin fallback)
  type PresetItem = { id: string; name: string; source: 'cloud' | 'local' | 'builtin'; isUser: boolean };

  // Filter filament presets based on search (merged cloud + local + builtin)
  const filteredPresets = useMemo(() => {
    const query = searchQuery.toLowerCase();
    const items: PresetItem[] = [];

    // Collect IDs already covered by cloud and local to avoid duplicates in fallback
    const coveredIds = new Set<string>();

    // 1. Cloud presets
    if (cloudSettings?.filament) {
      for (const cp of cloudSettings.filament) {
        coveredIds.add(cp.setting_id);
        if (!query || cp.name.toLowerCase().includes(query)) {
          items.push({ id: cp.setting_id, name: cp.name, source: 'cloud', isUser: isUserPreset(cp.setting_id) });
        }
      }
    }

    // 2. Local presets
    if (localPresets?.filament) {
      for (const lp of localPresets.filament) {
        if (!query || lp.name.toLowerCase().includes(query)) {
          items.push({ id: `local_${lp.id}`, name: lp.name, source: 'local', isUser: false });
        }
      }
    }

    // 3. Built-in filament names (fallback — only add entries not already covered)
    if (builtinFilaments) {
      for (const bf of builtinFilaments) {
        if (coveredIds.has(bf.filament_id)) continue;
        // Convert filament_id to setting_id format for cloud compatibility
        // e.g. "GFA00" → cloud setting_id would be "GFSA00" (insert S after GF)
        const settingId = bf.filament_id.startsWith('GF')
          ? 'GFS' + bf.filament_id.slice(2)
          : bf.filament_id;
        if (coveredIds.has(settingId)) continue;
        if (!query || bf.name.toLowerCase().includes(query)) {
          items.push({ id: `builtin_${bf.filament_id}`, name: bf.name, source: 'builtin', isUser: false });
        }
      }
    }

    // Sort: cloud user presets first, then cloud built-in, then local, then builtin fallback
    return items.sort((a, b) => {
      const sourceOrder = { cloud: 0, local: 1, builtin: 2 };
      if (a.source !== b.source) return sourceOrder[a.source] - sourceOrder[b.source];
      if (a.isUser && !b.isUser) return -1;
      if (!a.isUser && b.isUser) return 1;
      return a.name.localeCompare(b.name);
    });
  }, [cloudSettings?.filament, localPresets?.filament, builtinFilaments, searchQuery]);

  // Get full preset name for K profile filtering (brand + material, without printer suffix)
  const selectedPresetInfo = useMemo(() => {
    if (!selectedPresetId) return null;

    // Resolve the name from cloud, local, or builtin presets
    let presetName: string | null = null;
    if (selectedPresetId.startsWith('local_')) {
      const localId = parseInt(selectedPresetId.replace('local_', ''), 10);
      const lp = localPresets?.filament.find(p => p.id === localId);
      presetName = lp?.name || null;
    } else if (selectedPresetId.startsWith('builtin_')) {
      const filamentId = selectedPresetId.replace('builtin_', '');
      const bf = builtinFilaments?.find(b => b.filament_id === filamentId);
      presetName = bf?.name || null;
    } else if (cloudSettings?.filament) {
      const cp = cloudSettings.filament.find(p => p.setting_id === selectedPresetId);
      presetName = cp?.name || null;
    }
    if (!presetName) return null;

    // Remove printer/nozzle suffix (e.g., "@BBL X1C" or "@0.4 nozzle")
    let nameWithoutSuffix = presetName.replace(/@.+$/, '').trim();
    // Strip leading "# " from custom preset names (user convention)
    if (nameWithoutSuffix.startsWith('# ')) {
      nameWithoutSuffix = nameWithoutSuffix.slice(2).trim();
    }
    const parsed = parsePresetName(nameWithoutSuffix);

    return {
      fullName: nameWithoutSuffix,
      material: parsed.material,
      brand: parsed.brand,
    };
  }, [selectedPresetId, cloudSettings?.filament, localPresets?.filament, builtinFilaments]);

  // For backwards compatibility with the label
  const selectedMaterial = selectedPresetInfo?.fullName || '';

  const matchingKProfiles = useMemo(() => {
    if (!kprofilesData?.profiles || !selectedPresetInfo) return [];

    const { fullName, material, brand } = selectedPresetInfo;
    const upperFullName = fullName.toUpperCase();
    const upperMaterial = material.toUpperCase();
    const upperBrand = brand.toUpperCase();

    // Material must be at least 2 chars to avoid false positives
    if (!upperMaterial || upperMaterial.length < 2) return [];

    // Filter profiles - require brand match if brand is present in selected preset
    const filtered = kprofilesData.profiles.filter(p => {
      const profileName = p.name.toUpperCase();

      // If the selected preset has a brand (e.g., "Azurefilm PLA Wood"),
      // only show profiles that match the brand
      if (upperBrand) {
        // Must contain the brand name
        if (!profileName.includes(upperBrand)) {
          return false;
        }
        // And must contain the material type
        if (!profileName.includes(upperMaterial)) {
          return false;
        }
        return true;
      }

      // No brand in selected preset - match on full name or material
      // Priority 1: Exact match with full name
      if (profileName.includes(upperFullName)) {
        return true;
      }

      // Priority 2: Material type match (only when no brand specified)
      if (profileName.includes(upperMaterial)) {
        return true;
      }

      // Check for common material aliases
      const aliases: Record<string, string[]> = {
        'NYLON': ['PA', 'PA-CF', 'PA6'],
        'PA': ['NYLON'],
      };

      const materialAliases = aliases[upperMaterial] || [];
      for (const alias of materialAliases) {
        if (profileName.includes(alias)) {
          return true;
        }
      }

      return false;
    });

    // Deduplicate profiles with same name and k_value (multi-nozzle printers have duplicates)
    // Prefer extruder_id=1 (High Flow) profiles as they're more commonly used on H2D
    const seen = new Map<string, KProfile>();
    for (const profile of filtered) {
      const key = `${profile.name}|${profile.k_value}`;
      const existing = seen.get(key);
      if (!existing) {
        seen.set(key, profile);
      } else if (profile.extruder_id === 1 && existing.extruder_id === 0) {
        // Replace extruder_id=0 profile with extruder_id=1 (High Flow) profile
        seen.set(key, profile);
      }
    }
    return Array.from(seen.values());
  }, [kprofilesData?.profiles, selectedPresetInfo]);

  // Pre-select current profile when modal opens, reset when closes
  useEffect(() => {
    if (isOpen && cloudSettings?.filament) {
      // Try to pre-select current profile based on trayInfoIdx
      if (slotInfo.trayInfoIdx) {
        const currentPreset = cloudSettings.filament.find(
          p => p.setting_id === slotInfo.trayInfoIdx
        );
        if (currentPreset) {
          setSelectedPresetId(currentPreset.setting_id);
        }
      }
    } else if (!isOpen) {
      // Reset when modal closes
      setSelectedPresetId('');
      setSelectedKProfile(null);
      setColorHex('');
      setColorInput('');
      setSearchQuery('');
      setShowSuccess(false);
    }
  }, [isOpen, cloudSettings?.filament, slotInfo.trayInfoIdx]);

  // Auto-select best matching K profile when preset changes
  useEffect(() => {
    if (matchingKProfiles.length > 0) {
      // Auto-select first matching profile
      setSelectedKProfile(matchingKProfiles[0]);
    } else {
      setSelectedKProfile(null);
    }
  }, [selectedPresetId, matchingKProfiles]);

  // Escape key handler
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      onClose();
    }
  }, [onClose]);

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      return () => document.removeEventListener('keydown', handleKeyDown);
    }
  }, [isOpen, handleKeyDown]);

  if (!isOpen) return null;

  const isLoading = (settingsLoading && !cloudError) || localLoading || builtinLoading || kprofilesLoading;
  const canSave = selectedPresetId && !configureMutation.isPending;

  // Get display color (custom or slot default)
  const displayColor = colorHex || slotInfo.trayColor?.slice(0, 6) || 'FFFFFF';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-lg mx-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <Settings2 className="w-5 h-5 text-bambu-blue" />
            <h2 className="text-lg font-semibold text-white">Configure AMS Slot</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-bambu-gray hover:text-white rounded transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 space-y-4 max-h-[60vh] overflow-y-auto">
          {/* Success overlay */}
          {showSuccess && (
            <div className="absolute inset-0 bg-bambu-dark-secondary/95 z-10 flex items-center justify-center rounded-xl">
              <div className="text-center space-y-3">
                <CheckCircle2 className="w-16 h-16 text-bambu-green mx-auto" />
                <p className="text-lg font-semibold text-white">Slot Configured!</p>
                <p className="text-sm text-bambu-gray">{t('configureAmsSlot.settingsSentToPrinter')}</p>
              </div>
            </div>
          )}

          {/* Slot info */}
          <div className="p-3 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
            <p className="text-xs text-bambu-gray mb-1">Configuring slot:</p>
            <div className="flex items-center gap-2">
              {slotInfo.trayColor && (
                <span
                  className="w-4 h-4 rounded-full border border-white/20"
                  style={{ backgroundColor: `#${slotInfo.trayColor.slice(0, 6)}` }}
                />
              )}
              <span className="text-white font-medium">
                {getAmsLabel(slotInfo.amsId, slotInfo.trayCount)} Slot {slotInfo.trayId + 1}
              </span>
              {slotInfo.traySubBrands && (
                <span className="text-bambu-gray">({slotInfo.traySubBrands})</span>
              )}
            </div>
          </div>

          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
            </div>
          ) : (
            <>
              {/* Filament Profile Select */}
              <div>
                <label className="block text-sm text-bambu-gray mb-2">
                  Filament Profile <span className="text-red-400">*</span>
                </label>
                <div className="relative">
                  <input
                    type="text"
                    placeholder={t('configureAmsSlot.searchPresets')}
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder:text-bambu-gray focus:border-bambu-green focus:outline-none mb-2"
                  />
                  <div className="max-h-48 overflow-y-auto space-y-1">
                    {filteredPresets.length === 0 ? (
                      <p className="text-center py-4 text-bambu-gray">
                        {(cloudSettings?.filament?.length === 0 && !localPresets?.filament?.length)
                          ? 'No presets available. Login to Bambu Cloud or import local profiles.'
                          : 'No matching presets found.'}
                      </p>
                    ) : (
                      filteredPresets.map((preset) => (
                        <button
                          key={preset.id}
                          onClick={() => setSelectedPresetId(preset.id)}
                          className={`w-full p-2 rounded-lg border text-left transition-colors ${
                            selectedPresetId === preset.id
                              ? 'bg-bambu-green/20 border-bambu-green'
                              : 'bg-bambu-dark border-bambu-dark-tertiary hover:border-bambu-gray'
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-white text-sm truncate">{preset.name}</span>
                            <div className="flex items-center gap-1 flex-shrink-0">
                              {preset.source === 'local' && (
                                <span className="text-xs px-1.5 py-0.5 rounded bg-green-500/20 text-green-400">
                                  {t('profiles.localProfiles.badge')}
                                </span>
                              )}
                              {preset.source === 'builtin' && (
                                <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400">
                                  {t('configureAmsSlot.builtin')}
                                </span>
                              )}
                              {preset.isUser && (
                                <span className="text-xs px-1.5 py-0.5 rounded bg-bambu-blue/20 text-bambu-blue">
                                  {t('configureAmsSlot.custom')}
                                </span>
                              )}
                            </div>
                          </div>
                        </button>
                      ))
                    )}
                  </div>
                </div>
              </div>

              {/* K Profile Select */}
              <div>
                <label className="block text-sm text-bambu-gray mb-2">
                  K Profile (Pressure Advance)
                  {selectedMaterial && (
                    <span className="ml-2 text-xs text-bambu-blue">
                      Filtering for: {selectedMaterial}
                    </span>
                  )}
                </label>
                {matchingKProfiles.length > 0 ? (
                  <div className="relative">
                    <select
                      value={selectedKProfile?.name || ''}
                      onChange={(e) => {
                        const profile = matchingKProfiles.find(p => p.name === e.target.value);
                        setSelectedKProfile(profile || null);
                      }}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none pr-10"
                    >
                      <option value="">No K profile (use default 0.020)</option>
                      {matchingKProfiles.map((profile) => (
                        <option key={`${profile.name}-${profile.extruder_id}`} value={profile.name}>
                          {profile.name} (K={profile.k_value})
                        </option>
                      ))}
                    </select>
                    <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                  </div>
                ) : selectedPresetId ? (
                  <p className="text-sm text-bambu-gray italic py-2">
                    No matching K profiles found. Default K=0.020 will be used.
                  </p>
                ) : (
                  <span className="inline-block text-xs px-2 py-1 rounded bg-amber-500/20 text-amber-400 border border-amber-500/30">
                    Select a filament profile first
                  </span>
                )}
                {selectedKProfile && (
                  <p className="text-xs text-bambu-green mt-1">
                    K={selectedKProfile.k_value} from printer calibration
                  </p>
                )}
              </div>

              {/* Optional: Custom color */}
              <div>
                <label className="block text-sm text-bambu-gray mb-2">
                  Custom Color (optional)
                </label>
                {/* Quick color buttons */}
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {QUICK_COLORS_BASIC.map((color) => (
                    <button
                      key={color.hex}
                      onClick={() => {
                        setColorHex(color.hex);
                        setColorInput(color.name);
                      }}
                      className={`w-7 h-7 rounded-md border-2 transition-all ${
                        colorHex === color.hex
                          ? 'border-bambu-green scale-110'
                          : 'border-white/20 hover:border-white/40'
                      }`}
                      style={{ backgroundColor: `#${color.hex}` }}
                      title={color.name}
                    />
                  ))}
                  <button
                    onClick={() => setShowExtendedColors(!showExtendedColors)}
                    className="w-7 h-7 rounded-md border-2 border-white/20 hover:border-white/40 flex items-center justify-center text-white/60 hover:text-white/80 transition-all text-xs"
                    title={showExtendedColors ? 'Show less colors' : 'Show more colors'}
                  >
                    {showExtendedColors ? '−' : '+'}
                  </button>
                </div>
                {/* Extended colors (collapsible) */}
                {showExtendedColors && (
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {QUICK_COLORS_EXTENDED.map((color) => (
                      <button
                        key={color.hex}
                        onClick={() => {
                          setColorHex(color.hex);
                          setColorInput(color.name);
                        }}
                        className={`w-7 h-7 rounded-md border-2 transition-all ${
                          colorHex === color.hex
                            ? 'border-bambu-green scale-110'
                            : 'border-white/20 hover:border-white/40'
                        }`}
                        style={{ backgroundColor: `#${color.hex}` }}
                        title={color.name}
                      />
                    ))}
                  </div>
                )}
                {/* Color input: name or hex */}
                <div className="flex gap-2 items-center">
                  <div
                    className="w-10 h-10 rounded-lg border-2 border-white/20 flex-shrink-0"
                    style={{ backgroundColor: `#${displayColor}` }}
                  />
                  <input
                    type="text"
                    placeholder={t('configureAmsSlot.colorPlaceholder')}
                    value={colorInput}
                    onChange={(e) => {
                      const input = e.target.value;
                      setColorInput(input);

                      // Try to parse as color name first
                      const nameHex = colorNameToHex(input);
                      if (nameHex) {
                        setColorHex(nameHex);
                      } else {
                        // Try to parse as hex code
                        const cleaned = input.replace(/[^0-9A-Fa-f]/g, '').toUpperCase();
                        if (cleaned.length === 6) {
                          setColorHex(cleaned);
                        } else if (cleaned.length === 3) {
                          // Expand shorthand hex (e.g., F00 -> FF0000)
                          setColorHex(cleaned.split('').map(c => c + c).join(''));
                        }
                      }
                    }}
                    className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder:text-bambu-gray focus:border-bambu-green focus:outline-none text-sm"
                  />
                  {colorHex && (
                    <button
                      onClick={() => {
                        setColorHex('');
                        setColorInput('');
                      }}
                      className="px-2 py-1 text-xs text-bambu-gray hover:text-white bg-bambu-dark-tertiary rounded"
                      title={t('configureAmsSlot.clearCustomColor')}
                    >
                      Clear
                    </button>
                  )}
                </div>
                {colorHex && (
                  <p className="text-xs text-bambu-gray mt-1.5">
                    Hex: #{colorHex}
                  </p>
                )}
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-between p-4 border-t border-bambu-dark-tertiary">
          {/* Reset button on the left */}
          <Button
            variant="secondary"
            onClick={() => resetMutation.mutate()}
            disabled={resetMutation.isPending || configureMutation.isPending}
            className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
          >
            {resetMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Resetting...
              </>
            ) : (
              <>
                <RotateCcw className="w-4 h-4" />
                Reset Slot
              </>
            )}
          </Button>
          {/* Cancel and Configure buttons on the right */}
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button
              onClick={() => configureMutation.mutate()}
              disabled={!canSave}
            >
              {configureMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Configuring...
                </>
              ) : (
                <>
                  <Settings2 className="w-4 h-4" />
                  Configure Slot
                </>
              )}
            </Button>
          </div>
        </div>

        {/* Error */}
        {(configureMutation.isError || resetMutation.isError) && (
          <div className="mx-4 mb-4 p-2 bg-red-500/20 border border-red-500/50 rounded text-sm text-red-400">
            {(configureMutation.error as Error)?.message || (resetMutation.error as Error)?.message}
          </div>
        )}
      </div>
    </div>
  );
}
