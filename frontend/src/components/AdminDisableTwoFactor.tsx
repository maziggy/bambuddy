import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { ShieldOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';

interface AdminDisableTwoFactorProps {
  userId: number;
  username: string;
}

/**
 * Edit user: remove a user's 2FA (TOTP, email codes and backup codes), for a
 * user locked out of their authenticator. Admin-only, like the route. Not shown
 * on your own account, which has its own 2FA settings. The route asks for the
 * admin's own password unless the admin has none (LDAP and OIDC accounts,
 * unless they were given one).
 */
export function AdminDisableTwoFactor({ userId, username }: AdminDisableTwoFactorProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { authEnabled, isAdmin, user } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState('');
  // Set when the route asks for a password the sign-in type did not suggest:
  // an LDAP or SSO account that was given a local password in Edit user.
  const [passwordAsked, setPasswordAsked] = useState(false);

  const needsPassword = passwordAsked || user?.auth_source === 'local';

  const disableMutation = useMutation({
    mutationFn: () => api.admin2FADisable(userId, needsPassword ? password : null),
    onSuccess: (response) => {
      setConfirming(false);
      setPassword('');
      if (response.had_2fa) {
        showToast(t('users.form.twoFaRemoved', { username }), 'success');
      } else {
        showToast(t('users.form.twoFaNoneToRemove', { username }), 'info');
      }
    },
    onError: (error: Error) => {
      if (!needsPassword && error instanceof ApiError && error.status === 401) {
        setPasswordAsked(true);
        showToast(t('users.form.twoFaPasswordNeeded'), 'info');
        return;
      }
      showToast(error.message, 'error');
    },
  });

  if (!authEnabled || !isAdmin || user?.id === userId) return null;

  return (
    <div className="bg-bambu-dark-secondary/50 border border-bambu-dark-tertiary rounded-lg p-3 space-y-3">
      <div>
        <p className="text-sm font-medium text-white">{t('users.form.twoFaTitle')}</p>
        <p className="text-sm text-bambu-gray">{t('users.form.twoFaHint')}</p>
      </div>
      {confirming ? (
        <div className="space-y-3">
          {needsPassword && (
            <div>
              <label htmlFor="admin-disable-2fa-password" className="block text-sm font-medium text-white mb-1">
                {t('users.form.twoFaYourPassword')}
              </label>
              <input
                id="admin-disable-2fa-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="w-full px-4 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green"
              />
            </div>
          )}
          <div className="flex gap-3">
            <Button
              variant="secondary"
              size="sm"
              className="flex-1"
              onClick={() => {
                setConfirming(false);
                setPassword('');
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              size="sm"
              className="flex-1"
              disabled={disableMutation.isPending || (needsPassword && !password)}
              onClick={() => disableMutation.mutate()}
            >
              {t('common.confirm')}
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="secondary" size="sm" className="w-full" onClick={() => setConfirming(true)}>
          <ShieldOff className="w-4 h-4" />
          {t('users.form.twoFaDisable')}
        </Button>
      )}
    </div>
  );
}
