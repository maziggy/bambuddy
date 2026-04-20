import { useTranslation } from 'react-i18next';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Plus, Loader2, Play, Edit3, AlertCircle, CircleCheck, Clock } from 'lucide-react';
import { api, type Macro, type MacroRun } from '../api/client';
import { MacroEditor } from '../components/MacroEditor';
import { Button } from '../components/Button';
import { Card, CardContent } from '../components/Card';

function TriggerBadge({ type }: { type: Macro['trigger_type'] }) {
  const colors = {
    manual: 'bg-zinc-700 text-zinc-300',
    webhook: 'bg-blue-900 text-blue-300',
    schedule: 'bg-purple-900 text-purple-300',
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

function MacroCard({ macro, onEdit }: { macro: Macro; onEdit: () => void }) {
  const { data: runs } = useQuery({
    queryKey: ['macro-runs', macro.id],
    queryFn: () => api.getMacroRuns(macro.id),
    refetchInterval: (query) => {
      const runs = query.state.data as MacroRun[] | undefined;
      const active = runs?.some((r) => r.status === 'pending' || r.status === 'running');
      return active ? 2000 : false;
    },
  });

  return (
    <Card className="hover:border-bambu-green/40 transition-colors">
      <CardContent className="p-4 flex items-center gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="font-medium text-bambu-text truncate">{macro.name}</span>
            <TriggerBadge type={macro.trigger_type} />
          </div>
          {macro.description && (
            <p className="text-xs text-bambu-text-secondary truncate">{macro.description}</p>
          )}
        </div>
        <LastRunIcon runs={runs} />
        <Button variant="secondary" size="sm" onClick={onEdit}>
          <Edit3 className="w-4 h-4" />
        </Button>
      </CardContent>
    </Card>
  );
}

export function MacrosPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id?: string }>();
  const navigate = useNavigate();

  const { data: macros = [], isLoading } = useQuery({
    queryKey: ['macros'],
    queryFn: api.getMacros,
  });

  // id is either undefined (list), "new", or a numeric string
  const editorId: number | 'new' | null = id === 'new' ? 'new' : id ? Number(id) : null;

  if (editorId !== null) {
    return (
      <div className="p-6">
        <button
          onClick={() => navigate('/macros')}
          className="text-sm text-bambu-text-secondary hover:text-bambu-text mb-4 flex items-center gap-1"
        >
          ← {t('macros.title')}
        </button>
        <MacroEditor
          macroId={editorId}
          onSaved={(newId) => navigate(`/macros/${newId}`)}
          onDeleted={() => navigate('/macros')}
        />
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-bambu-text">{t('macros.title')}</h1>
        <Button variant="primary" onClick={() => navigate('/macros/new')}>
          <Plus className="w-4 h-4 mr-1" />
          {t('macros.newMacro')}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-bambu-text-secondary" />
        </div>
      ) : macros.length === 0 ? (
        <div className="text-center py-16 text-bambu-text-secondary">
          <Play className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p>{t('macros.noMacros')}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {macros.map((macro) => (
            <MacroCard
              key={macro.id}
              macro={macro}
              onEdit={() => navigate(`/macros/${macro.id}`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
