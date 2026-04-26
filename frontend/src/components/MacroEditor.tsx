import { useState, useEffect, useCallback } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { python } from '@codemirror/lang-python';
import { oneDark } from '@codemirror/theme-one-dark';
import { autocompletion, type CompletionContext, type CompletionResult } from '@codemirror/autocomplete';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Save,
  Play,
  Trash2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Copy,
  Check,
  Clock,
  AlertCircle,
  CircleCheck,
  X,
} from 'lucide-react';
import { api, type Macro, type MacroRun } from '../api/client';
import { Button } from './Button';
import { Card, CardContent } from './Card';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';

interface MacroEditorProps {
  macroId: number | 'new';
  onSaved: (id: number) => void;
  onDeleted: () => void;
}

const TRIGGER_TYPES = ['manual', 'webhook', 'schedule'] as const;

const SYSTEM_COMMANDS = [
  { cmd: 'AMS_DRYING --ams=0 --temp=65 --duration=30', desc: 'Dry AMS filament' },
  { cmd: 'PRINTER_PAUSE', desc: 'Pause current print' },
  { cmd: 'PRINTER_RESUME', desc: 'Resume paused print' },
  { cmd: 'PRINTER_STOP', desc: 'Stop current print' },
  { cmd: 'NOTIFY --message="Done!"', desc: 'Send a notification' },
  { cmd: 'WAIT --seconds=10', desc: 'Wait N seconds (max 300)' },
  { cmd: 'WAIT_FOR_TEMP --target=200 --tolerance=5', desc: 'Wait for nozzle temp' },
];

const CONTEXT_VARS = [
  { name: 'printer.state', desc: 'Current state string (RUNNING, IDLE, …)' },
  { name: 'printer.nozzle_temp', desc: 'Nozzle temperature (°C)' },
  { name: 'printer.bed_temp', desc: 'Bed temperature (°C)' },
  { name: 'printer.progress', desc: 'Print progress 0–100' },
  { name: 'printer.layer', desc: 'Current layer number' },
  { name: 'ams', desc: 'List of AMS unit dicts' },
  { name: 'queue', desc: 'Current queue length (int)' },
];

function RunStatusIcon({ status }: { status: MacroRun['status'] }) {
  if (status === 'pending' || status === 'running')
    return <Loader2 className="w-4 h-4 animate-spin text-blue-400" />;
  if (status === 'success')
    return <CircleCheck className="w-4 h-4 text-green-400" />;
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
    ? Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000)
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
            onClick={(e) => { e.stopPropagation(); onCancel(); }}
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

