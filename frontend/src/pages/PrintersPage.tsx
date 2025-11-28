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
} from 'lucide-react';
import { api } from '../api/client';
import type { Printer, PrinterCreate } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { FileManagerModal } from '../components/FileManagerModal';

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

function PrinterCard({ printer }: { printer: Printer }) {
  const queryClient = useQueryClient();
  const [showMenu, setShowMenu] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showFileManager, setShowFileManager] = useState(false);

  const { data: status } = useQuery({
    queryKey: ['printerStatus', printer.id],
    queryFn: () => api.getPrinterStatus(printer.id),
    refetchInterval: 30000, // Fallback polling, WebSocket handles real-time
  });

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

  return (
    <Card className="relative">
      <CardContent>
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="text-lg font-semibold text-white">{printer.name}</h3>
            <p className="text-sm text-bambu-gray">{printer.model || 'Unknown Model'}</p>
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
            {/* Printer State */}
            <div className="mb-4">
              <p className="text-sm text-bambu-gray mb-1">Status</p>
              <p className="text-white font-medium capitalize">
                {status.state?.toLowerCase() || 'Idle'}
              </p>
            </div>

            {/* Current Print */}
            {status.current_print && status.state === 'RUNNING' && (
              <div className="mb-4 p-3 bg-bambu-dark rounded-lg">
                <div className="flex gap-3">
                  {/* Cover Image */}
                  <CoverImage url={status.cover_url} printName={status.subtask_name || status.current_print || undefined} />
                  {/* Print Info */}
                  <div className="flex-1 min-w-0">
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
                  </div>
                </div>
              </div>
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
  const queryClient = useQueryClient();

  const { data: printers, isLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const addMutation = useMutation({
    mutationFn: api.createPrinter,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      setShowAddModal(false);
    },
  });

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Printers</h1>
          <p className="text-bambu-gray">Manage your Bambu Lab printers</p>
        </div>
        <Button onClick={() => setShowAddModal(true)}>
          <Plus className="w-4 h-4" />
          Add Printer
        </Button>
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
            <PrinterCard key={printer.id} printer={printer} />
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
