import { useState, useRef, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation } from '@tanstack/react-query';
import { Send, Loader2 } from 'lucide-react';
import { api, type MacroRun, type Printer } from '../api/client';
import { Button } from '../components/Button';

interface BufferLine {
  id: number;
  text: string;
  cls: string;
}

let _lineId = 0;
const nextId = () => ++_lineId;

function severityClass(s: number) {
  if (s === 1) return 'text-red-400';
  if (s === 2) return 'text-orange-400';
  if (s === 3) return 'text-yellow-400';
  return 'text-blue-400';
}

function lineClass(text: string): string {
  if (text.startsWith('[ERROR]') || text.startsWith('[PREFLIGHT]')) return 'text-red-400';
  if (text.startsWith('[WARN]')) return 'text-yellow-400';
  if (text.startsWith('[HMS')) return 'text-orange-400';
  if (text.startsWith('[MACRO]')) return 'text-blue-400';
  if (text.startsWith('[GCODE]')) return 'text-zinc-400';
  if (text.startsWith('[CANCELLED]')) return 'text-yellow-600';
  if (text.startsWith('[SKIP]')) return 'text-zinc-500';
  return 'text-bambu-text-secondary';
}

/**
 * Parse @tag from the start of a line.
 * Returns { tag, rest } where tag is the raw @word and rest is everything after it.
 * Returns null if no @tag present.
 */
function parseAtTag(line: string): { tag: string; rest: string } | null {
  const m = line.match(/^(@\S+)\s*(.*)/);
  if (!m) return null;
  return { tag: m[1], rest: m[2] };
}

/**
 * Resolve @tag against printer list.
 * Returns the matched printers, or [] if no match.
 * "@all" matches every printer.
 */
function resolveTag(tag: string, printers: Printer[]): Printer[] {
  const name = tag.slice(1).toLowerCase();
  if (name === 'all') return printers;
  return printers.filter((p) => p.name.toLowerCase().startsWith(name));
}

