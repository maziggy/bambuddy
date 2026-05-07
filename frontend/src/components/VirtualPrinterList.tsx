import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Plus, Printer, ExternalLink, AlertTriangle, Info, FileText } from 'lucide-react';
import { multiVirtualPrinterApi, virtualPrinterApi } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { Toggle } from './Toggle';
import { useToast } from '../contexts/ToastContext';
import { VirtualPrinterCard } from './VirtualPrinterCard';
import { VirtualPrinterAddDialog } from './VirtualPrinterAddDialog';

export function VirtualPrinterList() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [showAddDialog, setShowAddDialog] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['virtual-printers'],
    queryFn: multiVirtualPrinterApi.list,
    refetchInterval: 10000,
  });

  const { data: globalSettings } = useQuery({
    queryKey: ['virtual-printer-settings'],
    queryFn: virtualPrinterApi.getSettings,
  });

  const archiveNameSourceMutation = useMutation({
    mutationFn: (source: 'metadata' | 'filename') =>
      virtualPrinterApi.updateSettings({ archive_name_source: source }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['virtual-printer-settings'] });
      showToast(t('virtualPrinter.toast.updated'));
    },
    onError: (error: Error) => {
      showToast(error.message || t('virtualPrinter.toast.failedToUpdate'), 'error');
    },
  });

  const useFilename = globalSettings?.archive_name_source === 'filename';

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
      {/* Top row - Setup Required (1 col) + How it works (2 cols) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-stretch">
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

        <Card className="lg:col-span-2">
          <CardContent className="py-3 px-4">
            <div className="flex items-start gap-2">
              <Info className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" />
              <div className="text-xs text-bambu-gray">
                <p className="text-white font-medium mb-1">{t('virtualPrinter.howItWorks.title')}</p>
                <ul className="space-y-1 list-disc list-inside">
                  <li>{t('virtualPrinter.howItWorks.step1')}</li>
                  <li>{t('virtualPrinter.howItWorks.step2')}</li>
                  <li>{t('virtualPrinter.howItWorks.step3')}</li>
                </ul>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Global VP behavior settings */}
      <Card>
        <CardContent className="py-3 px-4">
          <div className="flex items-start gap-3">
            <FileText className="w-4 h-4 text-bambu-green flex-shrink-0 mt-1" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm text-white font-medium">
                  {t('virtualPrinter.archiveNameSource.title')}
                </p>
                <div className="flex items-center gap-2">
                  <span className={`text-xs ${useFilename ? 'text-bambu-gray' : 'text-white'}`}>
                    {t('virtualPrinter.archiveNameSource.metadata')}
                  </span>
                  <Toggle
                    checked={useFilename}
                    onChange={(checked) => archiveNameSourceMutation.mutate(checked ? 'filename' : 'metadata')}
                    disabled={archiveNameSourceMutation.isPending}
                  />
                  <span className={`text-xs ${useFilename ? 'text-white' : 'text-bambu-gray'}`}>
                    {t('virtualPrinter.archiveNameSource.filename')}
                  </span>
                </div>
              </div>
              <p className="text-xs text-bambu-gray mt-1">
                {t('virtualPrinter.archiveNameSource.description')}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

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
