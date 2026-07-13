/**
 * Tests for the in-app sponsor-toast hook (#2477 regression guard).
 *
 * The bug: the 14-day cooldown is backend-owned, but the anchor is only
 * persisted by POST /sponsor-prompt/dismiss — and the hook used to call
 * dismiss ONLY from the "View supporters" CTA's onClick. A user who saw the
 * toast but never clicked it persisted no state, so the toast re-fired on
 * every fresh browser session. The fix records-on-show: the hook POSTs
 * /dismiss the moment it renders the toast. These tests pin that contract so
 * it can't silently regress back to click-only anchoring.
 */

import type { ReactNode } from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { renderHook, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { useSponsorPrompt } from '../../hooks/useSponsorPrompt';

// The hook only needs `loading` from auth and `showPersistentToast` from the
// toast context — mock both so the test doesn't drag in the real providers
// (auth bootstrap, toast portal). The real sponsorPromptApi still runs and
// hits MSW, which is exactly what we want to assert on.
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ loading: false }),
}));

const showPersistentToast = vi.fn();
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ showPersistentToast }),
}));

// The hook reads the printers list (fleet size decides which ask it makes), so
// it now needs a QueryClient. Fresh per test so one test's fleet can't leak
// into the next through the cache.
function wrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

/** Make GET /printers/ return exactly `count` printers. */
function withFleet(count: number) {
  server.use(
    http.get('/api/v1/printers/', () =>
      HttpResponse.json(
        Array.from({ length: count }, (_, i) => ({
          id: i + 1,
          name: `Printer ${i + 1}`,
          serial_number: `SN${i + 1}`,
          ip_address: '192.168.1.10',
          model: 'X1C',
          is_active: true,
        })),
      ),
    ),
  );
}

