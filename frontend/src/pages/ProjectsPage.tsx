import { useState, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  FolderKanban,
  Loader2,
  Plus,
  Trash2,
  Edit3,
  Archive,
  ListTodo,
  Package,
  Layers,
  FolderTree,
  Clock,
  CheckCircle2,
  AlertTriangle,
  ChevronRight,
  MoreVertical,
  Download,
  Upload,
  ExternalLink,
  Image as ImageIcon,
  X,
} from 'lucide-react';
import { api } from '../api/client';
import type { ProjectListItem, ProjectCreate, ProjectUpdate, ProjectImport, Permission } from '../api/client';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { getCurrencySymbol } from '../utils/currency';
import { eligibleParents } from '../utils/projectTree';

const PROJECT_COLORS = [
  '#ef4444', // red
  '#f97316', // orange
  '#eab308', // yellow
  '#22c55e', // green
  '#06b6d4', // cyan
  '#3b82f6', // blue
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#6b7280', // gray
];

type TFunction = (key: string, options?: Record<string, unknown>) => string;

interface ProjectModalProps {
  project?: ProjectListItem;
  onClose: () => void;
  onSave: (data: ProjectCreate | ProjectUpdate) => void;
  isLoading: boolean;
  currencySymbol: string;
  t: TFunction;
}

