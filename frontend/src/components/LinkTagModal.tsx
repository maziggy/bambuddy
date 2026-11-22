import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2, Tag, X } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { InventorySpool, SpoolAssignment } from '../api/client';
import { Button } from './Button';
import { SpoolLinkPicker } from './SpoolLinkPicker';
import { useToast } from '../contexts/ToastContext';
import { formatAssignmentSlotLabel } from '../utils/amsHelpers';
import { useRfidDialog } from '../hooks/useRfidDialog';
import { linkChoices, movedTagBody, rfidLinkBody, tagLinkError, type RfidTag } from '../utils/rfidTagLink';

export interface LinkTagTarget {
  /** The tag the AMS read in the slot. Omitted when moving a spool's own tag from the spool dialog. */
  tag?: RfidTag | null;
  /** The spool that carries the tag now. Set to move the tag away from it. */
  holder?: InventorySpool | null;
  /** The slot the tag is in; the chosen spool is assigned there. */
  slot?: { printerId: number; amsId: number; trayId: number } | null;
  /** "X1C • AMS A • Slot 1", for the message. */
  location?: string | null;
  /** Assignment snapshot when the editor opened the chooser. */
  assignments?: SpoolAssignment[];
  material?: string | null;
  colorHex?: string | null;
}

interface LinkTagModalProps {
  target: LinkTagTarget | null;
  onClose: () => void;
  /** Called after the tag was linked, before onClose. */
  onLinked?: () => void;
}

/**
 * Link an RFID tag to a spool the user picks, or move it there from the spool
 * that has it (#3658). One call does it: link-tag links the tag, takes it
 * from the holder (``move``), and assigns the slot the way an RFID match
 * does.
 */
export function LinkTagModal({ target, onClose, onLinked }: LinkTagModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [assignmentId, setAssignmentId] = useState<number | null>(null);
  const assignments = target?.assignments;
  const assignment = assignments?.length === 1 ? assignments[0] : assignments?.find(row => row.id === assignmentId);
  const slotChosen = !assignments?.length || !!assignment;
  const isOpen = target !== null;
  const holder = target?.holder ?? null;

  // A new pick each time it opens. Keyed on opening rather than on the target,
  // which a caller may rebuild on every render.
  useEffect(() => {
    if (!isOpen) {
      setSelectedId(null);
      setAssignmentId(null);
    }
  }, [isOpen]);

  const { data: spools, isLoading, isError, refetch } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(true),
    enabled: isOpen,
  });

  const material = target?.material ?? holder?.material ?? null;
  const choices = useMemo(
    () =>
      linkChoices(spools, {
        material,
        colorHex: target?.colorHex ?? holder?.rgba ?? null,
        excludeId: holder?.id ?? null,
      }),
    [spools, material, target?.colorHex, holder],
  );

  const linkMutation = useMutation({
    mutationFn: (spoolId: number) => {
      const body = target?.tag ? rfidLinkBody(target.tag) : movedTagBody(holder!);
      const slot = assignment ? { printerId: assignment.printer_id, amsId: assignment.ams_id, trayId: assignment.tray_id } : target?.slot;
      return api.linkTagToSpool(spoolId, {
        ...body,
        move: holder ? true : undefined,
        source_spool_id: holder?.id,
        ...(slot ? { printer_id: slot.printerId, ams_id: slot.amsId, tray_id: slot.trayId } : {}),
      });
    },
    onSuccess: (_spool, spoolId) => {
      queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
      showToast(
        holder
          ? t('inventory.rfidLink.moved', { from: holder.id, to: spoolId })
          : t('inventory.rfidLink.linked', { id: spoolId }),
        'success',
      );
      onLinked?.();
      onClose();
    },
    onError: (error: Error) => {
      showToast(tagLinkError(error, t, holder ? t('inventory.rfidLink.moveFailed') : t('inventory.rfidLink.linkFailed')), 'error');
      if (error instanceof ApiError && error.status === 409) {
        queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
        queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
        onClose();
      }
    },
  });

  const dialog = useRfidDialog(isOpen, linkMutation.isPending, onClose);
  const chosen = !isError && choices.some(({ spool }) => spool.id === selectedId) ? selectedId : null;

  if (!target) return null;

  const pending = linkMutation.isPending;
  const title = holder ? t('inventory.rfidLink.moveTitle') : t('inventory.rfidLink.linkTitle');
  const message = holder
    ? t('inventory.rfidLink.moveMessage', { id: holder.id })
    : target.location
      ? t('inventory.rfidLink.linkMessageAt', { location: target.location })
      : t('inventory.rfidLink.linkMessage');
  const confirmText = chosen
    ? holder
      ? t('inventory.rfidLink.moveTo', { id: chosen })
      : t('inventory.rfidLink.linkTo', { id: chosen })
    : holder
      ? t('inventory.rfidLink.moveTag')
      : t('inventory.rfidLink.link');

  return (
    <div className="fixed inset-0 z-[100] flex items-start sm:items-center justify-center p-4 overflow-y-auto">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={pending ? undefined : onClose} />
      <div
        role="dialog"
        tabIndex={-1}
        ref={dialog}
        aria-modal="true"
        aria-labelledby="link-tag-title"
        className="relative w-full max-w-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl my-auto"
      >
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <Tag className="w-5 h-5 text-bambu-green" />
            <h2 id="link-tag-title" className="text-lg font-semibold text-white">{title}</h2>
          </div>
          <button
            onClick={onClose}
            disabled={pending}
            className="p-1 text-bambu-gray hover:text-white rounded transition-colors"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-4 space-y-3">
          <p className="text-sm text-bambu-gray whitespace-pre-line">{message}</p>
          {holder && (
            <p className="text-xs text-amber-400 whitespace-pre-line">{t('inventory.rfidLink.usageStays', { id: holder.id })}</p>
          )}
          {assignments && assignments.length > 1 && (
            <label className="block text-sm text-white">
              {t('inventory.qrAssign.selectSlot')}
              <select
                className="mt-1 w-full rounded border border-bambu-dark-tertiary bg-bambu-dark p-2"
                value={assignmentId ?? ''}
                disabled={pending}
                onChange={event => setAssignmentId(Number(event.target.value))}
              >
                <option value="" disabled>{t('inventory.qrAssign.selectSlot')}</option>
                {assignments.map(row => (
                  <option key={row.id} value={row.id}>
                    {row.printer_name || t('inventory.qrAssign.printer') + ' #' + row.printer_id} · {formatAssignmentSlotLabel(row)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <SpoolLinkPicker
            choices={choices}
            selectedId={selectedId}
            onSelect={setSelectedId}
            material={material}
            isLoading={isLoading}
            onRetry={isError ? () => { void refetch(); } : undefined}
            disabled={pending}
          />
        </div>
        <div className="flex gap-2 p-4 border-t border-bambu-dark-tertiary">
          <Button variant="secondary" onClick={onClose} disabled={pending} className="flex-1">
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => chosen && slotChosen && linkMutation.mutate(chosen)}
            disabled={!chosen || !slotChosen || pending}
            className="flex-1"
          >
            {pending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Tag className="w-4 h-4" />}
            {confirmText}
          </Button>
        </div>
      </div>
    </div>
  );
}
