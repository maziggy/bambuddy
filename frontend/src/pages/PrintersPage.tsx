import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Plus,
  Wifi,
  WifiOff,
  Thermometer,
  Clock,
  MoreVertical,
  Trash2,
  RefreshCw,
  Box,
  HardDrive,
  AlertTriangle,
  Terminal,
  Power,
  PowerOff,
  Zap,
  Wrench,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { Printer, PrinterCreate } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { FileManagerModal } from '../components/FileManagerModal';
import { MQTTDebugModal } from '../components/MQTTDebugModal';
import { HMSErrorModal } from '../components/HMSErrorModal';
import { PrinterQueueWidget } from '../components/PrinterQueueWidget';

function formatTime(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function CoverImage({ url, printName }: { url: string | null; printName?: string }) {
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  const [showOverlay, setShowOverlay] = useState(false);

  return (
    <>
      <div
        className={`w-20 h-20 flex-shrink-0 rounded-lg overflow-hidden bg-bambu-dark-tertiary flex items-center justify-center ${url && loaded ? 'cursor-pointer' : ''}`}
        onClick={() => url && loaded && setShowOverlay(true)}
      >
        {url && !error ? (
          <>
            <img
              src={url}
              alt="Print preview"
              className={`w-full h-full object-cover ${loaded ? 'block' : 'hidden'}`}
              onLoad={() => setLoaded(true)}
              onError={() => setError(true)}
            />
            {!loaded && <Box className="w-8 h-8 text-bambu-gray" />}
          </>
        ) : (
          <Box className="w-8 h-8 text-bambu-gray" />
        )}
      </div>

      {/* Cover Image Overlay */}
      {showOverlay && url && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-8"
          onClick={() => setShowOverlay(false)}
        >
          <div className="relative max-w-2xl max-h-full">
            <img
              src={url}
              alt="Print preview"
              className="max-w-full max-h-[80vh] rounded-lg shadow-2xl"
            />
            {printName && (
              <p className="text-white text-center mt-4 text-lg">{printName}</p>
            )}
          </div>
        </div>
      )}
    </>
  );
}

interface PrinterMaintenanceInfo {
  due_count: number;
  warning_count: number;
  total_print_hours: number;
}

