import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Cloud, ExternalLink, LogOut, Loader2, AlertCircle, AlertTriangle, Check, Mail, ArrowLeft } from 'lucide-react';

import { api } from '../api/client';
import type { OrcaOAuthProvider } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { OrcaCloudProfilesView } from './OrcaCloudProfilesView';

/**
 * Orca Cloud profile sync tab.
 *
 * Auth uses a paste-based PKCE handshake: backend generates the verifier and
 * authorize URL, the user opens it in a new tab and signs in, the browser
 * redirects to ``http://localhost:41172/callback`` (which fails to load since
 * Bambuddy isn't on the user's localhost), and the user copies the URL from
 * their address bar back into the paste textarea below. The backend extracts
 * the code, validates state for CSRF, and exchanges for tokens.
 *
 * See OrcaSlicer/OrcaSlicer#14028 for the open feature request asking
 * SoftFever to broaden the Supabase redirect_to allowlist so we could ship
 * a clean OAuth callback instead.
 */
export function OrcaCloudView() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const canManage = hasPermission('orca_cloud:auth');

  // Paste-flow local state: once the user clicks an OAuth provider, we hold
  // the returned auth_url so the same URL stays clickable while they go
  // fetch the callback URL from their browser. ``mode`` drives which
  // sub-form is showing: picker → OAuth paste-flow → email/password form.
  const [mode, setMode] = useState<'picker' | 'paste' | 'password'>('picker');
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [pastedUrl, setPastedUrl] = useState('');
  const [pasteError, setPasteError] = useState<string | null>(null);
  const [passwordEmail, setPasswordEmail] = useState('');
  const [passwordValue, setPasswordValue] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);

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

  const startAuthMutation = useMutation({
    mutationFn: (provider: OrcaOAuthProvider) => api.orcaCloudStartAuth(provider),
    onSuccess: (data) => {
      setAuthUrl(data.auth_url);
      setPastedUrl('');
      setPasteError(null);
      setMode('paste');
      // Open in a new tab so the user can keep Bambuddy open in their
      // current tab while they sign in.
      window.open(data.auth_url, '_blank', 'noopener,noreferrer');
    },
    onError: (err: Error) => {
      showToast(err.message || t('profiles.orcaCloud.errors.startFailed'), 'error');
    },
  });

  const finishAuthMutation = useMutation({
    mutationFn: (url: string) => api.orcaCloudFinishAuth(url),
    onSuccess: (data) => {
      setAuthUrl(null);
      setPastedUrl('');
      setPasteError(null);
      setMode('picker');
      queryClient.invalidateQueries({ queryKey: ['orcaCloudStatus'] });
      queryClient.invalidateQueries({ queryKey: ['orcaCloudProfiles'] });
      showToast(t('profiles.orcaCloud.toast.connected', { email: data.email || '' }));
    },
    onError: (err: Error) => {
      // Surface the backend's error message in the paste-error slot so the
      // user can fix the input (rather than a transient toast they might miss).
      setPasteError(err.message || t('profiles.orcaCloud.errors.finishFailed'));
    },
  });

  const passwordLoginMutation = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      api.orcaCloudPasswordLogin(email, password),
    onSuccess: (data) => {
      setPasswordEmail('');
      setPasswordValue('');
      setPasswordError(null);
      setMode('picker');
      queryClient.invalidateQueries({ queryKey: ['orcaCloudStatus'] });
      queryClient.invalidateQueries({ queryKey: ['orcaCloudProfiles'] });
      showToast(t('profiles.orcaCloud.toast.connected', { email: data.email || '' }));
    },
    onError: (err: Error) => {
      setPasswordError(err.message || t('profiles.orcaCloud.errors.passwordFailed'));
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

  const handleSubmitPaste = (e: React.FormEvent) => {
    e.preventDefault();
    setPasteError(null);
    const trimmed = pastedUrl.trim();
    if (!trimmed) {
      setPasteError(t('profiles.orcaCloud.errors.emptyPaste'));
      return;
    }
    if (!trimmed.includes('code=')) {
      setPasteError(t('profiles.orcaCloud.errors.noCode'));
      return;
    }
    finishAuthMutation.mutate(trimmed);
  };

  const handleSubmitPassword = (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);
    const email = passwordEmail.trim();
    if (!email || !passwordValue) {
      setPasswordError(t('profiles.orcaCloud.errors.passwordEmpty'));
      return;
    }
    passwordLoginMutation.mutate({ email, password: passwordValue });
  };

  const resetToPicker = () => {
    setMode('picker');
    setAuthUrl(null);
    setPastedUrl('');
    setPasteError(null);
    setPasswordEmail('');
    setPasswordValue('');
    setPasswordError(null);
  };

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
              {t('profiles.orcaCloud.connectedAs')}{' '}
              <span className="text-white">{status?.email}</span>
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
        <ConnectFlow
          mode={mode}
          authUrl={authUrl}
          pastedUrl={pastedUrl}
          setPastedUrl={setPastedUrl}
          pasteError={pasteError}
          passwordEmail={passwordEmail}
          setPasswordEmail={setPasswordEmail}
          passwordValue={passwordValue}
          setPasswordValue={setPasswordValue}
          passwordError={passwordError}
          onPickProvider={(provider) => startAuthMutation.mutate(provider)}
          onPickPassword={() => {
            setMode('password');
            setPasswordError(null);
          }}
          onSubmitPaste={handleSubmitPaste}
          onSubmitPassword={handleSubmitPassword}
          onBack={resetToPicker}
          isStarting={startAuthMutation.isPending}
          isFinishing={finishAuthMutation.isPending}
          isPasswordLoading={passwordLoginMutation.isPending}
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

