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

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { renderHook, waitFor, cleanup } from '@testing-library/react';
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

    renderHook(() => useSponsorPrompt('EUR'));

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

    renderHook(() => useSponsorPrompt('EUR'));

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

    const first = renderHook(() => useSponsorPrompt('EUR'));
    await waitFor(() => expect(checkCalls).toBe(1));
    first.unmount();

    // A second mount in the same session (e.g. a route change that remounts
    // Layout) must not re-run the check — that per-tab guard is what keeps the
    // toast from flashing repeatedly while a session is open.
    renderHook(() => useSponsorPrompt('EUR'));
    await new Promise((r) => setTimeout(r, 20));
    expect(checkCalls).toBe(1);
  });
});
