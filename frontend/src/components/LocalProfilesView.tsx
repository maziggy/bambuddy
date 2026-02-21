import { useState, useMemo, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Upload,
  Loader2,
  Search,
  Trash2,
  ChevronDown,
  ChevronUp,
  HardDrive,
  Droplet,
  Settings2,
  Layers,
  AlertCircle,
} from 'lucide-react';
import { api } from '../api/client';
import type { LocalPreset } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';

// Known material types for name-parsing fallback
const MATERIAL_TYPES = ['PLA', 'PETG', 'PCTG', 'ABS', 'ASA', 'TPU', 'PC', 'PA', 'PVA', 'HIPS', 'PP', 'PET', 'NYLON'];

const FILAMENT_TYPE_COLORS: Record<string, string> = {
  PLA: 'E8E8E8', PETG: '4A90D9', ABS: 'E67E22', ASA: 'D35400',
  TPU: '9B59B6', PC: 'BDC3C7', PA: '2ECC71', NYLON: '2ECC71',
  PVA: 'F1C40F', HIPS: '95A5A6', PP: 'ECF0F1', PET: '3498DB',
};

// Extract material type from preset name as fallback
function parseMaterialFromName(name: string): string | null {
  const upper = name.toUpperCase();
  for (const mat of MATERIAL_TYPES) {
    if (new RegExp(`\\b${mat}\\b`).test(upper)) return mat;
  }
  return null;
}

// Extract vendor from preset name (text before the material type)
function parseVendorFromName(name: string): string | null {
  // Strip printer/nozzle suffix first (e.g. "@BBL X1C")
  const clean = name.replace(/@.+$/, '').trim();
  const upper = clean.toUpperCase();
  for (const mat of MATERIAL_TYPES) {
    const idx = upper.indexOf(mat);
    if (idx > 0) {
      const vendor = clean.slice(0, idx).trim();
      // Skip if vendor looks like a generic prefix (e.g., "Generic", "Bambu")
      if (vendor && vendor.length > 1) return vendor;
    }
  }
  return null;
}

