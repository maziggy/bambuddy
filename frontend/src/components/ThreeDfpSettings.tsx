import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertCircle, ExternalLink, Link2, Loader2, LogOut } from 'lucide-react';

import { api, ApiError } from '../api/client';
import type { ThreeDfpDeviceStartResponse, ThreeDfpDevicePollStatus } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';

/**
 * 3D Filament Profiles connection (#2981), one per user.
 *
 * The RFC 8628 device flow: Connect asks the backend for a code, the user
 * approves it on 3dfilamentprofiles.com, and the card polls the backend (which
 * polls 3DFP) until the token arrives. The code is shown even though the link
 * carries it: 3DFP's approval page asks the user to check that they match.
 */
export function ThreeDfpSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const canManage = hasPermission('threedfp:auth');

  const [pairing, setPairing] = useState<ThreeDfpDeviceStartResponse | null>(null);
  const [pairingStartedAt, setPairingStartedAt] = useState(0);
  const [pollIntervalMs, setPollIntervalMs] = useState(5000);
  const [now, setNow] = useState(() => Date.now());
  const [connectError, setConnectError] = useState<string | null>(null);

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['threedfpStatus'],
    queryFn: api.threedfpStatus,
    enabled: canManage,
  });
  const connected = !!status?.connected;

  // Asked live when the card opens: /me costs no quota.
  const { data: account, error: accountError } = useQuery({
    queryKey: ['threedfpAccount'],
    queryFn: api.threedfpAccount,
    enabled: canManage && connected,
    retry: false,
  });

  // 3DFP no longer accepts the token: the backend has forgotten it.
  useEffect(() => {
    if (accountError instanceof ApiError && accountError.status === 409) {
      queryClient.invalidateQueries({ queryKey: ['threedfpStatus'] });
    }
  }, [accountError, queryClient]);

  const finishPairing = () => {
    setPairing(null);
    setPollIntervalMs(5000);
  };

  const startMutation = useMutation({
    mutationFn: api.threedfpDeviceStart,
    onSuccess: (data) => {
      setConnectError(null);
      setPollIntervalMs(Math.max(1, data.interval) * 1000);
      setPairingStartedAt(Date.now());
      setNow(Date.now());
      setPairing(data);
    },
    onError: () => setConnectError(t('settings.threedfp.errors.startFailed')),
  });

  const disconnectMutation = useMutation({
    mutationFn: api.threedfpDisconnect,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['threedfpStatus'] });
      queryClient.removeQueries({ queryKey: ['threedfpAccount'] });
      showToast(t('settings.threedfp.disconnected'));
    },
    onError: () => showToast(t('settings.threedfp.errors.disconnectFailed'), 'error'),
  });

  const { data: pollData, error: pollError } = useQuery({
    queryKey: ['threedfpDevicePoll', pairing?.user_code ?? 'none'],
    queryFn: api.threedfpDevicePoll,
    enabled: pairing !== null,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: pairing !== null ? pollIntervalMs : false,
  });

  const lastHandled = useRef<ThreeDfpDevicePollStatus | null>(null);
  useEffect(() => {
    if (pairing) lastHandled.current = null;
  }, [pairing]);

  useEffect(() => {
    if (!pairing || !pollData) return;
    const s = pollData.status;
    if (s === 'slow_down') {
      setPollIntervalMs((ms) => ms + 5000);
      return;
    }
    if (s === 'authorization_pending' || lastHandled.current === s) return;
    lastHandled.current = s;
    if (s === 'complete') {
      finishPairing();
      queryClient.invalidateQueries({ queryKey: ['threedfpStatus'] });
      queryClient.invalidateQueries({ queryKey: ['threedfpAccount'] });
      showToast(t('settings.threedfp.connectedToast'));
    } else {
      setConnectError(t(s === 'access_denied' ? 'settings.threedfp.errors.denied' : 'settings.threedfp.errors.expired'));
      finishPairing();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollData, pairing]);

  useEffect(() => {
    if (pairing && pollError) {
      setConnectError(t('settings.threedfp.errors.pollFailed'));
      finishPairing();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollError, pairing]);

  // Countdown to the code's expiry; the code is dead after it.
  const remainingS = pairing ? Math.max(0, Math.round(pairing.expires_in - (now - pairingStartedAt) / 1000)) : 0;
  useEffect(() => {
    if (!pairing) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [pairing]);
  useEffect(() => {
    if (pairing && remainingS === 0) {
      setConnectError(t('settings.threedfp.errors.expired'));
      finishPairing();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairing, remainingS]);

  if (!canManage) return null;

  const canSync = account?.active_scopes.includes('spools:read') ?? false;
  const countdown = `${Math.floor(remainingS / 60)}:${String(remainingS % 60).padStart(2, '0')}`;

  return (
    <Card id="card-threedfp">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Link2 className="w-5 h-5 text-bambu-green" />
          <h2 className="text-lg font-semibold text-white">{t('settings.threedfp.title')}</h2>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-bambu-gray">{t('settings.threedfp.description')}</p>

        {statusLoading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="w-5 h-5 text-bambu-green animate-spin" />
          </div>
        ) : connected ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm">
              <div className="w-2 h-2 rounded-full bg-bambu-green" />
              <span className="text-white break-all">
                {account?.email || status?.email
                  ? t('settings.threedfp.connectedAs', { email: account?.email || status?.email })
                  : t('settings.threedfp.connected')}
              </span>
            </div>
            {account?.plan && (
              <p className="text-xs text-bambu-gray">
                {t('settings.threedfp.plan', {
                  plan: account.plan === 'api' ? t('settings.threedfp.planApi') : t('settings.threedfp.planFree'),
                })}
              </p>
            )}
            {account?.quota?.limit != null && account.quota.remaining != null && (
              <p className="text-xs text-bambu-gray">
                {t('settings.threedfp.quota', { remaining: account.quota.remaining, limit: account.quota.limit })}
              </p>
            )}
            {account && !canSync && (
              <p className="text-xs text-bambu-gray">
                {t('settings.threedfp.syncNeedsApi')}{' '}
                {account.upgrade_url && (
                  <a
                    href={account.upgrade_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-bambu-green hover:underline inline-flex items-center gap-1"
                  >
                    {t('settings.threedfp.upgrade')}
                    <ExternalLink className="w-3 h-3" />
                  </a>
                )}
              </p>
            )}
            {accountError && !(accountError instanceof ApiError && accountError.status === 409) && (
              <p className="text-xs text-red-700 dark:text-red-400">{t('settings.threedfp.errors.accountFailed')}</p>
            )}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => disconnectMutation.mutate()}
              disabled={disconnectMutation.isPending}
            >
              <LogOut className="w-4 h-4" />
              {t('settings.threedfp.disconnect')}
            </Button>
          </div>
        ) : pairing ? (
          <div className="space-y-3 text-center">
            <p className="text-sm text-bambu-gray">{t('settings.threedfp.device.instruction')}</p>
            <div className="py-2 px-3 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg">
              <span className="text-xl font-mono font-bold tracking-[0.3em] text-white select-all">
                {pairing.user_code}
              </span>
            </div>
            <a href={pairing.verification_uri_complete} target="_blank" rel="noopener noreferrer" className="block">
              <Button className="w-full">
                <ExternalLink className="w-4 h-4" />
                {t('settings.threedfp.device.open')}
              </Button>
            </a>
            <div className="flex items-center justify-center gap-2 text-sm text-bambu-gray">
              <Loader2 className="w-4 h-4 animate-spin text-bambu-green" />
              {t('settings.threedfp.device.waiting', { time: countdown })}
            </div>
            <button type="button" onClick={finishPairing} className="text-bambu-gray hover:text-white text-sm">
              {t('common.cancel')}
            </button>
          </div>
        ) : (
          <div>
            <Button onClick={() => startMutation.mutate()} disabled={startMutation.isPending}>
              {startMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Link2 className="w-4 h-4" />}
              {t('settings.threedfp.connect')}
            </Button>
            {connectError && (
              <p className="mt-2 text-sm text-red-700 dark:text-red-400 flex items-center gap-2">
                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                {connectError}
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
