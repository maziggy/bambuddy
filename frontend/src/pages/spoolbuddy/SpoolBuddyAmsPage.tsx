import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useOutletContext } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Layers } from 'lucide-react';
import type { SpoolBuddyOutletContext } from '../../components/spoolbuddy/SpoolBuddyLayout';
import { api } from '../../api/client';
import type { PrinterStatus, AMSTray } from '../../api/client';
import { AmsUnitCard, HumidityIndicator, TemperatureIndicator, NozzleBadge } from '../../components/spoolbuddy/AmsUnitCard';
import type { AmsThresholds } from '../../components/spoolbuddy/AmsUnitCard';
import { ConfigureAmsSlotModal } from '../../components/ConfigureAmsSlotModal';

function getAmsName(amsId: number): string {
  if (amsId <= 3) return `AMS ${String.fromCharCode(65 + amsId)}`;
  if (amsId >= 128 && amsId <= 135) return `AMS HT ${String.fromCharCode(65 + amsId - 128)}`;
  return `AMS ${amsId}`;
}

function mapModelCode(ssdpModel: string | null): string {
  if (!ssdpModel) return '';
  const modelMap: Record<string, string> = {
    'O1D': 'H2D', 'O1E': 'H2D Pro', 'O2D': 'H2D Pro', 'O1C': 'H2C', 'O1S': 'H2S',
    'BL-P001': 'X1C', 'BL-P002': 'X1', 'BL-P003': 'X1E',
    'C11': 'P1S', 'C12': 'P1P', 'C13': 'P2S',
    'N2S': 'A1', 'N1': 'A1 Mini',
    'X1C': 'X1C', 'X1': 'X1', 'X1E': 'X1E', 'P1S': 'P1S', 'P1P': 'P1P', 'P2S': 'P2S',
    'A1': 'A1', 'A1 Mini': 'A1 Mini', 'H2D': 'H2D', 'H2D Pro': 'H2D Pro', 'H2C': 'H2C', 'H2S': 'H2S',
  };
  return modelMap[ssdpModel] || ssdpModel;
}

function isTrayEmpty(tray: AMSTray): boolean {
  return !tray.tray_type || tray.tray_type === '';
}

function trayColorToCSS(color: string | null): string {
  if (!color) return '#808080';
  return `#${color.slice(0, 6)}`;
}

