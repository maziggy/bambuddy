import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Cloud,
  LogIn,
  LogOut,
  Loader2,
  ChevronDown,
  ChevronRight,
  Settings2,
  Printer,
  Droplet,
  X,
  Key,
  RefreshCw,
} from 'lucide-react';
import { api } from '../api/client';
import type { SlicerSetting, SlicerSettingsResponse } from '../api/client';
import { Card, CardContent, CardHeader } from '../components/Card';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';

type LoginStep = 'email' | 'code' | 'token';

function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const { showToast } = useToast();
  const [step, setStep] = useState<LoginStep>('email');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [token, setToken] = useState('');
  const [region, setRegion] = useState('global');

  const loginMutation = useMutation({
    mutationFn: () => api.cloudLogin(email, password, region),
    onSuccess: (result) => {
      if (result.success) {
        showToast('Logged in successfully');
        onSuccess();
      } else if (result.needs_verification) {
        showToast('Verification code sent to your email');
        setStep('code');
      } else {
        showToast(result.message, 'error');
      }
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const verifyMutation = useMutation({
    mutationFn: () => api.cloudVerify(email, code),
    onSuccess: (result) => {
      if (result.success) {
        showToast('Logged in successfully');
        onSuccess();
      } else {
        showToast(result.message, 'error');
      }
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const tokenMutation = useMutation({
    mutationFn: () => api.cloudSetToken(token),
    onSuccess: () => {
      showToast('Token set successfully');
      onSuccess();
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (step === 'email') {
      loginMutation.mutate();
    } else if (step === 'code') {
      verifyMutation.mutate();
    } else if (step === 'token') {
      tokenMutation.mutate();
    }
  };

  const isPending = loginMutation.isPending || verifyMutation.isPending || tokenMutation.isPending;

  return (
    <Card className="max-w-md mx-auto">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Cloud className="w-5 h-5 text-bambu-green" />
          <h2 className="text-xl font-semibold text-white">Connect to Bambu Cloud</h2>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          {step === 'email' && (
            <>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  placeholder="your@email.com"
                  required
                />
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  placeholder="••••••••"
                  required
                />
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Region</label>
                <select
                  value={region}
                  onChange={(e) => setRegion(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="global">Global</option>
                  <option value="china">China</option>
                </select>
              </div>
            </>
          )}

          {step === 'code' && (
            <div>
              <label className="block text-sm text-bambu-gray mb-1">
                Verification Code
              </label>
              <p className="text-xs text-bambu-gray mb-2">
                Check your email ({email}) for a 6-digit code
              </p>
              <input
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-center text-2xl tracking-widest focus:border-bambu-green focus:outline-none"
                placeholder="000000"
                maxLength={6}
                required
              />
            </div>
          )}

          {step === 'token' && (
            <div>
              <label className="block text-sm text-bambu-gray mb-1">
                Access Token
              </label>
              <p className="text-xs text-bambu-gray mb-2">
                Paste your Bambu Lab access token (from Bambu Studio)
              </p>
              <textarea
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-xs font-mono focus:border-bambu-green focus:outline-none resize-none"
                placeholder="eyJ..."
                rows={3}
                required
              />
            </div>
          )}

          <div className="flex gap-2">
            {step === 'code' && (
              <Button
                type="button"
                variant="secondary"
                onClick={() => setStep('email')}
                className="flex-1"
              >
                Back
              </Button>
            )}
            <Button type="submit" disabled={isPending} className="flex-1">
              {isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <LogIn className="w-4 h-4" />
              )}
              {step === 'email' ? 'Login' : step === 'code' ? 'Verify' : 'Set Token'}
            </Button>
          </div>

          {step === 'email' && (
            <div className="pt-4 border-t border-bambu-dark-tertiary">
              <button
                type="button"
                onClick={() => setStep('token')}
                className="text-sm text-bambu-gray hover:text-white flex items-center gap-1"
              >
                <Key className="w-3 h-3" />
                Use access token instead
              </button>
            </div>
          )}

          {step === 'token' && (
            <div className="pt-4 border-t border-bambu-dark-tertiary">
              <button
                type="button"
                onClick={() => setStep('email')}
                className="text-sm text-bambu-gray hover:text-white flex items-center gap-1"
              >
                <LogIn className="w-3 h-3" />
                Login with email instead
              </button>
            </div>
          )}
        </form>
      </CardContent>
    </Card>
  );
}

function SettingCard({
  setting,
  onClick,
}: {
  setting: SlicerSetting;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="w-full text-left p-3 bg-bambu-dark rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
    >
      <p className="text-white font-medium truncate">{setting.name}</p>
      {setting.updated_time && (
        <p className="text-xs text-bambu-gray mt-1">
          Updated: {new Date(setting.updated_time).toLocaleDateString()}
        </p>
      )}
    </button>
  );
}

function SettingDetailModal({
  setting,
  onClose,
}: {
  setting: SlicerSetting;
  onClose: () => void;
}) {
  const { data: detail, isLoading } = useQuery({
    queryKey: ['cloudSettingDetail', setting.setting_id],
    queryFn: () => api.getCloudSettingDetail(setting.setting_id),
  });

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] flex flex-col">
        <CardContent className="p-0 flex flex-col h-full">
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <div>
              <h2 className="text-xl font-semibold text-white">{setting.name}</h2>
              <p className="text-sm text-bambu-gray capitalize">{setting.type} preset</p>
            </div>
            <button
              onClick={onClose}
              className="text-bambu-gray hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-4">
            {isLoading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
              </div>
            ) : detail ? (
              <pre className="text-xs text-bambu-gray font-mono whitespace-pre-wrap overflow-x-auto bg-bambu-dark p-4 rounded-lg">
                {JSON.stringify(detail, null, 2)}
              </pre>
            ) : (
              <p className="text-bambu-gray text-center py-8">
                Failed to load preset details
              </p>
            )}
          </div>

          <div className="p-4 border-t border-bambu-dark-tertiary">
            <Button variant="secondary" onClick={onClose} className="w-full">
              Close
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function ProfilesView({ settings }: { settings: SlicerSettingsResponse }) {
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());
  const [selectedSetting, setSelectedSetting] = useState<SlicerSetting | null>(null);

  // Sort items alphabetically by name
  const sortByName = (items: SlicerSetting[]) =>
    [...items].sort((a, b) => a.name.localeCompare(b.name));

  const toggleSection = (section: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(section)) {
        next.delete(section);
      } else {
        next.add(section);
      }
      return next;
    });
  };

  const sections = [
    {
      key: 'filament',
      label: 'Filament Presets',
      icon: Droplet,
      items: sortByName(settings.filament),
    },
    {
      key: 'printer',
      label: 'Printer Presets',
      icon: Printer,
      items: sortByName(settings.printer),
    },
    {
      key: 'process',
      label: 'Process Presets',
      icon: Settings2,
      items: sortByName(settings.process),
    },
  ];

  return (
    <>
      <div className="space-y-4">
        {sections.map(({ key, label, icon: Icon, items }) => (
          <Card key={key}>
            <button
              onClick={() => toggleSection(key)}
              className="w-full flex items-center justify-between p-4"
            >
              <div className="flex items-center gap-3">
                <Icon className="w-5 h-5 text-bambu-green" />
                <span className="text-lg font-semibold text-white">{label}</span>
                <span className="text-sm text-bambu-gray">({items.length})</span>
              </div>
              {expandedSections.has(key) ? (
                <ChevronDown className="w-5 h-5 text-bambu-gray" />
              ) : (
                <ChevronRight className="w-5 h-5 text-bambu-gray" />
              )}
            </button>
            {expandedSections.has(key) && items.length > 0 && (
              <CardContent className="pt-0">
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {items.map((item) => (
                    <SettingCard
                      key={item.setting_id}
                      setting={item}
                      onClick={() => setSelectedSetting(item)}
                    />
                  ))}
                </div>
              </CardContent>
            )}
            {expandedSections.has(key) && items.length === 0 && (
              <CardContent className="pt-0">
                <p className="text-bambu-gray text-sm">No presets found</p>
              </CardContent>
            )}
          </Card>
        ))}
      </div>

      {selectedSetting && (
        <SettingDetailModal
          setting={selectedSetting}
          onClose={() => setSelectedSetting(null)}
        />
      )}
    </>
  );
}

export function CloudProfilesPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['cloudStatus'],
    queryFn: api.getCloudStatus,
  });

  const { data: settings, isLoading: settingsLoading, refetch: refetchSettings } = useQuery({
    queryKey: ['cloudSettings'],
    queryFn: () => api.getCloudSettings(),
    enabled: !!status?.is_authenticated,
    retry: false,
    staleTime: 1000 * 60 * 5, // 5 minutes
  });

  const logoutMutation = useMutation({
    mutationFn: api.cloudLogout,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cloudStatus'] });
      queryClient.removeQueries({ queryKey: ['cloudSettings'] });
      showToast('Logged out');
    },
  });

  const handleLoginSuccess = () => {
    queryClient.invalidateQueries({ queryKey: ['cloudStatus'] });
  };

  if (statusLoading) {
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
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Cloud className="w-6 h-6 text-bambu-green" />
            Cloud Profiles
          </h1>
          <p className="text-bambu-gray">
            {status?.is_authenticated
              ? `Connected as ${status.email}`
              : 'Manage your Bambu Cloud slicer presets'}
          </p>
        </div>
        {status?.is_authenticated && (
          <div className="flex gap-2">
            <Button
              variant="secondary"
              onClick={() => refetchSettings()}
              disabled={settingsLoading}
            >
              <RefreshCw className={`w-4 h-4 ${settingsLoading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
            <Button
              variant="secondary"
              onClick={() => logoutMutation.mutate()}
              disabled={logoutMutation.isPending}
            >
              <LogOut className="w-4 h-4" />
              Logout
            </Button>
          </div>
        )}
      </div>

      {!status?.is_authenticated ? (
        <LoginForm onSuccess={handleLoginSuccess} />
      ) : settingsLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
        </div>
      ) : settings ? (
        <ProfilesView settings={settings} />
      ) : (
        <Card>
          <CardContent className="py-8 text-center">
            <p className="text-bambu-gray">Failed to load profiles</p>
            <Button className="mt-4" onClick={() => refetchSettings()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
