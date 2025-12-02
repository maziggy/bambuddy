import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { PrinterStatus } from '../api/client';
import { CameraFeed } from '../components/control/CameraFeed';
import { PrintStatus } from '../components/control/PrintStatus';
import { TemperatureColumn } from '../components/control/TemperatureColumn';
import { JogPad } from '../components/control/JogPad';
import { BedControls } from '../components/control/BedControls';
import { ExtruderControls } from '../components/control/ExtruderControls';
import { AMSSectionDual } from '../components/control/AMSSectionDual';
import { Loader2, WifiOff, Video, Webcam, Settings } from 'lucide-react';

export function ControlPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);

  // Fetch all printers
  const { data: printers, isLoading: loadingPrinters } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Get statuses for all printers
  const { data: statuses } = useQuery({
    queryKey: ['printerStatuses'],
    queryFn: async () => {
      if (!printers) return {};
      const statusMap: Record<number, PrinterStatus> = {};
      await Promise.all(
        printers.map(async (p) => {
          try {
            statusMap[p.id] = await api.getPrinterStatus(p.id);
          } catch {
            // Printer offline
          }
        })
      );
      return statusMap;
    },
    enabled: !!printers && printers.length > 0,
    refetchInterval: 2000,
  });

  // Initialize selected printer from URL or first printer
  useEffect(() => {
    const printerParam = searchParams.get('printer');
    if (printerParam) {
      const id = parseInt(printerParam, 10);
      if (!isNaN(id)) {
        setSelectedPrinterId(id);
        return;
      }
    }
    // Default to first printer
    if (printers && printers.length > 0 && !selectedPrinterId) {
      setSelectedPrinterId(printers[0].id);
    }
  }, [printers, searchParams, selectedPrinterId]);

  // Update URL when printer changes
  const handlePrinterSelect = (printerId: number) => {
    setSelectedPrinterId(printerId);
    setSearchParams({ printer: String(printerId) });
  };

  const selectedPrinter = printers?.find((p) => p.id === selectedPrinterId);
  const selectedStatus = selectedPrinterId ? statuses?.[selectedPrinterId] : null;

  if (loadingPrinters) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      </div>
    );
  }

  if (!printers || printers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-screen text-bambu-gray">
        <WifiOff className="w-16 h-16 mb-4" />
        <p className="text-xl">No printers configured</p>
        <p className="text-sm mt-2">Add a printer in the Printers page first</p>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-bambu-dark">
      {/* Printer Tabs */}
      <div className="bg-bambu-dark-secondary border-b border-bambu-dark-tertiary">
        <div className="flex overflow-x-auto">
          {printers.map((printer) => {
            const status = statuses?.[printer.id];
            const isConnected = status?.connected ?? false;
            const isSelected = printer.id === selectedPrinterId;

            return (
              <button
                key={printer.id}
                onClick={() => handlePrinterSelect(printer.id)}
                className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors whitespace-nowrap border-b-2 ${
                  isSelected
                    ? 'border-bambu-green text-bambu-green bg-bambu-dark'
                    : 'border-transparent text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary'
                }`}
              >
                <span
                  className={`w-2 h-2 rounded-full ${
                    isConnected ? 'bg-bambu-green' : 'bg-red-500'
                  }`}
                />
                {printer.name}
                {status?.state && status.state !== 'IDLE' && (
                  <span className="text-xs px-2 py-0.5 rounded bg-bambu-dark-tertiary">
                    {status.state}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Main Content - Bambu Studio Layout */}
      {selectedPrinter && (
        <div className="flex-1 flex overflow-hidden">
          {/* Left Panel - Camera & Print Progress */}
          <div className="flex-1 flex flex-col bg-bambu-dark">
            {/* Camera Header Icons - same height as Control header */}
            <div className="flex items-center justify-end gap-2 px-3 py-2.5 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary min-h-[44px]">
              <button className="p-1.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white">
                <img src="/icons/micro-sd.svg" alt="SD Card" className="w-4 h-4 icon-theme" />
              </button>
              <button className="p-1.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white">
                <Video className="w-4 h-4" />
              </button>
              <button className="p-1.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white">
                <Webcam className="w-4 h-4" />
              </button>
              <button className="p-1.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white">
                <Settings className="w-4 h-4" />
              </button>
            </div>

            {/* Camera Feed - Embedded directly */}
            <div className="flex-1 bg-black">
              <CameraFeed
                printerId={selectedPrinter.id}
                isConnected={selectedStatus?.connected ?? false}
              />
            </div>

            {/* Status Bar */}
            <div className="h-1 bg-bambu-green" />

            {/* Print Progress with integrated controls */}
            <div className="bg-bambu-dark-secondary p-4 px-5">
              <PrintStatus
                printerId={selectedPrinter.id}
                status={selectedStatus}
              />
            </div>
          </div>

          {/* Right Panel - Control */}
          <div className="w-[680px] flex flex-col bg-bambu-dark-secondary border-l border-bambu-dark-tertiary overflow-y-auto">
            {/* Control Header - same height as Camera header */}
            <div className="flex items-center justify-between px-3 py-2.5 border-b border-bambu-dark-tertiary min-h-[44px]">
              <span className="text-sm text-bambu-gray">Control</span>
              <div className="flex gap-2">
                <button className="px-4 py-1.5 text-xs rounded bg-bambu-green text-white hover:bg-bambu-green-dark">
                  Printer Parts
                </button>
                <button className="px-4 py-1.5 text-xs rounded bg-bambu-green text-white hover:bg-bambu-green-dark">
                  Print Options
                </button>
                <button className="px-4 py-1.5 text-xs rounded bg-bambu-green text-white hover:bg-bambu-green-dark">
                  Calibration
                </button>
              </div>
            </div>

            {/* Connection Warning */}
            {!selectedStatus?.connected && (
              <div className="m-3 p-3 bg-red-500/20 border border-red-500/50 rounded-lg flex items-center gap-3">
                <WifiOff className="w-4 h-4 text-red-500" />
                <span className="text-sm text-red-400">
                  Printer is not connected. Controls are disabled.
                </span>
              </div>
            )}

            {/* Control Body */}
            <div className="flex-1 p-4 bg-bambu-dark">
              {/* Top Section: Temp + Movement + Extruder */}
              <div className="mb-4 bg-bambu-dark-tertiary rounded-[10px] p-3">
                <div className="flex gap-4 bg-bambu-dark-secondary rounded-[8px] p-4 overflow-hidden" style={{ minHeight: '300px' }}>
                  {/* Temperature Column */}
                  <TemperatureColumn
                  printerId={selectedPrinter.id}
                  status={selectedStatus}
                  nozzleCount={selectedPrinter.nozzle_count}
                />

                {/* Movement Column */}
                <div className="flex-1 flex gap-6 items-center justify-center">
                  {/* Jog Section */}
                  <div className="flex flex-col items-center">
                    <JogPad
                      printerId={selectedPrinter.id}
                      status={selectedStatus}
                    />
                    <BedControls
                      printerId={selectedPrinter.id}
                      status={selectedStatus}
                    />
                  </div>

                  {/* Extruder Section */}
                  <ExtruderControls
                    printerId={selectedPrinter.id}
                    status={selectedStatus}
                    nozzleCount={selectedPrinter.nozzle_count}
                  />
                </div>
                </div>
              </div>

              {/* AMS Section */}
              <AMSSectionDual
                printerId={selectedPrinter.id}
                status={selectedStatus}
                nozzleCount={selectedPrinter.nozzle_count}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
