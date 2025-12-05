import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { X, ExternalLink } from 'lucide-react';
import { api } from '../../api/client';
import type { AMSTray, KProfile } from '../../api/client';

interface AMSMaterialsModalProps {
  tray: AMSTray;
  amsId: number;  // AMS unit ID (0, 1, 2, 3)
  slotLabel: string;
  printerId: number;
  printerModel: string;  // e.g., "H2D", "X1C", "P1S"
  nozzleDiameter?: string;
  extruderId?: number;  // 0=right nozzle, 1=left nozzle (for filtering K-profiles)
  onClose: () => void;
  onConfirm?: (data: MaterialSettings) => void;
}

// Extract base filament name (without printer/nozzle suffix)
// e.g., "# Bambu PLA Basic @BBL H2D" -> "# Bambu PLA Basic"
// e.g., "Devil Design PLA Basic @Bambu Lab H2D 0.4 nozzle" -> "Devil Design PLA Basic"
function getBaseFilamentName(name: string): string {
  const atIndex = name.indexOf(' @');
  return atIndex > 0 ? name.substring(0, atIndex) : name;
}

// Get normalized name for deduplication (without # prefix and @ suffix)
// e.g., "# Bambu PLA Basic @BBL H2D" -> "Bambu PLA Basic"
// e.g., "Bambu PLA Basic @BBL X1C" -> "Bambu PLA Basic"
function getNormalizedName(name: string): string {
  let normalized = name;
  if (normalized.startsWith('# ')) {
    normalized = normalized.substring(2);
  }
  const atIndex = normalized.indexOf(' @');
  return atIndex > 0 ? normalized.substring(0, atIndex) : normalized;
}

// Get clean name for K-profile matching (without # prefix and @ suffix)
function getCleanFilamentName(name: string): string {
  let cleanName = name;
  if (cleanName.startsWith('# ')) {
    cleanName = cleanName.substring(2);
  }
  const atIndex = cleanName.indexOf(' @');
  return atIndex > 0 ? cleanName.substring(0, atIndex) : cleanName;
}

export interface MaterialSettings {
  filamentType: string;
  color: string;
  nozzleTempMax: number;
  nozzleTempMin: number;
  kProfile: string | null;
  kValue: number;
}

function hexToRgb(hex: string | null): string {
  if (!hex) return 'rgb(128, 128, 128)';
  const cleanHex = hex.replace('#', '').substring(0, 6);
  const rParsed = parseInt(cleanHex.substring(0, 2), 16);
  const gParsed = parseInt(cleanHex.substring(2, 4), 16);
  const bParsed = parseInt(cleanHex.substring(4, 6), 16);
  const r = isNaN(rParsed) ? 128 : rParsed;
  const g = isNaN(gParsed) ? 128 : gParsed;
  const b = isNaN(bParsed) ? 128 : bParsed;
  return `rgb(${r}, ${g}, ${b})`;
}

function trayColorToHex(trayColor: string | null): string {
  if (!trayColor) return '#808080';
  // Tray color comes as RRGGBBAA, we need #RRGGBB
  const cleanHex = trayColor.replace('#', '').substring(0, 6);
  return `#${cleanHex}`;
}

function hexToTrayColor(hex: string): string {
  // Convert #RRGGBB to RRGGBBFF (with full opacity)
  const cleanHex = hex.replace('#', '').toUpperCase();
  return cleanHex.length === 6 ? `${cleanHex}FF` : `${cleanHex.substring(0, 6)}FF`;
}

// Check if tray has valid Bambu Lab UUID (32-char hex)
function isBambuLabSpool(trayUuid: string | null): boolean {
  if (!trayUuid) return false;
  const uuid = trayUuid.trim();
  if (uuid.length !== 32) return false;
  if (uuid === '00000000000000000000000000000000') return false;
  return /^[0-9a-fA-F]{32}$/.test(uuid);
}

// Bambu Lab color codes from tray_id_name (e.g., "A00-Y2" -> "Sunflower Yellow")
const BAMBU_COLOR_CODES: Record<string, string> = {
  'Y2': 'Sunflower Yellow', 'Y0': 'Yellow', 'Y1': 'Lemon Yellow',
  'K0': 'Black', 'W0': 'White', 'W1': 'Ivory White',
  'R0': 'Red', 'R1': 'Scarlet Red', 'R2': 'Magenta',
  'B0': 'Blue', 'B1': 'Navy Blue', 'B2': 'Sky Blue', 'B3': 'Cyan',
  'G0': 'Green', 'G1': 'Grass Green', 'G2': 'Jade Green',
  'O0': 'Orange', 'O1': 'Mandarin Orange',
  'P0': 'Purple', 'P1': 'Pink', 'P2': 'Sakura Pink',
  'N0': 'Gray', 'N1': 'Silver Gray', 'N2': 'Charcoal',
  'D0': 'Brown', 'D1': 'Chocolate',
  'T0': 'Titan Gray', 'T1': 'Jade White',
};

