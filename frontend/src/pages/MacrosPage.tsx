import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Plus,
  Loader2,
  FileCode2,
  AlertCircle,
  Play,
  Settings,
  ChevronRight,
  Clock,
  CircleCheck,
  Trash2,
} from 'lucide-react';
import { api, type MacroCfgFile, type Macro, type MacroRun } from '../api/client';
import { CfgFileEditor } from '../components/CfgFileEditor';
import { MacroSettingsPanel } from '../components/MacroSettingsPanel';
import { Button } from '../components/Button';
import { Card, CardContent } from '../components/Card';
import { ConfirmModal } from '../components/ConfirmModal';
import { useToast } from '../contexts/ToastContext';

// ── Helpers ───────────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: Macro['status'] }) {
  const styles: Record<string, string> = {
    active: 'bg-green-900/50 text-green-300',
    orphaned: 'bg-zinc-700 text-zinc-400',
    error: 'bg-red-900/50 text-red-300',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? styles.error}`}>
      {status}
    </span>
  );
}

function TriggerBadge({ type }: { type: Macro['trigger_type'] }) {
  const colors = {
    manual: 'bg-zinc-700 text-zinc-300',
    webhook: 'bg-blue-900/50 text-blue-300',
    schedule: 'bg-purple-900/50 text-purple-300',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium capitalize ${colors[type]}`}>
      {type}
    </span>
  );
}

function LastRunIcon({ runs }: { runs?: MacroRun[] }) {
  const last = runs?.[0];
  if (!last) return <Clock className="w-4 h-4 text-bambu-text-secondary" title="Never run" />;
  if (last.status === 'success') return <CircleCheck className="w-4 h-4 text-green-400" title="Last run: success" />;
  if (last.status === 'error') return <AlertCircle className="w-4 h-4 text-red-400" title="Last run: error" />;
  return <Loader2 className="w-4 h-4 animate-spin text-blue-400" title="Running…" />;
}

// ── Macro row ─────────────────────────────────────────────────────────────────

