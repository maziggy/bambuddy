import { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { Printer } from '../api/client';
import { api, ApiError } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { useTranslation } from 'react-i18next';
import { getAmsLabel } from '../utils/amsHelpers';
import { hasRfidTag, rfidLinkBody, tagLinkError } from '../utils/rfidTagLink';
import { useUiSettings } from './useUiSettings';

export interface UnknownTagDetail {
  printer_id: number;
  ams_id: number;
  tray_id: number;
  tag_uid?: string;
  tray_uuid?: string;
  tray_info_idx?: string | null;
  // Backend-provided so the modal doesn't need to look up stale cached
  // `printerStatus` data — the React Query cache often lags the WS event by
  // several seconds while the new MQTT push is being applied.
  tray_type?: string | null;
  tray_color?: string | null;
  tray_sub_brands?: string | null;
  tray_count?: number | null;
  // Untagged spools that match the tag but differ, so the matcher left the
  // choice to the user (#3658). Oldest first.
  candidate_spool_ids?: number[];
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
  tag_uid: string | null;
  tray_uuid: string | null;
  tray_info_idx?: string | null;
  candidate_spool_ids: number[];
}

function slotKey(printer_id: number, ams_id: number, tray_id: number): string {
  return `${printer_id}|${ams_id}|${tray_id}`;
}

export function useUnknownTagPrompt() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { user, authEnabled, hasPermission } = useAuth();
  const [queue, setQueue] = useState<UnknownSpoolPrompt[]>([]);

  const isAuthed = !authEnabled || !!user;
  const { data: uiSettings } = useUiSettings({ enabled: isAuthed });

  const buildPrompt = useCallback(
    (detail: UnknownTagDetail): UnknownSpoolPrompt | null => {
      // The backend only broadcasts unknown_tag for slots with real tray data,
      // and includes the relevant fields in the payload — no need to fall
      // back to the (often stale) cached printerStatus query for these.
      if (!detail.tray_type) return null;
      const printers = queryClient.getQueryData<Printer[]>(['printers']);
      const printer = printers?.find(p => p.id === detail.printer_id);
      return {
        printer_id: detail.printer_id,
        ams_id: detail.ams_id,
        tray_id: detail.tray_id,
        printer_name: printer?.name ?? `Printer ${detail.printer_id}`,
        ams_label: getAmsLabel(detail.ams_id),
        slot_label: `${t('inventory.unknownSpoolSlot', 'Slot')} ${detail.tray_id + 1}`,
        material: detail.tray_type ?? null,
        color_hex: detail.tray_color ?? null,
        brand: detail.tray_sub_brands ?? null,
        tag_uid: detail.tag_uid ?? null,
        tray_uuid: detail.tray_uuid ?? null,
        tray_info_idx: detail.tray_info_idx,
        candidate_spool_ids: detail.candidate_spool_ids ?? [],
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
        const key = slotKey(prompt.printer_id, prompt.ams_id, prompt.tray_id);
        const index = prev.findIndex(p => slotKey(p.printer_id, p.ams_id, p.tray_id) === key);
        if (index >= 0) {
          if (uiSettings?.spoolman_enabled) return prev;
          const old = prev[index];
          if (old.tag_uid === prompt.tag_uid && old.tray_uuid === prompt.tray_uuid &&
              old.candidate_spool_ids.join() === prompt.candidate_spool_ids.join()) return prev;
          return prev.map((item, i) => i === index ? prompt : item);
        }
        return [...prev, prompt];
      });
    };
    window.addEventListener('unknown-tag', handler);
    return () => window.removeEventListener('unknown-tag', handler);
  }, [isAuthed, buildPrompt, uiSettings?.spoolman_enabled]);

  const current = queue[0] ?? null;

  const dismiss = useCallback((prompt: UnknownSpoolPrompt) => {
    setQueue(prev => prev.filter(item => item !== prompt));
  }, []);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
    queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
  };

  const addMutation = useMutation({
    mutationFn: async (prompt: UnknownSpoolPrompt) => {
      if (uiSettings?.spoolman_enabled) {
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
          tag_uid: prompt.tag_uid ?? undefined,
          tray_uuid: prompt.tray_uuid ?? undefined,
        });
      }
    },
    onSuccess: (_, prompt) => {
      showToast(t('inventory.addToInventorySuccess'), 'success');
      refresh();
      queryClient.invalidateQueries({ queryKey: ['spoolman-inventory-spools'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments'] });
      queryClient.invalidateQueries({ queryKey: ['linked-spools'] });
      dismiss(prompt);
    },
    onError: (error: Error, prompt) => {
      if (uiSettings?.spoolman_enabled) {
        showToast(error.message || t('inventory.addToInventoryFailed'), 'error');
        return;
      }
      showToast(tagLinkError(error, t, error.message || t('inventory.addToInventoryFailed')), 'error');
      if (error instanceof ApiError && error.status === 409) {
        refresh();
        dismiss(prompt);
      }
    },
  });

  // Built-in inventory only: link the tag to a spool the user picked and
  // assign the slot, in one call (#3658). Spoolman mode has its own link
  // (#3632).
  const linkMutation = useMutation({
    mutationFn: async ({ prompt, spoolId }: { prompt: UnknownSpoolPrompt; spoolId: number }) =>
      api.linkTagToSpool(spoolId, {
        ...rfidLinkBody(prompt),
        printer_id: prompt.printer_id,
        ams_id: prompt.ams_id,
        tray_id: prompt.tray_id,
      }),
    onSuccess: (_spool, { spoolId, prompt }) => {
      showToast(t('inventory.rfidLink.linked', { id: spoolId }), 'success');
      refresh();
      dismiss(prompt);
    },
    onError: (error: Error, { prompt }) => {
      showToast(tagLinkError(error, t, t('inventory.rfidLink.linkFailed')), 'error');
      if (error instanceof ApiError && error.status === 409) {
        refresh();
        dismiss(prompt);
      }
    },
  });

  const isPending = addMutation.isPending || linkMutation.isPending;
  const canAdd = typeof uiSettings?.spoolman_enabled === 'boolean' &&
    (uiSettings.spoolman_enabled || (hasPermission('inventory:update') && hasRfidTag(current)));
  const canLink = uiSettings?.spoolman_enabled === false && canAdd && hasPermission('inventory:read');

  const confirm = useCallback(() => {
    if (!current || isPending || !canAdd) return;
    addMutation.mutate(current);
  }, [current, isPending, canAdd, addMutation]);

  const link = useCallback(
    (spoolId: number) => {
      if (!current || isPending || !canLink) return;
      linkMutation.mutate({ prompt: current, spoolId });
    },
    [current, isPending, canLink, linkMutation],
  );

  const cancel = useCallback(() => {
    if (!current) return;
    dismiss(current);
  }, [current, dismiss]);

  return useMemo(
    () => ({
      prompt: current,
      isPending,
      canLink,
      canAdd,
      adding: addMutation.isPending,
      confirm,
      link,
      cancel,
    }),
    [current, isPending, canLink, canAdd, addMutation.isPending, confirm, link, cancel],
  );
}
