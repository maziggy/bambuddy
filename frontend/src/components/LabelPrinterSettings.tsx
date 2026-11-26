import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2, Printer } from 'lucide-react';
import {
  api,
  LABEL_PRINTER_MODELS,
  LABEL_PRINTER_TAPES,
  type LabelPrinterModel,
  type LabelPrinterTape,
  type LabelPrinterTestResult,
  type LabelPrinterType,
} from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';

// Brother DK continuous tape part numbers, for the tape dropdown.
const TAPE_PARTS: Record<LabelPrinterTape, string> = {
  '12': 'DK-22214',
  '29': 'DK-22210',
  '38': 'DK-22225',
  '50': 'DK-22223',
  '54': 'DK-22211',
  '62': 'DK-22205',
};

interface Draft {
  type: LabelPrinterType;
  host: string;
  model: LabelPrinterModel;
  tape: LabelPrinterTape;
}

const SAVE_DELAY_MS = 600;

/**
 * Settings > Filament > Label printer (#3612): a network label printer that
 * spool labels are sent to directly. Saved as you type, like the page's other
 * cards; "Test print" uses the values on screen, saved or not.
 */
export function LabelPrinterSettings() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const canEdit = hasPermission('settings:update');

  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const savedRef = useRef<Draft | null>(null);

  useEffect(() => {
    if (!settings || draft) return;
    const initial: Draft = {
      type: settings.label_printer_type ?? '',
      host: settings.label_printer_host ?? '',
      model: settings.label_printer_model ?? 'QL-820NWB',
      tape: settings.label_printer_tape ?? '50',
    };
    savedRef.current = initial;
    setDraft(initial);
  }, [settings, draft]);

  const saveMutation = useMutation({
    mutationFn: (data: Record<string, string>) => api.updateSettings(data),
    onSuccess: (data, vars) => {
      const patch = Object.fromEntries(Object.entries(vars).map(([k, v]) => [k.replace('label_printer_', ''), v]));
      savedRef.current = { ...(savedRef.current as Draft), ...patch };
      queryClient.setQueryData(['settings'], data);
      void queryClient.invalidateQueries({ queryKey: ['ui-flags'] });
      void queryClient.invalidateQueries({ queryKey: ['ui-settings'] });
      showToast(t('settings.toast.settingsSaved'), 'success');
    },
    onError: (error: Error) => showToast(t('settings.labelPrinter.saveFailed', 'Failed to save: {{msg}}', { msg: error.message }), 'error'),
  });

  // Debounced save of what changed since the last save. The pending payload is
  // kept in a ref so leaving the page flushes it instead of dropping it.
  const pendingRef = useRef<Record<string, string> | null>(null);
  useEffect(() => {
    if (!draft || !savedRef.current || !canEdit) return;
    const saved = savedRef.current;
    const changed: Partial<Draft> = {};
    if (draft.type !== saved.type) changed.type = draft.type;
    if (draft.host.trim() !== saved.host) changed.host = draft.host.trim();
    if (draft.model !== saved.model) changed.model = draft.model;
    if (draft.tape !== saved.tape) changed.tape = draft.tape;
    if (Object.keys(changed).length === 0) {
      pendingRef.current = null;
      return;
    }
    const payload = Object.fromEntries(Object.entries(changed).map(([k, v]) => [`label_printer_${k}`, v]));
    pendingRef.current = payload;
    const timer = window.setTimeout(() => {
      pendingRef.current = null;
      saveMutation.mutate(payload);
    }, SAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, canEdit]);

  // On unmount: send what is still waiting for the debounce (#3612).
  useEffect(
    () => () => {
      const pending = pendingRef.current;
      if (!pending) return;
      pendingRef.current = null;
      void api
        .updateSettings(pending)
        .then((data) => {
          queryClient.setQueryData(['settings'], data);
          void queryClient.invalidateQueries({ queryKey: ['ui-flags'] });
          void queryClient.invalidateQueries({ queryKey: ['ui-settings'] });
        })
        .catch(() => undefined);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const testMutation = useMutation({
    mutationFn: (d: Draft) => api.testLabelPrinter(d),
    onSuccess: ({ status }: LabelPrinterTestResult) => {
      if (status && status.errors.length > 0) {
        setResult({
          ok: false,
          text: t('settings.labelPrinter.testReportedErrors', 'Printer reports: {{errors}}', { errors: status.errors.join(', ') }),
        });
      } else if (status && status.media_width_mm) {
        setResult({
          ok: true,
          text: t('settings.labelPrinter.testAnswered', 'Test label sent. Printer answered: {{width}} mm {{media}} tape', {
            width: status.media_width_mm,
            media: status.media_type ?? '',
          }),
        });
      } else {
        setResult({ ok: true, text: t('settings.labelPrinter.testSent', 'Test label sent.') });
      }
    },
    onError: (error: Error) => setResult({ ok: false, text: error.message }),
  });

  const off = !draft || draft.type === '';
  const inputClass =
    'w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green disabled:opacity-50';
  const update = (change: Partial<Draft>) => {
    setResult(null);
    setDraft((prev) => (prev ? { ...prev, ...change } : prev));
  };

  return (
    <Card id="card-label-printer">
      <CardHeader>
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <Printer className="w-5 h-5 text-bambu-green" />
          {t('settings.labelPrinter.title', 'Label printer')}
        </h2>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-bambu-gray">
          {t(
            'settings.labelPrinter.description',
            'Print spool labels straight to a network label printer, without a PDF. Once set up, the labels dialog and the Add spool dialog offer printing.',
          )}
        </p>
        {draft && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <label className="block space-y-1">
              <span className="text-sm text-white">{t('settings.labelPrinter.type', 'Printer type')}</span>
              <select
                data-testid="label-printer-type"
                value={draft.type}
                disabled={!canEdit}
                onChange={(e) => update({ type: e.target.value as LabelPrinterType })}
                className={inputClass}
              >
                <option value="">{t('settings.labelPrinter.typeOff', 'Off')}</option>
                <option value="brother_ql">{t('settings.labelPrinter.typeBrotherQl', 'Brother QL (network)')}</option>
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-sm text-white">{t('settings.labelPrinter.host', 'Address')}</span>
              <input
                data-testid="label-printer-host"
                type="text"
                value={draft.host}
                disabled={off || !canEdit}
                onChange={(e) => update({ host: e.target.value })}
                placeholder={t('settings.labelPrinter.hostPlaceholder', '192.168.1.50 or label-printer.local')}
                className={inputClass}
              />
            </label>
            <label className="block space-y-1">
              <span className="text-sm text-white">{t('settings.labelPrinter.model', 'Model')}</span>
              <select
                data-testid="label-printer-model"
                value={draft.model}
                disabled={off || !canEdit}
                onChange={(e) => update({ model: e.target.value as LabelPrinterModel })}
                className={inputClass}
              >
                {LABEL_PRINTER_MODELS.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1 sm:col-span-2">
              <span className="text-sm text-white">{t('settings.labelPrinter.tape', 'Loaded tape')}</span>
              <select
                data-testid="label-printer-tape"
                value={draft.tape}
                disabled={off || !canEdit}
                onChange={(e) => update({ tape: e.target.value as LabelPrinterTape })}
                className={inputClass}
              >
                {LABEL_PRINTER_TAPES.map((tape) => (
                  <option key={tape} value={tape}>
                    {t('settings.labelPrinter.tapeOption', '{{width}} mm continuous ({{part}})', {
                      width: tape,
                      part: TAPE_PARTS[tape],
                    })}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            data-testid="label-printer-test"
            variant="secondary"
            disabled={off || !draft?.host.trim() || !canEdit || testMutation.isPending}
            onClick={() => {
              if (!draft) return;
              setResult(null);
              testMutation.mutate({ ...draft, host: draft.host.trim() });
            }}
          >
            {testMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Printer className="w-4 h-4" />}
            {t('settings.labelPrinter.test', 'Test print')}
          </Button>
          {result && (
            <span
              data-testid="label-printer-test-result"
              role="status"
              className={`text-sm ${result.ok ? 'text-bambu-green' : 'text-red-400'}`}
            >
              {result.text}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