function getColorNameFromTrayId(trayIdName: string | null): string | null {
  if (!trayIdName) return null;
  // tray_id_name format: "A00-Y2" or "G02-K0" - color code is after the dash
  const parts = trayIdName.split('-');
  if (parts.length < 2) return null;
  const colorCode = parts[1];
  return BAMBU_COLOR_CODES[colorCode] || null;
}

// Find best matching K-profile for a filament
// Profiles have names like "HF Bambu PLA Basic Sunflower Yellow", "High Flow_Bambu PLA Basic"
// tray_sub_brands is like "PLA Basic", "PETG HF"
// tray_type is like "PLA", "PETG"
// colorName is like "Sunflower Yellow"
function findBestKProfile(
  profiles: KProfile[],
  traySubBrands: string | null,
  trayType: string | null,
  colorName: string | null
): KProfile | null {
  if (!profiles.length) return null;

  const subBrands = traySubBrands?.toLowerCase() || '';
  const type = trayType?.toUpperCase() || '';
  const color = colorName?.toLowerCase() || '';

  // Priority 1: Match tray_sub_brands AND color (e.g., "PLA Basic" + "Sunflower Yellow")
  if (subBrands && color) {
    const exactColorMatch = profiles.find(p => {
      const name = p.name.toLowerCase();
      return name.includes(subBrands) && name.includes(color);
    });
    if (exactColorMatch) return exactColorMatch;
  }

  // Priority 2: Match tray_sub_brands without color (e.g., "PLA Basic" in "High Flow_Bambu PLA Basic")
  if (subBrands) {
    const subBrandsMatch = profiles.find(p =>
      p.name.toLowerCase().includes(subBrands)
    );
    if (subBrandsMatch) return subBrandsMatch;
  }

  // Priority 3: Match filament type in profile name (e.g., "PLA" in "High Flow_Bambu PLA Basic")
  if (type) {
    const typeMatches = profiles.filter(p =>
      p.name.toUpperCase().includes(type)
    );

    if (typeMatches.length > 0) {
      // Prefer "Basic" profiles for generic type matching
      const basicMatch = typeMatches.find(p =>
        p.name.toLowerCase().includes('basic')
      );
      if (basicMatch) return basicMatch;

      return typeMatches[0];
    }
  }

  // Priority 4: Default profile
  const defaultProfile = profiles.find(p =>
    p.name.toLowerCase() === 'default' || p.slot_id === 0
  );
  return defaultProfile || null;
}