function MacroRow({
  macro,
  onSettings,
  onRun,
}: {
  macro: Macro;
  onSettings: () => void;
  onRun: () => void;
}) {
  const { data: runs } = useQuery({
    queryKey: ['macro-runs', macro.id],
    queryFn: () => api.getMacroRuns(macro.id),
    refetchInterval: (query) => {
      const r = query.state.data as MacroRun[] | undefined;
      return r?.some((run) => run.status === 'pending' || run.status === 'running') ? 1500 : 10000;
    },
  });

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 border-b border-bambu-dark-tertiary last:border-0 hover:bg-bambu-dark-secondary/40 transition-colors">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-bambu-text text-sm">{macro.name}</span>
          <StatusBadge status={macro.status} />
          <TriggerBadge type={macro.trigger_type} />
        </div>
        {macro.description && (
          <p className="text-xs text-bambu-text-secondary mt-0.5 truncate">{macro.description}</p>
        )}
      </div>
      <LastRunIcon runs={runs} />
      <button
        onClick={onSettings}
        className="p-1.5 rounded hover:bg-bambu-dark-secondary transition-colors text-bambu-text-secondary hover:text-bambu-text"
        title="Settings"
      >
        <Settings className="w-4 h-4" />
      </button>
      {macro.status === 'active' && (
        <button
          onClick={onRun}
          className="p-1.5 rounded hover:bg-bambu-dark-secondary transition-colors text-bambu-text-secondary hover:text-bambu-text"
          title="Run"
        >
          <Play className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}

// ── Cfg file list item ────────────────────────────────────────────────────────

function CfgFileItem({
  file,
  isSelected,
  onClick,
}: {
  file: MacroCfgFile;
  isSelected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 rounded flex items-center gap-2 transition-colors ${
        isSelected
          ? 'bg-bambu-green/20 text-bambu-green'
          : 'hover:bg-bambu-dark-secondary text-bambu-text'
      }`}
    >
      <FileCode2 className="w-4 h-4 shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{file.name}</div>
        <div className="text-xs text-bambu-text-secondary truncate">{file.file_path}</div>
      </div>
      {file.parse_error && (
        <AlertCircle className="w-4 h-4 text-red-400 shrink-0" title={file.parse_error} />
      )}
      {isSelected && <ChevronRight className="w-4 h-4 shrink-0" />}
    </button>
  );
}

// ── Run modal ─────────────────────────────────────────────────────────────────

function RunModal({
  macro,
  onClose,
}: {
  macro: Macro;
  onClose: () => void;
}) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [printerId, setPrinterId] = useState<number | null>(macro.printer_id);

  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const runMutation = useMutation({
    mutationFn: () => api.runMacro(macro.id, printerId ?? undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['macro-runs', macro.id] });
      showToast('Macro started');
      onClose();
    },
    onError: () => showToast('Failed to start macro', 'error'),
  });

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <Card className="w-80">
        <CardContent className="p-4 flex flex-col gap-3">
          <div className="font-semibold text-bambu-text">Run "{macro.name}"</div>
          <div>
            <label className="block text-sm text-bambu-text-secondary mb-1">Target printer</label>
            <select
              value={printerId ?? ''}
              onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-sm outline-none focus:border-bambu-green text-bambu-text"
            >
              <option value="">Any printer</option>
              {printers.map((p: { id: number; name: string }) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
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
  );
}

// ── New file modal ────────────────────────────────────────────────────────────

function NewFileModal({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [name, setName] = useState('');

  const createMutation = useMutation({
    mutationFn: () => api.createMacroCfgFile({ name }),
    onSuccess: (file) => {
      queryClient.invalidateQueries({ queryKey: ['macro-cfg-files'] });
      showToast(`Created ${file.name}`);
      onCreated(file.id);
    },
    onError: () => showToast('Failed to create file', 'error'),
  });

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <Card className="w-80">
        <CardContent className="p-4 flex flex-col gap-3">
          <div className="font-semibold text-bambu-text">New macro file</div>
          <div>
            <label className="block text-sm text-bambu-text-secondary mb-1">File name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. my_macros"
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-sm outline-none focus:border-bambu-green text-bambu-text"
              onKeyDown={(e) => e.key === 'Enter' && name.trim() && createMutation.mutate()}
              autoFocus
            />
            <p className="text-xs text-bambu-text-secondary mt-1">A .cfg file will be created in the macros directory.</p>
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !name.trim()}
            >
              {createMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Create'}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

type PanelView = 'macros' | 'edit-file';

export function MacrosPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [selectedFileId, setSelectedFileId] = useState<number | null>(null);
  const [panelView, setPanelView] = useState<PanelView>('macros');
  const [settingsMacroId, setSettingsMacroId] = useState<number | null>(null);
  const [runMacro, setRunMacro] = useState<Macro | null>(null);
  const [showNewFile, setShowNewFile] = useState(false);
  const [deleteFileId, setDeleteFileId] = useState<number | null>(null);

  const { data: cfgFiles = [], isLoading: filesLoading } = useQuery({
    queryKey: ['macro-cfg-files'],
    queryFn: api.getMacroCfgFiles,
  });

  const { data: allMacros = [], isLoading: macrosLoading } = useQuery({
    queryKey: ['macros'],
    queryFn: () => api.getMacros(),
  });

  const deleteFileMutation = useMutation({
    mutationFn: (id: number) => api.deleteMacroCfgFile(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['macro-cfg-files'] });
      queryClient.invalidateQueries({ queryKey: ['macros'] });
      showToast('File deleted');
      if (selectedFileId === deleteFileId) {
        setSelectedFileId(null);
        setPanelView('macros');
      }
      setDeleteFileId(null);
    },
    onError: () => showToast('Failed to delete file', 'error'),
  });

  const selectedFile = cfgFiles.find((f) => f.id === selectedFileId) ?? null;
  const fileMacros = selectedFile
    ? allMacros.filter((m) => m.cfg_file_id === selectedFile.id)
    : [];

  const settingsMacro = settingsMacroId
    ? allMacros.find((m) => m.id === settingsMacroId) ?? null
    : null;

  function handleFileSelect(fileId: number) {
    setSelectedFileId(fileId);
    setPanelView('macros');
    setSettingsMacroId(null);
  }

  return (
    <div className="flex min-h-screen">
      {/* ── Left panel: cfg file list ─────────────────────────────────────── */}
      <div className="w-64 shrink-0 border-r border-bambu-dark-tertiary flex flex-col">
        <div className="flex items-center justify-between px-3 py-3 border-b border-bambu-dark-tertiary">
          <span className="text-sm font-semibold text-bambu-text">{t('macros.files')}</span>
          <button
            onClick={() => setShowNewFile(true)}
            className="p-1 rounded hover:bg-bambu-dark-secondary text-bambu-text-secondary hover:text-bambu-text transition-colors"
            title="New file"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-1">
          {filesLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-bambu-text-secondary" />
            </div>
          ) : cfgFiles.length === 0 ? (
            <p className="text-xs text-bambu-text-secondary text-center py-6 px-2">
              No macro files yet. Create one to get started.
            </p>
          ) : (
            cfgFiles.map((file) => (
              <CfgFileItem
                key={file.id}
                file={file}
                isSelected={selectedFileId === file.id}
                onClick={() => handleFileSelect(file.id)}
              />
            ))
          )}
        </div>
      </div>

      {/* ── Right panel ───────────────────────────────────────────────────── */}
      <div className="flex-1 min-w-0 flex flex-col">
        {!selectedFile ? (
          <div className="flex-1 flex items-center justify-center text-bambu-text-secondary">
            <div className="text-center">
              <FileCode2 className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">{cfgFiles.length === 0 ? 'Create a macro file to get started.' : 'Select a file to view its macros.'}</p>
            </div>
          </div>
        ) : panelView === 'edit-file' ? (
          <CfgFileEditor
            file={selectedFile}
            onBack={() => {
              setPanelView('macros');
              queryClient.invalidateQueries({ queryKey: ['macros'] });
              queryClient.invalidateQueries({ queryKey: ['macro-cfg-files'] });
            }}
          />
        ) : settingsMacro ? (
          <MacroSettingsPanel
            macro={settingsMacro}
            onBack={() => setSettingsMacroId(null)}
          />
        ) : (
          /* Macro list for selected file */
          <div className="flex flex-col h-full">
            <div className="flex items-center justify-between px-4 py-3 border-b border-bambu-dark-tertiary">
              <div>
                <h2 className="text-base font-semibold text-bambu-text">{selectedFile.name}</h2>
                <p className="text-xs text-bambu-text-secondary">{selectedFile.file_path}</p>
                {selectedFile.parse_error && (
                  <p className="text-xs text-red-400 mt-0.5 flex items-center gap-1">
                    <AlertCircle className="w-3 h-3" />
                    {selectedFile.parse_error}
                  </p>
                )}
              </div>
              <div className="flex gap-2">
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => setDeleteFileId(selectedFile.id)}
                  title="Delete file"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setPanelView('edit-file')}
                >
                  <FileCode2 className="w-4 h-4 mr-1" />
                  {t('macros.editFile')}
                </Button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto">
              {macrosLoading ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="w-6 h-6 animate-spin text-bambu-text-secondary" />
                </div>
              ) : fileMacros.length === 0 ? (
                <div className="text-center py-12 text-bambu-text-secondary text-sm">
                  <p>No macros in this file.</p>
                  <p className="text-xs mt-1">Add <code className="text-bambu-green">[macro name]</code> blocks in the editor.</p>
                </div>
              ) : (
                <div>
                  {fileMacros.map((macro) => (
                    <MacroRow
                      key={macro.id}
                      macro={macro}
                      onSettings={() => setSettingsMacroId(macro.id)}
                      onRun={() => setRunMacro(macro)}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Modals */}
      {runMacro && <RunModal macro={runMacro} onClose={() => setRunMacro(null)} />}
      {showNewFile && (
        <NewFileModal
          onClose={() => setShowNewFile(false)}
          onCreated={(id) => {
            setShowNewFile(false);
            setSelectedFileId(id);
            setPanelView('edit-file');
          }}
        />
      )}
      {deleteFileId !== null && (
        <ConfirmModal
          title="Delete macro file"
          message={`Delete "${cfgFiles.find((f) => f.id === deleteFileId)?.name}"? All its macros will be orphaned (run history preserved).`}
          variant="danger"
          isLoading={deleteFileMutation.isPending}
          onConfirm={() => deleteFileMutation.mutate(deleteFileId)}
          onCancel={() => setDeleteFileId(null)}
        />
      )}
    </div>
  );
}
