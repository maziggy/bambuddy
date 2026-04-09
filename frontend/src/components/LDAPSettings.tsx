import { useState, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Shield, Lock, Unlock, AlertTriangle, CheckCircle, Loader2, Send } from 'lucide-react';
import { api } from '../api/client';
import type { AppSettings } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { Collapsible } from './Collapsible';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';

const SECURITY_PORT_MAP: Record<string, string> = {
  starttls: '389',
  ldaps: '636',
};

interface LDAPFormState {
  ldap_server_url: string;
  ldap_bind_dn: string;
  ldap_bind_password: string;
  ldap_search_base: string;
  ldap_user_filter: string;
  ldap_security: string;
  ldap_group_mapping: string;
  ldap_auto_provision: boolean;
}

export function LDAPSettings() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const { authEnabled } = useAuth();

  const [form, setForm] = useState<LDAPFormState>({
    ldap_server_url: '',
    ldap_bind_dn: '',
    ldap_bind_password: '',
    ldap_search_base: '',
    ldap_user_filter: '(sAMAccountName={username})',
    ldap_security: 'starttls',
    ldap_group_mapping: '',
    ldap_auto_provision: false,
  });

  // Fetch settings
  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.getSettings(),
  });

  // Fetch LDAP status
  const { data: ldapStatus } = useQuery({
    queryKey: ['ldapStatus'],
    queryFn: () => api.getLDAPStatus(),
  });

  // Fetch groups for mapping display
  const { data: groups = [] } = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.getGroups(),
  });

  // Load settings into form
  useEffect(() => {
    if (settings) {
      setForm({
        ldap_server_url: settings.ldap_server_url || '',
        ldap_bind_dn: settings.ldap_bind_dn || '',
        ldap_bind_password: '', // Never show password
        ldap_search_base: settings.ldap_search_base || '',
        ldap_user_filter: settings.ldap_user_filter || '(sAMAccountName={username})',
        ldap_security: settings.ldap_security || 'starttls',
        ldap_group_mapping: settings.ldap_group_mapping || '',
        ldap_auto_provision: settings.ldap_auto_provision ?? false,
      });
    }
  }, [settings]);

  // Save settings
  const saveMutation = useMutation({
    mutationFn: (data: Partial<AppSettings>) => api.updateSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['ldapStatus'] });
      showToast(t('settings.ldap.settingsSaved') || 'LDAP settings saved', 'success');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // Toggle LDAP
  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => api.updateSettings({ ldap_enabled: enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['ldapStatus'] });
      showToast(
        ldapStatus?.ldap_enabled
          ? (t('settings.ldap.disabled') || 'LDAP authentication disabled')
          : (t('settings.ldap.enabled') || 'LDAP authentication enabled'),
        'success'
      );
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // Test connection
  const testMutation = useMutation({
    mutationFn: () => api.testLDAP(),
    onSuccess: (data: { success: boolean; message: string }) => {
      showToast(data.message, data.success ? 'success' : 'error');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const handleSave = () => {
    if (!form.ldap_server_url) {
      showToast(t('settings.ldap.errors.serverRequired') || 'LDAP server URL is required', 'error');
      return;
    }
    if (!form.ldap_search_base) {
      showToast(t('settings.ldap.errors.searchBaseRequired') || 'Search base DN is required', 'error');
      return;
    }

    // Build the update payload — only include password if user entered one
    const update: Record<string, unknown> = {
      ldap_server_url: form.ldap_server_url,
      ldap_bind_dn: form.ldap_bind_dn,
      ldap_search_base: form.ldap_search_base,
      ldap_user_filter: form.ldap_user_filter,
      ldap_security: form.ldap_security,
      ldap_group_mapping: form.ldap_group_mapping,
      ldap_auto_provision: form.ldap_auto_provision,
    };
    if (form.ldap_bind_password) {
      update.ldap_bind_password = form.ldap_bind_password;
    }
    saveMutation.mutate(update as Partial<AppSettings>);
  };

  const handleToggle = () => {
    if (!authEnabled) {
      showToast(t('settings.ldap.errors.enableAuthFirst') || 'Enable authentication first', 'error');
      return;
    }
    if (!ldapStatus?.ldap_enabled && !ldapStatus?.ldap_configured) {
      showToast(t('settings.ldap.errors.configureLdapFirst') || 'Save LDAP settings first', 'error');
      return;
    }
    toggleMutation.mutate(!ldapStatus?.ldap_enabled);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center p-12">
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      </div>
    );
  }

  const ldapEnabled = ldapStatus?.ldap_enabled ?? false;
  const inputClasses = "w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors";

  return (
    <div className="space-y-3">
      {/* LDAP Toggle */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Shield className="w-5 h-5 text-bambu-green" />
              <h2 className="text-lg font-semibold text-white">
                {t('settings.ldap.title') || 'LDAP Authentication'}
              </h2>
            </div>
            <Button
              onClick={handleToggle}
              disabled={toggleMutation.isPending}
              variant={ldapEnabled ? 'danger' : 'primary'}
            >
              {ldapEnabled ? (
                <>
                  <Unlock className="w-4 h-4" />
                  {t('common.disable') || 'Disable'}
                </>
              ) : (
                <>
                  <Lock className="w-4 h-4" />
                  {t('common.enable') || 'Enable'}
                </>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {ldapEnabled ? (
            <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-4">
              <div className="flex items-start gap-3">
                <CheckCircle className="w-5 h-5 text-green-400 mt-0.5 flex-shrink-0" />
                <div className="space-y-2">
                  <p className="text-white font-medium">
                    {t('settings.ldap.enabledDesc') || 'LDAP authentication is enabled'}
                  </p>
                  <ul className="text-sm text-green-300 space-y-1 list-disc list-inside">
                    <li>{t('settings.ldap.feature1') || 'Users can login with LDAP credentials'}</li>
                    <li>{t('settings.ldap.feature2') || 'Local admin account remains as fallback'}</li>
                    <li>{t('settings.ldap.feature3') || 'LDAP groups are mapped to BamBuddy groups on login'}</li>
                  </ul>
                </div>
              </div>
            </div>
          ) : (
            <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="w-5 h-5 text-yellow-400 mt-0.5 flex-shrink-0" />
                <div>
                  <p className="text-white font-medium">
                    {t('settings.ldap.disabledDesc') || 'LDAP authentication is disabled'}
                  </p>
                  <p className="text-sm text-yellow-300 mt-1">
                    {t('settings.ldap.disabledHint') || 'Configure and save LDAP settings below, then enable.'}
                  </p>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* LDAP Server Configuration */}
      <Card>
        <CardHeader>
          <h2 className="text-lg font-semibold text-white">
            {t('settings.ldap.serverConfig') || 'LDAP Server Configuration'}
          </h2>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {/* Server URL + Security (side by side) */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div className="md:col-span-2">
                <label className="block text-sm font-medium text-bambu-gray mb-1">
                  {t('settings.ldap.serverUrl') || 'Server URL'}
                </label>
                <input
                  type="text"
                  className={inputClasses}
                  placeholder="ldaps://ldap.example.com:636"
                  value={form.ldap_server_url}
                  onChange={e => setForm({ ...form, ldap_server_url: e.target.value })}
                />
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.ldap.serverUrlHint') || 'Use ldaps:// for SSL or ldap:// with StartTLS'}
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-bambu-gray mb-1">
                  {t('settings.ldap.security') || 'Security'}
                </label>
                <div className="flex gap-2">
                  {(['starttls', 'ldaps'] as const).map(sec => (
                    <button
                      key={sec}
                      onClick={() => setForm({ ...form, ldap_security: sec })}
                      className={`flex-1 px-2 py-2 rounded-lg text-sm font-medium transition-colors ${
                        form.ldap_security === sec
                          ? 'bg-bambu-green text-black'
                          : 'bg-bambu-dark-secondary text-bambu-gray hover:text-white border border-bambu-dark-tertiary'
                      }`}
                    >
                      {sec === 'starttls' ? 'StartTLS' : 'LDAPS'}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.ldap.securityHint') || `Default port: ${SECURITY_PORT_MAP[form.ldap_security]}`}
                </p>
              </div>
            </div>

            {/* Bind DN + Password (side by side) */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-bambu-gray mb-1">
                  {t('settings.ldap.bindDn') || 'Bind DN (Service Account)'}
                </label>
                <input
                  type="text"
                  className={inputClasses}
                  placeholder="cn=service-account,ou=service,dc=example,dc=com"
                  value={form.ldap_bind_dn}
                  onChange={e => setForm({ ...form, ldap_bind_dn: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-bambu-gray mb-1">
                  {t('settings.ldap.bindPassword') || 'Bind Password'}
                </label>
                <input
                  type="password"
                  className={inputClasses}
                  placeholder={settings?.ldap_bind_dn ? '••••••••' : ''}
                  value={form.ldap_bind_password}
                  onChange={e => setForm({ ...form, ldap_bind_password: e.target.value })}
                />
              </div>
            </div>

            {/* Search Base + User Filter (side by side) */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-bambu-gray mb-1">
                  {t('settings.ldap.searchBase') || 'Search Base DN'}
                </label>
                <input
                  type="text"
                  className={inputClasses}
                  placeholder="ou=users,dc=example,dc=com"
                  value={form.ldap_search_base}
                  onChange={e => setForm({ ...form, ldap_search_base: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-bambu-gray mb-1">
                  {t('settings.ldap.userFilter') || 'User Search Filter'}
                </label>
                <input
                  type="text"
                  className={inputClasses}
                  placeholder="(sAMAccountName={username})"
                  value={form.ldap_user_filter}
                  onChange={e => setForm({ ...form, ldap_user_filter: e.target.value })}
                />
              </div>
            </div>

            {/* Advanced (collapsed by default) */}
            <Collapsible
              summary={
                <span className="text-sm font-medium text-bambu-gray">
                  {t('settings.ldap.advanced') || 'Advanced'}
                </span>
              }
              className="border-t border-bambu-dark-tertiary pt-3"
              summaryClassName="py-1"
            >
              <div className="space-y-3">
                {/* Auto Provision */}
                <div className="flex items-center justify-between">
                  <div>
                    <label className="block text-sm font-medium text-white">
                      {t('settings.ldap.autoProvision') || 'Auto-provision users'}
                    </label>
                    <p className="text-xs text-bambu-gray mt-0.5">
                      {t('settings.ldap.autoProvisionHint') || 'Automatically create a BamBuddy account on first LDAP login'}
                    </p>
                  </div>
                  <button
                    onClick={() => setForm({ ...form, ldap_auto_provision: !form.ldap_auto_provision })}
                    className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors flex-shrink-0 ${
                      form.ldap_auto_provision ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                    }`}
                  >
                    <span
                      className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                        form.ldap_auto_provision ? 'translate-x-6' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>

                {/* Group Mapping */}
                <div>
                  <label className="block text-sm font-medium text-bambu-gray mb-1">
                    {t('settings.ldap.groupMapping') || 'Group Mapping (JSON)'}
                  </label>
                  <textarea
                    className={`${inputClasses} font-mono text-sm`}
                    rows={4}
                    placeholder={'{\n  "CN=PrintFarm_Admins,OU=Groups,DC=example,DC=com": "Administrators",\n  "CN=PrintFarm_Users,OU=Groups,DC=example,DC=com": "Operators"\n}'}
                    value={form.ldap_group_mapping}
                    onChange={e => setForm({ ...form, ldap_group_mapping: e.target.value })}
                  />
                  <p className="text-xs text-bambu-gray mt-1">
                    {t('settings.ldap.groupMappingHint') || 'Map LDAP group DNs to BamBuddy groups. Available groups: '}{groups.map(g => g.name).join(', ')}
                  </p>
                </div>
              </div>
            </Collapsible>

            {/* Action Buttons */}
            <div className="flex gap-3 pt-2">
              <Button
                onClick={handleSave}
                disabled={saveMutation.isPending}
              >
                {saveMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <CheckCircle className="w-4 h-4" />
                )}
                {t('common.save') || 'Save'}
              </Button>
              <Button
                variant="secondary"
                onClick={() => testMutation.mutate()}
                disabled={testMutation.isPending}
              >
                {testMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
                {t('settings.ldap.testConnection') || 'Test Connection'}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
