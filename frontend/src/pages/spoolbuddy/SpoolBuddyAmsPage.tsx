import { useEffect, useMemo } from 'react';
import { useOutletContext } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Layers } from 'lucide-react';
import type { SpoolBuddyOutletContext } from '../../components/spoolbuddy/SpoolBuddyLayout';
import { api } from '../../api/client';
import type { PrinterStatus } from '../../api/client';
import { AmsUnitCard } from '../../components/spoolbuddy/AmsUnitCard';

function getAmsName(amsId: number): string {
  if (amsId <= 3) return `AMS ${String.fromCharCode(65 + amsId)}`;
  if (amsId >= 128 && amsId <= 135) return `AMS HT ${String.fromCharCode(65 + amsId - 128)}`;
  return `AMS ${amsId}`;
}

export function SpoolBuddyAmsPage() {
  const { selectedPrinterId, setAlert } = useOutletContext<SpoolBuddyOutletContext>();
  const { t } = useTranslation();

  const { data: status } = useQuery<PrinterStatus>({
    queryKey: ['printerStatus', selectedPrinterId],
    queryFn: () => api.getPrinterStatus(selectedPrinterId!),
    enabled: selectedPrinterId !== null,
    staleTime: 30 * 1000,
  });

  const isConnected = status?.connected ?? false;
  const amsUnits = useMemo(() => status?.ams ?? [], [status?.ams]);
  const trayNow = status?.tray_now ?? 255;

  const getActiveSlotForAms = (amsId: number): number | null => {
    if (trayNow === 255 || trayNow === 254) return null;
    if (amsId <= 3) {
      const activeAmsId = Math.floor(trayNow / 4);
      if (activeAmsId === amsId) return trayNow % 4;
    }
    // AMS-HT: tray_now 16-23 maps to AMS-HT 128-135
    if (amsId >= 128 && amsId <= 135) {
      const htIndex = amsId - 128;
      if (trayNow === 16 + htIndex) return 0;
    }
    return null;
  };

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

  return (
    <div className="h-full flex flex-col p-4">
      <div className="flex-1 min-h-0 overflow-y-auto">
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
        ) : amsUnits.length === 0 ? (
          <div className="flex-1 flex items-center justify-center h-full">
            <div className="text-center text-white/50">
              <Layers className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p className="text-lg mb-2">{t('spoolbuddy.ams.noData', 'No AMS detected')}</p>
              <p className="text-sm">{t('spoolbuddy.ams.connectAms', 'Connect an AMS to see filament slots')}</p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {amsUnits.map((unit) => (
              <AmsUnitCard
                key={unit.id}
                unit={unit}
                activeSlot={getActiveSlotForAms(unit.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
