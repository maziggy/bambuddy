import { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
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
  Download,
  Pencil,
} from 'lucide-react';
import { api } from '../api/client';
import { parseUTCDate, formatDateOnly, formatDateTime, type TimeFormat } from '../utils/date';
import type { Archive, ProjectUpdate, BOMItem, BOMItemCreate, BOMItemUpdate } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
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

type TFunction = (key: string, options?: Record<string, unknown>) => string;

function StatusBadge({ status, t }: { status: string; t: TFunction }) {
  const colors = {
    active: 'bg-bambu-green/20 text-bambu-green',
    completed: 'bg-blue-500/20 text-blue-400',
    archived: 'bg-bambu-gray/20 text-bambu-gray',
  };
  const color = colors[status as keyof typeof colors] || colors.active;

  const labels: Record<string, string> = {
    active: t('projectDetail.status.active'),
    completed: t('projectDetail.status.completed'),
    archived: t('projectDetail.status.archived'),
  };

  return (
    <span className={`px-2 py-1 rounded text-sm font-medium ${color}`}>
      {labels[status] || status.charAt(0).toUpperCase() + status.slice(1)}
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

function ArchiveGrid({ archives, t }: { archives: Archive[]; t: TFunction }) {
  if (archives.length === 0) {
    return (
      <div className="text-center py-8 text-bambu-gray">
        <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
        <p>{t('projectDetail.noPrints')}</p>
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
              alt={archive.print_name || 'Print'}
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
            <p className="text-xs text-white truncate">{archive.print_name || 'Unknown'}</p>
          </div>
        </Link>
      ))}
    </div>
  );
}

