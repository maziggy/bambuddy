/**
 * Process-settings editor mirroring OrcaSlicer's own Print Settings tabs.
 *
 * Structure, labels, tooltips, bounds, defaults and enable/disable rules all
 * come from metadata extracted from OrcaSlicer's C++ sources (see
 * `src/data/slicer/`), so the pages, groups and ordering match what users see
 * in the desktop slicer rather than a hand-picked subset.
 *
 * Option labels and tooltips are deliberately English-only for now: they are
 * 348 strings lifted verbatim from `PrintConfig.cpp`, and hand-translating them
 * into all 13 locales is not viable. The panel's own chrome — mode switch,
 * search, buttons, empty states — goes through i18n as usual. OrcaSlicer ships
 * its own translation catalogs for these strings, which is the obvious source
 * if they are ever picked up.
 *
 * Values are held sparsely: only options the user actually changed are tracked
 * and sent, so a slice with an untouched panel is byte-identical to one from
 * before this panel existed.
 */

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, RotateCcw, Loader2, ChevronDown } from 'lucide-react';

import { disabledKeys, type ToggleRules } from '../lib/slicerToggle';
import { baselineForDisplay, displaySidetext, isModified, numericBound, serializeOverrides } from '../lib/slicerSettings';
import type { OptionMode, ProcessOption, ProcessSchema, ProcessUiTree, SettingValue } from '../types/slicerSettings';
import type { DesignOverride } from '../types/plates';
import type { SlicerPresetValuesReason } from '../api/client';

interface SlicerData {
  schema: ProcessSchema;
  tree: ProcessUiTree;
  toggles: ToggleRules;
}

interface Props {
  values: Record<string, SettingValue>;
  /**
   * Reports both the panel's editing state and the same values serialised for
   * the slice request. Serialising here rather than in the caller keeps the
   * option schema — the only thing that knows a percent needs its `%` back —
   * in the one component that has already loaded it.
   *
   * `serialized` carries only options that actually differ from their default,
   * so an untouched panel sends nothing at all.
   */
  onChange: (values: Record<string, SettingValue>, serialized: Record<string, string | string[]>) => void;
  disabled?: boolean;
  /**
   * Process settings the source 3MF's designer moved off the stock preset
   * (#2622), as recorded by BambuStudio in `different_settings_to_system`.
   *
   * These are shown inline against the options they belong to rather than in a
   * list of their own, so there is one place to see what this slice will use.
   * Their *values* are not routed through this component: the backend reads
   * them straight out of the file, which keeps settings faithful even for keys
   * outside the option schema we vendor. All this panel decides is which of
   * them are switched on.
   */
  sourceOverrides?: DesignOverride[];
  /** Which source-override keys are currently switched on. */
  sourceSelected?: Set<string>;
  onToggleSource?: (key: string, on: boolean) => void;
  /**
   * The filaments picked on the slice dialog's left-hand side, in slot order.
   *
   * A handful of options select *which filament* prints a given feature —
   * supports, outer walls, infill. The slicer stores those as a plain integer
   * where 0 means "whatever filament the region already uses" and 1..N is a
   * slot. A bare number field makes the user count their own AMS slots, so
   * when this is supplied those options become a dropdown of the actual
   * picks instead.
   */
  filamentChoices?: FilamentChoice[];
  /**
   * The picked process preset's effective values, flattened by the sidecar.
   * Used as the baseline an untouched field shows and a revert returns to.
   * Empty when unavailable, in which case the panel falls back to the option
   * schema's compiled-in defaults and says the values are indicative.
   */
  presetValues?: Record<string, SettingValue>;
  /** False when the preset's values could not be fetched. */
  presetValuesResolved?: boolean;
  /**
   * Why they could not be fetched, so the notice can name a fix. Left
   * unset while the fetch is still in flight.
   */
  presetValuesReason?: SlicerPresetValuesReason;
}

export interface FilamentChoice {
  /** 1-based slot index, matching the integer the slicer stores. */
  index: number;
  /** Preset name, or a fallback when the slot has no pick yet. */
  label: string;
  /** Slot colour from the source plate, for the swatch. */
  color?: string;
}

