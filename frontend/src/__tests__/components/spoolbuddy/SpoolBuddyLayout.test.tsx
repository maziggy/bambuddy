/**
 * Tests for SpoolBuddyLayout component:
 * - Renders without crashing
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { SpoolBuddyLayout } from '../../../components/spoolbuddy/SpoolBuddyLayout';
import { ToastProvider } from '../../../contexts/ToastContext';

// The kiosk runs signed in; these tests render it without the auth provider.
vi.mock('../../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => ({ hasPermission: () => true }) };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

vi.mock('../../../api/client', () => ({
  api: {
    getPrinters: vi.fn().mockResolvedValue([]),
    getPrinterStatus: vi.fn().mockResolvedValue({ connected: false }),
    getSettings: vi.fn().mockResolvedValue({ time_format: 'system', language: 'en' }),
  },
  spoolbuddyApi: {
    getDevices: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('../../../utils/date', () => ({
  formatTimeOnly: () => '12:00',
}));

vi.mock('lucide-react', () => {
  const Stub = (props: Record<string, unknown>) => <span {...props} />;
  return {
    WifiOff: (props: Record<string, unknown>) => <span data-testid="wifi-off" {...props} />,
    // ToastProvider, brought in by SpoolBuddyLayout's useToast(), imports these.
    AlertCircle: Stub,
    CheckCircle: Stub,
    ChevronDown: Stub,
    ChevronUp: Stub,
    Info: Stub,
    Loader2: Stub,
    X: Stub,
    XCircle: Stub,
  };
});

vi.mock('../../../components/VirtualKeyboard', () => ({
  VirtualKeyboard: () => <div data-testid="virtual-keyboard" />,
}));

function renderLayout() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <ToastProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/spoolbuddy']}>
          <Routes>
            <Route path="spoolbuddy" element={<SpoolBuddyLayout />}>
              <Route index element={<div data-testid="child-page">Child</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ToastProvider>
  );
}

describe('SpoolBuddyLayout', () => {
  it('says so in the status bar when a scanned tag could not be looked up', async () => {
    renderLayout();

    act(() => {
      window.dispatchEvent(
        new CustomEvent('spoolman-unavailable', { detail: { type: 'spoolman_unavailable', device_id: 'sb-1' } }),
      );
    });

    expect(await screen.findByText('spoolbuddy.status.spoolmanUnavailable')).toBeDefined();
  });

  it('clears the lookup notice after a while and gives the status bar back', async () => {
    renderLayout();
    expect(await screen.findByText('SpoolBuddy device disconnected')).toBeDefined();
    vi.useFakeTimers();

    act(() => {
      window.dispatchEvent(new CustomEvent('spoolbuddy-lookup-error', { detail: { device_id: 'sb-1' } }));
    });
    expect(screen.getByText('spoolbuddy.status.lookupFailed')).toBeDefined();
    expect(screen.queryByText('SpoolBuddy device disconnected')).toBeNull();

    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(screen.queryByText('spoolbuddy.status.lookupFailed')).toBeNull();
    expect(screen.getByText('SpoolBuddy device disconnected')).toBeDefined();
    vi.useRealTimers();
  });

  it('renders without crashing', () => {
    const { container } = renderLayout();
    expect(container.firstChild).not.toBeNull();
  });

  it('renders the top bar with logo', () => {
    renderLayout();
    const img = document.querySelector('img[alt="SpoolBuddy"]');
    expect(img).not.toBeNull();
  });

  it('renders the bottom nav', () => {
    renderLayout();
    const nav = document.querySelector('nav');
    expect(nav).not.toBeNull();
  });

  it('renders the status bar', () => {
    renderLayout();
    // Status bar shows "System Ready" by default (device offline triggers warning later via useEffect)
    // Just check the status bar container exists
    const statusBar = document.querySelector('.shrink-0.h-9');
    expect(statusBar).not.toBeNull();
  });

  it('renders child outlet content', () => {
    renderLayout();
    const child = document.querySelector('[data-testid="child-page"]');
    expect(child).not.toBeNull();
  });

  it('suppresses the global toast viewport while mounted', () => {
    const { unmount } = renderLayout();
    // Visible viewport gets `hidden` class while the kiosk is up. Position is
    // set via safe-area calc() (#2612) so match the stable data-testid.
    const viewport = document.querySelector('[data-testid="toast-viewport"]');
    expect(viewport?.className).toContain('hidden');

    // Cleanup restores the viewport when the kiosk unmounts (e.g. user
    // navigates back to the main app).
    unmount();
    const viewportAfter = document.querySelector('[data-testid="toast-viewport"]');
    // After unmount the toast container is gone with the provider; the
    // important guarantee is the suppression flag was untoggled, which the
    // ToastContext tests pin directly. Here we only assert no crash on
    // unmount during cleanup.
    expect(viewportAfter).toBeNull();
  });
});
