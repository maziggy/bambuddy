import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Circle, Check, AlertTriangle, RefreshCw, ChevronDown, ChevronUp, Palette } from 'lucide-react';
import { api } from '../../api/client';
import type { SlotSpoolIdentity } from '../../api/client';
import { useFilamentMapping } from '../../hooks/useFilamentMapping';
import { getGlobalTrayId, effectivePreferLowest, FTS_INLET_SIDE } from '../../utils/amsHelpers';
import { disambiguateColorNames, getColorName } from '../../utils/colors';
import { useFilamentLabels } from './useFilamentLabels';
import { autoAssignRackPositions, rackOptionsForGroup } from '../../utils/nozzleRack';
import type { FilamentMappingProps, RackGroupInfo } from './types';

/**
 * Filament mapping UI for comparing required filaments with loaded AMS slots.
 * Shows auto-matched and manually overridden slot assignments.
 */
export function FilamentMapping({
  printerId,
  filamentReqs,
  manualMappings,
  onManualMappingChange,
  onEstimatedCostChange,
  budgetAvailable,
  quantity = 1,
  currencySymbol,
  defaultCostPerKg,
  defaultExpanded = false,
  forceColorMatch,
  onForceColorMatchChange,
  plateLabel,
  archiveAmsMapping,
  nozzleRackChoice,
  onNozzleRackChoiceChange,
}: FilamentMappingProps & { defaultExpanded?: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  // "Mapping" toggle (only shown when the archive has a saved slicer pick):
  // ON selects every slot straight from `archiveAmsMapping`, bypassing the
  // type/color auto-match entirely — same mechanism as a manual per-slot
  // pick (`manualMappings`), just applied to every required slot at once.
  // OFF removes exactly those overrides so the panel falls back to its
  // normal auto-match, without touching any *other* manual picks the user
  // made by hand.
  const [usingArchiveMapping, setUsingArchiveMapping] = useState(false);
  // Which slot IDs the ON branch below actually wrote into manualMappings —
  // so OFF can undo exactly those and leave any *other* manual pick the user
  // made by hand (before or after pressing the button) untouched.
  const appliedSlotIdsRef = useRef<number[]>([]);

  // Reset the toggle whenever the saved mapping it would apply changes — a
  // different printer, plate selection, or archive entirely. Without this
  // the button can read ON (green) from a previous printer/archive/plate
  // even though it was never pressed against the mapping currently in scope.
  useEffect(() => {
    setUsingArchiveMapping(false);
    appliedSlotIdsRef.current = [];
  }, [archiveAmsMapping, plateLabel, printerId]);

  const toggleArchiveMapping = () => {
    if (!archiveAmsMapping || !filamentReqs?.filaments) return;
    if (usingArchiveMapping) {
      const next = { ...manualMappings };
      for (const slotId of appliedSlotIdsRef.current) {
        delete next[slotId];
      }
      onManualMappingChange(next);
      appliedSlotIdsRef.current = [];
      setUsingArchiveMapping(false);
      return;
    }
    const next = { ...manualMappings };
    const appliedSlotIds: number[] = [];
    for (const req of filamentReqs.filaments) {
      const idx = req.slot_id - 1;
      // A negative value (e.g. the external spool sentinel) means the
      // slicer didn't resolve this filament to an AMS tray — leave that
      // slot's existing auto-match/manual pick alone rather than clearing it.
      if (req.slot_id > 0 && idx >= 0 && idx < archiveAmsMapping.length && archiveAmsMapping[idx] >= 0) {
        next[req.slot_id] = archiveAmsMapping[idx];
        appliedSlotIds.push(req.slot_id);
      }
    }
    onManualMappingChange(next);
    appliedSlotIdsRef.current = appliedSlotIds;
    setUsingArchiveMapping(true);
  };

  // Fetch printer status
  const { data: printerStatus } = useQuery({
    queryKey: ['printer-status', printerId],
    queryFn: () => api.getPrinterStatus(printerId),
    enabled: !!printerId,
  });

  const { data: assignments } = useQuery({
    queryKey: ['spool-assignments', printerId],
    queryFn: () => api.getAssignments(printerId),
    enabled: !!printerId,
  });

  // Settings + inventory map drive the same prefer-lowest + AMS-backup gate
  // the dispatcher uses (#1766). Without this, the per-slot dropdown's
  // auto-suggestion could disagree with what actually gets dispatched.
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });
  const { data: inventoryRemain } = useQuery({
    queryKey: ['printer-inventory-remain', printerId],
    queryFn: () => api.getInventoryRemain(printerId),
    enabled: !!printerId,
    staleTime: 30 * 1000,
    // Fresh on every open, cached while open. This payload now names the
    // slots, and a spool assigned moments ago would otherwise keep its old
    // name for the rest of the stale window. Doing it here rather than
    // invalidating the key from each of the eighteen places a binding or a
    // spool can change: half of those are internal-inventory paths and half
    // are Spoolman ones, and covering some of them would make freshness
    // depend on which inventory mode you run.
    refetchOnMount: 'always',
  });
  const inventoryByTrayId = useMemo(() => {
    if (!inventoryRemain?.inventory_remain_g) return undefined;
    const map = new Map<number, number>();
    Object.entries(inventoryRemain.inventory_remain_g).forEach(([key, grams]) => {
      const gtid = Number(key);
      if (!Number.isNaN(gtid)) map.set(gtid, grams);
    });
    return map;
  }, [inventoryRemain]);
  // The other half of the same payload: what Bambuddy has bound to each slot,
  // so a slot reads as the spool the operator assigned rather than as whatever
  // the printer can say about it. A third-party spool reports no sub-brand at
  // all and its colour hex resolves against Bambu's catalogue, so without this
  // the dialog named slots differently from the printer card.
  const slotSpools = useMemo(() => {
    const slots = inventoryRemain?.slot_materials;
    if (!slots?.length) return undefined;
    const map = new Map<number, SlotSpoolIdentity>();
    slots.forEach((slot) => {
      if (slot.spool) map.set(slot.global_tray_id, slot.spool);
    });
    return map.size > 0 ? map : undefined;
  }, [inventoryRemain]);
  const gatedPreferLowest = effectivePreferLowest(
    settings?.prefer_lowest_filament,
    printerStatus?.ams_filament_backup,
  );

  const { loadedFilaments, filamentComparison, hasTypeMismatch, hasColorMismatch } =
    useFilamentMapping(
      filamentReqs,
      printerStatus,
      manualMappings,
      gatedPreferLowest,
      inventoryByTrayId,
      slotSpools,
    );

  // Per-slot sub-brand + material-disambiguated colour labels (#1718). Same
  // shared hook the model-mode FilamentOverride uses so both panels render
  // the same sliced-3MF identity. Falls back to the raw type / generic
  // colour bucket when the SKU is unknown or the by-material lookup hasn't
  // resolved — never blanks out the required row.
  const filamentLabels = useFilamentLabels(filamentReqs?.filaments);

  const trayCostMap = useMemo(() => {
    const map = new Map<number, number | null>();
    for (const assignment of assignments || []) {
      const isExternal = assignment.ams_id === 255;
      const globalTrayId = getGlobalTrayId(assignment.ams_id, assignment.tray_id, isExternal);
      map.set(globalTrayId, assignment.spool?.cost_per_kg ?? null);
    }
    return map;
  }, [assignments]);

  const trayRemainingWeightMap = useMemo(() => {
    const map = new Map<number, number | null>();
    for (const assignment of assignments || []) {
      const isExternal = assignment.ams_id === 255;
      const globalTrayId = getGlobalTrayId(assignment.ams_id, assignment.tray_id, isExternal);
      const spool = assignment.spool;
      if (!spool) {
        map.set(globalTrayId, null);
        continue;
      }
      map.set(globalTrayId, Math.max(0, Math.round((spool.label_weight ?? 0) - (spool.weight_used ?? 0))));
    }
    return map;
  }, [assignments]);

  const totalCost = useMemo(() => {
    let total = 0;
    for (const item of filamentComparison) {
      const trayId = item.loaded?.globalTrayId;
      if (trayId == null) continue;
      const assignedCost = trayCostMap.get(trayId) ?? null;
      const costPerKg = assignedCost ?? defaultCostPerKg;
      if (costPerKg > 0) {
        total += (item.used_grams / 1000) * costPerKg;
      }
    }
    return total;
  }, [filamentComparison, trayCostMap, defaultCostPerKg]);

  // Callers rendering one mapping per selected plate naturally create a
  // plate-scoped callback inline. Keep the latest callback in a ref so a new
  // function identity does not retrigger the cost effect and create a
  // parent/child render loop.
  const onEstimatedCostChangeRef = useRef(onEstimatedCostChange);
  useEffect(() => {
    onEstimatedCostChangeRef.current = onEstimatedCostChange;
  }, [onEstimatedCostChange]);
  useEffect(() => {
    onEstimatedCostChangeRef.current?.(totalCost > 0 ? totalCost : null);
  }, [totalCost]);

  const hasAnyCost = useMemo(
    () => Array.from(trayCostMap.values()).some((v) => v != null && v > 0),
    [trayCostMap]
  );
  const budgetCheckCost = totalCost * Math.max(1, quantity);
  const isBudgetInsufficient = budgetAvailable != null && budgetCheckCost > budgetAvailable;
  const hasFilamentReqs = filamentReqs?.filaments && filamentReqs.filaments.length > 0;
  const isDualNozzle = filamentReqs?.filaments?.some((f) => f.nozzle_id != null) ?? false;

  // Nozzle rack (#1784). The 3MF names a filament *group* per slot and says
  // which groups need a hotend off the rack; which of the six positions each
  // takes is the operator's to choose and is stated nowhere in the file. A
  // group, not a slot, is the unit of choice — two slots sharing a group share
  // one hotend and cannot point at different positions.
  const rackGroups = useMemo(() => {
    const groups = new Map<number, RackGroupInfo>();
    for (const f of filamentReqs?.filaments ?? []) {
      if (f.group_id != null && f.group) groups.set(f.group_id, f.group);
    }
    return groups;
  }, [filamentReqs]);
  const hasRack = (printerStatus?.nozzle_rack?.some((n) => n.id >= 16) ?? false)
    && [...rackGroups.values()].some((g) => g.on_rack);

  // What the dispatcher would assign if nothing were picked, shown as the
  // pre-selection so the dialog states what will happen rather than leaving
  // every picker blank. Explicit picks are pinned and the rest fill in around
  // them, exactly as the backend does it.
  const effectiveRackChoice = useMemo(() => {
    if (!hasRack) return {};
    return (
      autoAssignRackPositions(printerStatus?.nozzle_rack, rackGroups, nozzleRackChoice ?? {})
      ?? (nozzleRackChoice ?? {})
    );
  }, [hasRack, printerStatus?.nozzle_rack, rackGroups, nozzleRackChoice]);

  const pickRackPosition = (groupId: number, position: number) => {
    if (!onNozzleRackChoiceChange) return;
    // Every group is written back, not just the edited one: leaving the others
    // implicit would let the dispatcher re-assign them around the new pick and
    // silently move a hotend the operator had already seen and accepted.
    onNozzleRackChoiceChange({ ...effectiveRackChoice, [groupId]: position });
  };

  // Filament Track Switch: when installed, AMS-to-extruder mapping is dynamic
  // (any slot can be routed to either extruder), so the per-nozzle dropdown
  // filter is suppressed. See #1162.
  //
  // What a slot CAN be labelled with is the switch inlet its AMS is plumbed
  // into (ams_switch_inlet, from AMS info bits 24-27). That is the stable
  // relationship the printer's own "Manual AMS Setup" screen sets. The live
  // inlet-to-outlet route is deliberately not shown: the firmware never reports
  // which inlet is currently paired with which outlet, so any left/right label
  // on a slot would be a guess.
  const ftsInstalled = printerStatus?.fila_switch?.installed === true;
  const amsSwitchInlet = printerStatus?.ams_switch_inlet;
  const ftsInletForAms = (amsId: number): 'A' | 'B' | null =>
    (ftsInstalled && amsSwitchInlet?.[String(amsId)]) || null;

  // Every filament for this print sitting behind one inlet is the case worth
  // flagging. Bambu's own guidance: a change between two filaments on the same
  // inlet has to retract the old one all the way back to its AMS before the new
  // one can be fed through the shared tube, where a change across the two
  // inlets only retracts as far as the switch. All-on-one-inlet means every
  // single change in the job takes the slow path.
  const sameInletWarning = useMemo(() => {
    if (!ftsInstalled) return null;
    const inlets = new Set<string>();
    for (const item of filamentComparison) {
      if (!item.loaded || item.loaded.isExternal) return null;
      const inlet = ftsInletForAms(item.loaded.amsId);
      if (!inlet) return null;
      inlets.add(inlet);
    }
    if (filamentComparison.length < 2 || inlets.size !== 1) return null;
    return [...inlets][0];
    // ftsInletForAms is a stable closure over the two values already listed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ftsInstalled, amsSwitchInlet, filamentComparison]);

  // Don't render if no filament requirements
  if (!hasFilamentReqs) {
    return null;
  }

  // Don't render until we have printer status to do the comparison
  if (!printerStatus) {
    return null;
  }

  // Determine status indicator color
  const statusColor = hasTypeMismatch
    ? '#f97316' // orange
    : hasColorMismatch
    ? '#facc15' // yellow
    : '#00ae42'; // green

  const handleSlotChange = (slotId: number, value: string) => {
    if (slotId > 0) {
      if (value === '') {
        // Clear manual override
        const next = { ...manualMappings };
        delete next[slotId];
        onManualMappingChange(next);
      } else {
        onManualMappingChange({
          ...manualMappings,
          [slotId]: parseInt(value, 10),
        });
      }
    }
  };

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      // Request fresh data from printer via MQTT pushall command
      await api.refreshPrinterStatus(printerId);
      // Wait a moment for printer to respond, then refetch
      await new Promise((r) => setTimeout(r, 500));
      await queryClient.refetchQueries({ queryKey: ['printer-status', printerId] });
    } finally {
      setIsRefreshing(false);
    }
  };

  return (
    <div className="mb-4">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-2 text-sm text-bambu-gray hover:text-white transition-colors w-full"
      >
        <Circle className="w-4 h-4" fill={statusColor} stroke="none" />
        <span>{plateLabel ? `${t('printModal.filamentMapping')} — ${plateLabel}` : t('printModal.filamentMapping')}</span>
        {hasTypeMismatch ? (
          <span className="text-xs text-orange-700 dark:text-orange-400">({t('printModal.statusTypeNotFound')})</span>
        ) : hasColorMismatch ? (
          <span className="text-xs text-yellow-700 dark:text-yellow-400">({t('printModal.statusColorMismatch')})</span>
        ) : (
          <span className="text-xs text-bambu-green">({t('printModal.statusReady')})</span>
        )}
        {isExpanded ? (
          <ChevronUp className="w-4 h-4 ml-auto" />
        ) : (
          <ChevronDown className="w-4 h-4 ml-auto" />
        )}
      </button>

      {isExpanded && (
        <div className="mt-2 bg-bambu-dark rounded-lg p-3 space-y-2">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-bambu-gray">{t('printModal.clickToChangeSlot')}</span>
            <div className="flex items-center gap-1.5">
              {archiveAmsMapping && (
                <button
                  type="button"
                  onClick={toggleArchiveMapping}
                  title={t('printModal.useArchiveMappingTooltip')}
                  className={`flex items-center gap-1 px-2 py-0.5 text-xs rounded border transition-colors ${
                    usingArchiveMapping
                      ? 'border-bambu-green bg-bambu-green/10 text-bambu-green'
                      : 'border-bambu-gray/30 hover:border-bambu-gray hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white'
                  }`}
                >
                  <Check className="w-3 h-3" />
                  <span>{t('printModal.useArchiveMapping')}</span>
                </button>
              )}
              <button
                type="button"
                onClick={handleRefresh}
                className="flex items-center gap-1 px-2 py-0.5 text-xs rounded border border-bambu-gray/30 hover:border-bambu-gray hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray hover:text-white"
                disabled={isRefreshing}
              >
                <RefreshCw className={`w-3 h-3 ${isRefreshing ? 'animate-spin' : ''}`} />
                <span>{t('printModal.reRead')}</span>
              </button>
            </div>
          </div>
          {sameInletWarning && (
            <div className="flex items-start gap-1.5 rounded border border-yellow-500/40 bg-yellow-500/10 px-2 py-1.5 text-xs text-yellow-700 dark:text-yellow-400">
              <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
              <span>{t('printModal.ftsSameInletHint', { inlet: sameInletWarning })}</span>
            </div>
          )}
          {filamentComparison.map((item, idx) => {
            // #1717: surface the same per-slot force-color-match checkbox here
            // that FilamentOverride exposes for model-mode dispatch. The
            // scheduler honors the flag in both modes; only the UI was missing.
            const slotId = item.slot_id ?? 0;
            const canForceMatch = slotId > 0 && onForceColorMatchChange != null;
            // #1718: same sub-brand + colour resolution as FilamentOverride.
            // Indexing is safe because ``useFilamentLabels`` mirrors the input
            // array shape; defensive fallback covers the empty-reqs render
            // path that shouldn't reach here anyway.
            const { resolvedName, colorLabel } = filamentLabels[idx] ?? { resolvedName: item.type, colorLabel: getColorName(item.color) };
            // Both sides of a colour mismatch routinely resolve to the same
            // name -- a slicer's pure blue and a spool's navy are both "Blue" --
            // which made the warning look like it was contradicting itself
            // (#2941). Qualify them with their hex when the names collide.
            const [requiredColorLabel, loadedColorLabel] = disambiguateColorNames(
              { name: colorLabel, hex: item.color },
              { name: item.loaded?.colorName, hex: item.loaded?.color },
            );
            return (
            <div key={idx} className="space-y-1">
              <div
                className="grid items-center gap-2 text-xs"
                style={{
                  // The rack picker sits inside the required-filament cell, so
                  // on a rack machine that cell has to carry the name *and* an
                  // ~85px dropdown. Raising only the floor (not the fraction)
                  // keeps every other printer's layout exactly as it was, and
                  // keeps the AMS dropdown — which names type, colour and
                  // remaining weight — the widest column.
                  gridTemplateColumns: hasRack
                    ? '16px minmax(210px, 1.4fr) auto 2fr 16px'
                    : '16px minmax(70px, 1fr) auto 2fr 16px',
                }}
              >
                {/* Required color */}
                <span title={t('printModal.requiredTooltip', { name: resolvedName, color: requiredColorLabel })}>
                  <Circle className="w-3 h-3" fill={item.color} stroke={item.color} />
                </span>
                {/* Required type + grams + nozzle badge. Only the name
                    truncates; the gram usage is pinned (shrink-0) so it never
                    clips on narrow/mobile widths (#2669). */}
                <span className="text-white flex items-center gap-1 min-w-0">
                  {hasRack && item.group_id != null && item.group ? (
                    item.group.on_rack ? (
                      <select
                        value={effectiveRackChoice[item.group_id] ?? ''}
                        onChange={(e) => pickRackPosition(item.group_id!, Number(e.target.value))}
                        disabled={!onNozzleRackChoiceChange}
                        title={t('printModal.rackPositionTooltip')}
                        aria-label={t('printModal.rackPosition')}
                        className="shrink-0 bg-bambu-dark-tertiary text-white text-[10px] font-bold rounded px-1 py-0.5 border border-bambu-dark-tertiary focus:border-bambu-green outline-none disabled:opacity-60"
                      >
                        {rackOptionsForGroup(printerStatus?.nozzle_rack, item.group, t).map((option) => (
                          <option
                            key={option.position}
                            value={option.position}
                            disabled={!option.eligible}
                            title={option.reason}
                          >
                            R{option.position}
                            {option.diameter ? ` · ${option.diameter}` : ''}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span
                        className="inline-flex items-center justify-center w-3.5 h-3.5 rounded text-[9px] font-bold leading-none bg-bambu-gray/20 text-bambu-gray shrink-0"
                        title={t('printModal.leftNozzleTooltip')}
                      >
                        {t('printModal.leftNozzle')}
                      </span>
                    )
                  ) : isDualNozzle && item.nozzle_id != null ? (
                    <span
                      className="inline-flex items-center justify-center w-3.5 h-3.5 rounded text-[9px] font-bold leading-none bg-bambu-gray/20 text-bambu-gray shrink-0"
                      title={item.nozzle_id === 1 ? t('printModal.leftNozzleTooltip') : t('printModal.rightNozzleTooltip')}
                    >
                      {item.nozzle_id === 1 ? t('printModal.leftNozzle') : t('printModal.rightNozzle')}
                    </span>
                  ) : null}
                  <span className="truncate min-w-0" title={resolvedName}>{resolvedName}</span>
                  <span className="text-bambu-gray shrink-0 whitespace-nowrap">({item.used_grams}g)</span>
                </span>
                {/* Arrow */}
                <span className="text-bambu-gray">→</span>
                {/* Slot selector dropdown */}
                <select
                  value={item.loaded?.globalTrayId ?? ''}
                  onChange={(e) => handleSlotChange(slotId, e.target.value)}
                  className={`flex-1 px-2 py-1 rounded border text-xs bg-bambu-dark-secondary focus:outline-none focus:ring-1 focus:ring-bambu-green ${
                    item.status === 'match'
                      ? 'border-bambu-green/50 text-bambu-green'
                      : item.status === 'type_only'
                      ? 'border-yellow-500 dark:border-yellow-400/50 text-yellow-700 dark:text-yellow-400'
                      : 'border-orange-500 dark:border-orange-400/50 text-orange-700 dark:text-orange-400'
                  } ${item.isManual ? 'ring-1 ring-blue-400/50' : ''}`}
                  title={item.isManual ? t('printModal.manuallySelected') : t('printModal.autoMatched')}
                >
                  <option value="" className="bg-bambu-dark text-bambu-gray">
                    -- {t('printModal.selectSlot')} --
                  </option>
                  {/*
                    #1722: every loaded slot is offered for every filament row,
                    regardless of which extruder the slot is wired to. Before this
                    change a slot was only listed when its extruder matched the
                    filament's slicer-assigned nozzle (item.nozzle_id), which
                    locked users out of cross-extruder picks even when they'd
                    intentionally loaded the required filament into the "other"
                    AMS. The L/R badge on the filament row still tells the user
                    what the slicer planned; the dropdown now trusts the user to
                    pick based on their physical setup. Printer firmware accepts
                    or rejects the ams_mapping at start-print — failure is loud,
                    not silent.
                  */}
                  {loadedFilaments.map((f) => {
                      const remainingWeight = trayRemainingWeightMap.get(f.globalTrayId);
                      const remainingLabel = remainingWeight != null
                        ? t('printModal.slotRemainingShort', {
                            grams: remainingWeight,
                            defaultValue: ` - ${remainingWeight}g left`,
                          })
                        : '';
                      // FTS badge: which switch inlet this slot's AMS feeds. Not a
                      // nozzle — the slot reaches both through the switch — but it
                      // is what decides whether a change to the next filament is
                      // the fast cross-inlet one or the slow same-inlet one.
                      const ftsInlet = ftsInletForAms(f.amsId);
                      // Same L/R lettering the printer card uses for inlets, so the
                      // two views agree. Not translated: L and R are the letters on
                      // the machine.
                      const ftsBadge = ftsInlet == null ? '' : ` [${FTS_INLET_SIDE[ftsInlet]}]`;
                      return (
                        <option key={f.globalTrayId} value={f.globalTrayId} className="bg-bambu-dark text-white">
                          {f.label}: {f.spoolName || f.traySubBrands || f.type} ({f.colorName}){remainingLabel}{ftsBadge}
                        </option>
                      );
                  })}
                </select>
                {/* Status icon */}
                {item.status === 'match' ? (
                  <Check className="w-3 h-3 text-bambu-green" />
                ) : item.status === 'type_only' ? (
                  <span
                    title={
                      requiredColorLabel && loadedColorLabel
                        ? t('printModal.sameTypeDifferentColorDetail', {
                            required: requiredColorLabel,
                            loaded: loadedColorLabel,
                          })
                        : t('printModal.sameTypeDifferentColor')
                    }
                  >
                    <AlertTriangle className="w-3 h-3 text-yellow-600 dark:text-yellow-400" />
                  </span>
                ) : (
                  <span title={t('printModal.filamentTypeNotLoaded')}>
                    <AlertTriangle className="w-3 h-3 text-orange-600 dark:text-orange-400" />
                  </span>
                )}
              </div>
              {/* Force Color Match checkbox — matches FilamentOverride's layout. */}
              {canForceMatch && (
                <label className="inline-flex items-center gap-1.5 text-xs text-bambu-gray cursor-pointer select-none pl-5">
                  <input
                    type="checkbox"
                    checked={forceColorMatch?.[slotId] ?? false}
                    onChange={(e) => onForceColorMatchChange(slotId, e.target.checked)}
                    className="accent-bambu-green w-3 h-3"
                  />
                  <Palette className="w-3 h-3" />
                  {t('printModal.forceColorMatch')}
                </label>
              )}
            </div>
            );
          })}
          <div className="text-xs text-bambu-gray">
            {t('printModal.totalCost')}{' '}
            <span className="text-white">
              {totalCost > 0 || hasAnyCost ? `${currencySymbol}${totalCost.toFixed(2)}` : 'N/A'}
            </span>
            {quantity > 1 && totalCost > 0 && (
              <span className="ml-2">
                {t('printModal.totalCostForQuantity', 'total: {{cost}}', {
                  cost: `${currencySymbol}${budgetCheckCost.toFixed(2)}`,
                })}
              </span>
            )}
          </div>
          {isBudgetInsufficient && (
            <p className="text-xs text-red-400 mt-2">
              {t('printModal.insufficientBudget', 'Insufficient budget for this cost center.')}
            </p>
          )}
          {hasTypeMismatch && (
            <p className="text-xs text-orange-700 dark:text-orange-400 mt-2">
              {t('printModal.requiredTypeNotInPrinter')}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
