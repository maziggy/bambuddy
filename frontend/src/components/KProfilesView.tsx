import React, { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Gauge,
  Loader2,
  RefreshCw,
  Printer,
  Plus,
  X,
  AlertCircle,
  WifiOff,
  Trash2,
  Search,
  Copy,
  Download,
  Upload,
  CheckSquare,
  Square,
  StickyNote,
} from 'lucide-react';
import { api } from '../api/client';
import type { KProfile, KProfileCreate, KProfileDelete, Permission } from '../api/client';
import {
  buildFilamentPresetOptions,
  resolveFilamentId,
  type FilamentPresetOption,
  type FilamentPresetSource,
} from '../utils/filamentPresets';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { useCancellableTimeout } from '../hooks/useCancellableTimeout';

interface KProfileCardProps {
  profile: KProfile;
  onEdit: () => void;
  onCopy?: () => void;
  selectionMode?: boolean;
  isSelected?: boolean;
  onToggleSelect?: () => void;
  note?: string;  // Note text to display as preview
}

// Truncate to 3 decimal places (like Bambu Studio) instead of rounding
const truncateK = (value: string) => {
  const num = parseFloat(value);
  return (Math.trunc(num * 1000) / 1000).toFixed(3);
};

// nozzle_id encodes the flow type, per the slicer's own generator:
//   "H" + (Standard ? "S" : "H") + "00" + "-" + diameter
// so "HS00-0.4" is Standard and "HH00-0.4" is High Flow. The "00" is a literal,
// not a material code.
const STANDARD_FLOW = 'HS00';
const HIGH_FLOW = 'HH00';

// Many printers omit nozzle_id from their extrusion_cali_get response entirely
// (#1748) — the field simply isn't in the payload. BambuStudio treats that as
// Standard (its parser falls back to nvtStandard when the key is absent), and
// so do we: the flow type stays a real, editable value rather than a blank.
const getNozzleTypePrefix = (nozzleId: string) => {
  const match = nozzleId.match(/^([A-Z]{2}\d{2})/);
  return match ? match[1] : STANDARD_FLOW;
};

// Short label for the profile list.
const getFlowTypeLabel = (nozzleId: string) =>
  getNozzleTypePrefix(nozzleId) === HIGH_FLOW ? 'HF' : 'S';

// Extract filament name from profile name (e.g., "High Flow_Devil Design PLA Basic" -> "Devil Design PLA Basic")
const extractFilamentName = (profileName: string) => {
  // Profile names are formatted as "{Flow Type}_{Filament Name}" or "{Flow Type} {Filament Name}"
  // Remove common prefixes - check both underscore and space separators
  const prefixes = [
    'High Flow_', 'High Flow ',  // underscore or space
    'Standard_', 'Standard ',
    'HF_', 'HF ',
    'S_', 'S ',
  ];
  for (const prefix of prefixes) {
    if (profileName.startsWith(prefix)) {
      return profileName.slice(prefix.length);
    }
  }
  // If no prefix found, check for underscore separator
  const underscoreIdx = profileName.indexOf('_');
  if (underscoreIdx > 0) {
    return profileName.slice(underscoreIdx + 1);
  }
  return profileName;
};

function KProfileCard({ profile, onEdit, onCopy, selectionMode, isSelected, onToggleSelect, note }: KProfileCardProps) {
  const flowType = getFlowTypeLabel(profile.nozzle_id);
  const diameter = profile.nozzle_diameter;

  const handleClick = () => {
    if (selectionMode && onToggleSelect) {
      onToggleSelect();
    } else {
      onEdit();
    }
  };

  return (
    <div className="flex items-center gap-2">
      {selectionMode && (
        <button
          onClick={onToggleSelect}
          className="text-bambu-gray hover:text-white transition-colors p-1"
        >
          {isSelected ? (
            <CheckSquare className="w-4 h-4 text-bambu-green" />
          ) : (
            <Square className="w-4 h-4" />
          )}
        </button>
      )}
      <button
        onClick={handleClick}
        className={`flex-1 text-left px-3 py-2 bg-bambu-dark rounded hover:bg-bambu-dark-tertiary transition-colors ${isSelected ? 'ring-1 ring-bambu-green' : ''}`}
      >
        <div className="flex items-center gap-2">
          <span className="text-bambu-green font-mono text-sm font-bold whitespace-nowrap">
            {truncateK(profile.k_value)}
          </span>
          <span className="text-white text-sm truncate flex-1" title={profile.name}>
            {profile.name || 'Unnamed'}
          </span>
          {note && (
            <span title="Has note">
              <StickyNote className="w-3 h-3 text-yellow-500" />
            </span>
          )}
          <span className="text-xs text-bambu-gray whitespace-nowrap">
            {flowType} {diameter}
          </span>
        </div>
        {note && (
          <div className="text-xs mt-0.5 truncate text-yellow-500/70" title={note}>
            Note: {note.length > 50 ? note.substring(0, 50) + '...' : note}
          </div>
        )}
      </button>
      {!selectionMode && onCopy && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onCopy();
          }}
          className="text-bambu-gray hover:text-white transition-colors p-1"
          title="Copy profile"
        >
          <Copy className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}

interface KProfileModalProps {
  profile?: KProfile;
  printerId: number;
  nozzleDiameter: string;
  existingProfiles?: KProfile[];  // Existing profiles, used for name resolution
  builtinFilaments?: { filament_id: string; name: string }[];  // Filament ID → name lookup
  filamentPresets?: FilamentPresetOption[];  // Every filament this install knows, tiered
  isDualNozzle?: boolean;  // Whether this is a dual-nozzle printer
  supportsFlowType?: boolean;  // Model sells both Standard and High Flow nozzles
  initialNote?: string;  // Initial note value for the profile
  initialNoteKey?: string | null;  // Key the note was stored under (for clearing)
  onClose: () => void;
  onSave: () => void;
  onSaveNote?: (settingId: string, note: string) => void;  // Callback to save note
  hasPermission: (permission: Permission) => boolean;
}

