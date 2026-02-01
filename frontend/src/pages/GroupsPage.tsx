import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  X,
  Plus,
  Edit2,
  Trash2,
  Save,
  Loader2,
  Shield,
  ArrowLeft,
  Users,
  Check,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import { api } from '../api/client';
import type { Group, GroupCreate, GroupUpdate, Permission, PermissionCategory } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { Button } from '../components/Button';
import { Card, CardContent, CardHeader } from '../components/Card';
import { ConfirmModal } from '../components/ConfirmModal';

export function GroupsPage() {
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingGroup, setEditingGroup] = useState<Group | null>(null);
  const [deleteGroupId, setDeleteGroupId] = useState<number | null>(null);
  const [formData, setFormData] = useState<{
    name: string;
    description: string;
    permissions: Permission[];
  }>({
    name: '',
    description: '',
    permissions: [],
  });
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  // Close modal on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && (showCreateModal || editingGroup)) {
        setShowCreateModal(false);
        setEditingGroup(null);
        resetForm();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showCreateModal, editingGroup]);

  const { data: groups = [], isLoading: groupsLoading } = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.getGroups(),
    enabled: hasPermission('groups:read'),
  });

  const { data: permissionsData } = useQuery({
    queryKey: ['permissions'],
    queryFn: () => api.getPermissions(),
    enabled: hasPermission('groups:read'),
  });

  const createMutation = useMutation({
    mutationFn: (data: GroupCreate) => api.createGroup(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      setShowCreateModal(false);
      resetForm();
      showToast('Group created successfully');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: GroupUpdate }) => api.updateGroup(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      setEditingGroup(null);
      resetForm();
      showToast('Group updated successfully');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteGroup(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      showToast('Group deleted successfully');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const resetForm = () => {
    setFormData({ name: '', description: '', permissions: [] });
    setExpandedCategories(new Set());
  };

  const handleCreate = () => {
    if (!formData.name.trim()) {
      showToast('Please enter a group name', 'error');
      return;
    }
    createMutation.mutate({
      name: formData.name,
      description: formData.description || undefined,
      permissions: formData.permissions,
    });
  };

  const handleUpdate = () => {
    if (!editingGroup) return;
    if (!formData.name.trim()) {
      showToast('Please enter a group name', 'error');
      return;
    }
    updateMutation.mutate({
      id: editingGroup.id,
      data: {
        name: formData.name !== editingGroup.name ? formData.name : undefined,
        description: formData.description,
        permissions: formData.permissions,
      },
    });
  };

  const handleDelete = (id: number) => {
    setDeleteGroupId(id);
  };

  const startEdit = (group: Group) => {
    setEditingGroup(group);
    setFormData({
      name: group.name,
      description: group.description || '',
      permissions: group.permissions,
    });
    // Expand categories that have selected permissions
    const cats = new Set<string>();
    permissionsData?.categories.forEach((cat) => {
      if (cat.permissions.some((p) => group.permissions.includes(p.value))) {
        cats.add(cat.name);
      }
    });
    setExpandedCategories(cats);
  };

  const toggleCategory = (categoryName: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(categoryName)) {
        next.delete(categoryName);
      } else {
        next.add(categoryName);
      }
      return next;
    });
  };

  const togglePermission = (permission: Permission) => {
    setFormData((prev) => {
      const permissions = prev.permissions.includes(permission)
        ? prev.permissions.filter((p) => p !== permission)
        : [...prev.permissions, permission];
      return { ...prev, permissions };
    });
  };

  const toggleCategoryPermissions = (category: PermissionCategory, checked: boolean) => {
    setFormData((prev) => {
      const categoryPerms = category.permissions.map((p) => p.value);
      const otherPerms = prev.permissions.filter((p) => !categoryPerms.includes(p));
      const permissions = checked ? [...otherPerms, ...categoryPerms] : otherPerms;
      return { ...prev, permissions };
    });
  };

  const isCategoryFullySelected = (category: PermissionCategory) => {
    return category.permissions.every((p) => formData.permissions.includes(p.value));
  };

  const isCategoryPartiallySelected = (category: PermissionCategory) => {
    const selected = category.permissions.filter((p) => formData.permissions.includes(p.value));
    return selected.length > 0 && selected.length < category.permissions.length;
  };

  // Permission check
  if (!hasPermission('groups:read')) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="py-6">
            <div className="flex items-center gap-3 text-red-400">
              <Shield className="w-5 h-5" />
              <p className="text-white">You do not have permission to access this page.</p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const renderPermissionEditor = () => (
    <div className="space-y-2 max-h-96 overflow-y-auto">
      {permissionsData?.categories.map((category) => (
        <div key={category.name} className="border border-bambu-dark-tertiary rounded-lg overflow-hidden">
          <div
            className="flex items-center justify-between px-4 py-2 bg-bambu-dark-secondary cursor-pointer hover:bg-bambu-dark-tertiary transition-colors"
            onClick={() => toggleCategory(category.name)}
          >
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  toggleCategoryPermissions(category, !isCategoryFullySelected(category));
                }}
                className={`w-5 h-5 rounded border flex items-center justify-center transition-colors ${
                  isCategoryFullySelected(category)
                    ? 'bg-bambu-green border-bambu-green'
                    : isCategoryPartiallySelected(category)
                    ? 'bg-bambu-green/50 border-bambu-green'
                    : 'border-bambu-gray hover:border-white'
                }`}
              >
                {(isCategoryFullySelected(category) || isCategoryPartiallySelected(category)) && (
                  <Check className="w-3 h-3 text-white" />
                )}
              </button>
              <span className="text-white font-medium">{category.name}</span>
              <span className="text-xs text-bambu-gray">
                ({category.permissions.filter((p) => formData.permissions.includes(p.value)).length}/
                {category.permissions.length})
              </span>
            </div>
            {expandedCategories.has(category.name) ? (
              <ChevronDown className="w-4 h-4 text-bambu-gray" />
            ) : (
              <ChevronRight className="w-4 h-4 text-bambu-gray" />
            )}
          </div>
          {expandedCategories.has(category.name) && (
            <div className="p-3 bg-bambu-dark space-y-2">
              {category.permissions.map((perm) => (
                <label
                  key={perm.value}
                  className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-bambu-dark-secondary cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={formData.permissions.includes(perm.value)}
                    onChange={() => togglePermission(perm.value)}
                    className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark-secondary"
                  />
                  <span className="text-sm text-bambu-gray">{perm.label}</span>
                </label>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );

  return (
    <div className="p-6">
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/settings?tab=users')}
            className="p-2 rounded-lg bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors"
            title="Back to Settings"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-2">
              <Shield className="w-6 h-6 text-bambu-green" />
              Group Management
            </h1>
            <p className="text-sm text-bambu-gray mt-1">
              Manage permission groups for access control
            </p>
          </div>
        </div>
        {hasPermission('groups:create') && (
          <Button
            onClick={() => {
              setShowCreateModal(true);
              resetForm();
            }}
          >
            <Plus className="w-4 h-4" />
            Create Group
          </Button>
        )}
      </div>

      {groupsLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {groups.map((group) => (
            <Card key={group.id}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Shield
                      className={`w-5 h-5 ${
                        group.name === 'Administrators'
                          ? 'text-purple-400'
                          : group.name === 'Operators'
                          ? 'text-blue-400'
                          : group.name === 'Viewers'
                          ? 'text-green-400'
                          : 'text-bambu-gray'
                      }`}
                    />
                    <h3 className="text-lg font-semibold text-white">{group.name}</h3>
                    {group.is_system && (
                      <span className="px-2 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">
                        System
                      </span>
                    )}
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-bambu-gray mb-4">{group.description || 'No description'}</p>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm text-bambu-gray">
                    <Users className="w-4 h-4" />
                    <span>{group.user_count} users</span>
                  </div>
                  <div className="text-xs text-bambu-gray">
                    {group.permissions.length} permissions
                  </div>
                </div>
                <div className="flex gap-2 mt-4 pt-4 border-t border-bambu-dark-tertiary">
                  {hasPermission('groups:update') && (
                    <Button size="sm" variant="ghost" onClick={() => startEdit(group)}>
                      <Edit2 className="w-4 h-4" />
                      Edit
                    </Button>
                  )}
                  {hasPermission('groups:delete') && !group.is_system && (
                    <Button size="sm" variant="ghost" onClick={() => handleDelete(group.id)}>
                      <Trash2 className="w-4 h-4" />
                      Delete
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create/Edit Group Modal */}
      {(showCreateModal || editingGroup) && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={() => {
            setShowCreateModal(false);
            setEditingGroup(null);
            resetForm();
          }}
        >
          <Card
            className="w-full max-w-2xl max-h-[90vh] overflow-y-auto"
            onClick={(e: React.MouseEvent) => e.stopPropagation()}
          >
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Shield className="w-5 h-5 text-bambu-green" />
                  <h2 className="text-lg font-semibold text-white">
                    {editingGroup ? 'Edit Group' : 'Create Group'}
                  </h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowCreateModal(false);
                    setEditingGroup(null);
                    resetForm();
                  }}
                >
                  <X className="w-5 h-5" />
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    Group Name
                  </label>
                  <input
                    type="text"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    disabled={editingGroup?.is_system}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors disabled:opacity-50"
                    placeholder="Enter group name"
                  />
                  {editingGroup?.is_system && (
                    <p className="text-xs text-yellow-400 mt-1">System group names cannot be changed</p>
                  )}
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    Description
                  </label>
                  <textarea
                    value={formData.description}
                    onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                    rows={2}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors resize-none"
                    placeholder="Enter description (optional)"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    Permissions ({formData.permissions.length} selected)
                  </label>
                  {renderPermissionEditor()}
                </div>
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setShowCreateModal(false);
                    setEditingGroup(null);
                    resetForm();
                  }}
                >
                  Cancel
                </Button>
                <Button
                  onClick={editingGroup ? handleUpdate : handleCreate}
                  disabled={createMutation.isPending || updateMutation.isPending || !formData.name.trim()}
                >
                  {(createMutation.isPending || updateMutation.isPending) ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {editingGroup ? 'Saving...' : 'Creating...'}
                    </>
                  ) : (
                    <>
                      <Save className="w-4 h-4" />
                      {editingGroup ? 'Save Changes' : 'Create Group'}
                    </>
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteGroupId !== null && (
        <ConfirmModal
          title="Delete Group"
          message="Are you sure you want to delete this group? Users in this group will lose these permissions."
          confirmText="Delete Group"
          variant="danger"
          onConfirm={() => {
            deleteMutation.mutate(deleteGroupId);
            setDeleteGroupId(null);
          }}
          onCancel={() => setDeleteGroupId(null)}
        />
      )}
    </div>
  );
}
