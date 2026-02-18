import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Loader2, Plus, Printer, ExternalLink, AlertTriangle, Info } from 'lucide-react';
import { multiVirtualPrinterApi } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { VirtualPrinterCard } from './VirtualPrinterCard';
import { VirtualPrinterAddDialog } from './VirtualPrinterAddDialog';

export function VirtualPrinterList() {
  const { t } = useTranslation();
  const [showAddDialog, setShowAddDialog] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['virtual-printers'],
    queryFn: multiVirtualPrinterApi.list,
    refetchInterval: 10000,
  });

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-8 flex justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
        </CardContent>
      </Card>
    );
  }

  const printers = data?.printers || [];
  const models = data?.models || {};

  return (
    <div className="space-y-4">
      {/* Top row - Setup Required (25%) + How it works (75%) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4 items-start">
        <Card className="border-l-4 border-l-yellow-500">
          <CardContent className="py-3 px-4">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 text-yellow-500 flex-shrink-0 mt-0.5" />
              <div className="text-xs">
                <p className="text-white font-medium">{t('virtualPrinter.setupRequired.title')}</p>
                <p className="text-bambu-gray mt-1">{t('virtualPrinter.setupRequired.description')}</p>
                <a
                  href="https://wiki.bambuddy.cool/features/virtual-printer/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 mt-2 px-3 py-1.5 bg-yellow-500/20 border border-yellow-500/50 rounded text-yellow-400 hover:bg-yellow-500/30 transition-colors text-xs"
                >
                  <ExternalLink className="w-3 h-3" />
                  {t('virtualPrinter.setupRequired.readGuide')}
                </a>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="lg:col-span-3">
          <CardContent className="py-3 px-4">
            <div className="flex items-start gap-2">
              <Info className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" />
              <div className="text-xs text-bambu-gray">
                <p className="text-white font-medium mb-1">{t('virtualPrinter.howItWorks.title')}</p>
                <p className="mb-2">{t('virtualPrinter.howItWorks.intro')}</p>
                <div className="grid grid-cols-2 xl:grid-cols-4 gap-2">
                  <div className="p-2 rounded bg-bambu-dark-secondary">
                    <p className="text-white font-medium mb-0.5">{t('virtualPrinter.mode.archive')}</p>
                    <p>{t('virtualPrinter.howItWorks.archiveDesc')}</p>
                  </div>
                  <div className="p-2 rounded bg-bambu-dark-secondary">
                    <p className="text-white font-medium mb-0.5">{t('virtualPrinter.mode.review')}</p>
                    <p>{t('virtualPrinter.howItWorks.reviewDesc')}</p>
                  </div>
                  <div className="p-2 rounded bg-bambu-dark-secondary">
                    <p className="text-white font-medium mb-0.5">{t('virtualPrinter.mode.queue')}</p>
                    <p>{t('virtualPrinter.howItWorks.queueDesc')}</p>
                  </div>
                  <div className="p-2 rounded bg-bambu-dark-secondary border border-blue-500/30">
                    <p className="text-white font-medium mb-0.5">{t('virtualPrinter.mode.proxy')}</p>
                    <p>{t('virtualPrinter.howItWorks.proxyDesc')}</p>
                  </div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Header with add button */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Printer className="w-5 h-5 text-bambu-green" />
          <h2 className="text-lg font-semibold text-white">{t('virtualPrinter.list.title')}</h2>
          <span className="text-sm text-bambu-gray">({printers.length})</span>
        </div>
        <Button variant="primary" onClick={() => setShowAddDialog(true)}>
          <Plus className="w-4 h-4 mr-1" />
          {t('virtualPrinter.list.add')}
        </Button>
      </div>

      {/* Printer cards - 3 column grid */}
      {printers.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center">
            <Printer className="w-12 h-12 text-bambu-gray mx-auto mb-3" />
            <p className="text-bambu-gray mb-4">{t('virtualPrinter.list.empty')}</p>
            <Button variant="primary" onClick={() => setShowAddDialog(true)}>
              <Plus className="w-4 h-4 mr-1" />
              {t('virtualPrinter.list.addFirst')}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 items-start">
          {printers.map((printer) => (
            <VirtualPrinterCard key={printer.id} printer={printer} models={models} />
          ))}
        </div>
      )}

      {showAddDialog && (
        <VirtualPrinterAddDialog onClose={() => setShowAddDialog(false)} />
      )}
    </div>
  );
}