function KProfileModal({
  profile,
  printerId,
  nozzleDiameter,
  existingProfiles = [],
  builtinFilaments = [],
  filamentPresets = [],
  isDualNozzle = false,
  supportsFlowType = true,
  initialNote = '',
  initialNoteKey = null,
  onClose,
  onSave,
  onSaveNote,
  hasPermission,
}: KProfileModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();

  const [name, setName] = useState(profile?.name || '');
  const [kValue, setKValue] = useState(
    profile?.k_value ? truncateK(profile.k_value) : '0.020'
  );
  // What the Filament select is bound to. When editing, the printer's own
  // filament_id (the select is read-only). For a new profile, the *preset
  // handle* from the tiered list — a local row id, an Orca UUID, a Bambu Cloud
  // setting_id or a builtin filament id — which is resolved to a real
  // filament_id on submit, since only some tiers carry one directly.
  const [filamentChoice, setFilamentChoice] = useState(profile?.filament_id || '');
  // Split nozzle into type and diameter
  // Both selects are read-only while editing: they report what the printer
  // holds, they don't set it. '' means the printer reported no nozzle_id, which
  // single-nozzle models never do (#1748) — showing "High Flow" there was the
  // UI inventing a value the printer never sent.
  const [nozzleType, setNozzleType] = useState(
    profile ? getNozzleTypePrefix(profile.nozzle_id) : STANDARD_FLOW
  );
  const [modalDiameter, setModalDiameter] = useState(
    profile?.nozzle_diameter || nozzleDiameter
  );
  // For new profiles on dual-nozzle: allow selecting multiple extruders
  // For editing: use single extruder from the profile
  const [selectedExtruders, setSelectedExtruders] = useState<number[]>(
    profile ? [profile.extruder_id] : isDualNozzle ? [0, 1] : [0]  // Default: both extruders for new dual-nozzle profiles
  );
  const [isSyncing, setIsSyncing] = useState(false);
  const [savingProgress, setSavingProgress] = useState({ current: 0, total: 0 });
  const [note, setNote] = useState(initialNote);
  const [filamentQuery, setFilamentQuery] = useState('');
  // The modal defers its own close so the printer has time to process the
  // command; that timer must not outlive the modal.
  const { schedule: scheduleClose } = useCancellableTimeout();

  // Name for the filament an existing profile is bound to. The builtin table
  // (which the parent has already merged with the user's cloud presets) is
  // authoritative; a profile whose filament_id is in neither falls back to the
  // name the printer stored for it.
  const editedFilamentName = React.useMemo(() => {
    if (!profile?.filament_id) return '';
    const builtinName = builtinFilaments.find(bf => bf.filament_id === profile.filament_id)?.name;
    if (builtinName) return builtinName;
    const fromProfile = existingProfiles.find(p => p.filament_id === profile.filament_id);
    return extractFilamentName(fromProfile?.name || profile.name || '') || profile.filament_id;
  }, [profile, existingProfiles, builtinFilaments]);

  // The tiered list, grouped for rendering. Order is fixed app-wide —
  // imported, then Orca Cloud, then Bambu Cloud, then the hardcoded table —
  // and buildFilamentPresetOptions has already sorted by it, so grouping is
  // just a partition that preserves that order.
  const presetGroups = React.useMemo(() => {
    const labels: [FilamentPresetSource, string][] = [
      ['local', t('kProfiles.modal.source.local')],
      ['orca_cloud', t('kProfiles.modal.source.orcaCloud')],
      ['cloud', t('kProfiles.modal.source.bambuCloud')],
      ['builtin', t('kProfiles.modal.source.builtin')],
    ];
    const query = filamentQuery.trim().toLowerCase();
    const matches = query
      ? filamentPresets.filter(p => p.name.toLowerCase().includes(query))
      : filamentPresets;
    return labels
      .map(([source, label]) => ({ source, label, items: matches.filter(p => p.source === source) }))
      .filter(g => g.items.length > 0);
  }, [filamentPresets, filamentQuery, t]);

  const saveMutation = useMutation({
    mutationFn: (data: KProfileCreate) => {
      console.log('[KProfile] Calling API...');
      return api.setKProfile(printerId, data);
    },
    onSuccess: (result, variables) => {
      console.log('[KProfile] Save success:', result);
      showToast(t('kProfiles.toast.profileSaved'));
      // Save note if it changed (including clearing it)
      if (onSaveNote && note !== initialNote) {
        let profileKey: string;
        if (note === '' && initialNoteKey) {
          // Clearing note: use the same key it was stored under
          profileKey = initialNoteKey;
        } else if (profile && profile.slot_id > 0) {
          // Editing: use setting_id if available, or composite key with slot_id
          profileKey = profile.setting_id || `slot_${profile.slot_id}_${profile.filament_id}_${profile.extruder_id}`;
        } else {
          // New profile: use name as key (matched against the reloaded profile,
          // so it has to be the resolved filament_id that was sent — not the
          // preset handle the user picked).
          profileKey = `name_${name}_${variables.filament_id}`;
        }
        onSaveNote(profileKey, note);
      }
      // Show syncing indicator while printer processes the command
      setIsSyncing(true);
      // Add delay before closing to give printer time to process the save
      // onSave will trigger refetch in the parent component
      scheduleClose(() => {
        setIsSyncing(false);
        onSave();
      }, 2500);
    },
    onError: (error: Error) => {
      console.error('[KProfile] Save error:', error);
      showToast(error.message, 'error');
      setIsSyncing(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (data: KProfileDelete) => {
      console.log('[KProfile] Deleting profile...');
      return api.deleteKProfile(printerId, data);
    },
    onSuccess: (result) => {
      console.log('[KProfile] Delete success:', result);
      showToast(t('kProfiles.toast.profileDeleted'));
      // Show syncing indicator while printer processes the command
      setIsSyncing(true);
      // Add longer delay for delete - printer needs more time to process
      // before it can return the updated profile list
      scheduleClose(() => {
        setIsSyncing(false);
        onClose();
      }, 4000);
    },
    onError: (error: Error) => {
      console.error('[KProfile] Delete error:', error);
      showToast(error.message, 'error');
      setIsSyncing(false);
    },
  });

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const handleDelete = () => {
    if (!profile) return;
    deleteMutation.mutate({
      slot_id: profile.slot_id,
      extruder_id: profile.extruder_id,
      nozzle_id: profile.nozzle_id,
      nozzle_diameter: profile.nozzle_diameter,
      filament_id: profile.filament_id,
      setting_id: profile.setting_id,
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    // Validate at least one extruder is selected for dual-nozzle
    if (isDualNozzle && !profile && selectedExtruders.length === 0) {
      showToast(t('kProfiles.toast.selectAtLeastOneExtruder'), 'error');
      return;
    }

    // Format k_value to 6 decimal places for Bambu protocol
    const formattedKValue = parseFloat(kValue).toFixed(6);
    // Combine nozzle type and diameter into nozzle_id (e.g., "HH00-0.4")
    const nozzleId = `${nozzleType}-${modalDiameter}`;

    // An edit is delete + re-add on single-nozzle printers, so the nozzle
    // fields have to survive the round trip — both selects are disabled while
    // editing. Rebuilding them blindly from the selects is what let a 0.6mm
    // profile come back as "HH00-0.4" once the parse defaults had stamped it
    // 0.4 (#1748), so prefer whatever the printer reported. Where it reported
    // no nozzle_id at all, send the rebuilt one rather than an empty string —
    // the field is part of the profile's identity on the wire and the slicer
    // always populates it.
    const editNozzleId = profile ? profile.nozzle_id || nozzleId : nozzleId;
    const editDiameter = profile ? profile.nozzle_diameter : modalDiameter;

    // The printer indexes its calibration table by filament_id, so the preset
    // the user picked has to be reduced to one before anything is sent. Only
    // the builtin tier and Bambu's official cloud presets carry one outright;
    // a cloud *user* preset needs its detail fetched, and imported / Orca
    // presets have no Bambu id at all and map to the generic for their
    // material. Refuse rather than guess when nothing resolves — a profile
    // filed under the wrong filament is invisible to the slot that needs it.
    let resolvedFilamentId = profile?.filament_id || '';
    if (!profile) {
      const picked = filamentPresets.find(p => p.id === filamentChoice);
      if (!picked) {
        showToast(t('kProfiles.toast.selectFilament'), 'error');
        return;
      }
      resolvedFilamentId = await resolveFilamentId(picked, api.getCloudSettingDetail);
      if (!resolvedFilamentId) {
        showToast(t('kProfiles.toast.filamentNotResolvable', { name: picked.name }), 'error');
        return;
      }
    }

    // For editing or single extruder: just save one profile
    if (profile || selectedExtruders.length === 1) {
      const payload = {
        name: name,
        k_value: formattedKValue,
        filament_id: resolvedFilamentId,
        nozzle_id: editNozzleId,
        nozzle_diameter: editDiameter,
        extruder_id: profile ? profile.extruder_id : selectedExtruders[0],
        setting_id: profile?.setting_id,
        slot_id: profile?.slot_id ?? 0,
      };
      console.log('[KProfile] Saving profile:', payload);
      saveMutation.mutate(payload);
      return;
    }

    // For new profiles with multiple extruders: use batch endpoint
    setIsSyncing(true);
    setSavingProgress({ current: 1, total: selectedExtruders.length });

    // Build payload for all selected extruders
    const batchPayload = selectedExtruders.map(extruderId => ({
      name: name,
      k_value: formattedKValue,
      filament_id: resolvedFilamentId,
      nozzle_id: nozzleId,
      nozzle_diameter: modalDiameter,
      extruder_id: extruderId,
      setting_id: undefined,
      slot_id: 0,
    }));

    console.log(`[KProfile] Saving ${batchPayload.length} profiles in batch:`, batchPayload);

    try {
      await api.setKProfilesBatch(printerId, batchPayload);
      showToast(t('kProfiles.toast.profilesSaved', { count: selectedExtruders.length }));
      // Save note for new batch profiles
      if (onSaveNote && note) {
        const profileKey = `name_${name}_${resolvedFilamentId}`;
        onSaveNote(profileKey, note);
      }
    } catch (error) {
      console.error('[KProfile] Failed to save batch:', error);
      showToast(t('kProfiles.toast.failedToSaveBatch'), 'error');
      setIsSyncing(false);
      setSavingProgress({ current: 0, total: 0 });
      return;
    }

    setSavingProgress({ current: selectedExtruders.length, total: selectedExtruders.length });
    // Wait for final sync before closing
    // onSave will trigger refetch in the parent component
    scheduleClose(() => {
      setIsSyncing(false);
      setSavingProgress({ current: 0, total: 0 });
      onSave();
    }, 3000);
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md relative">
        {/* Syncing overlay */}
        {isSyncing && (
          <div className="absolute inset-0 bg-bambu-dark-secondary/90 flex flex-col items-center justify-center z-10 rounded-lg">
            <Loader2 className="w-8 h-8 text-bambu-green animate-spin mb-3" />
            <p className="text-white font-medium">
              {savingProgress.total > 1
                ? t('kProfiles.modal.savingExtruder', { current: savingProgress.current, total: savingProgress.total })
                : t('kProfiles.modal.syncing')}
            </p>
            <p className="text-bambu-gray text-sm mt-1">{t('kProfiles.modal.pleaseWait')}</p>
          </div>
        )}
        <CardContent className="p-0">
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <h2 className="text-xl font-semibold text-white">
              {profile ? t('kProfiles.modal.editTitle') : t('kProfiles.modal.addTitle')}
            </h2>
            <button
              onClick={onClose}
              className="text-bambu-gray hover:text-white transition-colors"
              disabled={isSyncing}
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <form onSubmit={handleSubmit} className="p-4 space-y-4">
            {/* Profile Name - read-only when editing */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('kProfiles.modal.profileName')}</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={!!profile}
                className={`w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none ${profile ? 'opacity-60 cursor-not-allowed' : ''}`}
                placeholder={t('kProfiles.modal.profileNamePlaceholder')}
                required={!profile}
              />
            </div>

            {/* K-Value - always editable */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('kProfiles.modal.kValue')}</label>
              <input
                type="text"
                inputMode="decimal"
                value={kValue}
                onChange={(e) => {
                  // Allow typing any decimal value
                  const val = e.target.value;
                  if (val === '' || /^\d*\.?\d*$/.test(val)) {
                    setKValue(val);
                  }
                }}
                onBlur={(e) => {
                  // Format to 3 decimal places on blur
                  const num = parseFloat(e.target.value);
                  if (!isNaN(num)) {
                    setKValue((Math.trunc(num * 1000) / 1000).toFixed(3));
                  }
                }}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none font-mono"
                placeholder={t('kProfiles.modal.kValuePlaceholder')}
                required
              />
              <p className="text-xs text-bambu-gray mt-1">
                {t('kProfiles.modal.kValueHelp')}
              </p>
            </div>

            {/* Filament - read-only when editing */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('kProfiles.modal.filament')}</label>
              {profile ? (
                // Editing or copying: the filament is fixed, so this is a
                // readout rather than a control.
                <div className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white opacity-60">
                  {editedFilamentName || profile.filament_id}
                </div>
              ) : (
                // A real list rather than a <select>: Chrome ignores almost
                // every CSS property on <optgroup>, so a source heading inside
                // a native dropdown can't be made to stand out.
                <div className="border border-bambu-dark-tertiary rounded-lg overflow-hidden">
                  <div className="relative border-b border-bambu-dark-tertiary">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
                    <input
                      type="text"
                      value={filamentQuery}
                      onChange={(e) => setFilamentQuery(e.target.value)}
                      placeholder={t('kProfiles.modal.searchFilaments')}
                      className="w-full pl-10 pr-3 py-2 bg-bambu-dark text-white placeholder-bambu-gray focus:outline-none"
                    />
                  </div>
                  <div className="max-h-56 overflow-y-auto bg-bambu-dark">
                    {presetGroups.length === 0 ? (
                      <p className="px-3 py-3 text-xs text-bambu-gray">
                        {filamentPresets.length === 0
                          ? t('kProfiles.modal.noFilamentsHelp')
                          : t('kProfiles.modal.noFilamentMatches')}
                      </p>
                    ) : presetGroups.map((group) => (
                      <div key={group.source}>
                        <div className="sticky top-0 z-10 flex items-center gap-2 px-3 py-1.5 bg-bambu-dark-secondary border-y border-bambu-dark-tertiary">
                          <span className="text-xs font-bold uppercase tracking-wider text-bambu-green">
                            {group.label}
                          </span>
                          <span className="text-[10px] text-bambu-gray">{group.items.length}</span>
                        </div>
                        {group.items.map((f) => (
                          <button
                            key={f.id}
                            type="button"
                            onClick={() => {
                              setFilamentChoice(f.id);
                              // Auto-generate the profile name, but never over
                              // an entry the user typed.
                              if (!name) {
                                const flowLabel = nozzleType === HIGH_FLOW ? 'HF' : 'S';
                                setName(`${flowLabel} ${f.name}`);
                              }
                            }}
                            className={`w-full text-left px-3 py-1.5 text-sm transition-colors ${
                              filamentChoice === f.id
                                ? 'bg-bambu-green/20 text-white'
                                : 'text-white hover:bg-bambu-dark-tertiary'
                            }`}
                          >
                            {f.name}
                          </button>
                        ))}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Flow Type and Nozzle Size - read-only when editing. Flow type
                is hidden on models sold with a single nozzle variant (the
                A-series), where the choice would be meaningless — same gate
                the slicer applies via support_nozzle_volume(). */}
            <div className={supportsFlowType ? 'grid grid-cols-2 gap-4' : ''}>
              <div className={supportsFlowType ? '' : 'hidden'}>
                <label className="block text-sm text-bambu-gray mb-1">{t('kProfiles.modal.flowType')}</label>
                <select
                  value={nozzleType}
                  onChange={(e) => {
                    const newNozzleType = e.target.value;
                    setNozzleType(newNozzleType);
                    // Update profile name when flow type changes (for new profiles)
                    // Only auto-generate if name is empty - don't overwrite user input
                    if (!profile && filamentChoice && !name) {
                      const selectedFilament = filamentPresets.find(f => f.id === filamentChoice);
                      if (selectedFilament) {
                        const flowLabel = newNozzleType === HIGH_FLOW ? 'HF' : 'S';
                        setName(`${flowLabel} ${selectedFilament.name}`);
                      }
                    }
                  }}
                  disabled={!!profile}
                  className={`w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none ${profile ? 'opacity-60 cursor-not-allowed' : ''}`}
                >
                  <option value={HIGH_FLOW}>{t('kProfiles.modal.highFlow')}</option>
                  <option value={STANDARD_FLOW}>{t('kProfiles.modal.standard')}</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">{t('kProfiles.modal.nozzleSize')}</label>
                <select
                  value={modalDiameter}
                  onChange={(e) => setModalDiameter(e.target.value)}
                  disabled={!!profile}
                  className={`w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none ${profile ? 'opacity-60 cursor-not-allowed' : ''}`}
                >
                  <option value="0.2">0.2mm</option>
                  <option value="0.4">0.4mm</option>
                  <option value="0.6">0.6mm</option>
                  <option value="0.8">0.8mm</option>
                </select>
              </div>
            </div>

            {/* Extruder - only show for dual-nozzle printers */}
            {isDualNozzle && (
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {profile ? t('kProfiles.modal.extruder') : t('kProfiles.modal.extruders')}
                </label>
                {profile ? (
                  // Read-only display for editing
                  <div className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white opacity-60">
                    {profile.extruder_id === 1 ? t('kProfiles.modal.left') : t('kProfiles.modal.right')}
                  </div>
                ) : (
                  // Checkboxes for new profile - can select both
                  <div className="flex gap-4">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedExtruders.includes(1)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelectedExtruders([...selectedExtruders, 1]);
                          } else {
                            setSelectedExtruders(selectedExtruders.filter(id => id !== 1));
                          }
                        }}
                        className="w-4 h-4 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green focus:ring-offset-0 accent-bambu-green"
                      />
                      <span className="text-white">{t('kProfiles.modal.left')}</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedExtruders.includes(0)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelectedExtruders([...selectedExtruders, 0]);
                          } else {
                            setSelectedExtruders(selectedExtruders.filter(id => id !== 0));
                          }
                        }}
                        className="w-4 h-4 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green focus:ring-offset-0 accent-bambu-green"
                      />
                      <span className="text-white">{t('kProfiles.modal.right')}</span>
                    </label>
                  </div>
                )}
              </div>
            )}

            {/* Notes */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('kProfiles.modal.notes')}</label>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder={t('kProfiles.modal.notesPlaceholder')}
                rows={2}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none resize-none"
              />
              <p className="text-xs text-bambu-gray mt-1">
                {t('kProfiles.modal.notesHelp')}
              </p>
            </div>

            <div className="flex gap-2 pt-4">
              {profile && (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setShowDeleteConfirm(true)}
                  disabled={deleteMutation.isPending || isSyncing || !hasPermission('kprofiles:delete')}
                  title={!hasPermission('kprofiles:delete') ? t('kProfiles.permission.noDelete') : undefined}
                  className="text-red-500 hover:bg-red-500/10"
                >
                  {deleteMutation.isPending ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Trash2 className="w-4 h-4" />
                  )}
                </Button>
              )}
              <Button
                type="button"
                variant="secondary"
                onClick={onClose}
                disabled={isSyncing}
                className="flex-1"
              >
                {t('common.cancel')}
              </Button>
              <Button
                type="submit"
                disabled={saveMutation.isPending || isSyncing || !hasPermission(profile ? 'kprofiles:update' : 'kprofiles:create')}
                title={!hasPermission(profile ? 'kprofiles:update' : 'kprofiles:create') ? t(profile ? 'kProfiles.permission.noUpdate' : 'kProfiles.permission.noCreate') : undefined}
                className="flex-1"
              >
                {saveMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Gauge className="w-4 h-4" />
                )}
                {t('common.save')}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Delete Confirmation Modal */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[60]">
          <Card className="w-full max-w-sm">
            <CardContent className="p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-red-500/20 flex items-center justify-center">
                  <Trash2 className="w-5 h-5 text-red-500" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white">{t('kProfiles.deleteConfirm.title')}</h3>
                  <p className="text-sm text-bambu-gray">{t('kProfiles.deleteConfirm.cannotUndo')}</p>
                </div>
              </div>
              <p className="text-bambu-gray mb-6">
                {t('kProfiles.deleteConfirm.message', { name: profile?.name })}
              </p>
              <div className="flex gap-3">
                <Button
                  variant="secondary"
                  onClick={() => setShowDeleteConfirm(false)}
                  className="flex-1"
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={() => {
                    setShowDeleteConfirm(false);
                    handleDelete();
                  }}
                  disabled={deleteMutation.isPending}
                  className="flex-1 bg-red-500 hover:bg-red-600 text-white"
                >
                  {deleteMutation.isPending ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Trash2 className="w-4 h-4" />
                  )}
                  {t('common.delete')}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

