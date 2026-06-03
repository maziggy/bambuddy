import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { api, type Printer, type PrinterCreate } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import { Button } from '../Button';

interface PrinterSettingsSectionProps {
  printer: Printer;
  canUpdate: boolean;
}

export function PrinterSettingsSection({ printer, canUpdate }: PrinterSettingsSectionProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [form, setForm] = useState({
    name: printer.name,
    ip_address: printer.ip_address,
    model: printer.model || '',
    location: printer.location || '',
    auto_archive: printer.auto_archive,
  });

  const updateMutation = useMutation({
    mutationFn: (data: Partial<PrinterCreate>) => api.updatePrinter(printer.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      queryClient.invalidateQueries({ queryKey: ['printer', printer.id] });
      showToast(t('printerDetail.settingsSaved'));
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canUpdate) return;
    updateMutation.mutate({
      name: form.name,
      ip_address: form.ip_address,
      model: form.model || undefined,
      location: form.location || undefined,
      auto_archive: form.auto_archive,
    });
  };

  return (
    <div className="space-y-2 border-t border-bambu-dark-tertiary pt-3">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="text-xs font-semibold uppercase tracking-wider text-bambu-gray hover:text-white w-full text-left"
      >
        {t('printerDetail.settings')} {expanded ? '▼' : '▶'}
      </button>
      {expanded && (
        <form onSubmit={handleSave} className="space-y-3">
          <div>
            <label className="block text-xs text-bambu-gray mb-1">{t('printers.name')}</label>
            <input
              type="text"
              required
              disabled={!canUpdate}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-full px-2 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded text-white disabled:opacity-50"
            />
          </div>
          <div>
            <label className="block text-xs text-bambu-gray mb-1">{t('printers.ipAddress')}</label>
            <input
              type="text"
              required
              disabled={!canUpdate}
              value={form.ip_address}
              onChange={(e) => setForm({ ...form, ip_address: e.target.value })}
              className="w-full px-2 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded text-white disabled:opacity-50"
            />
          </div>
          <div>
            <label className="block text-xs text-bambu-gray mb-1">{t('printers.modal.locationGroup')}</label>
            <input
              type="text"
              disabled={!canUpdate}
              value={form.location}
              onChange={(e) => setForm({ ...form, location: e.target.value })}
              className="w-full px-2 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded text-white disabled:opacity-50"
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-bambu-gray">
            <input
              type="checkbox"
              disabled={!canUpdate}
              checked={form.auto_archive}
              onChange={(e) => setForm({ ...form, auto_archive: e.target.checked })}
            />
            {t('printers.modal.autoArchiveLabel')}
          </label>
          <p className="text-[10px] text-bambu-gray">
            {t('printerDetail.cameraSettingsHint')}{' '}
            <Link to="/settings?tab=general" className="text-bambu-green hover:underline">
              {t('nav.settings')}
            </Link>
          </p>
          {canUpdate && (
            <Button type="submit" size="sm" disabled={updateMutation.isPending}>
              {t('printers.modal.saveChanges')}
            </Button>
          )}
        </form>
      )}
    </div>
  );
}
