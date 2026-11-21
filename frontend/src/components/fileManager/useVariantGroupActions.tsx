import { useCallback, useState, type ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, type LibraryFileListItem } from '../../api/client';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { isSlicedLibraryFile } from '../../utils/libraryFiles';
import type { GroupRow } from '../../pages/fileManager/variantGroups';
import { invalidateAfterLibraryDelete } from '../../pages/fileManager/invalidate';
import { ConfirmModal } from '../ConfirmModal';

export interface VariantGroupActions {
  /** Print the group as one job with printer alternatives. */
  print: (row: GroupRow) => void;
  canPrint: (row: GroupRow) => boolean;
  /** At least one version is sliced, so there is something to print. */
  hasPrintable: (row: GroupRow) => boolean;
  ungroup: (row: GroupRow) => void;
  canUngroup: (row: GroupRow) => boolean;
  deleteFiles: (row: GroupRow) => void;
  canDeleteFiles: (row: GroupRow) => boolean;
  removeFromGroup: (file: LibraryFileListItem, groupId: number) => void;
  canRemove: (file: LibraryFileListItem) => boolean;
}

type Confirm = { kind: 'ungroup' | 'delete'; row: GroupRow };

// The actions of a version group (#3628), shared by the grid card, the list
// row and the columns row: permission checks, the requests, and the confirm
// dialogs, which the caller renders once as `dialogs`. Printing is the
// page's, since the print dialog lives there.
export function useVariantGroupActions({
  onPrintGroup,
  onFilesDeleted,
}: {
  onPrintGroup: (row: GroupRow) => void;
  onFilesDeleted: (ids: number[]) => void;
}): { actions: VariantGroupActions; dialogs: ReactNode } {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission, canModify } = useAuth();
  const [confirm, setConfirm] = useState<Confirm | null>(null);

  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['library-files'] });
    queryClient.invalidateQueries({ queryKey: ['variant-group'] });
  }, [queryClient]);

  const ungroupMutation = useMutation({
    mutationFn: (groupId: number) => api.deleteVariantGroup(groupId),
    onSuccess: () => {
      refresh();
      showToast(t('fileManager.variants.ungrouped'), 'success');
      setConfirm(null);
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
      setConfirm(null);
    },
  });

  const removeMutation = useMutation({
    mutationFn: ({ groupId, fileId }: { groupId: number; fileId: number }) =>
      api.removeVariantGroupMember(groupId, fileId),
    onSuccess: () => {
      refresh();
      showToast(t('fileManager.variants.removed'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const deleteMutation = useMutation({
    mutationFn: (ids: number[]) => api.bulkDeleteLibrary(ids, []),
    onSuccess: (_, ids) => {
      refresh();
      invalidateAfterLibraryDelete(queryClient);
      showToast(t('fileManager.toast.filesDeleted', { count: ids.length }), 'success');
      onFilesDeleted(ids);
      setConfirm(null);
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
      setConfirm(null);
    },
  });

  const hasPrintable = useCallback((row: GroupRow) => row.members.some(isSlicedLibraryFile), []);
  const canPrint = useCallback(
    (row: GroupRow) => hasPrintable(row) && hasPermission('queue:create'),
    [hasPrintable, hasPermission],
  );
  // Ungroup and delete act on every version, so they are offered only to
  // someone who may change every one of them.
  const canUngroup = useCallback(
    (row: GroupRow) => row.members.every((m) => canModify('library', 'update', m.created_by_id)),
    [canModify],
  );
  const canDeleteFiles = useCallback(
    (row: GroupRow) => row.members.every((m) => canModify('library', 'delete', m.created_by_id)),
    [canModify],
  );
  const canRemove = useCallback(
    (file: LibraryFileListItem) => canModify('library', 'update', file.created_by_id),
    [canModify],
  );

  const actions: VariantGroupActions = {
    print: (row) => {
      if (canPrint(row)) onPrintGroup(row);
    },
    canPrint,
    hasPrintable,
    ungroup: (row) => {
      if (canUngroup(row)) setConfirm({ kind: 'ungroup', row });
    },
    canUngroup,
    deleteFiles: (row) => {
      if (canDeleteFiles(row)) setConfirm({ kind: 'delete', row });
    },
    canDeleteFiles,
    removeFromGroup: (file, groupId) => {
      if (canRemove(file)) removeMutation.mutate({ groupId, fileId: file.id });
    },
    canRemove,
  };

  // The confirm says how many of the versions sit in other folders: deleting
  // the group deletes those too, which the folder on screen does not show.
  const deleteMessage = (row: GroupRow) => {
    const message = t('fileManager.variants.deleteGroupConfirm', { count: row.members.length, name: row.name });
    const elsewhere = row.members.filter((m) => m.variant_elsewhere).length;
    return elsewhere > 0 ? `${message} ${t('fileManager.variants.deleteGroupElsewhere', { count: elsewhere })}` : message;
  };

  const dialogs = confirm ? (
    confirm.kind === 'ungroup' ? (
      <ConfirmModal
        title={t('fileManager.variants.ungroupTitle')}
        message={t('fileManager.variants.ungroupConfirm', { count: confirm.row.members.length, name: confirm.row.name })}
        confirmText={t('fileManager.variants.ungroup')}
        variant="warning"
        isLoading={ungroupMutation.isPending}
        loadingText={t('fileManager.variants.ungrouping')}
        onConfirm={() => ungroupMutation.mutate(confirm.row.id)}
        onCancel={() => setConfirm(null)}
      />
    ) : (
      <ConfirmModal
        title={t('fileManager.variants.deleteGroupTitle', { count: confirm.row.members.length })}
        message={deleteMessage(confirm.row)}
        confirmText={t('common.delete')}
        variant="danger"
        isLoading={deleteMutation.isPending}
        loadingText={t('fileManager.deleting')}
        onConfirm={() => deleteMutation.mutate(confirm.row.members.map((m) => m.id))}
        onCancel={() => setConfirm(null)}
      />
    )
  ) : null;

  return { actions, dialogs };
}