/**
 * Options whose integer value names a filament slot rather than a quantity.
 * All use the same encoding: 0 = "default / current filament", 1..N = slot.
 * Support base and interface are the pair on the Support page; the rest are
 * the Multimaterial page's per-region pickers, which have the same wart.
 */
const FILAMENT_SLOT_OPTIONS = new Set([
  'support_filament',
  'support_interface_filament',
  'outer_wall_filament_id',
  'inner_wall_filament_id',
  'top_surface_filament_id',
  'bottom_surface_filament_id',
  'internal_solid_filament_id',
  'sparse_infill_filament_id',
]);

/** Visibility tiers, in increasing order of how much they reveal. */
const MODES: OptionMode[] = ['simple', 'advanced', 'expert'];
const MODE_RANK: Record<string, number> = { simple: 0, advanced: 1, expert: 2, develop: 3 };

export default function SlicerSettingsPanel({
  values,
  onChange,
  disabled = false,
  sourceOverrides = [],
  sourceSelected,
  onToggleSource,
  filamentChoices,
  presetValues,
  presetValuesResolved = true,
  presetValuesReason,
}: Props) {
  const { t } = useTranslation();
  const [data, setData] = useState<SlicerData | null>(null);
  const [mode, setMode] = useState<OptionMode>('simple');
  const [page, setPage] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  // 150 KB of extracted metadata has no business in the main bundle — it is
  // only needed once someone opens this panel.
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      import('../data/slicer/process-schema.json'),
      import('../data/slicer/process-ui-tree.json'),
      import('../data/slicer/process-toggle-rules.json'),
    ]).then(([schema, tree, toggles]) => {
      if (cancelled) return;
      setData({
        schema: (schema.default ?? schema) as unknown as ProcessSchema,
        tree: (tree.default ?? tree) as unknown as ProcessUiTree,
        toggles: (toggles.default ?? toggles) as unknown as ToggleRules,
      });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // What this slice will actually run with, in the same precedence order the
  // rows display: the picked preset underneath, the designer's value for each
  // key that is switched on, and anything typed here on top.
  const effectiveValues = useMemo(() => {
    const merged: Record<string, SettingValue> = { ...(presetValues ?? {}) };
    for (const o of sourceOverrides) {
      if (sourceSelected?.has(o.key)) merged[o.key] = o.value as SettingValue;
    }
    // An emptied field is not a value — leaving it in would read as "" and
    // send the config reader to the schema default, past the preset.
    for (const [key, value] of Object.entries(values)) {
      if (value !== undefined && value !== '') merged[key] = value;
    }
    return merged;
  }, [presetValues, sourceOverrides, sourceSelected, values]);

  // The slicer's own `enable_if` rules, evaluated against that rather than
  // against `values` alone (#2942). `values` holds only what the user typed
  // here, and the config reader falls back to the *schema* default for
  // everything else — so a preset with supports on read as
  // `enable_support: false` and greyed out the whole Support page, including
  // rows whose "from file" tick was on and whose value the slice used. A
  // greyed row used to grey its tick too, which left a setting that came from
  // the file, that the slice applied, and that nothing on screen could
  // switch off.
  const off = useMemo(
    () => (data ? disabledKeys(effectiveValues, data.schema, data.toggles) : new Set<string>()),
    [data, effectiveValues],
  );

  const sourceByKey = useMemo(
    () => new Map(sourceOverrides.map((o) => [o.key, o])),
    [sourceOverrides],
  );

  // The subset a bulk "use the designer's settings" may switch on: everything
  // the file changed except the values tuned for the designer's own machine
  // and the two that *are* the picked preset. Those two classes stay a
  // per-key decision, which is the classification #2622 made and this does
  // not widen.
  const carryableSource = useMemo(
    () => sourceOverrides.filter((o) => !o.printer_coupled && !o.preset_defining),
    [sourceOverrides],
  );

  const selectedSourceCount = useMemo(
    () => sourceOverrides.filter((o) => sourceSelected?.has(o.key)).length,
    [sourceOverrides, sourceSelected],
  );

  // Source overrides for keys the vendored schema doesn't cover. They still
  // apply — the backend reads their values from the file — so they get a group
  // of their own rather than being dropped from view.
  const unlistedSource = useMemo(() => {
    if (!data) return [];
    return sourceOverrides.filter((o) => !data.schema[o.key]);
  }, [data, sourceOverrides]);

  const emit = (next: Record<string, SettingValue>) => {
    if (!data) return;
    // Only genuine deviations are worth sending: an override that equals the
    // preset's own value is noise in the process JSON and makes the slice
    // request harder to read when something goes wrong.
    const changed: Record<string, SettingValue> = {};
    for (const [k, v] of Object.entries(next)) {
      if (data.schema[k] && isModified(data.schema[k], v, presetValues?.[k])) changed[k] = v;
    }
    onChange(next, serializeOverrides(changed, data.schema));
  };

  const setValue = (key: string, value: SettingValue | undefined) => {
    const next = { ...values };
    if (value === undefined) delete next[key];
    else next[key] = value;
    emit(next);
  };

  // Search cuts across every page; without a query we show the selected page.
  const visiblePages = useMemo(() => {
    if (!data) return [];
    // Underscores and spaces are interchangeable so "outer wall speed" finds
    // `outer_wall_speed`. That matters more than it looks: several labels are
    // only meaningful with their group ("Outer wall" under Speed), so the key
    // is often the only place the full phrase appears.
    const flatten = (s: string) => s.toLowerCase().replace(/[_\s]+/g, ' ').trim();
    const needle = flatten(query);
    const withinMode = (key: string) => MODE_RANK[data.schema[key]?.mode ?? 'expert'] <= MODE_RANK[mode];
    const matches = (key: string, group: string, page: string) => {
      if (!needle) return true;
      const opt = data.schema[key];
      // Group and page are matched too, so "speed" lists the Speed page's
      // options rather than only the handful with "speed" in their label.
      const haystack = [key, opt?.label ?? '', opt?.tooltip ?? '', group, page];
      return haystack.some((h) => flatten(h).includes(needle));
    };

    return data.tree
      .map((p) => ({
        ...p,
        groups: p.groups
          .map((g) => ({
            ...g,
            options: g.options.filter((k) => withinMode(k) && matches(k, g.group, p.page)),
          }))
          .filter((g) => g.options.length > 0),
      }))
      .filter((p) => p.groups.length > 0);
  }, [data, mode, query]);

  const activePage = useMemo(() => {
    if (visiblePages.length === 0) return null;
    if (query.trim()) return null; // Searching shows every match, not one page.
    return visiblePages.find((p) => p.page === page) ?? visiblePages[0];
  }, [visiblePages, page, query]);

  const modifiedCount = useMemo(() => {
    if (!data) return 0;
    return Object.keys(values).filter((k) => data.schema[k] && isModified(data.schema[k], values[k], presetValues?.[k])).length;
  }, [data, values, presetValues]);

  if (!data) {
    return (
      <div className="flex items-center justify-center gap-2 py-8 text-sm text-bambu-gray">
        <Loader2 className="w-4 h-4 animate-spin" />
        {t('slicerSettings.loading', 'Loading slicer settings...')}
      </div>
    );
  }

  const shownPages = activePage ? [activePage] : visiblePages;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex overflow-hidden rounded border border-bambu-dark-tertiary">
          {MODES.map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              disabled={disabled}
              className={`px-2.5 py-1 text-xs capitalize transition-colors ${
                mode === m ? 'bg-bambu-green text-white' : 'text-bambu-gray hover:text-white'
              }`}
            >
              {t(`slicerSettings.mode.${m}`, m)}
            </button>
          ))}
        </div>

        <div className="relative flex-1 min-w-[10rem]">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-bambu-gray" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            disabled={disabled}
            placeholder={t('slicerSettings.searchPlaceholder', 'Search settings')}
            className="w-full rounded border border-bambu-dark-tertiary bg-bambu-dark pl-7 pr-2 py-1 text-xs text-white placeholder:text-bambu-gray/60 focus:border-bambu-green focus:outline-none disabled:opacity-40"
          />
        </div>

        {modifiedCount > 0 && (
          <button
            type="button"
            onClick={() => emit({})}
            disabled={disabled}
            className="flex items-center gap-1 text-xs text-bambu-gray hover:text-white"
          >
            <RotateCcw className="w-3 h-3" />
            {t('slicerSettings.resetAll', 'Reset {{count}}', { count: modifiedCount })}
          </button>
        )}
      </div>

      {!presetValuesResolved && (
        <p className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-[0.7rem] text-amber-800 dark:border-amber-700/40 dark:bg-amber-900/20 dark:text-amber-200">
          {presetValuesReason === 'sidecar_outdated'
            ? t(
                'slicerSettings.presetValuesOutdatedSidecar',
                "Showing slicer defaults: your slicer sidecar is older than this feature and can't report a preset's values. Update the sidecar image to see them. Anything you don't change still uses the preset.",
              )
            : presetValuesReason === 'not_configured'
              ? t(
                  'slicerSettings.presetValuesNotConfigured',
                  "Showing slicer defaults: no slicer sidecar is configured, so a preset's values can't be read. Anything you don't change still uses the preset.",
                )
              : presetValuesReason === 'sidecar_unavailable'
                ? t(
                    'slicerSettings.presetValuesSidecarUnavailable',
                    "Showing slicer defaults: the slicer sidecar did not answer, so a preset's values can't be read. Anything you don't change still uses the preset.",
                  )
                : t(
                    'slicerSettings.presetValuesUnavailable',
                    "Showing slicer defaults: the picked preset's own values could not be read. Anything you don't change still uses the preset.",
                  )}
        </p>
      )}

      {/* What the file brings, and the only bulk way to take it. Nothing here
          is pre-ticked any more (#2942), so without this line the designer's
          settings would be reachable only by hunting for green chips across
          six pages of 348 options. "Use them" ticks the keys that carry
          across printers; the machine-tuned ones and the two that define the
          picked preset stay off, as they always have. */}
      {sourceOverrides.length > 0 && onToggleSource && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-bambu-dark-tertiary px-2 py-1.5 text-[0.7rem] text-bambu-gray">
          <span className="min-w-0 flex-1">
            {t(
              'slicerSettings.fromFileSummary',
              'The designer changed {{count}} process settings in this file. Only the ones you tick are used.',
              { count: sourceOverrides.length },
            )}
          </span>
          <button
            type="button"
            disabled={disabled}
            onClick={() => carryableSource.forEach((o) => onToggleSource(o.key, true))}
            title={t(
              'slicerSettings.fromFileUseAllHint',
              "Ticks the settings that carry across printers. The ones tuned for the designer's own printer, and the ones that define the preset you picked, stay off.",
            )}
            className="shrink-0 rounded border border-bambu-dark-tertiary px-1.5 py-0.5 hover:text-white disabled:opacity-40"
          >
            {t('slicerSettings.fromFileUseAll', "Use the designer's settings")}
          </button>
          {selectedSourceCount > 0 && (
            <button
              type="button"
              disabled={disabled}
              onClick={() => sourceOverrides.forEach((o) => onToggleSource(o.key, false))}
              className="shrink-0 rounded border border-bambu-dark-tertiary px-1.5 py-0.5 hover:text-white disabled:opacity-40"
            >
              {t('slicerSettings.fromFileClear', 'Clear {{count}}', { count: selectedSourceCount })}
            </button>
          )}
        </div>
      )}

      {!query.trim() && (
        <div className="flex flex-wrap gap-1">
          {visiblePages.map((p) => (
            <button
              key={p.page}
              type="button"
              onClick={() => setPage(p.page)}
              disabled={disabled}
              className={`px-2 py-1 text-xs rounded transition-colors ${
                activePage?.page === p.page ? 'bg-bambu-dark-tertiary text-white' : 'text-bambu-gray hover:text-white'
              }`}
            >
              {p.page}
            </button>
          ))}
        </div>
      )}

      {shownPages.length === 0 ? (
        <p className="py-6 text-center text-xs text-bambu-gray">
          {t('slicerSettings.noMatches', 'No settings match this search.')}
        </p>
      ) : (
        // Taller once the panel has a column of its own; the narrow cap keeps
        // it from swallowing the single-column stack on small screens.
        <div className="flex flex-col gap-4 max-h-[22rem] lg:max-h-[58vh] overflow-y-auto pr-1">
          {shownPages.map((p) => (
            <div key={p.page} className="flex flex-col gap-3">
              {query.trim() && <p className="text-[0.7rem] uppercase tracking-wide text-bambu-gray/70">{p.page}</p>}
              {p.groups.map((g) => (
                <fieldset key={`${p.page}:${g.group}`} className="flex flex-col gap-1.5">
                  <legend className="mb-1 text-xs font-medium text-white">{g.group}</legend>
                  {g.options.map((key) => (
                    <OptionRow
                      key={key}
                      optionKey={key}
                      option={data.schema[key]}
                      value={values[key]}
                      onChange={(v) => setValue(key, v)}
                      disabled={disabled || off.has(key)}
                      disabledBySlicer={off.has(key)}
                      formDisabled={disabled}
                      source={sourceByKey.get(key)}
                      sourceOn={sourceSelected?.has(key) ?? false}
                      onToggleSource={onToggleSource}
                      filamentChoices={FILAMENT_SLOT_OPTIONS.has(key) ? filamentChoices : undefined}
                      presetValue={presetValues?.[key]}
                    />
                  ))}
                </fieldset>
              ))}
            </div>
          ))}

          {/* Source-file settings the vendored schema has no entry for: they
              still apply (the backend reads their values from the file), so
              they get a plain key/value group rather than disappearing from a
              panel that claims to show what this slice will use. */}
          {unlistedSource.length > 0 && !query.trim() && (
            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-1 text-xs font-medium text-white">
                {t('slicerSettings.otherFromFile', 'Other settings from this file')}
              </legend>
              {unlistedSource.map((o) => (
                <label key={o.key} className="flex items-center gap-2 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    checked={sourceSelected?.has(o.key) ?? false}
                    disabled={disabled || !onToggleSource}
                    onChange={(e) => onToggleSource?.(o.key, e.target.checked)}
                    className="shrink-0 cursor-pointer disabled:opacity-40"
                  />
                  <span className="min-w-0 flex-1 truncate">
                    <span className="font-mono text-bambu-gray">{o.key}</span>
                    <span className="ml-1.5 text-white">{formatSourceValue(o.value)}</span>
                  </span>
                  {(o.printer_coupled || o.preset_defining) && (
                    <span className="shrink-0 rounded bg-amber-100 px-1 py-0.5 text-[10px] text-amber-700 dark:bg-amber-500/20 dark:text-amber-400">
                      {o.printer_coupled
                        ? t('slicerSettings.fromFilePrinterCoupled', "designer's printer")
                        : t('slicerSettings.fromFileOverridesPreset', 'overrides preset')}
                    </span>
                  )}
                </label>
              ))}
            </fieldset>
          )}
        </div>
      )}
    </div>
  );
}