beforeEach(() => {
  showPersistentToast.mockClear();
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe('useSponsorPrompt', () => {
  it('records the toast as shown (POSTs /dismiss) as soon as it renders, without a CTA click', async () => {
    const dismissed: string[] = [];
    server.use(
      http.get('/api/v1/sponsor-prompt/check', () =>
        HttpResponse.json({
          show: true,
          milestone: 'prints-500',
          family: 'prints',
          threshold: 500,
          payload: { count: 512 },
        }),
      ),
      http.post('/api/v1/sponsor-prompt/dismiss', async ({ request }) => {
        const body = (await request.json()) as { milestone: string };
        dismissed.push(body.milestone);
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderHook(() => useSponsorPrompt('EUR'), { wrapper: wrapper() });

    // The toast is shown...
    await waitFor(() => expect(showPersistentToast).toHaveBeenCalledTimes(1));
    // ...and the cooldown is anchored on show, not on any CTA interaction.
    await waitFor(() => expect(dismissed).toEqual(['prints-500']));

    // The CTA is present for navigation but carries no onClick side effect —
    // anchoring no longer depends on the user clicking through.
    const options = showPersistentToast.mock.calls[0][3];
    expect(options.action.href).toContain('from=app-toast-prints-500');
    expect(options.action.onClick).toBeUndefined();
  });

  it('does not show a toast or anchor the cooldown when the check returns show:false', async () => {
    let dismissCalls = 0;
    server.use(
      http.get('/api/v1/sponsor-prompt/check', () => HttpResponse.json({ show: false })),
      http.post('/api/v1/sponsor-prompt/dismiss', () => {
        dismissCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderHook(() => useSponsorPrompt('EUR'), { wrapper: wrapper() });

    // Give the async effect a chance to run before asserting the negatives.
    await waitFor(() => expect(sessionStorage.getItem('sponsorPromptShown')).toBe('1'));
    expect(showPersistentToast).not.toHaveBeenCalled();
    expect(dismissCalls).toBe(0);
  });

  it('does not re-check within the same browser session (sessionStorage guard)', async () => {
    let checkCalls = 0;
    server.use(
      http.get('/api/v1/sponsor-prompt/check', () => {
        checkCalls += 1;
        return HttpResponse.json({ show: false });
      }),
      http.post('/api/v1/sponsor-prompt/dismiss', () => new HttpResponse(null, { status: 204 })),
    );

    const first = renderHook(() => useSponsorPrompt('EUR'), { wrapper: wrapper() });
    await waitFor(() => expect(checkCalls).toBe(1));
    first.unmount();

    // A second mount in the same session (e.g. a route change that remounts
    // Layout) must not re-run the check — that per-tab guard is what keeps the
    // toast from flashing repeatedly while a session is open.
    renderHook(() => useSponsorPrompt('EUR'), { wrapper: wrapper() });
    await new Promise((r) => setTimeout(r, 20));
    expect(checkCalls).toBe(1);
  });
});

/**
 * Fleet-size audience split.
 *
 * A print farm has no use for a "chip in $5" toast — it wants a support
 * contract. At/above BUSINESS_FLEET_THRESHOLD configured printers the toast
 * makes the commercial ask instead, and points at business.html rather than
 * sponsors.html. Same milestone, same cooldown, same single interruption; only
 * the ask changes.
 */
describe('useSponsorPrompt — fleet-size audience', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/sponsor-prompt/check', () =>
        HttpResponse.json({
          show: true,
          milestone: 'prints-25',
          family: 'prints',
          threshold: 25,
          payload: { count: 30 },
        }),
      ),
      http.post('/api/v1/sponsor-prompt/dismiss', () => new HttpResponse(null, { status: 204 })),
    );
  });

  it('makes the personal ask below the threshold', async () => {
    withFleet(4);
    renderHook(() => useSponsorPrompt('EUR'), { wrapper: wrapper() });

    await waitFor(() => expect(showPersistentToast).toHaveBeenCalledTimes(1));
    const [, message, , options] = showPersistentToast.mock.calls[0];
    expect(options.action.href).toContain('sponsors.html');
    expect(options.action.href).not.toContain('business.html');
    expect(message).toContain('30'); // the prints milestone copy, not the fleet copy
  });

  it('makes the commercial ask at the threshold, pointing at business.html', async () => {
    withFleet(5);
    renderHook(() => useSponsorPrompt('EUR'), { wrapper: wrapper() });

    await waitFor(() => expect(showPersistentToast).toHaveBeenCalledTimes(1));
    const [, message, , options] = showPersistentToast.mock.calls[0];
    expect(options.action.href).toContain('business.html');
    // Attribution still rides on the milestone, so Matomo keeps segmenting it.
    expect(options.action.href).toContain('from=app-toast-prints-25');
    expect(message).toContain('5'); // fleet size, not the print count
    expect(message).toMatch(/support plan/i);
  });

  it('counts configured printers, not active ones — maintenance mode must not downgrade the ask', async () => {
    // Eight printers, five of them in maintenance (is_active: false). This is
    // still an eight-printer business; if the split filtered on is_active it
    // would see three and pitch them as a hobbyist mid-outage.
    server.use(
      http.get('/api/v1/printers/', () =>
        HttpResponse.json(
          Array.from({ length: 8 }, (_, i) => ({
            id: i + 1,
            name: `Printer ${i + 1}`,
            serial_number: `SN${i + 1}`,
            ip_address: '192.168.1.10',
            model: 'X1C',
            is_active: i < 3,
          })),
        ),
      ),
    );

    renderHook(() => useSponsorPrompt('EUR'), { wrapper: wrapper() });

    await waitFor(() => expect(showPersistentToast).toHaveBeenCalledTimes(1));
    const [, message, , options] = showPersistentToast.mock.calls[0];
    expect(options.action.href).toContain('business.html');
    expect(message).toContain('8');
  });

  it('waits for the fleet to load before deciding — a farm is never pitched as a hobbyist', async () => {
    // Slow printers response: if the hook fired the toast before the fleet
    // resolved, it would default to 0 printers and make the personal ask.
    server.use(
      http.get('/api/v1/printers/', async () => {
        await new Promise((r) => setTimeout(r, 50));
        return HttpResponse.json(
          Array.from({ length: 6 }, (_, i) => ({
            id: i + 1,
            name: `Printer ${i + 1}`,
            serial_number: `SN${i + 1}`,
            ip_address: '192.168.1.10',
            model: 'X1C',
            is_active: true,
          })),
        );
      }),
    );

    renderHook(() => useSponsorPrompt('EUR'), { wrapper: wrapper() });

    await waitFor(() => expect(showPersistentToast).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(showPersistentToast.mock.calls[0][3].action.href).toContain('business.html');
  });
});
