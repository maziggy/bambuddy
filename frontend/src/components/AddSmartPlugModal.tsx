import { useState, useEffect } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { X, Save, Loader2, Wifi, WifiOff, CheckCircle } from 'lucide-react';
import { api } from '../api/client';
import type { SmartPlug, SmartPlugCreate, SmartPlugUpdate } from '../api/client';
import { Button } from './Button';

interface AddSmartPlugModalProps {
  plug?: SmartPlug | null;
  onClose: () => void;
}

export function AddSmartPlugModal({ plug, onClose }: AddSmartPlugModalProps) {
  const queryClient = useQueryClient();
  const isEditing = !!plug;

  const [name, setName] = useState(plug?.name || '');
  const [ipAddress, setIpAddress] = useState(plug?.ip_address || '');
  const [username, setUsername] = useState(plug?.username || '');
  const [password, setPassword] = useState(plug?.password || '');
  const [printerId, setPrinterId] = useState<number | null>(plug?.printer_id || null);
  const [testResult, setTestResult] = useState<{ success: boolean; state?: string | null; device_name?: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch printers for linking
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Fetch existing plugs to check for conflicts
  const { data: existingPlugs } = useQuery({
    queryKey: ['smart-plugs'],
    queryFn: api.getSmartPlugs,
  });

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Test connection mutation
  const testMutation = useMutation({
    mutationFn: () => api.testSmartPlugConnection(ipAddress, username || null, password || null),
    onSuccess: (result) => {
      setTestResult(result);
      setError(null);
      // Auto-fill name from device if empty
      if (!name && result.device_name) {
        setName(result.device_name);
      }
    },
    onError: (err: Error) => {
      setTestResult(null);
      setError(err.message);
    },
  });

  // Create mutation
  const createMutation = useMutation({
    mutationFn: (data: SmartPlugCreate) => api.createSmartPlug(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
      onClose();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: (data: SmartPlugUpdate) => api.updateSmartPlug(plug!.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
      onClose();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  // Filter out printers that already have a plug assigned (except current plug's printer)
  const availablePrinters = printers?.filter(p => {
    const hasPlug = existingPlugs?.some(ep => ep.printer_id === p.id && ep.id !== plug?.id);
    return !hasPlug;
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError('Name is required');
      return;
    }
    if (!ipAddress.trim()) {
      setError('IP address is required');
      return;
    }

    const data = {
      name: name.trim(),
      ip_address: ipAddress.trim(),
      username: username.trim() || null,
      password: password.trim() || null,
      printer_id: printerId,
    };

    if (isEditing) {
      updateMutation.mutate(data);
    } else {
      createMutation.mutate(data);
    }
  };

  const isPending = createMutation.isPending || updateMutation.isPending;

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">
            {isEditing ? 'Edit Smart Plug' : 'Add Smart Plug'}
          </h2>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && (
            <div className="p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-400">
              {error}
            </div>
          )}

          {/* IP Address */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">IP Address *</label>
            <div className="flex gap-2">
              <input
                type="text"
                value={ipAddress}
                onChange={(e) => {
                  setIpAddress(e.target.value);
                  setTestResult(null);
                }}
                placeholder="192.168.1.100"
                className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              />
              <Button
                type="button"
                variant="secondary"
                onClick={() => testMutation.mutate()}
                disabled={!ipAddress.trim() || testMutation.isPending}
              >
                {testMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Wifi className="w-4 h-4" />
                )}
                Test
              </Button>
            </div>
          </div>

          {/* Test Result */}
          {testResult && (
            <div className={`p-3 rounded-lg flex items-center gap-2 ${
              testResult.success
                ? 'bg-bambu-green/20 border border-bambu-green/50 text-bambu-green'
                : 'bg-red-500/20 border border-red-500/50 text-red-400'
            }`}>
              {testResult.success ? (
                <>
                  <CheckCircle className="w-5 h-5" />
                  <div>
                    <p className="font-medium">Connected!</p>
                    <p className="text-sm opacity-80">
                      {testResult.device_name && `Device: ${testResult.device_name} - `}
                      State: {testResult.state}
                    </p>
                  </div>
                </>
              ) : (
                <>
                  <WifiOff className="w-5 h-5" />
                  <span>Connection failed</span>
                </>
              )}
            </div>
          )}

          {/* Name */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">Name *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Living Room Plug"
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            />
          </div>

          {/* Authentication (optional) */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Username</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="********"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              />
            </div>
          </div>
          <p className="text-xs text-bambu-gray -mt-2">
            Leave empty if your Tasmota device doesn't require authentication
          </p>

          {/* Link to Printer */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">Link to Printer</label>
            <select
              value={printerId ?? ''}
              onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="">No printer (manual control only)</option>
              {availablePrinters?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <p className="text-xs text-bambu-gray mt-1">
              Linking enables automatic on/off when prints start/complete
            </p>
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <Button
              type="button"
              variant="secondary"
              onClick={onClose}
              className="flex-1"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={isPending}
              className="flex-1"
            >
              {isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {isEditing ? 'Save' : 'Add'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
