import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Check, Copy, Download, Loader2, ShieldCheck } from 'lucide-react';
import { multiVirtualPrinterApi } from '../api/client';
import { Card, CardContent } from './Card';
import { useToast } from '../contexts/ToastContext';
import { copyTextToClipboard, downloadTextFile } from '../utils/clipboard';

// Operating system names, the same in every language.
const INSTALLER_PLATFORMS = [
  ['windows', 'Windows'],
  ['unix', 'Linux / macOS'],
] as const;

/**
 * The CA certificate every virtual printer presents, with the installer that
 * adds it to Bambu Studio and OrcaSlicer. The slicer imports it once to trust
 * every virtual printer. In Settings > Virtual Printer and on the Connect
 * slicer page (#3602); only the public certificate, so any signed-in user.
 */
export function SlicerCaCertCard() {
  const { t } = useTranslation();
  const { showToast } = useToast();

  // Generated on demand by the backend, never changes.
  const { data: caCert } = useQuery({
    queryKey: ['vp-ca-certificate'],
    queryFn: multiVirtualPrinterApi.getCaCertificate,
    staleTime: Infinity,
  });
  const [caCopied, setCaCopied] = useState(false);

  const handleCopyCert = async () => {
    if (!caCert) return;
    const ok = await copyTextToClipboard(caCert.pem);
    if (ok) {
      setCaCopied(true);
      showToast(t('virtualPrinter.caCert.copied'));
      setTimeout(() => setCaCopied(false), 2000);
    } else {
      showToast(t('virtualPrinter.toast.copyFailed'), 'error');
    }
  };

  const handleDownloadCert = () => {
    if (!caCert) return;
    downloadTextFile(caCert.pem, 'bambuddy-virtual-printer-ca.crt', 'application/x-pem-file');
  };

  const [installerPending, setInstallerPending] = useState<'windows' | 'unix' | null>(null);
  const handleDownloadInstaller = async (platform: 'windows' | 'unix') => {
    setInstallerPending(platform);
    try {
      const installer = await multiVirtualPrinterApi.getCaCertificateInstaller(platform);
      downloadTextFile(installer.content, installer.filename, 'application/octet-stream');
    } catch (error) {
      showToast((error as Error).message || t('virtualPrinter.caCert.installerFailed'), 'error');
    } finally {
      setInstallerPending(null);
    }
  };

  return (
    <Card>
      <CardContent className="py-3 px-4">
        <div className="flex items-start gap-3">
          <ShieldCheck className="w-4 h-4 text-bambu-green flex-shrink-0 mt-1" />
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm text-white font-medium">
                {t('virtualPrinter.caCert.title')}
              </p>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={handleCopyCert}
                  disabled={!caCert}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-bambu-dark-secondary border border-bambu-dark-tertiary text-white hover:border-bambu-gray disabled:opacity-50 transition-colors"
                >
                  {caCopied
                    ? <Check className="w-3.5 h-3.5 text-bambu-green" />
                    : <Copy className="w-3.5 h-3.5" />}
                  {caCopied ? t('virtualPrinter.caCert.copied') : t('virtualPrinter.caCert.copy')}
                </button>
                <button
                  onClick={handleDownloadCert}
                  disabled={!caCert}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-bambu-dark-secondary border border-bambu-dark-tertiary text-white hover:border-bambu-gray disabled:opacity-50 transition-colors"
                >
                  <Download className="w-3.5 h-3.5" />
                  {t('virtualPrinter.caCert.download')}
                </button>
              </div>
            </div>
            <p className="text-xs text-bambu-gray mt-1">
              {t('virtualPrinter.caCert.description')}
            </p>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <span className="text-xs text-white">{t('virtualPrinter.caCert.installerTitle')}</span>
              {INSTALLER_PLATFORMS.map(([platform, label]) => (
                <button
                  key={platform}
                  onClick={() => handleDownloadInstaller(platform)}
                  disabled={installerPending !== null}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-bambu-dark-secondary border border-bambu-dark-tertiary text-white hover:border-bambu-gray disabled:opacity-50 transition-colors"
                >
                  {installerPending === platform
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : <Download className="w-3.5 h-3.5" />}
                  {label}
                </button>
              ))}
            </div>
            <p className="text-xs text-bambu-gray mt-1">
              {t('virtualPrinter.caCert.installerHint')}
            </p>
            {caCert && (
              <p
                className="text-[10px] text-bambu-gray font-mono mt-1 truncate"
                title={caCert.fingerprint_sha256}
              >
                {t('virtualPrinter.caCert.fingerprint')}: {caCert.fingerprint_sha256}
              </p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