export function SpoolBuddyAmsPage() {
  const { selectedPrinterId, setAlert } = useOutletContext<SpoolBuddyOutletContext>();
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const { data: status } = useQuery<PrinterStatus>({
    queryKey: ['printerStatus', selectedPrinterId],
    queryFn: () => api.getPrinterStatus(selectedPrinterId!),
    enabled: selectedPrinterId !== null,
    staleTime: 30 * 1000,
  });

  const { data: printer } = useQuery({
    queryKey: ['printer', selectedPrinterId],
    queryFn: () => api.getPrinter(selectedPrinterId!),
    enabled: selectedPrinterId !== null,
    staleTime: 60 * 1000,
  });

  const { data: slotPresets } = useQuery({
    queryKey: ['slotPresets', selectedPrinterId],
    queryFn: () => api.getSlotPresets(selectedPrinterId!),
    enabled: selectedPrinterId !== null,
    staleTime: 2 * 60 * 1000,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.getSettings(),
    staleTime: 5 * 60 * 1000,
  });

  const isConnected = status?.connected ?? false;
  const amsUnits = useMemo(() => status?.ams ?? [], [status?.ams]);
  const regularAms = useMemo(() => amsUnits.filter(u => !u.is_ams_ht), [amsUnits]);
  const htAms = useMemo(() => amsUnits.filter(u => u.is_ams_ht), [amsUnits]);
  const trayNow = status?.tray_now ?? 255;
  const isDualNozzle = printer?.nozzle_count === 2 || status?.temperatures?.nozzle_2 !== undefined;
  const vtTrays = useMemo(() => [...(status?.vt_tray ?? [])].sort((a, b) => (a.id ?? 254) - (b.id ?? 254)), [status?.vt_tray]);

  const amsThresholds: AmsThresholds | undefined = settings ? {
    humidityGood: Number(settings.ams_humidity_good) || 40,
    humidityFair: Number(settings.ams_humidity_fair) || 60,
    tempGood: Number(settings.ams_temp_good) || 28,
    tempFair: Number(settings.ams_temp_fair) || 35,
  } : undefined;

  // Cache ams_extruder_map to prevent L/R indicators bouncing on updates
  const cachedAmsExtruderMap = useRef<Record<string, number>>({});
  useEffect(() => {
    if (status?.ams_extruder_map && Object.keys(status.ams_extruder_map).length > 0) {
      cachedAmsExtruderMap.current = status.ams_extruder_map;
    }
  }, [status?.ams_extruder_map]);
  const amsExtruderMap = (status?.ams_extruder_map && Object.keys(status.ams_extruder_map).length > 0)
    ? status.ams_extruder_map
    : cachedAmsExtruderMap.current;

  const getNozzleSide = useCallback((amsId: number): 'L' | 'R' | null => {
    if (!isDualNozzle) return null;
    const mappedExtruderId = amsExtruderMap[String(amsId)];
    const normalizedId = amsId >= 128 ? amsId - 128 : amsId;
    const extruderId = mappedExtruderId !== undefined ? mappedExtruderId : normalizedId;
    // extruder 0 = right, 1 = left
    return extruderId === 1 ? 'L' : 'R';
  }, [isDualNozzle, amsExtruderMap]);

  const [configureSlotModal, setConfigureSlotModal] = useState<{
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
  } | null>(null);

  const getActiveSlotForAms = useCallback((amsId: number): number | null => {
    if (trayNow === 255 || trayNow === 254) return null;
    if (amsId <= 3) {
      const activeAmsId = Math.floor(trayNow / 4);
      if (activeAmsId === amsId) return trayNow % 4;
    }
    if (amsId >= 128 && amsId <= 135) {
      const htIndex = amsId - 128;
      if (trayNow === 16 + htIndex) return 0;
    }
    return null;
  }, [trayNow]);

  const handleAmsSlotClick = useCallback((amsId: number, trayId: number, tray: AMSTray | null) => {
    const globalTrayId = amsId >= 128 ? (amsId - 128) * 4 + trayId + 64 : amsId * 4 + trayId;
    const slotPreset = slotPresets?.[globalTrayId];
    const mappedExtruderId = amsExtruderMap[String(amsId)];
    const normalizedId = amsId >= 128 ? amsId - 128 : amsId;
    const extruderId = mappedExtruderId !== undefined ? mappedExtruderId : normalizedId;
    setConfigureSlotModal({
      amsId,
      trayId,
      trayCount: tray ? (amsId >= 128 ? 1 : 4) : 4,
      trayType: tray?.tray_type || undefined,
      trayColor: tray?.tray_color || undefined,
      traySubBrands: tray?.tray_sub_brands || undefined,
      trayInfoIdx: tray?.tray_info_idx || undefined,
      extruderId: isDualNozzle ? extruderId : undefined,
      caliIdx: tray?.cali_idx,
      savedPresetId: slotPreset?.preset_id,
    });
  }, [slotPresets, amsExtruderMap, isDualNozzle]);

  const handleExtSlotClick = useCallback((extTray: AMSTray) => {
    const extTrayId = extTray.id ?? 254;
    const slotTrayId = extTrayId - 254;
    const extSlotPreset = slotPresets?.[255 * 4 + slotTrayId];
    setConfigureSlotModal({
      amsId: 255,
      trayId: slotTrayId,
      trayCount: 1,
      trayType: extTray.tray_type || undefined,
      trayColor: extTray.tray_color || undefined,
      traySubBrands: extTray.tray_sub_brands || undefined,
      trayInfoIdx: extTray.tray_info_idx || undefined,
      extruderId: isDualNozzle ? (extTrayId === 254 ? 1 : 0) : undefined,
      caliIdx: extTray.cali_idx,
      savedPresetId: extSlotPreset?.preset_id,
    });
  }, [slotPresets, isDualNozzle]);

  // Set alert for low filament in status bar
  useEffect(() => {
    if (!isConnected && selectedPrinterId) {
      setAlert({ type: 'warning', message: t('spoolbuddy.ams.printerDisconnected', 'Printer disconnected') });
      return;
    }
    for (const unit of amsUnits) {
      for (const tray of unit.tray || []) {
        if (tray.remain !== null && tray.remain >= 0 && tray.remain < 15 && tray.tray_type) {
          setAlert({
            type: 'warning',
            message: `Low Filament: ${tray.tray_type} (${getAmsName(unit.id)}) - ${tray.remain}% remaining`,
          });
          return;
        }
      }
    }
    setAlert(null);
  }, [amsUnits, isConnected, selectedPrinterId, setAlert, t]);

  // Build list of single-slot items (AMS-HT + External) for compact rendering
  const singleSlots = useMemo(() => {
    const items: {
      key: string; label: string; tray: AMSTray; isEmpty: boolean; isActive: boolean;
      temp?: number | null; humidity?: number | null; nozzleSide?: 'L' | 'R' | null;
      onClick: () => void;
    }[] = [];

    for (const unit of htAms) {
      const tray = unit.tray?.[0] || {
        id: 0, tray_color: null, tray_type: '', tray_sub_brands: null,
        tray_id_name: null, tray_info_idx: null, remain: -1, k: null,
        cali_idx: null, tag_uid: null, tray_uuid: null, nozzle_temp_min: null, nozzle_temp_max: null,
      };
      items.push({
        key: `ht-${unit.id}`,
        label: getAmsName(unit.id),
        tray,
        isEmpty: isTrayEmpty(tray),
        isActive: getActiveSlotForAms(unit.id) === 0,
        temp: unit.temp,
        humidity: unit.humidity,
        nozzleSide: getNozzleSide(unit.id),
        onClick: () => handleAmsSlotClick(unit.id, 0, isTrayEmpty(tray) ? null : tray),
      });
    }

    for (const extTray of vtTrays) {
      const extTrayId = extTray.id ?? 254;
      const isExtActive = isDualNozzle && trayNow === 254
        ? (extTrayId === 254 && status?.active_extruder === 1) ||
          (extTrayId === 255 && status?.active_extruder === 0)
        : trayNow === extTrayId;
      items.push({
        key: `ext-${extTrayId}`,
        label: isDualNozzle
          ? (extTrayId === 254 ? t('printers.extL', 'Ext-L') : t('printers.extR', 'Ext-R'))
          : t('printers.ext', 'Ext'),
        tray: extTray,
        isEmpty: isTrayEmpty(extTray),
        isActive: isExtActive,
        nozzleSide: isDualNozzle ? (extTrayId === 254 ? 'L' : 'R') : null,
        onClick: () => handleExtSlotClick(extTray),
      });
    }

    return items;
  }, [htAms, vtTrays, isDualNozzle, trayNow, status?.active_extruder, t, getActiveSlotForAms, getNozzleSide, handleAmsSlotClick, handleExtSlotClick]);

  return (
    <div className="h-full flex flex-col p-3">
      <div className="flex-1 min-h-0">
        {!selectedPrinterId ? (
          <div className="flex-1 flex items-center justify-center h-full">
            <div className="text-center text-white/50">
              <p className="text-lg mb-2">{t('spoolbuddy.ams.noPrinter', 'No printer selected')}</p>
              <p className="text-sm">{t('spoolbuddy.ams.selectPrinter', 'Select a printer from the top bar')}</p>
            </div>
          </div>
        ) : !isConnected ? (
          <div className="flex-1 flex items-center justify-center h-full">
            <div className="text-center text-white/50">
              <p className="text-lg mb-2">{t('spoolbuddy.ams.printerDisconnected', 'Printer disconnected')}</p>
            </div>
          </div>
        ) : amsUnits.length === 0 && vtTrays.length === 0 ? (
          <div className="flex-1 flex items-center justify-center h-full">
            <div className="text-center text-white/50">
              <Layers className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p className="text-lg mb-2">{t('spoolbuddy.ams.noData', 'No AMS detected')}</p>
              <p className="text-sm">{t('spoolbuddy.ams.connectAms', 'Connect an AMS to see filament slots')}</p>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3 h-full">
            {/* Regular AMS cards — 4-slot, 2-col grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 flex-1 min-h-0">
              {regularAms.map((unit) => (
                <AmsUnitCard
                  key={unit.id}
                  unit={unit}
                  activeSlot={getActiveSlotForAms(unit.id)}
                  onConfigureSlot={handleAmsSlotClick}
                  isDualNozzle={isDualNozzle}
                  nozzleSide={getNozzleSide(unit.id)}
                  thresholds={amsThresholds}
                />
              ))}
            </div>

            {/* Third row: single-slot cards (AMS-HT + External) */}
            {singleSlots.length > 0 && (
              <div className="flex gap-2 flex-shrink-0">
                {singleSlots.map(({ key, label, tray, isEmpty, isActive, temp, humidity, nozzleSide, onClick }) => {
                  const color = trayColorToCSS(tray.tray_color);
                  return (
                    <div
                      key={key}
                      className={`bg-bambu-dark-secondary rounded-lg px-2 py-1.5 cursor-pointer hover:bg-bambu-dark-secondary/80 transition-all flex items-center gap-2 ${isActive ? 'ring-2 ring-bambu-green' : ''}`}
                      onClick={onClick}
                    >
                      {/* Spool */}
                      <div className="relative w-8 h-8 flex-shrink-0">
                        {isEmpty ? (
                          <div className="w-full h-full rounded-full border-2 border-dashed border-gray-500 flex items-center justify-center">
                            <div className="w-1.5 h-1.5 rounded-full bg-gray-600" />
                          </div>
                        ) : (
                          <svg viewBox="0 0 56 56" className="w-full h-full">
                            <circle cx="28" cy="28" r="26" fill={color} />
                            <circle cx="28" cy="28" r="20" fill={color} style={{ filter: 'brightness(0.85)' }} />
                            <ellipse cx="20" cy="20" rx="6" ry="4" fill="white" opacity="0.3" />
                            <circle cx="28" cy="28" r="8" fill="#2d2d2d" />
                            <circle cx="28" cy="28" r="5" fill="#1a1a1a" />
                          </svg>
                        )}
                        {isActive && (
                          <div className="absolute -bottom-0.5 left-1/2 -translate-x-1/2 w-1.5 h-1.5 bg-bambu-green rounded-full" />
                        )}
                      </div>
                      {/* Info */}
                      <div className="min-w-0">
                        <div className="flex items-center gap-1">
                          <span className="text-[10px] text-white/50 font-medium truncate">{label}</span>
                          {nozzleSide && <NozzleBadge side={nozzleSide} />}
                        </div>
                        <div className="text-xs text-white/80 truncate">
                          {isEmpty ? 'Empty' : tray.tray_type || '?'}
                        </div>
                        {(temp != null || humidity != null) && (
                          <div className="flex items-center gap-1.5">
                            {temp != null && (
                              <TemperatureIndicator
                                temp={temp}
                                goodThreshold={amsThresholds?.tempGood}
                                fairThreshold={amsThresholds?.tempFair}
                              />
                            )}
                            {humidity != null && (
                              <HumidityIndicator
                                humidity={humidity}
                                goodThreshold={amsThresholds?.humidityGood}
                                fairThreshold={amsThresholds?.humidityFair}
                              />
                            )}
                          </div>
                        )}
                      </div>
                      {/* Fill bar */}
                      {!isEmpty && tray.remain != null && tray.remain >= 0 && (
                        <div className="w-1 h-6 bg-bambu-dark-tertiary rounded-full overflow-hidden flex-shrink-0 flex flex-col-reverse">
                          <div
                            className="w-full rounded-full"
                            style={{
                              height: `${tray.remain}%`,
                              backgroundColor: tray.remain > 50 ? '#22c55e' : tray.remain > 20 ? '#f59e0b' : '#ef4444',
                            }}
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {configureSlotModal && selectedPrinterId && (
        <ConfigureAmsSlotModal
          isOpen={!!configureSlotModal}
          onClose={() => setConfigureSlotModal(null)}
          printerId={selectedPrinterId}
          slotInfo={configureSlotModal}
          printerModel={mapModelCode(printer?.model ?? null) || undefined}
          onSuccess={() => {
            queryClient.invalidateQueries({ queryKey: ['slotPresets', selectedPrinterId] });
            queryClient.invalidateQueries({ queryKey: ['printerStatus', selectedPrinterId] });
          }}
        />
      )}
    </div>
  );
}
