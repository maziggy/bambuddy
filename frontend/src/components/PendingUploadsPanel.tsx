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
import { useTranslation } from 'react-i18next';

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTimeAgo(dateStr: string, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return t('pendingUploads.justNow');
  if (diffMins < 60) return t('pendingUploads.minutesAgo', { count: diffMins });
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return t('pendingUploads.hoursAgo', { count: diffHours });
  const diffDays = Math.floor(diffHours / 24);
  return t('pendingUploads.daysAgo', { count: diffDays });
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
  const { t } = useTranslation();
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
                  {formatTimeAgo(upload.uploaded_at, t)}
                </span>
                {upload.source_ip && (
                  <>
                    <span>·</span>
                    <span>{t('pendingUploads.fromSource', { ip: upload.source_ip })}</span>
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
                  {t('pendingUploads.archive')}
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
            title={t('pendingUploads.discardTitle')}
            message={t('pendingUploads.discardConfirm', { filename: upload.filename })}
            confirmText={t('pendingUploads.discard')}
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
              <label className="block text-sm text-bambu-gray mb-1">{t('pendingUploads.tags')}</label>
              <input
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder={t('pendingUploads.tagsPlaceholder')}
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white placeholder-bambu-gray text-sm"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('pendingUploads.notes')}</label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder={t('pendingUploads.notesPlaceholder')}
                rows={2}
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white placeholder-bambu-gray text-sm resize-none"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('pendingUploads.project')}</label>
              <select
                value={projectId || ''}
                onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)}
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md px-3 py-2 text-white text-sm"
              >
                <option value="">{t('pendingUploads.noProject')}</option>
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
  const { t } = useTranslation();
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
      showToast(t('pendingUploads.archived', { name: data.print_name }));
    },
    onError: (error: Error) => {
      showToast(error.message || t('pendingUploads.archiveFailed'), 'error');
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
      showToast(t('pendingUploads.discarded'));
    },
    onError: (error: Error) => {
      showToast(error.message || t('pendingUploads.discardFailed'), 'error');
    },
  });

  // Archive all mutation
  const archiveAllMutation = useMutation({
    mutationFn: pendingUploadsApi.archiveAll,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['pending-uploads'] });
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('pendingUploads.archivedCount', { count: data.archived, failed: data.failed }));
    },
    onError: (error: Error) => {
      showToast(error.message || t('pendingUploads.archiveAllFailed'), 'error');
    },
  });

  // Discard all mutation
  const discardAllMutation = useMutation({
    mutationFn: pendingUploadsApi.discardAll,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['pending-uploads'] });
      showToast(t('pendingUploads.discardedCount', { count: data.discarded }));
    },
    onError: (error: Error) => {
      showToast(error.message || t('pendingUploads.discardAllFailed'), 'error');
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
                {t('pendingUploads.title', { count: uploads.length })}
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
                    {t('pendingUploads.archiveAll')}
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
                    {t('pendingUploads.discardAll')}
                  </>
                )}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-bambu-gray mb-4">
            {t('pendingUploads.description')}
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
          title={t('pendingUploads.archiveAllTitle')}
          message={t('pendingUploads.archiveAllConfirm', { count: uploads.length })}
          confirmText={t('pendingUploads.archiveAll')}
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
          title={t('pendingUploads.discardAllTitle')}
          message={t('pendingUploads.discardAllConfirm', { count: uploads.length })}
          confirmText={t('pendingUploads.discardAll')}
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
