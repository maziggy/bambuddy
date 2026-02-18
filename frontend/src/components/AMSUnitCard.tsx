import { useTranslation } from 'react-i18next';
import { MoreVertical, RefreshCw } from 'lucide-react';
import { FilamentHoverCard, EmptySlotHoverCard } from './FilamentHoverCard';
import type { AMSUnit, AMSTray, LinkedSpoolInfo, SpoolAssignment, Permission } from '../api/client';

interface AMSUnitCardProps {
  ams: AMSUnit;
  isDualNozzle: boolean;
  amsExtruderMap: Record<string, number>;
  effectiveTrayNow: number | null | undefined;
  filamentInfo?: Record<string, { name: string; k: number | null }>;
  slotPresets?: Record<number, { preset_id: string; preset_name: string }>;
  amsThresholds?: {
    humidityGood: number;
    humidityFair: number;
    tempGood: number;
    tempFair: number;
  };
  printerId: number;
  printerState?: string | null;
  // External spool mode
  isExternal?: boolean;
  // Spoolman
  spoolmanEnabled: boolean;
  hasUnlinkedSpools: boolean;
  linkedSpools?: Record<string, LinkedSpoolInfo>;
  spoolmanUrl?: string | null | undefined;
  // Inventory
  onGetAssignment?: (printerId: number, amsId: number, trayId: number) => SpoolAssignment | undefined;
  onUnassignSpool?: (printerId: number, amsId: number, trayId: number) => void;
  // Slot menu state
  amsSlotMenu: { amsId: number; slotId: number } | null;
  setAmsSlotMenu: (menu: { amsId: number; slotId: number } | null) => void;
  // Refreshing
  refreshingSlot: { amsId: number; slotId: number } | null;
  onRefreshSlot: (amsId: number, slotId: number) => void;
  // Permissions
  hasPermission: (permission: Permission) => boolean;
  // Modal openers
  onOpenAmsHistory: (amsId: number, amsLabel: string, mode: 'humidity' | 'temperature') => void;
  onOpenLinkSpool: (tagUid: string, trayUuid: string, printerId: number, amsId: number, trayId: number) => void;
  onOpenAssignSpool: (printerId: number, amsId: number, trayId: number, trayInfo: { type: string; color: string; location: string }) => void;
  onOpenConfigureSlot: (config: {
    amsId: number;
    trayId: number;
    trayCount: number;
    trayType?: string;
    trayColor?: string;
    traySubBrands?: string;
    trayInfoIdx?: string;
    extruderId?: number;
    caliIdx?: number | null;
    savedPresetId?: string;
  }) => void;
  // Helper functions
  getAmsLabel: (amsId: number | string, trayCount: number) => string;
  getFillBarColor: (fillLevel: number) => string;
  getSpoolmanFillLevel: (spool: LinkedSpoolInfo | undefined) => number | null;
  isBambuLabSpool: (tray: { tray_uuid?: string | null; tag_uid?: string | null } | null | undefined) => boolean;
  getBambuColorName: (trayIdName: string | null | undefined) => string | null;
  hexToBasicColorName: (hex: string | null | undefined) => string;
  formatKValue: (k: number | null | undefined) => string;
  // Sub-components
  NozzleBadge: React.ComponentType<{ side: 'L' | 'R' }>;
  HumidityIndicator: React.ComponentType<{
    humidity: number;
    goodThreshold?: number;
    fairThreshold?: number;
    onClick?: () => void;
    compact?: boolean;
  }>;
  TemperatureIndicator: React.ComponentType<{
    temp: number;
    goodThreshold?: number;
    fairThreshold?: number;
    onClick?: () => void;
    compact?: boolean;
  }>;
}

