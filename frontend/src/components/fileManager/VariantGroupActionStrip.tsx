import { useTranslation } from 'react-i18next';
import { Printer, Trash2, Unlink } from 'lucide-react';
import type { GroupRow } from '../../pages/fileManager/variantGroups';
import type { VariantGroupActions } from './useVariantGroupActions';

// Group-level icons of the list and columns views: print all versions as one
// job, ungroup, delete every version (#3628). Same look as a file's strip.
export function VariantGroupActionStrip({
  row,
  actions,
  tabIndex,
}: {
  row: GroupRow;
  actions: VariantGroupActions;
  tabIndex?: number;
}) {
  const { t } = useTranslation();
  const canPrint = actions.canPrint(row);
  const canUngroup = actions.canUngroup(row);
  const canDelete = actions.canDeleteFiles(row);
  const base = 'p-1.5 rounded transition-colors';
  const off = 'text-bambu-gray/50 cursor-not-allowed';
  return (
    <div
      className="flex items-center gap-1"
      data-file-actions
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      {actions.hasPrintable(row) && (
        <button
          type="button"
          tabIndex={tabIndex}
          onClick={() => actions.print(row)}
          disabled={!canPrint}
          className={`${base} ${canPrint ? 'hover:bg-bambu-dark text-bambu-gray hover:text-bambu-green' : off}`}
          title={canPrint ? t('fileManager.variants.print') : t('fileManager.noPermissionAddToQueue')}
          aria-label={t('fileManager.variants.print')}
        >
          <Printer className="w-4 h-4" />
        </button>
      )}
      <button
        type="button"
        tabIndex={tabIndex}
        onClick={() => actions.ungroup(row)}
        disabled={!canUngroup}
        className={`${base} ${canUngroup ? 'hover:bg-bambu-dark text-bambu-gray hover:text-white' : off}`}
        title={canUngroup ? t('fileManager.variants.ungroup') : actions.ungroupBlockedReason(row)}
        aria-label={t('fileManager.variants.ungroup')}
      >
        <Unlink className="w-4 h-4" />
      </button>
      <button
        type="button"
        tabIndex={tabIndex}
        onClick={() => actions.deleteFiles(row)}
        disabled={!canDelete}
        className={`${base} ${canDelete ? 'hover:bg-bambu-dark text-bambu-gray hover:text-red-700 dark:hover:text-red-400' : off}`}
        title={canDelete ? t('fileManager.variants.deleteGroup') : t('fileManager.variants.noPermissionDeleteGroup')}
        aria-label={t('fileManager.variants.deleteGroup')}
      >
        <Trash2 className="w-4 h-4" />
      </button>
    </div>
  );
}