interface ConnectFlowProps {
  mode: 'picker' | 'paste' | 'password';
  authUrl: string | null;
  pastedUrl: string;
  setPastedUrl: (v: string) => void;
  pasteError: string | null;
  passwordEmail: string;
  setPasswordEmail: (v: string) => void;
  passwordValue: string;
  setPasswordValue: (v: string) => void;
  passwordError: string | null;
  onPickProvider: (provider: OrcaOAuthProvider) => void;
  onPickPassword: () => void;
  onSubmitPaste: (e: React.FormEvent) => void;
  onSubmitPassword: (e: React.FormEvent) => void;
  onBack: () => void;
  isStarting: boolean;
  isFinishing: boolean;
  isPasswordLoading: boolean;
  canManage: boolean;
  t: (key: string, opts?: Record<string, string>) => string;
}

function ConnectFlow(props: ConnectFlowProps) {
  if (props.mode === 'paste' && props.authUrl) {
    return <PasteCard {...props} authUrl={props.authUrl} />;
  }
  if (props.mode === 'password') {
    return <PasswordCard {...props} />;
  }
  return <PickerCard {...props} />;
}

function PickerCard({
  onPickProvider,
  onPickPassword,
  isStarting,
  canManage,
  t,
}: ConnectFlowProps) {
  // Orca's web sign-in offers four options: Google, Apple, GitHub (all
  // OAuth, paste-flow) and email+password (direct). We mirror that surface
  // so users with a non-Google account aren't blocked.
  return (
    <Card>
      <CardContent className="p-8 text-center">
        <Cloud className="w-12 h-12 text-bambu-green mx-auto mb-4" />
        <h2 className="text-xl font-bold text-white mb-2">
          {t('profiles.orcaCloud.connect.title')}
        </h2>
        <p className="text-bambu-gray mb-6 max-w-xl mx-auto">
          {t('profiles.orcaCloud.connect.description')}
        </p>
        <div className="flex flex-col gap-2 max-w-sm mx-auto">
          <Button
            onClick={onPickPassword}
            disabled={isStarting || !canManage}
            title={!canManage ? t('profiles.orcaCloud.noConnectPermission') : undefined}
          >
            <Mail className="w-4 h-4" />
            {t('profiles.orcaCloud.providers.email')}
          </Button>
          <Button
            variant="secondary"
            onClick={() => onPickProvider('google')}
            disabled={isStarting || !canManage}
            title={!canManage ? t('profiles.orcaCloud.noConnectPermission') : undefined}
          >
            {isStarting ? <Loader2 className="w-4 h-4 animate-spin" /> : <ExternalLink className="w-4 h-4" />}
            {t('profiles.orcaCloud.providers.google')}
          </Button>
          <Button
            variant="secondary"
            onClick={() => onPickProvider('github')}
            disabled={isStarting || !canManage}
            title={!canManage ? t('profiles.orcaCloud.noConnectPermission') : undefined}
          >
            <ExternalLink className="w-4 h-4" />
            {t('profiles.orcaCloud.providers.github')}
          </Button>
          <Button
            variant="secondary"
            onClick={() => onPickProvider('apple')}
            disabled={isStarting || !canManage}
            title={!canManage ? t('profiles.orcaCloud.noConnectPermission') : undefined}
          >
            <ExternalLink className="w-4 h-4" />
            {t('profiles.orcaCloud.providers.apple')}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function PasteCard({
  authUrl,
  pastedUrl,
  setPastedUrl,
  pasteError,
  onSubmitPaste,
  onBack,
  isFinishing,
  t,
}: ConnectFlowProps & { authUrl: string }) {
  return (
    <Card>
      <CardContent className="p-6">
        <button
          type="button"
          onClick={onBack}
          className="text-bambu-gray hover:text-white text-sm flex items-center gap-1 mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          {t('profiles.orcaCloud.back')}
        </button>
        <h2 className="text-xl font-bold text-white mb-4">
          {t('profiles.orcaCloud.paste.title')}
        </h2>

        {/* Numbered-step list with prominent visual treatment. Step 2 carries
            the critical "the page failing is expected" message inside an
            amber callout so users don't read the connection-refused page
            as a Bambuddy error. */}
        <ol className="space-y-3 mb-6">
          <li className="flex gap-3">
            <span className="flex-shrink-0 w-7 h-7 rounded-full bg-bambu-dark-tertiary text-white text-sm font-bold flex items-center justify-center">1</span>
            <p className="text-base text-white pt-0.5">{t('profiles.orcaCloud.paste.step1')}</p>
          </li>
          <li className="flex gap-3">
            <span className="flex-shrink-0 w-7 h-7 rounded-full bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400 text-sm font-bold flex items-center justify-center">2</span>
            <div className="flex-1 p-3 bg-amber-500/10 border border-amber-500/40 rounded">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
                <p className="text-base text-white font-medium">{t('profiles.orcaCloud.paste.step2')}</p>
              </div>
            </div>
          </li>
          <li className="flex gap-3">
            <span className="flex-shrink-0 w-7 h-7 rounded-full bg-bambu-dark-tertiary text-white text-sm font-bold flex items-center justify-center">3</span>
            <p className="text-base text-white pt-0.5">{t('profiles.orcaCloud.paste.step3')}</p>
          </li>
        </ol>

        <div className="mb-4 p-3 bg-bambu-dark rounded border border-bambu-dark-tertiary">
          <p className="text-xs text-bambu-gray mb-1">{t('profiles.orcaCloud.paste.signInUrl')}</p>
          <a
            href={authUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-bambu-green text-sm break-all hover:underline"
          >
            {authUrl}
          </a>
        </div>
        <form onSubmit={onSubmitPaste}>
          <label htmlFor="orca-callback-url" className="block text-sm text-bambu-gray mb-2">
            {t('profiles.orcaCloud.paste.label')}
          </label>
          <textarea
            id="orca-callback-url"
            value={pastedUrl}
            onChange={(e) => setPastedUrl(e.target.value)}
            placeholder={t('profiles.orcaCloud.paste.placeholder')}
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm font-mono resize-none focus:outline-none focus:border-bambu-green"
            rows={3}
            disabled={isFinishing}
          />
          {pasteError && (
            <p className="mt-2 text-sm text-red-700 dark:text-red-400 flex items-center gap-2">
              <AlertCircle className="w-4 h-4" />
              {pasteError}
            </p>
          )}
          <div className="mt-4 flex items-center gap-3">
            <Button type="submit" disabled={isFinishing || !pastedUrl.trim()}>
              {isFinishing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              {t('profiles.orcaCloud.paste.submit')}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function PasswordCard({
  passwordEmail,
  setPasswordEmail,
  passwordValue,
  setPasswordValue,
  passwordError,
  onSubmitPassword,
  onBack,
  isPasswordLoading,
  t,
}: ConnectFlowProps) {
  return (
    <Card>
      <CardContent className="p-6 max-w-md mx-auto">
        <button
          type="button"
          onClick={onBack}
          className="text-bambu-gray hover:text-white text-sm flex items-center gap-1 mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          {t('profiles.orcaCloud.back')}
        </button>
        <h2 className="text-xl font-bold text-white mb-4">
          {t('profiles.orcaCloud.password.title')}
        </h2>
        <form onSubmit={onSubmitPassword} className="space-y-4">
          <div>
            <label htmlFor="orca-password-email" className="block text-sm text-bambu-gray mb-1">
              {t('profiles.orcaCloud.password.email')}
            </label>
            <input
              id="orca-password-email"
              type="email"
              value={passwordEmail}
              onChange={(e) => setPasswordEmail(e.target.value)}
              placeholder={t('profiles.orcaCloud.password.emailPlaceholder')}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:outline-none focus:border-bambu-green"
              disabled={isPasswordLoading}
              autoComplete="email"
            />
          </div>
          <div>
            <label htmlFor="orca-password-value" className="block text-sm text-bambu-gray mb-1">
              {t('profiles.orcaCloud.password.password')}
            </label>
            <input
              id="orca-password-value"
              type="password"
              value={passwordValue}
              onChange={(e) => setPasswordValue(e.target.value)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:outline-none focus:border-bambu-green"
              disabled={isPasswordLoading}
              autoComplete="current-password"
            />
          </div>
          {passwordError && (
            <p className="text-sm text-red-700 dark:text-red-400 flex items-center gap-2">
              <AlertCircle className="w-4 h-4" />
              {passwordError}
            </p>
          )}
          <Button type="submit" disabled={isPasswordLoading || !passwordEmail.trim() || !passwordValue}>
            {isPasswordLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
            {t('profiles.orcaCloud.password.submit')}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
