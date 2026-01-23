import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { api } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useTheme } from '../contexts/ThemeContext';
import { useAuth } from '../contexts/AuthContext';
import { Info } from 'lucide-react';

export function SetupPage() {
  const navigate = useNavigate();
  const { showToast } = useToast();
  const { mode } = useTheme();
  const { refreshAuth } = useAuth();
  const [authEnabled, setAuthEnabled] = useState(false);
  const [adminUsername, setAdminUsername] = useState('');
  const [adminPassword, setAdminPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const setupMutation = useMutation({
    mutationFn: () =>
      api.setupAuth({
        auth_enabled: authEnabled,
        admin_username: authEnabled ? adminUsername : undefined,
        admin_password: authEnabled ? adminPassword : undefined,
      }),
    onSuccess: async (data) => {
      // Refresh auth status after setup
      await refreshAuth();

      if (data.auth_enabled) {
        if (data.admin_created) {
          showToast('Authentication enabled and admin user created');
          navigate('/login');
        } else {
          showToast('Authentication enabled using existing admin users');
          navigate('/login');
        }
      } else {
        showToast('Setup completed');
        navigate('/');
      }
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (authEnabled) {
      // Only validate if credentials are provided
      // If no credentials provided, backend will use existing admin users if they exist
      if (adminUsername || adminPassword) {
        if (!adminUsername || !adminPassword) {
          showToast('Please enter both admin username and password, or leave both empty to use existing admin users', 'error');
          return;
        }
        if (adminPassword !== confirmPassword) {
          showToast('Passwords do not match', 'error');
          return;
        }
        if (adminPassword.length < 6) {
          showToast('Password must be at least 6 characters', 'error');
          return;
        }
      }
    }

    setupMutation.mutate();
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
            Bambuddy Setup
          </h2>
          <p className="mt-2 text-sm text-bambu-gray">
            Configure authentication for your Bambuddy instance
          </p>
        </div>

        <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
          <div className="space-y-4">
            <div className="flex items-center p-4 bg-bambu-dark-secondary/50 rounded-lg border border-bambu-dark-tertiary">
              <input
                id="auth-enabled"
                type="checkbox"
                checked={authEnabled}
                onChange={(e) => setAuthEnabled(e.target.checked)}
                className="h-4 w-4 text-bambu-green focus:ring-bambu-green border-bambu-dark-tertiary rounded bg-bambu-dark-secondary"
              />
              <label htmlFor="auth-enabled" className="ml-3 block text-sm font-medium text-white">
                Enable Authentication
              </label>
            </div>

            {authEnabled && (
              <div className="space-y-4 mt-4">
                <div className="p-3 bg-bambu-dark-secondary/50 border border-bambu-dark-tertiary rounded-lg">
                  <div className="flex items-start gap-2">
                    <Info className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                    <div className="text-sm text-bambu-gray">
                      <p className="text-white font-medium mb-1">Admin Account</p>
                      <p>
                        If admin users already exist, authentication will be enabled using the existing admin accounts.
                        Leave the fields below empty to use existing admins, or enter new credentials to create a new admin user.
                      </p>
                    </div>
                  </div>
                </div>

                <div>
                  <label htmlFor="admin-username" className="block text-sm font-medium text-white mb-2">
                    Admin Username <span className="text-bambu-gray text-xs">(optional if admin users exist)</span>
                  </label>
                  <input
                    id="admin-username"
                    type="text"
                    value={adminUsername}
                    onChange={(e) => setAdminUsername(e.target.value)}
                    className="block w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder="Enter admin username (optional)"
                    autoComplete="username"
                  />
                </div>

                <div>
                  <label htmlFor="admin-password" className="block text-sm font-medium text-white mb-2">
                    Admin Password <span className="text-bambu-gray text-xs">(optional if admin users exist)</span>
                  </label>
                  <input
                    id="admin-password"
                    type="password"
                    value={adminPassword}
                    onChange={(e) => setAdminPassword(e.target.value)}
                    className="block w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder="Enter admin password (optional)"
                    minLength={6}
                    autoComplete="new-password"
                  />
                </div>

                {adminPassword && (
                  <div>
                    <label htmlFor="confirm-password" className="block text-sm font-medium text-white mb-2">
                      Confirm Password
                    </label>
                    <input
                      id="confirm-password"
                      type="password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      className="block w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                      placeholder="Confirm admin password"
                      minLength={6}
                      autoComplete="new-password"
                    />
                  </div>
                )}
              </div>
            )}
          </div>

          <div>
            <button
              type="submit"
              disabled={setupMutation.isPending}
              className="w-full flex justify-center py-3 px-4 bg-bambu-green hover:bg-bambu-green-light text-white font-medium rounded-lg shadow-lg shadow-bambu-green/20 hover:shadow-bambu-green/30 focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:ring-offset-2 focus:ring-offset-bambu-dark-secondary transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-bambu-green"
            >
              {setupMutation.isPending ? 'Setting up...' : 'Complete Setup'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
