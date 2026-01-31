import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Plus, Edit2, Trash2, Save, Loader2, Users as UsersIcon, Shield, ArrowLeft } from 'lucide-react';
import { api } from '../api/client';
import type { UserCreate, UserUpdate } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { Button } from '../components/Button';
import { Card, CardContent, CardHeader } from '../components/Card';
import { ConfirmModal } from '../components/ConfirmModal';

export function UsersPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user: currentUser } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingUser, setEditingUser] = useState<number | null>(null);
  const [deleteUserId, setDeleteUserId] = useState<number | null>(null);
  const [formData, setFormData] = useState<UserCreate>({
    username: '',
    password: '',
    role: 'user',
  });

  // Close modal on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && showCreateModal) {
        setShowCreateModal(false);
        setFormData({ username: '', password: '', role: 'user' });
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showCreateModal]);

  const { data: users = [], isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
  });

  const createMutation = useMutation({
    mutationFn: (data: UserCreate) => api.createUser(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setShowCreateModal(false);
      setFormData({ username: '', password: '', role: 'user' });
      showToast(t('users.toast.userCreated'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: UserUpdate }) => api.updateUser(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setEditingUser(null);
      setFormData({ username: '', password: '', role: 'user' });
      showToast(t('users.toast.userUpdated'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteUser(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      showToast(t('users.toast.userDeleted'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const handleCreate = () => {
    if (!formData.username || !formData.password) {
      showToast(t('users.toast.fillRequiredFields'), 'error');
      return;
    }
    createMutation.mutate(formData);
  };

  const handleUpdate = (id: number) => {
    const updateData: UserUpdate = {
      username: formData.username || undefined,
      password: formData.password || undefined,
      role: formData.role,
    };
    // Remove password if empty
    if (!updateData.password) {
      delete updateData.password;
    }
    updateMutation.mutate({ id, data: updateData });
  };

  const handleDelete = (id: number) => {
    setDeleteUserId(id);
  };

  const startEdit = (user: { id: number; username: string; role: string }) => {
    setEditingUser(user.id);
    setFormData({
      username: user.username,
      password: '',
      role: user.role,
    });
  };

  if (currentUser?.role !== 'admin') {
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
              {t('users.description')}
            </p>
          </div>
        </div>
        <Button
          onClick={() => {
            setShowCreateModal(true);
            setFormData({ username: '', password: '', role: 'user' });
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
                    {t('users.table.role')}
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
                      {editingUser === user.id ? (
                        <input
                          type="text"
                          value={formData.username}
                          onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                          className="px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green"
                        />
                      ) : (
                        user.username
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      {editingUser === user.id ? (
                        <select
                          value={formData.role}
                          onChange={(e) => setFormData({ ...formData, role: e.target.value as 'admin' | 'user' })}
                          className="px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green"
                        >
                          <option value="user">{t('users.roleUser')}</option>
                          <option value="admin">{t('users.roleAdmin')}</option>
                        </select>
                      ) : (
                        <span className={`px-3 py-1 rounded-full text-xs font-medium ${
                          user.role === 'admin'
                            ? 'bg-purple-500/20 text-purple-300'
                            : 'bg-blue-500/20 text-blue-300'
                        }`}>
                          {user.role}
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      <span className={`px-3 py-1 rounded-full text-xs font-medium ${
                        user.is_active
                          ? 'bg-bambu-green/20 text-bambu-green'
                          : 'bg-red-500/20 text-red-400'
                      }`}>
                        {user.is_active ? t('users.statusActive') : t('users.statusInactive')}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                      {editingUser === user.id ? (
                        <div className="flex items-center gap-2">
                          <Button
                            size="sm"
                            onClick={() => handleUpdate(user.id)}
                            disabled={updateMutation.isPending}
                          >
                            {updateMutation.isPending ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <Save className="w-4 h-4" />
                            )}
                            {t('users.save')}
                          </Button>
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => {
                              setEditingUser(null);
                              setFormData({ username: '', password: '', role: 'user' });
                            }}
                          >
                            {t('users.cancel')}
                          </Button>
                        </div>
                      ) : (
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
                        </div>
                      )}
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
            setFormData({ username: '', password: '', role: 'user' });
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
                  <h2 className="text-lg font-semibold text-white">{t('users.createUser')}</h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowCreateModal(false);
                    setFormData({ username: '', password: '', role: 'user' });
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
                    {t('users.table.username')}
                  </label>
                  <input
                    type="text"
                    value={formData.username}
                    onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('users.placeholderUsername')}
                    autoComplete="username"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('users.password')}
                  </label>
                  <input
                    type="password"
                    value={formData.password}
                    onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('users.placeholderPassword')}
                    autoComplete="new-password"
                    minLength={6}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('users.table.role')}
                  </label>
                  <select
                    value={formData.role}
                    onChange={(e) => setFormData({ ...formData, role: e.target.value as 'admin' | 'user' })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                  >
                    <option value="user">{t('users.roleUser')}</option>
                    <option value="admin">{t('users.roleAdmin')}</option>
                  </select>
                </div>
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setShowCreateModal(false);
                    setFormData({ username: '', password: '', role: 'user' });
                  }}
                >
                  {t('users.cancel')}
                </Button>
                <Button
                  onClick={handleCreate}
                  disabled={createMutation.isPending || !formData.username || !formData.password}
                >
                  {createMutation.isPending ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('users.creating')}
                    </>
                  ) : (
                    <>
                      <Plus className="w-4 h-4" />
                      {t('users.createUser')}
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
          title={t('users.deleteUser')}
          message={t('users.deleteConfirmMessage')}
          confirmText={t('users.deleteUser')}
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
