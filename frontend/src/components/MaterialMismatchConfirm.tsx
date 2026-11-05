import { useTranslation } from 'react-i18next';
import { ConfirmModal } from './ConfirmModal';
import { materialMismatchMessage, type MaterialMismatch } from '../utils/assignMismatch';

/**
 * The question asked before a spool goes into a slot of another material, as
 * the Assign Spool dialog and the QR assignment ask it.
 */
export function MaterialMismatchConfirm({
  mismatch,
  location,
  isLoading,
  onConfirm,
  onCancel,
}: {
  mismatch: MaterialMismatch;
  /** The slot, as the question names it. */
  location: string;
  isLoading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <ConfirmModal
      title={t('inventory.assignMismatchTitle')}
      message={materialMismatchMessage(t, mismatch, location)}
      confirmText={t('inventory.assignMismatchConfirm')}
      variant="warning"
      // Above the assign dialogs' own z-[100] layer (#1336) — without this the
      // question is hidden behind the dialog that asks it.
      overlayZIndex="z-[110]"
      isLoading={isLoading}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
