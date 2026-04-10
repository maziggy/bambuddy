import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, ShieldOff, Mail, Smartphone, Key, RefreshCw, Trash2, X, Eye, EyeOff, Copy } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { Toggle } from './Toggle';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';

// ─── Small reusable code input ────────────────────────────────────────────────
function CodeInput({
  value,
  onChange,
  placeholder,
  maxLength = 6,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  maxLength?: number;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value.toUpperCase().replace(/\s/g, ''))}
      maxLength={maxLength}
      className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors font-mono tracking-widest text-center"
      placeholder={placeholder}
      autoComplete="one-time-code"
    />
  );
}

// ─── Backup codes display ─────────────────────────────────────────────────────
function BackupCodesDisplay({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(codes.join('\n'));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-4">
      <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-4">
        <p className="text-amber-400 text-sm font-medium">{t('settings.twoFa.backupCodesWarning')}</p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {codes.map((code) => (
          <code key={code} className="bg-bambu-dark-secondary rounded px-3 py-2 text-center font-mono text-sm text-white tracking-widest">
            {code}
          </code>
        ))}
      </div>
      <div className="flex gap-3">
        <Button variant="secondary" size="sm" onClick={handleCopy} className="flex items-center gap-2">
          <Copy className="w-4 h-4" />
          {copied ? t('common.copied') : t('common.copy')}
        </Button>
        <Button variant="primary" size="sm" onClick={onDone} className="flex-1">
          {t('settings.twoFa.savedCodes')}
        </Button>
      </div>
    </div>
  );
}