type ExtruderFilter = 'all' | 'left' | 'right';
type FlowTypeFilter = 'all' | 'hf' | 's';
type SortOption = 'name' | 'k_value' | 'filament';

// localStorage keys
const STORAGE_KEYS = {
  NOZZLE_DIAMETER: 'bambusy_kprofiles_nozzle',
  SORT_OPTION: 'bambusy_kprofiles_sort',
};

export function KProfilesView() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const [selectedPrinter, setSelectedPrinter] = useState<number | null>(null);
  // Load nozzle diameter from localStorage
  const [nozzleDiameter, setNozzleDiameter] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEYS.NOZZLE_DIAMETER);
    return saved || '0.4';
  });
  const [editingProfile, setEditingProfile] = useState<KProfile | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [copyingProfile, setCopyingProfile] = useState<KProfile | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [extruderFilter, setExtruderFilter] = useState<ExtruderFilter>('all');
  const [flowTypeFilter, setFlowTypeFilter] = useState<FlowTypeFilter>('all');
  // Load sort option from localStorage
  const [sortOption, setSortOption] = useState<SortOption>(() => {
    const saved = localStorage.getItem(STORAGE_KEYS.SORT_OPTION);
    return (saved as SortOption) || 'name';
  });
  // Bulk selection mode
  // Use composite key: `${slot_id}_${extruder_id}` since slot_id alone is not unique across extruders
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedProfiles, setSelectedProfiles] = useState<Set<string>>(new Set());
  const [showBulkDeleteConfirm, setShowBulkDeleteConfirm] = useState(false);
  const [bulkDeleteInProgress, setBulkDeleteInProgress] = useState(false);

  // Helper to create unique profile key for selection - wrapped in useCallback to prevent re-renders
  const getProfileKey = useCallback((profile: KProfile) => `${profile.slot_id}_${profile.extruder_id}`, []);

  // Save nozzle diameter to localStorage when it changes
  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.NOZZLE_DIAMETER, nozzleDiameter);
  }, [nozzleDiameter]);

  // Save sort option to localStorage when it changes
  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.SORT_OPTION, sortOption);
  }, [sortOption]);

  // Get available printers
  const { data: printers, isLoading: printersLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Get K-profiles for selected printer (filtered by nozzle diameter)
  const {
    data: kprofiles,
    isLoading: kprofilesLoading,
    isFetching,
    error: kprofilesError,
    refetch: refetchProfiles,
  } = useQuery({
    queryKey: ['kprofiles', selectedPrinter, nozzleDiameter],
    queryFn: async () => {
      console.log('[KProfiles] Fetching profiles for printer', selectedPrinter, 'nozzle', nozzleDiameter);
      const result = await api.getKProfiles(selectedPrinter!, nozzleDiameter);
      console.log('[KProfiles] Received profiles:', result?.profiles?.length || 0, 'profiles');
      return result;
    },
    enabled: !!selectedPrinter,
    retry: false,
    staleTime: 0,  // Always consider data stale to ensure fresh fetch
    gcTime: 0,  // Don't cache results
    refetchOnMount: 'always',  // Always refetch when component mounts
  });

  // A second fetch for 0.4mm profiles used to seed the Add-Profile filament
  // dropdown. The dropdown is built from the filament preset tiers now
  // (#2719), so the round trip bought nothing — and it fired concurrently
  // with the fetch above whenever a different nozzle was selected, which is
  // exactly the two-requests-in-flight case that made K-profile fetches time
  // out (#1748).

  // Fetch builtin filament names for accurate filament_id → name resolution
  const { data: builtinFilaments } = useQuery({
    queryKey: ['builtinFilaments'],
    queryFn: () => api.getBuiltinFilaments(),
    staleTime: 300000,  // Cache for 5 minutes (static data)
  });

  // Fetch filament_id → name mapping for user cloud presets (P* IDs)
  const { data: filamentIdMap } = useQuery({
    queryKey: ['filamentIdMap'],
    queryFn: () => api.getFilamentIdMap(),
    staleTime: 300000,  // Cache for 5 minutes
  });

  // The other three filament tiers, so a printer with no K-profiles yet can
  // still be given its first one (#2719). Each query stands alone and fails
  // quietly: not being signed in to a cloud should thin the list, not break
  // the page, and the builtin tier above guarantees it is never empty.
  const { data: localPresets } = useQuery({
    queryKey: ['localPresets'],
    queryFn: () => api.getLocalPresets(),
    retry: false,
  });

  const { data: orcaCloudList } = useQuery({
    queryKey: ['orcaCloudProfilesForKProfiles'],
    queryFn: () => api.orcaCloudListProfiles(),
    retry: false,
  });

  const { data: cloudSettings } = useQuery({
    queryKey: ['cloudSettings'],
    queryFn: () => api.getCloudSettings(),
    retry: false,
  });

  // Fetch K-profile notes (stored locally)
  const {
    data: notesData,
    refetch: refetchNotes,
  } = useQuery({
    queryKey: ['kprofile-notes', selectedPrinter],
    queryFn: () => api.getKProfileNotes(selectedPrinter!),
    enabled: !!selectedPrinter,
    staleTime: 30000,  // Cache for 30 seconds
  });

  // Check if error is due to printer not being connected
  const isOfflineError = kprofilesError?.message?.includes('not connected');

  // Auto-select first connected printer
  useEffect(() => {
    if (!selectedPrinter && printers && printers.length > 0) {
      const activePrinter = printers.find((p) => p.is_active);
      if (activePrinter) {
        setSelectedPrinter(activePrinter.id);
      }
    }
  }, [selectedPrinter, printers]);

  // Refetch profiles when printer selection changes
  useEffect(() => {
    if (selectedPrinter) {
      // Delay refetch to ensure query is enabled after state update
      const timer = setTimeout(() => {
        refetchProfiles();
      }, 150);
      return () => clearTimeout(timer);
    }
  }, [selectedPrinter, nozzleDiameter]); // eslint-disable-line react-hooks/exhaustive-deps

  // Get connected printers for display
  const connectedPrinters = printers?.filter((p) => p.is_active) || [];

  // Build filament lookup for name resolution (builtin + user cloud presets)
  const builtinFilamentMap = React.useMemo(() => {
    const map = new Map<string, string>();
    if (builtinFilaments) {
      for (const bf of builtinFilaments) {
        map.set(bf.filament_id, bf.name);
      }
    }
    // Also add user cloud presets (P* filament_ids resolved from cloud details)
    if (filamentIdMap) {
      for (const [fid, name] of Object.entries(filamentIdMap)) {
        if (!map.has(fid)) {
          map.set(fid, name);
        }
      }
    }
    return map;
  }, [builtinFilaments, filamentIdMap]);

  // Enriched builtin filaments array (builtin + cloud presets merged)
  // Pass this to modals so they have the full filament name lookup
  const enrichedBuiltinFilaments = React.useMemo(() => {
    return Array.from(builtinFilamentMap.entries()).map(([fid, name]) => ({
      filament_id: fid,
      name,
    }));
  }, [builtinFilamentMap]);

  // Every filament this install knows about, ranked in the app-wide order:
  // imported presets, then Orca Cloud, then Bambu Cloud, then the hardcoded
  // built-in table as the floor.
  const filamentPresets = React.useMemo(
    () => buildFilamentPresetOptions({
      localPresets: localPresets?.filament,
      orcaProfiles: orcaCloudList?.filament,
      cloudSettings: cloudSettings?.filament,
      builtinFilaments,
    }),
    [localPresets?.filament, orcaCloudList?.filament, cloudSettings?.filament, builtinFilaments]
  );

  // Resolve filament name: builtin table first, then extract from profile name
  const resolveFilamentName = React.useCallback((profile: KProfile) => {
    return builtinFilamentMap.get(profile.filament_id) || extractFilamentName(profile.name);
  }, [builtinFilamentMap]);

  // Filter and sort profiles
  // Note: nozzle diameter filtering is done server-side via MQTT request
  const filteredProfiles = React.useMemo(() => {
    if (!kprofiles?.profiles) return [];

    const filtered = kprofiles.profiles.filter((p) => {
      // Search filter - match name or filament_id (case-insensitive)
      const query = searchQuery.toLowerCase();
      const matchesSearch =
        !query ||
        p.name.toLowerCase().includes(query) ||
        p.filament_id.toLowerCase().includes(query);

      // Extruder filter
      const matchesExtruder =
        extruderFilter === 'all' ||
        (extruderFilter === 'left' && p.extruder_id === 1) ||
        (extruderFilter === 'right' && p.extruder_id === 0);

      // Flow type filter (HH = High Flow, HS = Standard)
      const matchesFlowType =
        flowTypeFilter === 'all' ||
        (flowTypeFilter === 'hf' && p.nozzle_id.startsWith('HH')) ||
        (flowTypeFilter === 's' && p.nozzle_id.startsWith('HS'));

      return matchesSearch && matchesExtruder && matchesFlowType;
    });

    // Sort profiles
    return filtered.sort((a, b) => {
      switch (sortOption) {
        case 'k_value':
          return parseFloat(a.k_value) - parseFloat(b.k_value);
        case 'filament':
          return resolveFilamentName(a).localeCompare(resolveFilamentName(b));
        case 'name':
        default:
          return a.name.localeCompare(b.name);
      }
    });
  }, [kprofiles?.profiles, searchQuery, extruderFilter, flowTypeFilter, sortOption, resolveFilamentName]);

  // Check if selected printer is dual-nozzle (auto-detected from MQTT temperature data)
  const selectedPrinterData = printers?.find((p) => p.id === selectedPrinter);
  const isDualNozzle = selectedPrinterData?.nozzle_count === 2;

  // Whether this printer model is sold with both Standard and High Flow
  // nozzles. Comes from the model, not from whether the payload happened to
  // carry a nozzle_id — most printers omit that field entirely (#1748) while
  // still offering both flows. Only the A-series has a single variant.
  const supportsFlowType = selectedPrinterData?.supports_nozzle_flow_type ?? true;

  // Don't strand the list behind a filter whose control just disappeared.
  useEffect(() => {
    if (!supportsFlowType) setFlowTypeFilter('all');
  }, [supportsFlowType]);


  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't trigger shortcuts when typing in input fields
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) {
        return;
      }
      // Don't trigger when modal is open
      if (editingProfile || showAddModal || copyingProfile) {
        return;
      }

      if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        refetchProfiles();
      } else if (e.key === 'n' || e.key === 'N') {
        e.preventDefault();
        setShowAddModal(true);
      } else if (e.key === 'Escape' && selectionMode) {
        e.preventDefault();
        setSelectionMode(false);
        setSelectedProfiles(new Set());
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [editingProfile, showAddModal, copyingProfile, selectionMode, refetchProfiles]);

  // Export profiles to JSON file
  const handleExport = useCallback(() => {
    if (!kprofiles?.profiles || kprofiles.profiles.length === 0) {
      showToast(t('kProfiles.toast.noProfilesToExport'), 'error');
      return;
    }

    const exportData = {
      version: 1,
      exported_at: new Date().toISOString(),
      printer: selectedPrinterData?.name || 'Unknown',
      nozzle_diameter: nozzleDiameter,
      profiles: kprofiles.profiles.map(p => ({
        name: p.name,
        k_value: p.k_value,
        filament_id: p.filament_id,
        nozzle_id: p.nozzle_id,
        nozzle_diameter: p.nozzle_diameter,
        extruder_id: p.extruder_id,
      })),
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `kprofiles_${selectedPrinterData?.name || 'printer'}_${nozzleDiameter}mm_${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast(t('kProfiles.toast.exportedProfiles', { count: kprofiles.profiles.length }));
  }, [kprofiles?.profiles, selectedPrinterData, nozzleDiameter, showToast, t]);

  // Import profiles from JSON file
  const handleImport = useCallback(() => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;

      try {
        const text = await file.text();
        const data = JSON.parse(text);

        if (!data.profiles || !Array.isArray(data.profiles)) {
          showToast(t('kProfiles.toast.invalidFileFormat'), 'error');
          return;
        }

        // Import profiles one by one
        let imported = 0;
        for (const p of data.profiles) {
          if (!p.name || !p.k_value || !p.filament_id) continue;

          try {
            await api.setKProfile(selectedPrinter!, {
              name: p.name,
              k_value: parseFloat(p.k_value).toFixed(6),
              filament_id: p.filament_id,
              // An export from a printer that reports no nozzle_id carries
              // none; fall back to Standard, the same default the slicer's
              // parser uses for a missing field.
              nozzle_id: p.nozzle_id || `${STANDARD_FLOW}-${nozzleDiameter}`,
              nozzle_diameter: p.nozzle_diameter || nozzleDiameter,
              extruder_id: p.extruder_id ?? 0,
              slot_id: 0, // Always create new
            });
            imported++;
            // Small delay between imports
            await new Promise(resolve => setTimeout(resolve, 500));
          } catch (err) {
            console.error('Failed to import profile:', p.name, err);
          }
        }

        showToast(t('kProfiles.toast.importedProfiles', { count: imported, total: data.profiles.length }));
        refetchProfiles();
      } catch (err) {
        console.error('Import error:', err);
        showToast(t('kProfiles.toast.failedToParseImport'), 'error');
      }
    };
    input.click();
  }, [selectedPrinter, nozzleDiameter, showToast, refetchProfiles, t]);

  // Toggle profile selection using composite key
  const toggleProfileSelection = useCallback((profileKey: string) => {
    setSelectedProfiles(prev => {
      const next = new Set(prev);
      if (next.has(profileKey)) {
        next.delete(profileKey);
      } else {
        next.add(profileKey);
      }
      return next;
    });
  }, []);

  // Select all visible profiles
  const selectAllProfiles = useCallback(() => {
    setSelectedProfiles(new Set(filteredProfiles.map(p => getProfileKey(p))));
  }, [filteredProfiles, getProfileKey]);

  // Delete selected profiles
  const handleBulkDelete = useCallback(() => {
    if (selectedProfiles.size === 0) return;
    setShowBulkDeleteConfirm(true);
  }, [selectedProfiles.size]);

  // Execute the actual bulk delete
  const executeBulkDelete = useCallback(async () => {
    const profilesToDelete = filteredProfiles.filter(p => selectedProfiles.has(getProfileKey(p)));
    setBulkDeleteInProgress(true);

    let deleted = 0;
    for (const profile of profilesToDelete) {
      try {
        await api.deleteKProfile(selectedPrinter!, {
          slot_id: profile.slot_id,
          extruder_id: profile.extruder_id,
          nozzle_id: profile.nozzle_id,
          nozzle_diameter: profile.nozzle_diameter,
          filament_id: profile.filament_id,
          setting_id: profile.setting_id,
        });
        deleted++;
        // Small delay between deletes
        await new Promise(resolve => setTimeout(resolve, 300));
      } catch (err) {
        console.error('Failed to delete profile:', profile.name, err);
      }
    }

    showToast(t('kProfiles.toast.profilesDeleted', { count: deleted }));
    setBulkDeleteInProgress(false);
    setShowBulkDeleteConfirm(false);
    setSelectionMode(false);
    setSelectedProfiles(new Set());
    refetchProfiles();
  }, [selectedPrinter, selectedProfiles, filteredProfiles, showToast, refetchProfiles, getProfileKey, t]);

  // Generate possible keys for a profile (for notes lookup)
  // Returns array of keys to check: setting_id, slot-based, name-based
  const getProfileKeys = useCallback((profile: KProfile): string[] => {
    const keys: string[] = [];
    if (profile.setting_id) {
      keys.push(profile.setting_id);
    }
    // Slot-based key (for profiles without setting_id)
    keys.push(`slot_${profile.slot_id}_${profile.filament_id}_${profile.extruder_id}`);
    // Name-based key (for newly created profiles)
    keys.push(`name_${profile.name}_${profile.filament_id}`);
    return keys;
  }, []);

  // Save note for a profile
  const handleSaveNote = useCallback(async (profileKey: string, noteText: string) => {
    if (!selectedPrinter) return;
    try {
      await api.setKProfileNote(selectedPrinter, profileKey, noteText);
      refetchNotes();
    } catch (err) {
      console.error('Failed to save note:', err);
      showToast(t('kProfiles.toast.failedToSaveNote'), 'error');
    }
  }, [selectedPrinter, refetchNotes, showToast, t]);

  // Get note for a profile (checks all possible keys)
  // Returns { note, key } so we know which key the note was stored under
  const getNoteWithKey = useCallback((profile: KProfile): { note: string; key: string | null } => {
    if (!notesData?.notes) return { note: '', key: null };
    const keys = getProfileKeys(profile);
    for (const key of keys) {
      if (notesData.notes[key]) {
        return { note: notesData.notes[key], key };
      }
    }
    return { note: '', key: null };
  }, [notesData, getProfileKeys]);

  // Simple getter for display purposes
  const getNote = useCallback((profile: KProfile) => {
    return getNoteWithKey(profile).note;
  }, [getNoteWithKey]);

  if (printersLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  if (!printers || printers.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <AlertCircle className="w-12 h-12 text-bambu-gray mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-white mb-2">{t('kProfiles.noPrintersConfigured')}</h3>
          <p className="text-bambu-gray">
            {t('kProfiles.addPrinterInSettings')}
          </p>
        </CardContent>
      </Card>
    );
  }

  if (connectedPrinters.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <Printer className="w-12 h-12 text-bambu-gray mx-auto mb-4" />
          <h3 className="text-lg font-semibold text-white mb-2">{t('kProfiles.noActivePrinters')}</h3>
          <p className="text-bambu-gray">
            {t('kProfiles.enablePrinterConnection')}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      {/* Loading overlay when refetching profiles (not initial load) */}
      {isFetching && !kprofilesLoading && (
        <div className="fixed inset-0 bg-black/50 flex flex-col items-center justify-center z-40">
          <Loader2 className="w-10 h-10 text-bambu-green animate-spin mb-3" />
          <p className="text-white font-medium">{t('kProfiles.loadingProfiles')}</p>
        </div>
      )}

      {/* Printer & Nozzle Selector */}
      <div className="flex flex-wrap gap-4 mb-6">
        <div className="flex-1 min-w-48">
          <label className="block text-sm text-bambu-gray mb-1">{t('kProfiles.printer')}</label>
          <select
            value={selectedPrinter || ''}
            onChange={(e) => setSelectedPrinter(parseInt(e.target.value))}
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
          >
            {connectedPrinters.map((printer) => (
              <option key={printer.id} value={printer.id}>
                {printer.name}
              </option>
            ))}
          </select>
        </div>

        <div className="w-32">
          <label className="block text-sm text-bambu-gray mb-1">{t('kProfiles.nozzle')}</label>
          <select
            value={nozzleDiameter}
            onChange={(e) => setNozzleDiameter(e.target.value)}
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
          >
            <option value="0.2">0.2mm</option>
            <option value="0.4">0.4mm</option>
            <option value="0.6">0.6mm</option>
            <option value="0.8">0.8mm</option>
          </select>
        </div>

        <div className="flex items-end gap-2">
          <Button
            variant="secondary"
            onClick={() => refetchProfiles()}
            disabled={isFetching || !hasPermission('kprofiles:read')}
            title={!hasPermission('kprofiles:read') ? t('kProfiles.permission.noRead') : undefined}
          >
            <RefreshCw className={`w-4 h-4 ${isFetching ? 'animate-spin' : ''}`} />
            {t('kProfiles.refresh')}
          </Button>
          <Button
            onClick={() => setShowAddModal(true)}
            disabled={!hasPermission('kprofiles:create')}
            title={!hasPermission('kprofiles:create') ? t('kProfiles.permission.noCreate') : undefined}
          >
            <Plus className="w-4 h-4" />
            {t('kProfiles.addProfile')}
          </Button>
        </div>
      </div>

      {/* Search & Filter Row */}
      <div className="flex flex-wrap gap-4 mb-4">
        <div className="flex-1 min-w-48 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t('kProfiles.searchPlaceholder')}
            className="w-full pl-10 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
          />
        </div>
        {isDualNozzle && (
          <div className="w-36">
            <select
              value={extruderFilter}
              onChange={(e) => setExtruderFilter(e.target.value as ExtruderFilter)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="all">{t('kProfiles.allExtruders')}</option>
              <option value="left">{t('kProfiles.leftOnly')}</option>
              <option value="right">{t('kProfiles.rightOnly')}</option>
            </select>
          </div>
        )}
        {supportsFlowType && (
          <div className="w-32">
            <select
              value={flowTypeFilter}
              onChange={(e) => setFlowTypeFilter(e.target.value as FlowTypeFilter)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="all">{t('kProfiles.allFlow')}</option>
              <option value="hf">{t('kProfiles.hfOnly')}</option>
              <option value="s">{t('kProfiles.sOnly')}</option>
            </select>
          </div>
        )}
        <div className="w-32">
          <select
            value={sortOption}
            onChange={(e) => setSortOption(e.target.value as SortOption)}
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
          >
            <option value="name">{t('kProfiles.sortName')}</option>
            <option value="k_value">{t('kProfiles.sortKValue')}</option>
            <option value="filament">{t('kProfiles.sortFilament')}</option>
          </select>
        </div>
      </div>

      {/* Toolbar Row */}
      <div className="flex flex-wrap gap-2 mb-6">
        <Button
          variant="secondary"
          onClick={handleExport}
          disabled={!kprofiles?.profiles?.length || !hasPermission('kprofiles:read')}
          title={!hasPermission('kprofiles:read') ? t('kProfiles.permission.noExport') : undefined}
        >
          <Download className="w-4 h-4" />
          {t('kProfiles.export')}
        </Button>
        <Button
          variant="secondary"
          onClick={handleImport}
          disabled={!hasPermission('kprofiles:create')}
          title={!hasPermission('kprofiles:create') ? t('kProfiles.permission.noImport') : undefined}
        >
          <Upload className="w-4 h-4" />
          {t('kProfiles.import')}
        </Button>
        <div className="flex-1" />
        {selectionMode ? (
          <>
            <Button
              variant="secondary"
              onClick={selectAllProfiles}
            >
              <CheckSquare className="w-4 h-4" />
              {t('kProfiles.selectAll')}
            </Button>
            <Button
              variant="secondary"
              onClick={handleBulkDelete}
              disabled={selectedProfiles.size === 0 || !hasPermission('kprofiles:delete')}
              className="text-red-500 hover:bg-red-500/10"
              title={!hasPermission('kprofiles:delete') ? t('kProfiles.permission.noDelete') : undefined}
            >
              <Trash2 className="w-4 h-4" />
              {t('kProfiles.delete')} ({selectedProfiles.size})
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setSelectionMode(false);
                setSelectedProfiles(new Set());
              }}
            >
              <X className="w-4 h-4" />
              {t('common.cancel')}
            </Button>
          </>
        ) : (
          <Button
            variant="secondary"
            onClick={() => setSelectionMode(true)}
            disabled={!filteredProfiles.length || !hasPermission('kprofiles:delete')}
            title={!hasPermission('kprofiles:delete') ? t('kProfiles.permission.noDelete') : undefined}
          >
            <CheckSquare className="w-4 h-4" />
            {t('kProfiles.select')}
          </Button>
        )}
      </div>

      {/* K-Profiles Grid */}
      {kprofilesLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
        </div>
      ) : isOfflineError ? (
        <Card>
          <CardContent className="py-12 text-center">
            <WifiOff className="w-12 h-12 text-bambu-gray mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-white mb-2">{t('kProfiles.printerOffline')}</h3>
            <p className="text-bambu-gray mb-4">
              {t('kProfiles.printerOfflineDesc')}
            </p>
            <Button variant="secondary" onClick={() => refetchProfiles()}>
              <RefreshCw className="w-4 h-4" />
              {t('common.refresh')}
            </Button>
          </CardContent>
        </Card>
      ) : filteredProfiles.length > 0 ? (
        isDualNozzle ? (
          // Dual-nozzle: show Left/Right columns
          <div className="grid grid-cols-2 gap-4">
            {/* Left Extruder (extruder_id 1 on Bambu) */}
            <div>
              <h3 className="text-sm font-medium text-bambu-gray mb-2 px-1">{t('kProfiles.leftExtruder')}</h3>
              <div className="space-y-1">
                {filteredProfiles
                  .filter((p) => p.extruder_id === 1)
                  .map((profile) => (
                    <KProfileCard
                      key={getProfileKey(profile)}
                      profile={profile}
                      onEdit={() => setEditingProfile(profile)}
                      onCopy={() => setCopyingProfile(profile)}
                      selectionMode={selectionMode}
                      isSelected={selectedProfiles.has(getProfileKey(profile))}
                      onToggleSelect={() => toggleProfileSelection(getProfileKey(profile))}
                      note={getNote(profile)}
                    />
                  ))}
              </div>
            </div>
            {/* Right Extruder (extruder_id 0 on Bambu) */}
            <div>
              <h3 className="text-sm font-medium text-bambu-gray mb-2 px-1">{t('kProfiles.rightExtruder')}</h3>
              <div className="space-y-1">
                {filteredProfiles
                  .filter((p) => p.extruder_id === 0)
                  .map((profile) => (
                    <KProfileCard
                      key={getProfileKey(profile)}
                      profile={profile}
                      onEdit={() => setEditingProfile(profile)}
                      onCopy={() => setCopyingProfile(profile)}
                      selectionMode={selectionMode}
                      isSelected={selectedProfiles.has(getProfileKey(profile))}
                      onToggleSelect={() => toggleProfileSelection(getProfileKey(profile))}
                      note={getNote(profile)}
                    />
                  ))}
              </div>
            </div>
          </div>
        ) : (
          // Single-nozzle: show all profiles in one list
          <div className="space-y-1">
            {filteredProfiles.map((profile) => (
              <KProfileCard
                key={getProfileKey(profile)}
                profile={profile}
                onEdit={() => setEditingProfile(profile)}
                onCopy={() => setCopyingProfile(profile)}
                selectionMode={selectionMode}
                isSelected={selectedProfiles.has(getProfileKey(profile))}
                onToggleSelect={() => toggleProfileSelection(getProfileKey(profile))}
                note={getNote(profile)}
              />
            ))}
          </div>
        )
      ) : searchQuery || extruderFilter !== 'all' || flowTypeFilter !== 'all' ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Search className="w-12 h-12 text-bambu-gray mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-white mb-2">{t('kProfiles.noMatchingProfiles')}</h3>
            <p className="text-bambu-gray">
              {t('kProfiles.noMatchingProfilesDesc')}
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="py-12 text-center">
            <Gauge className="w-12 h-12 text-bambu-gray mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-white mb-2">{t('kProfiles.noKProfiles')}</h3>
            <p className="text-bambu-gray mb-4">
              {t('kProfiles.noKProfilesDesc', { diameter: nozzleDiameter })}
            </p>
            <Button onClick={() => setShowAddModal(true)}>
              <Plus className="w-4 h-4" />
              {t('kProfiles.createFirstProfile')}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Edit Modal */}
      {editingProfile && selectedPrinter && (() => {
        const { note, key } = getNoteWithKey(editingProfile);
        return (
          <KProfileModal
            profile={editingProfile}
            printerId={selectedPrinter}
            nozzleDiameter={nozzleDiameter}
            existingProfiles={kprofiles?.profiles}
            builtinFilaments={enrichedBuiltinFilaments}
            filamentPresets={filamentPresets}
            isDualNozzle={isDualNozzle}
            supportsFlowType={supportsFlowType}
            initialNote={note}
            initialNoteKey={key}
            onSaveNote={handleSaveNote}
            hasPermission={hasPermission}
            onClose={() => {
              console.log('[KProfiles] Edit modal onClose - refetching profiles...');
              setEditingProfile(null);
              refetchProfiles();  // Refetch after close (handles delete case)
            }}
            onSave={() => {
              setEditingProfile(null);
              refetchProfiles();
            }}
          />
        );
      })()}

      {/* Add Modal */}
      {showAddModal && selectedPrinter && (
        <KProfileModal
          printerId={selectedPrinter}
          nozzleDiameter={nozzleDiameter}
          existingProfiles={kprofiles?.profiles}
          builtinFilaments={enrichedBuiltinFilaments}
          filamentPresets={filamentPresets}
          isDualNozzle={isDualNozzle}
          supportsFlowType={supportsFlowType}
          onSaveNote={handleSaveNote}
          hasPermission={hasPermission}
          onClose={() => {
            setShowAddModal(false);
            refetchProfiles();  // Refetch after close
          }}
          onSave={() => {
            setShowAddModal(false);
            refetchProfiles();
          }}
        />
      )}

      {/* Copy Modal - opens add modal with prefilled values from source profile */}
      {copyingProfile && selectedPrinter && (
        <KProfileModal
          printerId={selectedPrinter}
          nozzleDiameter={nozzleDiameter}
          existingProfiles={kprofiles?.profiles}
          builtinFilaments={enrichedBuiltinFilaments}
          filamentPresets={filamentPresets}
          isDualNozzle={isDualNozzle}
          supportsFlowType={supportsFlowType}
          onSaveNote={handleSaveNote}
          hasPermission={hasPermission}
          // Pass profile data but without slot_id to create a new profile
          profile={{
            ...copyingProfile,
            slot_id: 0,  // Force new profile creation
            name: `${copyingProfile.name} (Copy)`,  // Indicate it's a copy
          }}
          onClose={() => {
            setCopyingProfile(null);
            refetchProfiles();
          }}
          onSave={() => {
            setCopyingProfile(null);
            refetchProfiles();
          }}
        />
      )}

      {/* Bulk Delete Confirmation Modal */}
      {showBulkDeleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-sm">
            <CardContent className="p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-red-500/20 flex items-center justify-center">
                  <Trash2 className="w-5 h-5 text-red-500" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white">{t('kProfiles.bulkDelete.title')}</h3>
                  <p className="text-sm text-bambu-gray">{t('kProfiles.bulkDelete.cannotUndo')}</p>
                </div>
              </div>
              <p className="text-bambu-gray mb-6">
                {t('kProfiles.bulkDelete.message', { count: selectedProfiles.size })}
              </p>
              <div className="flex gap-3">
                <Button
                  variant="secondary"
                  onClick={() => setShowBulkDeleteConfirm(false)}
                  disabled={bulkDeleteInProgress}
                  className="flex-1"
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={executeBulkDelete}
                  disabled={bulkDeleteInProgress}
                  className="flex-1 bg-red-500 hover:bg-red-600 text-white"
                >
                  {bulkDeleteInProgress ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Trash2 className="w-4 h-4" />
                  )}
                  {t('common.delete')}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </>
  );
}
