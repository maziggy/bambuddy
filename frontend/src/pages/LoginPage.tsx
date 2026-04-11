import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useTheme } from '../contexts/ThemeContext';
import { X, Mail, Shield, Smartphone, Key } from 'lucide-react';
import { api, type LoginResponse } from '../api/client';
import { Card, CardHeader, CardContent } from '../components/Card';
import { Button } from '../components/Button';

type LoginStep = 'credentials' | '2fa' | 'reset-password';

export function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { t } = useTranslation();
  const { login, loginWithToken } = useAuth();
  const { showToast } = useToast();
  const { mode } = useTheme();

  // Credentials step state
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showForgotPassword, setShowForgotPassword] = useState(false);
  const [forgotEmail, setForgotEmail] = useState('');

  // 2FA step state
  const [step, setStep] = useState<LoginStep>('credentials');
  const [preAuthToken, setPreAuthToken] = useState('');
  const [twoFAMethods, setTwoFAMethods] = useState<string[]>([]);
  const [twoFAMethod, setTwoFAMethod] = useState<'totp' | 'email' | 'backup'>('totp');
  const [twoFACode, setTwoFACode] = useState('');
  const [emailOTPSent, setEmailOTPSent] = useState(false);

  // H-6: Password reset step state
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  // Check if advanced auth is enabled
  const { data: advancedAuthStatus } = useQuery({
    queryKey: ['advancedAuthStatus'],
    queryFn: () => api.getAdvancedAuthStatus(),
  });

  // Fetch enabled OIDC providers for login buttons
  const { data: oidcProviders } = useQuery({
    queryKey: ['oidcProviders'],
    queryFn: () => api.getOIDCProviders(),
  });

  // H-6: Detect ?reset_token=... in the URL and switch to the password-reset step.
  useEffect(() => {
    const token = searchParams.get('reset_token');
    if (token) {
      setResetToken(token);
      setStep('reset-password');
      // Remove the token from the URL immediately so it can't be bookmarked or logged.
      navigate('/login', { replace: true });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Handle OIDC callback: if #oidc_token=... is present in the fragment, exchange it.
  // H-4: Read from the URL fragment (#) — fragments are never sent to the server
  // so the exchange token stays out of access logs and Referer headers.
  useEffect(() => {
    const hash = window.location.hash;
    const oidcToken = hash.startsWith('#oidc_token=') ? hash.slice('#oidc_token='.length) : null;
    const oidcError = searchParams.get('oidc_error');

    if (oidcError) {
      showToast(oidcError, 'error');
      // Remove query params from URL cleanly
      navigate('/', { replace: true });
      return;
    }

    if (oidcToken) {
      api.exchangeOIDCToken(oidcToken).then((resp: LoginResponse) => {
        if (resp.requires_2fa && resp.pre_auth_token) {
          // OIDC user has 2FA enabled — redirect to 2FA step
          setPreAuthToken(resp.pre_auth_token);
          const methods = resp.two_fa_methods ?? [];
          setTwoFAMethods(methods);
          if (methods.includes('totp')) setTwoFAMethod('totp');
          else if (methods.includes('email')) setTwoFAMethod('email');
          else setTwoFAMethod('backup');
          setStep('2fa');
          // Remove oidc_token from URL so page refresh doesn't re-trigger exchange
          navigate('/login', { replace: true });
        } else if (resp.access_token && resp.user) {
          loginWithToken(resp.access_token, resp.user);
          showToast(t('login.loginSuccess'));
          navigate('/', { replace: true });
        }
      }).catch((err: Error) => {
        showToast(err.message || t('login.oidcLoginFailed'), 'error');
        navigate('/', { replace: true });
      });
    }
  }, [searchParams]); // eslint-disable-line react-hooks/exhaustive-deps

  // --- Step 1: Credentials login ---
  const loginMutation = useMutation({
    mutationFn: () => login(username, password),
    onSuccess: (resp: LoginResponse) => {
      if (resp.requires_2fa && resp.pre_auth_token) {
        // 2FA required — switch to verification step
        setPreAuthToken(resp.pre_auth_token);
        const methods = resp.two_fa_methods ?? [];
        setTwoFAMethods(methods);
        // Pick a sensible default method
        if (methods.includes('totp')) setTwoFAMethod('totp');
        else if (methods.includes('email')) setTwoFAMethod('email');
        else setTwoFAMethod('backup');
        setStep('2fa');
      } else if (resp.access_token && resp.user) {
        showToast(t('login.loginSuccess'));
        navigate('/');
      }
    },
    onError: (error: Error) => {
      showToast(error.message || t('login.loginFailed'), 'error');
    },
  });

  const forgotPasswordMutation = useMutation({
    mutationFn: (email: string) => api.forgotPassword({ email }),
    onSuccess: (data) => {
      showToast(data.message, 'success');
      setShowForgotPassword(false);
      setForgotEmail('');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // H-6: Mutation to set a new password using the reset token from the email link
  const resetPasswordMutation = useMutation({
    mutationFn: () => api.forgotPasswordConfirm(resetToken, newPassword),
    onSuccess: (data) => {
      showToast(data.message, 'success');
      setStep('credentials');
      setResetToken('');
      setNewPassword('');
      setConfirmPassword('');
    },
    onError: (error: Error) => {
      showToast(error.message || 'Password reset failed. The link may have expired.', 'error');
    },
  });

  // --- Step 2: 2FA verification ---
  const sendEmailOTPMutation = useMutation({
    mutationFn: () => api.sendEmailOTP(preAuthToken),
    onSuccess: (data: { message: string; pre_auth_token?: string }) => {
      setEmailOTPSent(true);
      // Backend issues a fresh pre-auth token after consuming the original one
      if (data.pre_auth_token) setPreAuthToken(data.pre_auth_token);
      showToast(data.message, 'success');
    },
    onError: (error: Error) => {
      showToast(error.message || t('login.twoFA.sendCodeFailed'), 'error');
    },
  });

  const verify2FAMutation = useMutation({
    mutationFn: () =>
      api.verify2FA({ pre_auth_token: preAuthToken, code: twoFACode, method: twoFAMethod }),
    onSuccess: (resp: LoginResponse) => {
      if (resp.access_token && resp.user) {
        loginWithToken(resp.access_token, resp.user);
        showToast(t('login.loginSuccess'));
        navigate('/');
      }
    },
    onError: (error: Error) => {
      showToast(error.message || t('login.twoFA.invalidCode'), 'error');
      setTwoFACode('');
    },
  });

  // OIDC login
  const oidcLoginMutation = useMutation({
    mutationFn: (providerId: number) => api.getOIDCAuthorizeUrl(providerId),
    onSuccess: (data) => {
      window.location.href = data.auth_url;
    },
    onError: (error: Error) => {
      showToast(error.message || t('login.oidcLoginFailed'), 'error');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      showToast(t('login.enterCredentials'), 'error');
      return;
    }
    loginMutation.mutate();
  };

  const handle2FASubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!twoFACode.trim()) {
      showToast(t('login.twoFA.enterCode'), 'error');
      return;
    }
    verify2FAMutation.mutate();
  };

  const handleForgotPassword = (e: React.FormEvent) => {
    e.preventDefault();
    if (!forgotEmail) {
      showToast(t('login.enterEmail'), 'error');
      return;
    }
    forgotPasswordMutation.mutate(forgotEmail);
  };

  const handleMethodChange = (method: 'totp' | 'email' | 'backup') => {
    setTwoFAMethod(method);
    setTwoFACode('');
    setEmailOTPSent(false);
  };

  // ---- Render: password-reset step (H-6) ----
  if (step === 'reset-password') {
    const handleResetSubmit = (e: React.FormEvent) => {
      e.preventDefault();
      if (newPassword !== confirmPassword) {
        showToast('Passwords do not match', 'error');
        return;
      }
      if (newPassword.length < 8) {
        showToast('Password must be at least 8 characters', 'error');
        return;
      }
      resetPasswordMutation.mutate();
    };

    return (
      <div className="min-h-screen flex items-center justify-center bg-bambu-dark p-4">
        <div className="max-w-md w-full space-y-8 p-8 bg-gradient-to-br from-bambu-card to-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary shadow-lg">
          <div className="text-center">
            <div className="flex items-center justify-center mb-4">
              <div className="w-14 h-14 rounded-full bg-bambu-green/20 flex items-center justify-center">
                <Key className="w-7 h-7 text-bambu-green" />
              </div>
            </div>
            <h2 className="text-2xl font-bold text-white">Set New Password</h2>
            <p className="mt-2 text-sm text-bambu-gray">Enter and confirm your new password below.</p>
          </div>

          <form onSubmit={handleResetSubmit} className="space-y-4">
            <div>
              <label htmlFor="new-password" className="block text-sm font-medium text-white mb-2">
                New Password
              </label>
              <input
                id="new-password"
                type="password"
                required
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="block w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                placeholder="At least 8 characters"
                autoFocus
                autoComplete="new-password"
                minLength={8}
              />
            </div>

            <div>
              <label htmlFor="confirm-password" className="block text-sm font-medium text-white mb-2">
                Confirm Password
              </label>
              <input
                id="confirm-password"
                type="password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="block w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                placeholder="Repeat new password"
                autoComplete="new-password"
              />
            </div>

            <button
              type="submit"
              disabled={resetPasswordMutation.isPending || !newPassword || !confirmPassword}
              className="w-full flex justify-center py-3 px-4 bg-bambu-green hover:bg-bambu-green-light text-white font-medium rounded-lg shadow-lg shadow-bambu-green/20 hover:shadow-bambu-green/30 focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:ring-offset-2 focus:ring-offset-bambu-dark-secondary transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {resetPasswordMutation.isPending ? 'Saving…' : 'Set New Password'}
            </button>
          </form>

          <div className="text-center">
            <button
              type="button"
              onClick={() => {
                setStep('credentials');
                setResetToken('');
                setNewPassword('');
                setConfirmPassword('');
              }}
              className="text-sm text-bambu-gray hover:text-bambu-green transition-colors"
            >
              Back to login
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ---- Render: 2FA step ----
  if (step === '2fa') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bambu-dark p-4">
        <div className="max-w-md w-full space-y-8 p-8 bg-gradient-to-br from-bambu-card to-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary shadow-lg">
          <div className="text-center">
            <div className="flex items-center justify-center mb-4">
              <div className="w-14 h-14 rounded-full bg-bambu-green/20 flex items-center justify-center">
                <Shield className="w-7 h-7 text-bambu-green" />
              </div>
            </div>
            <h2 className="text-2xl font-bold text-white">{t('login.twoFA.title')}</h2>
            <p className="mt-2 text-sm text-bambu-gray">{t('login.twoFA.subtitle')}</p>
          </div>

          {/* Method selector — only show if multiple methods available */}
          {twoFAMethods.length > 1 && (
            <div className="flex gap-2">
              {twoFAMethods.includes('totp') && (
                <button
                  type="button"
                  onClick={() => handleMethodChange('totp')}
                  className={`flex-1 flex flex-col items-center gap-1 py-2 px-3 rounded-lg border text-xs font-medium transition-colors ${
                    twoFAMethod === 'totp'
                      ? 'border-bambu-green bg-bambu-green/10 text-bambu-green'
                      : 'border-bambu-dark-tertiary text-bambu-gray hover:border-bambu-green/50'
                  }`}
                >
                  <Smartphone className="w-4 h-4" />
                  {t('login.twoFA.methodAuthenticator')}
                </button>
              )}
              {twoFAMethods.includes('email') && (
                <button
                  type="button"
                  onClick={() => handleMethodChange('email')}
                  className={`flex-1 flex flex-col items-center gap-1 py-2 px-3 rounded-lg border text-xs font-medium transition-colors ${
                    twoFAMethod === 'email'
                      ? 'border-bambu-green bg-bambu-green/10 text-bambu-green'
                      : 'border-bambu-dark-tertiary text-bambu-gray hover:border-bambu-green/50'
                  }`}
                >
                  <Mail className="w-4 h-4" />
                  {t('login.twoFA.methodEmail')}
                </button>
              )}
              {twoFAMethods.includes('backup') && (
                <button
                  type="button"
                  onClick={() => handleMethodChange('backup')}
                  className={`flex-1 flex flex-col items-center gap-1 py-2 px-3 rounded-lg border text-xs font-medium transition-colors ${
                    twoFAMethod === 'backup'
                      ? 'border-bambu-green bg-bambu-green/10 text-bambu-green'
                      : 'border-bambu-dark-tertiary text-bambu-gray hover:border-bambu-green/50'
                  }`}
                >
                  <Key className="w-4 h-4" />
                  {t('login.twoFA.methodBackup')}
                </button>
              )}
            </div>
          )}

          <form onSubmit={handle2FASubmit} className="space-y-4">
            {/* Method-specific instructions */}
            {twoFAMethod === 'totp' && (
              <p className="text-sm text-bambu-gray">{t('login.twoFA.instructionsTotp')}</p>
            )}
            {twoFAMethod === 'email' && (
              <div className="space-y-3">
                <p className="text-sm text-bambu-gray">
                  {emailOTPSent
                    ? t('login.twoFA.instructionsEmail')
                    : t('login.twoFA.instructionsEmailNotSent')}
                </p>
                {!emailOTPSent && (
                  <Button
                    type="button"
                    variant="secondary"
                    className="w-full"
                    onClick={() => sendEmailOTPMutation.mutate()}
                    disabled={sendEmailOTPMutation.isPending}
                  >
                    {sendEmailOTPMutation.isPending
                      ? t('login.twoFA.sendingCode')
                      : t('login.twoFA.sendCodeButton')}
                  </Button>
                )}
                {emailOTPSent && (
                  <button
                    type="button"
                    onClick={() => { setEmailOTPSent(false); sendEmailOTPMutation.mutate(); }}
                    className="text-xs text-bambu-gray hover:text-bambu-green transition-colors"
                  >
                    {t('login.twoFA.resendCode')}
                  </button>
                )}
              </div>
            )}
            {twoFAMethod === 'backup' && (
              <p className="text-sm text-bambu-gray">{t('login.twoFA.instructionsBackup')}</p>
            )}

            <div>
              <label htmlFor="twofa-code" className="block text-sm font-medium text-white mb-2">
                {twoFAMethod === 'backup'
                  ? t('login.twoFA.backupCodeLabel')
                  : t('login.twoFA.codeLabel')}
              </label>
              <input
                id="twofa-code"
                type="text"
                inputMode={twoFAMethod === 'backup' ? 'text' : 'numeric'}
                autoComplete="one-time-code"
                value={twoFACode}
                onChange={(e) => setTwoFACode(e.target.value.trim())}
                disabled={twoFAMethod === 'email' && !emailOTPSent}
                className="block w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray text-center tracking-widest text-xl font-mono focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors disabled:opacity-40"
                placeholder={twoFAMethod === 'backup'
                  ? t('login.twoFA.backupCodePlaceholder')
                  : t('login.twoFA.codePlaceholder')}
                maxLength={twoFAMethod === 'backup' ? 8 : 6}
                autoFocus
              />
            </div>

            <button
              type="submit"
              disabled={
                verify2FAMutation.isPending ||
                !twoFACode.trim() ||
                (twoFAMethod === 'email' && !emailOTPSent)
              }
              className="w-full flex justify-center py-3 px-4 bg-bambu-green hover:bg-bambu-green-light text-white font-medium rounded-lg shadow-lg shadow-bambu-green/20 hover:shadow-bambu-green/30 focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:ring-offset-2 focus:ring-offset-bambu-dark-secondary transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {verify2FAMutation.isPending
                ? t('login.twoFA.verifyingButton')
                : t('login.twoFA.verifyButton')}
            </button>
          </form>

          <div className="text-center">
            <button
              type="button"
              onClick={() => {
                setStep('credentials');
                setPreAuthToken('');
                setTwoFACode('');
                setEmailOTPSent(false);
              }}
              className="text-sm text-bambu-gray hover:text-bambu-green transition-colors"
            >
              {t('login.twoFA.backToLogin')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ---- Render: credentials step ----
  return (
    <div className="min-h-screen flex items-center justify-center bg-bambu-dark p-4">
      <div className="max-w-md w-full space-y-8 p-8 bg-gradient-to-br from-bambu-card to-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary shadow-lg">
        <div className="text-center">
          <div className="flex items-center justify-center mb-6">
            <img
              src={mode === 'dark' ? '/img/bambuddy_logo_dark_transparent.png' : '/img/bambuddy_logo_light.png'}
              alt="Bambuddy"
              className="h-16"
            />
          </div>
          <h2 className="text-3xl font-bold text-white">
            {t('login.title')}
          </h2>
          <p className="mt-2 text-sm text-bambu-gray">
            {t('login.subtitle')}
          </p>
        </div>

        <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
          <div className="space-y-4">
            <div>
              <label htmlFor="username" className="block text-sm font-medium text-white mb-2">
                {advancedAuthStatus?.advanced_auth_enabled
                  ? t('login.usernameOrEmail') || 'Username or Email'
                  : t('login.username')}
              </label>
              <input
                id="username"
                type="text"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="block w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                placeholder={advancedAuthStatus?.advanced_auth_enabled
                  ? t('login.usernameOrEmailPlaceholder') || 'Enter your username or email'
                  : t('login.usernamePlaceholder')}
                autoComplete="username"
              />
            </div>

            <div>
              <label htmlFor="password" className="block text-sm font-medium text-white mb-2">
                {t('login.password')}
              </label>
              <input
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="block w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                placeholder={t('login.passwordPlaceholder')}
                autoComplete="current-password"
              />
            </div>
          </div>

          <div>
            <button
              type="submit"
              disabled={loginMutation.isPending}
              className="w-full flex justify-center py-3 px-4 bg-bambu-green hover:bg-bambu-green-light text-white font-medium rounded-lg shadow-lg shadow-bambu-green/20 hover:shadow-bambu-green/30 focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:ring-offset-2 focus:ring-offset-bambu-dark-secondary transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-bambu-green"
            >
              {loginMutation.isPending ? t('login.signingIn') : t('login.signIn')}
            </button>
          </div>

          <div className="text-center">
            <button
              type="button"
              onClick={() => setShowForgotPassword(true)}
              className="text-sm text-bambu-gray hover:text-bambu-green transition-colors"
            >
              {t('login.forgotPassword')}
            </button>
          </div>
        </form>

        {/* OIDC provider buttons */}
        {oidcProviders && oidcProviders.length > 0 && (
          <div className="space-y-3">
            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-bambu-dark-tertiary" />
              </div>
              <div className="relative flex justify-center text-sm">
                <span className="px-2 bg-bambu-dark-secondary text-bambu-gray">{t('login.twoFA.orContinueWith')}</span>
              </div>
            </div>

            <div className="space-y-2">
              {oidcProviders.map((provider) => (
                <button
                  key={provider.id}
                  type="button"
                  onClick={() => oidcLoginMutation.mutate(provider.id)}
                  disabled={oidcLoginMutation.isPending}
                  className="w-full flex items-center justify-center gap-3 py-3 px-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary hover:border-bambu-green/50 rounded-lg text-white font-medium transition-colors disabled:opacity-50"
                >
                  {provider.icon_url ? (
                    <img src={provider.icon_url} alt="" className="w-5 h-5 object-contain" />
                  ) : (
                    <Shield className="w-5 h-5 text-bambu-green" />
                  )}
                  {t('login.twoFA.signInWith', { provider: provider.name })}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Forgot Password Modal */}
      {showForgotPassword && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={() => setShowForgotPassword(false)}
        >
          <Card
            className="w-full max-w-md"
            onClick={(e: React.MouseEvent) => e.stopPropagation()}
          >
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Mail className="w-5 h-5 text-bambu-green" />
                  <h2 className="text-lg font-semibold text-white">{t('login.forgotPasswordTitle')}</h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowForgotPassword(false);
                    setForgotEmail('');
                  }}
                >
                  <X className="w-5 h-5" />
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {advancedAuthStatus?.advanced_auth_enabled ? (
                <form onSubmit={handleForgotPassword} className="space-y-4">
                  <p className="text-bambu-gray text-sm">
                    {t('login.forgotPasswordEmailMessage') || 'Enter your email address and we\'ll send you a new password.'}
                  </p>

                  <div>
                    <label htmlFor="forgot-email" className="block text-sm font-medium text-white mb-2">
                      {t('login.emailAddress') || 'Email Address'}
                    </label>
                    <input
                      id="forgot-email"
                      type="email"
                      required
                      value={forgotEmail}
                      onChange={(e) => setForgotEmail(e.target.value)}
                      className="block w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                      placeholder={t('login.emailPlaceholder') || 'your.email@example.com'}
                    />
                  </div>

                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      className="flex-1"
                      onClick={() => {
                        setShowForgotPassword(false);
                        setForgotEmail('');
                      }}
                    >
                      {t('login.cancel') || 'Cancel'}
                    </Button>
                    <Button
                      type="submit"
                      className="flex-1"
                      disabled={forgotPasswordMutation.isPending}
                    >
                      {forgotPasswordMutation.isPending
                        ? (t('login.sending') || 'Sending...')
                        : (t('login.sendResetEmail') || 'Send Reset Email')}
                    </Button>
                  </div>
                </form>
              ) : (
                <div className="space-y-4">
                  <p className="text-bambu-gray">
                    {t('login.forgotPasswordMessage')}
                  </p>

                  <div className="bg-bambu-dark rounded-lg p-4 space-y-2">
                    <p className="text-sm text-white font-medium">{t('login.howToReset')}</p>
                    <ol className="text-sm text-bambu-gray space-y-1 list-decimal list-inside">
                      <li>{t('login.resetStep1')}</li>
                      <li>{t('login.resetStep2')}</li>
                      <li>{t('login.resetStep3')}</li>
                      <li>{t('login.resetStep4')}</li>
                    </ol>
                  </div>

                  <Button
                    variant="secondary"
                    className="w-full"
                    onClick={() => setShowForgotPassword(false)}
                  >
                    {t('login.gotIt')}
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
