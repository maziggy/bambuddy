import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CalendarClock, ChevronDown, ChevronRight, Layers, MoreVertical, Printer, Trash2, Unlink } from 'lucide-react';
import { api } from '../../api/client';
import { formatDate } from '../../utils/date';
import { formatFileSize } from '../../utils/file';
import type { GroupRow } from '../../pages/fileManager/variantGroups';
import { ContextMenu, type ContextMenuItem } from '../ContextMenu';
import { VariantMemberLine } from './VariantMemberLine';
import { VariantSelectionBox } from './VariantSelectionBox';
import { selectedMemberCount, type VariantGroupViewProps } from './variantTypes';

// A version group as one card of the grid view (#3628): the first version's
// thumbnail, the name, the printer models, and the versions behind a toggle.
export function VariantGroupCard({
  row,
  selectedIds,
  highlightedFileId,
  thumbnailVersions,
  showModified,
  onToggleSelect,
  memberActions,
  groupActions,
}: { row: GroupRow } & VariantGroupViewProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState<{ x: number; y: number } | null>(null);
  const panelId = useId();

  const first = row.thumbnailFile;
  const selectedCount = selectedMemberCount(row, selectedIds);
  const models = [...new Set(row.members.map((m) => m.sliced_for_model).filter((m): m is string => !!m))];
  const totalSize = row.members.reduce((sum, m) => sum + m.file_size, 0);
  const printed = row.members.reduce((sum, m) => sum + m.print_count, 0);
  const highlighted = highlightedFileId !== null && row.members.some((m) => m.id === highlightedFileId);
  const version = thumbnailVersions[first.id];
  const thumbUrl = api.getLibraryFileThumbnailUrl(first.id);

  const menuItems: ContextMenuItem[] = [];
  if (groupActions.hasPrintable(row)) {
    menuItems.push({
      label: t('fileManager.variants.print'),
      icon: <Printer className="w-4 h-4 text-bambu-green" />,
      onClick: () => groupActions.print(row),
      disabled: !groupActions.canPrint(row),
      title: !groupActions.canPrint(row) ? t('fileManager.noPermissionAddToQueue') : undefined,
    });
  }
  menuItems.push(
    {
      label: t('fileManager.variants.ungroup'),
      icon: <Unlink className="w-4 h-4" />,
      onClick: () => groupActions.ungroup(row),
      disabled: !groupActions.canUngroup(row),
      title: !groupActions.canUngroup(row) ? t('fileManager.variants.noPermissionUngroup') : undefined,
    },
    {
      label: t('fileManager.variants.deleteGroup'),
      icon: <Trash2 className="w-4 h-4" />,
      onClick: () => groupActions.deleteFiles(row),
      danger: true,
      disabled: !groupActions.canDeleteFiles(row),
      title: !groupActions.canDeleteFiles(row) ? t('fileManager.variants.noPermissionDeleteGroup') : undefined,
    },
  );

  return (
    <div
      data-testid="variant-group-card"
      data-variant-group-id={row.id}
      data-file-id={first.id}
      data-member-ids={row.members.map((m) => m.id).join(' ')}
      style={highlighted ? { outline: '4px solid #facc15', outlineOffset: '2px' } : undefined}
      className="relative self-start pb-2 min-w-0"
    >
      {/* The cards behind it: a pile, not a single file. */}
      <div aria-hidden="true" className="absolute inset-x-2 bottom-1 h-3 rounded-b-lg border border-t-0 border-bambu-dark-tertiary bg-bambu-dark-secondary" />
      <div aria-hidden="true" className="absolute inset-x-4 bottom-0 h-3 rounded-b-lg border border-t-0 border-bambu-dark-tertiary bg-bambu-dark" />
      <div
        className={`group relative bg-bambu-dark-secondary rounded-lg border transition-all cursor-pointer ${
          selectedCount > 0
            ? 'border-bambu-green ring-1 ring-bambu-green'
            : 'border-bambu-dark-tertiary hover:border-bambu-green/50'
        }`}
        onClick={() => onToggleSelect(row)}
      >
        <div className="aspect-square bg-bambu-dark flex items-center justify-center overflow-hidden rounded-t-lg relative">
          {first.thumbnail_path ? (
            <img
              src={`${thumbUrl}${version ? (thumbUrl.includes('?') ? '&' : '?') + `v=${version}` : ''}`}
              loading="lazy"
              alt={row.name}
              className="w-full h-full object-cover"
            />
          ) : (
            <Layers className="w-12 h-12 text-bambu-gray/30" />
          )}
          <div className="absolute top-2 right-2 text-xs px-1.5 py-0.5 rounded font-medium bg-bambu-green/90 text-white flex items-center gap-1">
            <Layers className="w-3 h-3" aria-hidden="true" />
            {t('fileManager.variants.badge', { count: row.members.length })}
          </div>
        </div>

        <div className="p-3">
          <h3 className="text-sm font-medium text-white truncate" title={row.name}>
            {row.name}
          </h3>
          {models.length > 0 && (
            <div className="mt-1 text-xs text-bambu-gray flex items-center gap-1 min-w-0">
              <Printer className="w-3 h-3 flex-shrink-0" aria-hidden="true" />
              <span className="truncate" title={models.join(' · ')}>{models.join(' · ')}</span>
            </div>
          )}
          <div className="mt-1 text-xs text-bambu-gray">{formatFileSize(totalSize)}</div>
          {printed > 0 && (
            <div className="mt-1 text-xs text-bambu-green">{t('fileManager.printedCount', { count: printed })}</div>
          )}
          {showModified && (
            <div className="mt-1 text-xs text-bambu-gray flex items-center gap-1" title={t('fileManager.lastModified')}>
              <CalendarClock className="w-3 h-3" />
              {formatDate(row.newest.fs_modified_at ?? row.newest.created_at)}
            </div>
          )}
          <div className="mt-2 flex items-center justify-between gap-2">
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={panelId}
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
            onDoubleClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 text-xs text-bambu-green hover:underline"
          >
            {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            {expanded ? t('fileManager.variants.hideVersions') : t('fileManager.variants.showVersions')}
          </button>
          <div
            className="flex-shrink-0 transition-opacity can-hover:opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
            onClick={(e) => e.stopPropagation()}
            onDoubleClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              aria-label={t('fileManager.variants.groupActions', { name: row.name })}
              aria-haspopup="menu"
              onClick={(e) => {
                const rect = e.currentTarget.getBoundingClientRect();
                setMenuAnchor({ x: rect.left, y: rect.bottom + 4 });
              }}
              className="p-1.5 rounded bg-bambu-dark-secondary/90 hover:bg-bambu-dark-tertiary"
            >
              <MoreVertical className="w-4 h-4 text-bambu-gray" />
            </button>
          </div>
          </div>
        </div>

        {expanded && (
          <div id={panelId} role="group" aria-label={t('fileManager.variants.versionsHeading')} className="px-1 pb-2 border-t border-bambu-dark-tertiary">
            {row.members.map((m) => (
              <VariantMemberLine
                key={m.id}
                file={m}
                groupId={row.id}
                memberActions={memberActions}
                groupActions={groupActions}
                highlighted={highlightedFileId === m.id}
              />
            ))}
          </div>
        )}

        {menuAnchor && <ContextMenu x={menuAnchor.x} y={menuAnchor.y} items={menuItems} onClose={() => setMenuAnchor(null)} />}

        <VariantSelectionBox
          selectedCount={selectedCount}
          total={row.members.length}
          label={t('fileManager.variants.selectGroup', { name: row.name })}
          className={`absolute top-2 left-2 ${
            selectedCount > 0 ? '' : 'border-white/30 bg-black/30 can-hover:opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'
          }`}
        />
      </div>
    </div>
  );
}
