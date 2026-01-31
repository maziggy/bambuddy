import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useTheme } from '../contexts/ThemeContext';

export function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { login } = useAuth();
  const { showToast } = useToast();
  const { mode } = useTheme();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const loginMutation = useMutation({
    mutationFn: () => login(username, password),
    onSuccess: () => {
      showToast(t('login.success'));
      navigate('/');
    },
    onError: (error: Error) => {
      showToast(error.message || t('login.failed'), 'error');
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
                {t('login.username')}
              </label>
              <input
                id="username"
                type="text"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="block w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                placeholder={t('login.usernamePlaceholder')}
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
              {loginMutation.isPending ? t('login.loggingIn') : t('login.signIn')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