interface RowProps {
  optionKey: string;
  option: ProcessOption;
  value: SettingValue | undefined;
  onChange: (value: SettingValue | undefined) => void;
  disabled: boolean;
  /** Greyed because the slicer's own rules turn it off, not because the form is busy. */
  disabledBySlicer: boolean;
  /**
   * The panel-wide disabled state, without the slicer's per-option rules.
   *
   * Gates the "from file" tick, which answers a different question from the
   * control beside it: not "is this option in play" but "where does its value
   * come from". An option the slicer has switched off can still be one the
   * user wants the file's value for once it comes back into play, and folding
   * the two together is what made a ticked source setting unclearable (#2942).
   */
  formDisabled: boolean;
  /** Set when the source file's designer moved this option off the stock preset. */
  source?: DesignOverride;
  sourceOn?: boolean;
  onToggleSource?: (key: string, on: boolean) => void;
  /** Set only for options whose integer value names a filament slot. */
  filamentChoices?: FilamentChoice[];
  /** The picked preset's value for this option, when known. */
  presetValue?: SettingValue;
}

function OptionRow({
  optionKey,
  option,
  value,
  onChange,
  disabled,
  disabledBySlicer,
  formDisabled,
  source,
  sourceOn = false,
  onToggleSource,
  filamentChoices,
  presetValue,
}: RowProps) {
  const { t } = useTranslation();
  const modified = isModified(option, value, presetValue);
  const unit = displaySidetext(option);
  // What this slice will actually use, in precedence order: a value typed here
  // wins, then the designer's value if it is switched on, then the preset's own
  // (or the schema default when the preset's values are unavailable).
  const current =
    value !== undefined
      ? String(value)
      : sourceOn && source
        ? formatSourceValue(source.value)
        : baselineForDisplay(option, presetValue);

  return (
    <div className="flex items-center gap-2 group" title={option.tooltip}>
      {/* Label takes the slack; the control group is a fixed width anchored to
          the right edge. Fixed widths on the control *and* the unit are what
          keep that column straight — sizing either to content makes each row's
          input land at a different x. */}
      <label
        htmlFor={`slicer-opt-${optionKey}`}
        className={`flex min-w-0 flex-1 items-center gap-1 text-xs ${disabledBySlicer ? 'text-bambu-gray/40' : 'text-bambu-gray'}`}
      >
        {/* Own title: a fixed column truncates more than the old flex-1 label
            did, and the row's title carries the tooltip, not the name. */}
        <span className="truncate" title={option.label || optionKey}>
          {option.label || optionKey}
        </span>
        {modified && <span className="shrink-0 text-bambu-green" aria-hidden="true">•</span>}
        {source && (
          <span
            className={`shrink-0 rounded px-1 py-0.5 text-[10px] ${
              source.printer_coupled || source.preset_defining
                ? 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-400'
                : 'bg-bambu-green/15 text-bambu-green'
            }`}
            title={
              source.printer_coupled
                ? t('slicerSettings.fromFilePrinterCoupledHint', "Tuned for the printer this file was designed for -- may be wrong or out of range on yours.")
                : source.preset_defining
                  ? t(
                      'slicerSettings.fromFileOverridesPresetHint',
                      'The file sets this to {{value}} where the preset you picked uses {{preset}}. Tick it only if the file should win.',
                      { value: formatSourceValue(source.value), preset: baselineForDisplay(option, presetValue) },
                    )
                  : t('slicerSettings.fromFileHint', "The designer changed this in the source file. Its value is {{value}}.", { value: formatSourceValue(source.value) })
            }
          >
            {source.printer_coupled
              ? t('slicerSettings.fromFilePrinterCoupled', "designer's printer")
              : source.preset_defining
                ? t('slicerSettings.fromFileOverridesPreset', 'overrides preset')
                : t('slicerSettings.fromFile', 'from file')}
          </span>
        )}
      </label>

      <div className="flex shrink-0 items-center gap-1.5">
        {/* The "use the file's value" tick comes *before* the control it
            qualifies, as a checkbox that gates a field conventionally does —
            it used to sit past the unit, out at the right edge, reading as
            unrelated to the field. The slot is reserved on every row so rows
            with and without a source override keep the control column
            straight. */}
        <span className="flex w-3 shrink-0 justify-center">
          {source && onToggleSource && (
            <input
              type="checkbox"
              checked={sourceOn}
              disabled={formDisabled}
              onChange={(e) => onToggleSource(optionKey, e.target.checked)}
              aria-label={t('slicerSettings.useFromFile', "Use the source file's value for {{option}}", {
                option: option.label || optionKey,
              })}
              title={t('slicerSettings.useFromFile', "Use the source file's value for {{option}}", {
                option: option.label || optionKey,
              })}
              className="w-3 h-3 cursor-pointer disabled:opacity-40"
            />
          )}
        </span>
        <div className="w-40">
          <OptionControl
            id={`slicer-opt-${optionKey}`}
            option={option}
            current={current}
            onChange={onChange}
            disabled={disabled}
            filamentChoices={filamentChoices}
          />
        </div>
        {/* Fixed width so the control column stays straight, but wide enough
            for the longest unit in the schema ("mm/s² or %") — a narrower cap
            truncated those to "mm o...". Rendered even when empty so rows
            without a unit keep the revert button aligned. */}
        <span className="w-16 shrink-0 whitespace-nowrap text-[0.65rem] text-bambu-gray/60">{unit ?? ''}</span>
        <button
          type="button"
          onClick={() => onChange(undefined)}
          disabled={disabled || !modified}
          aria-label={t('slicerSettings.resetOption', 'Reset to default')}
          className={`p-0.5 transition-opacity ${modified ? 'text-bambu-gray hover:text-white' : 'opacity-0 pointer-events-none'}`}
        >
          <RotateCcw className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}

/**
 * Render a value read out of the source file. Bambu's process config stores
 * everything as strings or arrays of strings, so this only has to flatten
 * arrays — no unit or type interpretation, which would rot against every
 * slicer release.
 */
function formatSourceValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((v) => String(v)).join(', ');
  if (value == null) return '';
  return String(value);
}

