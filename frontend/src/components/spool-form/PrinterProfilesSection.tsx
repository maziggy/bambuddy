import { Fragment, useMemo } from 'react';
import { Check, Loader2, Printer as PrinterIcon, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type {
  CalibrationProfile,
  FilamentOption,
  PrinterProfilesSectionProps,
  PrinterWithCalibrations,
} from './types';
import { hotendKey, isMatchingCalibration, presetKey } from './utils';
import { STANDARD_NOZZLE_DIAMETERS } from './constants';
import { PresetPicker } from './PresetPicker';
import { extractPresetModel, matchesPrinterModelSuffix } from '../../utils/slicerPrinterMatch';
import { flowLabel, normaliseFlow } from '../../utils/nozzleFlow';
import type { NozzleFlow } from '../../utils/nozzleFlow';

/**
 * The spool form's Printers tab: which filament preset this spool uses on each
 * printer MODEL, and which K profile it uses on each individual hotend.
 *
 * The two halves are keyed differently on purpose. A slicer preset is a
 * property of the model -- "@BBL X1C" is the same preset on every X1C the user
 * owns -- so asking per machine would make them pick the identical value once
 * per printer. A K value is measured on one individual hotend, so it stays per
 * printer, per extruder, per nozzle diameter, which is what both K tables have
 * always been keyed on.
 *
 * Layout is a model list plus a detail pane rather than a stack of cards: the
 * list is fixed height whatever the fleet size, and the detail pane is bounded
 * by the largest single model instead of by the total number of printers.
 *
 * Nothing here reads `status.nozzles[]` positionally. Which array index belongs
 * to which extruder is genuinely unsettled in the backend (the H2/X2 and legacy
 * MQTT parsers disagree), so every nozzle fact on this screen comes from data
 * that names its own extruder: a calibration profile carries both its
 * `extruder_id` and its `nozzle_diameter`, and the per-model diameter list is a
 * deduplicated SET, which no ordering can get wrong.
 */

interface ModelGroup {
  /**
   * Identifies the row. Prefixed so the two kinds cannot collide: `m:` for a
   * real model, `p:` for a printer that has not reported one. Distinct from
   * `model` because a model-less printer has no model to be keyed by, and two
   * of them would otherwise be the same row.
   */
  id: string;
  /** Empty when the printer has not reported a model. */
  model: string;
  printers: PrinterWithCalibrations[];
  /** Distinct nozzle diameters across this model's machines. Order-independent. */
  diameters: string[];
}

/**
 * The flow type fitted to one hotend, or null when the printer does not say.
 *
 * Same array and the same indexing as the diameter. Legacy printers put the
 * nozzle MATERIAL in this field ("hardened_steel"), which normaliseFlow reads
 * as "unknown" -- correct, since those machines never report a flow.
 */
function fittedFlow(entry: PrinterWithCalibrations, extruder: number): NozzleFlow | null {
  const nozzles = entry.nozzles ?? [];
  const isDual = (entry.printer.nozzle_count ?? 1) > 1;
  const index = isDual && extruder > 0 ? extruder : 0;
  return normaliseFlow(nozzles[index]?.nozzle_type) ?? normaliseFlow(nozzles[0]?.nozzle_type);
}

function distinctDiameters(entry: PrinterWithCalibrations): string[] {
  const seen = new Set<string>();
  for (const nozzle of entry.nozzles ?? []) {
    const raw = (nozzle?.nozzle_diameter ?? '').trim();
    if (raw && parseFloat(raw) > 0) seen.add(raw);
  }
  for (const cal of entry.calibrations) {
    const raw = (cal.nozzle_diameter ?? '').trim();
    if (raw && parseFloat(raw) > 0) seen.add(raw);
  }
  return Array.from(seen).sort((a, b) => parseFloat(a) - parseFloat(b));
}

/**
 * The hotend columns for one printer, in the order they sit on the machine.
 *
 * Extruder 0 is the RIGHT hotend and 1 is the left, so a left-to-right table
 * reads [1, 0]. A single-nozzle machine has one unnamed column -- there is no
 * side to name.
 */
function columnsOf(
  entry: PrinterWithCalibrations,
  labels: { left: string; right: string; single: string },
): Array<{ extruder: number; label: string }> {
  if ((entry.printer.nozzle_count ?? 1) > 1) {
    return [
      { extruder: 1, label: labels.left },
      { extruder: 0, label: labels.right },
    ];
  }
  return [{ extruder: 0, label: labels.single }];
}

export function PrinterProfilesSection({
  formData,
  printersWithCalibrations,
  filamentOptions,
  modelPresets,
  setModelPresets,
  selectedProfiles,
  setSelectedProfiles,
  selectedGroupId,
  setSelectedGroupId,
  printerModels,
  isLoading = false,
}: PrinterProfilesSectionProps) {
  const { t } = useTranslation();

  // Group the fleet by model. A printer whose model the backend has not
  // reported is grouped under its own name rather than dropped -- it still has
  // K profiles worth setting, and the preset row is disabled for it below.
  const groups = useMemo<ModelGroup[]>(() => {
    const byModel = new Map<string, PrinterWithCalibrations[]>();
    const modelless: PrinterWithCalibrations[] = [];
    for (const entry of printersWithCalibrations) {
      const model = (entry.printer.model || '').trim();
      if (!model) {
        modelless.push(entry);
        continue;
      }
      const list = byModel.get(model);
      if (list) list.push(entry);
      else byModel.set(model, [entry]);
    }

    const grouped = Array.from(byModel.entries())
      .map(([model, printers]) => ({
        id: `m:${model}`,
        model,
        printers,
        // Every standard size, plus anything unusual this model reports as
        // fitted. Not just the fitted ones: a spool is configured once and
        // nozzles get swapped, so the user has to be able to set the preset
        // for a size they are about to change to.
        diameters: Array.from(
          new Set([...STANDARD_NOZZLE_DIAMETERS, ...printers.flatMap(distinctDiameters)]),
        ).sort((a, b) => parseFloat(a) - parseFloat(b)),
      }))
      .sort((a, b) => a.model.localeCompare(b.model));

    // A printer that has not reported its model gets a row of its own, last:
    // it has K profiles worth setting but cannot share a preset with anything,
    // and it must not be folded in with other model-less printers.
    return [
      ...grouped,
      ...modelless.map(entry => ({
        id: `p:${entry.printer.id}`,
        model: '',
        printers: [entry],
        diameters: Array.from(
          new Set([...STANDARD_NOZZLE_DIAMETERS, ...distinctDiameters(entry)]),
        ).sort((a, b) => parseFloat(a) - parseFloat(b)),
      })),
    ];
  }, [printersWithCalibrations]);

  const active = groups.find(g => g.id === selectedGroupId) ?? groups[0];

  /**
   * The presets worth offering for one model.
   *
   * A preset name carries the model it belongs to ("@BBL H2C", "@Bambu Lab X1
   * Carbon", or just "X1C ..." at the front), and offering an X1C preset for an
   * H2C is offering something that machine has no profile for -- which is the
   * bug this whole tab exists to fix. Uses the same matcher the Configure AMS
   * Slot modal filters with, so the two lists agree.
   *
   * Two things are deliberately kept: a preset whose model cannot be read at
   * all (many user-authored and Orca presets name no model), because hiding
   * what we cannot classify would hide most third-party profiles; and whatever
   * is currently selected, so an override already saved never silently
   * disappears from the control that shows it.
   */
  const optionsForModel = useMemo(() => {
    const cache = new Map<string, FilamentOption[]>();
    return (model: string, selected: string | undefined): FilamentOption[] => {
      if (!model) return filamentOptions;
      let list = cache.get(model);
      if (!list) {
        list = filamentOptions.filter(option => {
          const presetModel = extractPresetModel(option.name, printerModels ?? {});
          return !presetModel || matchesPrinterModelSuffix(presetModel, model);
        });
        cache.set(model, list);
      }
      if (selected && !list.some(o => o.code === selected)) {
        const kept = filamentOptions.find(o => o.code === selected);
        if (kept) return [kept, ...list];
      }
      return list;
    };
  }, [filamentOptions, printerModels]);

  // "Bambu PLA Matte", from whichever of the three fields are filled in. Blank
  // parts are skipped rather than padded with "Any brand", which reads as a
  // filter setting rather than as what the spool is.
  const identity = [formData.brand, formData.material, formData.subtype]
    .map(part => part.trim())
    .filter(Boolean)
    .join(' ');

  // The spool's own colour. rgba is RRGGBBAA; the alpha is dropped because a
  // translucent swatch would show the panel behind it rather than the filament.
  const swatch = /^[0-9A-Fa-f]{6,8}$/.test(formData.rgba)
    ? `#${formData.rgba.slice(0, 6)}`
    : 'var(--bambu-gray, #808080)';

  const columns = (entry: PrinterWithCalibrations) =>
    columnsOf(entry, {
      left: t('inventory.leftNozzle'),
      right: t('inventory.rightNozzle'),
      single: t('inventory.nozzle'),
    });

  const matchingFor = (entry: PrinterWithCalibrations) =>
    entry.printer.connected
      ? entry.calibrations.filter(cal => isMatchingCalibration(cal, formData))
      : [];

  /**
   * Grid cells that could hold a K profile but do not -- the left rail's
   * "unset" badge. Only cells with something to choose are counted: a size the
   * printer has no calibration for is not an unfinished decision.
   */
  const unsetCount = (group: ModelGroup) => {
    let unset = 0;
    for (const entry of group.printers) {
      const matching = matchingFor(entry);
      for (const column of columns(entry)) {
        for (const diameter of group.diameters) {
          const hasCandidate = matching.some(
            cal =>
              (cal.extruder_id ?? 0) === column.extruder
              && ((cal.nozzle_diameter ?? '').trim() || '0.4') === diameter,
          );
          if (!hasCandidate) continue;
          if (!selectedProfiles.get(hotendKey(entry.printer.id, column.extruder, diameter))) unset++;
        }
      }
    }
    return unset;
  };

  const setPreset = (model: string, diameter: string, option: FilamentOption | null) => {
    setModelPresets(prev => {
      const next = new Map(prev);
      const key = presetKey(model, diameter);
      // Removing the entry is what "inherited" means -- the backend cascade
      // falls through to the spool's own preset when no row exists. Storing a
      // row that repeats the spool's value would freeze it instead: later
      // edits to the spool preset would stop reaching this model.
      if (!option) next.delete(key);
      else next.set(key, { code: option.code, name: option.name });
      return next;
    });
  };

  const chooseProfile = (
    printerId: number,
    extruder: number,
    diameter: string,
    cal: CalibrationProfile | null,
  ) => {
    setSelectedProfiles(prev => {
      const next = new Map(prev);
      const key = hotendKey(printerId, extruder, diameter);
      if (!cal) next.delete(key);
      else next.set(key, cal);
      return next;
    });
  };

  /**
   * Fill each model's preset with the variant of the spool's own preset that
   * names that model. Preset names are mechanical ("Bambu PLA Basic @BBL X1C"),
   * so the match is a name comparison, not a guess about filament identity: a
   * model with no such variant is left inherited rather than given something
   * approximate.
   */
  const autoMatch = () => {
    const base = filamentOptions.find(o => o.code === formData.slicer_filament);
    if (!base) return;
    const stem = base.name.split('@')[0].trim().toLowerCase();
    if (!stem) return;

    setModelPresets(prev => {
      const next = new Map(prev);
      for (const group of groups) {
        if (!group.model) continue;
        // Only presets that name this model. An unclassifiable one stays in the
        // list to be picked by hand but is never assigned for the user.
        const candidates = optionsForModel(group.model, undefined).filter(
          option =>
            option.name.toLowerCase().startsWith(stem)
            && extractPresetModel(option.name, printerModels ?? {}) !== null,
        );
        if (candidates.length === 0) continue;

        for (const diameter of group.diameters) {
          // Bambu names the size in the preset ("@BBL X1C 0.4 nozzle"), so
          // prefer the variant for this size and fall back to one that names
          // the model without a size. A size with neither is left inherited --
          // an approximate preset is worse than the spool's own.
          const sized = candidates.find(option =>
            new RegExp(`\\b${diameter.replace('.', '\\.')}\\s*nozzle\\b`, 'i').test(option.name),
          );
          const unsized = candidates.find(option => !/\b[\d.]+\s*nozzle\b/i.test(option.name));
          const match = sized ?? unsized;
          if (match) next.set(presetKey(group.model, diameter), { code: match.code, name: match.name });
        }
      }
      return next;
    });
  };

  if (printersWithCalibrations.length === 0) {
    return (
      <div className="p-6 bg-bambu-dark rounded-lg text-center">
        {/* "No printers configured" is a claim about the user's setup and must
            not be made while the printers are still being asked -- reading each
            one's calibration table is several MQTT round trips. */}
        <p className="text-bambu-gray flex items-center justify-center gap-2">
          {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
          {isLoading ? t('common.loading') : t('inventory.noPrintersConfigured')}
        </p>
      </div>
    );
  }

  const renderPresetRow = (model: string, diameter: string, inheritLabel: string) => {
    const key = presetKey(model, diameter);
    const chosen = modelPresets.get(key);
    const options = optionsForModel(model, chosen?.code);
    return (
      <div className="flex items-center gap-2">
        <div className="flex-1 min-w-0">
          <PresetPicker
            ariaLabel={`${model} ${diameter}mm ${t('inventory.filamentPreset')}`}
            value={chosen?.code ?? ''}
            options={options}
            inheritLabel={inheritLabel}
            disabled={!model}
            onChange={option => setPreset(model, diameter, option)}
          />
        </div>
        <span
          className={`text-[10px] font-semibold uppercase tracking-wide px-2 py-1 rounded-full shrink-0 ${
            chosen ? 'bg-bambu-green/20 text-bambu-green' : 'bg-bambu-dark-tertiary text-bambu-gray'
          }`}
        >
          {chosen ? t('inventory.presetOverride') : t('inventory.presetInherited')}
        </span>
      </div>
    );
  };

  return (
    <div className="space-y-3">
      {/* Which spool is being configured. Worth a line of its own here: this
          tab is the one place you read printer names rather than filament, and
          the K-profile lists below are filtered by exactly these fields --
          brand, material and subtype -- so an empty list is explained by what
          this line says. */}
      {(identity || formData.color_name) && (
        <div className="flex items-center gap-2.5 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg">
          <span
            className="w-5 h-5 rounded-full border border-white/15 shrink-0"
            style={{ background: swatch }}
            aria-hidden="true"
          />
          {identity && <span className="text-sm text-white truncate">{identity}</span>}
          {formData.color_name && (
            <span className="text-sm text-bambu-gray truncate">{formData.color_name}</span>
          )}
        </div>
      )}

      <div className="flex flex-col md:flex-row gap-4">
      {/* Model list. Sticky rather than its own scroll region: a second
          scrollbar inside the modal's own scrolling body means the user has to
          find which one moves the thing they are looking at. */}
      <div
        role="tablist"
        aria-label={t('inventory.printersTab')}
        aria-orientation="vertical"
        className="md:w-52 md:shrink-0 md:self-start md:sticky md:top-0 flex md:flex-col gap-1.5 overflow-x-auto md:overflow-x-visible"
      >
        {groups.map(group => {
          const isActive = group === active;
          const unset = unsetCount(group);
          return (
            <button
              key={group.id}
              type="button"
              role="tab"
              onClick={() => setSelectedGroupId(group.id)}
              aria-selected={isActive}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-left transition-colors shrink-0 md:shrink ${
                isActive
                  ? 'bg-bambu-green/10 border-bambu-green/40 text-white'
                  : 'bg-transparent border-transparent text-bambu-gray hover:bg-bambu-dark hover:text-white'
              }`}
            >
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold truncate">
                  {group.model || t('inventory.unknownModel')}
                </div>
                <div className="text-[11px] text-bambu-gray">
                  {group.printers.length === 1
                    ? t('inventory.onePrinter')
                    : t('inventory.nPrinters', { n: group.printers.length })}
                </div>
              </div>
              {unset > 0 ? (
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full bg-bambu-dark-tertiary text-bambu-gray shrink-0">
                  {unset}
                </span>
              ) : (
                <Check className="w-3.5 h-3.5 text-bambu-green shrink-0" />
              )}
            </button>
          );
        })}
      </div>

      {/* Detail */}
      <div className="flex-1 min-w-0">
        {active && (
          <div className="space-y-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h4 className="text-base font-semibold text-white truncate">
                  {active.model || t('inventory.unknownModel')}
                </h4>
                <p className="text-xs text-bambu-gray">
                  {active.printers.map(p => p.printer.name).join(', ')}
                </p>
              </div>
              {formData.slicer_filament && (
                <button
                  type="button"
                  onClick={autoMatch}
                  title={t('inventory.autoMatchPresetsHint')}
                  className="flex items-center gap-1.5 px-2 py-1 text-xs bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray hover:text-white hover:border-bambu-green transition-colors shrink-0"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  {t('inventory.autoMatchPresets')}
                </button>
              )}
            </div>

            {/* Filament preset — model scoped */}
            <div className="space-y-2">
              <p className="text-xs font-semibold text-bambu-gray uppercase tracking-wide">
                {t('inventory.filamentPreset')}
              </p>
              {!active.model ? (
                <p className="text-sm text-bambu-gray italic">{t('inventory.presetNeedsModel')}</p>
              ) : (
                /* One row per nozzle size, and no model-wide row above them:
                   the preset is written to an AMS slot, a slot feeds exactly
                   one nozzle, and Bambu names its presets per size anyway
                   ("@BBL X1C 0.4 nozzle"). A size left alone falls straight
                   back to the spool's own preset. */
                <div className="space-y-2">
                  {active.diameters.map(diameter => (
                    <div key={diameter} className="flex items-center gap-3">
                      <span className="text-xs font-mono text-bambu-gray w-14 shrink-0">
                        {diameter}mm
                      </span>
                      <div className="flex-1 min-w-0">
                        {renderPresetRow(
                          active.model,
                          diameter,
                          t('inventory.presetUseSpoolDefault'),
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* K profiles — machine scoped */}
            <div className="space-y-2">
              <p className="text-xs font-semibold text-bambu-gray uppercase tracking-wide">
                {t('inventory.kProfilesPerPrinter')}
              </p>
              {!formData.material ? (
                <p className="text-sm text-bambu-gray italic">{t('inventory.selectMaterialFirst')}</p>
              ) : (
                active.printers.map(entry => {
                  const matching = matchingFor(entry);
                  return (
                    <div
                      key={entry.printer.id}
                      className="p-3 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg"
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <PrinterIcon className="w-3.5 h-3.5 text-bambu-gray shrink-0" />
                        <span className="text-sm font-semibold text-white truncate">
                          {entry.printer.name}
                        </span>
                        <span
                          className={`text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full shrink-0 ${
                            entry.printer.connected
                              ? 'bg-green-500/20 text-green-500'
                              : 'bg-bambu-dark-tertiary text-bambu-gray'
                          }`}
                        >
                          {entry.printer.connected
                            ? t('inventory.connected')
                            : t('inventory.offline')}
                        </span>
                      </div>

                      {!entry.printer.connected ? (
                        <p className="text-sm text-bambu-gray italic py-1">
                          {t('inventory.printerOffline')}
                        </p>
                      ) : matching.length === 0 ? (
                        <p className="text-sm text-bambu-gray italic py-1">
                          {t('inventory.noKProfilesMatch')}
                        </p>
                      ) : (
                        /* A grid rather than a list of rows: nozzle size down
                           the side, hotend across the top. A dual-nozzle
                           machine has up to eight cells, and stacked rows made
                           that a scroll where a table is a glance. Columns run
                           left-then-right to match the machine, which is the
                           reverse of the extruder ids behind them (extruder 0
                           is the RIGHT hotend). */
                        <div
                          className="grid gap-x-3 gap-y-1.5 items-center"
                          style={{
                            gridTemplateColumns: `3.5rem repeat(${columns(entry).length}, minmax(0, 1fr))`,
                          }}
                        >
                          <span />
                          {columns(entry).map(column => (
                            <span
                              key={column.extruder}
                              className="text-[11px] font-semibold uppercase tracking-wide text-bambu-gray"
                            >
                              {column.label}
                            </span>
                          ))}

                          {active.diameters.map(diameter => (
                            <Fragment key={diameter}>
                              <span className="text-xs font-mono text-bambu-gray">{diameter}mm</span>
                              {columns(entry).map(column => {
                                const candidates = matching.filter(
                                  cal =>
                                    (cal.extruder_id ?? 0) === column.extruder
                                    && ((cal.nozzle_diameter ?? '').trim() || '0.4') === diameter,
                                );
                                const key = hotendKey(entry.printer.id, column.extruder, diameter);
                                const chosen = selectedProfiles.get(key);
                                if (candidates.length === 0) {
                                  return (
                                    // The printer has no calibration for this
                                    // size on this hotend. Shown rather than
                                    // omitted so the size is visibly accounted
                                    // for instead of looking forgotten.
                                    <span
                                      key={key}
                                      className="text-xs text-bambu-gray/50 px-2 py-1.5"
                                      title={t('inventory.noKProfilesMatch')}
                                    >
                                      &mdash;
                                    </span>
                                  );
                                }
                                // A stored profile whose flow disagrees with
                                // the nozzle now fitted is not applied at
                                // assign time -- a K value measured on a
                                // high-flow nozzle is not a fact about a
                                // standard one. Say so here rather than let it
                                // look configured and quietly do nothing.
                                const fitted = fittedFlow(entry, column.extruder);
                                const chosenFlow = normaliseFlow(chosen?.nozzle_id);
                                const flowMismatch = !!(fitted && chosenFlow && fitted !== chosenFlow);
                                return (
                                  <select
                                    key={key}
                                    title={
                                      flowMismatch
                                        ? t('inventory.kProfileFlowMismatch', {
                                            profile: flowLabel(chosenFlow),
                                            fitted: flowLabel(fitted),
                                          })
                                        : undefined
                                    }
                                    aria-label={`${entry.printer.name} ${column.label} ${diameter}mm`}
                                    value={chosen ? String(chosen.cali_idx) : ''}
                                    onChange={e => {
                                      const cal =
                                        candidates.find(c => String(c.cali_idx) === e.target.value)
                                        ?? null;
                                      chooseProfile(entry.printer.id, column.extruder, diameter, cal);
                                    }}
                                    className={`min-w-0 px-2 py-1.5 bg-bambu-dark-secondary border rounded-lg text-sm text-white focus:outline-none focus:border-bambu-green ${
                                      flowMismatch ? 'border-amber-500/60' : 'border-bambu-dark-tertiary'
                                    }`}
                                  >
                                    <option value="">{t('inventory.kProfileNotSet')}</option>
                                    {candidates.map(cal => {
                                      // The flow the profile was measured on.
                                      // Shown because the same filament reads a
                                      // different K through a high-flow nozzle,
                                      // and a printer can hold both -- this H2D
                                      // has 102 high-flow entries and 6
                                      // standard. Omitted where the printer
                                      // declares none (an X1C declares none at
                                      // all), since there is nothing to say.
                                      const label = flowLabel(normaliseFlow(cal.nozzle_id));
                                      return (
                                        <option key={cal.cali_idx} value={cal.cali_idx}>
                                          {`${label ? `[${label}] ` : ''}${cal.name || cal.filament_id}  K=${cal.k_value.toFixed(3)}`}
                                        </option>
                                      );
                                    })}
                                  </select>
                                );
                              })}
                            </Fragment>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>
      </div>
    </div>
  );
}
