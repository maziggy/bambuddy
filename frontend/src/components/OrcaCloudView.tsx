import { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Cloud, ExternalLink, LogOut, Loader2, AlertCircle, Check } from 'lucide-react';

import { api } from '../api/client';
import type { OrcaDeviceStartResponse, OrcaDevicePollStatus } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { OrcaCloudProfilesView } from './OrcaCloudProfilesView';

/**
 * Orca Cloud profile sync tab.
 *
 * Auth uses the RFC 8628 device-authorization grant: the backend requests a
 * device code from Orca and returns a short user_code plus a verification link.
 * The user opens the link, approves the code in their Orca Cloud settings, and
 * Bambuddy polls the backend (which polls Orca's token endpoint) until the
 * pairing completes. No redirect URL, no callback paste, no client secret —
 * see backend/app/services/orca_cloud.py for the deep dive.
 */
export function OrcaCloudView() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const canManage = hasPermission('orca_cloud:auth');

  // Pairing sub-state: null until the user clicks Connect, then the device
  // response (code + link) that we display while polling.
  const [pairing, setPairing] = useState<OrcaDeviceStartResponse | null>(null);
  const [pollIntervalMs, setPollIntervalMs] = useState(5000);
  const [connectError, setConnectError] = useState<string | null>(null);

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['orcaCloudStatus'],
    queryFn: api.orcaCloudStatus,
  });

  const connected = !!status?.connected;

  const {
    data: profilesData,
    isLoading: profilesLoading,
    refetch: refetchProfiles,
    isRefetching: profilesRefetching,
    error: profilesError,
    dataUpdatedAt: profilesUpdatedAt,
  } = useQuery({
    queryKey: ['orcaCloudProfiles'],
    queryFn: api.orcaCloudListProfiles,
    enabled: connected,
    retry: false,
    staleTime: 1000 * 60 * 5,
  });

  // Configured Bambuddy printers — fed into the profile-view's printer
  // filter dropdown so the user can narrow profiles to a specific printer
  // model. Same usage as the Bambu Cloud tab.
  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
    enabled: connected,
  });

  const [lastSyncTime, setLastSyncTime] = useState<Date | undefined>();
  useEffect(() => {
    if (profilesUpdatedAt) setLastSyncTime(new Date(profilesUpdatedAt));
  }, [profilesUpdatedAt]);

  const finishPairing = () => {
    setPairing(null);
    setPollIntervalMs(5000);
  };

  const handleTerminal = (status: OrcaDevicePollStatus) => {
    if (status === 'access_denied') setConnectError(t('profiles.orcaCloud.errors.denied'));
    else if (status === 'expired_token') setConnectError(t('profiles.orcaCloud.errors.expired'));
    finishPairing();
  };

  const startMutation = useMutation({
    mutationFn: api.orcaCloudDeviceStart,
    onSuccess: (data) => {
      setConnectError(null);
      setPollIntervalMs(Math.max(1, data.interval) * 1000);
      setPairing(data);
    },
    onError: (err: Error) => {
      setConnectError(err.message || t('profiles.orcaCloud.errors.startFailed'));
    },
  });

  const logoutMutation = useMutation({
    mutationFn: api.orcaCloudLogout,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['orcaCloudStatus'] });
      queryClient.removeQueries({ queryKey: ['orcaCloudProfiles'] });
      showToast(t('profiles.orcaCloud.toast.disconnected'));
    },
  });

  // Poll the backend while a pairing is in flight. react-query drives the
  // cadence; the effect below reacts to each poll result. refetchInterval
  // returns false once we stop (pairing cleared), which halts polling.
  const { data: pollData, error: pollError } = useQuery({
    // Scope the cache per pairing attempt so a fresh Connect never re-consumes
    // a previous attempt's cached 'complete'/terminal result.
    queryKey: ['orcaCloudDevicePoll', pairing?.user_code ?? 'none'],
    queryFn: api.orcaCloudDevicePoll,
    enabled: pairing !== null,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: pairing !== null ? pollIntervalMs : false,
  });

  // A ref so the poll-result effect can act exactly once per new result
  // without re-running when unrelated state (interval, etc.) changes.
  const lastHandledStatus = useRef<OrcaDevicePollStatus | null>(null);
  useEffect(() => {
    if (!pairing || !pollData) return;
    const s = pollData.status;
    if (s === 'slow_down') {
      // Back off as the RFC prescribes, then keep waiting.
      setPollIntervalMs((ms) => ms + 5000);
      return;
    }
    if (s === 'authorization_pending') return;
    if (lastHandledStatus.current === s) return;
    lastHandledStatus.current = s;
    if (s === 'complete') {
      finishPairing();
      queryClient.invalidateQueries({ queryKey: ['orcaCloudStatus'] });
      queryClient.invalidateQueries({ queryKey: ['orcaCloudProfiles'] });
      showToast(t('profiles.orcaCloud.connectedShort'));
    } else {
      handleTerminal(s);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollData, pairing]);

  // A poll HTTP error (e.g. the pending state vanished server-side) ends the
  // flow rather than spinning forever.
  useEffect(() => {
    if (pairing && pollError) {
      setConnectError(t('profiles.orcaCloud.errors.pollFailed'));
      finishPairing();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollError, pairing]);

  // Reset the one-shot guard whenever a new pairing starts.
  useEffect(() => {
    if (pairing) lastHandledStatus.current = null;
  }, [pairing]);

  if (statusLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  return (
    <div>
      {connected && (
        <div className="flex items-center justify-between p-3 mb-6 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 rounded-full bg-bambu-green animate-pulse" />
            <span className="text-sm text-bambu-gray">
              {status?.email ? (
                <>
                  {t('profiles.orcaCloud.connectedAs')} <span className="text-white">{status.email}</span>
                </>
              ) : (
                <span className="text-white">{t('profiles.orcaCloud.connectedShort')}</span>
              )}
            </span>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => logoutMutation.mutate()}
            disabled={logoutMutation.isPending || !canManage}
            title={!canManage ? t('profiles.orcaCloud.noLogoutPermission') : undefined}
          >
            <LogOut className="w-4 h-4" />
            {t('profiles.orcaCloud.logout')}
          </Button>
        </div>
      )}

      {!connected ? (
        <ConnectCard
          pairing={pairing}
          connectError={connectError}
          onConnect={() => startMutation.mutate()}
          onCancel={finishPairing}
          isStarting={startMutation.isPending}
          canManage={canManage}
          t={t}
        />
      ) : profilesLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
        </div>
      ) : profilesError ? (
        <div className="text-center py-16">
          <p className="text-bambu-gray mb-4">{(profilesError as Error).message}</p>
          <Button onClick={() => refetchProfiles()}>{t('profiles.orcaCloud.retry')}</Button>
        </div>
      ) : profilesData ? (
        <OrcaCloudProfilesView
          settings={profilesData}
          lastSyncTime={lastSyncTime}
          onRefresh={() => refetchProfiles()}
          isRefreshing={profilesRefetching}
          printers={printers}
          t={t}
        />
      ) : null}
    </div>
  );
}

