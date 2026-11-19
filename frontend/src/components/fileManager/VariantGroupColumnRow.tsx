import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CalendarClock, ChevronDown, ChevronRight, Layers } from 'lucide-react';
import { api } from '../../api/client';
import { formatDate } from '../../utils/date';
import { formatFileSize } from '../../utils/file';
import type { GroupRow } from '../../pages/fileManager/variantGroups';
import { VariantGroupActionStrip } from './VariantGroupActionStrip';
import { VariantMemberLine } from './VariantMemberLine';
import { VariantSelectionBox } from './VariantSelectionBox';
import { groupSelection } from '../../pages/fileManager/variantSelection';
import { dragSourceProps, type VariantGroupViewProps } from './variantTypes';

// A version group as one row of a Miller column (#3628). The row sits where a
// file's row would; the versions are listed in a pane under it, opened with
// the chevron.
export function VariantGroupColumnRow({
  row,
  isFocused,
  onSelect,
  selectedIds,
  highlightedFileId,
  thumbnailVersions,
  showModified,
  memberActions,
  groupActions,
  groupDrag,
}: {
  row: GroupRow;
  isFocused: boolean;
  /** A click on the row: focuses it and toggles all its versions. */
  onSelect: (row: GroupRow) => void;
} & VariantGroupViewProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const panelId = useId();

  const first = row.thumbnailFile;
  const { count: selectedCount, total: selectableCount } = groupSelection(row, selectedIds);
  const highlighted = highlightedFileId !== null && row.members.some((m) => m.id === highlightedFileId);
  const version = thumbnailVersions[first.id];
  const thumbUrl = api.getLibraryFileThumbnailUrl(first.id);
  const totalSize = row.members.reduce((sum, m) => sum + m.file_size, 0);
  const printed = row.members.reduce((sum, m) => sum + m.print_count, 0);

  return (
    <div
      data-testid="variant-group-column-row"
      data-variant-group-id={row.id}
      data-file-id={first.id}
      data-member-ids={row.members.map((m) => m.id).join(' ')}
      style={highlighted ? { outline: '2px solid #facc15', outlineOffset: '-2px' } : undefined}
    >
      <div
        onClick={() => onSelect(row)}
        {...dragSourceProps(row, groupDrag)}
        className={`group flex items-center gap-3 px-3 py-2 cursor-pointer transition-colors ${
          selectedCount > 0 ? 'bg-bambu-green/10' : 'hover:bg-bambu-dark'
        } ${isFocused ? 'ring-1 ring-inset ring-bambu-green/60' : ''}`}
        title={row.name}
      >
        <VariantSelectionBox
          selectedCount={selectedCount}
          total={selectableCount}
          label={t('fileManager.variants.selectGroup', { name: row.name })}
          onToggle={() => onSelect(row)}
          tabIndex={-1}
          size="w-4 h-4"
        />
        <div className="w-10 h-10 rounded bg-bambu-dark flex-shrink-0 overflow-hidden flex items-center justify-center">
          {first.thumbnail_path ? (
            <img
              src={`${thumbUrl}${version ? (thumbUrl.includes('?') ? '&' : '?') + `v=${version}` : ''}`}
              loading="lazy"
              alt=""
              className="w-full h-full object-cover"
            />
          ) : (
            <Layers className="w-5 h-5 text-bambu-gray/50" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm text-white truncate">{row.name}</div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-bambu-gray">
            <span className="inline-flex items-center gap-1 text-bambu-green">
              <Layers className="w-3 h-3" aria-hidden="true" />
              {t('fileManager.variants.badge', { count: row.members.length })}
            </span>
            <span>{formatFileSize(totalSize)}</span>
            {printed > 0 && <span>{printed}x</span>}
            {showModified && (
              <span className="flex items-center gap-1 truncate" title={t('fileManager.lastModified')}>
                <CalendarClock className="w-3 h-3 flex-shrink-0" />
                {formatDate(row.newest.fs_modified_at ?? row.newest.created_at)}
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          tabIndex={-1}
          aria-expanded={expanded}
          aria-controls={panelId}
          aria-label={`${expanded ? t('fileManager.variants.hideVersions') : t('fileManager.variants.showVersions')}: ${row.name}`}
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
          onDoubleClick={(e) => e.stopPropagation()}
          className="flex-shrink-0 p-1 rounded text-bambu-gray hover:text-white"
        >
          {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>
        <div
          className={`flex-shrink-0 transition-opacity ${
            isFocused || selectedCount > 0 ? '' : 'can-hover:opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'
          }`}
        >
          <VariantGroupActionStrip row={row} actions={groupActions} tabIndex={isFocused ? 0 : -1} />
        </div>
      </div>
      {expanded && (
        <div
          id={panelId}
          role="group"
          aria-label={t('fileManager.variants.versionsHeading')}
          data-testid="variant-detail-pane"
          className="ml-8 mr-2 mb-1 border-l-2 border-bambu-green/40 pl-1"
        >
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
    </div>
  );
}
