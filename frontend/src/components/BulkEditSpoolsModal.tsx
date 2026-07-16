import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { X, Loader2, ChevronDown } from 'lucide-react';
import { api } from '../api/client';
import type { InventorySpool } from '../api/client';
import { Button } from './Button';
import { MATERIALS, DEFAULT_BRANDS, KNOWN_VARIANTS } from './spool-form/constants';
import { buildFilamentOptions } from './spool-form/utils';

/** Subset of InventorySpool fields the bulk-edit modal can patch.
 *  Mirrors the agreed set discussed for #1795 — flat per-spool fields only;
 *  K-profile editing stays per-spool.
 */
type EditableField =
  | 'material'
  | 'subtype'
  | 'brand'
  | 'color_name'
  | 'rgba'
  | 'location_id'
  | 'slicer_filament_name'
  | 'slicer_filament'
  | 'cost_per_kg'
  | 'note'
  | 'label_weight'
  | 'core_weight'
  | 'category'
  | 'low_stock_threshold_pct';

type FieldSpec = {
  id: EditableField;
  /** searchable = custom dropdown with text input + filtered options (free text allowed).
   *  searchableClosed = same but no custom value (must pick from list — used for storage_location).
   *  text = plain text input.
   *  number = number input.
   *  color = colour picker + hex input.
   *  textarea = multi-line text. */
  type: 'searchable' | 'searchableClosed' | 'text' | 'number' | 'color' | 'textarea';
  labelKey: string;
  min?: number;
  max?: number;
  step?: number;
  /** Hex pattern for the rgba field. */
  pattern?: string;
};

const FIELDS: FieldSpec[] = [
  { id: 'material', type: 'searchable', labelKey: 'inventory.material' },
  { id: 'subtype', type: 'searchable', labelKey: 'inventory.subtype' },
  { id: 'brand', type: 'searchable', labelKey: 'inventory.brand' },
  { id: 'color_name', type: 'text', labelKey: 'inventory.colorName' },
  { id: 'rgba', type: 'color', labelKey: 'inventory.color', pattern: '^[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$' },
  { id: 'location_id', type: 'searchableClosed', labelKey: 'inventory.storageLocation' },
  { id: 'slicer_filament_name', type: 'searchable', labelKey: 'inventory.slicerFilamentName' },
  { id: 'slicer_filament', type: 'searchable', labelKey: 'inventory.slicerFilament' },
  { id: 'cost_per_kg', type: 'number', labelKey: 'inventory.costPerKg', min: 0, step: 0.01 },
  { id: 'note', type: 'textarea', labelKey: 'inventory.note' },
  { id: 'label_weight', type: 'number', labelKey: 'inventory.labelWeight', min: 1, step: 1 },
  { id: 'core_weight', type: 'number', labelKey: 'inventory.coreWeight', min: 0, step: 1 },
  { id: 'category', type: 'searchable', labelKey: 'inventory.category' },
  { id: 'low_stock_threshold_pct', type: 'number', labelKey: 'inventory.lowStockThresholdOverride', min: 1, max: 99, step: 1 },
];