function PresetCard({
  preset,
  onDelete,
  onExpand,
  isExpanded,
}: {
  preset: LocalPreset;
  onDelete: (id: number) => void;
  onExpand: (id: number | null) => void;
  isExpanded: boolean;
}) {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();

  // Resolve material type: DB field → parse from name
  const material = preset.filament_type || parseMaterialFromName(preset.name);

  // Resolve vendor: DB field → parse from name
  const vendor = preset.filament_vendor || parseVendorFromName(preset.name);

  // Parse colour for swatch — try explicit colour, then fall back to material type
  let colourHex: string | null = null;
  let hasExplicitColour = false;
  if (preset.default_filament_colour) {
    try {
      const parsed = JSON.parse(preset.default_filament_colour);
      const raw = Array.isArray(parsed) ? parsed[0] : parsed;
      if (typeof raw === 'string' && /^#?[0-9a-fA-F]{6,8}$/.test(raw.replace('#', ''))) {
        colourHex = raw.replace('#', '').slice(0, 6);
        hasExplicitColour = true;
      }
    } catch {
      const raw = preset.default_filament_colour;
      if (/^#?[0-9a-fA-F]{6,8}$/.test(raw.replace('#', ''))) {
        colourHex = raw.replace('#', '').slice(0, 6);
        hasExplicitColour = true;
      }
    }
  }
  if (!colourHex && material) {
    colourHex = FILAMENT_TYPE_COLORS[material.toUpperCase()] || null;
  }

  return (
    <Card className="bg-bambu-dark border-bambu-dark-tertiary hover:border-bambu-dark-tertiary/80 transition-colors">
      <CardContent className="p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              {/* 1) Color dot — always shown for filament presets, dimmed if no explicit colour */}
              {preset.preset_type === 'filament' && (
                <div
                  className={`w-4 h-4 rounded-full border border-white/20 flex-shrink-0 ${
                    !hasExplicitColour && !colourHex ? 'opacity-25' : !hasExplicitColour ? 'opacity-50' : ''
                  }`}
                  style={{ backgroundColor: colourHex ? `#${colourHex}` : '#666' }}
                />
              )}
              <span className="text-sm font-medium text-white truncate">{preset.name}</span>
            </div>

            <div className="flex items-center gap-2 flex-wrap">
              {/* 2) Material tag — fallback to name parsing */}
              {material && (
                <span className="text-xs px-1.5 py-0.5 rounded bg-bambu-green/20 text-bambu-green">
                  {material}
                </span>
              )}
              {/* 3) Vendor — fallback to name parsing */}
              {vendor && (
                <span className="text-xs text-bambu-gray">{vendor}</span>
              )}
              <span className="text-xs px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400">
                {t('profiles.localProfiles.badge')}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-1 flex-shrink-0">
            {/* 4) Only delete, no edit */}
            {hasPermission('settings:update') && (
              <button
                onClick={() => onDelete(preset.id)}
                className="p-1 text-bambu-gray hover:text-red-400 transition-colors"
                title={t('profiles.localProfiles.delete')}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
            <button
              onClick={() => onExpand(isExpanded ? null : preset.id)}
              className="p-1 text-bambu-gray hover:text-white transition-colors"
            >
              {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </button>
          </div>
        </div>

        {/* 5) Expanded detail — show meaningful fields, hide self-inherits */}
        {isExpanded && (
          <div className="mt-3 pt-3 border-t border-bambu-dark-tertiary text-xs space-y-1.5">
            {material && (
              <div className="flex justify-between">
                <span className="text-bambu-gray">{t('profiles.localProfiles.filamentType')}</span>
                <span className="text-white">{material}</span>
              </div>
            )}
            {vendor && (
              <div className="flex justify-between">
                <span className="text-bambu-gray">{t('profiles.localProfiles.vendor')}</span>
                <span className="text-white">{vendor}</span>
              </div>
            )}
            {preset.nozzle_temp_min != null && preset.nozzle_temp_max != null && (
              <div className="flex justify-between">
                <span className="text-bambu-gray">{t('profiles.localProfiles.nozzleTemp')}</span>
                <span className="text-white">{preset.nozzle_temp_min}–{preset.nozzle_temp_max}°C</span>
              </div>
            )}
            {preset.filament_cost && (
              <div className="flex justify-between">
                <span className="text-bambu-gray">{t('profiles.localProfiles.cost')}</span>
                <span className="text-white">{preset.filament_cost}</span>
              </div>
            )}
            {preset.filament_density && (
              <div className="flex justify-between">
                <span className="text-bambu-gray">{t('profiles.localProfiles.density')}</span>
                <span className="text-white">{preset.filament_density} g/cm³</span>
              </div>
            )}
            {preset.pressure_advance && (
              <div className="flex justify-between">
                <span className="text-bambu-gray">{t('profiles.localProfiles.pressureAdvance')}</span>
                <span className="text-white">{preset.pressure_advance}</span>
              </div>
            )}
            {preset.compatible_printers && (
              <div className="flex justify-between">
                <span className="text-bambu-gray">{t('profiles.localProfiles.compatiblePrinters')}</span>
                <span className="text-white truncate ml-2">
                  {(() => { try { return JSON.parse(preset.compatible_printers).join(', '); } catch { return preset.compatible_printers; } })()}
                </span>
              </div>
            )}
            {/* Only show inherits if different from own name */}
            {preset.inherits && preset.inherits !== preset.name && (
              <div className="flex justify-between">
                <span className="text-bambu-gray">{t('profiles.localProfiles.inheritsFrom')}</span>
                <span className="text-white truncate ml-2">{preset.inherits}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="text-bambu-gray">{t('profiles.localProfiles.source')}</span>
              <span className="text-white capitalize">{preset.source}</span>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function LocalProfilesView() {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null);

  const { data: presets, isLoading } = useQuery({
    queryKey: ['localPresets'],
    queryFn: () => api.getLocalPresets(),
  });

  const importMutation = useMutation({
    mutationFn: async (files: FileList) => {
      const results = [];
      for (const file of Array.from(files)) {
        const formData = new FormData();
        formData.append('file', file);
        results.push(await api.importLocalPresets(formData));
      }
      return results;
    },
    onSuccess: (results) => {
      queryClient.invalidateQueries({ queryKey: ['localPresets'] });
      let totalImported = 0;
      let totalSkipped = 0;
      let totalErrors = 0;
      for (const r of results) {
        totalImported += r.imported;
        totalSkipped += r.skipped;
        totalErrors += r.errors.length;
      }

      if (totalImported > 0) {
        showToast(t('profiles.localProfiles.toast.importSuccess', { count: totalImported }));
      }
      if (totalSkipped > 0) {
        showToast(t('profiles.localProfiles.toast.importSkipped', { count: totalSkipped }), 'warning');
      }
      if (totalErrors > 0) {
        showToast(t('profiles.localProfiles.toast.importError', { count: totalErrors }), 'error');
      }
    },
    onError: (err: Error) => {
      showToast(err.message, 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteLocalPreset(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['localPresets'] });
      setDeleteConfirm(null);
      showToast(t('profiles.localProfiles.toast.deleted'));
    },
  });

  const handleFiles = useCallback((files: FileList | null) => {
    if (!files || files.length === 0) return;
    importMutation.mutate(files);
  }, [importMutation]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    handleFiles(e.dataTransfer.files);
  }, [handleFiles]);

  const filterPresets = useCallback((list: LocalPreset[]) => {
    if (!searchQuery) return list;
    const q = searchQuery.toLowerCase();
    return list.filter(p =>
      p.name.toLowerCase().includes(q) ||
      p.filament_type?.toLowerCase().includes(q) ||
      p.filament_vendor?.toLowerCase().includes(q)
    );
  }, [searchQuery]);

  const filaments = useMemo(() => filterPresets(presets?.filament || []), [presets?.filament, filterPresets]);
  const printers = useMemo(() => filterPresets(presets?.printer || []), [presets?.printer, filterPresets]);
  const processes = useMemo(() => filterPresets(presets?.process || []), [presets?.process, filterPresets]);
  const totalCount = filaments.length + printers.length + processes.length;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Import Zone */}
      {hasPermission('settings:update') && (
        <div
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          className={`relative border-2 border-dashed rounded-lg p-6 text-center transition-colors ${
            isDragging
              ? 'border-bambu-green bg-bambu-green/10'
              : 'border-bambu-dark-tertiary hover:border-bambu-gray'
          }`}
        >
          <input
            type="file"
            accept=".json,.zip,.orca_filament,.bbscfg,.bbsflmt"
            multiple
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
            onChange={(e) => handleFiles(e.target.files)}
          />
          {importMutation.isPending ? (
            <div className="flex items-center justify-center gap-2">
              <Loader2 className="w-5 h-5 text-bambu-green animate-spin" />
              <span className="text-bambu-gray">{t('profiles.localProfiles.importing')}</span>
            </div>
          ) : (
            <>
              <Upload className="w-8 h-8 text-bambu-gray mx-auto mb-2" />
              <p className="text-sm text-white font-medium">{t('profiles.localProfiles.import')}</p>
              <p className="text-xs text-bambu-gray mt-1">{t('profiles.localProfiles.importDesc')}</p>
            </>
          )}
        </div>
      )}

      {/* Search Bar */}
      {totalCount > 0 && (
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t('profiles.localProfiles.search')}
            className="w-full pl-9 pr-4 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
          />
        </div>
      )}

      {/* No Presets */}
      {totalCount === 0 && !isLoading && (
        <div className="text-center py-12">
          <HardDrive className="w-12 h-12 text-bambu-gray mx-auto mb-3 opacity-50" />
          <p className="text-bambu-gray">{t('profiles.localProfiles.noPresets')}</p>
          <p className="text-xs text-bambu-gray/60 mt-1">{t('profiles.localProfiles.importDesc')}</p>
        </div>
      )}

      {/* 3-Column Preset Lists */}
      {totalCount > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {/* Filament Column */}
          {filaments.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <Droplet className="w-4 h-4 text-bambu-green" />
                <h3 className="text-sm font-medium text-white">
                  {t('profiles.localProfiles.filament')}
                </h3>
                <span className="text-xs text-bambu-gray">({filaments.length})</span>
              </div>
              <div className="space-y-2">
                {filaments.map(p => (
                  <PresetCard
                    key={p.id}
                    preset={p}
                    onDelete={(id) => setDeleteConfirm(id)}
                    onExpand={setExpandedId}
                    isExpanded={expandedId === p.id}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Process Column */}
          {processes.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <Layers className="w-4 h-4 text-blue-400" />
                <h3 className="text-sm font-medium text-white">
                  {t('profiles.localProfiles.process')}
                </h3>
                <span className="text-xs text-bambu-gray">({processes.length})</span>
              </div>
              <div className="space-y-2">
                {processes.map(p => (
                  <PresetCard
                    key={p.id}
                    preset={p}
                    onDelete={(id) => setDeleteConfirm(id)}
                    onExpand={setExpandedId}
                    isExpanded={expandedId === p.id}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Printer Column */}
          {printers.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <Settings2 className="w-4 h-4 text-orange-400" />
                <h3 className="text-sm font-medium text-white">
                  {t('profiles.localProfiles.printer')}
                </h3>
                <span className="text-xs text-bambu-gray">({printers.length})</span>
              </div>
              <div className="space-y-2">
                {printers.map(p => (
                  <PresetCard
                    key={p.id}
                    preset={p}
                    onDelete={(id) => setDeleteConfirm(id)}
                    onExpand={setExpandedId}
                    isExpanded={expandedId === p.id}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteConfirm !== null && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-6 max-w-sm mx-4">
            <div className="flex items-center gap-2 mb-3">
              <AlertCircle className="w-5 h-5 text-red-400" />
              <h3 className="text-white font-medium">{t('profiles.localProfiles.deleteConfirmTitle')}</h3>
            </div>
            <p className="text-sm text-bambu-gray mb-4">{t('profiles.localProfiles.deleteConfirm')}</p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => setDeleteConfirm(null)}>
                {t('profiles.localProfiles.cancel')}
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => deleteMutation.mutate(deleteConfirm)}
                disabled={deleteMutation.isPending}
              >
                {deleteMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                {t('profiles.localProfiles.delete')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
