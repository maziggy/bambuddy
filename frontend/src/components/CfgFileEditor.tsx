import { useState, useEffect, useCallback, useRef } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { oneDark } from '@codemirror/theme-one-dark';
import { python } from '@codemirror/lang-python';
import { autocompletion, type CompletionContext, type CompletionResult } from '@codemirror/autocomplete';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Save, ArrowLeft, Loader2, AlertCircle, Download, Upload } from 'lucide-react';
import { api, type MacroCfgFile } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

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
  { name: 'printer.state', desc: 'Current state string' },
  { name: 'printer.nozzle_temp', desc: 'Nozzle temperature (°C)' },
  { name: 'printer.bed_temp', desc: 'Bed temperature (°C)' },
  { name: 'printer.progress', desc: 'Print progress 0–100' },
  { name: 'printer.layer', desc: 'Current layer number' },
  { name: 'ams', desc: 'List of AMS unit dicts' },
];

interface CfgFileEditorProps {
  file: MacroCfgFile;
  onBack: () => void;
}

export function CfgFileEditor({ file, onBack }: CfgFileEditorProps) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [content, setContent] = useState('');
  const [dirty, setDirty] = useState(false);
  const uploadRef = useRef<HTMLInputElement>(null);

  function handleDownload() {
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = file.file_path.split('/').pop() ?? `${file.name}.cfg`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setContent(ev.target?.result as string);
      setDirty(true);
    };
    reader.readAsText(f);
    e.target.value = '';
  }

  const [contentLoading, setContentLoading] = useState(true);

  useEffect(() => {
    setContentLoading(true);
    api.getMacroCfgFileContent(file.id).then((data) => {
      setContent(data.content);
      setDirty(false);
    }).finally(() => setContentLoading(false));
  }, [file.id]);

  const { data: gcodeWhitelist = [] } = useQuery({
    queryKey: ['gcode-whitelist'],
    queryFn: api.getGcodeWhitelist,
  });

  const { data: allMacros = [] } = useQuery({
    queryKey: ['macros'],
    queryFn: () => api.getMacros(),
  });

  const saveMutation = useMutation({
    mutationFn: () => api.saveMacroCfgFile(file.id, content),
    onSuccess: () => {
      showToast('File saved');
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ['macros'] });
      queryClient.invalidateQueries({ queryKey: ['macro-cfg-files'] });
    },
    onError: () => showToast('Failed to save file', 'error'),
  });

  const completions = useCallback(
    (context: CompletionContext): CompletionResult | null => {
      const word = context.matchBefore(/[\w._-]*/);
      if (!word || (word.from === word.to && !context.explicit)) return null;

      const options = [
        ...CONTEXT_VARS.map((v) => ({ label: v.name, type: 'variable', detail: v.desc })),
        ...SYSTEM_COMMANDS.map((c) => ({
          label: c.cmd.split(' ')[0],
          type: 'keyword',
          detail: c.desc,
          apply: c.cmd,
        })),
        { label: 'if', type: 'keyword', apply: '{% if  %}\n{% endif %}' },
        { label: 'for', type: 'keyword', apply: '{% for item in  %}\n{% endfor %}' },
        { label: 'run_macro', type: 'function', apply: 'run_macro("")' },
        ...gcodeWhitelist.map((g) => ({ label: g, type: 'constant', detail: 'G-code' })),
        ...allMacros.map((m) => ({
          label: m.name,
          type: 'class',
          detail: 'macro',
          apply: `run_macro("${m.name}")`,
        })),
      ];

      return { from: word.from, options };
    },
    [gcodeWhitelist, allMacros]
  );

  const extensions = [python(), autocompletion({ override: [completions] })];

  function handleChange(value: string) {
    setContent(value);
    setDirty(true);
  }

  function handleBack() {
    onBack();
  }

  return (
    <div className="flex flex-col" style={{ height: '100vh' }}>
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-bambu-dark-tertiary shrink-0">
        <button
          onClick={handleBack}
          className="p-1.5 rounded hover:bg-bambu-dark-secondary text-bambu-text-secondary hover:text-bambu-text transition-colors"
          title="Back"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-bambu-text">{file.name}</span>
            {dirty && <span className="text-xs text-bambu-text-secondary">(unsaved)</span>}
          </div>
          <p className="text-xs text-bambu-text-secondary">{file.file_path}</p>
        </div>
        <input
          ref={uploadRef}
          type="file"
          accept=".cfg,.txt"
          className="hidden"
          onChange={handleUpload}
        />
        <button
          onClick={handleDownload}
          className="p-1.5 rounded hover:bg-bambu-dark-secondary text-bambu-text-secondary hover:text-bambu-text transition-colors"
          title="Download .cfg file"
        >
          <Download className="w-4 h-4" />
        </button>
        <button
          onClick={() => uploadRef.current?.click()}
          className="p-1.5 rounded hover:bg-bambu-dark-secondary text-bambu-text-secondary hover:text-bambu-text transition-colors"
          title="Upload .cfg file (replaces current content)"
        >
          <Upload className="w-4 h-4" />
        </button>
        <Button
          variant="primary"
          size="sm"
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending || !dirty}
        >
          {saveMutation.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4 mr-1" />
          )}
          Save
        </Button>
      </div>

      {/* Parse error banner */}
      {file.parse_error && (
        <div className="flex items-start gap-2 px-4 py-2 bg-red-900/20 border-b border-red-900/40 text-xs text-red-300 shrink-0">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{file.parse_error}</span>
        </div>
      )}

      {/* Editor + hints */}
      <div className="flex flex-1 overflow-hidden">
        {/* CodeMirror */}
        <div className="flex-1 min-w-0 overflow-auto">
          {contentLoading && !content ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-6 h-6 animate-spin text-bambu-text-secondary" />
            </div>
          ) : (
            <CodeMirror
              value={content}
              onChange={handleChange}
              extensions={extensions}
              theme={oneDark}
              minHeight="600px"
              className="text-sm"
            />
          )}
        </div>

        {/* Hints panel */}
        <div className="w-56 shrink-0 border-l border-bambu-dark-tertiary overflow-y-auto flex flex-col gap-0">
          <div className="p-3 border-b border-bambu-dark-tertiary">
            <div className="text-xs font-semibold text-bambu-text mb-2">Format</div>
            <pre className="text-xs text-bambu-text-secondary font-mono whitespace-pre-wrap leading-relaxed">{`[macro name]
description: optional text
trigger: manual|webhook|schedule
cron: 0 8 * * *
printer: My Printer Name
G28
M104 S200
NOTIFY --message="Done"`}</pre>
          </div>

          <div className="p-3 border-b border-bambu-dark-tertiary">
            <div className="text-xs font-semibold text-bambu-text mb-2">Context</div>
            {CONTEXT_VARS.map((v) => (
              <div key={v.name} className="mb-1.5">
                <code className="text-bambu-green text-xs">{`{{ ${v.name} }}`}</code>
                <div className="text-bambu-text-secondary text-xs">{v.desc}</div>
              </div>
            ))}
          </div>

          <div className="p-3 border-b border-bambu-dark-tertiary">
            <div className="text-xs font-semibold text-bambu-text mb-2">Commands</div>
            {SYSTEM_COMMANDS.map((c) => (
              <div key={c.cmd} className="mb-2">
                <code className="text-yellow-400 text-xs block break-all">{c.cmd}</code>
                <span className="text-bambu-text-secondary text-xs">{c.desc}</span>
              </div>
            ))}
          </div>

          <div className="p-3 border-b border-bambu-dark-tertiary">
            <div className="text-xs font-semibold text-bambu-text mb-2">G-code</div>
            <div className="flex flex-wrap gap-1">
              {gcodeWhitelist.map((g) => (
                <span
                  key={g}
                  className="px-1 py-0.5 rounded bg-bambu-dark-secondary text-xs font-mono text-bambu-text-secondary"
                >
                  {g}
                </span>
              ))}
            </div>
          </div>

          {allMacros.length > 0 && (
            <div className="p-3">
              <div className="text-xs font-semibold text-bambu-text mb-2">Call macro</div>
              {allMacros.map((m) => (
                <code key={m.id} className="block text-xs text-blue-400 mb-1 break-all">
                  {`{{ run_macro("${m.name}") }}`}
                </code>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
