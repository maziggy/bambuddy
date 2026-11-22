import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Check, Copy, Info, Loader2, MonitorSmartphone, Printer } from 'lucide-react';
import { multiVirtualPrinterApi, type VPConnectPrinter } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { MarkdownContent } from '../components/MarkdownContent';
import { SlicerAccessCodeCard } from '../components/SlicerAccessCodeCard';
import { SlicerCaCertCard } from '../components/SlicerCaCertCard';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { copyTextToClipboard } from '../utils/clipboard';

/**
 * Connect slicer (#3602): everything a user enters in Bambu Studio or
 * OrcaSlicer to send to Bambuddy's virtual printers, for users without
 * Settings access. Their personal access code, the virtual printers they may
 * send to (the sign-in's own rule), the CA certificate and the admin's notes.
 * Reached from the account menu; its URL can be sent to a new user.
 */
export function ConnectSlicerPage() {
  const { t } = useTranslation();
  const { user, hasPermission } = useAuth();

  const { data, isLoading, isError } = useQuery({
    // Per user: what they may send to is theirs.
    queryKey: ['vp-connect-info', user?.id ?? null],
    queryFn: multiVirtualPrinterApi.getConnectInfo,
  });

  return (
    <div className="p-4 md:p-8 space-y-4 max-w-5xl">
      <div className="flex items-start gap-3">
        <MonitorSmartphone className="w-6 h-6 text-bambu-green flex-shrink-0 mt-1" />
        <div>
          <h1 className="text-2xl font-bold text-white">{t('connectSlicer.title')}</h1>
          <p className="text-sm text-bambu-gray mt-1">{t('connectSlicer.subtitle')}</p>
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
        </div>
      ) : isError || !data ? (
        <Card>
          <CardContent className="py-4 text-sm text-red-600 dark:text-red-400">{t('connectSlicer.loadFailed')}</CardContent>
        </Card>
      ) : (
        <>
          {data.instructions.trim() && (
            <Card>
              <CardContent className="py-3 px-4 text-sm text-bambu-gray-light leading-relaxed">
                <div data-testid="connect-slicer-instructions">
                  <MarkdownContent>{data.instructions}</MarkdownContent>
                </div>
              </CardContent>
            </Card>
          )}

          {!data.auth_enabled ? (
            <Card>
              <CardContent className="py-3 px-4">
                <div className="flex items-start gap-2 text-sm">
                  <Info className="w-4 h-4 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" />
                  <div>
                    <p className="text-bambu-gray">{t('connectSlicer.authOff')}</p>
                    {hasPermission('settings:read') && (
                      <Link to="/settings?tab=virtual-printer" className="inline-block mt-1 text-bambu-green hover:underline">
                        {t('connectSlicer.openSettings')}
                      </Link>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ) : (
            <>
              <ol className="text-sm text-bambu-gray list-decimal pl-5 space-y-1">
                <li>{t('connectSlicer.steps.certificate')}</li>
                <li>{t('connectSlicer.steps.addPrinter')}</li>
                <li>{t('connectSlicer.steps.accessCode')}</li>
              </ol>

              <SlicerAccessCodeCard />

              <div className="flex items-center gap-2 pt-2">
                <Printer className="w-5 h-5 text-bambu-green" />
                <h2 className="text-lg font-semibold text-white">{t('connectSlicer.printers')}</h2>
              </div>
              {data.printers.length === 0 ? (
                <Card>
                  <CardContent className="py-6 text-center text-sm text-bambu-gray">{t('connectSlicer.noPrinters')}</CardContent>
                </Card>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 items-start">
                  {data.printers.map((printer) => (
                    <ConnectPrinterCard key={printer.id} printer={printer} />
                  ))}
                </div>
              )}
            </>
          )}

          <SlicerCaCertCard />
        </>
      )}
    </div>
  );
}

function ConnectPrinterCard({ printer }: { printer: VPConnectPrinter }) {
  const { t } = useTranslation();

  const destination =
    printer.mode === 'library'
      ? t('connectSlicer.destination.library')
      : printer.mode === 'review'
        ? t('connectSlicer.destination.review')
        : printer.target_printer_name
          ? t('connectSlicer.destination.queuePrinter', { printer: printer.target_printer_name })
          : t('connectSlicer.destination.queueModel', { model: printer.model_name });

  return (
    <Card>
      <CardContent className="py-3 px-4 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-white font-medium">{printer.name}</span>
          {!printer.running && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400">
              {t('connectSlicer.notRunning')}
            </span>
          )}
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <CopyRow label={t('connectSlicer.field.address')} value={printer.address} />
          <CopyRow label={t('connectSlicer.field.serial')} value={printer.serial} />
          <dt className="text-bambu-gray">{t('connectSlicer.field.model')}</dt>
          <dd className="text-white">{printer.model_name}</dd>
        </dl>
        <p className="text-xs text-bambu-gray">{destination}</p>
        <p className="text-xs text-bambu-gray">
          {printer.target_printer_name
            ? t('connectSlicer.liveStatus', { printer: printer.target_printer_name })
            : t('connectSlicer.noLiveStatus')}
        </p>
      </CardContent>
    </Card>
  );
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (await copyTextToClipboard(value)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } else {
      showToast(t('slicerAccessCode.copyFailed'), 'error');
    }
  };

  return (
    <>
      <dt className="text-bambu-gray">{label}</dt>
      <dd className="flex items-center gap-1.5 min-w-0">
        <code className="font-mono text-white truncate">{value || '-'}</code>
        {value && (
          <button
            onClick={copy}
            aria-label={`${t('slicerAccessCode.copy')}: ${label}`}
            className="p-0.5 text-bambu-gray hover:text-white flex-shrink-0"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-bambu-green" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
        )}
      </dd>
    </>
  );
}
