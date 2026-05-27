import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { X, FolderKanban, Loader2, XCircle, Search } from 'lucide-react';
import { api } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface BatchProjectModalProps {
  selectedIds: number[];
  onClose: () => void;
}

export function BatchProjectModal({ selectedIds, onClose }: BatchProjectModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [query, setQuery] = useState('');

  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
  });

  const sortedProjects = useMemo(
    () => (projects ? [...projects].sort((a, b) => a.name.localeCompare(b.name)) : undefined),
    [projects],
  );

  const trimmed = query.trim().toLowerCase();
  const visibleProjects = trimmed
    ? sortedProjects?.filter((p) => p.name.toLowerCase().includes(trimmed))
    : sortedProjects;
  const showSearch = (sortedProjects?.length ?? 0) > 5;

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Helper to invalidate all project-related queries
  const invalidateProjectQueries = () => {
    queryClient.invalidateQueries({ queryKey: ['archives'] });
    queryClient.invalidateQueries({ queryKey: ['projects'] });
    // Invalidate project detail pages (partial match catches all project IDs)
    queryClient.invalidateQueries({ queryKey: ['project'] });
    queryClient.invalidateQueries({ queryKey: ['project-archives'] });
  };

  // Assign to project mutation (uses bulk API)
  const assignMutation = useMutation({
    mutationFn: async (projectId: number) => {
      await api.addArchivesToProject(projectId, selectedIds);
      return projectId;
    },
    onSuccess: (projectId) => {
      const project = projects?.find(p => p.id === projectId);
      invalidateProjectQueries();
      showToast(`Added ${selectedIds.length} archive${selectedIds.length !== 1 ? 's' : ''} to "${project?.name}"`);
      onClose();
    },
    onError: () => {
      showToast('Failed to assign project', 'error');
    },
  });

  // Remove from project mutation (updates each archive individually)
  const removeMutation = useMutation({
    mutationFn: async () => {
      for (const id of selectedIds) {
        await api.updateArchive(id, { project_id: null });
      }
      return selectedIds.length;
    },
    onSuccess: (count) => {
      invalidateProjectQueries();
      showToast(`Removed ${count} archive${count !== 1 ? 's' : ''} from project`);
      onClose();
    },
    onError: () => {
      showToast('Failed to remove from project', 'error');
    },
  });

  const isPending = assignMutation.isPending || removeMutation.isPending;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md max-h-[80vh] flex flex-col">
        <CardContent className="p-0 flex flex-col min-h-0">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary shrink-0">
            <div className="flex items-center gap-2">
              <FolderKanban className="w-5 h-5 text-bambu-green" />
              <h2 className="text-xl font-semibold text-white">
                Assign to Project
              </h2>
            </div>
            <button
              onClick={onClose}
              className="text-bambu-gray hover:text-white transition-colors"
              disabled={isPending}
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Content */}
          <div className="p-4 space-y-3 overflow-y-auto min-h-0">
            <p className="text-sm text-bambu-gray">
              Assign {selectedIds.length} selected archive{selectedIds.length !== 1 ? 's' : ''} to a project
            </p>

            {isLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-bambu-gray" />
              </div>
            ) : (
              <div className="space-y-2">
                {/* Remove from project option */}
                <button
                  onClick={() => removeMutation.mutate()}
                  disabled={isPending}
                  className="w-full flex items-center gap-3 p-3 rounded-lg bg-bambu-dark hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary transition-colors text-left disabled:opacity-50"
                >
                  <div className="w-8 h-8 rounded-full bg-red-500/20 flex items-center justify-center shrink-0">
                    <XCircle className="w-4 h-4 text-red-400" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-white font-medium">Remove from project</p>
                    <p className="text-sm text-bambu-gray truncate">Clear project assignment</p>
                  </div>
                  {removeMutation.isPending && (
                    <Loader2 className="w-4 h-4 animate-spin text-bambu-gray shrink-0" />
                  )}
                </button>

                {/* Divider */}
                {sortedProjects && sortedProjects.length > 0 && (
                  <div className="flex items-center gap-2 py-2">
                    <div className="flex-1 h-px bg-bambu-dark-tertiary" />
                    <span className="text-xs text-bambu-gray">or assign to</span>
                    <div className="flex-1 h-px bg-bambu-dark-tertiary" />
                  </div>
                )}

                {/* Search input */}
                {showSearch && (
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                    <input
                      type="text"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder={t('archives.menu.searchProjects')}
                      className="w-full pl-9 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray text-sm focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                )}

                {/* Project list */}
                {visibleProjects?.map((project) => (
                  <button
                    key={project.id}
                    onClick={() => assignMutation.mutate(project.id)}
                    disabled={isPending}
                    className="w-full flex items-center gap-3 p-3 rounded-lg bg-bambu-dark hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary transition-colors text-left disabled:opacity-50"
                  >
                    <div
                      className="w-8 h-8 rounded-full flex items-center justify-center shrink-0"
                      style={{ backgroundColor: project.color ? `${project.color}20` : 'rgb(var(--bambu-green) / 0.2)' }}
                    >
                      <FolderKanban
                        className="w-4 h-4"
                        style={{ color: project.color || 'rgb(var(--bambu-green))' }}
                      />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-white font-medium truncate">{project.name}</p>
                      <p className="text-sm text-bambu-gray truncate">
                        {project.archive_count} archive{project.archive_count !== 1 ? 's' : ''}
                        {project.status && ` • ${project.status}`}
                      </p>
                    </div>
                    {assignMutation.isPending && assignMutation.variables === project.id && (
                      <Loader2 className="w-4 h-4 animate-spin text-bambu-gray shrink-0" />
                    )}
                  </button>
                ))}

                {(!sortedProjects || sortedProjects.length === 0) && (
                  <p className="text-center text-bambu-gray py-4">
                    No projects yet. Create one from the Projects page.
                  </p>
                )}

                {sortedProjects && sortedProjects.length > 0 && visibleProjects?.length === 0 && (
                  <p className="text-center text-bambu-gray text-sm py-4">—</p>
                )}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex gap-3 p-4 border-t border-bambu-dark-tertiary shrink-0">
            <Button variant="secondary" onClick={onClose} className="flex-1" disabled={isPending}>
              Cancel
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