export function AMSMaterialsModal({
  tray,
  amsId,
  slotLabel,
  printerId,
  printerModel,
  nozzleDiameter = '0.4',
  extruderId = 0,
  onClose,
  onConfirm,
}: AMSMaterialsModalProps) {
  const queryClient = useQueryClient();

  // Determine slot type
  const isEmpty = !tray.tray_type || tray.tray_type === '' || tray.tray_type === 'NONE';
  const isBambuSpool = isBambuLabSpool(tray.tray_uuid);
  const isEditable = isEmpty || !isBambuSpool;

  // Fetch K-profiles
  const { data: kProfilesData } = useQuery({
    queryKey: ['kprofiles', printerId, nozzleDiameter],
    queryFn: () => api.getKProfiles(printerId, nozzleDiameter),
  });

  // Fetch cloud filament presets
  const { data: cloudSettings } = useQuery({
    queryKey: ['cloudSettings'],
    queryFn: () => api.getCloudSettings(),
  });

  // Fetch saved slot preset mapping
  const { data: savedPreset } = useQuery({
    queryKey: ['slotPreset', printerId, amsId, tray.id],
    queryFn: () => api.getSlotPreset(printerId, amsId, tray.id),
    enabled: isEditable,  // Only fetch for editable (non-Bambu) slots
  });

  // Mutation for saving preset mapping
  const savePresetMutation = useMutation({
    mutationFn: ({ presetId, presetName }: { presetId: string; presetName: string }) =>
      api.saveSlotPreset(printerId, amsId, tray.id, presetId, presetName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['slotPreset', printerId, amsId, tray.id] });
    },
  });

  // Mutation for saving filament settings to printer (including K value)
  const saveFilamentSettingMutation = useMutation({
    mutationFn: (data: {
      ams_id: number;
      tray_id: number;
      tray_info_idx: string;
      tray_type: string;
      tray_sub_brands: string;
      tray_color: string;
      nozzle_temp_min: number;
      nozzle_temp_max: number;
      k: number;
    }) => api.amsSetFilamentSetting(printerId, data),
    onSuccess: (result) => {
      console.log('[AMSMaterialsModal] saveFilamentSettingMutation SUCCESS:', result);
    },
    onError: (error) => {
      console.error('[AMSMaterialsModal] saveFilamentSettingMutation ERROR:', error);
    },
  });

  // Get filament presets from cloud settings:
  // 1. Filter out presets starting with "#" (experimental/special presets)
  // 2. Prioritize current printer model, then deduplicate by base name
  // 3. Sort alphabetically
  const filamentPresets = (() => {
    const allPresets = cloudSettings?.filament || [];

    // Filter out presets starting with "#" (experimental/special presets)
    const filteredPresets = allPresets.filter(p => !p.name.startsWith('# '));

    // Sort presets: current printer model first, then others
    const modelPattern = new RegExp(`@.*\\b${printerModel}\\b`, 'i');
    const sortedByModel = [...filteredPresets].sort((a, b) => {
      const aMatches = modelPattern.test(a.name);
      const bMatches = modelPattern.test(b.name);
      if (aMatches && !bMatches) return -1;
      if (!aMatches && bMatches) return 1;
      return 0;
    });

    // Remove duplicates - keep first occurrence of each normalized name
    // (presets for current printer model will be kept due to sorting above)
    // Uses normalized name (without # prefix) so "# Bambu ABS" and "Bambu ABS" are treated as same
    const seenNormalizedNames = new Set<string>();
    const unique = sortedByModel.filter(p => {
      const normalizedName = getNormalizedName(p.name);
      if (seenNormalizedNames.has(normalizedName)) return false;
      seenNormalizedNames.add(normalizedName);
      return true;
    });

    // Sort by base name alphabetically
    const sorted = unique.sort((a, b) =>
      getBaseFilamentName(a.name).localeCompare(getBaseFilamentName(b.name))
    );

    return sorted;
  })();

  // State
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const [selectedPresetName, setSelectedPresetName] = useState<string | null>(null);
  const [selectedFilamentType, setSelectedFilamentType] = useState(tray.tray_type || '');
  const [selectedColor, setSelectedColor] = useState(trayColorToHex(tray.tray_color));
  const [nozzleTempMax, setNozzleTempMax] = useState(tray.nozzle_temp_max || 220);
  const [nozzleTempMin, setNozzleTempMin] = useState(tray.nozzle_temp_min || 190);
  const [selectedKProfile, setSelectedKProfile] = useState<string>('');
  const [kValue, setKValue] = useState(tray.k || 0);

  // Load saved preset when data is available
  useEffect(() => {
    if (savedPreset && !selectedPresetId) {
      setSelectedPresetId(savedPreset.preset_id);
      setSelectedPresetName(savedPreset.preset_name);
    }
  }, [savedPreset, selectedPresetId]);

  // Handle filament preset selection from dropdown
  const handlePresetSelect = (settingId: string) => {
    if (!settingId) {
      setSelectedPresetId(null);
      setSelectedPresetName(null);
      return;
    }
    setSelectedPresetId(settingId);
    const preset = filamentPresets.find(f => f.setting_id === settingId);
    if (preset) {
      setSelectedPresetName(preset.name);
      // Extract filament type from preset name (e.g., "Devil Design PLA Basic @BBL X1C" -> look for PLA, PETG, etc.)
      const typeMatch = preset.name.match(/\b(PLA|PETG|ABS|TPU|ASA|PA|PC|PVA|HIPS|PLA-S|PETG-CF|PA-CF|PET-CF)\b/i);
      if (typeMatch) {
        setSelectedFilamentType(typeMatch[1].toUpperCase());
      }
    }
  };

  // Find best K-profile when data loads or filament type changes
  // First filter all profiles by extruder ID (for dual-nozzle printers)
  const allProfiles = kProfilesData?.profiles || [];
  const profiles = allProfiles.filter(p => p.extruder_id === extruderId);

  // Filter profiles to show only matching ones in dropdown
  const getMatchingProfiles = (): KProfile[] => {
    if (!profiles.length) return [];

    const subBrands = isBambuSpool
      ? tray.tray_sub_brands?.toLowerCase()
      : (selectedPresetName ? getCleanFilamentName(selectedPresetName).toLowerCase() : null);
    const type = isBambuSpool
      ? tray.tray_type?.toUpperCase()
      : (selectedFilamentType?.toUpperCase() || tray.tray_type?.toUpperCase());

    // If we have tray_sub_brands, only show profiles matching that (not generic type)
    // For Bambu spools, also require "bambu" in the profile name to exclude third-party profiles
    if (subBrands) {
      const matches = profiles.filter(p => p.name.toLowerCase().includes(subBrands));
      if (isBambuSpool) {
        return matches.filter(p => p.name.toLowerCase().includes('bambu'));
      }
      return matches;
    }

    // Only fall back to type matching if no sub_brands available
    if (type) {
      return profiles.filter(p => p.name.toUpperCase().includes(type));
    }

    return [];
  };

  const matchingProfiles = getMatchingProfiles();

  // Get color name from Bambu tray_id_name (e.g., "A00-Y2" -> "Sunflower Yellow")
  const bambuColorName = getColorNameFromTrayId(tray.tray_id_name);

  useEffect(() => {
    // First, try to find a profile matching the tray's current K value (from printer)
    // This preserves the user's selection when reopening the modal
    const currentK = tray.k;
    if (currentK && currentK > 0) {
      const matchingKProfile = matchingProfiles.find(p => {
        const profileK = parseFloat(p.k_value);
        return Math.abs(profileK - currentK) < 0.0001; // Allow small floating point tolerance
      });
      if (matchingKProfile) {
        setSelectedKProfile(matchingKProfile.name);
        setKValue(currentK);
        return;
      }
    }

    // Fall back to name-based matching if no K-value match found
    // For Bambu spools, use tray_sub_brands (e.g., "PLA Basic") and color name
    // For non-Bambu, use clean name from preset (e.g., "Bambu PLA Basic" without #) for K-profile matching
    const subBrands = isBambuSpool
      ? tray.tray_sub_brands
      : (selectedPresetName ? getCleanFilamentName(selectedPresetName) : null);
    const type = isBambuSpool ? tray.tray_type : (selectedFilamentType || tray.tray_type);
    const colorName = isBambuSpool ? bambuColorName : null;

    const bestProfile = findBestKProfile(profiles, subBrands, type, colorName);

    if (bestProfile) {
      setSelectedKProfile(bestProfile.name);
      setKValue(parseFloat(bestProfile.k_value) || 0);
    } else {
      setSelectedKProfile('Default');
      setKValue(0);
    }
  }, [profiles, matchingProfiles, selectedFilamentType, selectedPresetName, tray.tray_type, tray.tray_sub_brands, tray.tray_id_name, tray.k, isBambuSpool, bambuColorName]);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const handleKProfileChange = (profileName: string) => {
    setSelectedKProfile(profileName);
    const profile = profiles.find(p => p.name === profileName);
    if (profile) {
      setKValue(parseFloat(profile.k_value) || 0);
    }
  };

  const handleConfirm = () => {
    console.log('[AMSMaterialsModal] handleConfirm called');
    console.log('[AMSMaterialsModal] amsId:', amsId, 'tray.id:', tray.id, 'kValue:', kValue);

    // Save preset mapping for non-Bambu slots
    if (isEditable && selectedPresetId && selectedPresetName) {
      console.log('[AMSMaterialsModal] Saving preset mapping');
      savePresetMutation.mutate({
        presetId: selectedPresetId,
        presetName: selectedPresetName,
      });
    }

    // Send filament settings to printer (including K value)
    // Use current tray data for Bambu spools, or edited values for non-Bambu
    const trayColor = isBambuSpool
      ? (tray.tray_color || '808080FF')
      : hexToTrayColor(selectedColor);
    const trayType = isBambuSpool
      ? (tray.tray_type || 'PLA')
      : (selectedFilamentType || 'PLA');
    const traySubBrands = isBambuSpool
      ? (tray.tray_sub_brands || '')
      : (selectedPresetName ? getCleanFilamentName(selectedPresetName) : '');

    const payload = {
      ams_id: amsId,
      tray_id: tray.id,
      tray_info_idx: tray.tray_info_idx || '',
      tray_type: trayType,
      tray_sub_brands: traySubBrands,
      tray_color: trayColor,
      nozzle_temp_min: nozzleTempMin,
      nozzle_temp_max: nozzleTempMax,
      k: kValue,
    };
    console.log('[AMSMaterialsModal] Calling saveFilamentSettingMutation with payload:', payload);
    saveFilamentSettingMutation.mutate(payload);

    onConfirm?.({
      filamentType: selectedFilamentType,
      color: selectedColor,
      nozzleTempMax,
      nozzleTempMin,
      kProfile: selectedKProfile,
      kValue,
    });
    onClose();
  };

  const handleReset = () => {
    setSelectedFilamentType(tray.tray_type || '');
    setSelectedColor(trayColorToHex(tray.tray_color));
    setNozzleTempMax(tray.nozzle_temp_max || 220);
    setNozzleTempMin(tray.nozzle_temp_min || 190);
    setKValue(tray.k || 0);
  };

  const filamentDisplayName = isBambuSpool
    ? (tray.tray_sub_brands || tray.tray_type || 'Unknown')
    : (selectedFilamentType || 'Select filament');

  const serialNumber = isBambuSpool && tray.tray_uuid
    ? tray.tray_uuid
    : 'N/A';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-bambu-dark-secondary rounded-lg shadow-xl max-w-md w-full">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            AMS Materials Setting - {slotLabel}
          </h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-gray-100 dark:hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-gray-500 dark:text-bambu-gray" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-4">
          {/* Status indicator */}
          {isBambuSpool && (
            <div className="flex items-center gap-2 text-xs text-bambu-green bg-bambu-green/10 px-3 py-1.5 rounded-md">
              <span className="w-2 h-2 bg-bambu-green rounded-full"></span>
              Bambu Lab RFID detected - fields auto-filled
            </div>
          )}
          {!isBambuSpool && !isEmpty && (
            <div className="flex items-center gap-2 text-xs text-yellow-600 dark:text-yellow-400 bg-yellow-100 dark:bg-yellow-900/20 px-3 py-1.5 rounded-md">
              <span className="w-2 h-2 bg-yellow-500 rounded-full"></span>
              Non-Bambu Lab filament - please verify settings
            </div>
          )}
          {isEmpty && (
            <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-bambu-gray bg-gray-100 dark:bg-bambu-dark px-3 py-1.5 rounded-md">
              <span className="w-2 h-2 bg-gray-400 rounded-full"></span>
              Empty slot - configure filament settings
            </div>
          )}

          {/* Filament */}
          <div className="flex items-center gap-4">
            <label className="w-28 text-sm text-gray-600 dark:text-bambu-gray">Filament</label>
            {isEditable ? (
              <select
                value={selectedPresetId || ''}
                onChange={(e) => handlePresetSelect(e.target.value)}
                className="flex-1 min-w-0 px-3 py-2 bg-gray-100 dark:bg-bambu-dark rounded-md border border-gray-300 dark:border-bambu-dark-tertiary text-gray-900 dark:text-white text-sm truncate"
              >
                <option value="">Select filament...</option>
                {filamentPresets.length > 0 ? (
                  filamentPresets.map(preset => (
                    <option key={preset.setting_id} value={preset.setting_id}>
                      {getBaseFilamentName(preset.name)}
                    </option>
                  ))
                ) : (
                  <option value="" disabled>No filament presets for {printerModel}</option>
                )}
              </select>
            ) : (
              <div className="flex-1 px-3 py-2 bg-gray-100 dark:bg-bambu-dark rounded-md text-gray-900 dark:text-white">
                {filamentDisplayName}
              </div>
            )}
          </div>

          {/* Color */}
          <div className="flex items-center gap-4">
            <label className="w-28 text-sm text-gray-600 dark:text-bambu-gray">Color</label>
            <div className="flex items-center gap-2 flex-1">
              {isEditable ? (
                <>
                  <input
                    type="color"
                    value={selectedColor}
                    onChange={(e) => setSelectedColor(e.target.value)}
                    className="w-10 h-10 rounded cursor-pointer border-2 border-gray-300 dark:border-bambu-dark-tertiary"
                  />
                  <span className="text-gray-900 dark:text-white text-sm">{selectedColor.toUpperCase()}</span>
                </>
              ) : (
                <>
                  <div
                    className="w-8 h-8 rounded-full border-2 border-gray-300 dark:border-bambu-dark-tertiary flex-shrink-0"
                    style={{ backgroundColor: hexToRgb(tray.tray_color) }}
                  />
                  <span className="text-gray-900 dark:text-white">
                    {bambuColorName || trayColorToHex(tray.tray_color).toUpperCase()}
                  </span>
                </>
              )}
            </div>
          </div>

          {/* Nozzle Temperature */}
          <div className="flex items-center gap-4">
            <label className="w-28 text-sm text-gray-600 dark:text-bambu-gray">Nozzle Temp</label>
            <div className="flex items-center gap-2 flex-1">
              <div>
                <div className="text-xs text-gray-500 dark:text-bambu-gray mb-1">min</div>
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    value={nozzleTempMin}
                    onChange={(e) => setNozzleTempMin(Number(e.target.value))}
                    disabled={!isEditable}
                    className="w-20 px-2 py-1 bg-gray-100 dark:bg-bambu-dark rounded border border-gray-300 dark:border-bambu-dark-tertiary text-gray-900 dark:text-white disabled:opacity-60"
                  />
                  <span className="text-gray-600 dark:text-bambu-gray">°C</span>
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-500 dark:text-bambu-gray mb-1">max</div>
                <div className="flex items-center gap-1">
                  <input
                    type="number"
                    value={nozzleTempMax}
                    onChange={(e) => setNozzleTempMax(Number(e.target.value))}
                    disabled={!isEditable}
                    className="w-20 px-2 py-1 bg-gray-100 dark:bg-bambu-dark rounded border border-gray-300 dark:border-bambu-dark-tertiary text-gray-900 dark:text-white disabled:opacity-60"
                  />
                  <span className="text-gray-600 dark:text-bambu-gray">°C</span>
                </div>
              </div>
            </div>
          </div>

          {/* SN */}
          <div className="flex items-center gap-4">
            <label className="w-28 text-sm text-gray-600 dark:text-bambu-gray">SN</label>
            <div className="flex-1 px-3 py-2 bg-gray-100 dark:bg-bambu-dark rounded-md text-gray-900 dark:text-white text-xs font-mono truncate">
              {serialNumber}
            </div>
          </div>

          {/* Flow Dynamics Calibration */}
          <div className="pt-4 border-t border-gray-200 dark:border-bambu-dark-tertiary">
            <h3 className="text-sm font-medium text-gray-900 dark:text-white mb-3">
              Flow Dynamics Calibration{' '}
              <a
                href="https://wiki.bambulab.com/en/software/bambu-studio/calibration_pa"
                target="_blank"
                rel="noopener noreferrer"
                className="text-bambu-green hover:underline inline-flex items-center gap-1"
              >
                Wiki
                <ExternalLink className="w-3 h-3" />
              </a>
            </h3>

            {/* K Profile */}
            <div className="flex items-center gap-4 mb-3">
              <label className="w-28 text-sm text-gray-600 dark:text-bambu-gray flex-shrink-0">PA Profile</label>
              <select
                value={selectedKProfile}
                onChange={(e) => handleKProfileChange(e.target.value)}
                className="flex-1 min-w-0 px-3 py-2 bg-gray-100 dark:bg-bambu-dark rounded-md border border-gray-300 dark:border-bambu-dark-tertiary text-gray-900 dark:text-white text-sm truncate"
              >
                <option value="Default">Default</option>
                {matchingProfiles.map(profile => (
                  <option key={profile.slot_id} value={profile.name}>
                    {profile.name} (K={parseFloat(profile.k_value).toFixed(3)})
                  </option>
                ))}
              </select>
            </div>

            {/* Factor K */}
            <div className="flex items-center gap-4">
              <label className="w-28 text-sm text-gray-600 dark:text-bambu-gray">Factor K</label>
              <div className="flex-1 px-3 py-2 bg-gray-100 dark:bg-bambu-dark rounded-md text-gray-900 dark:text-white">
                {kValue.toFixed(3)}
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-center gap-3 p-4 border-t border-gray-200 dark:border-bambu-dark-tertiary">
          <button
            onClick={handleConfirm}
            className="px-6 py-2 bg-bambu-green text-white rounded-md hover:bg-bambu-green-dark transition-colors"
          >
            Confirm
          </button>
          <button
            onClick={handleReset}
            className="px-6 py-2 bg-gray-200 dark:bg-bambu-dark text-gray-700 dark:text-bambu-gray rounded-md hover:bg-gray-300 dark:hover:bg-bambu-dark-tertiary transition-colors"
          >
            Reset
          </button>
          <button
            onClick={onClose}
            className="px-6 py-2 bg-gray-200 dark:bg-bambu-dark text-gray-700 dark:text-bambu-gray rounded-md hover:bg-gray-300 dark:hover:bg-bambu-dark-tertiary transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
