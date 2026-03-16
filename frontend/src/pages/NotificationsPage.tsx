import { useState, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Bell, CheckCircle2, Loader2, Mail, Save } from 'lucide-react';
import { api } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { Button } from '../components/Button';
import { Card, CardContent, CardHeader } from '../components/Card';

export function NotificationsPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [notifyPrintStart, setNotifyPrintStart] = useState(true);
  const [notifyPrintComplete, setNotifyPrintComplete] = useState(true);
  const [notifyPrintFailed, setNotifyPrintFailed] = useState(true);
  const [notifyPrintStopped, setNotifyPrintStopped] = useState(true);
  const [isDirty, setIsDirty] = useState(false);

  // Check advanced auth status - redirect if disabled
  const { data: advancedAuthStatus, isLoading: isAdvancedAuthLoading } = useQuery({
    queryKey: ['advancedAuthStatus'],
    queryFn: api.getAdvancedAuthStatus,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });

  // Fetch current preferences
  const { data: preferences, isLoading } = useQuery({
    queryKey: ['user-email-preferences'],
    queryFn: () => api.getUserEmailPreferences(),
  });

  // Redirect to settings if Advanced Auth is disabled
  useEffect(() => {
    if (advancedAuthStatus && !advancedAuthStatus.advanced_auth_enabled) {
      navigate('/settings', { replace: true });
    }
  }, [advancedAuthStatus, navigate]);

  // Populate form when preferences load
  useEffect(() => {
    if (preferences) {
      setNotifyPrintStart(preferences.notify_print_start);
      setNotifyPrintComplete(preferences.notify_print_complete);
      setNotifyPrintFailed(preferences.notify_print_failed);
      setNotifyPrintStopped(preferences.notify_print_stopped);
      setIsDirty(false);
    }
  }, [preferences]);

  // Save preferences
  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateUserEmailPreferences({
        notify_print_start: notifyPrintStart,
        notify_print_complete: notifyPrintComplete,
        notify_print_failed: notifyPrintFailed,
        notify_print_stopped: notifyPrintStopped,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['user-email-preferences'] });
      setIsDirty(false);
      showToast(t('notifications.userEmail.saveSuccess'), 'success');
    },
    onError: (err: Error) => {
      showToast(err.message || t('notifications.userEmail.saveError'), 'error');
    },
  });

  const handleToggle = (
    setter: React.Dispatch<React.SetStateAction<boolean>>,
    value: boolean
  ) => {
    setter(!value);
    setIsDirty(true);
  };

  if (isLoading || isAdvancedAuthLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6 max-w-2xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Bell className="w-7 h-7 text-bambu-green" />
        <h1 className="text-2xl font-bold text-white">{t('notifications.userEmail.title')}</h1>
      </div>

      {/* Info card */}
      <Card className="mb-6 border-blue-500/30 bg-blue-500/5">
        <CardContent className="py-4">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-full flex items-center justify-center bg-blue-500/20 flex-shrink-0">
              <Mail className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <h3 className="text-white font-medium">{t('notifications.userEmail.emailNotifications')}</h3>
              <p className="text-sm text-bambu-gray mt-1">
                {t('notifications.userEmail.emailNotificationsDesc')}
              </p>
              {user?.email ? (
                <p className="text-sm text-blue-400 mt-2">
                  {t('notifications.userEmail.sendingTo')}: <strong>{user.email}</strong>
                </p>
              ) : (
                <p className="text-sm text-yellow-400 mt-2">
                  {t('notifications.userEmail.noEmailWarning')}
                </p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Preferences card */}
      <Card className="mb-6">
        <CardHeader>
          <h2 className="text-lg font-semibold text-white">{t('notifications.userEmail.printJobNotifications')}</h2>
          <p className="text-sm text-bambu-gray mt-1">{t('notifications.userEmail.printJobNotificationsDesc')}</p>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Print Job Starts */}
          <div className="flex items-center justify-between p-4 bg-bambu-dark rounded-lg">
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center ${notifyPrintStart ? 'bg-bambu-green/20' : 'bg-bambu-dark-tertiary'}`}>
                <CheckCircle2 className={`w-5 h-5 ${notifyPrintStart ? 'text-bambu-green' : 'text-bambu-gray'}`} />
              </div>
              <div>
                <p className="text-white font-medium">{t('notifications.userEmail.printJobStarts')}</p>
                <p className="text-sm text-bambu-gray">{t('notifications.userEmail.printJobStartsDesc')}</p>
              </div>
            </div>
            <button
              onClick={() => handleToggle(setNotifyPrintStart, notifyPrintStart)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-bambu-green focus:ring-offset-2 focus:ring-offset-bambu-dark ${
                notifyPrintStart ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
              }`}
              role="switch"
              aria-checked={notifyPrintStart}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  notifyPrintStart ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Print Job Finishes */}
          <div className="flex items-center justify-between p-4 bg-bambu-dark rounded-lg">
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center ${notifyPrintComplete ? 'bg-bambu-green/20' : 'bg-bambu-dark-tertiary'}`}>
                <CheckCircle2 className={`w-5 h-5 ${notifyPrintComplete ? 'text-bambu-green' : 'text-bambu-gray'}`} />
              </div>
              <div>
                <p className="text-white font-medium">{t('notifications.userEmail.printJobFinishes')}</p>
                <p className="text-sm text-bambu-gray">{t('notifications.userEmail.printJobFinishesDesc')}</p>
              </div>
            </div>
            <button
              onClick={() => handleToggle(setNotifyPrintComplete, notifyPrintComplete)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-bambu-green focus:ring-offset-2 focus:ring-offset-bambu-dark ${
                notifyPrintComplete ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
              }`}
              role="switch"
              aria-checked={notifyPrintComplete}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  notifyPrintComplete ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Print Errors */}
          <div className="flex items-center justify-between p-4 bg-bambu-dark rounded-lg">
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center ${notifyPrintFailed ? 'bg-bambu-green/20' : 'bg-bambu-dark-tertiary'}`}>
                <CheckCircle2 className={`w-5 h-5 ${notifyPrintFailed ? 'text-bambu-green' : 'text-bambu-gray'}`} />
              </div>
              <div>
                <p className="text-white font-medium">{t('notifications.userEmail.printErrors')}</p>
                <p className="text-sm text-bambu-gray">{t('notifications.userEmail.printErrorsDesc')}</p>
              </div>
            </div>
            <button
              onClick={() => handleToggle(setNotifyPrintFailed, notifyPrintFailed)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-bambu-green focus:ring-offset-2 focus:ring-offset-bambu-dark ${
                notifyPrintFailed ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
              }`}
              role="switch"
              aria-checked={notifyPrintFailed}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  notifyPrintFailed ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>

          {/* Print Job Stops */}
          <div className="flex items-center justify-between p-4 bg-bambu-dark rounded-lg">
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center ${notifyPrintStopped ? 'bg-bambu-green/20' : 'bg-bambu-dark-tertiary'}`}>
                <CheckCircle2 className={`w-5 h-5 ${notifyPrintStopped ? 'text-bambu-green' : 'text-bambu-gray'}`} />
              </div>
              <div>
                <p className="text-white font-medium">{t('notifications.userEmail.printJobStops')}</p>
                <p className="text-sm text-bambu-gray">{t('notifications.userEmail.printJobStopsDesc')}</p>
              </div>
            </div>
            <button
              onClick={() => handleToggle(setNotifyPrintStopped, notifyPrintStopped)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-bambu-green focus:ring-offset-2 focus:ring-offset-bambu-dark ${
                notifyPrintStopped ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
              }`}
              role="switch"
              aria-checked={notifyPrintStopped}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  notifyPrintStopped ? 'translate-x-6' : 'translate-x-1'
                }`}
              />
            </button>
          </div>
        </CardContent>
      </Card>

      {/* Save button */}
      <div className="flex justify-end">
        <Button
          onClick={() => saveMutation.mutate()}
          disabled={!isDirty || saveMutation.isPending || !user?.email}
        >
          {saveMutation.isPending ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('common.saving')}
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              {t('common.save')}
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
