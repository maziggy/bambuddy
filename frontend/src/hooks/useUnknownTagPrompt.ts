import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { Printer } from '../api/client';
import { api } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { useTranslation } from 'react-i18next';
import { getAmsLabel } from '../utils/amsHelpers';

export interface UnknownTagDetail {
  printer_id: number;
  ams_id: number;
  tray_id: number;
  tag_uid?: string;
  tray_uuid?: string;
  // Backend-provided so the modal doesn't need to look up stale cached
  // `printerStatus` data — the React Query cache often lags the WS event by
  // several seconds while the new MQTT push is being applied.
  tray_type?: string | null;
  tray_color?: string | null;
  tray_sub_brands?: string | null;
  tray_count?: number | null;
}

export interface UnknownSpoolPrompt {
  printer_id: number;
  ams_id: number;
  tray_id: number;
  printer_name: string;
  ams_label: string;
  slot_label: string;
  material: string | null;
  color_hex: string | null;
  brand: string | null;
}

function slotKey(printer_id: number, ams_id: number, tray_id: number): string {
  return `${printer_id}|${ams_id}|${tray_id}`;
}

export function useUnknownTagPrompt() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { user, authEnabled } = useAuth();
  const [queue, setQueue] = useState<UnknownSpoolPrompt[]>([]);

  const isAuthed = !authEnabled || !!user;

  const buildPrompt = useCallback(
    (detail: UnknownTagDetail): UnknownSpoolPrompt | null => {
      // The backend only broadcasts unknown_tag for slots with real tray data,
      // and includes the relevant fields in the payload — no need to fall
      // back to the (often stale) cached printerStatus query for these.
      if (!detail.tray_type) return null;
      const printers = queryClient.getQueryData<Printer[]>(['printers']);
      const printer = printers?.find(p => p.id === detail.printer_id);
      const trayCount = detail.tray_count ?? 4;
      return {
        printer_id: detail.printer_id,
        ams_id: detail.ams_id,
        tray_id: detail.tray_id,
        printer_name: printer?.name ?? `Printer ${detail.printer_id}`,
        ams_label: getAmsLabel(detail.ams_id, trayCount),
        slot_label: `${t('inventory.unknownSpoolSlot', 'Slot')} ${detail.tray_id + 1}`,
        material: detail.tray_type ?? null,
        color_hex: detail.tray_color ?? null,
        brand: detail.tray_sub_brands ?? null,
      };
    },
    [queryClient, t],
  );

  useEffect(() => {
    if (!isAuthed) return;
    const handler = (e: Event) => {
      const ce = e as CustomEvent<UnknownTagDetail>;
      const detail = ce.detail;
      if (!detail) return;
      const prompt = buildPrompt(detail);
      if (!prompt) return;
      setQueue(prev => {
        // Don't double-queue the same slot — the backend dedupes per
        // (slot, tag) so repeat events here mean the user is still
        // looking at the same modal.
        const key = slotKey(prompt.printer_id, prompt.ams_id, prompt.tray_id);
        if (prev.some(p => slotKey(p.printer_id, p.ams_id, p.tray_id) === key)) {
          return prev;
        }
        return [...prev, prompt];
      });
    };
    window.addEventListener('unknown-tag', handler);
    return () => window.removeEventListener('unknown-tag', handler);
  }, [isAuthed, buildPrompt]);

  const current = queue[0] ?? null;

  const addMutation = useMutation({
    mutationFn: async (prompt: UnknownSpoolPrompt) => {
      const settings = queryClient.getQueryData<{ spoolman_enabled?: boolean }>(['settings']);
      if (settings?.spoolman_enabled) {
        await api.createSpoolmanSpoolFromSlot({
          printer_id: prompt.printer_id,
          ams_id: prompt.ams_id,
          tray_id: prompt.tray_id,
        });
      } else {
        await api.createSpoolFromSlot({
          printer_id: prompt.printer_id,
          ams_id: prompt.ams_id,
          tray_id: prompt.tray_id,
        });
      }
    },
    onSuccess: () => {
      showToast(t('inventory.addToInventorySuccess'), 'success');
      queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-inventory-spools'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments'] });
      queryClient.invalidateQueries({ queryKey: ['linked-spools'] });
      setQueue(prev => prev.slice(1));
    },
    onError: (error: Error) => {
      showToast(error.message || t('inventory.addToInventoryFailed'), 'error');
    },
  });

  const confirm = useCallback(() => {
    if (!current || addMutation.isPending) return;
    addMutation.mutate(current);
  }, [current, addMutation]);

  const cancel = useCallback(() => {
    if (!current) return;
    setQueue(prev => prev.slice(1));
  }, [current]);

  return useMemo(
    () => ({
      prompt: current,
      isPending: addMutation.isPending,
      confirm,
      cancel,
    }),
    [current, addMutation.isPending, confirm, cancel],
  );
}