export function AMSUnitCard({
  ams,
  isDualNozzle,
  amsExtruderMap,
  effectiveTrayNow,
  filamentInfo,
  slotPresets,
  amsThresholds,
  printerId,
  printerState,
  spoolmanEnabled,
  hasUnlinkedSpools,
  linkedSpools,
  spoolmanUrl,
  onGetAssignment,
  onUnassignSpool,
  amsSlotMenu,
  setAmsSlotMenu,
  refreshingSlot,
  onRefreshSlot,
  hasPermission,
  onOpenAmsHistory,
  onOpenLinkSpool,
  onOpenAssignSpool,
  onOpenConfigureSlot,
  getAmsLabel,
  getFillBarColor,
  getSpoolmanFillLevel,
  isBambuLabSpool,
  getBambuColorName,
  hexToBasicColorName,
  formatKValue,
  HumidityIndicator,
  TemperatureIndicator,
}: AMSUnitCardProps) {
  const { t } = useTranslation();
  const isExternal = ams.id === 255
  const isHt = ams.tray.length <= 1 && !isExternal;
  const isSingleSlot = isHt || isExternal;
  const mappedExtruderId = amsExtruderMap[String(ams.id)];

  // Resolve tray, slot index, and global tray ID per tray entry
  const resolveSlotInfo = (tray: AMSTray | undefined, arrayIdx: number) => {
    if (isExternal) {
      const extTrayId = tray?.id ?? 254;
      const slotTrayId = extTrayId - 254; // 0 or 1
      return { tray, slotIdx: slotTrayId, globalTrayId: extTrayId, slotPresetKey: 255 * 4 + slotTrayId };
    }
    if (isHt) {
      const htTray = ams.tray[0];
      const htSlotId = htTray?.id ?? 0;
      return { tray: htTray, slotIdx: htSlotId, globalTrayId: ams.id * 4 + htSlotId, slotPresetKey: ams.id * 4 + htSlotId };
    }
    const slotIdx = arrayIdx;
    const resolved = ams.tray[slotIdx] || ams.tray.find(t => t.id === slotIdx);
    const globalTrayId = ams.id * 4 + slotIdx;
    return { tray: resolved, slotIdx, globalTrayId, slotPresetKey: globalTrayId };
  };

  // For external: iterate sorted trays; for HT: single tray; for regular: 4 slots
  const slotEntries = isExternal
    ? [...ams.tray].sort((a, b) => (a.id ?? 254) - (b.id ?? 254))
    : isHt
      ? [ams.tray[0]]
      : [undefined, undefined, undefined, undefined]; // placeholders for [0,1,2,3]

  const renderSlot = (trayEntry: AMSTray | undefined, arrayIdx: number) => {
    const { tray, slotIdx, globalTrayId, slotPresetKey } = resolveSlotInfo(trayEntry, arrayIdx);
    const hasFillLevel = tray?.tray_type && tray.remain >= 0;
    const isEmpty = !tray?.tray_type;
    const isActive = effectiveTrayNow === globalTrayId;
    const cloudInfo = tray?.tray_info_idx ? filamentInfo?.[tray.tray_info_idx] : null;
    const slotPreset = slotPresets?.[slotPresetKey];

    // Fill level fallback chain
    const trayTag = tray?.tray_uuid?.toUpperCase();
    const linkedSpool = trayTag ? linkedSpools?.[trayTag] : undefined;
    const spoolmanFill = getSpoolmanFillLevel(linkedSpool);
    const inventoryAssignment = onGetAssignment?.(printerId, ams.id, slotIdx);
    const inventoryFill = (() => {
      const sp = inventoryAssignment?.spool;
      if (sp && sp.label_weight > 0 && sp.weight_used > 0) {
        return Math.round(Math.max(0, sp.label_weight - sp.weight_used) / sp.label_weight * 100);
      }
      return null;
    })();

    // External spools have no AMS remain; regular/HT use AMS remain as primary
    const effectiveFill = isExternal
      ? (spoolmanFill ?? inventoryFill ?? null)
      : (hasFillLevel && tray.remain > 0
        ? tray.remain
        : (spoolmanFill ?? inventoryFill ?? (hasFillLevel ? tray.remain : null)));

    const fillSource = isExternal
      ? (spoolmanFill !== null ? 'spoolman' as const
        : inventoryFill !== null ? 'inventory' as const
          : undefined)
      : ((hasFillLevel && tray.remain === 0 && (spoolmanFill !== null || inventoryFill !== null))
        ? (spoolmanFill !== null ? 'spoolman' as const : 'inventory' as const)
        : 'ams' as const);

    // Build filament data for hover card
    const filamentData = (isExternal || tray?.tray_type) ? {
      vendor: (isBambuLabSpool(tray) ? 'Bambu Lab' : 'Generic') as 'Bambu Lab' | 'Generic',
      profile: cloudInfo?.name || slotPreset?.preset_name || tray?.tray_sub_brands || tray?.tray_type || (isExternal ? 'Unknown' : ''),
      colorName: getBambuColorName(tray?.tray_id_name) || hexToBasicColorName(tray?.tray_color),
      colorHex: tray?.tray_color || null,
      kFactor: formatKValue(tray?.k),
      fillLevel: effectiveFill,
      trayUuid: tray?.tray_uuid || null,
      tagUid: tray?.tag_uid || null,
      fillSource,
    } : null;

    // For external empty trays, filamentData is set but we need to know it's truly empty
    const hasFilament = isExternal ? !isEmpty : !!filamentData;

    const isRefreshing = refreshingSlot?.amsId === ams.id &&
      refreshingSlot?.slotId === slotIdx;

    // Nozzle label for external dual-nozzle slots
    const extNozzleLabel = isExternal && isDualNozzle
      ? ((tray?.id ?? 254) === 254 ? t('printers.extL') : t('printers.extR'))
      : '';

    // Location label for assign spool modal
    const locationLabel = isExternal
      ? (extNozzleLabel || t('printers.external'))
      : isHt
        ? getAmsLabel(ams.id, ams.tray.length)
        : `${getAmsLabel(ams.id, ams.tray.length)} Slot ${slotIdx + 1}`;

    // ExtruderId for configure slot
    const configExtruderId = isExternal
      ? (isDualNozzle ? ((tray?.id ?? 254) === 254 ? 1 : 0) : undefined)
      : mappedExtruderId;

    const slotVisual = (
      <div
        className={`bg-bambu-dark-tertiary rounded p-1 text-center ${isEmpty ? 'opacity-50' : ''} ${isActive ? `${isSingleSlot ? 'ring-2' : 'ring-1'} ring-bambu-green ring-offset-1 ring-offset-bambu-dark` : ''}`}
      >
        <div
          className="w-3.5 h-3.5 rounded-full mx-auto mb-0.5 border-2"
          style={{
            backgroundColor: tray?.tray_color ? `#${tray.tray_color}` : (tray?.tray_type ? '#333' : 'transparent'),
            borderColor: isEmpty ? '#666' : 'rgba(255,255,255,0.1)',
            borderStyle: isEmpty ? 'dashed' : 'solid',
          }}
        />
        <div className={`text-[9px] font-bold truncate ${isExternal && isEmpty ? 'text-white/40' : 'text-white'}`}>
          {tray?.tray_type || '—'}
        </div>
        {/* Fill bar */}
        <div className="mt-1 h-1.5 bg-black/30 rounded-full overflow-hidden">
          {effectiveFill !== null && effectiveFill >= 0 && (isSingleSlot || tray) && !isEmpty ? (
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${effectiveFill}%`,
                backgroundColor: getFillBarColor(effectiveFill),
              }}
            />
          ) : (tray?.tray_type && !isEmpty) ? (
            <div className="h-full w-full rounded-full bg-white/50 dark:bg-gray-500/40" />
          ) : null}
        </div>
      </div>
    );

    return (
      <div key={isExternal ? (tray?.id ?? arrayIdx) : slotIdx} className={`relative group ${isSingleSlot && !isExternal ? 'flex-1 min-w-0' : ''}`}>
        {/* Loading overlay during RFID re-read */}
        {!isExternal && isRefreshing && (
          <div className="absolute inset-0 bg-bambu-dark-tertiary/80 rounded flex items-center justify-center z-20">
            <RefreshCw className="w-4 h-4 text-bambu-green animate-spin" />
          </div>
        )}
        {/* Menu button - appears on hover, hidden when printer busy (not for external spools) */}
        {!isExternal && printerState !== 'RUNNING' && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setAmsSlotMenu(
                amsSlotMenu?.amsId === ams.id && amsSlotMenu?.slotId === slotIdx
                  ? null
                  : { amsId: ams.id, slotId: slotIdx }
              );
            }}
            className="absolute -top-1 -right-1 w-4 h-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity z-10 hover:bg-bambu-dark-tertiary"
            title={t('printers.slotOptions')}
          >
            <MoreVertical className="w-2.5 h-2.5 text-bambu-gray" />
          </button>
        )}
        {/* Dropdown menu (not for external spools) */}
        {!isExternal && printerState !== 'RUNNING' && amsSlotMenu?.amsId === ams.id && amsSlotMenu?.slotId === slotIdx && (
          <div className="absolute top-full left-0 mt-1 z-50 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl py-1 min-w-[120px]">
            <button
              className={`w-full px-3 py-1.5 text-left text-xs flex items-center gap-2 ${hasPermission('printers:ams_rfid')
                ? 'text-white hover:bg-bambu-dark-tertiary'
                : 'text-bambu-gray/50 cursor-not-allowed'
                }`}
              onClick={(e) => {
                e.stopPropagation();
                if (!hasPermission('printers:ams_rfid')) return;
                onRefreshSlot(ams.id, slotIdx);
                setAmsSlotMenu(null);
              }}
              disabled={isRefreshing || !hasPermission('printers:ams_rfid')}
              title={!hasPermission('printers:ams_rfid') ? t('printers.permission.noAmsRfid') : undefined}
            >
              <RefreshCw className={`w-3 h-3 ${isRefreshing ? 'animate-spin' : ''}`} />
              {t('printers.rfid.reread')}
            </button>
          </div>
        )}
        {/* Hover card wraps only the visual content */}
        {hasFilament && filamentData ? (
          <FilamentHoverCard
            data={filamentData}
            spoolman={{
              enabled: spoolmanEnabled,
              hasUnlinkedSpools,
              linkedSpoolId: filamentData.trayUuid ? linkedSpools?.[filamentData.trayUuid.toUpperCase()]?.id : undefined,
              spoolmanUrl,
              onLinkSpool: spoolmanEnabled && filamentData.trayUuid ? (uuid) => {
                onOpenLinkSpool(
                  filamentData.tagUid || '',
                  uuid,
                  printerId,
                  ams.id,
                  slotIdx,
                );
              } : undefined,
            }}
            inventory={spoolmanEnabled ? undefined : (() => {
              const assignment = onGetAssignment?.(printerId, ams.id, slotIdx);
              return {
                assignedSpool: assignment?.spool ? {
                  id: assignment.spool.id,
                  material: assignment.spool.material,
                  brand: assignment.spool.brand,
                  color_name: assignment.spool.color_name,
                } : null,
                onAssignSpool: (isExternal || filamentData.vendor !== 'Bambu Lab') ? () => onOpenAssignSpool(
                  printerId,
                  ams.id,
                  slotIdx,
                  {
                    type: filamentData.profile,
                    color: filamentData.colorHex || '',
                    location: locationLabel,
                  },
                ) : undefined,
                onUnassignSpool: assignment && (isExternal || filamentData.vendor !== 'Bambu Lab') ? () => onUnassignSpool?.(printerId, ams.id, slotIdx) : undefined,
              };
            })()}
            configureSlot={{
              enabled: hasPermission('printers:control'),
              onConfigure: () => onOpenConfigureSlot({
                amsId: ams.id,
                trayId: slotIdx,
                trayCount: isExternal ? 1 : ams.tray.length,
                trayType: tray?.tray_type || undefined,
                trayColor: tray?.tray_color || undefined,
                traySubBrands: tray?.tray_sub_brands || undefined,
                trayInfoIdx: tray?.tray_info_idx || undefined,
                extruderId: configExtruderId,
                caliIdx: tray?.cali_idx,
                savedPresetId: slotPreset?.preset_id,
              }),
            }}
          >
            {slotVisual}
          </FilamentHoverCard>
        ) : (
          <EmptySlotHoverCard
            configureSlot={{
              enabled: hasPermission('printers:control'),
              onConfigure: () => onOpenConfigureSlot({
                amsId: ams.id,
                trayId: slotIdx,
                trayCount: isExternal ? 1 : ams.tray.length,
                extruderId: configExtruderId,
              }),
            }}
          >
            {slotVisual}
          </EmptySlotHoverCard>
        )}
      </div>
    );
  };

  const statsIndicators = (vertical?: boolean) => (
    (ams.humidity != null || ams.temp != null) ? (
      <div className={`flex ${vertical ? 'flex-col' : 'items-center'} gap-1.5 ${vertical ? 'shrink-0' : ''}`}>
        {ams.humidity != null && (
          <HumidityIndicator
            humidity={ams.humidity}
            goodThreshold={amsThresholds?.humidityGood}
            fairThreshold={amsThresholds?.humidityFair}
            onClick={() => onOpenAmsHistory(
              ams.id,
              getAmsLabel(ams.id, ams.tray.length),
              'humidity',
            )}
            compact
          />
        )}
        {ams.temp != null && (
          <TemperatureIndicator
            temp={ams.temp}
            goodThreshold={amsThresholds?.tempGood}
            fairThreshold={amsThresholds?.tempFair}
            onClick={() => onOpenAmsHistory(
              ams.id,
              getAmsLabel(ams.id, ams.tray.length),
              'temperature',
            )}
            compact
          />
        )}
      </div>
    ) : null
  );

  const labelAndNozzle = (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] text-white font-medium">
        {isExternal ? 'EXT' : getAmsLabel(ams.id, ams.tray.length)}
      </span>
    </div>
  );

  // External spools card
  if (isExternal) {
    const trayCount = ams.tray.length;
    return (
      <div className={`min-h-[100px] p-2.5 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary/30 ${trayCount === 1 ? 'flex-[1] min-w-[50px] max-w-[80px]' : 'flex-[2] min-w-[100px] max-w-[150px]'}`}>
        <div className="flex items-center gap-1 mb-2">
          {labelAndNozzle}
        </div>
        <div className={`grid ${trayCount > 1 ? 'grid-cols-2' : 'grid-cols-1'} gap-1.5`}>
          {slotEntries.map((tray, i) => renderSlot(tray, i))}
        </div>
      </div>
    );
  }

  // HT AMS card (single slot)
  if (isHt) {
    return (
      <div className="min-h-[100px] flex-[2] min-w-[120px] max-w-[150px] p-2.5 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary/30">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-1">
          {labelAndNozzle}
          <div className="flex gap-1.5 w-full">
            {slotEntries.map((tray, i) => renderSlot(tray, i))}
            {statsIndicators(true)}
          </div>
        </div>
      </div>
    );
  }

  // Regular AMS card (4 slots)
  return (
    <div className="min-h-[100px] flex-[4] min-w-[180px] max-w-[320px] p-2.5 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary/30">
      <div className="flex items-center justify-between mb-2">
        {labelAndNozzle}
        {statsIndicators()}
      </div>
      <div className="grid grid-cols-4 gap-1.5">
        {slotEntries.map((tray, i) => renderSlot(tray, i))}
      </div>
    </div>
  );
}
