import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Save, RotateCcw, Loader2, Check } from 'lucide-react';
import { api } from '../api/client';
import type { AppSettings } from '../api/client';
import { Card, CardContent, CardHeader } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { useState, useEffect } from 'react';

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [localSettings, setLocalSettings] = useState<AppSettings | null>(null);
  const [hasChanges, setHasChanges] = useState(false);
  const [showSaved, setShowSaved] = useState(false);
  const [showResetConfirm, setShowResetConfirm] = useState(false);

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  // Sync local state when settings load
  useEffect(() => {
    if (settings && !localSettings) {
      setLocalSettings(settings);
    }
  }, [settings, localSettings]);

  // Track changes
  useEffect(() => {
    if (settings && localSettings) {
      const changed =
        settings.auto_archive !== localSettings.auto_archive ||
        settings.save_thumbnails !== localSettings.save_thumbnails ||
        settings.default_filament_cost !== localSettings.default_filament_cost ||
        settings.currency !== localSettings.currency;
      setHasChanges(changed);
    }
  }, [settings, localSettings]);

  const updateMutation = useMutation({
    mutationFn: api.updateSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(['settings'], data);
      setLocalSettings(data);
      setHasChanges(false);
      setShowSaved(true);
      setTimeout(() => setShowSaved(false), 2000);
    },
  });

  const resetMutation = useMutation({
    mutationFn: api.resetSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(['settings'], data);
      setLocalSettings(data);
      setHasChanges(false);
    },
  });

  const handleSave = () => {
    if (localSettings) {
      updateMutation.mutate(localSettings);
    }
  };

  const handleReset = () => {
    setShowResetConfirm(true);
  };

  const updateSetting = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    if (localSettings) {
      setLocalSettings({ ...localSettings, [key]: value });
    }
  };

  if (isLoading || !localSettings) {
    return (
      <div className="p-8 flex justify-center">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  return (
    <div className="p-8">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Settings</h1>
          <p className="text-bambu-gray">Configure Bambusy</p>
        </div>
        <div className="flex gap-3">
          <Button
            variant="secondary"
            onClick={handleReset}
            disabled={resetMutation.isPending}
          >
            <RotateCcw className="w-4 h-4" />
            Reset
          </Button>
          <Button
            onClick={handleSave}
            disabled={!hasChanges || updateMutation.isPending}
          >
            {updateMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : showSaved ? (
              <Check className="w-4 h-4" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {showSaved ? 'Saved!' : 'Save'}
          </Button>
        </div>
      </div>

      {updateMutation.isError && (
        <div className="mb-6 p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-400">
          Failed to save settings: {(updateMutation.error as Error).message}
        </div>
      )}

      <div className="space-y-6 max-w-2xl">
        <Card>
          <CardHeader>
            <h2 className="text-lg font-semibold text-white">Archive Settings</h2>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-white">Auto-archive prints</p>
                <p className="text-sm text-bambu-gray">
                  Automatically save 3MF files when prints complete
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={localSettings.auto_archive}
                  onChange={(e) => updateSetting('auto_archive', e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
              </label>
            </div>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-white">Save thumbnails</p>
                <p className="text-sm text-bambu-gray">
                  Extract and save preview images from 3MF files
                </p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={localSettings.save_thumbnails}
                  onChange={(e) => updateSetting('save_thumbnails', e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
              </label>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <h2 className="text-lg font-semibold text-white">Cost Tracking</h2>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="block text-sm text-bambu-gray mb-1">
                Default filament cost (per kg)
              </label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={localSettings.default_filament_cost}
                onChange={(e) =>
                  updateSetting('default_filament_cost', parseFloat(e.target.value) || 0)
                }
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Currency</label>
              <select
                value={localSettings.currency}
                onChange={(e) => updateSetting('currency', e.target.value)}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              >
                <option value="USD">USD ($)</option>
                <option value="EUR">EUR (€)</option>
                <option value="GBP">GBP (£)</option>
                <option value="CHF">CHF (Fr.)</option>
                <option value="JPY">JPY (¥)</option>
                <option value="CNY">CNY (¥)</option>
                <option value="CAD">CAD ($)</option>
                <option value="AUD">AUD ($)</option>
              </select>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <h2 className="text-lg font-semibold text-white">About</h2>
          </CardHeader>
          <CardContent>
            <div className="space-y-2 text-sm">
              <p className="text-white">Bambusy v0.1.0</p>
              <p className="text-bambu-gray">
                Archive and manage your Bambu Lab 3MF files
              </p>
              <p className="text-bambu-gray">
                Connect to printers via LAN mode (developer mode required)
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Reset Confirmation Modal */}
      {showResetConfirm && (
        <ConfirmModal
          title="Reset Settings"
          message="Reset all settings to defaults? This cannot be undone."
          confirmText="Reset"
          variant="danger"
          onConfirm={() => {
            resetMutation.mutate();
            setShowResetConfirm(false);
          }}
          onCancel={() => setShowResetConfirm(false)}
        />
      )}
    </div>
  );
}
