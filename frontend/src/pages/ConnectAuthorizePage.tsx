/**
 * "Sign in with Bambuddy" for connected apps.
 *
 * An app sends the browser here with its client_id, callback URL, PKCE
 * challenge and state. The page runs with the user's normal session: it asks
 * the backend whether the request is valid, shows a one-time consent screen,
 * and sends the browser back to the app with a single-use code.
 *
 * It never redirects anywhere until the backend has confirmed that the
 * callback URL is the one registered for that app, so it can't be used as an
 * open redirect. Every error before that point is shown here instead.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';
import { AlertTriangle, Link2, Loader2 } from 'lucide-react';
import { api, ApiError, type ConnectAuthorizeInfo } from '../api/client';
import { useAuth } from '../contexts/AuthContext';

type Phase =
  | { kind: 'loading' }
  | { kind: 'consent'; info: ConnectAuthorizeInfo }
  | { kind: 'redirecting' }
  | { kind: 'error'; message: string };

function withParams(base: string, params: Record<string, string>): string {
  const url = new URL(base);
  for (const [key, value] of Object.entries(params)) {
    if (value) url.searchParams.set(key, value);
  }
  return url.toString();
}

export function ConnectAuthorizePage() {
  const { t } = useTranslation();
  const { authEnabled, loading } = useAuth();
  const [searchParams] = useSearchParams();
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
  const started = useRef(false);

  const clientId = searchParams.get('client_id') ?? '';
  const redirectUri = searchParams.get('redirect_uri') ?? '';
  const state = searchParams.get('state') ?? '';
  const codeChallenge = searchParams.get('code_challenge') ?? '';
  const codeChallengeMethod = searchParams.get('code_challenge_method') ?? '';

  const approve = async () => {
    setPhase({ kind: 'redirecting' });
    try {
      const result = await api.connectAuthorize({
        client_id: clientId,
        redirect_uri: redirectUri,
        code_challenge: codeChallenge,
        code_challenge_method: 'S256',
      });
      // Use the callback the backend returned, not the query parameter.
      window.location.replace(withParams(result.redirect_uri, { code: result.code, state }));
    } catch (err) {
      setPhase({
        kind: 'error',
        message: err instanceof Error ? err.message : t('connectedApps.authorize.failed'),
      });
    }
  };

  const deny = () => {
    // Safe: we only reach consent after the backend matched redirectUri
    // against the app's registration.
    window.location.replace(withParams(redirectUri, { error: 'access_denied', state }));
  };

  useEffect(() => {
    if (loading || started.current) return;
    started.current = true;

    if (!authEnabled) {
      setPhase({ kind: 'error', message: t('connectedApps.authorize.authDisabled') });
      return;
    }
    if (!clientId || !redirectUri || !codeChallenge || codeChallengeMethod !== 'S256') {
      setPhase({ kind: 'error', message: t('connectedApps.authorize.invalidRequest') });
      return;
    }

    api
      .getConnectAuthorizeInfo(clientId, redirectUri)
      .then((info) => {
        if (info.already_granted) {
          void approve();
        } else {
          setPhase({ kind: 'consent', info });
        }
      })
      .catch((err) => {
        const disabled = err instanceof ApiError && err.status === 409;
        setPhase({
          kind: 'error',
          message: disabled ? t('connectedApps.authorize.authDisabled') : t('connectedApps.authorize.invalidRequest'),
        });
      });
    // approve() reads the same query parameters; running this once is the point.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, authEnabled]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-bambu-dark p-4">
      <div className="max-w-md w-full space-y-6 p-8 bg-gradient-to-br from-bambu-card to-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary shadow-lg">
        {(phase.kind === 'loading' || phase.kind === 'redirecting') && (
          <div className="flex flex-col items-center gap-3 text-bambu-gray" role="status">
            <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
            <p>
              {phase.kind === 'loading'
                ? t('connectedApps.authorize.checking')
                : t('connectedApps.authorize.redirecting')}
            </p>
          </div>
        )}

        {phase.kind === 'error' && (
          <div className="text-center space-y-3">
            <AlertTriangle className="w-10 h-10 mx-auto text-yellow-600 dark:text-yellow-400" />
            <h1 className="text-xl font-semibold text-white">{t('connectedApps.authorize.errorTitle')}</h1>
            <p className="text-sm text-bambu-gray">{phase.message}</p>
          </div>
        )}

        {phase.kind === 'consent' && (
          <>
            <div className="text-center space-y-3">
              <div className="w-14 h-14 mx-auto rounded-full bg-bambu-green/20 flex items-center justify-center">
                <Link2 className="w-7 h-7 text-bambu-green" />
              </div>
              <h1 className="text-xl font-semibold text-white text-balance">
                {t('connectedApps.authorize.title', { app: phase.info.app_name })}
              </h1>
              <p className="text-sm text-bambu-gray">
                {t('connectedApps.authorize.signedInAs', { username: phase.info.username })}
              </p>
            </div>
            <p className="text-sm text-bambu-gray">
              {t('connectedApps.authorize.sharedData', { app: phase.info.app_name })}
            </p>
            <div className="flex gap-3">
              <button
                type="button"
                onClick={deny}
                className="flex-1 px-4 py-2 bg-bambu-dark-tertiary text-white rounded-md hover:bg-bambu-dark-tertiary/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-bambu-green"
              >
                {t('connectedApps.authorize.deny')}
              </button>
              <button
                type="button"
                onClick={() => void approve()}
                className="flex-1 px-4 py-2 bg-bambu-green text-white rounded-md hover:bg-bambu-green/90 focus:outline-none focus-visible:ring-2 focus-visible:ring-white"
              >
                {t('connectedApps.authorize.allow')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
