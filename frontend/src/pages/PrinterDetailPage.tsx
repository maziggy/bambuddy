import { useEffect } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Loader2, Wifi, WifiOff } from 'lucide-react';
import { api } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { usePrinterMotionDisabled } from '../hooks/usePrinterMotionGuard';
import { getPrinterImage } from '../utils/printer';
import { getPrinterControlCapabilities } from '../utils/printerCapabilities';
import { PrinterCameraPanel } from '../components/printer-detail/PrinterCameraPanel';
import { PrinterTemperatureControls } from '../components/printer-detail/PrinterTemperatureControls';
import { PrinterFanControls } from '../components/printer-detail/PrinterFanControls';
import { PrinterMotionControls } from '../components/printer-detail/PrinterMotionControls';
import { PrinterExtruderControls } from '../components/printer-detail/PrinterExtruderControls';
import { PrinterMiscControls } from '../components/printer-detail/PrinterMiscControls';
import { PrinterSettingsSection } from '../components/printer-detail/PrinterSettingsSection';

export function PrinterDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { printerId } = useParams<{ printerId: string }>();
  const id = parseInt(printerId || '0', 10);
  const { hasPermission } = useAuth();

  const { data: printer, isLoading: printerLoading } = useQuery({
    queryKey: ['printer', id],
    queryFn: () => api.getPrinter(id),
    enabled: id > 0,
  });

  const { data: status } = useQuery({
    queryKey: ['printerStatus', id],
    queryFn: () => api.getPrinterStatus(id),
    refetchInterval: 30000,
    enabled: id > 0,
  });

  const motionDisabled = usePrinterMotionDisabled(status);
  const canControl = hasPermission('printers:control');
  const canUpdate = hasPermission('printers:update');

  useEffect(() => {
    if (printer) {
      document.title = `${printer.name} - Bambuddy`;
    }
    return () => {
      document.title = 'Bambuddy';
    };
  }, [printer]);

  if (id <= 0) {
    return (
      <div className="p-6 text-bambu-gray">
        {t('printerDetail.invalidId')}
        <Link to="/" className="text-bambu-green ml-2 hover:underline">
          {t('printerDetail.backToPrinters')}
        </Link>
      </div>
    );
  }

  if (printerLoading || !printer) {
    return (
      <div className="flex items-center justify-center min-h-[40vh]">
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      </div>
    );
  }

  const derivedStatus = status?.stg_cur_name || status?.state || t('printerDetail.unknown');
  const capabilities = getPrinterControlCapabilities(printer, status);

  return (
    <div className="flex flex-col h-full max-h-[calc(100vh-4rem)]">
      <header className="flex items-center gap-3 px-4 py-3 border-b border-bambu-dark-tertiary flex-shrink-0">
        <button
          type="button"
          onClick={() => navigate('/')}
          className="p-2 rounded-lg hover:bg-bambu-dark-secondary text-bambu-gray hover:text-white transition-colors"
          aria-label={t('printerDetail.backToPrinters')}
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <img
          src={getPrinterImage(printer.model)}
          alt=""
          className="w-10 h-10 object-contain rounded bg-bambu-dark flex-shrink-0"
        />
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold text-white truncate">{printer.name}</h1>
          <p className="text-sm text-bambu-gray truncate">
            {printer.model || t('printerDetail.unknownModel')}
            {status?.nozzles?.[0]?.nozzle_diameter && (
              <span className="ml-1.5">• {status.nozzles[0].nozzle_diameter}mm</span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {status?.connected ? (
            <span className="flex items-center gap-1 text-xs text-status-ok">
              <Wifi className="w-4 h-4" />
              {derivedStatus}
            </span>
          ) : (
            <span className="flex items-center gap-1 text-xs text-status-error">
              <WifiOff className="w-4 h-4" />
              {t('printers.connection.offline')}
            </span>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-auto p-4">
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4 max-w-[1600px] mx-auto">
          <div className="lg:col-span-3 min-h-[320px]">
            <PrinterCameraPanel printer={printer} status={status} canControl={canControl} />
          </div>

          <div className="lg:col-span-2 space-y-4">
            <div className="rounded-xl border border-bambu-dark-tertiary bg-bambu-dark-secondary p-4 space-y-4 max-h-[calc(100vh-8rem)] overflow-y-auto">
              <h2 className="text-sm font-semibold text-white">{t('printerDetail.controlPanel')}</h2>

              {status ? (
                <>
                  <PrinterTemperatureControls
                    printerId={id}
                    status={status}
                    capabilities={capabilities}
                    canControl={canControl}
                  />
                  <PrinterFanControls
                    printerId={id}
                    capabilities={capabilities}
                    coolingFanSpeed={status.cooling_fan_speed}
                    auxFanSpeed={status.big_fan1_speed}
                    chamberFanSpeed={status.big_fan2_speed}
                    canControl={canControl}
                    connected={status.connected}
                  />
                  <PrinterMiscControls
                    printerId={id}
                    status={status}
                    capabilities={capabilities}
                    canControl={canControl}
                  />
                  <PrinterMotionControls
                    printerId={id}
                    motionDisabled={motionDisabled}
                    canControl={canControl}
                  />
                  <PrinterExtruderControls
                    printerId={id}
                    status={status}
                    motionDisabled={motionDisabled}
                    canControl={canControl}
                  />
                </>
              ) : (
                <p className="text-sm text-bambu-gray">{t('printerDetail.waitingForStatus')}</p>
              )}

              <PrinterSettingsSection printer={printer} canUpdate={canUpdate} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
