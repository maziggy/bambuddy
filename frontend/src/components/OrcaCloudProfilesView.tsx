import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { TFunction } from 'i18next';
import { Search, Filter, RefreshCw, Droplet, Settings2, Printer as PrinterIcon, Layers, X, Loader2, Clock } from 'lucide-react';

import { api } from '../api/client';
import type { OrcaProfileListResponse, OrcaProfileMeta, Printer } from '../api/client';
import { Button } from './Button';
import { FilterDropdown } from '../pages/ProfilesPage';
import { formatRelativeTime } from '../utils/date';

/**
 * Read-only profile browser for the Orca Cloud tab.
 *
 * Visual parity with Bambu Cloud's CloudProfilesView: same filter bar layout,
 * same 3-column grouped list (Filament / Process / Printer), same card
 * styling. Differences vs the Bambu version are removals — no
 * Create / Edit / Duplicate / Delete / Compare / Templates buttons because
 * Orca's `/sync/push` and `/sync/delete` endpoints aren't wired in this
 * shipping cut (planned for a follow-up). The "Owner" filter is also
 * dropped: every profile that lives in a user's Orca Cloud account is
 * user-authored, the system/builtin distinction doesn't apply.
 */

type ProfileType = 'all' | 'filament' | 'printer' | 'process';

interface PresetMeta {
  printer: string | null;
  nozzle: string | null;
  layerHeight: string | null;
  filamentType: string | null;
}

// Mirror of ProfilesPage.tsx::extractMetadata. Inlined rather than exported
// because the patterns are conservative (Bambu printer-name regex etc.) and
// Orca profile naming follows the same conventions (Orca is a BambuStudio
// fork; profile names like "Bambu PLA Basic @BBL X1C" carry over verbatim).
function extractMetadata(name: string): PresetMeta {
  const printerMatch = name.match(/@?\s*(?:BBL\s+)?(?:Bambu\s+Lab\s+)?([XPAH][1-9][A-Z]?(?:\s*(?:Carbon|mini))?|H2D)/i);
  const nozzleMatch = name.match(/(\d+\.?\d*)\s*(?:mm\s*)?nozzle|nozzle\s*(\d+\.?\d*)/i);
  const layerMatch = name.match(/(\d+\.?\d*)mm\s*(?:Standard|Fine|Extra Fine|Draft|Quality)?/i);
  const filamentMatch = name.match(/\b(PLA|PETG|ABS|ASA|TPU|PC|PA|PVA|HIPS|PP|PET(?:-?CF)?|PA(?:-?CF)?|PLA(?:-?CF)?)\b/i);
  return {
    printer: printerMatch ? printerMatch[1].trim() : null,
    nozzle: nozzleMatch ? (nozzleMatch[1] || nozzleMatch[2]) + 'mm' : null,
    layerHeight: layerMatch ? layerMatch[1] + 'mm' : null,
    filamentType: filamentMatch ? filamentMatch[1].toUpperCase() : null,
  };
}

interface OrcaCloudProfilesViewProps {
  settings: OrcaProfileListResponse;
  lastSyncTime?: Date;
  onRefresh: () => void;
  isRefreshing: boolean;
  printers: Printer[];
  t: TFunction;
}

