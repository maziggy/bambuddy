import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Mail, Send, Lock, Unlock, AlertTriangle, CheckCircle, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import type { SMTPSettings, TestSMTPRequest } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { useEffect } from 'react';

export function EmailSettings() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  
  const [smtpSettings, setSMTPSettings] = useState<SMTPSettings>({
    smtp_host: '',
    smtp_port: 587,
    smtp_username: '',
    smtp_password: '',
    smtp_use_tls: true,
    smtp_from_email: '',
    smtp_from_name: 'BamBuddy',
  });
  const [testEmail, setTestEmail] = useState('');

  // Fetch SMTP settings
  const { data: existingSettings, isLoading } = useQuery({
    queryKey: ['smtpSettings'],
    queryFn: () => api.getSMTPSettings(),
  });

  // Fetch advanced auth status
  const { data: advancedAuthStatus } = useQuery({
    queryKey: ['advancedAuthStatus'],
    queryFn: () => api.getAdvancedAuthStatus(),
  });

  // Load existing settings when fetched
  useEffect(() => {
    if (existingSettings) {
      setSMTPSettings({
        ...existingSettings,
        smtp_password: '', // Never show password
      });
    }
  }, [existingSettings]);

  // Save SMTP settings
  const saveMutation = useMutation({
    mutationFn: (settings: SMTPSettings) => api.saveSMTPSettings(settings),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smtpSettings'] });
      queryClient.invalidateQueries({ queryKey: ['advancedAuthStatus'] });
      showToast('SMTP settings saved successfully', 'success');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // Test SMTP connection
  const testMutation = useMutation({
    mutationFn: (request: TestSMTPRequest) => api.testSMTP(request),
    onSuccess: (data) => {
      showToast(data.message, data.success ? 'success' : 'error');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // Toggle advanced auth
  const toggleAdvancedAuthMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      enabled ? api.enableAdvancedAuth() : api.disableAdvancedAuth(),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['advancedAuthStatus'] });
      showToast(data.message, 'success');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const handleSave = () => {
    // Validate required fields
    if (!smtpSettings.smtp_host || !smtpSettings.smtp_username || !smtpSettings.smtp_from_email) {
      showToast('Please fill in all required fields', 'error');
      return;
    }
    saveMutation.mutate(smtpSettings);
  };

  const handleTest = () => {
    if (!testEmail) {
      showToast('Please enter a test email address', 'error');
      return;
    }
    if (!smtpSettings.smtp_host || !smtpSettings.smtp_username || !smtpSettings.smtp_password || !smtpSettings.smtp_from_email) {
      showToast('Please fill in all SMTP settings before testing', 'error');
      return;
    }
    testMutation.mutate({
      smtp_host: smtpSettings.smtp_host,
      smtp_port: smtpSettings.smtp_port,
      smtp_username: smtpSettings.smtp_username,
      smtp_password: smtpSettings.smtp_password,
      smtp_use_tls: smtpSettings.smtp_use_tls,
      smtp_from_email: smtpSettings.smtp_from_email,
      test_recipient: testEmail,
    });
  };

  const handleToggleAdvancedAuth = () => {
    if (!advancedAuthStatus?.advanced_auth_enabled && !advancedAuthStatus?.smtp_configured) {
      showToast('Please configure and test SMTP settings first', 'error');
      return;
    }
    toggleAdvancedAuthMutation.mutate(!advancedAuthStatus?.advanced_auth_enabled);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center p-12">
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Advanced Authentication Toggle */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Mail className="w-5 h-5 text-bambu-green" />
              <h2 className="text-lg font-semibold text-white">
                {t('settings.email.advancedAuth') || 'Advanced Authentication'}
              </h2>
            </div>
            <Button
              onClick={handleToggleAdvancedAuth}
              disabled={toggleAdvancedAuthMutation.isPending}
              variant={advancedAuthStatus?.advanced_auth_enabled ? 'danger' : 'primary'}
            >
              {advancedAuthStatus?.advanced_auth_enabled ? (
                <>
                  <Unlock className="w-4 h-4" />
                  {t('settings.email.disable') || 'Disable'}
                </>
              ) : (
                <>
                  <Lock className="w-4 h-4" />
                  {t('settings.email.enable') || 'Enable'}
                </>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {advancedAuthStatus?.advanced_auth_enabled ? (
              <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-4">
                <div className="flex items-start gap-3">
                  <CheckCircle className="w-5 h-5 text-green-400 mt-0.5 flex-shrink-0" />
                  <div className="space-y-2">
                    <p className="text-white font-medium">
                      {t('settings.email.advancedAuthEnabled') || 'Advanced Authentication is enabled'}
                    </p>
                    <ul className="text-sm text-green-300 space-y-1 list-disc list-inside">
                      <li>{t('settings.email.feature1') || 'Passwords are auto-generated and emailed to new users'}</li>
                      <li>{t('settings.email.feature2') || 'Users can login with username or email'}</li>
                      <li>{t('settings.email.feature3') || 'Forgot password feature is available'}</li>
                      <li>{t('settings.email.feature4') || 'Admins can reset user passwords via email'}</li>
                    </ul>
                  </div>
                </div>
              </div>
            ) : (
              <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="w-5 h-5 text-yellow-400 mt-0.5 flex-shrink-0" />
                  <div className="space-y-2">
                    <p className="text-white font-medium">
                      {t('settings.email.advancedAuthDisabled') || 'Advanced Authentication is disabled'}
                    </p>
                    <p className="text-sm text-yellow-300">
                      {t('settings.email.advancedAuthDisabledDesc') || 'Configure and test SMTP settings below, then enable advanced authentication to activate email-based features.'}
                    </p>
                  </div>
                </div>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* SMTP Configuration */}
      <Card>
        <CardHeader>
          <h2 className="text-lg font-semibold text-white">
            {t('settings.email.smtpSettings') || 'SMTP Configuration'}
          </h2>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-white mb-2">
                  {t('settings.email.smtpHost') || 'SMTP Server'} *
                </label>
                <input
                  type="text"
                  value={smtpSettings.smtp_host}
                  onChange={(e) => setSMTPSettings({ ...smtpSettings, smtp_host: e.target.value })}
                  placeholder="smtp.gmail.com"
                  className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-white mb-2">
                  {t('settings.email.smtpPort') || 'Port'} *
                </label>
                <input
                  type="number"
                  value={smtpSettings.smtp_port}
                  onChange={(e) => setSMTPSettings({ ...smtpSettings, smtp_port: parseInt(e.target.value) || 587 })}
                  className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-white mb-2">
                {t('settings.email.username') || 'Username'} *
              </label>
              <input
                type="text"
                value={smtpSettings.smtp_username}
                onChange={(e) => setSMTPSettings({ ...smtpSettings, smtp_username: e.target.value })}
                placeholder="your.email@gmail.com"
                className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-white mb-2">
                {t('settings.email.password') || 'Password'} *
              </label>
              <input
                type="password"
                value={smtpSettings.smtp_password}
                onChange={(e) => setSMTPSettings({ ...smtpSettings, smtp_password: e.target.value })}
                placeholder={existingSettings ? '••••••••' : 'Enter password'}
                className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-white mb-2">
                  {t('settings.email.fromEmail') || 'From Email'} *
                </label>
                <input
                  type="email"
                  value={smtpSettings.smtp_from_email}
                  onChange={(e) => setSMTPSettings({ ...smtpSettings, smtp_from_email: e.target.value })}
                  placeholder="noreply@yourdomain.com"
                  className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-white mb-2">
                  {t('settings.email.fromName') || 'From Name'}
                </label>
                <input
                  type="text"
                  value={smtpSettings.smtp_from_name}
                  onChange={(e) => setSMTPSettings({ ...smtpSettings, smtp_from_name: e.target.value })}
                  placeholder="BamBuddy"
                  className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                />
              </div>
            </div>

            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="use_tls"
                checked={smtpSettings.smtp_use_tls}
                onChange={(e) => setSMTPSettings({ ...smtpSettings, smtp_use_tls: e.target.checked })}
                className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark"
              />
              <label htmlFor="use_tls" className="text-sm text-white">
                {t('settings.email.useTLS') || 'Use TLS (recommended)'}
              </label>
            </div>

            <div className="flex gap-2">
              <Button
                onClick={handleSave}
                disabled={saveMutation.isPending}
                className="flex-1"
              >
                {saveMutation.isPending ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    {t('settings.email.saving') || 'Saving...'}
                  </>
                ) : (
                  t('settings.email.save') || 'Save Settings'
                )}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Test SMTP */}
      <Card>
        <CardHeader>
          <h2 className="text-lg font-semibold text-white">
            {t('settings.email.testConnection') || 'Test SMTP Connection'}
          </h2>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-white mb-2">
                {t('settings.email.testRecipient') || 'Test Recipient Email'}
              </label>
              <input
                type="email"
                value={testEmail}
                onChange={(e) => setTestEmail(e.target.value)}
                placeholder="test@example.com"
                className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
              />
            </div>
            <Button
              onClick={handleTest}
              disabled={testMutation.isPending}
              variant="secondary"
            >
              {testMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('settings.email.sending') || 'Sending...'}
                </>
              ) : (
                <>
                  <Send className="w-4 h-4" />
                  {t('settings.email.sendTest') || 'Send Test Email'}
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
