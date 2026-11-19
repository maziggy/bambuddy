import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { CalendarClock, Loader2, Pencil, Plus, Trash2, X } from 'lucide-react';
import { api, type Printer, type PrinterReservation, type PrinterReservationInput } from '../api/client';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';

/** Manage per-printer time-based reservations (#2785).
 *
 *  A reservation keeps one or more printers free on chosen weekdays, all day
 *  or between two wall-clock times (an end before the start runs past
 *  midnight). Everyone sees every reservation on the timeline; editing is
 *  limited to the owner unless the user may update all queue items. */

export const RESERVATIONS_QUERY_KEY = ['printer-reservations'] as const;
export const RESERVATION_OCCURRENCES_QUERY_KEY = ['printer-reservation-occurrences'] as const;

const ALL_DAYS = 0b1111111;
const WEEKDAYS = 0b0011111;

interface ReservationsModalProps {
  open: boolean;
  onClose: () => void;
  printers: Printer[];
}

function minutesToTime(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function timeToMinutes(value: string): number {
  const [h, m] = value.split(':').map((part) => Number(part));
  if (!Number.isFinite(h) || !Number.isFinite(m)) return 0;
  return h * 60 + m;
}

const emptyDraft = (): PrinterReservationInput => ({
  label: '',
  printer_ids: [],
  days_mask: WEEKDAYS,
  all_day: false,
  start_minute: 15 * 60,
  end_minute: 5 * 60,
  valid_from: null,
  valid_until: null,
});

export function ReservationsModal({ open, onClose, printers }: ReservationsModalProps) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission, canModify } = useAuth();

  const [editing, setEditing] = useState<PrinterReservation | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [draft, setDraft] = useState<PrinterReservationInput>(emptyDraft);
  const [deleteTarget, setDeleteTarget] = useState<PrinterReservation | null>(null);

  const { data: reservations = [], isLoading } = useQuery({
    queryKey: RESERVATIONS_QUERY_KEY,
    queryFn: api.listPrinterReservations,
    enabled: open,
  });

  // Monday-first short weekday names in the UI language. 2024-01-01 is a Monday.
  const dayNames = useMemo(() => {
    const fmt = new Intl.DateTimeFormat(i18n.language, { weekday: 'short' });
    return Array.from({ length: 7 }, (_, d) => fmt.format(new Date(2024, 0, 1 + d)));
  }, [i18n.language]);

  const printerName = (id: number) => printers.find((p) => p.id === id)?.name ?? `#${id}`;

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: RESERVATIONS_QUERY_KEY });
    queryClient.invalidateQueries({ queryKey: RESERVATION_OCCURRENCES_QUERY_KEY });
  };

  const saveMutation = useMutation({
    mutationFn: (body: PrinterReservationInput) =>
      editing ? api.updatePrinterReservation(editing.id, body) : api.createPrinterReservation(body),
    onSuccess: () => {
      showToast(t('queue.reservations.saved'), 'success');
      invalidate();
      setEditorOpen(false);
      setEditing(null);
    },
    onError: (err: Error) => showToast(err.message || t('queue.reservations.saveFailed'), 'error'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deletePrinterReservation(id),
    onSuccess: () => {
      showToast(t('queue.reservations.deleted'), 'success');
      setDeleteTarget(null);
      invalidate();
    },
    onError: (err: Error) => showToast(err.message || t('queue.reservations.deleteFailed'), 'error'),
  });

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || saveMutation.isPending || deleteMutation.isPending || deleteTarget) return;
      if (editorOpen) setEditorOpen(false);
      else onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, editorOpen, deleteTarget, saveMutation.isPending, deleteMutation.isPending, onClose]);

  if (!open) return null;

  const canCreate = hasPermission('queue:create');

  const openCreate = () => {
    setEditing(null);
    setDraft(emptyDraft());
    setEditorOpen(true);
  };

  const openEdit = (row: PrinterReservation) => {
    setEditing(row);
    setDraft({
      label: row.label,
      printer_ids: [...row.printer_ids],
      days_mask: row.days_mask,
      all_day: row.all_day,
      start_minute: row.start_minute,
      end_minute: row.end_minute,
      valid_from: row.valid_from,
      valid_until: row.valid_until,
    });
    setEditorOpen(true);
  };

  const togglePrinter = (id: number) =>
    setDraft((d) => ({
      ...d,
      printer_ids: d.printer_ids.includes(id) ? d.printer_ids.filter((p) => p !== id) : [...d.printer_ids, id],
    }));

  const toggleDay = (bit: number) => setDraft((d) => ({ ...d, days_mask: d.days_mask ^ (1 << bit) }));

  const describeDays = (mask: number) => {
    if (mask === ALL_DAYS) return t('queue.reservations.everyDay');
    return dayNames.filter((_, d) => (mask >> d) & 1).join(', ');
  };

  const describeTime = (row: PrinterReservationInput) => {
    if (row.all_day) return t('queue.reservations.allDay');
    const range = `${minutesToTime(row.start_minute)}–${minutesToTime(row.end_minute)}`;
    return row.end_minute <= row.start_minute ? `${range} (${t('queue.reservations.overnight')})` : range;
  };

  const draftValid = draft.printer_ids.length > 0 && draft.days_mask !== 0;
  const inputClass =
    'w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green';
  const chipClass = (active: boolean) =>
    `px-2.5 py-1 rounded-md text-xs border transition-colors ${
      active
        ? 'bg-bambu-green/20 border-bambu-green text-white'
        : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
    }`;

  const editor = (
    <form
      className="space-y-4 p-6"
      onSubmit={(e) => {
        e.preventDefault();
        if (draftValid) saveMutation.mutate({ ...draft, label: draft.label.trim() });
      }}
    >
      <div>
        <label className="block text-sm font-medium text-bambu-gray mb-1" htmlFor="reservation-label">
          {t('queue.reservations.label')}
        </label>
        <input
          id="reservation-label"
          type="text"
          maxLength={100}
          className={inputClass}
          placeholder={t('queue.reservations.labelPlaceholder')}
          value={draft.label}
          onChange={(e) => setDraft((d) => ({ ...d, label: e.target.value }))}
          autoFocus
        />
      </div>

      <fieldset>
        <legend className="block text-sm font-medium text-bambu-gray mb-1">{t('queue.reservations.printers')}</legend>
        <div className="flex flex-wrap gap-1.5">
          {printers.map((p) => (
            <button
              key={p.id}
              type="button"
              aria-pressed={draft.printer_ids.includes(p.id)}
              className={chipClass(draft.printer_ids.includes(p.id))}
              onClick={() => togglePrinter(p.id)}
            >
              {p.name}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="block text-sm font-medium text-bambu-gray mb-1">{t('queue.reservations.days')}</legend>
        <div className="flex flex-wrap items-center gap-1.5">
          {dayNames.map((name, d) => (
            <button
              key={d}
              type="button"
              aria-pressed={Boolean((draft.days_mask >> d) & 1)}
              className={chipClass(Boolean((draft.days_mask >> d) & 1))}
              onClick={() => toggleDay(d)}
            >
              {name}
            </button>
          ))}
          <label className="flex items-center gap-1.5 text-xs text-bambu-gray ml-2">
            <input
              type="checkbox"
              checked={draft.days_mask === ALL_DAYS}
              onChange={(e) => setDraft((d) => ({ ...d, days_mask: e.target.checked ? ALL_DAYS : WEEKDAYS }))}
            />
            {t('queue.reservations.everyDay')}
          </label>
        </div>
      </fieldset>

      <div>
        <label className="flex items-center gap-1.5 text-sm text-bambu-gray mb-2">
          <input
            type="checkbox"
            checked={draft.all_day}
            onChange={(e) => setDraft((d) => ({ ...d, all_day: e.target.checked }))}
          />
          {t('queue.reservations.allDay')}
        </label>
        {!draft.all_day && (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-bambu-gray mb-1" htmlFor="reservation-start">
                {t('queue.reservations.start')}
              </label>
              <input
                id="reservation-start"
                type="time"
                className={inputClass}
                value={minutesToTime(draft.start_minute)}
                onChange={(e) => setDraft((d) => ({ ...d, start_minute: timeToMinutes(e.target.value) }))}
              />
            </div>
            <div>
              <label className="block text-xs text-bambu-gray mb-1" htmlFor="reservation-end">
                {t('queue.reservations.end')}
              </label>
              <input
                id="reservation-end"
                type="time"
                className={inputClass}
                value={minutesToTime(draft.end_minute)}
                onChange={(e) => setDraft((d) => ({ ...d, end_minute: timeToMinutes(e.target.value) }))}
              />
            </div>
            {draft.end_minute <= draft.start_minute && (
              <p className="col-span-2 text-xs text-bambu-gray">{t('queue.reservations.overnightHint')}</p>
            )}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-bambu-gray mb-1" htmlFor="reservation-from">
            {t('queue.reservations.validFrom')}
          </label>
          <input
            id="reservation-from"
            type="date"
            className={inputClass}
            value={draft.valid_from ?? ''}
            onChange={(e) => setDraft((d) => ({ ...d, valid_from: e.target.value || null }))}
          />
        </div>
        <div>
          <label className="block text-xs text-bambu-gray mb-1" htmlFor="reservation-until">
            {t('queue.reservations.validUntil')}
          </label>
          <input
            id="reservation-until"
            type="date"
            className={inputClass}
            value={draft.valid_until ?? ''}
            onChange={(e) => setDraft((d) => ({ ...d, valid_until: e.target.value || null }))}
          />
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={() => setEditorOpen(false)}>
          {t('common.cancel')}
        </Button>
        <Button type="submit" disabled={saveMutation.isPending || !draftValid}>
          {saveMutation.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
          {t('common.save')}
        </Button>
      </div>
    </form>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60"
        onClick={() => {
          if (!saveMutation.isPending && !deleteMutation.isPending) onClose();
        }}
      />
      <div
        className="relative w-full max-w-2xl mx-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-h-[90vh] flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-labelledby="reservations-modal-title"
      >
        <div className="flex items-center justify-between gap-4 px-6 py-4 border-b border-bambu-dark-tertiary">
          <div className="min-w-0">
            <h2 id="reservations-modal-title" className="text-lg font-semibold text-white flex items-center gap-2">
              <CalendarClock className="w-5 h-5 text-bambu-green" />
              {editorOpen
                ? t(editing ? 'queue.reservations.edit' : 'queue.reservations.add')
                : t('queue.reservations.title')}
            </h2>
            {!editorOpen && <p className="text-bambu-gray text-sm mt-0.5">{t('queue.reservations.subtitle')}</p>}
          </div>
          <div className="flex items-center gap-2">
            {!editorOpen && canCreate && (
              <Button onClick={openCreate}>
                <Plus className="w-4 h-4" />
                {t('queue.reservations.add')}
              </Button>
            )}
            <button
              type="button"
              className="p-1.5 text-bambu-gray hover:text-white rounded"
              onClick={onClose}
              aria-label={t('common.close')}
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="overflow-y-auto">
          {editorOpen ? (
            editor
          ) : isLoading ? (
            <div className="flex items-center justify-center py-16 text-bambu-gray">
              <Loader2 className="w-6 h-6 animate-spin mr-2" />
              {t('common.loading')}
            </div>
          ) : reservations.length === 0 ? (
            <div className="py-16 text-center text-bambu-gray">{t('queue.reservations.empty')}</div>
          ) : (
            <ul className="divide-y divide-bambu-dark-tertiary">
              {reservations.map((row) => (
                <li key={row.id} className="px-6 py-3 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-white text-sm font-medium truncate">
                      {row.label || t('queue.reservations.reserved')}
                    </div>
                    <div className="text-xs text-bambu-gray">
                      {row.printer_ids.map(printerName).join(', ')}
                    </div>
                    <div className="text-xs text-bambu-gray">
                      {describeDays(row.days_mask)} · {describeTime(row)}
                      {row.created_by_username ? ` · ${row.created_by_username}` : ''}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    {canModify('queue', 'update', row.created_by_id) && (
                      <button
                        type="button"
                        className="p-1.5 text-bambu-gray hover:text-white rounded"
                        onClick={() => openEdit(row)}
                        aria-label={t('queue.reservations.edit')}
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                    )}
                    {canModify('queue', 'delete', row.created_by_id) && (
                      <button
                        type="button"
                        className="p-1.5 text-bambu-gray hover:text-red-400 rounded"
                        onClick={() => setDeleteTarget(row)}
                        aria-label={t('common.delete')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {deleteTarget && (
        <ConfirmModal
          title={t('queue.reservations.deleteTitle')}
          message={t('queue.reservations.deleteMessage', {
            name: deleteTarget.label || t('queue.reservations.reserved'),
          })}
          confirmText={t('common.delete')}
          variant="danger"
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