interface ConnectCardProps {
  pairing: OrcaDeviceStartResponse | null;
  connectError: string | null;
  onConnect: () => void;
  onCancel: () => void;
  isStarting: boolean;
  canManage: boolean;
  t: (key: string, opts?: Record<string, string>) => string;
}

function ConnectCard({ pairing, connectError, onConnect, onCancel, isStarting, canManage, t }: ConnectCardProps) {
  // While pairing is in flight, show the code + approval link + waiting spinner.
  if (pairing) {
    return (
      <Card>
        <CardContent className="p-8 text-center max-w-md mx-auto">
          <Cloud className="w-12 h-12 text-bambu-green mx-auto mb-4" />
          <h2 className="text-xl font-bold text-white mb-2">{t('profiles.orcaCloud.device.title')}</h2>
          <p className="text-bambu-gray mb-6">{t('profiles.orcaCloud.device.instruction')}</p>

          <p className="text-xs uppercase tracking-wide text-bambu-gray mb-2">
            {t('profiles.orcaCloud.device.codeLabel')}
          </p>
          <div className="mb-6 py-3 px-4 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg">
            <span className="text-2xl font-mono font-bold tracking-[0.3em] text-white select-all">
              {pairing.user_code}
            </span>
          </div>

          <a href={pairing.verification_uri_complete} target="_blank" rel="noopener noreferrer">
            <Button className="w-full mb-3">
              <ExternalLink className="w-4 h-4" />
              {t('profiles.orcaCloud.device.openButton')}
            </Button>
          </a>
          <p className="text-xs text-bambu-gray break-all mb-6">
            {t('profiles.orcaCloud.device.manualHint', { url: pairing.verification_uri })}
          </p>

          <div className="flex items-center justify-center gap-2 text-sm text-bambu-gray mb-4">
            <Loader2 className="w-4 h-4 animate-spin text-bambu-green" />
            {t('profiles.orcaCloud.device.waiting')}
          </div>
          <button type="button" onClick={onCancel} className="text-bambu-gray hover:text-white text-sm">
            {t('profiles.orcaCloud.device.cancel')}
          </button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-8 text-center">
        <Cloud className="w-12 h-12 text-bambu-green mx-auto mb-4" />
        <h2 className="text-xl font-bold text-white mb-2">{t('profiles.orcaCloud.connect.title')}</h2>
        <p className="text-bambu-gray mb-6 max-w-xl mx-auto">{t('profiles.orcaCloud.connect.description')}</p>
        <div className="max-w-sm mx-auto">
          <Button
            onClick={onConnect}
            disabled={isStarting || !canManage}
            title={!canManage ? t('profiles.orcaCloud.noConnectPermission') : undefined}
            className="w-full"
          >
            {isStarting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
            {t('profiles.orcaCloud.connectButton')}
          </Button>
          {connectError && (
            <p className="mt-3 text-sm text-red-700 dark:text-red-400 flex items-center justify-center gap-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              {connectError}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