export function ProjectModal({ project, onClose, onSave, isLoading, currencySymbol, t }: ProjectModalProps) {
  const [name, setName] = useState(project?.name || '');
  const [description, setDescription] = useState(project?.description || '');
  const [color, setColor] = useState(project?.color || PROJECT_COLORS[0]);
  const [targetCount, setTargetCount] = useState(project?.target_count?.toString() || '');
  const [targetPartsCount, setTargetPartsCount] = useState(project?.target_parts_count?.toString() || '');
  const [targetSets, setTargetSets] = useState(project?.target_sets?.toString() || '');
  const [status, setStatus] = useState(project?.status || 'active');
  const [tags, setTags] = useState(project?.tags || '');
  const [dueDate, setDueDate] = useState(project?.due_date?.split('T')[0] || '');
  const [priority, setPriority] = useState(project?.priority || 'normal');
  const [budget, setBudget] = useState(project?.budget?.toString() || '');
  const [url, setUrl] = useState(project?.url || '');
  const [urlError, setUrlError] = useState<string | null>(null);
  const [parentId, setParentId] = useState<number | null>(project?.parent_id ?? null);
  const queryClient = useQueryClient();

  // Unfiltered on purpose: a completed or archived project is still a legal
  // parent, and the picker offering fewer options than the list does would be
  // hard to explain.
  const { data: allProjects } = useQuery({
    queryKey: ['projects', undefined],
    queryFn: () => api.getProjects(),
  });
  const parentOptions = eligibleParents(allProjects || [], project?.id);
  const [coverImageFilename, setCoverImageFilename] = useState(project?.cover_image_filename || null);
  const coverFileInputRef = useRef<HTMLInputElement>(null);
  const [coverUploading, setCoverUploading] = useState(false);
  // Cache-bust the cover image URL when it changes mid-edit so the preview
  // refreshes after upload/remove.
  const [coverCacheKey, setCoverCacheKey] = useState(0);

  const handleCoverFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !project) return;
    setCoverUploading(true);
    try {
      const result = await api.uploadProjectCoverImage(project.id, file);
      setCoverImageFilename(result.filename);
      setCoverCacheKey((k) => k + 1);
      queryClient.invalidateQueries({ queryKey: ['projects'] });
    } catch {
      // Upload failed — leave existing cover image in place.
    } finally {
      setCoverUploading(false);
      if (coverFileInputRef.current) coverFileInputRef.current.value = '';
    }
  };

  const handleRemoveCover = async () => {
    if (!project) return;
    setCoverUploading(true);
    try {
      await api.deleteProjectCoverImage(project.id);
      setCoverImageFilename(null);
      setCoverCacheKey((k) => k + 1);
      queryClient.invalidateQueries({ queryKey: ['projects'] });
    } finally {
      setCoverUploading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedUrl = url.trim();
    if (trimmedUrl && !/^https?:\/\//i.test(trimmedUrl)) {
      setUrlError(t('projects.urlInvalid'));
      return;
    }
    setUrlError(null);
    onSave({
      name: name.trim(),
      description: description.trim() || undefined,
      color,
      target_count: targetCount ? parseInt(targetCount, 10) : undefined,
      target_parts_count: targetPartsCount ? parseInt(targetPartsCount, 10) : undefined,
      // Null clears the copies-per-file target on edit (#1897); undefined omits on create.
      target_sets: project ? (targetSets ? parseInt(targetSets, 10) : null) : (targetSets ? parseInt(targetSets, 10) : undefined),
      // Null clears the stored value on edit; undefined omits the key on create.
      // Sending undefined on edit would make an emptied field un-clearable.
      tags: project ? (tags.trim() || null) : (tags.trim() || undefined),
      due_date: project ? (dueDate || null) : (dueDate || undefined),
      priority,
      budget: budget.trim() ? parseFloat(budget) : null,
      // Pydantic accepts null to clear the URL; an empty string would fail the
      // http(s) prefix validator.
      url: project ? (trimmedUrl || null) : (trimmedUrl || undefined),
      // The API reads 0 as "remove the parent" — null would be indistinguishable
      // from the field having been omitted (#1264).
      parent_id: project ? (parentId ?? 0) : (parentId ?? undefined),
      ...(project && { status }),
    });
  };

  return (
    // max-h + flex column on the card + overflow on the fields wrapper so the
    // modal stays inside the viewport on short screens (#1642). Outer p-4 is
    // 1rem each side, hence the 2rem subtraction below.
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-md border border-bambu-dark-tertiary flex flex-col max-h-[calc(100vh-2rem)]">
        <div className="p-4 border-b border-bambu-dark-tertiary flex-shrink-0">
          <h2 className="text-lg font-semibold text-white">
            {project ? t('projects.editProject') : t('projects.newProject')}
          </h2>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col flex-1 min-h-0">
          <div className="p-4 space-y-4 overflow-y-auto flex-1">
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('common.name')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
              placeholder={t('projects.namePlaceholder')}
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('common.description')}
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green resize-none"
              placeholder={t('projects.descriptionPlaceholder')}
              rows={2}
            />
          </div>

          {/* #1155: External URL */}
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('projects.urlLabel')}
            </label>
            <input
              type="url"
              value={url}
              onChange={(e) => { setUrl(e.target.value); if (urlError) setUrlError(null); }}
              className={`w-full bg-bambu-dark border rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none ${
                urlError ? 'border-red-500 focus:border-red-500' : 'border-bambu-dark-tertiary focus:border-bambu-green'
              }`}
              placeholder={t('projects.urlPlaceholder')}
              maxLength={2048}
            />
            {urlError && <p className="text-xs text-red-600 dark:text-red-400 mt-1">{urlError}</p>}
          </div>

          {/* #1264: Nest this project under another one */}
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('projects.parentLabel')}
            </label>
            <select
              value={parentId ?? ''}
              onChange={(e) => setParentId(e.target.value ? parseInt(e.target.value, 10) : null)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white focus:outline-none focus:border-bambu-green"
            >
              <option value="">{t('projects.parentNone')}</option>
              {parentOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
            </select>
            <p className="text-xs text-bambu-gray mt-1">{t('projects.parentHint')}</p>
          </div>

          {/* #1155: Cover image — only available when editing an existing project,
              since uploading needs a project_id. New projects can add it after save. */}
          {project && (
            <div>
              <label className="block text-sm font-medium text-white mb-1">
                {t('projects.coverImageLabel')}
              </label>
              <div className="flex items-center gap-3">
                <div className="w-20 h-20 rounded bg-bambu-dark border border-bambu-dark-tertiary overflow-hidden flex items-center justify-center flex-shrink-0">
                  {coverImageFilename ? (
                    <img
                      src={`${api.getProjectCoverImageUrl(project.id)}?v=${coverCacheKey}`}
                      alt={t('projects.coverImageAlt')}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <ImageIcon className="w-6 h-6 text-bambu-gray" />
                  )}
                </div>
                <div className="flex flex-col gap-2">
                  <input
                    ref={coverFileInputRef}
                    type="file"
                    accept="image/jpeg,image/png,image/gif,image/webp"
                    onChange={handleCoverFileChange}
                    className="hidden"
                  />
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => coverFileInputRef.current?.click()}
                    disabled={coverUploading}
                  >
                    {coverUploading ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Upload className="w-4 h-4 mr-1" />
                    )}
                    {coverImageFilename ? t('projects.coverImageReplace') : t('projects.coverImageUpload')}
                  </Button>
                  {coverImageFilename && (
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={handleRemoveCover}
                      disabled={coverUploading}
                    >
                      <X className="w-4 h-4 mr-1" />
                      {t('projects.coverImageRemove')}
                    </Button>
                  )}
                </div>
              </div>
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('projects.color')}
            </label>
            <div className="flex gap-2 flex-wrap">
              {PROJECT_COLORS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setColor(c)}
                  className={`w-8 h-8 rounded-full transition-transform ${
                    color === c ? 'ring-2 ring-white ring-offset-2 ring-offset-bambu-dark-secondary scale-110' : ''
                  }`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
          </div>

          {/* Target Counts - Plates and Parts side by side */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-white mb-1">
                {t('projects.targetPlates')}
              </label>
              <input
                type="number"
                value={targetCount}
                onChange={(e) => setTargetCount(e.target.value)}
                className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                placeholder={t('projects.targetPlatesPlaceholder')}
                min="1"
              />
              <p className="text-xs text-bambu-gray mt-1">{t('projects.targetPlatesHelp')}</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-white mb-1">
                {t('projects.targetParts')}
              </label>
              <input
                type="number"
                value={targetPartsCount}
                onChange={(e) => setTargetPartsCount(e.target.value)}
                className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                placeholder={t('projects.targetPartsPlaceholder')}
                min="1"
              />
              <p className="text-xs text-bambu-gray mt-1">{t('projects.targetPartsHelp')}</p>
            </div>
          </div>

          {/* Copies-per-file target (#1897) */}
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('projects.targetSets')}
            </label>
            <input
              type="number"
              value={targetSets}
              onChange={(e) => setTargetSets(e.target.value)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
              placeholder={t('projects.targetSetsPlaceholder')}
              min="1"
            />
            <p className="text-xs text-bambu-gray mt-1">{t('projects.targetSetsHelp')}</p>
          </div>

          {/* Tags */}
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('projects.tagsLabel')}
            </label>
            <input
              type="text"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
              placeholder={t('projects.tagsPlaceholder')}
            />
          </div>

          {/* Due Date and Priority in a row */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-white mb-1">
                {t('projects.dueDate')}
              </label>
              <input
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
                className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white focus:outline-none focus:border-bambu-green"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-white mb-1">
                {t('projects.priority')}
              </label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white focus:outline-none focus:border-bambu-green"
              >
                <option value="low">{t('projects.priorityLow')}</option>
                <option value="normal">{t('projects.priorityNormal')}</option>
                <option value="high">{t('projects.priorityHigh')}</option>
                <option value="urgent">{t('projects.priorityUrgent')}</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('projectDetail.cost.budget')}
            </label>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-bambu-gray pointer-events-none">
                {currencySymbol}
              </span>
              <input
                type="number"
                step="0.01"
                min="0"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded pl-8 pr-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                placeholder="0.00"
              />
            </div>
          </div>

          {project && (
            <div>
              <label className="block text-sm font-medium text-white mb-1">
                {t('common.status')}
              </label>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white focus:outline-none focus:border-bambu-green"
              >
                <option value="active">{t('projects.statusActive')}</option>
                <option value="completed">{t('projects.statusCompleted')}</option>
                <option value="archived">{t('projects.statusArchived')}</option>
              </select>
            </div>
          )}
          </div>

          {/* Sticky action footer — stays visible regardless of scroll
              position so Save/Cancel are always reachable on short screens
              (#1642). Buttons stay inside <form> for type="submit". */}
          <div className="flex justify-end gap-2 p-4 border-t border-bambu-dark-tertiary flex-shrink-0">
            <Button type="button" variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!name.trim() || isLoading}>
              {isLoading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : project ? (
                t('common.save')
              ) : (
                t('projects.create')
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * Cover thumbnail with portal-rendered hover preview (#1155 follow-up).
 *
 * Why a portal: the parent ``ProjectCard`` carries ``overflow-hidden`` for
 * its rounded-corner clipping and color accent bar; an in-tree popover
 * gets clipped by that and only the part that overlaps the card is
 * visible. Rendering the preview via ``createPortal`` to ``document.body``
 * escapes every ancestor clipping context, and ``position: fixed`` with
 * ``getBoundingClientRect()`` keeps it pinned next to the thumbnail
 * regardless of where the card sits in the grid.
 */
function ProjectCoverThumbnail({
  projectId,
  altText,
}: {
  projectId: number;
  altText: string;
}) {
  const thumbRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  const handleEnter = () => {
    if (!thumbRef.current) return;
    const rect = thumbRef.current.getBoundingClientRect();
    // Anchor the 384px preview just to the right of the thumbnail (8px gap).
    // Clamp ``top`` so the preview never overflows the viewport vertically;
    // similar story for ``left`` if the card is near the right edge — flip
    // to the LEFT side of the thumbnail in that case.
    const PREVIEW = 384;
    const GAP = 8;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = rect.right + GAP;
    if (left + PREVIEW > vw - 8) {
      left = rect.left - PREVIEW - GAP;
    }
    let top = rect.top;
    if (top + PREVIEW > vh - 8) {
      top = vh - PREVIEW - 8;
    }
    if (top < 8) top = 8;
    setPos({ left, top });
    setHovered(true);
  };

  const handleLeave = () => setHovered(false);

  return (
    <div
      ref={thumbRef}
      className="relative flex-shrink-0"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="w-10 h-10 rounded-lg overflow-hidden bg-bambu-dark border border-bambu-dark-tertiary">
        <img
          src={api.getProjectCoverImageUrl(projectId)}
          alt={altText}
          className="w-full h-full object-cover"
          loading="lazy"
        />
      </div>
      {hovered && pos &&
        createPortal(
          <div
            className="fixed z-[100] w-96 h-96 rounded-lg overflow-hidden border border-bambu-dark-tertiary shadow-2xl bg-bambu-dark pointer-events-none"
            style={{ left: pos.left, top: pos.top }}
            aria-hidden="true"
          >
            <img
              src={api.getProjectCoverImageUrl(projectId)}
              alt=""
              className="w-full h-full object-contain"
              loading="lazy"
            />
          </div>,
          document.body,
        )}
    </div>
  );
}


interface ProjectCardProps {
  project: ProjectListItem;
  parentName?: string;  // #1264 — resolved by the caller, which holds the whole list
  onClick: () => void;
  onEdit: () => void;
  onDelete: () => void;
  hasPermission: (permission: Permission) => boolean;
  t: TFunction;
}

function ProjectCard({ project, parentName, onClick, onEdit, onDelete, hasPermission, t }: ProjectCardProps) {
  // Plates progress: archive_count / target_count
  const platesProgressPercent = project.target_count
    ? Math.round((project.archive_count / project.target_count) * 100)
    : 0;
  // Parts progress: completed_count / target_parts_count
  const partsProgressPercent = project.target_parts_count
    ? Math.round((project.completed_count / project.target_parts_count) * 100)
    : 0;
  const isCompleted = project.status === 'completed';
  const isArchived = project.status === 'archived';
  const [showActions, setShowActions] = useState(false);

  // Status icon and color
  const getStatusConfig = () => {
    if (isCompleted) return { icon: CheckCircle2, color: 'text-bambu-green', bg: 'bg-bambu-green/10' };
    if (isArchived) return { icon: Archive, color: 'text-bambu-gray', bg: 'bg-bambu-gray/10' };
    if (project.queue_count > 0) return { icon: Clock, color: 'text-blue-600 dark:text-blue-400', bg: 'bg-blue-50 dark:bg-blue-400/10' };
    return { icon: FolderKanban, color: 'text-bambu-gray', bg: 'bg-bambu-gray/10' };
  };
  const statusConfig = getStatusConfig();

  return (
    <div
      className="group relative bg-gradient-to-br from-bambu-card to-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary hover:border-bambu-green/50 hover:shadow-lg hover:shadow-bambu-green/5 transition-all duration-300 cursor-pointer overflow-hidden"
      onClick={onClick}
    >
      {/* Color accent bar with glow */}
      <div
        className="absolute top-0 left-0 w-1.5 h-full"
        style={{
          backgroundColor: project.color || '#6b7280',
          boxShadow: `0 0 12px ${project.color || '#6b7280'}40`
        }}
      />

      <div className="p-5 pl-6">
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            {project.cover_image_filename ? (
              // #1155: cover photo replaces the status-icon box. The thumbnail
              // itself stays small so the card layout doesn't shift; on hover
              // a portal-rendered 384×384 preview pops out beside the card
              // so the user can identify the print without navigating into
              // the project view. The portal is needed because ProjectCard's
              // own ``overflow-hidden`` (for rounded corners) clips any
              // in-tree popover before it can extend outside the card.
              <ProjectCoverThumbnail
                projectId={project.id}
                altText={t('projects.coverImageAlt')}
              />
            ) : (
              <div className={`p-2 rounded-lg ${statusConfig.bg} flex-shrink-0`}>
                <statusConfig.icon className={`w-5 h-5 ${statusConfig.color}`} />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="font-semibold text-white truncate">{project.name}</h3>
                {project.url && (
                  <a
                    href={project.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    title={project.url}
                    aria-label={t('projects.openExternalUrl')}
                    className="inline-flex items-center justify-center w-6 h-6 rounded bg-bambu-dark border border-bambu-dark-tertiary text-bambu-green hover:bg-bambu-green/10 hover:border-bambu-green transition-colors flex-shrink-0"
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                )}
                {project.target_parts_count ? (
                  <span className={`text-xs px-2 py-0.5 rounded-full whitespace-nowrap font-medium ${
                    partsProgressPercent >= 100
                      ? 'bg-bambu-green/20 text-bambu-green'
                      : 'bg-bambu-dark text-bambu-gray'
                  }`}>
                    {project.completed_count}/{project.target_parts_count} {t('projects.parts')}
                  </span>
                ) : project.target_count ? (
                  <span className={`text-xs px-2 py-0.5 rounded-full whitespace-nowrap font-medium ${
                    platesProgressPercent >= 100
                      ? 'bg-bambu-green/20 text-bambu-green'
                      : 'bg-bambu-dark text-bambu-gray'
                  }`}>
                    {project.archive_count}/{project.target_count} {t('projects.plates')}
                  </span>
                ) : project.completed_count > 0 ? (
                  <span className="text-xs px-2 py-0.5 rounded-full whitespace-nowrap font-medium bg-bambu-dark text-bambu-gray">
                    {project.completed_count} {t('projects.parts')}
                  </span>
                ) : null}
                {isCompleted && (
                  <span className="text-xs bg-bambu-green/20 text-bambu-green px-2 py-0.5 rounded-full whitespace-nowrap">
                    {t('projects.done')}
                  </span>
                )}
                {isArchived && (
                  <span className="text-xs bg-bambu-gray/20 text-bambu-gray px-2 py-0.5 rounded-full whitespace-nowrap">
                    {t('projects.statusArchived')}
                  </span>
                )}
                {/* #1264: without these, eight sub-projects of one programme
                    look like eight unrelated projects in the grid. */}
                {project.child_count > 0 && (
                  <span
                    className="text-xs bg-bambu-dark text-bambu-gray px-2 py-0.5 rounded-full whitespace-nowrap inline-flex items-center gap-1"
                    title={t('projects.subProjectCount', { count: project.child_count })}
                  >
                    <FolderTree className="w-3 h-3" />
                    {project.child_count}
                  </span>
                )}
              </div>
              {parentName && (
                <p className="text-xs text-bambu-gray mt-1 flex items-center gap-1">
                  <Layers className="w-3 h-3 flex-shrink-0" />
                  <span className="truncate">{t('projects.partOf', { name: parentName })}</span>
                </p>
              )}
              {project.description && (
                <p className="text-sm text-bambu-gray/70 mt-1 line-clamp-1">
                  {project.description}
                </p>
              )}
              {/* Filament materials/colors */}
              {project.archives && project.archives.length > 0 && (() => {
                // Flatten comma-separated materials and deduplicate
                const allMaterials = project.archives
                  .map(a => a.filament_type)
                  .filter(Boolean)
                  .flatMap(m => (m as string).split(',').map(s => s.trim()))
                  .filter(Boolean);
                const materials = [...new Set(allMaterials)];
                // Flatten comma-separated colors and deduplicate
                const allColors = project.archives
                  .map(a => a.filament_color)
                  .filter(Boolean)
                  .flatMap(c => (c as string).split(',').map(s => s.trim()))
                  .filter(c => c.startsWith('#') || /^[0-9A-Fa-f]{6}$/.test(c));
                const colors = [...new Set(allColors)];
                if (materials.length === 0 && colors.length === 0) return null;
                return (
                  <div className="flex items-center gap-2 mt-1.5">
                    {/* Material types as text badges */}
                    {materials.slice(0, 3).map((mat) => (
                      <span key={mat} className="text-[10px] px-1.5 py-0.5 bg-bambu-dark text-bambu-gray rounded">
                        {mat}
                      </span>
                    ))}
                    {/* Colors as swatches */}
                    {colors.length > 0 && (
                      <div className="flex items-center gap-0.5">
                        {colors.slice(0, 5).map((col) => (
                          <div
                            key={col}
                            className="w-3 h-3 rounded-full border border-black/20"
                            style={{ backgroundColor: col.startsWith('#') ? col : `#${col}` }}
                            title={col}
                          />
                        ))}
                        {colors.length > 5 && (
                          <span className="text-[10px] text-bambu-gray ml-0.5">+{colors.length - 5}</span>
                        )}
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>
          </div>

          {/* Actions menu */}
          <div className="relative" onClick={(e) => e.stopPropagation()}>
            <button
              className="p-1.5 rounded-lg hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors opacity-0 group-hover:opacity-100"
              onClick={() => setShowActions(!showActions)}
            >
              <MoreVertical className="w-4 h-4" />
            </button>
            {showActions && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowActions(false)} />
                <div className="absolute right-0 top-8 z-20 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl py-1 min-w-[120px]">
                  <button
                    className={`w-full px-3 py-2 text-left text-sm flex items-center gap-2 ${
                      hasPermission('projects:update') ? 'text-white hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                    }`}
                    onClick={() => { if (hasPermission('projects:update')) { onEdit(); setShowActions(false); } }}
                    disabled={!hasPermission('projects:update')}
                    title={!hasPermission('projects:update') ? t('projects.noEditPermission') : undefined}
                  >
                    <Edit3 className="w-4 h-4" />
                    {t('common.edit')}
                  </button>
                  <button
                    className={`w-full px-3 py-2 text-left text-sm flex items-center gap-2 ${
                      hasPermission('projects:delete') ? 'text-red-600 dark:text-red-400 hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                    }`}
                    onClick={() => { if (hasPermission('projects:delete')) { onDelete(); setShowActions(false); } }}
                    disabled={!hasPermission('projects:delete')}
                    title={!hasPermission('projects:delete') ? t('projects.noDeletePermission') : undefined}
                  >
                    <Trash2 className="w-4 h-4" />
                    {t('common.delete')}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>

        {/* Progress section - show for all projects */}
        <div className="mb-4">
          {(project.target_count || project.target_parts_count) ? (
            <div className="space-y-3">
              {/* Plates progress */}
              {project.target_count && (
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-bambu-gray">{t('projects.plates')}</span>
                    <span className={platesProgressPercent >= 100 ? 'text-bambu-green font-medium' : 'text-white'}>
                      {project.archive_count} / {project.target_count}
                    </span>
                  </div>
                  <div className="h-2 bg-bambu-dark/80 rounded-full overflow-hidden backdrop-blur-sm">
                    <div
                      className="h-full transition-all duration-500 ease-out rounded-full relative"
                      style={{
                        width: `${Math.min(platesProgressPercent, 100)}%`,
                        background: platesProgressPercent >= 100
                          ? 'linear-gradient(90deg, #22c55e, #4ade80)'
                          : `linear-gradient(90deg, ${project.color || '#6b7280'}, ${project.color || '#6b7280'}cc)`,
                        boxShadow: `0 0 8px ${platesProgressPercent >= 100 ? '#22c55e' : project.color || '#6b7280'}60`
                      }}
                    />
                  </div>
                </div>
              )}
              {/* Parts progress */}
              {project.target_parts_count && (
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-bambu-gray">{t('projects.parts')}</span>
                    <span className={partsProgressPercent >= 100 ? 'text-bambu-green font-medium' : 'text-white'}>
                      {project.completed_count} / {project.target_parts_count}
                    </span>
                  </div>
                  <div className="h-2 bg-bambu-dark/80 rounded-full overflow-hidden backdrop-blur-sm">
                    <div
                      className="h-full transition-all duration-500 ease-out rounded-full relative"
                      style={{
                        width: `${Math.min(partsProgressPercent, 100)}%`,
                        background: partsProgressPercent >= 100
                          ? 'linear-gradient(90deg, #22c55e, #4ade80)'
                          : `linear-gradient(90deg, ${project.color || '#6b7280'}, ${project.color || '#6b7280'}cc)`,
                        boxShadow: `0 0 8px ${partsProgressPercent >= 100 ? '#22c55e' : project.color || '#6b7280'}60`
                      }}
                    />
                  </div>
                </div>
              )}
              {/* Failed count */}
              {project.failed_count > 0 && (
                <div className="text-xs text-red-600 dark:text-red-400">
                  {project.failed_count} {t('projects.failed')}
                </div>
              )}
            </div>
          ) : project.completed_count > 0 || project.failed_count > 0 ? (
            <div className="flex items-center gap-4 text-xs">
              {project.completed_count > 0 && (
                <div className="flex items-center gap-1.5 text-bambu-gray">
                  <Archive className="w-3.5 h-3.5" />
                  <span>{project.completed_count} {t('projects.completed')}</span>
                </div>
              )}
              {project.failed_count > 0 && (
                <div className="flex items-center gap-1.5 text-red-600 dark:text-red-400">
                  <AlertTriangle className="w-3.5 h-3.5" />
                  <span>{project.failed_count} {t('projects.failed')}</span>
                </div>
              )}
              {project.queue_count > 0 && (
                <div className="flex items-center gap-1.5 text-blue-600 dark:text-blue-400">
                  <Clock className="w-3.5 h-3.5" />
                  <span>{project.queue_count} {t('projects.inQueue')}</span>
                </div>
              )}
            </div>
          ) : (
            <div className="text-xs text-bambu-gray/60 italic">
              {t('projects.noPrintsYet')}
            </div>
          )}
        </div>

        {/* Archive thumbnails - compact 4-column grid */}
        {project.archives && project.archives.length > 0 && (
          <div className="mb-4">
            <div className="grid grid-cols-4 gap-1.5">
              {project.archives.slice(0, 4).map((archive) => (
                <div
                  key={archive.id}
                  className="relative aspect-square rounded-lg bg-bambu-dark overflow-hidden border border-bambu-dark-tertiary"
                  title={archive.print_name || 'Unknown'}
                >
                  {archive.thumbnail_path ? (
                    <img
                      src={api.getArchiveThumbnail(archive.id)}
                      alt={archive.print_name || ''}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-bambu-gray/50">
                      <Package className="w-6 h-6" />
                    </div>
                  )}
                  {archive.status === 'failed' && (
                    <div className="absolute inset-0 bg-red-500/40 flex items-center justify-center">
                      <AlertTriangle className="w-4 h-4 text-white" />
                    </div>
                  )}
                </div>
              ))}
            </div>
            {project.archive_count > 4 && (
              <p className="text-xs text-bambu-gray mt-1.5 text-center">
                {t('common.more', { count: project.archive_count - 4 })}
              </p>
            )}
          </div>
        )}

        {/* Stats footer */}
        <div className="flex items-center justify-between pt-3 border-t border-bambu-dark-tertiary">
          <div className="flex items-center gap-4 text-xs text-bambu-gray">
            <div className="flex items-center gap-1.5" title={t('projects.printJobs')}>
              <Layers className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
              <span>{project.archive_count} {t('projects.plates')}</span>
            </div>
            <div className="flex items-center gap-1.5" title={t('projects.partsPrinted')}>
              <Package className="w-3.5 h-3.5 text-bambu-green" />
              <span>{project.completed_count} {t('projects.parts')}</span>
            </div>
            {project.failed_count > 0 && (
              <div className="flex items-center gap-1.5 text-red-600 dark:text-red-400" title={t('projects.failedParts')}>
                <AlertTriangle className="w-3.5 h-3.5" />
                <span>{project.failed_count}</span>
              </div>
            )}
            {project.queue_count > 0 && (
              <div className="flex items-center gap-1.5 text-yellow-600 dark:text-yellow-400" title={t('projects.inQueue')}>
                <ListTodo className="w-3.5 h-3.5" />
                <span>{project.queue_count}</span>
              </div>
            )}
          </div>
          <ChevronRight className="w-4 h-4 text-bambu-gray/50 group-hover:text-bambu-gray transition-colors" />
        </div>
      </div>
    </div>
  );
}

export function ProjectsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const [showModal, setShowModal] = useState(false);
  const [editingProject, setEditingProject] = useState<ProjectListItem | undefined>();
  const [statusFilter, setStatusFilter] = useState<string>('active');
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null);

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const currencySymbol = getCurrencySymbol(settings?.currency || 'USD');

  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects', statusFilter === 'all' ? undefined : statusFilter],
    queryFn: () => api.getProjects(statusFilter === 'all' ? undefined : statusFilter),
  });

  // Parent names come from the unfiltered list, so a sub-project still says
  // what it belongs to when the status filter has hidden its parent (#1264).
  // Same query key the parent picker uses — one request shared between them,
  // and the same one as above whenever the filter is 'all'.
  const { data: allProjects } = useQuery({
    queryKey: ['projects', undefined],
    queryFn: () => api.getProjects(),
  });
  const namesById = new Map((allProjects || []).map((p) => [p.id, p.name]));

  // A sub-project is drawn inside its parent's group rather than as another
  // card somewhere in the grid — two cards that belong together are not
  // something a caption can convey when they sit columns apart (#1264).
  const visible = projects || [];
  const visibleIds = new Set(visible.map((p) => p.id));
  const childrenByParent = new Map<number, ProjectListItem[]>();
  for (const project of visible) {
    // Parent hidden by the status filter: the child has nothing to nest under
    // here, so it stays a top-level card and keeps its "part of" caption.
    if (project.parent_id !== null && visibleIds.has(project.parent_id)) {
      const siblings = childrenByParent.get(project.parent_id) || [];
      siblings.push(project);
      childrenByParent.set(project.parent_id, siblings);
    }
  }
  // Everything drawn at the top of the grid, in order. Roots first, then any
  // project the roots cannot reach: a database written before the API refused
  // A -> B -> A can still hold a cycle, and a cycle has no root, so filtering
  // on "has no visible parent" alone would drop every project in it off the
  // page. Each entry point marks its whole branch as drawn, so nothing appears
  // twice either.
  const drawn = new Set<number>();
  const markDrawn = (id: number) => {
    if (drawn.has(id)) return;
    drawn.add(id);
    for (const child of childrenByParent.get(id) || []) markDrawn(child.id);
  };
  const topLevel: ProjectListItem[] = [];
  for (const project of visible) {
    if (project.parent_id !== null && visibleIds.has(project.parent_id)) continue;
    topLevel.push(project);
    markDrawn(project.id);
  }
  for (const project of visible) {
    if (drawn.has(project.id)) continue;
    topLevel.push(project);
    markDrawn(project.id);
  }

  const renderProjectTree = (project: ProjectListItem, depth: number, seen: Set<number>) => {
    const card = (
      <ProjectCard
        project={project}
        // Only when it is detached from its parent — inside the group the
        // nesting is already visible, and the caption would be noise.
        parentName={depth === 0 && project.parent_id !== null ? namesById.get(project.parent_id) : undefined}
        onClick={() => handleClick(project)}
        onEdit={() => handleEdit(project)}
        onDelete={() => handleDeleteClick(project.id)}
        hasPermission={hasPermission}
        t={t}
      />
    );

    // Drop any child that is already an ancestor on this path. A database
    // written before the API refused A -> B -> A can still hold a cycle, and
    // following one would draw the same project over and over.
    const descended = new Set(seen).add(project.id);
    const children = (childrenByParent.get(project.id) || []).filter((c) => !descended.has(c.id));
    if (children.length === 0) return <div key={project.id}>{card}</div>;

    return (
      <div key={project.id} className="col-span-full space-y-4">
        {card}
        <div
          className="ml-4 md:ml-8 pl-4 md:pl-6 border-l-2 rounded-l space-y-4"
          style={{ borderColor: project.color || '#6b7280' }}
        >
          <p className="text-xs uppercase tracking-wide text-bambu-gray flex items-center gap-1.5">
            <FolderTree className="w-3.5 h-3.5" />
            {t('projects.subProjectsOf', { name: project.name })}
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            {children.map((child) => renderProjectTree(child, depth + 1, descended))}
          </div>
        </div>
      </div>
    );
  };

  const createMutation = useMutation({
    mutationFn: (data: ProjectCreate) => api.createProject(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      setShowModal(false);
      showToast(t('projects.toast.created'), 'success');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: ProjectUpdate }) =>
      api.updateProject(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      setShowModal(false);
      setEditingProject(undefined);
      showToast(t('projects.toast.updated'), 'success');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteProject(id),
    onSuccess: () => {
      setDeleteConfirm(null);
      showToast(t('projects.toast.deleted'), 'success');
      // Reload to refresh the list (React Query cache invalidation not working reliably)
      setTimeout(() => window.location.reload(), 100);
    },
    onError: (error: Error) => {
      setDeleteConfirm(null);
      showToast(error.message, 'error');
    },
  });

  const importMutation = useMutation({
    mutationFn: (data: ProjectImport) => api.importProject(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      showToast(t('projects.toast.imported'), 'success');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleExportAll = async () => {
    try {
      // Export all projects as JSON (metadata only, no files)
      const allProjects = await api.getProjects();
      const exports = await Promise.all(
        allProjects.map(async (p) => {
          const exported = await api.exportProjectJson(p.id);
          return exported;
        })
      );
      const blob = new Blob([JSON.stringify(exports, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `bambuddy_projects_${new Date().toISOString().split('T')[0]}.json`;
      a.click();
      URL.revokeObjectURL(url);
      showToast(t('projects.toast.exported'), 'success');
    } catch (error) {
      showToast((error as Error).message, 'error');
    }
  };

  const handleImportClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const filename = file.name.toLowerCase();

      if (filename.endsWith('.zip')) {
        // ZIP file: upload via file endpoint
        await api.importProjectFile(file);
        queryClient.invalidateQueries({ queryKey: ['projects'] });
        showToast(t('projects.toast.imported'), 'success');
      } else {
        // JSON file: parse and handle bulk or single import
        const text = await file.text();
        const data = JSON.parse(text);

        // Handle both single project and array of projects
        const projectsToImport = Array.isArray(data) ? data : [data];

        for (const project of projectsToImport) {
          await importMutation.mutateAsync(project);
        }

        if (projectsToImport.length > 1) {
          showToast(t('projects.toast.multipleImported', { count: projectsToImport.length }), 'success');
        }
      }
    } catch (error) {
      showToast(`${t('projects.toast.importFailed')}: ${(error as Error).message}`, 'error');
    }

    // Reset file input
    e.target.value = '';
  };

  const handleSave = (data: ProjectCreate | ProjectUpdate) => {
    if (editingProject) {
      updateMutation.mutate({ id: editingProject.id, data });
    } else {
      createMutation.mutate(data as ProjectCreate);
    }
  };

  const handleEdit = (project: ProjectListItem) => {
    setEditingProject(project);
    setShowModal(true);
  };

  const handleClick = (project: ProjectListItem) => {
    // Navigate to project detail page
    navigate(`/projects/${project.id}`);
  };

  const handleDeleteClick = (id: number) => {
    setDeleteConfirm(id);
  };

  const handleDeleteConfirm = () => {
    if (deleteConfirm !== null) {
      deleteMutation.mutate(deleteConfirm);
    }
  };

  // Count projects by status for filter badges
  const projectCounts = projects?.reduce((acc, p) => {
    acc[p.status] = (acc[p.status] || 0) + 1;
    acc.all = (acc.all || 0) + 1;
    return acc;
  }, {} as Record<string, number>) || {};

  return (
    <div className="p-4 md:p-8 space-y-8">
      {/* Hidden file input for import */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json,.zip"
        onChange={handleFileChange}
        className="hidden"
      />

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <FolderKanban className="w-7 h-7 text-bambu-green" />
            {t('projects.title')}
          </h1>
          <p className="text-bambu-gray mt-1">
            {t('projects.subtitle')}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            onClick={handleImportClick}
            disabled={!hasPermission('projects:create')}
            title={!hasPermission('projects:create') ? t('projects.noImportPermission') : t('projects.importProject')}
          >
            <Upload className="w-4 h-4 mr-2" />
            {t('projects.import')}
          </Button>
          <Button
            variant="secondary"
            onClick={handleExportAll}
            disabled={!hasPermission('projects:read')}
            title={!hasPermission('projects:read') ? t('projects.noExportPermission') : t('projects.exportAll')}
          >
            <Download className="w-4 h-4 mr-2" />
            {t('projects.export')}
          </Button>
          <Button
            onClick={() => setShowModal(true)}
            className="sm:w-auto w-full"
            disabled={!hasPermission('projects:create')}
            title={!hasPermission('projects:create') ? t('projects.noCreatePermission') : undefined}
          >
            <Plus className="w-4 h-4 mr-2" />
            {t('projects.newProject')}
          </Button>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 p-1 bg-bambu-dark rounded-xl w-fit">
        {[
          { key: 'active', label: t('projects.statusActive'), icon: Clock },
          { key: 'completed', label: t('projects.statusCompleted'), icon: CheckCircle2 },
          { key: 'archived', label: t('projects.statusArchived'), icon: Archive },
          { key: 'all', label: t('common.all'), icon: FolderKanban },
        ].map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setStatusFilter(key)}
            className={`flex items-center gap-2 px-4 py-2 text-sm rounded-lg transition-all ${
              statusFilter === key
                ? 'bg-bambu-card text-white shadow-sm'
                : 'text-bambu-gray hover:text-white'
            }`}
          >
            <Icon className="w-4 h-4" />
            <span>{label}</span>
            {projectCounts[key] > 0 && (
              <span className={`text-xs px-1.5 py-0.5 rounded-full ${
                statusFilter === key ? 'bg-bambu-green/20 text-bambu-green' : 'bg-bambu-dark-tertiary'
              }`}>
                {projectCounts[key]}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
            <p className="text-sm text-bambu-gray">{t('projects.loading')}</p>
          </div>
        </div>
      ) : projects?.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 px-4">
          <div className="p-4 bg-bambu-dark rounded-2xl mb-4">
            <FolderKanban className="w-12 h-12 text-bambu-gray/50" />
          </div>
          <h3 className="text-lg font-medium text-white mb-2">
            {statusFilter === 'all' ? t('projects.noProjects') : t('projects.noProjectsFiltered', { status: statusFilter })}
          </h3>
          <p className="text-bambu-gray text-center max-w-md mb-6">
            {statusFilter === 'all'
              ? t('projects.createFirst')
              : t('projects.noProjectsFilteredHelp', { status: statusFilter })
            }
          </p>
          {statusFilter === 'all' && (
            <Button
              onClick={() => setShowModal(true)}
              disabled={!hasPermission('projects:create')}
              title={!hasPermission('projects:create') ? t('projects.noCreatePermission') : undefined}
            >
              <Plus className="w-4 h-4 mr-2" />
              {t('projects.createFirstButton')}
            </Button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-8">
          {topLevel.map((project) => renderProjectTree(project, 0, new Set()))}
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteConfirm !== null && (
        <ConfirmModal
          title={t('projects.deleteProject')}
          message={t('projects.deleteConfirm')}
          confirmText={t('projects.deleteProject')}
          variant="danger"
          onConfirm={handleDeleteConfirm}
          onCancel={() => setDeleteConfirm(null)}
        />
      )}

      {/* Modal */}
      {showModal && (
        <ProjectModal
          project={editingProject}
          onClose={() => {
            setShowModal(false);
            setEditingProject(undefined);
          }}
          onSave={handleSave}
          isLoading={createMutation.isPending || updateMutation.isPending}
          currencySymbol={currencySymbol}
          t={t}
        />
      )}
    </div>
  );
}