export function OrcaCloudProfilesView({
  settings,
  lastSyncTime,
  onRefresh,
  isRefreshing,
  printers,
  t,
}: OrcaCloudProfilesViewProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [filterType, setFilterType] = useState<ProfileType>('all');
  const [filterPrinter, setFilterPrinter] = useState('all');
  const [filterNozzle, setFilterNozzle] = useState('all');
  const [filterFilament, setFilterFilament] = useState('all');
  const [filterLayerHeight, setFilterLayerHeight] = useState('all');
  const [selectedSetting, setSelectedSetting] = useState<OrcaProfileMeta | null>(null);

  const allPresetsWithMeta = useMemo(() => {
    const combined = [
      ...settings.filament.map(s => ({ ...s, type: 'filament' as const })),
      ...settings.printer.map(s => ({ ...s, type: 'printer' as const })),
      ...settings.process.map(s => ({ ...s, type: 'process' as const })),
    ];
    return combined.map(s => ({ ...s, meta: extractMetadata(s.name) }));
  }, [settings]);

  const filterOptions = useMemo(() => {
    const nozzles = new Set<string>();
    const filaments = new Set<string>();
    const layerHeights = new Set<string>();
    allPresetsWithMeta.forEach(p => {
      if (p.meta.nozzle) nozzles.add(p.meta.nozzle);
      if (p.meta.filamentType) filaments.add(p.meta.filamentType);
      if (p.meta.layerHeight) layerHeights.add(p.meta.layerHeight);
    });
    return {
      printers: printers.map(p => ({ id: p.id.toString(), name: p.name })),
      nozzles: Array.from(nozzles).sort((a, b) => parseFloat(a) - parseFloat(b)),
      filaments: Array.from(filaments).sort(),
      layerHeights: Array.from(layerHeights).sort((a, b) => parseFloat(a) - parseFloat(b)),
    };
  }, [allPresetsWithMeta, printers]);

  const selectedPrinterModel = useMemo(() => {
    if (filterPrinter === 'all') return null;
    const printer = printers.find(p => p.id.toString() === filterPrinter);
    return printer?.model || null;
  }, [filterPrinter, printers]);

  const filteredPresets = useMemo(() => {
    return allPresetsWithMeta
      .filter(s => filterType === 'all' || s.type === filterType)
      .filter(s => {
        if (filterPrinter === 'all' || !selectedPrinterModel) return true;
        const presetPrinter = s.meta.printer?.toLowerCase() || '';
        const configuredModel = selectedPrinterModel.toLowerCase();
        return presetPrinter.includes(configuredModel) || configuredModel.includes(presetPrinter);
      })
      .filter(s => filterNozzle === 'all' || s.meta.nozzle === filterNozzle)
      .filter(s => filterFilament === 'all' || s.meta.filamentType === filterFilament)
      .filter(s => filterLayerHeight === 'all' || s.meta.layerHeight === filterLayerHeight)
      .filter(s => searchQuery === '' || s.name.toLowerCase().includes(searchQuery.toLowerCase()))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [allPresetsWithMeta, filterType, filterPrinter, selectedPrinterModel, filterNozzle, filterFilament, filterLayerHeight, searchQuery]);

  const clearFilters = () => {
    setFilterType('all');
    setFilterPrinter('all');
    setFilterNozzle('all');
    setFilterFilament('all');
    setFilterLayerHeight('all');
    setSearchQuery('');
  };

  const hasActiveFilters =
    filterType !== 'all' ||
    filterPrinter !== 'all' ||
    filterNozzle !== 'all' ||
    filterFilament !== 'all' ||
    filterLayerHeight !== 'all' ||
    searchQuery !== '';

  const totalCount = settings.filament.length + settings.printer.length + settings.process.length;

  const presetsByType = (type: ProfileType) => filteredPresets.filter(p => p.type === type);

  return (
    <>
      {/* Search and Filters — mirrors the layout of profiles.cloudView in the
          Bambu Cloud tab so the two tabs feel like the same surface. */}
      <div className="space-y-4 mb-6">
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('profiles.cloudView.searchPlaceholder')}
              className="w-full pl-10 pr-4 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray-dark focus:border-bambu-green focus:outline-none"
            />
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onRefresh} disabled={isRefreshing}>
              <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} />
              {t('profiles.cloudView.refresh')}
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Filter className="w-4 h-4 text-bambu-gray" />
          <FilterDropdown
            label={t('profiles.cloudView.filters.type')}
            value={filterType}
            options={[
              { value: 'all', label: t('profiles.cloudView.filters.all'), count: totalCount },
              { value: 'filament', label: t('profiles.cloudView.filters.filament'), count: settings.filament.length },
              { value: 'printer', label: t('profiles.cloudView.filters.printer'), count: settings.printer.length },
              { value: 'process', label: t('profiles.cloudView.filters.process'), count: settings.process.length },
            ]}
            onChange={(v) => setFilterType(v as ProfileType)}
          />
          {filterOptions.printers.length > 0 && (
            <FilterDropdown
              label={t('profiles.cloudView.filters.printer')}
              value={filterPrinter}
              options={[
                { value: 'all', label: t('profiles.cloudView.filters.all') },
                ...filterOptions.printers.map(p => ({ value: p.id, label: p.name })),
              ]}
              onChange={setFilterPrinter}
            />
          )}
          {filterOptions.nozzles.length > 0 && (
            <FilterDropdown
              label={t('profiles.cloudView.filters.nozzle')}
              value={filterNozzle}
              options={[
                { value: 'all', label: t('profiles.cloudView.filters.all') },
                ...filterOptions.nozzles.map(n => ({ value: n, label: n })),
              ]}
              onChange={setFilterNozzle}
            />
          )}
          {filterOptions.filaments.length > 0 && (filterType === 'all' || filterType === 'filament') && (
            <FilterDropdown
              label={t('profiles.cloudView.filters.filament')}
              value={filterFilament}
              options={[
                { value: 'all', label: t('profiles.cloudView.filters.all') },
                ...filterOptions.filaments.map(f => ({ value: f, label: f })),
              ]}
              onChange={setFilterFilament}
            />
          )}
          {filterOptions.layerHeights.length > 0 && (filterType === 'all' || filterType === 'process') && (
            <FilterDropdown
              label={t('profiles.cloudView.filters.layer')}
              value={filterLayerHeight}
              options={[
                { value: 'all', label: t('profiles.cloudView.filters.all') },
                ...filterOptions.layerHeights.map(l => ({ value: l, label: l })),
              ]}
              onChange={setFilterLayerHeight}
            />
          )}
          {hasActiveFilters && (
            <button
              onClick={clearFilters}
              className="px-3 py-2 text-sm text-bambu-gray hover:text-white transition-colors"
            >
              {t('profiles.cloudView.clearFilters')}
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-4 mb-4 text-sm text-bambu-gray">
        {lastSyncTime && (
          <div className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {t('profiles.cloudView.lastSynced')} {formatRelativeTime(lastSyncTime.toISOString(), 'system', t)}
          </div>
        )}
        <span>{t('profiles.cloudView.showingCount', { showing: filteredPresets.length, total: totalCount })}</span>
      </div>

      {filteredPresets.length === 0 ? (
        <div className="text-center py-16">
          <Layers className="w-12 h-12 text-bambu-gray-dark mx-auto mb-4" />
          <p className="text-bambu-gray">{t('profiles.cloudView.noPresetsFound')}</p>
          {hasActiveFilters && (
            <button onClick={clearFilters} className="mt-2 text-sm text-bambu-green hover:text-bambu-green-light">
              {t('profiles.cloudView.clearFilters')}
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <PresetColumn
            icon={<Droplet className="w-4 h-4 text-amber-400" />}
            title={t('profiles.cloudView.columns.filament')}
            presets={presetsByType('filament')}
            emptyText={t('profiles.cloudView.noFilamentPresets')}
            onSelect={setSelectedSetting}
          />
          <PresetColumn
            icon={<Settings2 className="w-4 h-4 text-blue-400" />}
            title={t('profiles.cloudView.columns.process')}
            presets={presetsByType('process')}
            emptyText={t('profiles.cloudView.noProcessPresets')}
            onSelect={setSelectedSetting}
          />
          <PresetColumn
            icon={<PrinterIcon className="w-4 h-4 text-purple-400" />}
            title={t('profiles.cloudView.columns.printer')}
            presets={presetsByType('printer')}
            emptyText={t('profiles.cloudView.noPrinterPresets')}
            onSelect={setSelectedSetting}
          />
        </div>
      )}

      {selectedSetting && (
        <OrcaPresetDetailModal
          setting={selectedSetting}
          onClose={() => setSelectedSetting(null)}
          t={t}
        />
      )}
    </>
  );
}

interface PresetColumnProps {
  icon: React.ReactNode;
  title: string;
  presets: (OrcaProfileMeta & { meta: PresetMeta })[];
  emptyText: string;
  onSelect: (preset: OrcaProfileMeta) => void;
}

function PresetColumn({ icon, title, presets, emptyText, onSelect }: PresetColumnProps) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-3 px-1">
        {icon}
        <h3 className="text-sm font-medium text-bambu-gray">{title}</h3>
        <span className="text-xs text-bambu-gray-dark">({presets.length})</span>
      </div>
      <div className="space-y-1 max-h-[calc(100vh-320px)] overflow-y-auto pr-1">
        {presets.length === 0 ? (
          <p className="text-xs text-bambu-gray-dark px-3 py-2">{emptyText}</p>
        ) : (
          presets.map((preset) => (
            <PresetCard key={preset.setting_id} preset={preset} onClick={() => onSelect(preset)} />
          ))
        )}
      </div>
    </div>
  );
}

