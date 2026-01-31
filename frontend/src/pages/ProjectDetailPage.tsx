import { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Edit3,
  Loader2,
  Package,
  Clock,
  CheckCircle,
  XCircle,
  ListTodo,
  Printer,
  ChevronRight,
  FileText,
  Tag,
  Calendar,
  AlertTriangle,
  Save,
  X,
  Trash2,
  Plus,
  History,
  FolderTree,
  Copy,
  Layers,
  ExternalLink,
  ShoppingCart,
  FolderOpen,
} from 'lucide-react';
import { api } from '../api/client';
import { parseUTCDate, formatDateOnly, formatDateTime, type TimeFormat } from '../utils/date';
import type { Archive, ProjectUpdate, BOMItem, BOMItemCreate } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { RichTextEditor } from '../components/RichTextEditor';
import { ConfirmModal } from '../components/ConfirmModal';

// Project edit modal (reused from ProjectsPage)
import { ProjectModal } from './ProjectsPage';

function formatDuration(hours: number): string {
  if (hours < 1) {
    return `${Math.round(hours * 60)}m`;
  }
  const h = Math.floor(hours);
  const m = Math.round((hours - h) * 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function formatFilament(grams: number): string {
  if (grams >= 1000) {
    return `${(grams / 1000).toFixed(2)}kg`;
  }
  return `${Math.round(grams)}g`;
}

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  const colors = {
    active: 'bg-bambu-green/20 text-bambu-green',
    completed: 'bg-blue-500/20 text-blue-400',
    archived: 'bg-bambu-gray/20 text-bambu-gray',
  };
  const color = colors[status as keyof typeof colors] || colors.active;

  const labels: Record<string, string> = {
    active: t('projectDetail.statusActive'),
    completed: t('projectDetail.statusCompleted'),
    archived: t('projectDetail.statusArchived'),
  };

  return (
    <span className={`px-2 py-1 rounded text-sm font-medium ${color}`}>
      {labels[status] || (status.charAt(0).toUpperCase() + status.slice(1))}
    </span>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  subValue,
  hint,
  color = 'text-bambu-gray',
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  subValue?: string;
  hint?: string;
  color?: string;
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center gap-3" title={hint}>
          <div className={`p-2 rounded-lg bg-bambu-dark ${color}`}>
            <Icon className="w-5 h-5" />
          </div>
          <div>
            <p className="text-sm text-bambu-gray">{label}</p>
            <p className="text-xl font-semibold text-white">{value}</p>
            {subValue && <p className="text-xs text-bambu-gray/70">{subValue}</p>}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ArchiveGrid({ archives }: { archives: Archive[] }) {
  const { t } = useTranslation();
  if (archives.length === 0) {
    return (
      <div className="text-center py-8 text-bambu-gray">
        <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
        <p>{t('projectDetail.noPrintsYet')}</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
      {archives.map((archive) => (
        <Link
          key={archive.id}
          to={`/archives?search=${encodeURIComponent(archive.print_name || '')}`}
          className="group relative aspect-square rounded-lg bg-bambu-dark border border-bambu-dark-tertiary overflow-hidden hover:border-bambu-green transition-colors"
        >
          {archive.thumbnail_path ? (
            <img
              src={api.getArchiveThumbnail(archive.id)}
              alt={archive.print_name || t('projectDetail.print')}
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-bambu-gray">
              <Package className="w-8 h-8" />
            </div>
          )}

          {/* Status overlay */}
          {archive.status === 'failed' && (
            <div className="absolute inset-0 bg-red-500/30 flex items-center justify-center">
              <XCircle className="w-8 h-8 text-white" />
            </div>
          )}
          {archive.status === 'completed' && (
            <div className="absolute top-1 right-1">
              <CheckCircle className="w-4 h-4 text-bambu-green" />
            </div>
          )}

          {/* Name overlay on hover */}
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent p-2 opacity-0 group-hover:opacity-100 transition-opacity">
            <p className="text-xs text-white truncate">{archive.print_name || t('projectDetail.unknown')}</p>
          </div>
        </Link>
      ))}
    </div>
  );
}

function PriorityBadge({ priority }: { priority: string }) {
  const { t } = useTranslation();
  const config = {
    low: { color: 'bg-gray-500/20 text-gray-400', label: t('projectDetail.priorityLow') },
    normal: { color: 'bg-blue-500/20 text-blue-400', label: t('projectDetail.priorityNormal') },
    high: { color: 'bg-orange-500/20 text-orange-400', label: t('projectDetail.priorityHigh') },
    urgent: { color: 'bg-red-500/20 text-red-400', label: t('projectDetail.priorityUrgent') },
  };
  const { color, label } = config[priority as keyof typeof config] || config.normal;

  return (
    <span className={`px-2 py-1 rounded text-xs font-medium flex items-center gap-1 ${color}`}>
      {priority === 'urgent' && <AlertTriangle className="w-3 h-3" />}
      {label}
    </span>
  );
}

function formatDate(dateString: string | null): string {
  if (!dateString) return '';
  return formatDateOnly(dateString, { year: 'numeric', month: 'short', day: 'numeric' });
}

function getDueDateStatus(dateString: string | null, t: (key: string, options?: Record<string, unknown>) => string): { color: string; label: string } | null {
  if (!dateString) return null;
  const dueDate = parseUTCDate(dateString);
  if (!dueDate) return null;
  const now = new Date();
  const diffDays = Math.ceil((dueDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));

  if (diffDays < 0) return { color: 'text-red-400', label: t('projectDetail.overdue') };
  if (diffDays === 0) return { color: 'text-orange-400', label: t('projectDetail.dueToday') };
  if (diffDays <= 3) return { color: 'text-yellow-400', label: t('projectDetail.daysLeft', { count: diffDays }) };
  return { color: 'text-bambu-gray', label: t('projectDetail.daysLeft', { count: diffDays }) };
}

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [showEditModal, setShowEditModal] = useState(false);
  const [editingNotes, setEditingNotes] = useState(false);
  const [notesContent, setNotesContent] = useState('');

  const projectId = parseInt(id || '0', 10);

  const { data: project, isLoading: projectLoading, error: projectError } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId),
    enabled: projectId > 0,
  });

  const { data: archives, isLoading: archivesLoading } = useQuery({
    queryKey: ['project-archives', projectId],
    queryFn: () => api.getProjectArchives(projectId),
    enabled: projectId > 0,
  });

  const { data: bomItems, isLoading: bomLoading } = useQuery({
    queryKey: ['project-bom', projectId],
    queryFn: () => api.getProjectBOM(projectId),
    enabled: projectId > 0,
  });

  const { data: timeline, isLoading: timelineLoading } = useQuery({
    queryKey: ['project-timeline', projectId],
    queryFn: () => api.getProjectTimeline(projectId, 20),
    enabled: projectId > 0,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const { data: linkedFolders } = useQuery({
    queryKey: ['project-folders', projectId],
    queryFn: () => api.getLibraryFoldersByProject(projectId),
    enabled: projectId > 0,
  });

  const currency = settings?.currency || '$';
  const timeFormat: TimeFormat = settings?.time_format || 'system';

  const updateMutation = useMutation({
    mutationFn: (data: ProjectUpdate) => api.updateProject(projectId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      setShowEditModal(false);
      setEditingNotes(false);
      showToast(t('projectDetail.projectUpdated'), 'success');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const handleStartEditNotes = () => {
    setNotesContent(project?.notes || '');
    setEditingNotes(true);
  };

  const handleSaveNotes = () => {
    updateMutation.mutate({ notes: notesContent });
  };

  const handleCancelNotes = () => {
    setEditingNotes(false);
    setNotesContent('');
  };

  // BOM handlers
  const [newBomName, setNewBomName] = useState('');
  const [newBomQty, setNewBomQty] = useState(1);
  const [newBomPrice, setNewBomPrice] = useState('');
  const [newBomUrl, setNewBomUrl] = useState('');
  const [newBomRemarks, setNewBomRemarks] = useState('');
  const [showBomForm, setShowBomForm] = useState(false);
  const [hideBomCompleted, setHideBomCompleted] = useState(false);

  // Confirm modal state
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean;
    title: string;
    message: string;
    onConfirm: () => void;
  }>({ isOpen: false, title: '', message: '', onConfirm: () => {} });

  const createBomMutation = useMutation({
    mutationFn: (data: BOMItemCreate) => api.createBOMItem(projectId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project-bom', projectId] });
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
      setNewBomName('');
      setNewBomQty(1);
      setNewBomPrice('');
      setNewBomUrl('');
      setNewBomRemarks('');
      setShowBomForm(false);
      showToast(t('projectDetail.partAdded'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const updateBomMutation = useMutation({
    mutationFn: ({ itemId, data }: { itemId: number; data: { quantity_acquired?: number } }) =>
      api.updateBOMItem(projectId, itemId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project-bom', projectId] });
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const deleteBomMutation = useMutation({
    mutationFn: (itemId: number) => api.deleteBOMItem(projectId, itemId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project-bom', projectId] });
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
      showToast(t('projectDetail.partRemoved'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const handleAddBomItem = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newBomName.trim()) return;
    createBomMutation.mutate({
      name: newBomName.trim(),
      quantity_needed: newBomQty,
      unit_price: newBomPrice ? parseFloat(newBomPrice) : undefined,
      sourcing_url: newBomUrl.trim() || undefined,
      remarks: newBomRemarks.trim() || undefined,
    });
  };

  const handleToggleAcquired = (item: BOMItem) => {
    const newQty = item.is_complete ? 0 : item.quantity_needed;
    updateBomMutation.mutate({
      itemId: item.id,
      data: { quantity_acquired: newQty },
    });
  };

  const handleDeleteBomItem = (itemId: number, itemName: string) => {
    setConfirmModal({
      isOpen: true,
      title: t('projectDetail.deletePart'),
      message: t('projectDetail.deletePartConfirm', { name: itemName }),
      onConfirm: () => {
        setConfirmModal(prev => ({ ...prev, isOpen: false }));
        deleteBomMutation.mutate(itemId);
      },
    });
  };

  // Template handlers
  const createTemplateMutation = useMutation({
    mutationFn: () => api.createTemplateFromProject(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      showToast(t('projectDetail.templateCreated'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const formatTimelineDate = (timestamp: string) => {
    return formatDateTime(timestamp, timeFormat, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  if (projectLoading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      </div>
    );
  }

  if (projectError || !project) {
    return (
      <div className="text-center py-24">
        <p className="text-bambu-gray">
          {projectError ? `${t('projectDetail.error')}: ${(projectError as Error).message}` : t('projectDetail.projectNotFound')}
        </p>
        <Button variant="secondary" className="mt-4" onClick={() => navigate('/projects')}>
          {t('projectDetail.backToProjects')}
        </Button>
      </div>
    );
  }

  const stats = project.stats;
  // Plates progress: total_archives / target_count
  const platesProgressPercent = stats?.progress_percent ?? 0;
  // Parts progress: completed_prints / target_parts_count
  const partsProgressPercent = stats?.parts_progress_percent ?? 0;

  return (
    <div className="p-4 md:p-8 space-y-8">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-bambu-gray">
        <Link to="/projects" className="hover:text-white transition-colors">
          {t('projectDetail.projects')}
        </Link>
        <ChevronRight className="w-4 h-4" />
        <span className="text-white">{project.name}</span>
      </div>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/projects')}
            className="p-2 rounded-lg bg-bambu-card hover:bg-bambu-dark-tertiary transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-bambu-gray" />
          </button>
          <div className="flex items-center gap-3">
            <div
              className="w-4 h-4 rounded-full flex-shrink-0"
              style={{ backgroundColor: project.color || '#6b7280' }}
            />
            <div>
              <h1 className="text-2xl font-bold text-white">{project.name}</h1>
              {project.description && (
                <p className="text-bambu-gray mt-1">{project.description}</p>
              )}
            </div>
          </div>
          <StatusBadge status={project.status} />
        </div>
        <Button onClick={() => setShowEditModal(true)}>
          <Edit3 className="w-4 h-4 mr-2" />
          {t('projectDetail.edit')}
        </Button>
      </div>

      {/* Progress bars (if targets set) */}
      {(project.target_count || project.target_parts_count) && (
        <Card>
          <CardContent className="p-4 space-y-4">
            {/* Plates progress */}
            {project.target_count && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm text-bambu-gray">{t('projectDetail.platesProgress')}</span>
                  <span className="text-sm font-medium text-white">
                    {stats?.total_archives || 0} / {project.target_count} {t('projectDetail.printJobs')}
                  </span>
                </div>
                <div className="h-3 bg-bambu-dark rounded-full overflow-hidden">
                  <div
                    className="h-full transition-all duration-500"
                    style={{
                      width: `${Math.min(platesProgressPercent, 100)}%`,
                      backgroundColor: platesProgressPercent >= 100 ? '#22c55e' : project.color || '#6b7280',
                    }}
                  />
                </div>
                <div className="flex justify-between mt-1">
                  <span className="text-xs text-bambu-gray/70">
                    {platesProgressPercent.toFixed(0)}{t('projectDetail.percentComplete')}
                  </span>
                  {stats?.remaining_prints != null && stats.remaining_prints > 0 && (
                    <span className="text-xs text-bambu-gray/70">
                      {stats.remaining_prints} {t('projectDetail.remaining')}
                    </span>
                  )}
                </div>
              </div>
            )}
            {/* Parts progress */}
            {project.target_parts_count && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm text-bambu-gray">{t('projectDetail.partsProgress')}</span>
                  <span className="text-sm font-medium text-white">
                    {stats?.completed_prints || 0} / {project.target_parts_count} {t('projectDetail.parts')}
                  </span>
                </div>
                <div className="h-3 bg-bambu-dark rounded-full overflow-hidden">
                  <div
                    className="h-full transition-all duration-500"
                    style={{
                      width: `${Math.min(partsProgressPercent, 100)}%`,
                      backgroundColor: partsProgressPercent >= 100 ? '#22c55e' : project.color || '#6b7280',
                    }}
                  />
                </div>
                <div className="flex justify-between mt-1">
                  <span className="text-xs text-bambu-gray/70">
                    {partsProgressPercent.toFixed(0)}{t('projectDetail.percentComplete')}
                  </span>
                  {stats?.remaining_parts != null && stats.remaining_parts > 0 && (
                    <span className="text-xs text-bambu-gray/70">
                      {stats.remaining_parts} {t('projectDetail.remaining')}
                    </span>
                  )}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Stats grid */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardContent className="p-4">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-bambu-dark text-bambu-green">
                  <Package className="w-5 h-5" />
                </div>
                <div>
                  <p className="text-sm text-bambu-gray">{t('projectDetail.printJobsCard')}</p>
                  <p className="text-xl font-semibold text-white">{stats.total_archives} <span className="text-sm font-normal text-bambu-gray">{t('projectDetail.total')}</span></p>
                  {stats.failed_prints > 0 && (
                    <p className="text-sm text-red-400">{stats.failed_prints} {t('projectDetail.failed')}</p>
                  )}
                  <p className="text-sm text-bambu-gray">{stats.completed_prints} {t('projectDetail.partsPrinted')}</p>
                </div>
              </div>
            </CardContent>
          </Card>
          <StatCard
            icon={Clock}
            label={t('projectDetail.printTime')}
            value={formatDuration(stats.total_print_time_hours)}
            color="text-yellow-400"
          />
          <StatCard
            icon={Printer}
            label={t('projectDetail.filamentUsed')}
            value={formatFilament(stats.total_filament_grams)}
            color="text-purple-400"
          />
        </div>
      )}

      {/* Cost tracking */}
      {stats && (stats.estimated_cost > 0 || project.budget) && (
        <Card>
          <CardContent className="p-4">
            <h2 className="text-lg font-semibold text-white mb-3">
              {t('projectDetail.costTracking')}
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-bambu-gray uppercase">{t('projectDetail.filamentCost')}</p>
                <p className="text-lg font-semibold text-white">
                  {currency}{stats.estimated_cost.toFixed(2)}
                </p>
              </div>
              {stats.total_energy_kwh > 0 && (
                <div>
                  <p className="text-xs text-bambu-gray uppercase">{t('projectDetail.energy')}</p>
                  <p className="text-lg font-semibold text-white">
                    {stats.total_energy_kwh.toFixed(2)} kWh
                    {stats.total_energy_cost > 0 && (
                      <span className="text-sm text-bambu-gray ml-1">
                        ({currency}{stats.total_energy_cost.toFixed(2)})
                      </span>
                    )}
                  </p>
                </div>
              )}
              {project.budget && (
                <>
                  <div>
                    <p className="text-xs text-bambu-gray uppercase">{t('projectDetail.budget')}</p>
                    <p className="text-lg font-semibold text-white">{currency}{project.budget.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-bambu-gray uppercase">{t('projectDetail.budgetRemaining')}</p>
                    <p className={`text-lg font-semibold ${project.budget - stats.estimated_cost >= 0 ? 'text-bambu-green' : 'text-red-400'}`}>
                      {currency}{(project.budget - stats.estimated_cost).toFixed(2)}
                    </p>
                  </div>
                </>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Sub-projects */}
      {project.children && project.children.length > 0 && (
        <Card>
          <CardContent className="p-4">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-3">
              <FolderTree className="w-5 h-5" />
              {t('projectDetail.subProjects', { count: project.children.length })}
            </h2>
            <div className="space-y-2">
              {project.children.map((child) => (
                <Link
                  key={child.id}
                  to={`/projects/${child.id}`}
                  className="flex items-center justify-between p-3 bg-bambu-dark rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <div
                      className="w-3 h-3 rounded-full"
                      style={{ backgroundColor: child.color || '#6b7280' }}
                    />
                    <span className="text-white">{child.name}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      child.status === 'completed' ? 'bg-bambu-green/20 text-bambu-green' :
                      child.status === 'archived' ? 'bg-bambu-gray/20 text-bambu-gray' :
                      'bg-blue-500/20 text-blue-400'
                    }`}>
                      {child.status === 'active' ? t('projectDetail.statusActive') :
                       child.status === 'completed' ? t('projectDetail.statusCompleted') :
                       child.status === 'archived' ? t('projectDetail.statusArchived') :
                       child.status}
                    </span>
                  </div>
                  {child.progress_percent !== null && (
                    <span className="text-sm text-bambu-gray">
                      {child.progress_percent.toFixed(0)}%
                    </span>
                  )}
                </Link>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Parent project link */}
      {project.parent_id && project.parent_name && (
        <div className="flex items-center gap-2 text-sm">
          <Layers className="w-4 h-4 text-bambu-gray" />
          <span className="text-bambu-gray">{t('projectDetail.partOf')}</span>
          <Link
            to={`/projects/${project.parent_id}`}
            className="text-bambu-green hover:underline"
          >
            {project.parent_name}
          </Link>
        </div>
      )}

      {/* Meta info row - Tags, Due Date, Priority */}
      {(project.tags || project.due_date || project.priority !== 'normal') && (
        <div className="flex flex-wrap items-center gap-4">
          {/* Priority */}
          {project.priority && project.priority !== 'normal' && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-bambu-gray uppercase">{t('projectDetail.priority')}:</span>
              <PriorityBadge priority={project.priority} />
            </div>
          )}

          {/* Due Date */}
          {project.due_date && (
            <div className="flex items-center gap-2">
              <Calendar className="w-4 h-4 text-bambu-gray" />
              <span className="text-sm text-white">{formatDate(project.due_date)}</span>
              {getDueDateStatus(project.due_date, t) && (
                <span className={`text-xs ${getDueDateStatus(project.due_date, t)!.color}`}>
                  ({getDueDateStatus(project.due_date, t)!.label})
                </span>
              )}
            </div>
          )}

          {/* Tags */}
          {project.tags && (
            <div className="flex items-center gap-2">
              <Tag className="w-4 h-4 text-bambu-gray" />
              <div className="flex flex-wrap gap-1">
                {project.tags.split(',').map((tag, index) => (
                  <span
                    key={index}
                    className="px-2 py-0.5 bg-bambu-dark-tertiary text-bambu-gray text-xs rounded"
                  >
                    {tag.trim()}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Notes section */}
      <Card>
        <CardContent className="p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <FileText className="w-5 h-5" />
              {t('projectDetail.notes')}
            </h2>
            {!editingNotes ? (
              <Button variant="secondary" size="sm" onClick={handleStartEditNotes}>
                <Edit3 className="w-4 h-4 mr-1" />
                {t('projectDetail.edit')}
              </Button>
            ) : (
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleCancelNotes}
                  disabled={updateMutation.isPending}
                >
                  <X className="w-4 h-4 mr-1" />
                  {t('projectDetail.cancel')}
                </Button>
                <Button
                  size="sm"
                  onClick={handleSaveNotes}
                  disabled={updateMutation.isPending}
                >
                  {updateMutation.isPending ? (
                    <Loader2 className="w-4 h-4 animate-spin mr-1" />
                  ) : (
                    <Save className="w-4 h-4 mr-1" />
                  )}
                  {t('projectDetail.save')}
                </Button>
              </div>
            )}
          </div>

          {editingNotes ? (
            <RichTextEditor
              content={notesContent}
              onChange={setNotesContent}
              placeholder={t('projectDetail.notesPlaceholder')}
            />
          ) : project.notes ? (
            <div
              className="prose prose-invert prose-sm max-w-none"
              dangerouslySetInnerHTML={{ __html: project.notes }}
            />
          ) : (
            <p className="text-bambu-gray/70 text-sm italic">
              {t('projectDetail.noNotesYet')}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Files section - linked folders from File Manager */}
      <Card>
        <CardContent className="p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <FolderOpen className="w-5 h-5" />
              {t('projectDetail.files')}
            </h2>
          </div>

          <p className="text-xs text-bambu-gray mb-3">
            <Link to="/files" className="text-bambu-green hover:underline">
              {t('projectDetail.linkFoldersFromFileManager')}
            </Link>
            {' '}{t('projectDetail.toThisProjectForQuickAccess')}
          </p>

          {linkedFolders && linkedFolders.length > 0 ? (
            <div className="space-y-2">
              {linkedFolders.map((folder) => (
                <Link
                  key={folder.id}
                  to={`/files?folder=${folder.id}`}
                  className="flex items-center justify-between p-3 bg-bambu-dark rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <FolderOpen className="w-5 h-5 text-bambu-green flex-shrink-0" />
                    <div className="min-w-0">
                      <p className="text-sm text-white truncate">
                        {folder.name}
                      </p>
                      <p className="text-xs text-bambu-gray">
                        {t('projectDetail.fileCount', { count: folder.file_count })}
                      </p>
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-bambu-gray flex-shrink-0" />
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-bambu-gray/70 text-sm italic">
              {t('projectDetail.noFoldersLinked')}
            </p>
          )}
        </CardContent>
      </Card>

      {/* BOM Section - Parts to source/purchase */}
      <Card>
        <CardContent className="p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <ShoppingCart className="w-5 h-5" />
              {t('projectDetail.billOfMaterials')}
              {stats && stats.bom_total_items > 0 && (
                <span className="text-sm font-normal text-bambu-gray">
                  ({t('projectDetail.bomAcquired', { completed: stats.bom_completed_items, total: stats.bom_total_items })})
                </span>
              )}
            </h2>
            <div className="flex items-center gap-2">
              {bomItems && bomItems.some(item => item.is_complete) && (
                <button
                  onClick={() => setHideBomCompleted(!hideBomCompleted)}
                  className={`text-xs px-2 py-1 rounded transition-colors ${
                    hideBomCompleted
                      ? 'bg-bambu-green/20 text-bambu-green'
                      : 'bg-bambu-dark text-bambu-gray hover:text-white'
                  }`}
                >
                  {hideBomCompleted ? t('projectDetail.showAll') : t('projectDetail.hideDone')}
                </button>
              )}
              {!showBomForm && (
                <Button variant="secondary" size="sm" onClick={() => setShowBomForm(true)}>
                  <Plus className="w-4 h-4 mr-1" />
                  {t('projectDetail.addPart')}
                </Button>
              )}
            </div>
          </div>

          {/* Add BOM item form */}
          {showBomForm && (
            <form onSubmit={handleAddBomItem} className="bg-bambu-dark rounded-lg p-4 mb-4 space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <input
                  type="text"
                  value={newBomName}
                  onChange={(e) => setNewBomName(e.target.value)}
                  className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                  placeholder={t('projectDetail.partNamePlaceholder')}
                  autoFocus
                />
                <div className="flex gap-2">
                  <input
                    type="number"
                    value={newBomQty}
                    onChange={(e) => setNewBomQty(parseInt(e.target.value) || 1)}
                    className="w-20 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-bambu-green"
                    min="1"
                    placeholder={t('projectDetail.qty')}
                  />
                  <input
                    type="number"
                    step="0.01"
                    value={newBomPrice}
                    onChange={(e) => setNewBomPrice(e.target.value)}
                    className="flex-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                    placeholder={t('projectDetail.pricePlaceholder', { currency })}
                  />
                </div>
              </div>
              <input
                type="url"
                value={newBomUrl}
                onChange={(e) => setNewBomUrl(e.target.value)}
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                placeholder={t('projectDetail.sourcingUrlPlaceholder')}
              />
              <input
                type="text"
                value={newBomRemarks}
                onChange={(e) => setNewBomRemarks(e.target.value)}
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                placeholder={t('projectDetail.remarksPlaceholder')}
              />
              <div className="flex justify-end gap-2">
                <Button type="button" variant="secondary" size="sm" onClick={() => setShowBomForm(false)}>
                  {t('projectDetail.cancel')}
                </Button>
                <Button type="submit" size="sm" disabled={!newBomName.trim() || createBomMutation.isPending}>
                  {createBomMutation.isPending ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    t('projectDetail.addPart')
                  )}
                </Button>
              </div>
            </form>
          )}

          {bomLoading ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
            </div>
          ) : bomItems && bomItems.length > 0 ? (
            <div className="space-y-2">
              {bomItems
                .filter(item => !hideBomCompleted || !item.is_complete)
                .map((item) => (
                <div
                  key={item.id}
                  className={`p-3 rounded-lg transition-colors ${
                    item.is_complete ? 'bg-bambu-green/10' : 'bg-bambu-dark'
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <button
                      onClick={() => handleToggleAcquired(item)}
                      disabled={updateBomMutation.isPending}
                      className={`w-5 h-5 mt-0.5 rounded border-2 flex items-center justify-center transition-colors flex-shrink-0 ${
                        item.is_complete
                          ? 'bg-bambu-green border-bambu-green text-white'
                          : 'border-bambu-gray hover:border-bambu-green'
                      }`}
                    >
                      {item.is_complete && <CheckCircle className="w-3 h-3" />}
                    </button>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <p className={`text-sm font-medium ${item.is_complete ? 'text-bambu-gray line-through' : 'text-white'}`}>
                            {item.name}
                            <span className="text-bambu-gray font-normal ml-2">
                              x{item.quantity_needed}
                            </span>
                          </p>
                          {item.unit_price !== null && (
                            <span className="text-xs text-bambu-green whitespace-nowrap">
                              {currency}{(item.unit_price * item.quantity_needed).toFixed(2)}
                            </span>
                          )}
                        </div>
                        <button
                          onClick={() => handleDeleteBomItem(item.id, item.name)}
                          className="p-1 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-red-400 transition-colors flex-shrink-0"
                          title={t('projectDetail.delete')}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                      {/* Sourcing URL */}
                      {item.sourcing_url && (
                        <a
                          href={item.sourcing_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1 mt-1 text-xs text-blue-400 hover:text-blue-300 transition-colors"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <ExternalLink className="w-3 h-3 flex-shrink-0" />
                          <span className="truncate">
                            {(() => {
                              try {
                                return new URL(item.sourcing_url).hostname.replace('www.', '');
                              } catch {
                                return item.sourcing_url;
                              }
                            })()}
                          </span>
                        </a>
                      )}
                      {/* Remarks */}
                      {item.remarks && (
                        <p className="mt-1 text-xs text-bambu-gray/80 italic">
                          {item.remarks}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              ))}
              {/* BOM Total */}
              {bomItems.some(item => item.unit_price !== null) && (
                <div className="pt-2 mt-2 border-t border-bambu-dark-tertiary flex justify-between text-sm">
                  <span className="text-bambu-gray">{t('projectDetail.totalCost')}:</span>
                  <span className="text-white font-medium">
                    {currency}{bomItems.reduce((sum, item) => sum + (item.unit_price || 0) * item.quantity_needed, 0).toFixed(2)}
                  </span>
                </div>
              )}
            </div>
          ) : (
            <p className="text-bambu-gray/70 text-sm italic">
              {t('projectDetail.noBomParts')}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Timeline Section */}
      <Card>
        <CardContent className="p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <History className="w-5 h-5" />
              {t('projectDetail.activityTimeline')}
            </h2>
          </div>

          {timelineLoading ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
            </div>
          ) : timeline && timeline.length > 0 ? (
            <div className="space-y-3">
              {timeline.slice(0, 10).map((event, index) => (
                <div key={index} className="flex gap-3">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                    event.event_type === 'print_completed' ? 'bg-bambu-green/20 text-bambu-green' :
                    event.event_type === 'print_failed' ? 'bg-red-500/20 text-red-400' :
                    event.event_type === 'print_started' ? 'bg-yellow-500/20 text-yellow-400' :
                    'bg-bambu-dark-tertiary text-bambu-gray'
                  }`}>
                    {event.event_type === 'print_completed' && <CheckCircle className="w-4 h-4" />}
                    {event.event_type === 'print_failed' && <XCircle className="w-4 h-4" />}
                    {event.event_type === 'print_started' && <Printer className="w-4 h-4" />}
                    {event.event_type === 'queued' && <ListTodo className="w-4 h-4" />}
                    {event.event_type === 'project_created' && <Plus className="w-4 h-4" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-white">{t(`projectDetail.timelineEvents.${event.event_type}`, { defaultValue: event.title })}</p>
                    {event.description && (
                      <p className="text-xs text-bambu-gray truncate">{event.description}</p>
                    )}
                    <p className="text-xs text-bambu-gray/70">{formatTimelineDate(event.timestamp)}</p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-bambu-gray/70 text-sm italic">
              {t('projectDetail.noActivityYet')}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Template action */}
      {!project.is_template && (
        <div className="flex justify-end">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => createTemplateMutation.mutate()}
            disabled={createTemplateMutation.isPending}
          >
            {createTemplateMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
            ) : (
              <Copy className="w-4 h-4 mr-2" />
            )}
            {t('projectDetail.saveAsTemplate')}
          </Button>
        </div>
      )}

      {/* Queue section */}
      {stats && (stats.queued_prints > 0 || stats.in_progress_prints > 0) && (
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <ListTodo className="w-5 h-5" />
                {t('projectDetail.queue')}
              </h2>
              <Link
                to={`/queue?project=${projectId}`}
                className="text-sm text-bambu-green hover:underline"
              >
                {t('projectDetail.viewAll')}
              </Link>
            </div>
            <div className="flex items-center gap-4 text-sm">
              {stats.in_progress_prints > 0 && (
                <span className="text-yellow-400">
                  {stats.in_progress_prints} {t('projectDetail.printing')}
                </span>
              )}
              {stats.queued_prints > 0 && (
                <span className="text-bambu-gray">
                  {stats.queued_prints} {t('projectDetail.queued')}
                </span>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Archives section */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <Package className="w-5 h-5" />
            {t('projectDetail.prints', { count: archives?.length || 0 })}
          </h2>
        </div>
        {archivesLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
          </div>
        ) : (
          <ArchiveGrid archives={archives || []} />
        )}
      </div>

      {/* Edit Modal */}
      {showEditModal && (
        <ProjectModal
          project={{
            ...project,
            archive_count: stats?.total_archives || 0,
            total_items: stats?.total_items || 0,
            completed_count: stats?.completed_prints || 0,
            failed_count: stats?.failed_prints || 0,
            queue_count: stats?.queued_prints || 0,
            progress_percent: stats?.progress_percent || null,
            archives: [],
          }}
          onClose={() => setShowEditModal(false)}
          onSave={(data) => updateMutation.mutate(data as ProjectUpdate)}
          isLoading={updateMutation.isPending}
        />
      )}

      {/* Confirm Modal */}
      {confirmModal.isOpen && (
        <ConfirmModal
          title={confirmModal.title}
          message={confirmModal.message}
          confirmText={t('projectDetail.delete')}
          variant="danger"
          onConfirm={confirmModal.onConfirm}
          onCancel={() => setConfirmModal(prev => ({ ...prev, isOpen: false }))}
        />
      )}
    </div>
  );
}