export function TerminalPage() {
  const { t } = useTranslation();

  const [input, setInput] = useState('');
  const [printerId, setPrinterId] = useState<number | null>(null);
  const [buffer, setBuffer] = useState<BufferLine[]>([
    { id: nextId(), text: 'Bambuddy terminal — type a G-code, system command, or macro name and press Enter.', cls: 'text-zinc-500' },
    { id: nextId(), text: 'Tip: prefix with @printername or @all to target specific printers.', cls: 'text-zinc-600' },
    { id: nextId(), text: '', cls: '' },
  ]);
  const [inputHistory, setInputHistory] = useState<string[]>([]);
  const [inputHistoryIdx, setInputHistoryIdx] = useState(-1);
  const [atSuggestions, setAtSuggestions] = useState<string[]>([]);
  const [atSuggestionIdx, setAtSuggestionIdx] = useState(0);

  const logOffsets = useRef<Map<number, number>>(new Map());
  const pollHandles = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());
  const knownRunIds = useRef(new Set<number>());
  const inputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const push = useCallback((text: string, cls = 'text-bambu-text-secondary') => {
    const id = nextId();
    setBuffer((prev) => [...prev, { id, text, cls }]);
    return id;
  }, []);

  // Stream log lines for a run until it finishes
  const streamRun = useCallback((runId: number, label?: string) => {
    if (pollHandles.current.has(runId)) return;
    pollHandles.current.set(runId, null as unknown as ReturnType<typeof setInterval>);
    logOffsets.current.set(runId, 0);
    push(`  ▶ macro run #${runId}${label ? ` @ ${label}` : ''} — running…`, 'text-blue-400');

    // donePushed prevents the summary line firing more than once even if
    // multiple interval ticks resolve concurrently after the run finishes.
    let donePushed = false;

    const handle = setInterval(async () => {
      // If we already finished, do nothing (interval may still fire once after clearInterval)
      if (donePushed) return;
      // Also bail if another call somehow removed us
      if (!pollHandles.current.has(runId)) return;

      let run: MacroRun;
      try {
        run = await api.getMacroRun(runId);
      } catch {
        return;
      }

      const log = run.log ?? '';
      const offset = logOffsets.current.get(runId) ?? 0;
      const newContent = log.slice(offset);
      if (newContent) {
        logOffsets.current.set(runId, log.length);
        newContent
          .split('\n')
          .filter((l) => l.trim())
          .forEach((l) => push(`    ${l}`, lineClass(l)));
      }

      if (run.status === 'success' || run.status === 'error') {
        // Remove from map and set flag synchronously before any awaits
        pollHandles.current.delete(runId);
        clearInterval(handle);
        if (donePushed) return;
        donePushed = true;
        const elapsed = run.finished_at
          ? `${Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000)}s`
          : '?s';
        push(
          `  ${run.status === 'success' ? '✓' : '✗'} macro run #${runId} ${run.status}  ${elapsed}`,
          run.status === 'success' ? 'text-green-400' : 'text-red-400',
        );
        push('', '');
      }
    }, 500);

    pollHandles.current.set(runId, handle);
  }, [push]);

  // Discovery query for externally triggered runs
  const { data: macroRuns = [] } = useQuery({
    queryKey: ['macro-runs-terminal', printerId],
    queryFn: async () => {
      if (!printerId) return [];
      const macros = await api.getMacros();
      const runArrays = await Promise.all(
        macros
          .filter((m) => m.printer_id === printerId || m.printer_id === null)
          .map((m) => api.getMacroRuns(m.id))
      );
      return runArrays
        .flat()
        .filter((r) => r.printer_id === printerId || r.printer_id === null)
        .sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())
        .slice(0, 30);
    },
    enabled: !!printerId,
    refetchInterval: (query) => {
      const runs = query.state.data as MacroRun[] | undefined;
      const active = runs?.some((r) => r.status === 'pending' || r.status === 'running');
      return active ? 2000 : 8000;
    },
  });

  useEffect(() => {
    macroRuns.forEach((run) => {
      if (knownRunIds.current.has(run.id)) return;
      if (run.status !== 'pending' && run.status !== 'running') return;
      knownRunIds.current.add(run.id);
      streamRun(run.id);
    });
  }, [macroRuns, streamRun]);

  useEffect(() => {
    return () => {
      pollHandles.current.forEach((h) => { if (h) clearInterval(h); });
    };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'instant' });
  }, [buffer]);

  // Update @autocomplete suggestions as user types
  useEffect(() => {
    const atMatch = input.match(/^(@\S*)/);
    if (!atMatch) {
      setAtSuggestions([]);
      return;
    }
    const partial = atMatch[1].slice(1).toLowerCase();
    const names = partial === '' || 'all'.startsWith(partial)
      ? ['all', ...printers.map((p) => p.name)]
      : printers.filter((p) => p.name.toLowerCase().startsWith(partial)).map((p) => p.name);
    // deduplicate, keep 'all' first
    const unique = [...new Set(names)];
    setAtSuggestions(unique);
    setAtSuggestionIdx(0);
  }, [input, printers]);

  const execMutation = useMutation({
    mutationFn: ({ line, pid }: { line: string; pid: number | null }) =>
      api.execLine(line, pid ?? undefined),
    onSuccess: (data, vars) => {
      if (data.run_id != null) {
        const printer = printers.find((p) => p.id === vars.pid);
        knownRunIds.current.add(data.run_id);
        streamRun(data.run_id, printer?.name);
        return;
      }
      if (data.log) {
        data.log.trimEnd().split('\n').forEach((l) => { if (l) push(l, lineClass(l)); });
      }
      data.hms_errors?.forEach((e) => {
        push(
          `  ⚠ HMS ${e.code} [${e.severity === 1 ? 'Fatal' : e.severity === 2 ? 'Serious' : e.severity === 3 ? 'Warning' : 'Info'}]${e.message ? ' — ' + e.message : ''}`,
          severityClass(e.severity),
        );
      });
      if (data.printer_state) {
        push(`  printer state: ${data.printer_state}`, data.status === 'success' ? 'text-zinc-500' : 'text-yellow-600');
      }
      push('', '');
    },
    onError: (_err, vars) => {
      const printer = printers.find((p) => p.id === vars.pid);
      push(`[ERROR] Request failed${printer ? ` (${printer.name})` : ''}`, 'text-red-400');
      push('', '');
    },
  });

  const submitLine = useCallback((rawLine: string, overridePrinterIds?: number[]) => {
    if (!rawLine || execMutation.isPending) return;

    // Parse optional @tag
    const parsed = parseAtTag(rawLine);
    let command = rawLine;
    let targetIds: Array<number | null>;

    if (parsed) {
      const matched = resolveTag(parsed.tag, printers);
      if (matched.length === 0) {
        push(`[ERROR] No printer matching '${parsed.tag}'`, 'text-red-400');
        push('', '');
        return;
      }
      command = parsed.rest;
      if (!command.trim()) {
        push(`[ERROR] No command after '${parsed.tag}'`, 'text-red-400');
        push('', '');
        return;
      }
      targetIds = matched.map((p) => p.id);
    } else if (overridePrinterIds) {
      targetIds = overridePrinterIds;
    } else {
      targetIds = [printerId];
    }

    // Echo the command with target label
    const targetLabel = targetIds.length === printers.length && printers.length > 1
      ? 'all'
      : targetIds.map((id) => printers.find((p) => p.id === id)?.name ?? 'no printer').join(', ');
    push(`${targetLabel} > ${command}`, 'text-bambu-green');

    // Fan out to each target printer sequentially (fire and forget parallel)
    targetIds.forEach((pid) => {
      execMutation.mutate({ line: command, pid });
    });
  }, [execMutation, printerId, printers, push]);

  const submit = useCallback(() => {
    const line = input.trim();
    if (!line || execMutation.isPending) return;
    setInputHistory((prev) => [line, ...prev.slice(0, 99)]);
    setInputHistoryIdx(-1);
    setInput('');
    setAtSuggestions([]);
    submitLine(line);
  }, [input, execMutation.isPending, submitLine]);

  const applySuggestion = useCallback((name: string) => {
    // Replace the @partial at the start of input with @name + space
    const rest = input.replace(/^@\S*\s*/, '');
    const newInput = `@${name} ${rest}`;
    setInput(newInput);
    setAtSuggestions([]);
    inputRef.current?.focus();
  }, [input]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    // Autocomplete navigation
    if (atSuggestions.length > 0) {
      if (e.key === 'Tab' || (e.key === 'ArrowRight' && input.match(/^@\S*$/))) {
        e.preventDefault();
        applySuggestion(atSuggestions[atSuggestionIdx]);
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setAtSuggestionIdx((i) => (i + 1) % atSuggestions.length);
        return;
      }
      if (e.key === 'ArrowUp' && atSuggestions.length > 0 && input.startsWith('@')) {
        e.preventDefault();
        setAtSuggestionIdx((i) => (i - 1 + atSuggestions.length) % atSuggestions.length);
        return;
      }
      if (e.key === 'Escape') {
        setAtSuggestions([]);
        return;
      }
    }

    if (e.key === 'Enter') {
      submit();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const next = Math.min(inputHistoryIdx + 1, inputHistory.length - 1);
      setInputHistoryIdx(next);
      setInput(inputHistory[next] ?? '');
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = inputHistoryIdx - 1;
      if (next < 0) {
        setInputHistoryIdx(-1);
        setInput('');
      } else {
        setInputHistoryIdx(next);
        setInput(inputHistory[next] ?? '');
      }
    }
  };

  // Derive prompt label from input @tag or selected printer
  const parsedTag = parseAtTag(input);
  const taggedPrinters = parsedTag ? resolveTag(parsedTag.tag, printers) : null;
  const promptLabel = taggedPrinters
    ? (taggedPrinters.length === printers.length && printers.length > 1 ? 'all' : taggedPrinters.map((p) => p.name).join(', '))
    : (printers.find((p) => p.id === printerId)?.name ?? '');

  return (
    <div className="p-6 flex flex-col" style={{ height: 'calc(100vh - 4rem)' }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4 shrink-0">
        <h1 className="text-2xl font-bold text-bambu-text">{t('terminal.title')}</h1>
        <select
          value={printerId ?? ''}
          onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
          className="bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-1.5 text-sm text-bambu-text outline-none focus:border-bambu-green"
        >
          <option value="">{t('terminal.noPrinter')}</option>
          {printers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      {/* Terminal buffer */}
      <div className="flex-1 overflow-y-auto bg-zinc-950 rounded border border-bambu-dark-tertiary px-4 py-3 font-mono text-xs leading-5 min-h-0 mb-3">
        {buffer.map((line) => (
          <div key={line.id} className={line.cls || 'text-bambu-text-secondary'}>
            {line.text || ' '}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="relative flex gap-2 shrink-0 items-center font-mono text-sm">
        {/* @autocomplete dropdown */}
        {atSuggestions.length > 0 && (
          <div className="absolute bottom-full left-0 mb-1 bg-zinc-900 border border-bambu-dark-tertiary rounded shadow-lg z-10 min-w-[160px]">
            {atSuggestions.map((name, i) => (
              <button
                key={name}
                className={`w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-bambu-dark-tertiary transition-colors ${i === atSuggestionIdx ? 'bg-bambu-dark-tertiary text-bambu-green' : 'text-bambu-text-secondary'}`}
                onMouseDown={(e) => { e.preventDefault(); applySuggestion(name); }}
              >
                @{name}
              </button>
            ))}
          </div>
        )}

        <span className="text-bambu-green select-none whitespace-nowrap">
          {promptLabel ? `${promptLabel} >` : '>'}
        </span>
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('terminal.placeholder')}
          className="flex-1 bg-zinc-950 border border-bambu-dark-tertiary rounded px-3 py-2 font-mono text-sm text-bambu-text outline-none focus:border-bambu-green placeholder:text-zinc-600"
          autoFocus
          spellCheck={false}
          autoComplete="off"
        />
        <Button
          variant="primary"
          size="sm"
          onClick={submit}
          disabled={!input.trim() || execMutation.isPending}
        >
          {execMutation.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Send className="w-4 h-4" />
          )}
        </Button>
      </div>
    </div>
  );
}