export interface BulkEditSpoolsModalProps {
  isOpen: boolean;
  selectedCount: number;
  isPending: boolean;
  availableLocations: Array<{ id: number; name: string }>;
  /** Materials seen in the user's inventory — combined with the MATERIALS constant for suggestions. */
  availableMaterials: string[];
  availableSubtypes: string[];
  availableBrands: string[];
  availableCategories: string[];
  availableSlicerFilaments: string[];
  availableSlicerFilamentNames: string[];
  onClose: () => void;
  onApply: (patch: Partial<Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'>>) => void;
}

interface Option {
  value: string;
  label: string;
}

interface SearchableSelectProps {
  value: string;
  onChange: (next: string) => void;
  options: Option[];
  /** When true the user can also type a value not present in the option list. */
  allowCustom: boolean;
  placeholderKey?: string;
  disabled?: boolean;
}

/** Lightweight searchable dropdown matching the per-spool form's pattern —
 *  text input + chevron + filtered list of buttons, click-outside closes.
 *  Native `<select>` is intentionally avoided per the project's UI conventions. */
function SearchableSelect({ value, onChange, options, allowCustom, placeholderKey, disabled }: SearchableSelectProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setSearch('');
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        setSearch('');
      }
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onEsc);
    };
  }, [open]);

  const displayValue = (() => {
    if (open) return search;
    const match = options.find((o) => o.value === value);
    return match?.label ?? value;
  })();

  const filteredOptions = useMemo(() => {
    if (!open) return options;
    const q = search.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q));
  }, [open, search, options]);

  const noOptionMatch = open && search.trim() && !options.some((o) => o.value.toLowerCase() === search.trim().toLowerCase());

  return (
    <div className="relative" ref={ref}>
      <input
        type="text"
        disabled={disabled}
        value={displayValue}
        onChange={(e) => {
          setSearch(e.target.value);
          setOpen(true);
          if (allowCustom) onChange(e.target.value);
        }}
        onFocus={() => {
          setOpen(true);
          setSearch('');
        }}
        placeholder={placeholderKey ? t(placeholderKey) : undefined}
        className="w-full px-3 py-2 pr-9 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray/50 focus:border-bambu-green focus:outline-none"
      />
      <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50 pointer-events-none" />
      {open && (
        <div className="absolute z-50 left-0 right-0 mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-64 overflow-y-auto">
          {filteredOptions.length === 0 && !allowCustom && (
            <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.noResults')}</div>
          )}
          {filteredOptions.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
                value === opt.value ? 'bg-bambu-green/10 text-bambu-green' : 'text-white'
              }`}
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
                setSearch('');
              }}
            >
              {opt.label}
            </button>
          ))}
          {allowCustom && noOptionMatch && (
            <button
              type="button"
              className="w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary text-bambu-green border-t border-bambu-dark-tertiary"
              onClick={() => {
                onChange(search.trim());
                setOpen(false);
                setSearch('');
              }}
            >
              {t('inventory.bulk.useCustom', { value: search.trim() })}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function combineUnique(...lists: string[][]): string[] {
  const set = new Set<string>();
  for (const list of lists) for (const v of list) {
    const trimmed = v?.trim();
    if (trimmed) set.add(trimmed);
  }
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

export function BulkEditSpoolsModal({
  isOpen, selectedCount, isPending,
  availableLocations, availableMaterials, availableSubtypes, availableBrands, availableCategories,
  availableSlicerFilaments, availableSlicerFilamentNames,
  onClose, onApply,
}: BulkEditSpoolsModalProps) {
  const { t } = useTranslation();

  // Slicer preset sources — match the per-spool form (cloud Bambu + cloud Orca
  // + local + built-in). Gated on `isOpen` so closed modal doesn't fetch.
  const { data: cloudPresets = [] } = useQuery({
    queryKey: ['bulk-edit-cloud-presets'],
    enabled: isOpen,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const out: Awaited<ReturnType<typeof api.getFilamentPresets>> = [];
      try {
        const status = await api.getCloudStatus();
        if (status.is_authenticated) {
          const bambu = await api.getFilamentPresets();
          out.push(...bambu);
        }
      } catch {/* cloud offline → empty */}
      try {
        const orca = await api.orcaCloudStatus();
        if (orca.connected) {
          const list = await api.orcaCloudListProfiles();
          out.push(...(list.filament as unknown as typeof out));
        }
      } catch {/* orca offline → empty */}
      return out;
    },
  });
  const { data: localPresetsResp } = useQuery({
    queryKey: ['bulk-edit-local-presets'],
    enabled: isOpen,
    staleTime: 5 * 60 * 1000,
    queryFn: api.getLocalPresets,
  });
  const { data: builtinFilaments = [] } = useQuery({
    queryKey: ['builtin-filaments'],
    enabled: isOpen,
    staleTime: 5 * 60 * 1000,
    queryFn: api.getBuiltinFilaments,
  });
  const filamentOptions = useMemo(
    () => buildFilamentOptions(cloudPresets, new Set(), localPresetsResp?.filament ?? [], builtinFilaments),
    [cloudPresets, localPresetsResp, builtinFilaments],
  );
  // Per-field state: each entry is either undefined (leave unchanged) or
  // the new value. Clearing fields in bulk is intentionally NOT supported
  // (user decision on #1795): leave clearing to the per-spool editor so
  // an accidental "blank everything" isn't a single mis-click away.
  const [values, setValues] = useState<Record<string, string>>({});

  // Merge inventory-seen values with the canonical option lists so users
  // see the same dropdown choices the per-spool editor surfaces.
  const materialOptions: Option[] = useMemo(
    () => combineUnique(MATERIALS, availableMaterials).map((m) => ({ value: m, label: m })),
    [availableMaterials],
  );
  const subtypeOptions: Option[] = useMemo(
    () => combineUnique(KNOWN_VARIANTS, availableSubtypes).map((m) => ({ value: m, label: m })),
    [availableSubtypes],
  );
  const brandOptions: Option[] = useMemo(
    () => combineUnique(DEFAULT_BRANDS, availableBrands).map((m) => ({ value: m, label: m })),
    [availableBrands],
  );
  const categoryOptions: Option[] = useMemo(
    () => combineUnique(availableCategories).map((m) => ({ value: m, label: m })),
    [availableCategories],
  );
  const slicerFilamentOptions: Option[] = useMemo(() => {
    // value = preset code (what goes into spool.slicer_filament),
    // label = display name so the user can find it by name.
    const fromPresets = filamentOptions.map((p) => ({ value: p.code, label: p.displayName }));
    const fromInventory = availableSlicerFilaments
      .filter((code) => !fromPresets.some((p) => p.value === code))
      .map((code) => ({ value: code, label: code }));
    return [...fromPresets, ...fromInventory].sort((a, b) => a.label.localeCompare(b.label));
  }, [filamentOptions, availableSlicerFilaments]);
  const slicerFilamentNameOptions: Option[] = useMemo(() => {
    const fromPresets = filamentOptions.map((p) => ({ value: p.displayName, label: p.displayName }));
    const fromInventory = availableSlicerFilamentNames
      .filter((name) => !fromPresets.some((p) => p.value === name))
      .map((name) => ({ value: name, label: name }));
    return [...fromPresets, ...fromInventory].sort((a, b) => a.label.localeCompare(b.label));
  }, [filamentOptions, availableSlicerFilamentNames]);
  const locationOptions: Option[] = useMemo(
    () => availableLocations.map((l) => ({ value: String(l.id), label: l.name })),
    [availableLocations],
  );

  if (!isOpen) return null;

  const setField = (id: EditableField, value: string) => {
    setValues((prev) => ({ ...prev, [id]: value }));
  };

  const unsetField = (id: EditableField) => {
    setValues((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  const buildPatch = (): Record<string, string | number> => {
    const patch: Record<string, string | number> = {};
    for (const f of FIELDS) {
      const raw = values[f.id];
      if (raw === undefined) continue;
      const trimmed = typeof raw === 'string' ? raw.trim() : raw;
      if (trimmed === '' || trimmed === null) continue;
      if (f.type === 'number') {
        const n = Number(trimmed);
        if (Number.isFinite(n)) patch[f.id] = n;
      } else if (f.id === 'location_id') {
        const n = Number(trimmed);
        if (Number.isFinite(n) && n > 0) patch[f.id] = n;
      } else if (f.id === 'rgba') {
        const hex = String(trimmed).replace(/^#/, '');
        const normalized = hex.length === 6 ? `${hex}FF` : hex;
        if (/^[0-9A-Fa-f]{8}$/.test(normalized)) patch[f.id] = normalized.toUpperCase();
      } else {
        patch[f.id] = String(trimmed);
      }
    }
    return patch;
  };

  const patch = buildPatch();
  const hasChanges = Object.keys(patch).length > 0;
  // Block Apply when any ticked-and-non-empty field has invalid input that
  // would be silently dropped from the patch — e.g. a malformed rgba hex.
  // Without this guard the user clicks Apply, the field is dropped, and the
  // success toast still fires for the OTHER fields.
  const hasDroppedTickedField = FIELDS.some((f) => {
    const raw = values[f.id];
    if (raw === undefined) return false;
    if (raw.trim() === '') return false;
    return patch[f.id] === undefined;
  });

  const optionsFor = (id: EditableField): Option[] => {
    if (id === 'material') return materialOptions;
    if (id === 'subtype') return subtypeOptions;
    if (id === 'brand') return brandOptions;
    if (id === 'category') return categoryOptions;
    if (id === 'slicer_filament') return slicerFilamentOptions;
    if (id === 'slicer_filament_name') return slicerFilamentNameOptions;
    if (id === 'location_id') return locationOptions;
    return [];
  };

  const renderInput = (f: FieldSpec) => {
    const value = values[f.id] ?? '';

    if (f.type === 'searchable' || f.type === 'searchableClosed') {
      return (
        <SearchableSelect
          value={value}
          onChange={(next) => {
            if (next === '') unsetField(f.id);
            else setField(f.id, next);
          }}
          options={optionsFor(f.id)}
          allowCustom={f.type === 'searchable'}
          disabled={isPending}
        />
      );
    }

    if (f.type === 'textarea') {
      return (
        <textarea
          disabled={isPending}
          value={value}
          onChange={(e) => setField(f.id, e.target.value)}
          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray/50 focus:border-bambu-green focus:outline-none resize-none min-h-[60px]"
        />
      );
    }

    if (f.type === 'color') {
      const hexCandidate = value.trim().replace(/^#/, '');
      const normalized = hexCandidate.length === 6 ? `${hexCandidate}FF` : hexCandidate;
      const isInvalid = value.trim() !== '' && !/^[0-9A-Fa-f]{8}$/.test(normalized);
      return (
        <div>
          <div className="flex items-center gap-2">
            <input
              type="color"
              disabled={isPending}
              value={`#${(value || '808080').replace(/^#/, '').slice(0, 6)}`}
              onChange={(e) => setField(f.id, e.target.value.replace(/^#/, '').toUpperCase())}
              className="h-9 w-12 rounded cursor-pointer"
            />
            <input
              type="text"
              disabled={isPending}
              value={value}
              onChange={(e) => setField(f.id, e.target.value.replace(/^#/, '').toUpperCase())}
              placeholder="RRGGBB or RRGGBBAA"
              className={`flex-1 px-3 py-2 bg-bambu-dark border rounded-lg text-white placeholder-bambu-gray/50 focus:outline-none ${isInvalid ? 'border-red-500 focus:border-red-500' : 'border-bambu-dark-tertiary focus:border-bambu-green'}`}
              pattern={f.pattern}
            />
          </div>
          {isInvalid && (
            <p className="mt-1 text-xs text-red-600 dark:text-red-400">{t('inventory.bulk.invalidHex')}</p>
          )}
        </div>
      );
    }

    return (
      <input
        type={f.type === 'number' ? 'number' : 'text'}
        disabled={isPending}
        value={value}
        onChange={(e) => setField(f.id, e.target.value)}
        min={f.min}
        max={f.max}
        step={f.step}
        className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray/50 focus:border-bambu-green focus:outline-none"
      />
    );
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50"
      onClick={isPending ? undefined : onClose}
    >
      <div
        className="w-full max-w-3xl bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-5 border-b border-bambu-dark-tertiary">
          <div>
            <h2 className="text-lg font-semibold text-white">
              {t('inventory.bulk.editTitle')}
            </h2>
            <p className="text-sm text-bambu-gray mt-0.5">
              {t('inventory.bulk.editSubtitle', { count: selectedCount })}
            </p>
          </div>
          <button
            onClick={onClose}
            disabled={isPending}
            className="p-1 text-bambu-gray hover:text-white transition-colors"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <p className="px-5 pt-4 text-xs text-bambu-gray">
          {t('inventory.bulk.editHint')}
        </p>
        <div className="flex-1 overflow-y-auto p-5 space-y-3">
          {FIELDS.map((f) => {
            const enabled = values[f.id] !== undefined;
            return (
              <div key={f.id} className={`flex items-start gap-3 rounded-md p-2 transition-colors ${enabled ? 'bg-bambu-green/5 border border-bambu-green/30' : 'border border-transparent'}`}>
                <div className="pt-2">
                  <input
                    type="checkbox"
                    className="h-4 w-4 cursor-pointer"
                    checked={enabled}
                    onChange={(e) => {
                      if (e.target.checked) setField(f.id, '');
                      else unsetField(f.id);
                    }}
                    aria-label={t('inventory.bulk.toggleField')}
                  />
                </div>
                <div className="flex-1">
                  <label className="block text-sm text-bambu-gray mb-1">
                    {t(f.labelKey)}
                  </label>
                  {renderInput(f)}
                </div>
              </div>
            );
          })}
        </div>

        <div className="flex items-center gap-3 p-5 border-t border-bambu-dark-tertiary">
          <span className="text-xs text-bambu-gray">
            {t('inventory.bulk.changeCount', { count: Object.keys(patch).length })}
          </span>
          <div className="ml-auto flex gap-2">
            <Button variant="secondary" onClick={onClose} disabled={isPending}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => onApply(patch as Partial<Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'>>)}
              disabled={!hasChanges || isPending || hasDroppedTickedField}
            >
              {isPending ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  {t('inventory.bulk.applyPending')}
                </>
              ) : (
                t('inventory.bulk.applyButton', { count: selectedCount })
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