interface ControlProps {
  id: string;
  option: ProcessOption;
  current: string;
  onChange: (value: SettingValue | undefined) => void;
  disabled: boolean;
  filamentChoices?: FilamentChoice[];
}

function OptionControl({ id, option, current, onChange, disabled, filamentChoices }: ControlProps) {
  const { t } = useTranslation();
  // Theme tokens rather than raw black/white: bambu-dark and
  // bambu-dark-tertiary are CSS variables that follow the active theme, and
  // `text-white` is remapped to --text-primary in index.css.
  const inputClass =
    'w-full rounded border border-bambu-dark-tertiary bg-bambu-dark px-1.5 py-0.5 text-xs text-white focus:border-bambu-green focus:outline-none disabled:opacity-40';

  // Filament-slot pickers come before the generic branches: the value is an
  // integer, but offering a spinner over "1, 2, 3" makes the user map slot
  // numbers to their own AMS by hand.
  if (filamentChoices && filamentChoices.length > 0) {
    const selected = filamentChoices.find((c) => String(c.index) === current);
    return (
      <div className="relative w-full">
        <select
          id={id}
          value={current}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className={`${inputClass} w-full cursor-pointer appearance-none pr-5`}
          // The full name rarely fits in the control, so the hover carries it.
          title={selected?.label}
        >
          {/* 0 is the slicer's "no specific filament — use the region's own". */}
          <option value="0">{t('slicerSettings.filamentDefault', 'Default')}</option>
          {filamentChoices.map((choice) => (
            <option key={choice.index} value={String(choice.index)}>
              {choice.index}: {choice.label}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-1.5 top-1/2 h-3 w-3 -translate-y-1/2 text-bambu-gray" />
      </div>
    );
  }

  if (option.type === 'coBool') {
    return (
      <input
        id={id}
        type="checkbox"
        checked={current === '1' || current === 'true'}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
        className="w-3.5 h-3.5 cursor-pointer disabled:opacity-40"
      />
    );
  }

  if (option.type === 'coEnum' && option.enum_values) {
    // Native select chrome is replaced the same way as everywhere else in
    // Bambuddy: appearance-none plus our own chevron, so the control matches
    // the app in both themes instead of whatever the browser paints.
    return (
      <div className="relative w-full">
        <select
          id={id}
          value={current}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className={`${inputClass} w-full cursor-pointer appearance-none pr-5`}
        >
          {option.enum_values.map((v, i) => (
            <option key={v} value={v}>
              {option.enum_labels?.[i] ?? v}
            </option>
          ))}
        </select>
        <ChevronDown className="pointer-events-none absolute right-1.5 top-1/2 h-3 w-3 -translate-y-1/2 text-bambu-gray" />
      </div>
    );
  }

  if (option.type === 'coInt' || option.type === 'coFloat' || option.type === 'coPercent') {
    return (
      <input
        id={id}
        type="number"
        value={current.replace('%', '')}
        min={numericBound(option.min)}
        max={numericBound(option.max)}
        step={option.type === 'coInt' ? 1 : 'any'}
        // An empty field is kept as an empty string rather than dropped.
        // Dropping it would fall the input straight back to the default, so
        // clearing a value to retype it would silently append to the old one.
        // Empty never counts as modified, so nothing is sent for it either way;
        // the revert button is what actually removes the key.
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className={inputClass}
      />
    );
  }

  // coFloatOrPercent, the vector types and coString all accept free text: they
  // hold values like "50%", "0.42" or a comma-separated per-extruder list, none
  // of which a number input can represent.
  return (
    <input
      id={id}
      type="text"
      value={current}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className={inputClass}
    />
  );
}
