import { useState, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Save, Loader2, Search, Check, Minus, Shield, AlertTriangle } from 'lucide-react';
import { api } from '../api/client';
import type { Permission, PermissionCategory } from '../api/client';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { useToast } from '../contexts/ToastContext';

export function GroupEditPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const { showToast } = useToast();
  const isEditing = Boolean(id);

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [search, setSearch] = useState('');
  const [initialized, setInitialized] = useState(false);

  const { data: groupData, isLoading: groupLoading } = useQuery({
    queryKey: ['group', id],
    queryFn: () => api.getGroup(Number(id)),
    enabled: isEditing,
  });

  const { data: permissionsData, isLoading: permissionsLoading } = useQuery({
    queryKey: ['permissions'],
    queryFn: () => api.getPermissions(),
  });

  // Initialize form from fetched group data (once)
  if (isEditing && groupData && !initialized) {
    setName(groupData.name);
    setDescription(groupData.description || '');
    setPermissions(groupData.permissions);
    setInitialized(true);
  }

  const createMutation = useMutation({
    mutationFn: (data: { name: string; description?: string; permissions: Permission[] }) =>
      api.createGroup(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      showToast(t('groups.toast.created'));
      navigate('/settings?tab=users');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const updateMutation = useMutation({
    mutationFn: (data: { name?: string; description?: string; permissions: Permission[] }) =>
      api.updateGroup(Number(id), data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      showToast(t('groups.toast.updated'));
      navigate('/settings?tab=users');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const isSaving = createMutation.isPending || updateMutation.isPending;

  const handleSave = () => {
    if (!name.trim()) {
      showToast(t('groups.toast.enterGroupName'), 'error');
      return;
    }
    if (isEditing) {
      updateMutation.mutate({
        name: name !== groupData?.name ? name : undefined,
        description,
        permissions,
      });
    } else {
      createMutation.mutate({
        name,
        description: description || undefined,
        permissions,
      });
    }
  };

  const togglePermission = (perm: Permission) => {
    setPermissions((prev) =>
      prev.includes(perm) ? prev.filter((p) => p !== perm) : [...prev, perm]
    );
  };

  const toggleCategoryPermissions = (category: PermissionCategory, checked: boolean) => {
    const categoryPerms = category.permissions.map((p) => p.value);
    setPermissions((prev) => {
      const otherPerms = prev.filter((p) => !categoryPerms.includes(p));
      return checked ? [...otherPerms, ...categoryPerms] : otherPerms;
    });
  };

  const isCategoryFullySelected = (category: PermissionCategory) =>
    category.permissions.every((p) => permissions.includes(p.value));

  const isCategoryPartiallySelected = (category: PermissionCategory) => {
    const count = category.permissions.filter((p) => permissions.includes(p.value)).length;
    return count > 0 && count < category.permissions.length;
  };

  const selectAll = () => {
    if (permissionsData) {
      setPermissions(permissionsData.all_permissions);
    }
  };

  const clearAll = () => {
    setPermissions([]);
  };

  const searchLower = search.toLowerCase();

  const filteredCategories = useMemo(() => {
    if (!permissionsData) return [];
    if (!searchLower) return permissionsData.categories;
    return permissionsData.categories
      .map((cat) => ({
        ...cat,
        permissions: cat.permissions.filter((p) =>
          p.label.toLowerCase().includes(searchLower)
        ),
      }))
      .filter((cat) => cat.permissions.length > 0);
  }, [permissionsData, searchLower]);

  const totalPermissions = permissionsData?.all_permissions.length ?? 0;

  if (groupLoading || permissionsLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate('/settings?tab=users')}
          className="p-2 rounded-lg hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <h1 className="text-xl font-bold text-white">
          {isEditing ? t('groups.editor.title') : t('groups.editor.createTitle')}
        </h1>
      </div>

      {/* System group warning */}
      {isEditing && groupData?.is_system && (
        <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20 text-yellow-400 text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {t('groups.form.systemGroupWarning')}
        </div>
      )}

      {/* Name + Description */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-white mb-2">{t('groups.form.groupName')}</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={isEditing && groupData?.is_system}
            className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors disabled:opacity-50"
            placeholder={t('groups.form.groupNamePlaceholder')}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-white mb-2">{t('groups.form.description')}</label>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
            placeholder={t('groups.form.descriptionPlaceholder')}
          />
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <span className="text-sm text-bambu-gray">
            {t('groups.editor.permissionsSelected', { count: permissions.length })} / {totalPermissions}
          </span>
          <Button size="sm" variant="ghost" onClick={selectAll}>
            {t('groups.editor.selectAll')}
          </Button>
          <Button size="sm" variant="ghost" onClick={clearAll}>
            {t('groups.editor.clearAll')}
          </Button>
        </div>
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-bambu-gray" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('groups.editor.search')}
            className="pl-9 pr-4 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors w-64"
          />
        </div>
      </div>

      {/* Permission grid */}
      {filteredCategories.length === 0 ? (
        <div className="text-center py-12 text-bambu-gray">
          {t('groups.editor.noResults')}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filteredCategories.map((category) => {
            // Use the full (unfiltered) category for selection logic
            const fullCategory = permissionsData!.categories.find((c) => c.name === category.name)!;
            const selectedCount = fullCategory.permissions.filter((p) => permissions.includes(p.value)).length;
            const totalCount = fullCategory.permissions.length;
            const fullySelected = isCategoryFullySelected(fullCategory);
            const partiallySelected = isCategoryPartiallySelected(fullCategory);

            return (
              <Card key={category.name}>
                <div className="sticky top-0 z-10 flex items-center justify-between px-4 py-3 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary rounded-t-xl">
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => toggleCategoryPermissions(fullCategory, !fullySelected)}
                      className={`w-5 h-5 rounded border flex items-center justify-center transition-colors shrink-0 ${
                        fullySelected
                          ? 'bg-bambu-green border-bambu-green'
                          : partiallySelected
                          ? 'bg-bambu-green/50 border-bambu-green'
                          : 'border-bambu-gray hover:border-white'
                      }`}
                    >
                      {fullySelected && <Check className="w-3 h-3 text-white" />}
                      {partiallySelected && !fullySelected && <Minus className="w-3 h-3 text-white" />}
                    </button>
                    <Shield className="w-4 h-4 text-bambu-gray shrink-0" />
                    <span className="text-white font-medium text-sm">{category.name}</span>
                  </div>
                  <span className="text-xs text-bambu-gray tabular-nums">
                    {selectedCount}/{totalCount}
                  </span>
                </div>
                <div className="p-3 space-y-1">
                  {category.permissions.map((perm) => (
                    <label
                      key={perm.value}
                      className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-bambu-dark-secondary cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={permissions.includes(perm.value)}
                        onChange={() => togglePermission(perm.value)}
                        className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark-secondary"
                      />
                      <span className="text-sm text-bambu-gray">{perm.label}</span>
                    </label>
                  ))}
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* Spacer for fixed bottom bar */}
      <div className="h-16" />

      {/* Fixed bottom bar */}
      <div className="fixed bottom-0 left-0 right-0 z-20 px-6 py-3 bg-bambu-dark-secondary border-t border-bambu-dark-tertiary flex items-center justify-center gap-3">
        <Button variant="secondary" onClick={() => navigate('/settings?tab=users')}>
          {t('common.cancel')}
        </Button>
        <Button onClick={handleSave} disabled={isSaving || !name.trim()}>
          {isSaving ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('common.saving')}
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              {t('common.save')}
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
