import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Plus, Edit2, Trash2, Save, Loader2, Users as UsersIcon, Shield, ArrowLeft, RotateCcw } from 'lucide-react';
import { api } from '../api/client';
import type { UserCreate, UserUpdate, UserResponse } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { Button } from '../components/Button';
import { Card, CardContent, CardHeader } from '../components/Card';
import { ConfirmModal } from '../components/ConfirmModal';

interface FormData extends UserCreate {
  group_ids: number[];
  confirmPassword: string;
  email?: string;
}

export function UsersPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { user: currentUser, hasPermission } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [editingUserId, setEditingUserId] = useState<number | null>(null);
  const [deleteUserId, setDeleteUserId] = useState<number | null>(null);
  const [formData, setFormData] = useState<FormData>({
    username: '',
    password: '',
    email: '',
    confirmPassword: '',
    role: 'user',
    group_ids: [],
  });

  // Check if advanced auth is enabled
  const { data: advancedAuthStatus = { advanced_auth_enabled: false, smtp_configured: false } } = useQuery({
    queryKey: ['advancedAuthStatus'],
    queryFn: () => api.getAdvancedAuthStatus(),
  });

  // Close modal on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (showCreateModal) {
          setShowCreateModal(false);
          setFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
        }
        if (showEditModal) {
          setShowEditModal(false);
          setEditingUserId(null);
          setFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showCreateModal, showEditModal]);

  const { data: users = [], isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
    enabled: hasPermission('users:read'),
  });

  const { data: groups = [] } = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.getGroups(),
    enabled: hasPermission('groups:read'),
  });

  const createMutation = useMutation({
    mutationFn: (data: UserCreate) => api.createUser(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      setShowCreateModal(false);
      setFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
      showToast(t('users.toast.created'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: UserUpdate }) => api.updateUser(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      setShowEditModal(false);
      setEditingUserId(null);
      setFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
      showToast(t('users.toast.updated'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteUser(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      showToast(t('users.toast.deleted'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const resetPasswordMutation = useMutation({
    mutationFn: (userId: number) => api.resetUserPassword({ user_id: userId }),
    onSuccess: (data) => {
      showToast(data.message, 'success');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // Validation for create user button
  const isCreateButtonDisabled = useMemo(() => {
    if (createMutation.isPending || !formData.username) {
      return true;
    }
    if (advancedAuthStatus?.advanced_auth_enabled) {
      // When advanced auth is enabled, require email (password is auto-generated)
      return !formData.email;
    }
    // When advanced auth is disabled, require valid password
    return !formData.password || formData.password !== formData.confirmPassword || formData.password.length < 6;
  }, [
    createMutation.isPending,
    formData.username,
    formData.email,
    formData.password,
    formData.confirmPassword,
    advancedAuthStatus?.advanced_auth_enabled
  ]);

  const handleCreate = () => {
    // Use the status from the query hook
    const advancedAuthEnabled = advancedAuthStatus?.advanced_auth_enabled || false;
    
    if (!formData.username) {
      showToast(t('users.toast.fillRequired'), 'error');
      return;
    }
    
    // Email is required when advanced auth is enabled
    if (advancedAuthEnabled && !formData.email) {
      showToast('Email is required when advanced authentication is enabled', 'error');
      return;
    }
    
    // Password validation only when advanced auth is disabled
    if (!advancedAuthEnabled) {
      if (!formData.password) {
        showToast(t('users.toast.fillRequired'), 'error');
        return;
      }
      if (formData.password !== formData.confirmPassword) {
        showToast(t('users.toast.passwordsDoNotMatch'), 'error');
        return;
      }
      if (formData.password.length < 6) {
        showToast(t('users.toast.passwordTooShort'), 'error');
        return;
      }
    }
    
    createMutation.mutate({
      username: formData.username,
      password: advancedAuthEnabled ? undefined : formData.password,
      email: formData.email || undefined,
      role: formData.role,
      group_ids: formData.group_ids.length > 0 ? formData.group_ids : undefined,
    });
  };

  const handleUpdate = (id: number) => {
    // Validate password confirmation if a new password is being set
    if (formData.password) {
      if (formData.password !== formData.confirmPassword) {
        showToast(t('users.toast.passwordsDoNotMatch'), 'error');
        return;
      }
      if (formData.password.length < 6) {
        showToast(t('users.toast.passwordTooShort'), 'error');
        return;
      }
    }
    const updateData: UserUpdate = {
      username: formData.username || undefined,
      password: formData.password || undefined,
      email: formData.email || undefined,
      role: formData.role,
      group_ids: formData.group_ids,
    };
    // Remove password if empty
    if (!updateData.password) {
      delete updateData.password;
    }
    // Remove email if empty
    if (!updateData.email) {
      delete updateData.email;
    }
    updateMutation.mutate({ id, data: updateData });
  };

  const handleDelete = (id: number) => {
    setDeleteUserId(id);
  };

  const startEdit = (user: UserResponse) => {
    setEditingUserId(user.id);
    setFormData({
      username: user.username,
      password: '',
      email: user.email || '',
      confirmPassword: '',
      role: user.role,
      group_ids: user.groups?.map(g => g.id) || [],
    });
    setShowEditModal(true);
  };

  const closeEditModal = () => {
    setShowEditModal(false);
    setEditingUserId(null);
    setFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
  };

  const toggleGroup = (groupId: number) => {
    setFormData(prev => ({
      ...prev,
      group_ids: prev.group_ids.includes(groupId)
        ? prev.group_ids.filter(id => id !== groupId)
        : [...prev.group_ids, groupId],
    }));
  };

  if (!hasPermission('users:read')) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="py-6">
            <div className="flex items-center gap-3 text-red-400">
              <Shield className="w-5 h-5" />
              <p className="text-white">{t('users.noPermission')}</p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/settings?tab=users')}
            className="p-2 rounded-lg bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors"
            title={t('users.backToSettings')}
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-2">
              <UsersIcon className="w-6 h-6 text-bambu-green" />
              {t('users.title')}
            </h1>
            <p className="text-sm text-bambu-gray mt-1">
              {t('users.subtitle')}
            </p>
          </div>
        </div>
        <Button
          onClick={() => {
            setShowCreateModal(true);
            setFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
          }}
        >
          <Plus className="w-4 h-4" />
          {t('users.createUser')}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
        </div>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-bambu-dark-tertiary">
              <thead>
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-bambu-gray uppercase tracking-wider">
                    {t('users.table.username')}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-bambu-gray uppercase tracking-wider">
                    {t('users.table.groups')}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-bambu-gray uppercase tracking-wider">
                    {t('users.table.status')}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-bambu-gray uppercase tracking-wider">
                    {t('users.table.actions')}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-bambu-dark-tertiary">
                {users.map((user) => (
                  <tr key={user.id} className="hover:bg-bambu-dark-tertiary/50 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-white">
                      {user.username}
                    </td>
                    <td className="px-6 py-4 text-sm">
                      <div className="flex flex-wrap gap-1">
                        {user.is_admin && (
                          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-500/20 text-purple-300">
                            {t('users.admin')}
                          </span>
                        )}
                        {user.groups?.map(group => (
                          <span
                            key={group.id}
                            className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                              group.name === 'Administrators'
                                ? 'bg-purple-500/20 text-purple-300'
                                : group.name === 'Operators'
                                ? 'bg-blue-500/20 text-blue-300'
                                : group.name === 'Viewers'
                                ? 'bg-green-500/20 text-green-300'
                                : 'bg-gray-500/20 text-gray-300'
                            }`}
                          >
                            {group.name}
                          </span>
                        ))}
                        {(!user.groups || user.groups.length === 0) && !user.is_admin && (
                          <span className="text-bambu-gray">{t('users.noGroups')}</span>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      <span className={`px-3 py-1 rounded-full text-xs font-medium ${
                        user.is_active
                          ? 'bg-bambu-green/20 text-bambu-green'
                          : 'bg-red-500/20 text-red-400'
                      }`}>
                        {user.is_active ? t('users.active') : t('users.inactive')}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                      <div className="flex items-center gap-2">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => startEdit(user)}
                        >
                          <Edit2 className="w-4 h-4" />
                          {t('users.edit')}
                        </Button>
                        {user.id !== currentUser?.id && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleDelete(user.id)}
                          >
                            <Trash2 className="w-4 h-4" />
                            {t('users.delete')}
                          </Button>
                        )}
                        {advancedAuthStatus?.advanced_auth_enabled && user.email && user.id !== currentUser?.id && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => resetPasswordMutation.mutate(user.id)}
                            disabled={resetPasswordMutation.isPending}
                          >
                            <RotateCcw className="w-4 h-4" />
                            {t('users.resetPassword') || 'Reset Password'}
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Create User Modal */}
      {showCreateModal && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={() => {
            setShowCreateModal(false);
            setFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
          }}
        >
          <Card
            className="w-full max-w-md"
            onClick={(e: React.MouseEvent) => e.stopPropagation()}
          >
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <UsersIcon className="w-5 h-5 text-bambu-green" />
                  <h2 className="text-lg font-semibold text-white">{t('users.modal.createUser')}</h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowCreateModal(false);
                    setFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
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
                    {t('users.form.username')}
                  </label>
                  <input
                    type="text"
                    value={formData.username}
                    onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('users.form.usernamePlaceholder')}
                    autoComplete="username"
                  />
                </div>
                {advancedAuthStatus?.advanced_auth_enabled && (
                  <div>
                    <label className="block text-sm font-medium text-white mb-2">
                      {t('users.form.email') || 'Email'}
                    </label>
                    <input
                      type="email"
                      value={formData.email}
                      onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                      className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                      placeholder={t('users.form.emailPlaceholder') || 'user@example.com'}
                      required={advancedAuthStatus?.advanced_auth_enabled}
                    />
                  </div>
                )}
                {!advancedAuthStatus?.advanced_auth_enabled && (
                  <>
                    <div>
                      <label className="block text-sm font-medium text-white mb-2">
                        {t('users.form.password')}
                      </label>
                      <input
                        type="password"
                        value={formData.password}
                        onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                        className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                        placeholder={t('users.form.passwordPlaceholder')}
                        autoComplete="new-password"
                        minLength={6}
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-white mb-2">
                        {t('users.form.confirmPassword')}
                      </label>
                      <input
                        type="password"
                        value={formData.confirmPassword}
                        onChange={(e) => setFormData({ ...formData, confirmPassword: e.target.value })}
                        className={`w-full px-4 py-3 bg-bambu-dark-secondary border rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors ${
                          formData.confirmPassword && formData.password !== formData.confirmPassword
                            ? 'border-red-500'
                            : 'border-bambu-dark-tertiary'
                        }`}
                        placeholder={t('users.form.confirmPasswordPlaceholder')}
                        autoComplete="new-password"
                        minLength={6}
                      />
                      {formData.confirmPassword && formData.password !== formData.confirmPassword && (
                        <p className="text-red-400 text-xs mt-1">{t('users.toast.passwordsDoNotMatch')}</p>
                      )}
                    </div>
                  </>
                )}
                {advancedAuthStatus?.advanced_auth_enabled && (
                  <div className="bg-bambu-dark-secondary/50 border border-bambu-green/20 rounded-lg p-3">
                    <p className="text-sm text-bambu-gray">
                      {t('users.form.autoGeneratedPassword') || 'A secure password will be automatically generated and emailed to the user.'}
                    </p>
                  </div>
                )}
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('users.form.groups')}
                  </label>
                  <div className="space-y-2 max-h-40 overflow-y-auto p-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
                    {groups.map(group => (
                      <label
                        key={group.id}
                        className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-bambu-dark-tertiary cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={formData.group_ids.includes(group.id)}
                          onChange={() => toggleGroup(group.id)}
                          className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark"
                        />
                        <span className="text-sm text-white">{group.name}</span>
                        {group.is_system && (
                          <span className="text-xs text-yellow-400">({t('users.system')})</span>
                        )}
                      </label>
                    ))}
                    {groups.length === 0 && (
                      <p className="text-sm text-bambu-gray">{t('users.noGroupsAvailable')}</p>
                    )}
                  </div>
                </div>
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setShowCreateModal(false);
                    setFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
                  }}
                >
                  {t('users.modal.cancel')}
                </Button>
                <Button
                  onClick={handleCreate}
                  disabled={isCreateButtonDisabled}
                >
                  {createMutation.isPending ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('users.modal.creating')}
                    </>
                  ) : (
                    <>
                      <Plus className="w-4 h-4" />
                      {t('users.modal.createUser')}
                    </>
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Edit User Modal */}
      {showEditModal && editingUserId !== null && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={closeEditModal}
        >
          <Card
            className="w-full max-w-md"
            onClick={(e: React.MouseEvent) => e.stopPropagation()}
          >
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Edit2 className="w-5 h-5 text-bambu-green" />
                  <h2 className="text-lg font-semibold text-white">{t('users.modal.editUser')}</h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={closeEditModal}
                >
                  <X className="w-5 h-5" />
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('users.form.username')}
                  </label>
                  <input
                    type="text"
                    value={formData.username}
                    onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('users.form.usernamePlaceholder')}
                    autoComplete="username"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('users.form.email') || 'Email'} <span className="text-bambu-gray font-normal">({t('users.form.optional') || 'optional'})</span>
                  </label>
                  <input
                    type="email"
                    value={formData.email}
                    onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('users.form.emailPlaceholder') || 'user@example.com'}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('users.form.password')} <span className="text-bambu-gray font-normal">({t('users.form.leaveBlankToKeep')})</span>
                  </label>
                  <input
                    type="password"
                    value={formData.password}
                    onChange={(e) => setFormData({ ...formData, password: e.target.value, confirmPassword: '' })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('users.form.newPasswordPlaceholder')}
                    autoComplete="new-password"
                    minLength={6}
                  />
                </div>
                {formData.password && (
                  <div>
                    <label className="block text-sm font-medium text-white mb-2">
                      {t('users.form.confirmPassword')}
                    </label>
                    <input
                      type="password"
                      value={formData.confirmPassword}
                      onChange={(e) => setFormData({ ...formData, confirmPassword: e.target.value })}
                      className={`w-full px-4 py-3 bg-bambu-dark-secondary border rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors ${
                        formData.confirmPassword && formData.password !== formData.confirmPassword
                          ? 'border-red-500'
                          : 'border-bambu-dark-tertiary'
                      }`}
                      placeholder={t('users.form.confirmNewPasswordPlaceholder')}
                      autoComplete="new-password"
                      minLength={6}
                    />
                    {formData.confirmPassword && formData.password !== formData.confirmPassword && (
                      <p className="text-red-400 text-xs mt-1">{t('users.toast.passwordsDoNotMatch')}</p>
                    )}
                  </div>
                )}
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('users.form.groups')}
                  </label>
                  <div className="space-y-2 max-h-40 overflow-y-auto p-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
                    {groups.map(group => (
                      <label
                        key={group.id}
                        className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-bambu-dark-tertiary cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={formData.group_ids.includes(group.id)}
                          onChange={() => toggleGroup(group.id)}
                          className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark"
                        />
                        <span className="text-sm text-white">{group.name}</span>
                        {group.is_system && (
                          <span className="text-xs text-yellow-400">({t('users.system')})</span>
                        )}
                      </label>
                    ))}
                    {groups.length === 0 && (
                      <p className="text-sm text-bambu-gray">{t('users.noGroupsAvailable')}</p>
                    )}
                  </div>
                </div>
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <Button
                  variant="secondary"
                  onClick={closeEditModal}
                >
                  {t('users.modal.cancel')}
                </Button>
                <Button
                  onClick={() => handleUpdate(editingUserId)}
                  disabled={updateMutation.isPending || !formData.username || !!(formData.password && (formData.password !== formData.confirmPassword || formData.password.length < 6))}
                >
                  {updateMutation.isPending ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('users.modal.saving')}
                    </>
                  ) : (
                    <>
                      <Save className="w-4 h-4" />
                      {t('users.modal.saveChanges')}
                    </>
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteUserId !== null && (
        <ConfirmModal
          title={t('users.deleteModal.title')}
          message={t('users.deleteModal.message')}
          confirmText={t('users.deleteModal.confirm')}
          variant="danger"
          onConfirm={() => {
            deleteMutation.mutate(deleteUserId);
            setDeleteUserId(null);
          }}
          onCancel={() => setDeleteUserId(null)}
        />
      )}
    </div>
  );
}