// ─── TOTP setup wizard ────────────────────────────────────────────────────────
function TOTPSetupWizard({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [step, setStep] = useState<'qr' | 'confirm' | 'backup'>('qr');
  const [code, setCode] = useState('');
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
  const [showSecret, setShowSecret] = useState(false);

  const { data: setupData, isLoading } = useQuery({
    queryKey: ['totp-setup'],
    queryFn: () => api.setupTOTP(),
    staleTime: Infinity,
  });

  const enableMutation = useMutation({
    mutationFn: (c: string) => api.enableTOTP(c),
    onSuccess: (data) => {
      setBackupCodes(data.backup_codes);
      setStep('backup');
      queryClient.invalidateQueries({ queryKey: ['2fa-status'] });
    },
    onError: () => showToast(t('settings.twoFa.invalidCode'), 'error'),
  });

  if (isLoading || !setupData) {
    return (
      <div className="flex items-center justify-center py-8">
        <RefreshCw className="w-6 h-6 animate-spin text-bambu-green" />
      </div>
    );
  }

  if (step === 'qr') {
    return (
      <div className="space-y-4">
        <p className="text-bambu-gray-light text-sm">{t('settings.twoFa.setupInstructions')}</p>
        <div className="flex justify-center">
          <img
            src={`data:image/png;base64,${setupData.qr_code_b64}`}
            alt="TOTP QR Code"
            className="w-48 h-48 rounded-lg"
          />
        </div>
        <div>
          <p className="text-xs text-bambu-gray mb-1">{t('settings.twoFa.manualEntry')}</p>
          <div className="flex items-center gap-2 bg-bambu-dark-secondary rounded-lg px-3 py-2">
            <code className="text-white text-xs font-mono flex-1 break-all">
              {showSecret ? setupData.secret : '••••••••••••••••'}
            </code>
            <button onClick={() => setShowSecret(!showSecret)} className="text-bambu-gray hover:text-white">
              {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
            <button
              onClick={() => { navigator.clipboard.writeText(setupData.secret); }}
              className="text-bambu-gray hover:text-white"
            >
              <Copy className="w-4 h-4" />
            </button>
          </div>
        </div>
        <Button variant="primary" className="w-full" onClick={() => setStep('confirm')}>
          {t('settings.twoFa.scannedContinue')}
        </Button>
      </div>
    );
  }

  if (step === 'confirm') {
    return (
      <div className="space-y-4">
        <p className="text-bambu-gray-light text-sm">{t('settings.twoFa.enterCodeToConfirm')}</p>
        <CodeInput value={code} onChange={setCode} placeholder="000000" />
        <div className="flex gap-3">
          <Button variant="secondary" onClick={() => setStep('qr')} className="flex-1">
            {t('common.back')}
          </Button>
          <Button
            variant="primary"
            className="flex-1"
            disabled={code.length !== 6 || enableMutation.isPending}
            onClick={() => enableMutation.mutate(code)}
          >
            {enableMutation.isPending ? t('common.saving') : t('settings.twoFa.activate')}
          </Button>
        </div>
      </div>
    );
  }

  // step === 'backup'
  return (
    <div className="space-y-4">
      <h3 className="text-white font-medium">{t('settings.twoFa.backupCodesTitle')}</h3>
      <BackupCodesDisplay codes={backupCodes} onDone={onDone} />
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export function TwoFactorSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { user } = useAuth();

  const [showTOTPSetup, setShowTOTPSetup] = useState(false);
  const [showDisableTOTP, setShowDisableTOTP] = useState(false);
  const [showRegenBackup, setShowRegenBackup] = useState(false);
  const [disableCode, setDisableCode] = useState('');
  const [regenCode, setRegenCode] = useState('');
  const [newBackupCodes, setNewBackupCodes] = useState<string[] | null>(null);

  // Email OTP enable: two-step proof-of-possession flow
  const [emailSetupToken, setEmailSetupToken] = useState<string | null>(null);
  const [emailSetupCode, setEmailSetupCode] = useState('');

  // Email OTP disable: requires account password
  const [showDisableEmail, setShowDisableEmail] = useState(false);
  const [emailDisablePassword, setEmailDisablePassword] = useState('');
  const [showEmailDisablePassword, setShowEmailDisablePassword] = useState(false);

  const { data: status, isLoading } = useQuery({
    queryKey: ['2fa-status'],
    queryFn: () => api.get2FAStatus(),
  });

  const { data: oidcLinks } = useQuery({
    queryKey: ['oidc-links'],
    queryFn: () => api.getOIDCLinks(),
  });

  // Step 1: request verification code (proof of possession)
  const enableEmailRequestMutation = useMutation({
    mutationFn: () => api.enableEmailOTP(),
    onSuccess: (data: { message: string; setup_token: string }) => {
      setEmailSetupToken(data.setup_token);
      showToast(data.message, 'success');
    },
    onError: (e: Error) => {
      const msg = e.message ?? '';
      if (msg.toLowerCase().includes('smtp')) {
        showToast(t('settings.twoFa.smtpRequired'), 'error');
      } else {
        showToast(msg, 'error');
      }
    },
  });

  // Step 2: confirm with the code received by email
  const enableEmailConfirmMutation = useMutation({
    mutationFn: () => api.confirmEnableEmailOTP(emailSetupToken!, emailSetupCode),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['2fa-status'] });
      setEmailSetupToken(null);
      setEmailSetupCode('');
      showToast(t('settings.twoFa.emailOtpEnabled'), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const disableEmailMutation = useMutation({
    mutationFn: (password: string) => api.disableEmailOTP(password),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['2fa-status'] });
      setShowDisableEmail(false);
      setEmailDisablePassword('');
      showToast(t('settings.twoFa.emailOtpDisabled'), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const disableTOTPMutation = useMutation({
    mutationFn: (code: string) => api.disableTOTP(code),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['2fa-status'] });
      setShowDisableTOTP(false);
      setDisableCode('');
      showToast(t('settings.twoFa.totpDisabled'), 'success');
    },
    onError: () => showToast(t('settings.twoFa.invalidCode'), 'error'),
  });

  const regenMutation = useMutation({
    mutationFn: (code: string) => api.regenerateBackupCodes(code),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['2fa-status'] });
      setShowRegenBackup(false);
      setRegenCode('');
      setNewBackupCodes(data.backup_codes);
    },
    onError: () => showToast(t('settings.twoFa.invalidCode'), 'error'),
  });

  const unlinkOIDCMutation = useMutation({
    mutationFn: (providerId: number) => api.deleteOIDCLink(providerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['oidc-links'] });
      showToast(t('settings.twoFa.oidcUnlinked'), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <RefreshCw className="w-6 h-6 animate-spin text-bambu-green" />
      </div>
    );
  }

  const hasEmail = !!user?.email;

  return (
    <div className="space-y-6">
      {/* ── TOTP ─────────────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 rounded-full flex items-center justify-center ${status?.totp_enabled ? 'bg-green-500/20' : 'bg-gray-500/20'}`}>
              <Smartphone className={`w-5 h-5 ${status?.totp_enabled ? 'text-green-400' : 'text-gray-400'}`} />
            </div>
            <div>
              <h3 className="text-white font-semibold">{t('settings.twoFa.totpTitle')}</h3>
              <p className="text-bambu-gray text-sm">{t('settings.twoFa.totpDesc')}</p>
            </div>
            <div className="ml-auto">
              {status?.totp_enabled ? (
                <span className="flex items-center gap-1 text-green-400 text-sm font-medium">
                  <ShieldCheck className="w-4 h-4" /> {t('common.enabled')}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-bambu-gray text-sm">
                  <ShieldOff className="w-4 h-4" /> {t('common.disabled')}
                </span>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {/* TOTP Setup wizard */}
          {showTOTPSetup ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-white font-medium">{t('settings.twoFa.setupAuthApp')}</h4>
                <button onClick={() => { setShowTOTPSetup(false); queryClient.removeQueries({ queryKey: ['totp-setup'] }); }} className="text-bambu-gray hover:text-white">
                  <X className="w-5 h-5" />
                </button>
              </div>
              <TOTPSetupWizard onDone={() => { setShowTOTPSetup(false); queryClient.removeQueries({ queryKey: ['totp-setup'] }); }} />
            </div>
          ) : showDisableTOTP ? (
            <div className="space-y-4">
              <p className="text-bambu-gray-light text-sm">{t('settings.twoFa.disableConfirmHint')}</p>
              <CodeInput value={disableCode} onChange={setDisableCode} placeholder="000000 or XXXXXXXX" maxLength={8} />
              <div className="flex gap-3">
                <Button variant="secondary" onClick={() => { setShowDisableTOTP(false); setDisableCode(''); }} className="flex-1">
                  {t('common.cancel')}
                </Button>
                <Button
                  variant="danger"
                  className="flex-1"
                  disabled={disableCode.length < 6 || disableTOTPMutation.isPending}
                  onClick={() => disableTOTPMutation.mutate(disableCode)}
                >
                  {disableTOTPMutation.isPending ? t('common.saving') : t('settings.twoFa.disableTotp')}
                </Button>
              </div>
            </div>
          ) : showRegenBackup ? (
            <div className="space-y-4">
              <p className="text-bambu-gray-light text-sm">{t('settings.twoFa.regenBackupHint')}</p>
              <CodeInput value={regenCode} onChange={setRegenCode} placeholder="000000" />
              <div className="flex gap-3">
                <Button variant="secondary" onClick={() => { setShowRegenBackup(false); setRegenCode(''); }} className="flex-1">
                  {t('common.cancel')}
                </Button>
                <Button
                  variant="primary"
                  className="flex-1"
                  disabled={regenCode.length !== 6 || regenMutation.isPending}
                  onClick={() => regenMutation.mutate(regenCode)}
                >
                  {regenMutation.isPending ? t('common.saving') : t('settings.twoFa.regenBackup')}
                </Button>
              </div>
            </div>
          ) : newBackupCodes ? (
            <div className="space-y-4">
              <h4 className="text-white font-medium">{t('settings.twoFa.newBackupCodes')}</h4>
              <BackupCodesDisplay codes={newBackupCodes} onDone={() => setNewBackupCodes(null)} />
            </div>
          ) : (
            <div className="space-y-3">
              {!status?.totp_enabled ? (
                <Button variant="primary" onClick={() => setShowTOTPSetup(true)} className="flex items-center gap-2">
                  <Smartphone className="w-4 h-4" />
                  {t('settings.twoFa.setupTotp')}
                </Button>
              ) : (
                <div className="flex flex-wrap gap-3">
                  <div className="flex items-center gap-2 text-sm text-bambu-gray-light">
                    <Key className="w-4 h-4" />
                    {t('settings.twoFa.backupCodesRemaining', { count: status.backup_codes_remaining })}
                  </div>
                  <Button variant="secondary" size="sm" onClick={() => setShowRegenBackup(true)} className="flex items-center gap-2">
                    <RefreshCw className="w-4 h-4" />
                    {t('settings.twoFa.regenBackup')}
                  </Button>
                  <Button variant="danger" size="sm" onClick={() => setShowDisableTOTP(true)} className="flex items-center gap-2">
                    <Trash2 className="w-4 h-4" />
                    {t('settings.twoFa.disableTotp')}
                  </Button>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Email OTP ─────────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className={`w-10 h-10 rounded-full flex items-center justify-center ${status?.email_otp_enabled ? 'bg-green-500/20' : 'bg-gray-500/20'}`}>
              <Mail className={`w-5 h-5 ${status?.email_otp_enabled ? 'text-green-400' : 'text-gray-400'}`} />
            </div>
            <div className="flex-1">
              <h3 className="text-white font-semibold">{t('settings.twoFa.emailOtpTitle')}</h3>
              <p className="text-bambu-gray text-sm">
                {hasEmail
                  ? t('settings.twoFa.emailOtpDesc', { email: user?.email })
                  : t('settings.twoFa.emailOtpNoEmail')}
              </p>
            </div>
            {/* Show status badge; enable/disable handled in CardContent */}
            <div className="ml-auto">
              {status?.email_otp_enabled ? (
                <span className="flex items-center gap-1 text-green-400 text-sm font-medium">
                  <ShieldCheck className="w-4 h-4" /> {t('common.enabled')}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-bambu-gray text-sm">
                  <ShieldOff className="w-4 h-4" /> {t('common.disabled')}
                </span>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {!hasEmail ? (
            <p className="text-amber-400 text-sm">{t('settings.twoFa.addEmailFirst')}</p>
          ) : emailSetupToken ? (
            /* Step 2: enter the code that was sent to the email */
            <div className="space-y-4">
              <p className="text-bambu-gray-light text-sm">{t('settings.twoFa.emailSetupEnterCode')}</p>
              <CodeInput value={emailSetupCode} onChange={setEmailSetupCode} placeholder="000000" />
              <div className="flex gap-3">
                <Button
                  variant="secondary"
                  onClick={() => { setEmailSetupToken(null); setEmailSetupCode(''); }}
                  className="flex-1"
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  variant="primary"
                  className="flex-1"
                  disabled={emailSetupCode.length !== 6 || enableEmailConfirmMutation.isPending}
                  onClick={() => enableEmailConfirmMutation.mutate()}
                >
                  {enableEmailConfirmMutation.isPending ? t('common.saving') : t('settings.twoFa.verifyAndEnable')}
                </Button>
              </div>
            </div>
          ) : showDisableEmail ? (
            /* Disable: require account password for re-auth */
            <div className="space-y-4">
              <p className="text-bambu-gray-light text-sm">{t('settings.twoFa.emailDisablePasswordHint')}</p>
              <div className="relative">
                <input
                  type={showEmailDisablePassword ? 'text' : 'password'}
                  value={emailDisablePassword}
                  onChange={(e) => setEmailDisablePassword(e.target.value)}
                  placeholder={t('settings.twoFa.passwordPlaceholder')}
                  className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                />
                <button
                  type="button"
                  onClick={() => setShowEmailDisablePassword(!showEmailDisablePassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
                >
                  {showEmailDisablePassword ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                </button>
              </div>
              <div className="flex gap-3">
                <Button
                  variant="secondary"
                  onClick={() => { setShowDisableEmail(false); setEmailDisablePassword(''); }}
                  className="flex-1"
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  variant="danger"
                  className="flex-1"
                  disabled={!emailDisablePassword || disableEmailMutation.isPending}
                  onClick={() => disableEmailMutation.mutate(emailDisablePassword)}
                >
                  {disableEmailMutation.isPending ? t('common.saving') : t('settings.twoFa.disableEmailOtp')}
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex gap-3">
              {!status?.email_otp_enabled ? (
                <Button
                  variant="primary"
                  disabled={!hasEmail || enableEmailRequestMutation.isPending}
                  onClick={() => enableEmailRequestMutation.mutate()}
                  className="flex items-center gap-2"
                >
                  <Mail className="w-4 h-4" />
                  {enableEmailRequestMutation.isPending ? t('common.saving') : t('settings.twoFa.enableEmailOtp')}
                </Button>
              ) : (
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => setShowDisableEmail(true)}
                  className="flex items-center gap-2"
                >
                  <Trash2 className="w-4 h-4" />
                  {t('settings.twoFa.disableEmailOtp')}
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Linked SSO accounts ───────────────────────────────────────────── */}
      {oidcLinks && oidcLinks.length > 0 && (
        <Card>
          <CardHeader>
            <h3 className="text-white font-semibold">{t('settings.twoFa.linkedAccounts')}</h3>
            <p className="text-bambu-gray text-sm">{t('settings.twoFa.linkedAccountsDesc')}</p>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {oidcLinks.map((link) => (
                <div key={link.id} className="flex items-center justify-between py-2 border-b border-bambu-dark-tertiary last:border-0">
                  <div>
                    <p className="text-white text-sm font-medium">{link.provider_name}</p>
                    {link.provider_email && (
                      <p className="text-bambu-gray text-xs">{link.provider_email}</p>
                    )}
                  </div>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => unlinkOIDCMutation.mutate(link.provider_id)}
                    disabled={unlinkOIDCMutation.isPending}
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
