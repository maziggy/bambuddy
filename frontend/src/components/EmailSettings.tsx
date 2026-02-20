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

const SECURITY_PORT_MAP: Record<string, number> = {
  starttls: 587,
  ssl: 465,
  none: 25,
};

const PORT_SECURITY_MAP: Record<number, string> = {
  587: 'starttls',
  465: 'ssl',
  25: 'none',
};

export function EmailSettings() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const [smtpSettings, setSMTPSettings] = useState<SMTPSettings>({
    smtp_host: '',
    smtp_port: 587,
    smtp_username: '',
    smtp_password: '',
    smtp_security: 'starttls',
    smtp_auth_enabled: true,
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

  const handleSecurityChange = (security: 'starttls' | 'ssl' | 'none') => {
    setSMTPSettings({
      ...smtpSettings,
      smtp_security: security,
      smtp_port: SECURITY_PORT_MAP[security],
    });
  };

  const handlePortChange = (port: number) => {
    const matchedSecurity = PORT_SECURITY_MAP[port];
    setSMTPSettings({
      ...smtpSettings,
      smtp_port: port,
      ...(matchedSecurity ? { smtp_security: matchedSecurity as 'starttls' | 'ssl' | 'none' } : {}),
    });
  };

  const handleAuthChange = (enabled: boolean) => {
    setSMTPSettings({
      ...smtpSettings,
      smtp_auth_enabled: enabled,
      ...(!enabled ? { smtp_username: '', smtp_password: '' } : {}),
    });
  };

  // Save SMTP settings
  const saveMutation = useMutation({
    mutationFn: (settings: SMTPSettings) => api.saveSMTPSettings(settings),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smtpSettings'] });
      queryClient.invalidateQueries({ queryKey: ['advancedAuthStatus'] });
      showToast(t('settings.email.success.settingsSaved'), 'success');
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
    if (!smtpSettings.smtp_host || !smtpSettings.smtp_from_email) {
      showToast(t('settings.email.errors.requiredFields'), 'error');
      return;
    }
    // Validate auth fields when authentication is enabled
    if (smtpSettings.smtp_auth_enabled && (!smtpSettings.smtp_username)) {
      showToast(t('settings.email.errors.usernameRequired'), 'error');
      return;
    }
    saveMutation.mutate(smtpSettings);
  };

  const handleTest = () => {
    if (!testEmail) {
      showToast(t('settings.email.errors.enterTestEmail'), 'error');
      return;
    }
    if (!smtpSettings.smtp_host || !smtpSettings.smtp_from_email) {
      showToast(t('settings.email.errors.smtpServerAndEmail'), 'error');
      return;
    }
    // Validate auth fields when authentication is enabled
    if (smtpSettings.smtp_auth_enabled && (!smtpSettings.smtp_username || !smtpSettings.smtp_password)) {
      showToast(t('settings.email.errors.usernamePasswordRequired'), 'error');
      return;
    }
    testMutation.mutate({
      smtp_host: smtpSettings.smtp_host,
      smtp_port: smtpSettings.smtp_port,
      smtp_username: smtpSettings.smtp_username,
      smtp_password: smtpSettings.smtp_password,
      smtp_security: smtpSettings.smtp_security,
      smtp_auth_enabled: smtpSettings.smtp_auth_enabled,
      smtp_from_email: smtpSettings.smtp_from_email,
      test_recipient: testEmail,
    });
  };

  const handleToggleAdvancedAuth = () => {
    if (!advancedAuthStatus?.advanced_auth_enabled && !advancedAuthStatus?.smtp_configured) {
      showToast(t('settings.email.errors.configureSmtpFirst'), 'error');
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

  const advancedEnabled = advancedAuthStatus?.advanced_auth_enabled ?? false;
  const inputClasses = "w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors";
  const disabledInputClasses = "w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white/40 placeholder-bambu-gray/40 cursor-not-allowed";

  return (
    <div className="space-y-6">
      {/* Advanced Authentication Toggle - Always visible */}
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
              variant={advancedEnabled ? 'danger' : 'primary'}
            >
              {advancedEnabled ? (
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
            {advancedEnabled ? (
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
                      {t('settings.email.advancedAuthDisabledDesc') || 'Enable advanced authentication to activate email-based features for user management.'}
                    </p>
                  </div>
                </div>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* SMTP Configuration - dimmed when advanced auth is disabled */}
      <div className={!advancedEnabled ? 'opacity-50 pointer-events-none' : ''}>
        <Card>
          <CardHeader>
            <h2 className="text-lg font-semibold text-white">
              {t('settings.email.smtpSettings') || 'SMTP Configuration'}
            </h2>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {/* Authentication - at the top */}
              <div>
                <label className="block text-sm font-medium text-white mb-2">
                  {t('settings.email.authentication') || 'Authentication'}
                </label>
                <select
                  value={smtpSettings.smtp_auth_enabled ? 'true' : 'false'}
                  onChange={(e) => handleAuthChange(e.target.value === 'true')}
                  className={inputClasses}
                >
                  <option value="true">{t('settings.email.authOptions.enabled')}</option>
                  <option value="false">{t('settings.email.authOptions.disabled')}</option>
                </select>
              </div>

              {/* Username / Password - dimmed when auth disabled */}
              <div className={`grid grid-cols-1 md:grid-cols-2 gap-4 transition-opacity ${!smtpSettings.smtp_auth_enabled ? 'opacity-40 pointer-events-none' : ''}`}>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('settings.email.username') || 'Username'}
                  </label>
                  <input
                    type="text"
                    value={smtpSettings.smtp_username || ''}
                    onChange={(e) => setSMTPSettings({ ...smtpSettings, smtp_username: e.target.value })}
                    placeholder="your.email@gmail.com"
                    disabled={!smtpSettings.smtp_auth_enabled}
                    className={smtpSettings.smtp_auth_enabled ? inputClasses : disabledInputClasses}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('settings.email.password') || 'Password'}
                  </label>
                  <input
                    type="password"
                    value={smtpSettings.smtp_password || ''}
                    onChange={(e) => setSMTPSettings({ ...smtpSettings, smtp_password: e.target.value })}
                    placeholder={existingSettings ? '••••••••' : 'App password'}
                    disabled={!smtpSettings.smtp_auth_enabled}
                    className={smtpSettings.smtp_auth_enabled ? inputClasses : disabledInputClasses}
                  />
                </div>
              </div>

              {/* SMTP Server / Port */}
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
                    className={inputClasses}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('settings.email.smtpPort') || 'SMTP Port'}
                  </label>
                  <input
                    type="number"
                    value={smtpSettings.smtp_port}
                    onChange={(e) => handlePortChange(parseInt(e.target.value) || 587)}
                    placeholder="587"
                    className={inputClasses}
                  />
                </div>
              </div>

              {/* Security */}
              <div>
                <label className="block text-sm font-medium text-white mb-2">
                  {t('settings.email.security') || 'Security'}
                </label>
                <select
                  value={smtpSettings.smtp_security}
                  onChange={(e) => handleSecurityChange(e.target.value as 'starttls' | 'ssl' | 'none')}
                  className={inputClasses}
                >
                  <option value="starttls">{t('settings.email.securityOptions.starttls')}</option>
                  <option value="ssl">{t('settings.email.securityOptions.ssl')}</option>
                  <option value="none">{t('settings.email.securityOptions.none')}</option>
                </select>
              </div>

              {/* From Email / Name */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('settings.email.fromEmail') || 'From Email'} *
                  </label>
                  <input
                    type="email"
                    value={smtpSettings.smtp_from_email}
                    onChange={(e) => setSMTPSettings({ ...smtpSettings, smtp_from_email: e.target.value })}
                    placeholder="your@email.com"
                    className={inputClasses}
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
                    className={inputClasses}
                  />
                </div>
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
      </div>

      {/* Test SMTP - dimmed when advanced auth is disabled */}
      <div className={!advancedEnabled ? 'opacity-50 pointer-events-none' : ''}>
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
                  className={inputClasses}
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

    </div>
  );
}
