import { useState, useEffect, useRef } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { X, Save, Loader2, Wifi, WifiOff, CheckCircle, Bell, Clock, LayoutGrid, Search, Plug, Power, Home } from 'lucide-react';
import { api } from '../api/client';
import type { SmartPlug, SmartPlugCreate, SmartPlugUpdate, DiscoveredTasmotaDevice } from '../api/client';
import { Button } from './Button';

interface AddSmartPlugModalProps {
  plug?: SmartPlug | null;
  onClose: () => void;
}

export function AddSmartPlugModal({ plug, onClose }: AddSmartPlugModalProps) {
  const queryClient = useQueryClient();
  const isEditing = !!plug;

  // Plug type selection
  const [plugType, setPlugType] = useState<'tasmota' | 'homeassistant'>(plug?.plug_type || 'tasmota');

  const [name, setName] = useState(plug?.name || '');
  // Tasmota fields
  const [ipAddress, setIpAddress] = useState(plug?.ip_address || '');
  const [username, setUsername] = useState(plug?.username || '');
  const [password, setPassword] = useState(plug?.password || '');
  // Home Assistant fields
  const [haEntityId, setHaEntityId] = useState(plug?.ha_entity_id || '');

  const [printerId, setPrinterId] = useState<number | null>(plug?.printer_id || null);
  const [testResult, setTestResult] = useState<{ success: boolean; state?: string | null; device_name?: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Power alert settings
  const [powerAlertEnabled, setPowerAlertEnabled] = useState(plug?.power_alert_enabled || false);
  const [powerAlertHigh, setPowerAlertHigh] = useState<string>(plug?.power_alert_high?.toString() || '');
  const [powerAlertLow, setPowerAlertLow] = useState<string>(plug?.power_alert_low?.toString() || '');

  // Schedule settings
  const [scheduleEnabled, setScheduleEnabled] = useState(plug?.schedule_enabled || false);
  const [scheduleOnTime, setScheduleOnTime] = useState<string>(plug?.schedule_on_time || '');
  const [scheduleOffTime, setScheduleOffTime] = useState<string>(plug?.schedule_off_time || '');

  // Switchbar visibility
  const [showInSwitchbar, setShowInSwitchbar] = useState(plug?.show_in_switchbar || false);

  // Discovery state
  const [isScanning, setIsScanning] = useState(false);
  const [scanProgress, setScanProgress] = useState({ scanned: 0, total: 0 });
  const [discoveredDevices, setDiscoveredDevices] = useState<DiscoveredTasmotaDevice[]>([]);
  const scanPollRef = useRef<NodeJS.Timeout | null>(null);

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

  // Fetch settings to check if HA is configured
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  // Check if HA is properly configured
  const haConfigured = !!(settings?.ha_enabled && settings?.ha_url && settings?.ha_token);

  // Fetch Home Assistant entities when in HA mode AND HA is configured
  const { data: haEntities, isLoading: haEntitiesLoading } = useQuery({
    queryKey: ['ha-entities'],
    queryFn: api.getHAEntities,
    enabled: plugType === 'homeassistant' && haConfigured,
    retry: false,
    staleTime: 0,
  });

  // Close on Escape key and cleanup scan polling
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      if (scanPollRef.current) {
        clearInterval(scanPollRef.current);
      }
    };
  }, [onClose]);

  // Start scanning for Tasmota devices (auto-detects network)
  const startScan = async () => {
    setIsScanning(true);
    setDiscoveredDevices([]);
    setScanProgress({ scanned: 0, total: 0 });
    setError(null);

    try {
      await api.startTasmotaScan();

      // Poll function to fetch status and devices
      const pollStatus = async () => {
        try {
          const status = await api.getTasmotaScanStatus();
          setScanProgress({ scanned: status.scanned, total: status.total });

          const devices = await api.getDiscoveredTasmotaDevices();
          setDiscoveredDevices(devices);

          if (!status.running) {
            setIsScanning(false);
            if (scanPollRef.current) {
              clearInterval(scanPollRef.current);
              scanPollRef.current = null;
            }
          }
        } catch (e) {
          console.error('Polling error:', e);
        }
      };

      // Poll immediately, then every 500ms
      await pollStatus();
      scanPollRef.current = setInterval(pollStatus, 500);
    } catch (err) {
      setIsScanning(false);
      const errorMsg = err instanceof Error ? err.message : (typeof err === 'string' ? err : JSON.stringify(err));
      setError(errorMsg || 'Failed to start scan');
    }
  };

  // Stop scanning
  const stopScan = async () => {
    try {
      await api.stopTasmotaScan();
    } catch {
      // Ignore stop errors
    }
    setIsScanning(false);
    if (scanPollRef.current) {
      clearInterval(scanPollRef.current);
      scanPollRef.current = null;
    }
  };

  // Select a discovered device
  const selectDevice = (device: DiscoveredTasmotaDevice) => {
    setIpAddress(device.ip_address);
    setName(device.name);
    setTestResult(null);
  };

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

    if (plugType === 'tasmota' && !ipAddress.trim()) {
      setError('IP address is required for Tasmota plugs');
      return;
    }

    if (plugType === 'homeassistant' && !haEntityId) {
      setError('Entity is required for Home Assistant plugs');
      return;
    }

    const data = {
      name: name.trim(),
      plug_type: plugType,
      ip_address: plugType === 'tasmota' ? ipAddress.trim() : null,
      ha_entity_id: plugType === 'homeassistant' ? haEntityId : null,
      username: plugType === 'tasmota' ? (username.trim() || null) : null,
      password: plugType === 'tasmota' ? (password.trim() || null) : null,
      printer_id: printerId,
      // Power alerts
      power_alert_enabled: powerAlertEnabled,
      power_alert_high: powerAlertHigh ? parseFloat(powerAlertHigh) : null,
      power_alert_low: powerAlertLow ? parseFloat(powerAlertLow) : null,
      // Schedule
      schedule_enabled: scheduleEnabled,
      schedule_on_time: scheduleOnTime || null,
      schedule_off_time: scheduleOffTime || null,
      // Switchbar
      show_in_switchbar: showInSwitchbar,
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
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-md max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary flex-shrink-0">
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
        <form onSubmit={handleSubmit} className="p-6 space-y-4 overflow-y-auto">
          {error && (
            <div className="p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-400">
              {error}
            </div>
          )}

          {/* Plug Type Selector - only show when not editing */}
          {!isEditing && (
            <div className="flex gap-2 mb-2">
              <button
                type="button"
                onClick={() => {
                  setPlugType('tasmota');
                  setTestResult(null);
                  setError(null);
                }}
                className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg font-medium transition-colors ${
                  plugType === 'tasmota'
                    ? 'bg-bambu-green text-white'
                    : 'bg-bambu-dark text-bambu-gray hover:text-white border border-bambu-dark-tertiary'
                }`}
              >
                <Plug className="w-4 h-4" />
                Tasmota
              </button>
              <button
                type="button"
                onClick={() => {
                  setPlugType('homeassistant');
                  setTestResult(null);
                  setError(null);
                }}
                className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg font-medium transition-colors ${
                  plugType === 'homeassistant'
                    ? 'bg-bambu-green text-white'
                    : 'bg-bambu-dark text-bambu-gray hover:text-white border border-bambu-dark-tertiary'
                }`}
              >
                <Home className="w-4 h-4" />
                Home Assistant
              </button>
            </div>
          )}

          {/* Discovery Section - only show when not editing and Tasmota is selected */}
          {!isEditing && plugType === 'tasmota' && (
            <div className="space-y-3">
              {/* Scan button - auto-detects network */}
              {isScanning ? (
                <Button type="button" variant="secondary" onClick={stopScan} className="w-full">
                  <X className="w-4 h-4" />
                  Stop Scanning
                </Button>
              ) : (
                <Button type="button" variant="primary" onClick={startScan} className="w-full">
                  <Search className="w-4 h-4" />
                  Discover Tasmota Devices
                </Button>
              )}

              {/* Progress bar */}
              {isScanning && scanProgress.total > 0 && (
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-bambu-gray">
                    <span>Scanning network...</span>
                    <span>{scanProgress.scanned} / {scanProgress.total}</span>
                  </div>
                  <div className="w-full bg-bambu-dark-tertiary rounded-full h-2">
                    <div
                      className="bg-bambu-green h-2 rounded-full transition-all duration-300"
                      style={{ width: `${(scanProgress.scanned / scanProgress.total) * 100}%` }}
                    />
                  </div>
                </div>
              )}

              {/* Discovered devices */}
              {discoveredDevices.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs text-bambu-gray">Found {discoveredDevices.length} device(s) - click to select:</p>
                  <div className="max-h-40 overflow-y-auto space-y-1">
                    {discoveredDevices.map((device) => (
                      <button
                        key={device.ip_address}
                        type="button"
                        onClick={() => selectDevice(device)}
                        className="w-full flex items-center justify-between p-2 bg-bambu-dark hover:bg-bambu-dark-tertiary rounded-lg transition-colors text-left border border-bambu-dark-tertiary"
                      >
                        <div className="flex items-center gap-2">
                          <Plug className="w-4 h-4 text-bambu-green" />
                          <div>
                            <p className="text-sm text-white">{device.name}</p>
                            <p className="text-xs text-bambu-gray">{device.ip_address}</p>
                          </div>
                        </div>
                        {device.state && (
                          <span className={`flex items-center gap-1 text-xs ${
                            device.state === 'ON' ? 'text-bambu-green' : 'text-bambu-gray'
                          }`}>
                            <Power className="w-3 h-3" />
                            {device.state}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {!isScanning && discoveredDevices.length === 0 && scanProgress.total > 0 && (
                <p className="text-xs text-bambu-gray text-center py-2">
                  No Tasmota devices found on your network
                </p>
              )}
            </div>
          )}

          {/* Home Assistant Entity Selector - only show when HA is selected */}
          {plugType === 'homeassistant' && (
            <div className="space-y-3">
              {/* HA not configured */}
              {!haConfigured && (
                <div className="space-y-3">
                  <div className="p-3 bg-yellow-500/20 border border-yellow-500/50 rounded-lg text-sm text-yellow-400">
                    Home Assistant is not configured. Set it up in{' '}
                    <span className="font-medium">Settings → Network → Home Assistant</span>
                  </div>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1 opacity-50">Select Entity *</label>
                    <select
                      disabled
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray cursor-not-allowed opacity-50"
                    >
                      <option>Choose an entity...</option>
                    </select>
                  </div>
                </div>
              )}

              {/* HA configured - show loading/entities */}
              {haConfigured && (
                <>
                  {haEntitiesLoading && (
                    <div className="flex items-center justify-center py-4 text-bambu-gray">
                      <Loader2 className="w-5 h-5 animate-spin mr-2" />
                      Loading entities...
                    </div>
                  )}

                  {haEntities && haEntities.length === 0 && (
                    <div className="p-3 bg-yellow-500/20 border border-yellow-500/50 rounded-lg text-sm text-yellow-400">
                      No switch/light entities found in Home Assistant
                    </div>
                  )}

                  {haEntities && haEntities.length > 0 && (() => {
                    // Filter out entities already configured (except current plug when editing)
                    const configuredEntityIds = existingPlugs
                      ?.filter(p => p.ha_entity_id && p.id !== plug?.id)
                      .map(p => p.ha_entity_id) || [];
                    const availableEntities = haEntities.filter(e => !configuredEntityIds.includes(e.entity_id));

                    return (
                      <div>
                        <label className="block text-sm text-bambu-gray mb-1">Select Entity *</label>
                        <select
                          value={haEntityId}
                          onChange={(e) => {
                            setHaEntityId(e.target.value);
                            // Auto-fill name from entity friendly name
                            const entity = haEntities?.find(ent => ent.entity_id === e.target.value);
                            if (entity && !name) {
                              setName(entity.friendly_name);
                            }
                          }}
                          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                        >
                          <option value="">Choose an entity...</option>
                          {availableEntities.map((entity) => (
                            <option key={entity.entity_id} value={entity.entity_id}>
                              {entity.friendly_name} ({entity.entity_id}) - {entity.state}
                            </option>
                          ))}
                        </select>
                        {configuredEntityIds.length > 0 && (
                          <p className="text-xs text-bambu-gray mt-1">
                            {configuredEntityIds.length} entity(s) already configured
                          </p>
                        )}
                      </div>
                    );
                  })()}

                  {haEntityId && haEntities && (
                    <div className="p-3 bg-bambu-green/20 border border-bambu-green/50 rounded-lg text-sm text-bambu-green flex items-center gap-2">
                      <CheckCircle className="w-5 h-5" />
                      <div>
                        <p className="font-medium">Entity selected</p>
                        <p className="text-xs opacity-80">
                          {haEntities.find(e => e.entity_id === haEntityId)?.friendly_name} - {haEntities.find(e => e.entity_id === haEntityId)?.state}
                        </p>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* IP Address - only show for Tasmota */}
          {plugType === 'tasmota' && (
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
          )}

          {/* Test Result - only show for Tasmota */}
          {plugType === 'tasmota' && testResult && (
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

          {/* Authentication (optional) - only show for Tasmota */}
          {plugType === 'tasmota' && (
            <>
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
            </>
          )}

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

          {/* Power Alerts */}
          <div className="border-t border-bambu-dark-tertiary pt-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Bell className="w-4 h-4 text-bambu-green" />
                <span className="text-white font-medium">Power Alerts</span>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={powerAlertEnabled}
                  onChange={(e) => setPowerAlertEnabled(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
              </label>
            </div>
            {powerAlertEnabled && (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">Alert if above (W)</label>
                    <input
                      type="number"
                      value={powerAlertHigh}
                      onChange={(e) => setPowerAlertHigh(e.target.value)}
                      placeholder="e.g. 200"
                      min="0"
                      max="5000"
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">Alert if below (W)</label>
                    <input
                      type="number"
                      value={powerAlertLow}
                      onChange={(e) => setPowerAlertLow(e.target.value)}
                      placeholder="e.g. 10"
                      min="0"
                      max="5000"
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                </div>
                <p className="text-xs text-bambu-gray">
                  Get notified when power consumption crosses these thresholds. Leave empty to disable that direction.
                </p>
              </div>
            )}
          </div>

          {/* Schedule */}
          <div className="border-t border-bambu-dark-tertiary pt-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Clock className="w-4 h-4 text-bambu-green" />
                <span className="text-white font-medium">Daily Schedule</span>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={scheduleEnabled}
                  onChange={(e) => setScheduleEnabled(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
              </label>
            </div>
            {scheduleEnabled && (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">Turn On at</label>
                    <input
                      type="time"
                      value={scheduleOnTime}
                      onChange={(e) => setScheduleOnTime(e.target.value)}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">Turn Off at</label>
                    <input
                      type="time"
                      value={scheduleOffTime}
                      onChange={(e) => setScheduleOffTime(e.target.value)}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                </div>
                <p className="text-xs text-bambu-gray">
                  Automatically turn the plug on/off at these times daily. Leave empty to skip that action.
                </p>
              </div>
            )}
          </div>

          {/* Switchbar Visibility */}
          <div className="border-t border-bambu-dark-tertiary pt-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <LayoutGrid className="w-4 h-4 text-bambu-green" />
                <div>
                  <span className="text-white font-medium">Show in Switchbar</span>
                  <p className="text-xs text-bambu-gray">Quick access from sidebar</p>
                </div>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={showInSwitchbar}
                  onChange={(e) => setShowInSwitchbar(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
              </label>
            </div>
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