function PrinterCard({
  printer,
  hideIfDisconnected,
  maintenanceInfo
}: {
  printer: Printer;
  hideIfDisconnected?: boolean;
  maintenanceInfo?: PrinterMaintenanceInfo;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [showMenu, setShowMenu] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showFileManager, setShowFileManager] = useState(false);
  const [showMQTTDebug, setShowMQTTDebug] = useState(false);
  const [showPowerOnConfirm, setShowPowerOnConfirm] = useState(false);
  const [showPowerOffConfirm, setShowPowerOffConfirm] = useState(false);
  const [showHMSModal, setShowHMSModal] = useState(false);

  const { data: status } = useQuery({
    queryKey: ['printerStatus', printer.id],
    queryFn: () => api.getPrinterStatus(printer.id),
    refetchInterval: 30000, // Fallback polling, WebSocket handles real-time
  });

  // Fetch smart plug for this printer
  const { data: smartPlug } = useQuery({
    queryKey: ['smartPlugByPrinter', printer.id],
    queryFn: () => api.getSmartPlugByPrinter(printer.id),
  });

  // Fetch smart plug status if plug exists (faster refresh for energy monitoring)
  const { data: plugStatus } = useQuery({
    queryKey: ['smartPlugStatus', smartPlug?.id],
    queryFn: () => smartPlug ? api.getSmartPlugStatus(smartPlug.id) : null,
    enabled: !!smartPlug,
    refetchInterval: 10000, // 10 seconds for real-time power display
  });

  // Determine if this card should be hidden
  const shouldHide = hideIfDisconnected && status && !status.connected;

  const deleteMutation = useMutation({
    mutationFn: () => api.deletePrinter(printer.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
    },
  });

  const connectMutation = useMutation({
    mutationFn: () => api.connectPrinter(printer.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
  });

  // Smart plug control mutations
  const powerControlMutation = useMutation({
    mutationFn: (action: 'on' | 'off') =>
      smartPlug ? api.controlSmartPlug(smartPlug.id, action) : Promise.reject('No plug'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smartPlugStatus', smartPlug?.id] });
    },
  });

  const toggleAutoOffMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      smartPlug ? api.updateSmartPlug(smartPlug.id, { auto_off: enabled }) : Promise.reject('No plug'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smartPlugByPrinter', printer.id] });
      // Also invalidate the smart-plugs list to keep Settings page in sync
      queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
    },
  });

  if (shouldHide) {
    return null;
  }

  return (
    <Card className="relative">
      <CardContent>
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="text-lg font-semibold text-white">{printer.name}</h3>
            <p className="text-sm text-bambu-gray">
              {printer.model || 'Unknown Model'}
              {maintenanceInfo && maintenanceInfo.total_print_hours > 0 && (
                <span className="ml-2 text-bambu-gray">
                  <Clock className="w-3 h-3 inline-block mr-1" />
                  {Math.round(maintenanceInfo.total_print_hours)}h
                </span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-xs ${
                status?.connected
                  ? 'bg-bambu-green/20 text-bambu-green'
                  : 'bg-red-500/20 text-red-400'
              }`}
            >
              {status?.connected ? (
                <Wifi className="w-3 h-3" />
              ) : (
                <WifiOff className="w-3 h-3" />
              )}
              {status?.connected ? 'Connected' : 'Offline'}
            </span>
            {/* HMS Status Indicator */}
            {status?.connected && (
              <button
                onClick={() => setShowHMSModal(true)}
                className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs cursor-pointer hover:opacity-80 transition-opacity ${
                  status.hms_errors && status.hms_errors.length > 0
                    ? status.hms_errors.some(e => e.severity <= 2)
                      ? 'bg-red-500/20 text-red-400'
                      : 'bg-orange-500/20 text-orange-400'
                    : 'bg-bambu-green/20 text-bambu-green'
                }`}
                title="Click to view HMS errors"
              >
                <AlertTriangle className="w-3 h-3" />
                {status.hms_errors && status.hms_errors.length > 0
                  ? status.hms_errors.length
                  : 'OK'}
              </button>
            )}
            {/* Maintenance Status Indicator - always show */}
            {maintenanceInfo && (
              <button
                onClick={() => navigate('/maintenance')}
                className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs cursor-pointer hover:opacity-80 transition-opacity ${
                  maintenanceInfo.due_count > 0
                    ? 'bg-red-500/20 text-red-400'
                    : maintenanceInfo.warning_count > 0
                    ? 'bg-orange-500/20 text-orange-400'
                    : 'bg-bambu-green/20 text-bambu-green'
                }`}
                title={
                  maintenanceInfo.due_count > 0 || maintenanceInfo.warning_count > 0
                    ? `${maintenanceInfo.due_count > 0 ? `${maintenanceInfo.due_count} maintenance due` : ''}${maintenanceInfo.due_count > 0 && maintenanceInfo.warning_count > 0 ? ', ' : ''}${maintenanceInfo.warning_count > 0 ? `${maintenanceInfo.warning_count} due soon` : ''} - Click to view`
                    : 'All maintenance up to date - Click to view'
                }
              >
                <Wrench className="w-3 h-3" />
                {maintenanceInfo.due_count > 0 || maintenanceInfo.warning_count > 0
                  ? maintenanceInfo.due_count + maintenanceInfo.warning_count
                  : 'OK'}
              </button>
            )}
            <div className="relative">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowMenu(!showMenu)}
              >
                <MoreVertical className="w-4 h-4" />
              </Button>
              {showMenu && (
                <div className="absolute right-0 mt-2 w-48 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg z-10">
                  <button
                    className="w-full px-4 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      connectMutation.mutate();
                      setShowMenu(false);
                    }}
                  >
                    <RefreshCw className="w-4 h-4" />
                    Reconnect
                  </button>
                  <button
                    className="w-full px-4 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      setShowMQTTDebug(true);
                      setShowMenu(false);
                    }}
                  >
                    <Terminal className="w-4 h-4" />
                    MQTT Debug
                  </button>
                  <button
                    className="w-full px-4 py-2 text-left text-sm text-red-400 hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      setShowDeleteConfirm(true);
                      setShowMenu(false);
                    }}
                  >
                    <Trash2 className="w-4 h-4" />
                    Delete
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Delete Confirmation */}
        {showDeleteConfirm && (
          <ConfirmModal
            title="Delete Printer"
            message={`Are you sure you want to delete "${printer.name}"? This will also remove all connection settings.`}
            confirmText="Delete"
            variant="danger"
            onConfirm={() => {
              deleteMutation.mutate();
              setShowDeleteConfirm(false);
            }}
            onCancel={() => setShowDeleteConfirm(false)}
          />
        )}

        {/* Status */}
        {status?.connected && (
          <>
            {/* Current Print or Idle Placeholder */}
            <div className="mb-4 p-3 bg-bambu-dark rounded-lg">
              <div className="flex gap-3">
                {/* Cover Image */}
                <CoverImage
                  url={status.state === 'RUNNING' ? status.cover_url : null}
                  printName={status.state === 'RUNNING' ? (status.subtask_name || status.current_print || undefined) : undefined}
                />
                {/* Print Info */}
                <div className="flex-1 min-w-0">
                  {status.current_print && status.state === 'RUNNING' ? (
                    <>
                      <p className="text-sm text-bambu-gray mb-1">Printing</p>
                      <p className="text-white text-sm mb-2 truncate">
                        {status.subtask_name || status.current_print}
                      </p>
                      <div className="flex items-center justify-between text-sm">
                        <div className="flex-1 bg-bambu-dark-tertiary rounded-full h-2 mr-3">
                          <div
                            className="bg-bambu-green h-2 rounded-full transition-all"
                            style={{ width: `${status.progress || 0}%` }}
                          />
                        </div>
                        <span className="text-white">{Math.round(status.progress || 0)}%</span>
                      </div>
                      <div className="flex items-center gap-3 mt-2 text-xs text-bambu-gray">
                        {status.remaining_time != null && status.remaining_time > 0 && (
                          <span className="flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            {formatTime(status.remaining_time * 60)}
                          </span>
                        )}
                        {status.layer_num != null && status.total_layers != null && status.total_layers > 0 && (
                          <span>
                            Layer {status.layer_num}/{status.total_layers}
                          </span>
                        )}
                      </div>
                    </>
                  ) : (
                    <>
                      <p className="text-sm text-bambu-gray mb-1">Status</p>
                      <p className="text-white text-sm mb-2 capitalize">
                        {status.state?.toLowerCase() || 'Idle'}
                      </p>
                      <div className="flex items-center justify-between text-sm">
                        <div className="flex-1 bg-bambu-dark-tertiary rounded-full h-2 mr-3">
                          <div className="bg-bambu-dark-tertiary h-2 rounded-full" />
                        </div>
                        <span className="text-bambu-gray">—</span>
                      </div>
                      <p className="text-xs text-bambu-gray mt-2">Ready to print</p>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* Queue Widget - shows next scheduled print */}
            {status.state !== 'RUNNING' && (
              <PrinterQueueWidget printerId={printer.id} />
            )}

            {/* Temperatures */}
            {status.temperatures && (
              <div className="grid grid-cols-3 gap-3">
                <div className="text-center p-2 bg-bambu-dark rounded-lg">
                  <Thermometer className="w-4 h-4 mx-auto mb-1 text-orange-400" />
                  <p className="text-xs text-bambu-gray">Nozzle</p>
                  <p className="text-sm text-white">
                    {Math.round(status.temperatures.nozzle || 0)}°C
                  </p>
                </div>
                <div className="text-center p-2 bg-bambu-dark rounded-lg">
                  <Thermometer className="w-4 h-4 mx-auto mb-1 text-blue-400" />
                  <p className="text-xs text-bambu-gray">Bed</p>
                  <p className="text-sm text-white">
                    {Math.round(status.temperatures.bed || 0)}°C
                  </p>
                </div>
                {status.temperatures.chamber !== undefined && (
                  <div className="text-center p-2 bg-bambu-dark rounded-lg">
                    <Thermometer className="w-4 h-4 mx-auto mb-1 text-green-400" />
                    <p className="text-xs text-bambu-gray">Chamber</p>
                    <p className="text-sm text-white">
                      {Math.round(status.temperatures.chamber || 0)}°C
                    </p>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* Smart Plug Controls */}
        {smartPlug && (
          <div className="mt-4 pt-4 border-t border-bambu-dark-tertiary">
            <div className="flex items-center gap-3">
              {/* Plug name and status */}
              <div className="flex items-center gap-2 min-w-0">
                <Zap className="w-4 h-4 text-bambu-gray flex-shrink-0" />
                <span className="text-sm text-white truncate">{smartPlug.name}</span>
                {plugStatus && (
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded flex-shrink-0 ${
                      plugStatus.state === 'ON'
                        ? 'bg-bambu-green/20 text-bambu-green'
                        : plugStatus.state === 'OFF'
                        ? 'bg-red-500/20 text-red-400'
                        : 'bg-bambu-gray/20 text-bambu-gray'
                    }`}
                  >
                    {plugStatus.state || '?'}
                  </span>
                )}
                {/* Power consumption display */}
                {plugStatus?.energy?.power != null && plugStatus.state === 'ON' && (
                  <span className="text-xs text-yellow-400 font-medium flex-shrink-0">
                    {plugStatus.energy.power}W
                  </span>
                )}
              </div>

              {/* Spacer */}
              <div className="flex-1" />

              {/* Power buttons */}
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setShowPowerOnConfirm(true)}
                  disabled={powerControlMutation.isPending || plugStatus?.state === 'ON'}
                  className={`px-2 py-1 text-xs rounded transition-colors flex items-center gap-1 ${
                    plugStatus?.state === 'ON'
                      ? 'bg-bambu-green text-white'
                      : 'bg-bambu-dark text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary'
                  }`}
                >
                  <Power className="w-3 h-3" />
                  On
                </button>
                <button
                  onClick={() => setShowPowerOffConfirm(true)}
                  disabled={powerControlMutation.isPending || plugStatus?.state === 'OFF'}
                  className={`px-2 py-1 text-xs rounded transition-colors flex items-center gap-1 ${
                    plugStatus?.state === 'OFF'
                      ? 'bg-red-500/30 text-red-400'
                      : 'bg-bambu-dark text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary'
                  }`}
                >
                  <PowerOff className="w-3 h-3" />
                  Off
                </button>
              </div>

              {/* Auto-off toggle */}
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className={`text-xs hidden sm:inline ${smartPlug.auto_off_executed ? 'text-bambu-green' : 'text-bambu-gray'}`}>
                  {smartPlug.auto_off_executed ? 'Auto-off done' : 'Auto-off'}
                </span>
                <button
                  onClick={() => toggleAutoOffMutation.mutate(!smartPlug.auto_off)}
                  disabled={toggleAutoOffMutation.isPending || smartPlug.auto_off_executed}
                  title={smartPlug.auto_off_executed ? 'Auto-off was executed - turn printer on to reset' : 'Auto power-off after print'}
                  className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
                    smartPlug.auto_off_executed
                      ? 'bg-bambu-green/50 cursor-not-allowed'
                      : smartPlug.auto_off ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                  }`}
                >
                  <span
                    className={`absolute top-[2px] left-[2px] w-4 h-4 bg-white rounded-full transition-transform ${
                      smartPlug.auto_off || smartPlug.auto_off_executed ? 'translate-x-4' : 'translate-x-0'
                    }`}
                  />
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Connection Info & Actions */}
        <div className="mt-4 pt-4 border-t border-bambu-dark-tertiary flex items-center justify-between">
          <div className="text-xs text-bambu-gray">
            <p>{printer.ip_address}</p>
            <p className="truncate">{printer.serial_number}</p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowFileManager(true)}
            title="Browse printer files"
          >
            <HardDrive className="w-4 h-4" />
            Files
          </Button>
        </div>
      </CardContent>

      {/* File Manager Modal */}
      {showFileManager && (
        <FileManagerModal
          printerId={printer.id}
          printerName={printer.name}
          onClose={() => setShowFileManager(false)}
        />
      )}

      {/* MQTT Debug Modal */}
      {showMQTTDebug && (
        <MQTTDebugModal
          printerId={printer.id}
          printerName={printer.name}
          onClose={() => setShowMQTTDebug(false)}
        />
      )}

      {/* Power On Confirmation */}
      {showPowerOnConfirm && smartPlug && (
        <ConfirmModal
          title="Power On Printer"
          message={`Are you sure you want to turn ON the power for "${printer.name}"?`}
          confirmText="Power On"
          variant="default"
          onConfirm={() => {
            powerControlMutation.mutate('on');
            setShowPowerOnConfirm(false);
          }}
          onCancel={() => setShowPowerOnConfirm(false)}
        />
      )}

      {/* Power Off Confirmation */}
      {showPowerOffConfirm && smartPlug && (
        <ConfirmModal
          title="Power Off Printer"
          message={
            status?.state === 'RUNNING'
              ? `WARNING: "${printer.name}" is currently printing! Are you sure you want to turn OFF the power? This will interrupt the print and may damage the printer.`
              : `Are you sure you want to turn OFF the power for "${printer.name}"?`
          }
          confirmText="Power Off"
          variant="danger"
          onConfirm={() => {
            powerControlMutation.mutate('off');
            setShowPowerOffConfirm(false);
          }}
          onCancel={() => setShowPowerOffConfirm(false)}
        />
      )}

      {/* HMS Error Modal */}
      {showHMSModal && (
        <HMSErrorModal
          printerName={printer.name}
          errors={status?.hms_errors || []}
          onClose={() => setShowHMSModal(false)}
        />
      )}
    </Card>
  );
}

function AddPrinterModal({
  onClose,
  onAdd,
}: {
  onClose: () => void;
  onAdd: (data: PrinterCreate) => void;
}) {
  const [form, setForm] = useState<PrinterCreate>({
    name: '',
    serial_number: '',
    ip_address: '',
    access_code: '',
    model: '',
    auto_archive: true,
  });

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <Card className="w-full max-w-md" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent>
          <h2 className="text-xl font-semibold mb-4">Add Printer</h2>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              onAdd(form);
            }}
            className="space-y-4"
          >
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Name</label>
              <input
                type="text"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="My Printer"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">IP Address</label>
              <input
                type="text"
                required
                pattern="\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.ip_address}
                onChange={(e) => setForm({ ...form, ip_address: e.target.value })}
                placeholder="192.168.1.100"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Serial Number</label>
              <input
                type="text"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.serial_number}
                onChange={(e) => setForm({ ...form, serial_number: e.target.value })}
                placeholder="01P00A000000000"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Access Code</label>
              <input
                type="password"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.access_code}
                onChange={(e) => setForm({ ...form, access_code: e.target.value })}
                placeholder="From printer settings"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Model (optional)</label>
              <select
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.model || ''}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              >
                <option value="">Select model...</option>
                <optgroup label="H2 Series">
                  <option value="H2C">H2C</option>
                  <option value="H2D">H2D</option>
                  <option value="H2S">H2S</option>
                </optgroup>
                <optgroup label="X1 Series">
                  <option value="X1E">X1E</option>
                  <option value="X1C">X1 Carbon</option>
                  <option value="X1">X1</option>
                </optgroup>
                <optgroup label="P Series">
                  <option value="P2S">P2S</option>
                  <option value="P1S">P1S</option>
                  <option value="P1P">P1P</option>
                </optgroup>
                <optgroup label="A1 Series">
                  <option value="A1">A1</option>
                  <option value="A1 Mini">A1 Mini</option>
                </optgroup>
              </select>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="auto_archive"
                checked={form.auto_archive}
                onChange={(e) => setForm({ ...form, auto_archive: e.target.checked })}
                className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
              />
              <label htmlFor="auto_archive" className="text-sm text-bambu-gray">
                Auto-archive completed prints
              </label>
            </div>
            <div className="flex gap-3 pt-4">
              <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
                Cancel
              </Button>
              <Button type="submit" className="flex-1">
                Add Printer
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

export function PrintersPage() {
  const [showAddModal, setShowAddModal] = useState(false);
  const [hideDisconnected, setHideDisconnected] = useState(() => {
    return localStorage.getItem('hideDisconnectedPrinters') === 'true';
  });
  const queryClient = useQueryClient();

  const { data: printers, isLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Fetch maintenance overview for all printers to show badges
  const { data: maintenanceOverview } = useQuery({
    queryKey: ['maintenanceOverview'],
    queryFn: api.getMaintenanceOverview,
    staleTime: 60 * 1000, // 1 minute
  });

  // Create a map of printer_id -> maintenance info for quick lookup
  const maintenanceByPrinter = maintenanceOverview?.reduce(
    (acc, overview) => {
      acc[overview.printer_id] = {
        due_count: overview.due_count,
        warning_count: overview.warning_count,
        total_print_hours: overview.total_print_hours,
      };
      return acc;
    },
    {} as Record<number, PrinterMaintenanceInfo>
  ) || {};

  const addMutation = useMutation({
    mutationFn: api.createPrinter,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      setShowAddModal(false);
    },
  });

  const toggleHideDisconnected = () => {
    const newValue = !hideDisconnected;
    setHideDisconnected(newValue);
    localStorage.setItem('hideDisconnectedPrinters', String(newValue));
  };

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Printers</h1>
          <p className="text-bambu-gray">Manage your Bambu Lab printers</p>
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-bambu-gray cursor-pointer">
            <input
              type="checkbox"
              checked={hideDisconnected}
              onChange={toggleHideDisconnected}
              className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
            />
            Hide offline
          </label>
          <Button onClick={() => setShowAddModal(true)}>
            <Plus className="w-4 h-4" />
            Add Printer
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-bambu-gray">Loading printers...</div>
      ) : printers?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-bambu-gray mb-4">No printers configured yet</p>
            <Button onClick={() => setShowAddModal(true)}>
              <Plus className="w-4 h-4" />
              Add Your First Printer
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {printers?.map((printer) => (
            <PrinterCard
              key={printer.id}
              printer={printer}
              hideIfDisconnected={hideDisconnected}
              maintenanceInfo={maintenanceByPrinter[printer.id]}
            />
          ))}
        </div>
      )}

      {showAddModal && (
        <AddPrinterModal
          onClose={() => setShowAddModal(false)}
          onAdd={(data) => addMutation.mutate(data)}
        />
      )}
    </div>
  );
}