export function MacroEditor({ macroId, onSaved, onDeleted }: MacroEditorProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const isNew = macroId === 'new';

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [script, setScript] = useState('');
  const [triggerType, setTriggerType] = useState<string>('manual');
  const [cronExpression, setCronExpression] = useState('');
  const [printerId, setPrinterId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<'editor' | 'settings' | 'runs'>('editor');
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showRunModal, setShowRunModal] = useState(false);
  const [runPrinterId, setRunPrinterId] = useState<number | null>(null);
  const [copiedUrl, setCopiedUrl] = useState(false);

  const { data: macro, isLoading: macroLoading } = useQuery({
    queryKey: ['macro', macroId],
    queryFn: () => api.getMacro(macroId as number),
    enabled: !isNew,
  });

  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: gcodeWhitelist = [] } = useQuery({
    queryKey: ['gcode-whitelist'],
    queryFn: api.getGcodeWhitelist,
  });

  const { data: allMacros = [] } = useQuery({
    queryKey: ['macros'],
    queryFn: api.getMacros,
  });

  const { data: runs = [] } = useQuery({
    queryKey: ['macro-runs', macroId],
    queryFn: () => api.getMacroRuns(macroId as number),
    enabled: !isNew,
    refetchInterval: (query) => {
      const runs = query.state.data as MacroRun[] | undefined;
      const active = runs?.some((r) => r.status === 'pending' || r.status === 'running');
      return active ? 1500 : 5000;
    },
  });

  // Populate form when macro loads
  useEffect(() => {
    if (macro) {
      setName(macro.name);
      setDescription(macro.description ?? '');
      setScript(macro.script);
      setTriggerType(macro.trigger_type);
      setCronExpression(macro.cron_expression ?? '');
      setPrinterId(macro.printer_id ?? null);
    }
  }, [macro]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        name,
        description: description || null,
        script,
        trigger_type: triggerType,
        cron_expression: triggerType === 'schedule' ? cronExpression : null,
        printer_id: printerId,
      };
      if (isNew) return api.createMacro(payload as Parameters<typeof api.createMacro>[0]);
      return api.updateMacro(macroId as number, payload);
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ['macros'] });
      queryClient.invalidateQueries({ queryKey: ['macro', macroId] });
      showToast('Macro saved');
      onSaved(saved.id);
    },
    onError: () => showToast('Failed to save macro', 'error'),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteMacro(macroId as number),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['macros'] });
      showToast('Macro deleted');
      onDeleted();
    },
    onError: () => showToast('Failed to delete macro', 'error'),
  });

  const runMutation = useMutation({
    mutationFn: () => api.runMacro(macroId as number, runPrinterId ?? undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['macro-runs', macroId] });
      setShowRunModal(false);
      setActiveTab('runs');
      showToast('Macro started');
    },
    onError: () => showToast('Failed to start macro', 'error'),
  });

  const cancelMutation = useMutation({
    mutationFn: (runId: number) => api.cancelMacroRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['macro-runs', macroId] });
      showToast('Run cancelled');
    },
    onError: () => showToast('Failed to cancel run', 'error'),
  });

  const webhookUrl = !isNew
    ? `${window.location.origin}/api/v1/webhook/macro/${macroId}/run`
    : null;

  const copyWebhookUrl = () => {
    if (webhookUrl) {
      navigator.clipboard.writeText(webhookUrl);
      setCopiedUrl(true);
      setTimeout(() => setCopiedUrl(false), 2000);
    }
  };

  const macroCompletions = useCallback((context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/[\w._-]*/);
    if (!word || (word.from === word.to && !context.explicit)) return null;

    const options = [
      // Context variables
      ...CONTEXT_VARS.map((v) => ({ label: v.name, type: 'variable', detail: v.desc })),
      // System commands
      ...SYSTEM_COMMANDS.map((c) => ({ label: c.cmd.split(' ')[0], type: 'keyword', detail: c.desc, apply: c.cmd })),
      // Jinja2 blocks
      { label: 'if', type: 'keyword', apply: '{% if  %}\n{% endif %}' },
      { label: 'for', type: 'keyword', apply: '{% for item in  %}\n{% endfor %}' },
      { label: 'run_macro', type: 'function', apply: 'run_macro("")' },
      // Whitelisted G-codes
      ...gcodeWhitelist.map((g) => ({ label: g, type: 'constant', detail: 'G-code' })),
      // Other macros
      ...allMacros
        .filter((m) => isNew || m.id !== macroId)
        .map((m) => ({ label: m.name, type: 'class', detail: 'macro', apply: `run_macro("${m.name}")` })),
    ];

    return { from: word.from, options };
  }, [gcodeWhitelist, allMacros, macroId, isNew]);

  const extensions = [
    python(),
    autocompletion({ override: [macroCompletions] }),
  ];

  if (!isNew && macroLoading) {
    return (
      <div className="flex items-center justify-center p-12">
        <Loader2 className="w-8 h-8 animate-spin text-bambu-text-secondary" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Macro name (used as identifier)"
          className="flex-1 text-xl font-semibold bg-transparent border-b border-bambu-dark-tertiary focus:border-bambu-green outline-none px-1 py-1 text-bambu-text"
        />
        <div className="flex gap-2 shrink-0">
          {!isNew && (
            <>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowRunModal(true)}
                disabled={runMutation.isPending}
              >
                <Play className="w-4 h-4 mr-1" />
                {t('macros.runNow')}
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => setShowDeleteConfirm(true)}
              >
                <Trash2 className="w-4 h-4" />
              </Button>
            </>
          )}
          <Button
            variant="primary"
            size="sm"
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending || !name.trim()}
          >
            {saveMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Save className="w-4 h-4 mr-1" />
            )}
            {t('macros.save')}
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-bambu-dark-tertiary gap-4">
        {(['editor', 'settings', 'runs'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`pb-2 text-sm capitalize transition-colors ${
              activeTab === tab
                ? 'border-b-2 border-bambu-green text-bambu-green'
                : 'text-bambu-text-secondary hover:text-bambu-text'
            }`}
          >
            {tab === 'runs' ? `${t('macros.runs')} (${runs.length})` : t(`macros.${tab}`)}
          </button>
        ))}
      </div>

      {/* Editor tab */}
      {activeTab === 'editor' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Script textarea */}
          <div className="lg:col-span-2">
            <CodeMirror
              value={script}
              onChange={setScript}
              extensions={extensions}
              theme={oneDark}
              minHeight="480px"
              className="rounded border border-bambu-dark-tertiary overflow-hidden text-sm"
            />
          </div>

          {/* Hints panel */}
          <div className="flex flex-col gap-4 text-sm">
            {/* Context variables */}
            <Card>
              <CardContent className="p-3">
                <div className="font-semibold text-bambu-text mb-2">{t('macros.availableVariables')}</div>
                {CONTEXT_VARS.map((v) => (
                  <div key={v.name} className="mb-1">
                    <code className="text-bambu-green text-xs">{`{{ ${v.name} }}`}</code>
                    <span className="text-bambu-text-secondary text-xs ml-2">{v.desc}</span>
                  </div>
                ))}
              </CardContent>
            </Card>

            {/* System commands */}
            <Card>
              <CardContent className="p-3">
                <div className="font-semibold text-bambu-text mb-2">{t('macros.availableCommands')}</div>
                {SYSTEM_COMMANDS.map((c) => (
                  <div key={c.cmd} className="mb-2">
                    <code className="text-yellow-400 text-xs block">{c.cmd}</code>
                    <span className="text-bambu-text-secondary text-xs">{c.desc}</span>
                  </div>
                ))}
              </CardContent>
            </Card>

            {/* Call another macro */}
            {allMacros.filter((m) => isNew || m.id !== macroId).length > 0 && (
              <Card>
                <CardContent className="p-3">
                  <div className="font-semibold text-bambu-text mb-1">{t('macros.callMacro')}</div>
                  <div className="text-bambu-text-secondary text-xs mb-2">{t('macros.callMacroSyntax')}</div>
                  {allMacros
                    .filter((m) => isNew || m.id !== macroId)
                    .map((m) => (
                      <code key={m.id} className="block text-xs text-blue-400 mb-1">
                        {`{{ run_macro("${m.name}") }}`}
                      </code>
                    ))}
                </CardContent>
              </Card>
            )}

            {/* G-code whitelist */}
            <Card>
              <CardContent className="p-3">
                <div className="font-semibold text-bambu-text mb-2">{t('macros.gcodeWhitelist')}</div>
                <div className="flex flex-wrap gap-1">
                  {gcodeWhitelist.map((g) => (
                    <span
                      key={g}
                      className="px-1.5 py-0.5 rounded bg-bambu-dark-secondary text-xs font-mono text-bambu-text-secondary"
                    >
                      {g}
                    </span>
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {/* Settings tab */}
      {activeTab === 'settings' && (
        <div className="max-w-xl flex flex-col gap-4">
          <div>
            <label className="block text-sm text-bambu-text-secondary mb-1">{t('macros.description')}</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-sm outline-none focus:border-bambu-green text-bambu-text resize-none"
            />
          </div>

          <div>
            <label className="block text-sm text-bambu-text-secondary mb-1">{t('macros.triggerType')}</label>
            <select
              value={triggerType}
              onChange={(e) => setTriggerType(e.target.value)}
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
              <label className="block text-sm text-bambu-text-secondary mb-1">
                {t('macros.cronExpression')}
              </label>
              <input
                type="text"
                value={cronExpression}
                onChange={(e) => setCronExpression(e.target.value)}
                placeholder="* * * * *  (min hour day month weekday)"
                className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-sm font-mono outline-none focus:border-bambu-green text-bambu-text"
              />
            </div>
          )}

          <div>
            <label className="block text-sm text-bambu-text-secondary mb-1">{t('macros.targetPrinter')}</label>
            <select
              value={printerId ?? ''}
              onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-sm outline-none focus:border-bambu-green text-bambu-text"
            >
              <option value="">{t('macros.anyPrinter')}</option>
              {printers.map((p: { id: number; name: string }) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          {triggerType === 'webhook' && webhookUrl && (
            <div>
              <label className="block text-sm text-bambu-text-secondary mb-1">{t('macros.webhookUrl')}</label>
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
                  {copiedUrl ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4 text-bambu-text-secondary" />}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Runs tab */}
      {activeTab === 'runs' && (
        <div>
          {runs.length === 0 ? (
            <div className="text-bambu-text-secondary text-sm py-8 text-center">{t('macros.noRuns')}</div>
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
      )}

      {/* Run modal */}
      {showRunModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <Card className="w-80">
            <CardContent className="p-4 flex flex-col gap-3">
              <div className="font-semibold text-bambu-text">{t('macros.runNow')}</div>
              <div>
                <label className="block text-sm text-bambu-text-secondary mb-1">{t('macros.selectPrinter')}</label>
                <select
                  value={runPrinterId ?? printerId ?? ''}
                  onChange={(e) => setRunPrinterId(e.target.value ? Number(e.target.value) : null)}
                  className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-sm outline-none focus:border-bambu-green text-bambu-text"
                >
                  <option value="">{t('macros.anyPrinter')}</option>
                  {printers.map((p: { id: number; name: string }) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div className="flex gap-2 justify-end">
                <Button variant="secondary" size="sm" onClick={() => setShowRunModal(false)}>
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => runMutation.mutate()}
                  disabled={runMutation.isPending}
                >
                  {runMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 mr-1" />}
                  Run
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Delete confirm */}
      {showDeleteConfirm && (
        <ConfirmModal
          title={t('macros.delete')}
          message={t('macros.confirmDelete')}
          variant="danger"
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}
    </div>
  );
}