function PriorityBadge({ priority, t }: { priority: string; t: TFunction }) {
  const config = {
    low: { color: 'bg-gray-500/20 text-gray-400', label: t('projectDetail.priority.low') },
    normal: { color: 'bg-blue-500/20 text-blue-400', label: t('projectDetail.priority.normal') },
    high: { color: 'bg-orange-500/20 text-orange-400', label: t('projectDetail.priority.high') },
    urgent: { color: 'bg-red-500/20 text-red-400', label: t('projectDetail.priority.urgent') },
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

function getDueDateStatus(dateString: string | null, t: TFunction): { color: string; label: string } | null {
  if (!dateString) return null;
  const dueDate = parseUTCDate(dateString);
  if (!dueDate) return null;
  const now = new Date();
  const diffDays = Math.ceil((dueDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));

  if (diffDays < 0) return { color: 'text-red-400', label: t('projectDetail.dueDate.overdue') };
  if (diffDays === 0) return { color: 'text-orange-400', label: t('projectDetail.dueDate.today') };
  if (diffDays <= 3) return { color: 'text-yellow-400', label: t('projectDetail.dueDate.daysLeft', { count: diffDays }) };
  return { color: 'text-bambu-gray', label: t('projectDetail.dueDate.daysLeft', { count: diffDays }) };
}

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
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
      showToast(t('projectDetail.toast.projectUpdated'), 'success');
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
  const [editingBomItem, setEditingBomItem] = useState<BOMItem | null>(null);
  const [editBomName, setEditBomName] = useState('');
  const [editBomQty, setEditBomQty] = useState(1);
  const [editBomPrice, setEditBomPrice] = useState('');
  const [editBomUrl, setEditBomUrl] = useState('');
  const [editBomRemarks, setEditBomRemarks] = useState('');

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
      showToast(t('projectDetail.toast.partAdded'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const updateBomMutation = useMutation({
    mutationFn: ({ itemId, data }: { itemId: number; data: BOMItemUpdate }) =>
      api.updateBOMItem(projectId, itemId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project-bom', projectId] });
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
      setEditingBomItem(null);
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const deleteBomMutation = useMutation({
    mutationFn: (itemId: number) => api.deleteBOMItem(projectId, itemId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project-bom', projectId] });
      queryClient.invalidateQueries({ queryKey: ['project', projectId] });
      showToast(t('projectDetail.toast.partRemoved'), 'success');
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
      title: t('projectDetail.bom.deletePart'),
      message: t('projectDetail.bom.deleteConfirm', { name: itemName }),
      onConfirm: () => {
        setConfirmModal(prev => ({ ...prev, isOpen: false }));
        deleteBomMutation.mutate(itemId);
      },
    });
  };

  const handleEditBomItem = (item: BOMItem) => {
    setEditingBomItem(item);
    setEditBomName(item.name);
    setEditBomQty(item.quantity_needed);
    setEditBomPrice(item.unit_price?.toString() || '');
    setEditBomUrl(item.sourcing_url || '');
    setEditBomRemarks(item.remarks || '');
  };

  const handleSaveBomEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingBomItem || !editBomName.trim()) return;
    updateBomMutation.mutate({
      itemId: editingBomItem.id,
      data: {
        name: editBomName.trim(),
        quantity_needed: editBomQty,
        unit_price: editBomPrice ? parseFloat(editBomPrice) : undefined,
        sourcing_url: editBomUrl.trim() || undefined,
        remarks: editBomRemarks.trim() || undefined,
      },
    });
  };

  const handleCancelBomEdit = () => {
    setEditingBomItem(null);
  };

  const handleExportProject = async () => {
    try {
      const { blob, filename } = await api.exportProjectZip(Number(projectId));
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename || `${project?.name || 'project'}_${new Date().toISOString().split('T')[0]}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      showToast(t('projectDetail.toast.projectExported'), 'success');
    } catch (error) {
      showToast((error as Error).message, 'error');
    }
  };

  // Template handlers
  const createTemplateMutation = useMutation({
    mutationFn: () => api.createTemplateFromProject(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      showToast(t('projectDetail.toast.templateCreated'), 'success');
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
          {projectError ? `${t('common.error')}: ${(projectError as Error).message}` : t('projectDetail.notFound')}
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
          {t('navigation.projects')}
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
          <StatusBadge status={project.status} t={t} />
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            onClick={handleExportProject}
            disabled={!hasPermission('projects:read')}
            title={!hasPermission('projects:read') ? t('projectDetail.noExportPermission') : t('projectDetail.exportProject')}
          >
            <Download className="w-4 h-4 mr-2" />
            {t('projectDetail.export')}
          </Button>
          <Button
            onClick={() => setShowEditModal(true)}
            disabled={!hasPermission('projects:update')}
            title={!hasPermission('projects:update') ? t('projectDetail.noEditPermission') : undefined}
          >
            <Edit3 className="w-4 h-4 mr-2" />
            {t('common.edit')}
          </Button>
        </div>
      </div>

      {/* Progress bars (if targets set) */}
      {(project.target_count || project.target_parts_count) && (
        <Card>
          <CardContent className="p-4 space-y-4">
            {/* Plates progress */}
            {project.target_count && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm text-bambu-gray">{t('projectDetail.progress.platesProgress')}</span>
                  <span className="text-sm font-medium text-white">
                    {stats?.total_archives || 0} / {project.target_count} {t('projectDetail.progress.printJobs')}
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
                    {t('projectDetail.progress.percentComplete', { percent: platesProgressPercent.toFixed(0) })}
                  </span>
                  {stats?.remaining_prints != null && stats.remaining_prints > 0 && (
                    <span className="text-xs text-bambu-gray/70">
                      {t('projectDetail.progress.remaining', { count: stats.remaining_prints })}
                    </span>
                  )}
                </div>
              </div>
            )}
            {/* Parts progress */}
            {project.target_parts_count && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm text-bambu-gray">{t('projectDetail.progress.partsProgress')}</span>
                  <span className="text-sm font-medium text-white">
                    {stats?.completed_prints || 0} / {project.target_parts_count} {t('projectDetail.progress.parts')}
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
                    {t('projectDetail.progress.percentComplete', { percent: partsProgressPercent.toFixed(0) })}
                  </span>
                  {stats?.remaining_parts != null && stats.remaining_parts > 0 && (
                    <span className="text-xs text-bambu-gray/70">
                      {t('projectDetail.progress.remaining', { count: stats.remaining_parts })}
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
                  <p className="text-sm text-bambu-gray">{t('projectDetail.stats.printJobs')}</p>
                  <p className="text-xl font-semibold text-white">{stats.total_archives} <span className="text-sm font-normal text-bambu-gray">{t('projectDetail.stats.total')}</span></p>
                  {stats.failed_prints > 0 && (
                    <p className="text-sm text-status-error">{t('projectDetail.stats.failed', { count: stats.failed_prints })}</p>
                  )}
                  <p className="text-sm text-bambu-gray">{t('projectDetail.stats.partsPrinted', { count: stats.completed_prints })}</p>
                </div>
              </div>
            </CardContent>
          </Card>
          <StatCard
            icon={Clock}
            label={t('projectDetail.stats.printTime')}
            value={formatDuration(stats.total_print_time_hours)}
            color="text-yellow-400"
          />
          <StatCard
            icon={Printer}
            label={t('projectDetail.stats.filamentUsed')}
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
              {t('projectDetail.cost.title')}
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-bambu-gray uppercase">{t('projectDetail.cost.filamentCost')}</p>
                <p className="text-lg font-semibold text-white">
                  {currency}{stats.estimated_cost.toFixed(2)}
                </p>
              </div>
              {stats.total_energy_kwh > 0 && (
                <div>
                  <p className="text-xs text-bambu-gray uppercase">{t('projectDetail.cost.energy')}</p>
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
                    <p className="text-xs text-bambu-gray uppercase">{t('projectDetail.cost.budget')}</p>
                    <p className="text-lg font-semibold text-white">{currency}{project.budget.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-bambu-gray uppercase">{t('projectDetail.cost.remaining')}</p>
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
              {t('projectDetail.subProjects.title', { count: project.children.length })}
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
                      child.status === 'completed' ? 'bg-status-ok/20 text-status-ok' :
                      child.status === 'archived' ? 'bg-bambu-gray/20 text-bambu-gray' :
                      'bg-blue-500/20 text-blue-400'
                    }`}>
                      {child.status}
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
              <span className="text-xs text-bambu-gray uppercase">{t('projectDetail.priorityLabel')}</span>
              <PriorityBadge priority={project.priority} t={t} />
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
              {t('projectDetail.notes.title')}
            </h2>
            {!editingNotes ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={handleStartEditNotes}
                disabled={!hasPermission('projects:update')}
                title={!hasPermission('projects:update') ? t('projectDetail.notes.noEditPermission') : undefined}
              >
                <Edit3 className="w-4 h-4 mr-1" />
                {t('common.edit')}
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
                  {t('common.cancel')}
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
                  {t('common.save')}
                </Button>
              </div>
            )}
          </div>

          {editingNotes ? (
            <RichTextEditor
              content={notesContent}
              onChange={setNotesContent}
              placeholder={t('projectDetail.notes.placeholder')}
            />
          ) : project.notes ? (
            <div
              className="prose prose-invert prose-sm max-w-none"
              dangerouslySetInnerHTML={{ __html: project.notes }}
            />
          ) : (
            <p className="text-bambu-gray/70 text-sm italic">
              {t('projectDetail.notes.empty')}
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
              {t('projectDetail.files.title')}
            </h2>
          </div>

          <p className="text-xs text-bambu-gray mb-3">
            <Link to="/files" className="text-bambu-green hover:underline">
              {t('projectDetail.files.linkFolders')}
            </Link>
            {' '}{t('projectDetail.files.forQuickAccess')}
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
                        {t('projectDetail.files.fileCount', { count: folder.file_count })}
                      </p>
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-bambu-gray flex-shrink-0" />
                </Link>
              ))}
            </div>
          ) : (
            <p className="text-bambu-gray/70 text-sm italic">
              {t('projectDetail.files.empty')}
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
              {t('projectDetail.bom.title')}
              {stats && stats.bom_total_items > 0 && (
                <span className="text-sm font-normal text-bambu-gray">
                  ({t('projectDetail.bom.acquired', { completed: stats.bom_completed_items, total: stats.bom_total_items })})
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
                  {hideBomCompleted ? t('projectDetail.bom.showAll') : t('projectDetail.bom.hideDone')}
                </button>
              )}
              {!showBomForm && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setShowBomForm(true)}
                  disabled={!hasPermission('projects:update')}
                  title={!hasPermission('projects:update') ? t('projectDetail.bom.noAddPermission') : undefined}
                >
                  <Plus className="w-4 h-4 mr-1" />
                  {t('projectDetail.bom.addPart')}
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
                  placeholder={t('projectDetail.bom.partNamePlaceholder')}
                  autoFocus
                />
                <div className="flex gap-2">
                  <input
                    type="number"
                    value={newBomQty}
                    onChange={(e) => setNewBomQty(parseInt(e.target.value) || 1)}
                    className="w-20 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-bambu-green"
                    min="1"
                    placeholder={t('projectDetail.bom.qty')}
                  />
                  <input
                    type="number"
                    step="0.01"
                    value={newBomPrice}
                    onChange={(e) => setNewBomPrice(e.target.value)}
                    className="flex-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                    placeholder={t('projectDetail.bom.price', { currency })}
                  />
                </div>
              </div>
              <input
                type="url"
                value={newBomUrl}
                onChange={(e) => setNewBomUrl(e.target.value)}
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                placeholder={t('projectDetail.bom.sourcingUrlPlaceholder')}
              />
              <input
                type="text"
                value={newBomRemarks}
                onChange={(e) => setNewBomRemarks(e.target.value)}
                className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                placeholder={t('projectDetail.bom.remarksPlaceholder')}
              />
              <div className="flex justify-end gap-2">
                <Button type="button" variant="secondary" size="sm" onClick={() => setShowBomForm(false)}>
                  {t('common.cancel')}
                </Button>
                <Button type="submit" size="sm" disabled={!newBomName.trim() || createBomMutation.isPending}>
                  {createBomMutation.isPending ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    t('projectDetail.bom.addPart')
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
                    item.is_complete ? 'bg-status-ok/10' : 'bg-bambu-dark'
                  }`}
                >
                  {editingBomItem?.id === item.id ? (
                    // Edit form for this BOM item
                    <form onSubmit={handleSaveBomEdit} className="space-y-3">
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <input
                          type="text"
                          value={editBomName}
                          onChange={(e) => setEditBomName(e.target.value)}
                          className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                          placeholder={t('projectDetail.bom.partName')}
                          autoFocus
                        />
                        <div className="flex gap-2">
                          <input
                            type="number"
                            value={editBomQty}
                            onChange={(e) => setEditBomQty(parseInt(e.target.value) || 1)}
                            className="w-20 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-bambu-green"
                            min="1"
                            placeholder={t('projectDetail.bom.qty')}
                          />
                          <input
                            type="number"
                            step="0.01"
                            value={editBomPrice}
                            onChange={(e) => setEditBomPrice(e.target.value)}
                            className="flex-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                            placeholder={t('projectDetail.bom.price', { currency })}
                          />
                        </div>
                      </div>
                      <input
                        type="url"
                        value={editBomUrl}
                        onChange={(e) => setEditBomUrl(e.target.value)}
                        className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                        placeholder={t('projectDetail.bom.sourcingUrlPlaceholder')}
                      />
                      <input
                        type="text"
                        value={editBomRemarks}
                        onChange={(e) => setEditBomRemarks(e.target.value)}
                        className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                        placeholder={t('projectDetail.bom.remarksPlaceholder')}
                      />
                      <div className="flex justify-end gap-2">
                        <Button type="button" variant="secondary" size="sm" onClick={handleCancelBomEdit}>
                          {t('common.cancel')}
                        </Button>
                        <Button type="submit" size="sm" disabled={!editBomName.trim() || updateBomMutation.isPending}>
                          {updateBomMutation.isPending ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            t('common.save')
                          )}
                        </Button>
                      </div>
                    </form>
                  ) : (
                    // Display mode
                    <div className="flex items-start gap-3">
                      <button
                        onClick={() => hasPermission('projects:update') && handleToggleAcquired(item)}
                        disabled={updateBomMutation.isPending || !hasPermission('projects:update')}
                        title={!hasPermission('projects:update') ? t('projectDetail.bom.noUpdatePermission') : undefined}
                        className={`w-5 h-5 mt-0.5 rounded border-2 flex items-center justify-center transition-colors flex-shrink-0 ${
                          item.is_complete
                            ? 'bg-status-ok border-status-ok text-white'
                            : hasPermission('projects:update')
                              ? 'border-bambu-gray hover:border-bambu-green'
                              : 'border-bambu-gray/50 cursor-not-allowed'
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
                          <div className="flex items-center gap-1">
                            <button
                              onClick={() => hasPermission('projects:update') && handleEditBomItem(item)}
                              disabled={!hasPermission('projects:update')}
                              className={`p-1 rounded transition-colors flex-shrink-0 ${
                                hasPermission('projects:update')
                                  ? 'hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white'
                                  : 'text-bambu-gray/50 cursor-not-allowed'
                              }`}
                              title={!hasPermission('projects:update') ? t('projectDetail.bom.noEditPermission') : t('common.edit')}
                            >
                              <Pencil className="w-4 h-4" />
                            </button>
                            <button
                              onClick={() => hasPermission('projects:update') && handleDeleteBomItem(item.id, item.name)}
                              disabled={!hasPermission('projects:update')}
                              className={`p-1 rounded transition-colors flex-shrink-0 ${
                                hasPermission('projects:update')
                                  ? 'hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-red-400'
                                  : 'text-bambu-gray/50 cursor-not-allowed'
                              }`}
                              title={!hasPermission('projects:update') ? t('projectDetail.bom.noDeletePermission') : t('common.delete')}
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
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
                  )}
                </div>
              ))}
              {/* BOM Total */}
              {bomItems.some(item => item.unit_price !== null) && (
                <div className="pt-2 mt-2 border-t border-bambu-dark-tertiary flex justify-between text-sm">
                  <span className="text-bambu-gray">{t('projectDetail.bom.totalCost')}</span>
                  <span className="text-white font-medium">
                    {currency}{bomItems.reduce((sum, item) => sum + (item.unit_price || 0) * item.quantity_needed, 0).toFixed(2)}
                  </span>
                </div>
              )}
            </div>
          ) : (
            <p className="text-bambu-gray/70 text-sm italic">
              {t('projectDetail.bom.empty')}
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
              {t('projectDetail.timeline.title')}
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
                    event.event_type === 'print_completed' ? 'bg-status-ok/20 text-status-ok' :
                    event.event_type === 'print_failed' ? 'bg-status-error/20 text-status-error' :
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
                    <p className="text-sm text-white">{event.title}</p>
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
              {t('projectDetail.timeline.empty')}
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
            disabled={createTemplateMutation.isPending || !hasPermission('projects:create')}
            title={!hasPermission('projects:create') ? t('projectDetail.template.noCreatePermission') : undefined}
          >
            {createTemplateMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
            ) : (
              <Copy className="w-4 h-4 mr-2" />
            )}
            {t('projectDetail.template.saveAsTemplate')}
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
                {t('projectDetail.queue.title')}
              </h2>
              <Link
                to={`/queue?project=${projectId}`}
                className="text-sm text-bambu-green hover:underline"
              >
                {t('projectDetail.queue.viewAll')}
              </Link>
            </div>
            <div className="flex items-center gap-4 text-sm">
              {stats.in_progress_prints > 0 && (
                <span className="text-yellow-400">
                  {t('projectDetail.queue.printing', { count: stats.in_progress_prints })}
                </span>
              )}
              {stats.queued_prints > 0 && (
                <span className="text-bambu-gray">
                  {t('projectDetail.queue.queued', { count: stats.queued_prints })}
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
            {t('projectDetail.prints.title', { count: archives?.length || 0 })}
          </h2>
        </div>
        {archivesLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
          </div>
        ) : (
          <ArchiveGrid archives={archives || []} t={t} />
        )}
      </div>

      {/* Edit Modal */}
      {showEditModal && (
        <ProjectModal
          t={t}
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
          confirmText={t('common.delete')}
          variant="danger"
          onConfirm={confirmModal.onConfirm}
          onCancel={() => setConfirmModal(prev => ({ ...prev, isOpen: false }))}
        />
      )}
    </div>
  );
}
