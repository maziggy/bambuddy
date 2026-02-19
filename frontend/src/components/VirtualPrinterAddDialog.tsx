import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, ChevronDown, ArrowRightLeft } from 'lucide-react';
import { api, multiVirtualPrinterApi } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

type Mode = 'immediate' | 'review' | 'print_queue' | 'proxy';

const MODE_LABELS: Record<string, string> = {
  immediate: 'archive',
  review: 'review',
  print_queue: 'queue',
  proxy: 'proxy',
};

interface VirtualPrinterAddDialogProps {
  onClose: () => void;
}

export function VirtualPrinterAddDialog({ onClose }: VirtualPrinterAddDialogProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [name, setName] = useState('');
  const [mode, setMode] = useState<Mode>('immediate');
  const [targetPrinterId, setTargetPrinterId] = useState<number | null>(null);

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      multiVirtualPrinterApi.create({
        name: name.trim() || 'Bambuddy',
        mode,
        target_printer_id: mode === 'proxy' ? (targetPrinterId ?? undefined) : undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['virtual-printers'] });
      showToast(t('virtualPrinter.toast.created'));
      onClose();
    },
    onError: (error: Error) => {
      showToast(error.message || t('virtualPrinter.toast.failedToCreate'), 'error');
    },
  });

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <Card
        className="w-full max-w-md"
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
      >
        <CardContent className="p-6 space-y-4">
          <h3 className="text-lg font-semibold text-white">{t('virtualPrinter.addDialog.title')}</h3>

          {/* Name */}
          <div>
            <label className="text-sm text-white font-medium block mb-1">{t('virtualPrinter.addDialog.name')}</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Bambuddy"
              className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white text-sm placeholder-bambu-gray"
              autoFocus
            />
          </div>

          {/* Mode */}
          <div>
            <label className="text-sm text-white font-medium block mb-1">{t('virtualPrinter.mode.title')}</label>
            <div className="grid grid-cols-2 gap-2">
              {(['immediate', 'review', 'print_queue', 'proxy'] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={`p-2 rounded-lg border text-left transition-colors ${
                    mode === m
                      ? m === 'proxy'
                        ? 'border-blue-500 bg-blue-500/10'
                        : 'border-bambu-green bg-bambu-green/10'
                      : 'border-bambu-dark-tertiary hover:border-bambu-gray'
                  }`}
                >
                  <div className="flex items-center gap-1.5 text-white text-xs font-medium">
                    {m === 'proxy' && <ArrowRightLeft className="w-3 h-3" />}
                    {t(`virtualPrinter.mode.${MODE_LABELS[m]}`)}
                  </div>
                  <div className="text-[10px] text-bambu-gray">
                    {t(`virtualPrinter.mode.${MODE_LABELS[m]}Desc`)}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Target Printer - only for proxy mode */}
          {mode === 'proxy' && (
            <div>
              <label className="text-sm text-white font-medium block mb-1">{t('virtualPrinter.targetPrinter.title')}</label>
              <div className="relative">
                <select
                  value={targetPrinterId ?? ''}
                  onChange={(e) => {
                    const id = parseInt(e.target.value, 10);
                    setTargetPrinterId(isNaN(id) ? null : id);
                  }}
                  className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white text-sm appearance-none cursor-pointer pr-10"
                >
                  <option value="">{t('virtualPrinter.targetPrinter.placeholder')}</option>
                  {printers?.map((p) => (
                    <option key={p.id} value={p.id}>{p.name} ({p.ip_address})</option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
              </div>
            </div>
          )}

          <p className="text-xs text-bambu-gray">
            {t('virtualPrinter.addDialog.hint')}
          </p>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <Button variant="secondary" onClick={onClose} className="flex-1" disabled={createMutation.isPending}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="primary"
              onClick={() => createMutation.mutate()}
              className="flex-1"
              disabled={createMutation.isPending}
            >
              {createMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                t('virtualPrinter.addDialog.create')
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
