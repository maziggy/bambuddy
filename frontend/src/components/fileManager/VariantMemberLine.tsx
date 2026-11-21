import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Clock,
  Download,
  FolderSymlink,
  Info,
  MoreVertical,
  Pencil,
  Printer,
  Trash2,
  Unlink,
  Weight,
} from 'lucide-react';
import type { LibraryFileListItem } from '../../api/client';
import { useAuth } from '../../contexts/AuthContext';
import { formatDuration } from '../../utils/date';
import { isSlicedLibraryFile } from '../../utils/libraryFiles';
import { ContextMenu, type ContextMenuItem } from '../ContextMenu';
import type { VariantMemberActions } from './variantTypes';
import type { VariantGroupActions } from './useVariantGroupActions';

// One version of a group: printer model, print time, filament, a hint where
// it lives when that is not the folder on screen, and the actions a plain
// file has (#3628). Shared by the card, the list row and the columns row.
export function VariantMemberLine({
  file,
  groupId,
  memberActions,
  groupActions,
  highlighted = false,
}: {
  file: LibraryFileListItem;
  groupId: number;
  memberActions: VariantMemberActions;
  groupActions: VariantGroupActions;
  highlighted?: boolean;
}) {
  const { t } = useTranslation();
  const { hasPermission, canModify } = useAuth();
  const [menuAnchor, setMenuAnchor] = useState<{ x: number; y: number } | null>(null);

  const name = file.print_name || file.filename;
  const canRead = hasPermission('library:read');
  const canRename = canModify('library', 'update', file.created_by_id);
  const canDelete = canModify('library', 'delete', file.created_by_id);
  const canPrint = hasPermission('queue:create');

  const items: ContextMenuItem[] = [];
  if (isSlicedLibraryFile(file)) {
    items.push({
      label: t('fileManager.variants.printVersion'),
      icon: <Printer className="w-4 h-4 text-bambu-green" />,
      onClick: () => memberActions.onPrintFile(file),
      disabled: !canPrint,
      title: !canPrint ? t('fileManager.noPermissionAddToQueue') : undefined,
    });
  }
  const preview = memberActions.previewItem(file);
  if (preview) items.push(preview);
  items.push(
    {
      label: t('fileManager.details.title'),
      icon: <Info className="w-4 h-4" />,
      onClick: () => memberActions.onDetails(file),
      disabled: !canRead,
      title: !canRead ? t('fileManager.noPermissionPreview') : undefined,
    },
    {
      label: t('common.download'),
      icon: <Download className="w-4 h-4" />,
      onClick: () => memberActions.onDownload(file.id),
      disabled: !canRead,
      title: !canRead ? t('fileManager.noPermissionDownload') : undefined,
    },
    {
      label: t('common.rename'),
      icon: <Pencil className="w-4 h-4" />,
      onClick: () => memberActions.onRename(file),
      disabled: !canRename,
      title: !canRename ? t('fileManager.noPermissionRenameFile') : undefined,
    },
    {
      label: t('fileManager.variants.removeFromGroup'),
      icon: <Unlink className="w-4 h-4" />,
      onClick: () => groupActions.removeFromGroup(file, groupId),
      disabled: !groupActions.canRemove(file),
      title: !groupActions.canRemove(file) ? groupActions.removeBlockedReason(file) : undefined,
    },
    {
      label: t('common.delete'),
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => memberActions.onDelete(file.id),
      danger: true,
      disabled: !canDelete,
      title: !canDelete ? t('fileManager.noPermissionDeleteFile') : undefined,
    },
  );

  // Where an elsewhere version lives, from the server's flag and the folder it
  // reports, whatever filter is active (#3628). Root only when it is in no
  // folder; a folder the server gave no name for is just "another folder".
  const folderHint = !file.variant_elsewhere
    ? null
    : file.folder_id == null
      ? t('fileManager.variants.inLibraryRoot')
      : file.folder_name
        ? t('fileManager.variants.inFolder', { folder: file.folder_name })
        : t('fileManager.variants.inAnotherFolder');

  return (
    <div
      data-testid="variant-member-line"
      data-file-id={file.id}
      className={`flex items-center gap-2 min-w-0 px-2 py-1.5 rounded text-xs text-bambu-gray hover:bg-bambu-dark ${
        highlighted ? 'outline outline-2 outline-yellow-400' : ''
      }`}
      // A line is not "the row": its clicks neither select the group nor open it.
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <Printer className="w-3.5 h-3.5 flex-shrink-0 text-bambu-green" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 min-w-0">
          <span className="font-medium text-white">{file.sliced_for_model || '-'}</span>
          {file.print_time_seconds ? (
            <span className="inline-flex items-center gap-1">
              <Clock className="w-3 h-3" aria-hidden="true" />
              {formatDuration(file.print_time_seconds)}
            </span>
          ) : null}
          {file.filament_used_grams ? (
            <span className="inline-flex items-center gap-1">
              <Weight className="w-3 h-3" aria-hidden="true" />
              {Math.round(file.filament_used_grams)} g
            </span>
          ) : null}
          {folderHint && (
            <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400" data-testid="variant-elsewhere-hint">
              <FolderSymlink className="w-3 h-3" aria-hidden="true" />
              {folderHint}
            </span>
          )}
        </div>
        <div className="truncate" title={name}>
          {name}
        </div>
      </div>
      <button
        type="button"
        aria-label={t('fileManager.variants.versionActions', { name })}
        aria-haspopup="menu"
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          setMenuAnchor({ x: rect.left, y: rect.bottom + 4 });
        }}
        className="p-1.5 rounded flex-shrink-0 hover:bg-bambu-dark-tertiary"
      >
        <MoreVertical className="w-4 h-4" />
      </button>
      {menuAnchor && (
        <ContextMenu x={menuAnchor.x} y={menuAnchor.y} items={items} onClose={() => setMenuAnchor(null)} />
      )}
    </div>
  );
}
