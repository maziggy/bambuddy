/**
 * Where the bug-report trigger lives, and what shape its panel takes (#2750,
 * reporter @goodjaltman).
 *
 * The floating disc is pinned to the bottom-right corner, which is the most
 * contended region in the app — the Profiles scroll-to-top FAB, the floating
 * camera window, the Group Edit save bar, the bulk-selection toolbars and the
 * per-card action buttons on File Manager and Archives all sit there, and a
 * `fixed` disc covers whichever happens to be underneath at the time. Below the
 * sidebar-compact breakpoint the trigger moves into the compact header instead,
 * which frees the corner outright.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { Layout } from '../../components/Layout';
import { BugReportBubble } from '../../components/BugReportBubble';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

/** The always-false stub from setup.ts, restored after each test. */
const defaultMatchMedia = (query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: () => {},
  removeListener: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => true,
});

/**
 * Pretend the viewport is ``width`` px wide.
 *
 * Both breakpoint hooks read `window.innerWidth` for their initial value and
 * `matchMedia('(max-width: Npx)')` thereafter, so the stub has to answer the
 * query rather than return a fixed boolean — useIsMobile (768) and
 * useIsSidebarCompact (1144) ask different questions and a flat `true` would
 * conflate them.
 */
function stubViewport(width: number) {
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: width });
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => {
      const max = /max-width:\s*(\d+)px/.exec(query);
      return { ...defaultMatchMedia(query), matches: max ? width <= Number(max[1]) : false };
    },
  });
}

function setupLayoutHandlers() {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json({ connected: true, state: 'IDLE' })),
    http.get('/api/v1/version', () => HttpResponse.json({ version: '0.1.6', build: 'test' })),
    http.get('/api/v1/settings/', () =>
      HttpResponse.json({ check_updates: false, check_printer_firmware: false, auto_archive: true }),
    ),
    http.get('/api/v1/external-links/', () => HttpResponse.json([])),
    http.get('/api/v1/smart-plugs/', () => HttpResponse.json([])),
    http.get('/api/v1/support/debug-logging', () => HttpResponse.json({ enabled: false })),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/pending-uploads/count', () => HttpResponse.json({ count: 0 })),
    http.get('/api/v1/updates/check', () => HttpResponse.json({ update_available: false })),
    http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
    http.get('/api/v1/printers/developer-mode-warnings', () => HttpResponse.json([])),
    http.get('/api/v1/system/health', () => HttpResponse.json({ findings: [] })),
  );
}

/** The floating disc, identified by the shape only it has. */
const floatingDisc = () =>
  Array.from(document.querySelectorAll('button')).find(
    (b) => b.className.includes('rounded-full') && b.className.includes('bottom-4'),
  );

describe('bug-report trigger placement', () => {
  beforeEach(() => {
    vi.mocked(localStorage.getItem).mockReturnValue(null);
    setupLayoutHandlers();
  });

  afterEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: defaultMatchMedia,
    });
  });

  it('puts the trigger in the compact header and frees the corner below 1144px', async () => {
    stubViewport(390);
    render(<Layout />);

    await waitFor(() => expect(document.querySelector('header')).toBeInTheDocument());
    const header = document.querySelector('header')!;
    expect(within(header).getByRole('button', { name: /report a bug|bug/i })).toBeInTheDocument();
    expect(floatingDisc()).toBeUndefined();
  });

  it('keeps the floating disc when the sidebar is not compact', async () => {
    stubViewport(1440);
    render(<Layout />);

    await waitFor(() => expect(floatingDisc()).toBeDefined());
    // No compact header exists at this width, so there is nowhere else for it.
    expect(document.querySelector('header')).toBeNull();
  });

  it('opens the same panel from the header trigger', async () => {
    stubViewport(390);
    render(<Layout />);

    await waitFor(() => expect(document.querySelector('header')).toBeInTheDocument());
    const header = document.querySelector('header')!;
    fireEvent.click(within(header).getByRole('button', { name: /report a bug|bug/i }));

    await waitFor(() => expect(document.getElementById('bug-report-modal')).toBeInTheDocument());
  });
});

describe('bug-report panel geometry', () => {
  afterEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: defaultMatchMedia,
    });
  });

  it('is a full-width bottom sheet on a phone', async () => {
    // The regression: `fixed ... right-4 w-full max-w-md` resolves w-full
    // against the viewport, so on a 390px screen the panel was 390px wide and
    // then inset 16px from the right — putting its left edge at -16px and
    // cutting a strip of the form off-screen. max-w-md hid this above ~464px.
    stubViewport(390);
    render(<BugReportBubble open onOpenChange={() => {}} />);

    const panel = await waitFor(() => {
      const el = document.getElementById('bug-report-modal');
      expect(el).toBeInTheDocument();
      return el!;
    });
    expect(panel.className).toContain('inset-x-0');
    expect(panel.className).not.toContain('right-4');
    expect(panel.className).not.toContain('w-full');
  });

  it('anchors under the header when the trigger lives there (tablet band)', async () => {
    // 768-1143px: past the bottom-sheet breakpoint but the trigger is in the
    // compact header, so the panel must not open in a corner the user never
    // touched.
    stubViewport(900);
    render(<BugReportBubble showTrigger={false} open onOpenChange={() => {}} />);

    const panel = await waitFor(() => {
      const el = document.getElementById('bug-report-modal');
      expect(el).toBeInTheDocument();
      return el!;
    });
    expect(panel.className).toContain('top-16');
    expect(panel.className).not.toContain('bottom-20');
  });

  it('keeps the anchored card on desktop', async () => {
    stubViewport(1440);
    render(<BugReportBubble open onOpenChange={() => {}} />);

    const panel = await waitFor(() => {
      const el = document.getElementById('bug-report-modal');
      expect(el).toBeInTheDocument();
      return el!;
    });
    expect(panel.className).toContain('right-4');
    expect(panel.className).toContain('max-w-md');
  });
});

describe('bug-report form reset', () => {
  it('clears a half-filled form when reopened from a controlled trigger', async () => {
    // The reset used to live in the floating disc's click handler. With the
    // header trigger only flipping a controlled flag, that would have left the
    // previous draft sitting there for compact layouts.
    const user = userEvent.setup();
    const { rerender } = render(<BugReportBubble showTrigger={false} open onOpenChange={() => {}} />);

    const textarea = await waitFor(() => document.querySelector('textarea')!);
    await user.type(textarea, 'half-written report');
    expect(textarea).toHaveValue('half-written report');

    rerender(<BugReportBubble showTrigger={false} open={false} onOpenChange={() => {}} />);
    rerender(<BugReportBubble showTrigger={false} open onOpenChange={() => {}} />);

    await waitFor(() => expect(document.querySelector('textarea')).toHaveValue(''));
  });

  it('renders no floating disc when the trigger is hosted elsewhere', () => {
    render(<BugReportBubble showTrigger={false} />);
    expect(floatingDisc()).toBeUndefined();
  });
});
