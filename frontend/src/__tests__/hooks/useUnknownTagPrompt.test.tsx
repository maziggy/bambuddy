import { describe, it, expect, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { api } from '../../api/client';
import { useUnknownTagPrompt } from '../../hooks/useUnknownTagPrompt';

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  // A signed-in user who may not read /settings.
  return { ...actual, useAuth: () => ({ user: { id: 3 }, authEnabled: true, hasPermission: (permission: string) => permission.startsWith('inventory:') }) };
});
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: vi.fn() }) };
});

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('useUnknownTagPrompt', () => {
  it('adds the spool to Spoolman in Spoolman mode, for a user who may not read the settings', async () => {
    let served = false;
    server.use(
      http.get('/api/v1/settings/ui-flags', () => {
        served = true;
        return HttpResponse.json({ spoolman_enabled: true });
      }),
    );
    const toSpoolman = vi.spyOn(api, 'createSpoolmanSpoolFromSlot').mockResolvedValue({} as never);
    const toBuiltIn = vi.spyOn(api, 'createSpoolFromSlot').mockResolvedValue({} as never);
    const { result } = renderHook(() => useUnknownTagPrompt(), { wrapper });

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('unknown-tag', { detail: { printer_id: 1, ams_id: 0, tray_id: 2, tray_type: 'PETG' } }),
      );
    });
    await waitFor(() => expect(result.current.prompt).not.toBeNull());
    await waitFor(() => expect(served).toBe(true));
    await act(async () => {});
    await act(async () => result.current.confirm());

    await waitFor(() => expect(toSpoolman).toHaveBeenCalledWith({ printer_id: 1, ams_id: 0, tray_id: 2 }));
    expect(toBuiltIn).not.toHaveBeenCalled();
  });

  it('links the tag to a picked spool and assigns the slot in built-in mode (#3658)', async () => {
    server.use(http.get('/api/v1/settings/ui-flags', () => HttpResponse.json({ spoolman_enabled: false })));
    const link = vi.spyOn(api, 'linkTagToSpool').mockResolvedValue({} as never);
    const { result } = renderHook(() => useUnknownTagPrompt(), { wrapper });

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('unknown-tag', {
          detail: {
            printer_id: 1,
            ams_id: 0,
            tray_id: 2,
            tray_type: 'PETG',
            tag_uid: 'AABBCCDD11223344',
            tray_uuid: 'AABBCCDDEEFF0011AABBCCDDEEFF0011',
            candidate_spool_ids: [61, 73],
          },
        }),
      );
    });
    await waitFor(() => expect(result.current.prompt?.candidate_spool_ids).toEqual([61, 73]));
    await waitFor(() => expect(result.current.canLink).toBe(true));
    await act(async () => result.current.link(73));

    await waitFor(() =>
      expect(link).toHaveBeenCalledWith(73, {
        tag_uid: 'AABBCCDD11223344',
        tray_uuid: 'AABBCCDDEEFF0011AABBCCDDEEFF0011',
        tag_type: 'bambulab',
        data_origin: 'rfid_linked',
        printer_id: 1,
        ams_id: 0,
        tray_id: 2,
      }),
    );
    await waitFor(() => expect(result.current.prompt).toBeNull());
  });

  it('keeps the replacement tag when an earlier Add finishes', async () => {
    let releaseMode!: () => void;
    const mode = new Promise<void>(resolve => { releaseMode = resolve; });
    server.use(http.get('/api/v1/settings/ui-flags', async () => { await mode; return HttpResponse.json({ spoolman_enabled: false }); }));
    let finish!: () => void;
    const add = vi.spyOn(api, 'createSpoolFromSlot').mockImplementation(() =>
      new Promise(resolve => { finish = () => resolve({} as never); }));
    const { result } = renderHook(() => useUnknownTagPrompt(), { wrapper });
    const report = (uid: string) => window.dispatchEvent(new CustomEvent('unknown-tag', {
      detail: { printer_id: 1, ams_id: 0, tray_id: 3, tray_type: 'PETG', tag_uid: uid },
    }));
    await act(async () => { report('04556677'); });
    await act(async () => result.current.confirm());
    expect(add).not.toHaveBeenCalled();
    releaseMode();
    await waitFor(() => expect(result.current.canAdd).toBe(true));
    await act(async () => result.current.confirm());
    await waitFor(() => expect(add).toHaveBeenCalledWith({ printer_id: 1, ams_id: 0, tray_id: 3, tag_uid: '04556677', tray_uuid: undefined }));
    await act(async () => { report('048899AA'); finish(); });
    await waitFor(() => expect(result.current.isPending).toBe(false));
    expect(result.current.prompt?.tag_uid).toBe('048899AA');
  });
});
