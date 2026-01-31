import { useState, useEffect, useRef } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Save, Loader2, Wifi, WifiOff, CheckCircle, Bell, Clock, LayoutGrid, Search, Plug, Power, Home } from 'lucide-react';
import { api } from '../api/client';
import type { SmartPlug, SmartPlugCreate, SmartPlugUpdate, DiscoveredTasmotaDevice } from '../api/client';
import { Button } from './Button';

interface AddSmartPlugModalProps {
  plug?: SmartPlug | null;
  onClose: () => void;
}

export function AddSmartPlugModal({ plug, onClose }: AddSmartPlugModalProps) {
  const { t } = useTranslation();
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
  // HA energy sensor entities (optional)
  const [haPowerEntity, setHaPowerEntity] = useState(plug?.ha_power_entity || '');
  const [haEnergyTodayEntity, setHaEnergyTodayEntity] = useState(plug?.ha_energy_today_entity || '');
  const [haEnergyTotalEntity, setHaEnergyTotalEntity] = useState(plug?.ha_energy_total_entity || '');
  // HA entity search
  const [haEntitySearch, setHaEntitySearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [isEntityDropdownOpen, setIsEntityDropdownOpen] = useState(false);
  const entityDropdownRef = useRef<HTMLDivElement>(null);

  // Energy sensor search states
  const [powerSensorSearch, setPowerSensorSearch] = useState('');
  const [isPowerDropdownOpen, setIsPowerDropdownOpen] = useState(false);
  const powerDropdownRef = useRef<HTMLDivElement>(null);

  const [energyTodaySearch, setEnergyTodaySearch] = useState('');
  const [isEnergyTodayDropdownOpen, setIsEnergyTodayDropdownOpen] = useState(false);
  const energyTodayDropdownRef = useRef<HTMLDivElement>(null);

  const [energyTotalSearch, setEnergyTotalSearch] = useState('');
  const [isEnergyTotalDropdownOpen, setIsEnergyTotalDropdownOpen] = useState(false);
  const energyTotalDropdownRef = useRef<HTMLDivElement>(null);

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

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(haEntitySearch);
    }, 300);
    return () => clearTimeout(timer);
  }, [haEntitySearch]);

  // Close dropdowns when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (entityDropdownRef.current && !entityDropdownRef.current.contains(e.target as Node)) {
        setIsEntityDropdownOpen(false);
      }
      if (powerDropdownRef.current && !powerDropdownRef.current.contains(e.target as Node)) {
        setIsPowerDropdownOpen(false);
      }
      if (energyTodayDropdownRef.current && !energyTodayDropdownRef.current.contains(e.target as Node)) {
        setIsEnergyTodayDropdownOpen(false);
      }
      if (energyTotalDropdownRef.current && !energyTotalDropdownRef.current.contains(e.target as Node)) {
        setIsEnergyTotalDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Fetch Home Assistant entities when in HA mode AND HA is configured
  const { data: haEntities, isLoading: haEntitiesLoading, error: haEntitiesError } = useQuery({
    queryKey: ['ha-entities', debouncedSearch],
    queryFn: () => api.getHAEntities(debouncedSearch || undefined),
    enabled: plugType === 'homeassistant' && haConfigured,
    retry: false,
    staleTime: 0,
  });

  // Fetch Home Assistant sensor entities for energy monitoring
  const { data: haSensorEntities } = useQuery({
    queryKey: ['ha-sensor-entities'],
    queryFn: api.getHASensorEntities,
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
      setError(errorMsg || t('addSmartPlug.failedToStartScan'));
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
      setError(t('addSmartPlug.nameRequired'));
      return;
    }

    if (plugType === 'tasmota' && !ipAddress.trim()) {
      setError(t('addSmartPlug.ipRequired'));
      return;
    }

    if (plugType === 'homeassistant' && !haEntityId) {
      setError(t('addSmartPlug.entityRequired'));
      return;
    }

    const data = {
      name: name.trim(),
      plug_type: plugType,
      ip_address: plugType === 'tasmota' ? ipAddress.trim() : null,
      ha_entity_id: plugType === 'homeassistant' ? haEntityId : null,
      // HA energy sensor entities (optional)
      ha_power_entity: plugType === 'homeassistant' ? (haPowerEntity || null) : null,
      ha_energy_today_entity: plugType === 'homeassistant' ? (haEnergyTodayEntity || null) : null,
      ha_energy_total_entity: plugType === 'homeassistant' ? (haEnergyTotalEntity || null) : null,
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
            {isEditing ? t('addSmartPlug.editTitle') : t('addSmartPlug.addTitle')}
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
                {t('addSmartPlug.tasmota')}
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
                {t('addSmartPlug.homeAssistant')}
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
                  {t('addSmartPlug.stopScanning')}
                </Button>
              ) : (
                <Button type="button" variant="primary" onClick={startScan} className="w-full">
                  <Search className="w-4 h-4" />
                  {t('addSmartPlug.discoverDevices')}
                </Button>
              )}

              {/* Progress bar */}
              {isScanning && scanProgress.total > 0 && (
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-bambu-gray">
                    <span>{t('addSmartPlug.scanningNetwork')}</span>
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
                  <p className="text-xs text-bambu-gray">{t('addSmartPlug.foundDevices', { count: discoveredDevices.length })}</p>
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
                  {t('addSmartPlug.noDevicesFound')}
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
                    {t('addSmartPlug.haNotConfigured')}{' '}
                    <span className="font-medium">{t('addSmartPlug.haNotConfiguredPath')}</span>
                  </div>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1 opacity-50">{t('addSmartPlug.selectEntity')} *</label>
                    <select
                      disabled
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray cursor-not-allowed opacity-50"
                    >
                      <option>{t('addSmartPlug.chooseEntity')}</option>
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
                      {t('addSmartPlug.loadingEntities')}
                    </div>
                  )}

                  {haEntitiesError && (
                    <div className="p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-400">
                      {t('addSmartPlug.failedToLoadEntities', { error: (haEntitiesError as Error).message })}
                    </div>
                  )}

                  {/* Searchable Entity Dropdown */}
                  {(() => {
                    // Filter out entities already configured (except current plug when editing)
                    const configuredEntityIds = existingPlugs
                      ?.filter(p => p.ha_entity_id && p.id !== plug?.id)
                      .map(p => p.ha_entity_id) || [];
                    const availableEntities = (haEntities || []).filter(e => !configuredEntityIds.includes(e.entity_id));
                    const selectedEntity = haEntities?.find(e => e.entity_id === haEntityId);

                    return (
                      <div ref={entityDropdownRef} className="relative">
                        <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.selectEntity')} *</label>
                        <div className="relative">
                          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                          <input
                            type="text"
                            value={isEntityDropdownOpen ? haEntitySearch : (selectedEntity ? `${selectedEntity.friendly_name} (${selectedEntity.entity_id})` : '')}
                            onChange={(e) => {
                              setHaEntitySearch(e.target.value);
                              if (!isEntityDropdownOpen) setIsEntityDropdownOpen(true);
                            }}
                            onFocus={() => {
                              setIsEntityDropdownOpen(true);
                              setHaEntitySearch('');
                            }}
                            placeholder={t('addSmartPlug.searchEntities')}
                            className="w-full pl-9 pr-8 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
                          />
                          {haEntityId && !isEntityDropdownOpen && (
                            <button
                              type="button"
                              onClick={() => {
                                setHaEntityId('');
                                setHaEntitySearch('');
                              }}
                              className="absolute right-2 top-1/2 -translate-y-1/2 p-1 hover:bg-bambu-dark-tertiary rounded"
                            >
                              <X className="w-4 h-4 text-bambu-gray hover:text-white" />
                            </button>
                          )}
                          {haEntitiesLoading && (
                            <Loader2 className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray animate-spin" />
                          )}
                        </div>

                        {/* Dropdown */}
                        {isEntityDropdownOpen && (
                          <div className="absolute z-50 w-full mt-1 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-60 overflow-y-auto">
                            {haEntitiesLoading && (
                              <div className="px-3 py-2 text-sm text-bambu-gray flex items-center gap-2">
                                <Loader2 className="w-4 h-4 animate-spin" />
                                {t('common.loading')}
                              </div>
                            )}
                            {!haEntitiesLoading && availableEntities.length === 0 && (
                              <div className="px-3 py-2 text-sm text-bambu-gray">
                                {debouncedSearch
                                  ? t('addSmartPlug.noEntitiesMatching', { search: debouncedSearch })
                                  : t('addSmartPlug.noEntitiesAvailable')}
                              </div>
                            )}
                            {!haEntitiesLoading && availableEntities.map((entity) => (
                              <button
                                key={entity.entity_id}
                                type="button"
                                onClick={() => {
                                  setHaEntityId(entity.entity_id);
                                  setIsEntityDropdownOpen(false);
                                  setHaEntitySearch('');
                                  // Auto-fill name
                                  if (!name) {
                                    setName(entity.friendly_name);
                                  }
                                }}
                                className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary transition-colors ${
                                  entity.entity_id === haEntityId ? 'bg-bambu-green/20 text-bambu-green' : 'text-white'
                                }`}
                              >
                                <div className="font-medium">{entity.friendly_name}</div>
                                <div className="text-xs text-bambu-gray flex items-center justify-between">
                                  <span>{entity.entity_id}</span>
                                  <span className={entity.state === 'on' ? 'text-bambu-green' : ''}>{entity.state}</span>
                                </div>
                              </button>
                            ))}
                          </div>
                        )}

                        <p className="text-xs text-bambu-gray mt-1">
                          {debouncedSearch
                            ? t('addSmartPlug.searchingEntities', { count: availableEntities.length })
                            : t('addSmartPlug.showingEntities', { count: availableEntities.length })}
                        </p>
                      </div>
                    );
                  })()}


                  {/* Energy Monitoring Section (Optional) */}
                  {haEntityId && haSensorEntities && haSensorEntities.length > 0 && (
                    <div className="border-t border-bambu-dark-tertiary pt-4 mt-4 space-y-3">
                      <div>
                        <p className="text-white font-medium mb-1">{t('addSmartPlug.energyMonitoring')}</p>
                        <p className="text-xs text-bambu-gray mb-3">
                          {t('addSmartPlug.energyMonitoringDescription')}
                        </p>
                      </div>

                      {/* Power Sensor (W) */}
                      {(() => {
                        const powerSensors = haSensorEntities.filter(s =>
                          s.unit_of_measurement === 'W' || s.unit_of_measurement === 'kW' || s.unit_of_measurement === 'mW'
                        );
                        const filteredPowerSensors = powerSensorSearch
                          ? powerSensors.filter(s =>
                              s.entity_id.toLowerCase().includes(powerSensorSearch.toLowerCase()) ||
                              s.friendly_name.toLowerCase().includes(powerSensorSearch.toLowerCase())
                            )
                          : powerSensors;
                        const selectedPowerSensor = haSensorEntities.find(s => s.entity_id === haPowerEntity);

                        return (
                          <div ref={powerDropdownRef} className="relative">
                            <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.powerSensor')}</label>
                            <div className="relative">
                              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                              <input
                                type="text"
                                value={isPowerDropdownOpen ? powerSensorSearch : (selectedPowerSensor ? `${selectedPowerSensor.friendly_name} (${selectedPowerSensor.state} ${selectedPowerSensor.unit_of_measurement})` : '')}
                                onChange={(e) => {
                                  setPowerSensorSearch(e.target.value);
                                  if (!isPowerDropdownOpen) setIsPowerDropdownOpen(true);
                                }}
                                onFocus={() => {
                                  setIsPowerDropdownOpen(true);
                                  setPowerSensorSearch('');
                                }}
                                placeholder={t('addSmartPlug.searchPowerSensors')}
                                className="w-full pl-9 pr-8 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
                              />
                              {haPowerEntity && !isPowerDropdownOpen && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setHaPowerEntity('');
                                    setPowerSensorSearch('');
                                  }}
                                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 hover:bg-bambu-dark-tertiary rounded"
                                >
                                  <X className="w-4 h-4 text-bambu-gray hover:text-white" />
                                </button>
                              )}
                            </div>
                            {isPowerDropdownOpen && (
                              <div className="absolute z-50 w-full mt-1 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-48 overflow-y-auto">
                                <button
                                  type="button"
                                  onClick={() => {
                                    setHaPowerEntity('');
                                    setIsPowerDropdownOpen(false);
                                    setPowerSensorSearch('');
                                  }}
                                  className="w-full px-3 py-2 text-left text-sm text-bambu-gray hover:bg-bambu-dark-tertiary"
                                >
                                  {t('common.none')}
                                </button>
                                {filteredPowerSensors.map((sensor) => (
                                  <button
                                    key={sensor.entity_id}
                                    type="button"
                                    onClick={() => {
                                      setHaPowerEntity(sensor.entity_id);
                                      setIsPowerDropdownOpen(false);
                                      setPowerSensorSearch('');
                                    }}
                                    className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
                                      sensor.entity_id === haPowerEntity ? 'bg-bambu-green/20 text-bambu-green' : 'text-white'
                                    }`}
                                  >
                                    <div className="font-medium">{sensor.friendly_name}</div>
                                    <div className="text-xs text-bambu-gray">{sensor.entity_id} • {sensor.state} {sensor.unit_of_measurement}</div>
                                  </button>
                                ))}
                                {filteredPowerSensors.length === 0 && (
                                  <div className="px-3 py-2 text-sm text-bambu-gray">{t('addSmartPlug.noMatchingSensors')}</div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })()}

                      {/* Energy Today (kWh) */}
                      {(() => {
                        const energySensors = haSensorEntities.filter(s =>
                          s.unit_of_measurement === 'kWh' || s.unit_of_measurement === 'Wh' || s.unit_of_measurement === 'MWh'
                        );
                        const filteredEnergySensors = energyTodaySearch
                          ? energySensors.filter(s =>
                              s.entity_id.toLowerCase().includes(energyTodaySearch.toLowerCase()) ||
                              s.friendly_name.toLowerCase().includes(energyTodaySearch.toLowerCase())
                            )
                          : energySensors;
                        const selectedSensor = haSensorEntities.find(s => s.entity_id === haEnergyTodayEntity);

                        return (
                          <div ref={energyTodayDropdownRef} className="relative">
                            <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.energyToday')}</label>
                            <div className="relative">
                              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                              <input
                                type="text"
                                value={isEnergyTodayDropdownOpen ? energyTodaySearch : (selectedSensor ? `${selectedSensor.friendly_name} (${selectedSensor.state} ${selectedSensor.unit_of_measurement})` : '')}
                                onChange={(e) => {
                                  setEnergyTodaySearch(e.target.value);
                                  if (!isEnergyTodayDropdownOpen) setIsEnergyTodayDropdownOpen(true);
                                }}
                                onFocus={() => {
                                  setIsEnergyTodayDropdownOpen(true);
                                  setEnergyTodaySearch('');
                                }}
                                placeholder={t('addSmartPlug.searchEnergySensors')}
                                className="w-full pl-9 pr-8 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
                              />
                              {haEnergyTodayEntity && !isEnergyTodayDropdownOpen && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setHaEnergyTodayEntity('');
                                    setEnergyTodaySearch('');
                                  }}
                                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 hover:bg-bambu-dark-tertiary rounded"
                                >
                                  <X className="w-4 h-4 text-bambu-gray hover:text-white" />
                                </button>
                              )}
                            </div>
                            {isEnergyTodayDropdownOpen && (
                              <div className="absolute z-50 w-full mt-1 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-48 overflow-y-auto">
                                <button
                                  type="button"
                                  onClick={() => {
                                    setHaEnergyTodayEntity('');
                                    setIsEnergyTodayDropdownOpen(false);
                                    setEnergyTodaySearch('');
                                  }}
                                  className="w-full px-3 py-2 text-left text-sm text-bambu-gray hover:bg-bambu-dark-tertiary"
                                >
                                  {t('common.none')}
                                </button>
                                {filteredEnergySensors.map((sensor) => (
                                  <button
                                    key={sensor.entity_id}
                                    type="button"
                                    onClick={() => {
                                      setHaEnergyTodayEntity(sensor.entity_id);
                                      setIsEnergyTodayDropdownOpen(false);
                                      setEnergyTodaySearch('');
                                    }}
                                    className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
                                      sensor.entity_id === haEnergyTodayEntity ? 'bg-bambu-green/20 text-bambu-green' : 'text-white'
                                    }`}
                                  >
                                    <div className="font-medium">{sensor.friendly_name}</div>
                                    <div className="text-xs text-bambu-gray">{sensor.entity_id} • {sensor.state} {sensor.unit_of_measurement}</div>
                                  </button>
                                ))}
                                {filteredEnergySensors.length === 0 && (
                                  <div className="px-3 py-2 text-sm text-bambu-gray">{t('addSmartPlug.noMatchingSensors')}</div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })()}

                      {/* Total Energy (kWh) */}
                      {(() => {
                        const energySensors = haSensorEntities.filter(s =>
                          s.unit_of_measurement === 'kWh' || s.unit_of_measurement === 'Wh' || s.unit_of_measurement === 'MWh'
                        );
                        const filteredEnergySensors = energyTotalSearch
                          ? energySensors.filter(s =>
                              s.entity_id.toLowerCase().includes(energyTotalSearch.toLowerCase()) ||
                              s.friendly_name.toLowerCase().includes(energyTotalSearch.toLowerCase())
                            )
                          : energySensors;
                        const selectedSensor = haSensorEntities.find(s => s.entity_id === haEnergyTotalEntity);

                        return (
                          <div ref={energyTotalDropdownRef} className="relative">
                            <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.totalEnergy')}</label>
                            <div className="relative">
                              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                              <input
                                type="text"
                                value={isEnergyTotalDropdownOpen ? energyTotalSearch : (selectedSensor ? `${selectedSensor.friendly_name} (${selectedSensor.state} ${selectedSensor.unit_of_measurement})` : '')}
                                onChange={(e) => {
                                  setEnergyTotalSearch(e.target.value);
                                  if (!isEnergyTotalDropdownOpen) setIsEnergyTotalDropdownOpen(true);
                                }}
                                onFocus={() => {
                                  setIsEnergyTotalDropdownOpen(true);
                                  setEnergyTotalSearch('');
                                }}
                                placeholder={t('addSmartPlug.searchEnergySensors')}
                                className="w-full pl-9 pr-8 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
                              />
                              {haEnergyTotalEntity && !isEnergyTotalDropdownOpen && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setHaEnergyTotalEntity('');
                                    setEnergyTotalSearch('');
                                  }}
                                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 hover:bg-bambu-dark-tertiary rounded"
                                >
                                  <X className="w-4 h-4 text-bambu-gray hover:text-white" />
                                </button>
                              )}
                            </div>
                            {isEnergyTotalDropdownOpen && (
                              <div className="absolute z-50 w-full mt-1 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg shadow-lg max-h-48 overflow-y-auto">
                                <button
                                  type="button"
                                  onClick={() => {
                                    setHaEnergyTotalEntity('');
                                    setIsEnergyTotalDropdownOpen(false);
                                    setEnergyTotalSearch('');
                                  }}
                                  className="w-full px-3 py-2 text-left text-sm text-bambu-gray hover:bg-bambu-dark-tertiary"
                                >
                                  {t('common.none')}
                                </button>
                                {filteredEnergySensors.map((sensor) => (
                                  <button
                                    key={sensor.entity_id}
                                    type="button"
                                    onClick={() => {
                                      setHaEnergyTotalEntity(sensor.entity_id);
                                      setIsEnergyTotalDropdownOpen(false);
                                      setEnergyTotalSearch('');
                                    }}
                                    className={`w-full px-3 py-2 text-left text-sm hover:bg-bambu-dark-tertiary ${
                                      sensor.entity_id === haEnergyTotalEntity ? 'bg-bambu-green/20 text-bambu-green' : 'text-white'
                                    }`}
                                  >
                                    <div className="font-medium">{sensor.friendly_name}</div>
                                    <div className="text-xs text-bambu-gray">{sensor.entity_id} • {sensor.state} {sensor.unit_of_measurement}</div>
                                  </button>
                                ))}
                                {filteredEnergySensors.length === 0 && (
                                  <div className="px-3 py-2 text-sm text-bambu-gray">{t('addSmartPlug.noMatchingSensors')}</div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* IP Address - only show for Tasmota */}
          {plugType === 'tasmota' && (
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.ipAddress')} *</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={ipAddress}
                  onChange={(e) => {
                    setIpAddress(e.target.value);
                    setTestResult(null);
                  }}
                  placeholder={t('addSmartPlug.ipAddressPlaceholder')}
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
                  {t('addSmartPlug.test')}
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
                    <p className="font-medium">{t('addSmartPlug.connected')}</p>
                    <p className="text-sm opacity-80">
                      {testResult.device_name && `${t('addSmartPlug.device')}: ${testResult.device_name} - `}
                      {t('addSmartPlug.state')}: {testResult.state}
                    </p>
                  </div>
                </>
              ) : (
                <>
                  <WifiOff className="w-5 h-5" />
                  <span>{t('addSmartPlug.connectionFailed')}</span>
                </>
              )}
            </div>
          )}

          {/* Name */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.name')} *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('addSmartPlug.namePlaceholder')}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            />
          </div>

          {/* Authentication (optional) - only show for Tasmota */}
          {plugType === 'tasmota' && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.username')}</label>
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder={t('addSmartPlug.usernamePlaceholder')}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.password')}</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={t('addSmartPlug.passwordPlaceholder')}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                </div>
              </div>
              <p className="text-xs text-bambu-gray -mt-2">
                {t('addSmartPlug.authHint')}
              </p>
            </>
          )}

          {/* Link to Printer */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.linkToPrinter')}</label>
            <select
              value={printerId ?? ''}
              onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="">{t('addSmartPlug.noPrinterOption')}</option>
              {availablePrinters?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <p className="text-xs text-bambu-gray mt-1">
              {t('addSmartPlug.linkToPrinterHint')}
            </p>
          </div>

          {/* Power Alerts */}
          <div className="border-t border-bambu-dark-tertiary pt-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Bell className="w-4 h-4 text-bambu-green" />
                <span className="text-white font-medium">{t('addSmartPlug.powerAlerts')}</span>
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
                    <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.alertAbove')}</label>
                    <input
                      type="number"
                      value={powerAlertHigh}
                      onChange={(e) => setPowerAlertHigh(e.target.value)}
                      placeholder={t('addSmartPlug.alertAbovePlaceholder')}
                      min="0"
                      max="5000"
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.alertBelow')}</label>
                    <input
                      type="number"
                      value={powerAlertLow}
                      onChange={(e) => setPowerAlertLow(e.target.value)}
                      placeholder={t('addSmartPlug.alertBelowPlaceholder')}
                      min="0"
                      max="5000"
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                </div>
                <p className="text-xs text-bambu-gray">
                  {t('addSmartPlug.powerAlertHint')}
                </p>
              </div>
            )}
          </div>

          {/* Schedule */}
          <div className="border-t border-bambu-dark-tertiary pt-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Clock className="w-4 h-4 text-bambu-green" />
                <span className="text-white font-medium">{t('addSmartPlug.dailySchedule')}</span>
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
                    <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.turnOnAt')}</label>
                    <input
                      type="time"
                      value={scheduleOnTime}
                      onChange={(e) => setScheduleOnTime(e.target.value)}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">{t('addSmartPlug.turnOffAt')}</label>
                    <input
                      type="time"
                      value={scheduleOffTime}
                      onChange={(e) => setScheduleOffTime(e.target.value)}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                </div>
                <p className="text-xs text-bambu-gray">
                  {t('addSmartPlug.scheduleHint')}
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
                  <span className="text-white font-medium">{t('addSmartPlug.showInSwitchbar')}</span>
                  <p className="text-xs text-bambu-gray">{t('addSmartPlug.showInSwitchbarHint')}</p>
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
              {t('common.cancel')}
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
              {isEditing ? t('common.save') : t('common.add')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
