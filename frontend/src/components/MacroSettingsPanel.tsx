import { useState } from 'react';
import {
  ArrowLeft,
  Loader2,
  AlertCircle,
  CircleCheck,
  Clock,
  ChevronDown,
  ChevronRight,
  X,
  Copy,
  Check,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type Macro, type MacroRun } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

const TRIGGER_TYPES = ['manual', 'webhook', 'schedule'] as const;

function RunStatusIcon({ status }: { status: MacroRun['status'] }) {
  if (status === 'pending' || status === 'running')
    return <Loader2 className="w-4 h-4 animate-spin text-blue-400" />;
  if (status === 'success') return <CircleCheck className="w-4 h-4 text-green-400" />;
  return <AlertCircle className="w-4 h-4 text-red-400" />;
}

function RunRow({
  run,
  isExpanded,
  onToggle,
  onCancel,
}: {
  run: MacroRun;
  isExpanded: boolean;
  onToggle: () => void;
  onCancel?: () => void;
}) {
  const isActive = run.status === 'pending' || run.status === 'running';
  const duration = run.finished_at
    ? Math.round(
        (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000
      )
    : null;

  return (
    <div className="border border-bambu-dark-tertiary rounded mb-2">
      <div className="flex items-center gap-2 px-3 py-2">
        <button className="flex items-center gap-2 flex-1 text-left" onClick={onToggle}>
          <RunStatusIcon status={run.status} />
          <span className="text-sm flex-1">
            Run #{run.id} · <span className="capitalize">{run.trigger}</span>
          </span>
          <span className="text-xs text-bambu-text-secondary">
            {new Date(run.started_at).toLocaleString()}
          </span>
          {duration !== null && (
            <span className="text-xs text-bambu-text-secondary ml-2">{duration}s</span>
          )}
          {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>
        {isActive && onCancel && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onCancel();
            }}
            className="ml-1 p-1 rounded text-red-400 hover:bg-red-900/30 transition-colors"
            title="Cancel run"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
      {isExpanded && (
        <pre className="bg-zinc-900 rounded-b p-3 text-xs font-mono overflow-auto max-h-64 whitespace-pre-wrap text-bambu-text-secondary border-t border-bambu-dark-tertiary">
          {run.log || '(no output)'}
        </pre>
      )}
    </div>
  );
}

interface MacroSettingsPanelProps {
  macro: Macro;
  onBack: () => void;
}

export function MacroSettingsPanel({ macro, onBack }: MacroSettingsPanelProps) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const [triggerType, setTriggerType] = useState(macro.trigger_type);
  const [cronExpression, setCronExpression] = useState(macro.cron_expression ?? '');
  const [printerId, setPrinterId] = useState<number | null>(macro.printer_id);
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null);
  const [copiedUrl, setCopiedUrl] = useState(false);

  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: runs = [] } = useQuery({
    queryKey: ['macro-runs', macro.id],
    queryFn: () => api.getMacroRuns(macro.id),
    refetchInterval: (query) => {
      const r = query.state.data as MacroRun[] | undefined;
      return r?.some((run) => run.status === 'pending' || run.status === 'running') ? 1500 : 10000;
    },
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateMacro(macro.id, {
        trigger_type: triggerType,
        cron_expression: triggerType === 'schedule' ? cronExpression : undefined,
        printer_id: printerId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['macros'] });
      showToast('Settings saved');
    },
    onError: () => showToast('Failed to save settings', 'error'),
  });

  const cancelMutation = useMutation({
    mutationFn: (runId: number) => api.cancelMacroRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['macro-runs', macro.id] });
      showToast('Run cancelled');
    },
    onError: () => showToast('Failed to cancel run', 'error'),
  });

  const webhookUrl =
    triggerType === 'webhook'
      ? `${window.location.origin}/api/v1/webhook/macro/${macro.id}/run`
      : null;

  function copyWebhookUrl() {
    if (webhookUrl) {
      navigator.clipboard.writeText(webhookUrl);
      setCopiedUrl(true);
      setTimeout(() => setCopiedUrl(false), 2000);
    }
  }

  const dirty =
    triggerType !== macro.trigger_type ||
    (triggerType === 'schedule' && cronExpression !== (macro.cron_expression ?? '')) ||
    printerId !== macro.printer_id;

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-bambu-dark-tertiary shrink-0">
        <button
          onClick={onBack}
          className="p-1.5 rounded hover:bg-bambu-dark-secondary text-bambu-text-secondary hover:text-bambu-text transition-colors"
          title="Back"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-bambu-text">{macro.name}</div>
          {macro.description && (
            <div className="text-xs text-bambu-text-secondary">{macro.description}</div>
          )}
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending || !dirty}
        >
          {saveMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Save'}
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-6 max-w-xl">
        {/* Trigger */}
        <div>
          <label className="block text-sm text-bambu-text-secondary mb-1">Trigger type</label>
          <select
            value={triggerType}
            onChange={(e) => setTriggerType(e.target.value as Macro['trigger_type'])}
            className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-sm outline-none focus:border-bambu-green text-bambu-text"
          >
            {TRIGGER_TYPES.map((t) => (
              <option key={t} value={t} className="capitalize">
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </option>
            ))}
          </select>
        </div>

        {triggerType === 'schedule' && (
          <div>
            <label className="block text-sm text-bambu-text-secondary mb-1">Cron expression</label>
            <input
              type="text"
              value={cronExpression}
              onChange={(e) => setCronExpression(e.target.value)}
              placeholder="* * * * *  (min hour day month weekday)"
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-sm font-mono outline-none focus:border-bambu-green text-bambu-text"
            />
          </div>
        )}

        {triggerType === 'webhook' && webhookUrl && (
          <div>
            <label className="block text-sm text-bambu-text-secondary mb-1">Webhook URL</label>
            <div className="flex gap-2 items-center">
              <input
                readOnly
                value={webhookUrl}
                className="flex-1 bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-xs font-mono text-bambu-text-secondary"
              />
              <button
                onClick={copyWebhookUrl}
                className="p-2 rounded hover:bg-bambu-dark-secondary transition-colors"
                title="Copy URL"
              >
                {copiedUrl ? (
                  <Check className="w-4 h-4 text-green-400" />
                ) : (
                  <Copy className="w-4 h-4 text-bambu-text-secondary" />
                )}
              </button>
            </div>
          </div>
        )}

        {/* Target printer */}
        <div>
          <label className="block text-sm text-bambu-text-secondary mb-1">Target printer</label>
          <select
            value={printerId ?? ''}
            onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
            className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-sm outline-none focus:border-bambu-green text-bambu-text"
          >
            <option value="">Any printer</option>
            {printers.map((p: { id: number; name: string }) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        {/* Run history */}
        <div>
          <div className="text-sm text-bambu-text-secondary mb-2 font-semibold">
            Run history ({runs.length})
          </div>
          {runs.length === 0 ? (
            <div className="flex items-center gap-2 text-bambu-text-secondary text-sm py-4">
              <Clock className="w-4 h-4" />
              <span>Never run</span>
            </div>
          ) : (
            runs.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                isExpanded={expandedRunId === run.id}
                onToggle={() => setExpandedRunId(expandedRunId === run.id ? null : run.id)}
                onCancel={() => cancelMutation.mutate(run.id)}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
