import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Check, X, RefreshCw, Link2, Link2Off, Database, ChevronDown, Info, AlertTriangle } from 'lucide-react';
import { api } from '../api/client';
import type { SpoolmanSyncResult, Printer } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';

interface SpoolmanSettingsData {
  spoolman_enabled: string;
  spoolman_url: string;
  spoolman_sync_mode: string;
}

async function getSpoolmanSettings(): Promise<SpoolmanSettingsData> {
  const response = await fetch('/api/v1/settings/spoolman');
  if (!response.ok) {
    throw new Error('Failed to load Spoolman settings');
  }
  return response.json();
}

async function updateSpoolmanSettings(data: Partial<SpoolmanSettingsData>): Promise<SpoolmanSettingsData> {
  const response = await fetch('/api/v1/settings/spoolman', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error('Failed to save Spoolman settings');
  }
  return response.json();
}

export function SpoolmanSettings() {
  const queryClient = useQueryClient();
  const [localEnabled, setLocalEnabled] = useState(false);
  const [localUrl, setLocalUrl] = useState('');
  const [localSyncMode, setLocalSyncMode] = useState('auto');
  const [showSaved, setShowSaved] = useState(false);
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | 'all'>('all');
  const [isInitialized, setIsInitialized] = useState(false);
  const [showAllSkipped, setShowAllSkipped] = useState(false);

  // Fetch Spoolman settings
  const { data: settings, isLoading: settingsLoading } = useQuery({
    queryKey: ['spoolman-settings'],
    queryFn: getSpoolmanSettings,
  });

  // Fetch Spoolman status
  const { data: status, isLoading: statusLoading, refetch: refetchStatus } = useQuery({
    queryKey: ['spoolman-status'],
    queryFn: api.getSpoolmanStatus,
    refetchInterval: 30000, // Refresh every 30 seconds
  });

  // Fetch printers for the dropdown
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Initialize local state from settings
  useEffect(() => {
    if (settings) {
      setLocalEnabled(settings.spoolman_enabled === 'true');
      setLocalUrl(settings.spoolman_url || '');
      setLocalSyncMode(settings.spoolman_sync_mode || 'auto');
      setIsInitialized(true);
    }
  }, [settings]);

  // Auto-save when settings change (after initial load)
  // Intentionally omit saveMutation and settings from deps to avoid infinite loops
  useEffect(() => {
    if (!isInitialized || !settings) return;

    const hasChanges =
      (settings.spoolman_enabled === 'true') !== localEnabled ||
      (settings.spoolman_url || '') !== localUrl ||
      (settings.spoolman_sync_mode || 'auto') !== localSyncMode;

    if (hasChanges) {
      const timeoutId = setTimeout(() => {
        saveMutation.mutate();
      }, 500);
      return () => clearTimeout(timeoutId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localEnabled, localUrl, localSyncMode, isInitialized]);

  // Save mutation
  const saveMutation = useMutation({
    mutationFn: () =>
      updateSpoolmanSettings({
        spoolman_enabled: localEnabled ? 'true' : 'false',
        spoolman_url: localUrl,
        spoolman_sync_mode: localSyncMode,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['spoolman-settings'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-status'] });
      setShowSaved(true);
      setTimeout(() => setShowSaved(false), 2000);
    },
  });

  // Connect mutation
  const connectMutation = useMutation({
    mutationFn: api.connectSpoolman,
    onSuccess: () => {
      refetchStatus();
    },
  });

  // Disconnect mutation
  const disconnectMutation = useMutation({
    mutationFn: api.disconnectSpoolman,
    onSuccess: () => {
      refetchStatus();
    },
  });

  // Sync all mutation
  const syncAllMutation = useMutation({
    mutationFn: api.syncAllPrintersAms,
    onSuccess: (data: SpoolmanSyncResult) => {
      if (data.success) {
        // Show success message
      }
    },
  });

  // Sync single printer mutation
  const syncPrinterMutation = useMutation({
    mutationFn: (printerId: number) => api.syncPrinterAms(printerId),
    onSuccess: (data: SpoolmanSyncResult) => {
      if (data.success) {
        // Show success message
      }
    },
  });

  // Helper to handle sync based on selection
  const handleSync = () => {
    if (selectedPrinterId === 'all') {
      syncAllMutation.mutate();
    } else {
      syncPrinterMutation.mutate(selectedPrinterId);
    }
  };

  // Combine mutation states
  const isSyncing = syncAllMutation.isPending || syncPrinterMutation.isPending;
  const syncResult = selectedPrinterId === 'all' ? syncAllMutation.data : syncPrinterMutation.data;
  const syncSuccess = selectedPrinterId === 'all' ? syncAllMutation.isSuccess : syncPrinterMutation.isSuccess;

  if (settingsLoading) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Database className="w-5 h-5 text-bambu-green" />
            <h2 className="text-lg font-semibold text-white">Spoolman Integration</h2>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex justify-center py-8">
            <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Database className="w-5 h-5 text-bambu-green" />
            <h2 className="text-lg font-semibold text-white">Spoolman Integration</h2>
          </div>
          {saveMutation.isPending && (
            <Loader2 className="w-4 h-4 text-bambu-green animate-spin" />
          )}
          {showSaved && (
            <Check className="w-4 h-4 text-bambu-green" />
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-bambu-gray">
          Connect to Spoolman for filament inventory tracking. AMS data will sync automatically.
        </p>

        {/* Info banner about sync requirements */}
        <div className="p-3 bg-blue-500/10 border border-blue-500/30 rounded-lg">
          <div className="flex gap-2">
            <Info className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" />
            <div className="text-xs text-blue-300">
              <p className="font-medium mb-1">How Sync Works</p>
              <ul className="list-disc list-inside space-y-0.5 text-blue-300/80">
                <li>Only official Bambu Lab spools with RFID are synced</li>
                <li>New spools are auto-created in Spoolman on first sync</li>
                <li>Non-Bambu Lab spools (third-party, refilled) are skipped</li>
              </ul>
              <p className="font-medium mt-2 mb-1">Linking Existing Spools</p>
              <p className="text-blue-300/80">
                To link existing Spoolman spools to your AMS, hover over an AMS slot and click "Link to Spoolman".
              </p>
            </div>
          </div>
        </div>

        {/* Enable toggle */}
        <div className="flex items-center justify-between">
          <div>
            <p className="text-white">Enable Spoolman</p>
            <p className="text-sm text-bambu-gray">
              Sync filament data with Spoolman server
            </p>
          </div>
          <label className="relative inline-flex items-center cursor-pointer">
            <input
              type="checkbox"
              checked={localEnabled}
              onChange={(e) => setLocalEnabled(e.target.checked)}
              className="sr-only peer"
            />
            <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
          </label>
        </div>

        {/* URL input */}
        <div>
          <label className="block text-sm text-bambu-gray mb-1">
            Spoolman URL
          </label>
          <input
            type="text"
            placeholder="http://192.168.1.100:7912"
            value={localUrl}
            onChange={(e) => setLocalUrl(e.target.value)}
            disabled={!localEnabled}
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray/50 focus:border-bambu-green focus:outline-none disabled:opacity-50"
          />
          <p className="text-xs text-bambu-gray mt-1">
            URL of your Spoolman server (e.g., http://localhost:7912)
          </p>
        </div>

        {/* Sync mode */}
        <div>
          <label className="block text-sm text-bambu-gray mb-1">
            Sync Mode
          </label>
          <select
            value={localSyncMode}
            onChange={(e) => setLocalSyncMode(e.target.value)}
            disabled={!localEnabled}
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none disabled:opacity-50"
          >
            <option value="auto">Automatic</option>
            <option value="manual">Manual Only</option>
          </select>
          <p className="text-xs text-bambu-gray mt-1">
            {localSyncMode === 'auto'
              ? 'AMS data syncs automatically when changes are detected'
              : 'Only sync when manually triggered'}
          </p>
        </div>

        {/* Connection status */}
        {localEnabled && (
          <div className="pt-2 border-t border-bambu-dark-tertiary">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="text-sm text-bambu-gray">Status:</span>
                {statusLoading ? (
                  <Loader2 className="w-4 h-4 text-bambu-gray animate-spin" />
                ) : status?.connected ? (
                  <span className="flex items-center gap-1 text-sm text-green-500">
                    <Check className="w-4 h-4" />
                    Connected
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-sm text-red-500">
                    <X className="w-4 h-4" />
                    Disconnected
                  </span>
                )}
              </div>
              <div className="flex gap-2">
                {status?.connected ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => disconnectMutation.mutate()}
                    disabled={disconnectMutation.isPending}
                  >
                    {disconnectMutation.isPending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Link2Off className="w-4 h-4" />
                    )}
                    Disconnect
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    onClick={() => connectMutation.mutate()}
                    disabled={connectMutation.isPending || !localUrl}
                  >
                    {connectMutation.isPending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Link2 className="w-4 h-4" />
                    )}
                    Connect
                  </Button>
                )}
              </div>
            </div>

            {/* Error display */}
            {connectMutation.isError && (
              <div className="mb-3 p-2 bg-red-500/20 border border-red-500/50 rounded text-sm text-red-400">
                {(connectMutation.error as Error).message}
              </div>
            )}

            {/* Manual sync section */}
            {status?.connected && (
              <div className="space-y-3">
                <div>
                  <p className="text-sm text-white">Sync AMS Data</p>
                  <p className="text-xs text-bambu-gray">
                    Manually sync printer AMS data to Spoolman
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {/* Printer selector */}
                  <div className="relative flex-1">
                    <select
                      value={selectedPrinterId}
                      onChange={(e) => setSelectedPrinterId(e.target.value === 'all' ? 'all' : Number(e.target.value))}
                      className="w-full px-3 py-2 pr-8 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
                    >
                      <option value="all">All Printers</option>
                      {printers?.map((printer: Printer) => (
                        <option key={printer.id} value={printer.id}>
                          {printer.name}
                        </option>
                      ))}
                    </select>
                    <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                  </div>
                  {/* Sync button */}
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={handleSync}
                    disabled={isSyncing}
                  >
                    {isSyncing ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <RefreshCw className="w-4 h-4" />
                    )}
                    Sync
                  </Button>
                </div>
              </div>
            )}

            {/* Sync result */}
            {syncSuccess && syncResult && (
              <div className="mt-3 space-y-2">
                {/* Main result */}
                <div
                  className={`p-2 rounded text-sm ${
                    syncResult.success
                      ? 'bg-green-500/20 border border-green-500/50 text-green-400'
                      : 'bg-yellow-500/20 border border-yellow-500/50 text-yellow-400'
                  }`}
                >
                  {syncResult.success
                    ? `Synced ${syncResult.synced_count} spool${syncResult.synced_count !== 1 ? 's' : ''} successfully`
                    : `Synced ${syncResult.synced_count} spool${syncResult.synced_count !== 1 ? 's' : ''} with ${syncResult.errors.length} error${syncResult.errors.length !== 1 ? 's' : ''}`}
                </div>

                {/* Skipped spools */}
                {syncResult.skipped_count > 0 && (
                  <div className="p-2 bg-amber-500/10 border border-amber-500/30 rounded text-sm">
                    <div className="flex items-center justify-between text-amber-400 mb-1">
                      <div className="flex items-center gap-1.5">
                        <AlertTriangle className="w-3.5 h-3.5" />
                        <span className="font-medium">
                          {syncResult.skipped_count} spool{syncResult.skipped_count !== 1 ? 's' : ''} skipped
                        </span>
                      </div>
                      {syncResult.skipped_count > 5 && (
                        <button
                          onClick={() => setShowAllSkipped(!showAllSkipped)}
                          className="text-xs text-amber-400 hover:text-amber-300 underline"
                        >
                          {showAllSkipped ? 'Show less' : 'Show all'}
                        </button>
                      )}
                    </div>
                    <ul className="text-xs text-amber-300/80 space-y-0.5">
                      {(showAllSkipped ? syncResult.skipped : syncResult.skipped.slice(0, 5)).map((s, i) => (
                        <li key={i} className="flex items-center gap-2">
                          {s.color && (
                            <span
                              className="w-3 h-3 rounded-full border border-white/20"
                              style={{ backgroundColor: `#${s.color}` }}
                            />
                          )}
                          <span>{s.location}</span>
                          <span className="text-amber-300/60">- {s.reason}</span>
                        </li>
                      ))}
                      {!showAllSkipped && syncResult.skipped_count > 5 && (
                        <li className="text-amber-300/60 italic">
                          ...and {syncResult.skipped_count - 5} more
                        </li>
                      )}
                    </ul>
                  </div>
                )}

                {/* Errors */}
                {syncResult.errors.length > 0 && (
                  <div className="p-2 bg-red-500/10 border border-red-500/30 rounded text-sm">
                    <div className="text-red-400 font-medium mb-1">Errors:</div>
                    <ul className="text-xs text-red-300/80 space-y-0.5">
                      {syncResult.errors.map((err, i) => (
                        <li key={i}>{err}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
