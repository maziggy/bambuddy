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
  return { ...actual, useAuth: () => ({ user: { id: 3 }, authEnabled: true, hasPermission: () => false }) };
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
        new CustomEvent('unknown-tag', { detail: { printer_id: 1, ams_id: 0, tray_id: 2, tray_type: 'PLA' } }),
      );
    });
    await waitFor(() => expect(result.current.prompt).not.toBeNull());
    await waitFor(() => expect(served).toBe(true));
    await act(async () => {});
    await act(async () => result.current.confirm());

    await waitFor(() => expect(toSpoolman).toHaveBeenCalledWith({ printer_id: 1, ams_id: 0, tray_id: 2 }));
    expect(toBuiltIn).not.toHaveBeenCalled();
  });
});
