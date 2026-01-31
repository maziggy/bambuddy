import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { X, FolderKanban, Loader2, XCircle } from 'lucide-react';
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

  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
  });

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
      showToast(t('batchProject.addedToProject', { count: selectedIds.length, project: project?.name }));
      onClose();
    },
    onError: () => {
      showToast(t('batchProject.assignFailed'), 'error');
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
      showToast(t('batchProject.removedFromProject', { count }));
      onClose();
    },
    onError: () => {
      showToast(t('batchProject.removeFailed'), 'error');
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
                {t('batchProject.assignTitle')}
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
              {t('batchProject.assignDescription', { count: selectedIds.length })}
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
                    <p className="text-white font-medium">{t('batchProject.removeFromProject')}</p>
                    <p className="text-sm text-bambu-gray truncate">{t('batchProject.clearAssignment')}</p>
                  </div>
                  {removeMutation.isPending && (
                    <Loader2 className="w-4 h-4 animate-spin text-bambu-gray shrink-0" />
                  )}
                </button>

                {/* Divider */}
                {projects && projects.length > 0 && (
                  <div className="flex items-center gap-2 py-2">
                    <div className="flex-1 h-px bg-bambu-dark-tertiary" />
                    <span className="text-xs text-bambu-gray">{t('batchProject.orAssignTo')}</span>
                    <div className="flex-1 h-px bg-bambu-dark-tertiary" />
                  </div>
                )}

                {/* Project list */}
                {projects?.map((project) => (
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
                        {t('batchProject.archiveCount', { count: project.archive_count })}
                        {project.status && ` • ${project.status}`}
                      </p>
                    </div>
                    {assignMutation.isPending && assignMutation.variables === project.id && (
                      <Loader2 className="w-4 h-4 animate-spin text-bambu-gray shrink-0" />
                    )}
                  </button>
                ))}

                {(!projects || projects.length === 0) && (
                  <p className="text-center text-bambu-gray py-4">
                    {t('batchProject.noProjects')}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex gap-3 p-4 border-t border-bambu-dark-tertiary shrink-0">
            <Button variant="secondary" onClick={onClose} className="flex-1" disabled={isPending}>
              {t('common.cancel')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
