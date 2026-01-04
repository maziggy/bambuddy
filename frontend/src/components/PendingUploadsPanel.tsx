import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Archive, Trash2, FileBox, Clock, Upload, ChevronDown, ChevronUp } from 'lucide-react';
import { pendingUploadsApi } from '../api/client';
import type { PendingUpload, ProjectListItem } from '../api/client';
import { api } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { ConfirmModal } from './ConfirmModal';

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTimeAgo(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

interface PendingUploadItemProps {
  upload: PendingUpload;
  projects: ProjectListItem[];
  onArchive: (id: number, data?: { tags?: string; notes?: string; project_id?: number }) => void;
  onDiscard: (id: number) => void;
  isArchiving: boolean;
  isDiscarding: boolean;
}

function PendingUploadItem({
  upload,
  projects,
  onArchive,
  onDiscard,
  isArchiving,
  isDiscarding,
}: PendingUploadItemProps) {
  const [expanded, setExpanded] = useState(false);
  const [tags, setTags] = useState(upload.tags || '');
  const [notes, setNotes] = useState(upload.notes || '');
  const [projectId, setProjectId] = useState<number | null>(upload.project_id);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);

  return (
    <Card>
      <CardContent className="py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <FileBox className="w-8 h-8 text-bambu-green flex-shrink-0" />
            <div>
              <p className="text-white font-medium">{upload.filename}</p>
              <div className="flex items-center gap-2 text-xs text-bambu-gray">
                <span>{formatFileSize(upload.file_size)}</span>
                <span>·</span>
                <span className="flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {formatTimeAgo(upload.uploaded_at)}
                </span>
                {upload.source_ip && (
                  <>
                    <span>·</span>
                    <span>from {upload.source_ip}</span>
                  </>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setExpanded(!expanded)}
              className="p-1 text-bambu-gray hover:text-white transition-colors"
            >
              {expanded ? <ChevronUp className="w-5 h-5" /> : <ChevronDown className="w-5 h-5" />}
            </button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => onArchive(upload.id, { tags, notes, project_id: projectId || undefined })}
              disabled={isArchiving}
            >
              {isArchiving ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <>
                  <Archive className="w-4 h-4" />
                  Archive
                </>
              )}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setShowDiscardConfirm(true)}
              disabled={isDiscarding}
            >
              {isDiscarding ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Trash2 className="w-4 h-4 text-red-400" />
              )}
            </Button>
          </div>
        </div>

        {/* Discard Confirmation Modal */}
        {showDiscardConfirm && (
          <ConfirmModal
            title="Discard Upload"
            message={`Are you sure you want to discard "${upload.filename}"? This cannot be undone.`}
            confirmText="Discard"
            variant="danger"
            onConfirm={() => {
              onDiscard(upload.id);
              setShowDiscardConfirm(false);
            }}
            onCancel={() => setShowDiscardConfirm(false)}
          />
        )}

        {/* Expanded details for adding tags/notes/project */}
        {expanded && (
          <div className="mt-4 pt-4 border-t border-bambu-dark-tertiary space-y-3">
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Tags</label>
              <input
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="e.g., functional, prototype, gift"
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white placeholder-bambu-gray text-sm"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Notes</label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Add notes about this print..."
                rows={2}
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white placeholder-bambu-gray text-sm resize-none"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Project</label>
              <select
                value={projectId || ''}
                onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)}
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white text-sm"
              >
                <option value="">No project</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function PendingUploadsPanel() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [showArchiveAllConfirm, setShowArchiveAllConfirm] = useState(false);
  const [showDiscardAllConfirm, setShowDiscardAllConfirm] = useState(false);
  const [archivingIds, setArchivingIds] = useState<Set<number>>(new Set());
  const [discardingIds, setDiscardingIds] = useState<Set<number>>(new Set());

  // Fetch pending uploads
  const { data: uploads, isLoading: uploadsLoading } = useQuery({
    queryKey: ['pending-uploads'],
    queryFn: pendingUploadsApi.list,
    refetchInterval: 10000, // Refresh every 10 seconds
  });

  // Fetch projects for dropdown
  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
  });

  // Archive mutation
  const archiveMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data?: { tags?: string; notes?: string; project_id?: number } }) =>
      pendingUploadsApi.archive(id, data),
    onMutate: ({ id }) => {
      setArchivingIds((prev) => new Set(prev).add(id));
    },
    onSettled: (_, __, { id }) => {
      setArchivingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['pending-uploads'] });
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(`Archived: ${data.print_name}`);
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to archive', 'error');
    },
  });

  // Discard mutation
  const discardMutation = useMutation({
    mutationFn: (id: number) => pendingUploadsApi.discard(id),
    onMutate: (id) => {
      setDiscardingIds((prev) => new Set(prev).add(id));
    },
    onSettled: (_, __, id) => {
      setDiscardingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pending-uploads'] });
      showToast('Upload discarded');
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to discard', 'error');
    },
  });

  // Archive all mutation
  const archiveAllMutation = useMutation({
    mutationFn: pendingUploadsApi.archiveAll,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['pending-uploads'] });
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(`Archived ${data.archived} files${data.failed > 0 ? `, ${data.failed} failed` : ''}`);
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to archive all', 'error');
    },
  });

  // Discard all mutation
  const discardAllMutation = useMutation({
    mutationFn: pendingUploadsApi.discardAll,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['pending-uploads'] });
      showToast(`Discarded ${data.discarded} files`);
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to discard all', 'error');
    },
  });

  if (uploadsLoading) {
    return (
      <Card>
        <CardContent className="py-8 flex justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
        </CardContent>
      </Card>
    );
  }

  if (!uploads || uploads.length === 0) {
    return null; // Don't render if no pending uploads
  }

  return (
    <div className="mb-6">
      <Card className="border-l-4 border-l-yellow-500">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Upload className="w-5 h-5 text-yellow-500" />
              <h2 className="text-lg font-semibold text-white">
                Pending Uploads ({uploads.length})
              </h2>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="primary"
                size="sm"
                onClick={() => setShowArchiveAllConfirm(true)}
                disabled={archiveAllMutation.isPending}
              >
                {archiveAllMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <>
                    <Archive className="w-4 h-4" />
                    Archive All
                  </>
                )}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowDiscardAllConfirm(true)}
                disabled={discardAllMutation.isPending}
              >
                {discardAllMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    Discard All
                  </>
                )}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-bambu-gray mb-4">
            These files were uploaded via the virtual printer. Review and archive them to add to your collection.
          </p>
          <div className="space-y-3">
            {uploads.map((upload) => (
              <PendingUploadItem
                key={upload.id}
                upload={upload}
                projects={projects || []}
                onArchive={(id, data) => archiveMutation.mutate({ id, data })}
                onDiscard={(id) => discardMutation.mutate(id)}
                isArchiving={archivingIds.has(upload.id)}
                isDiscarding={discardingIds.has(upload.id)}
              />
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Archive All Confirmation */}
      {showArchiveAllConfirm && (
        <ConfirmModal
          title="Archive All Uploads"
          message={`Are you sure you want to archive all ${uploads.length} pending uploads?`}
          confirmText="Archive All"
          onConfirm={() => {
            archiveAllMutation.mutate();
            setShowArchiveAllConfirm(false);
          }}
          onCancel={() => setShowArchiveAllConfirm(false)}
        />
      )}

      {/* Discard All Confirmation */}
      {showDiscardAllConfirm && (
        <ConfirmModal
          title="Discard All Uploads"
          message={`Are you sure you want to discard all ${uploads.length} pending uploads? This cannot be undone.`}
          confirmText="Discard All"
          variant="danger"
          onConfirm={() => {
            discardAllMutation.mutate();
            setShowDiscardAllConfirm(false);
          }}
          onCancel={() => setShowDiscardAllConfirm(false)}
        />
      )}
    </div>
  );
}
