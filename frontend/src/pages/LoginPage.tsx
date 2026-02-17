import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useTheme } from '../contexts/ThemeContext';
import { X, Mail } from 'lucide-react';
import { api } from '../api/client';
import { Card, CardHeader, CardContent } from '../components/Card';
import { Button } from '../components/Button';

export function LoginPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { login } = useAuth();
  const { showToast } = useToast();
  const { mode } = useTheme();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showForgotPassword, setShowForgotPassword] = useState(false);
  const [forgotEmail, setForgotEmail] = useState('');

  // Check if advanced auth is enabled
  const { data: advancedAuthStatus } = useQuery({
    queryKey: ['advancedAuthStatus'],
    queryFn: () => api.getAdvancedAuthStatus(),
  });

  const loginMutation = useMutation({
    mutationFn: () => login(username, password),
    onSuccess: () => {
      showToast(t('login.loginSuccess'));
      navigate('/');
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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      showToast(t('login.enterCredentials'), 'error');
      return;
    }
    loginMutation.mutate();
  };

  const handleForgotPassword = (e: React.FormEvent) => {
    e.preventDefault();
    if (!forgotEmail) {
      showToast('Please enter your email address', 'error');
      return;
    }
    forgotPasswordMutation.mutate(forgotEmail);
  };

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

          {advancedAuthStatus?.advanced_auth_enabled && (
            <div className="text-center">
              <button
                type="button"
                onClick={() => setShowForgotPassword(true)}
                className="text-sm text-bambu-gray hover:text-bambu-green transition-colors"
              >
                {t('login.forgotPassword')}
              </button>
            </div>
          )}
        </form>
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