function PresetCard({
  preset,
  onClick,
}: {
  preset: OrcaProfileMeta & { meta: PresetMeta };
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="w-full text-left px-3 py-2 rounded bg-bambu-dark hover:bg-bambu-dark-tertiary transition-colors"
    >
      <div className="flex items-center gap-2">
        <span className="text-white text-sm truncate flex-1" title={preset.name}>
          {preset.name}
        </span>
        {preset.meta.filamentType && preset.type === 'filament' && (
          <span className="text-xs text-bambu-gray whitespace-nowrap">{preset.meta.filamentType}</span>
        )}
        {preset.meta.layerHeight && preset.type === 'process' && (
          <span className="text-xs text-bambu-gray whitespace-nowrap">{preset.meta.layerHeight}</span>
        )}
        {preset.meta.printer && (
          <span className="text-xs text-bambu-gray whitespace-nowrap">{preset.meta.printer}</span>
        )}
      </div>
    </button>
  );
}

interface OrcaPresetDetailModalProps {
  setting: OrcaProfileMeta;
  onClose: () => void;
  t: TFunction;
}

function OrcaPresetDetailModal({ setting, onClose, t }: OrcaPresetDetailModalProps) {
  const { data: detail, isLoading, error } = useQuery({
    queryKey: ['orcaCloudProfileDetail', setting.setting_id],
    queryFn: () => api.orcaCloudGetProfile(setting.setting_id),
    retry: false,
  });

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-2xl max-w-4xl w-full max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div>
            <h2 className="text-lg font-bold text-white">{setting.name}</h2>
            <p className="text-xs text-bambu-gray mt-0.5">{setting.type}</p>
          </div>
          <button onClick={onClose} className="text-bambu-gray hover:text-white p-1">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
            </div>
          ) : error ? (
            <p className="text-center text-bambu-gray py-16">{(error as Error).message}</p>
          ) : detail ? (
            <pre className="text-xs font-mono text-bambu-gray bg-bambu-dark p-3 rounded overflow-x-auto whitespace-pre">
              {JSON.stringify(detail.setting, null, 2)}
            </pre>
          ) : (
            <p className="text-center text-bambu-gray py-16">{t('profiles.cloudView.noPresetsFound')}</p>
          )}
        </div>
      </div>
    </div>
  );
}
