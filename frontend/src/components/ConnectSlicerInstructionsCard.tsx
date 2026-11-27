import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Loader2, MonitorSmartphone } from 'lucide-react';
import { api } from '../api/client';
import { Card, CardContent } from './Card';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';

const MAX_LENGTH = 4000;

/**
 * Settings > Virtual Printer: the link to the Connect slicer page and the
 * instructions it shows every user, in markdown (#3602).
 */
export function ConnectSlicerInstructionsCard() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const { authEnabled, hasPermission } = useAuth();
  const canEdit = !authEnabled || hasPermission('settings:update');

  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings });
  const saved = settings?.virtual_printer_connect_instructions ?? '';
  const [draft, setDraft] = useState(saved);
  useEffect(() => setDraft(saved), [saved]);

  const save = useMutation({
    mutationFn: (value: string) => api.updateSettings({ virtual_printer_connect_instructions: value }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['vp-connect-info'] });
      showToast(t('connectSlicer.instructions.saved'));
    },
    onError: (error: Error) => showToast(error.message || t('connectSlicer.instructions.saveFailed'), 'error'),
  });

  return (
    <Card>
      <CardContent className="py-3 px-4">
        <div className="flex items-start gap-3">
          <MonitorSmartphone className="w-4 h-4 text-bambu-green flex-shrink-0 mt-1" />
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-white font-medium">{t('connectSlicer.title')}</p>
              <Link
                to="/connect-slicer"
                className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-bambu-dark-secondary border border-bambu-dark-tertiary text-white hover:border-bambu-gray transition-colors"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                {t('connectSlicer.instructions.openPage')}
              </Link>
            </div>
            <p className="text-xs text-bambu-gray mt-1">{t('connectSlicer.instructions.description')}</p>
            <label htmlFor="connect-slicer-instructions" className="block text-xs text-white mt-2 mb-1">
              {t('connectSlicer.instructions.label')}
            </label>
            <textarea
              id="connect-slicer-instructions"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              maxLength={MAX_LENGTH}
              rows={3}
              disabled={!canEdit}
              placeholder={t('connectSlicer.instructions.placeholder')}
              className="w-full text-sm text-white bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 font-mono focus:border-bambu-green focus:outline-none disabled:opacity-50"
            />
            <div className="flex items-center justify-between gap-3 mt-1">
              <p className="text-[10px] text-bambu-gray">{t('connectSlicer.instructions.markdownHint')}</p>
              {canEdit && (
                <button
                  onClick={() => save.mutate(draft)}
                  disabled={save.isPending || draft === saved}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-bambu-green text-white hover:bg-bambu-green/80 disabled:opacity-50 transition-colors"
                >
                  {save.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {t('common.save')}
                </button>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
