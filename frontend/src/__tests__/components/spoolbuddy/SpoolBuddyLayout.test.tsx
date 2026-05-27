/**
 * Tests for SpoolBuddyLayout component:
 * - Renders without crashing
 */

import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { SpoolBuddyLayout } from '../../../components/spoolbuddy/SpoolBuddyLayout';
import { ToastProvider } from '../../../contexts/ToastContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
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
    // Visible viewport gets `hidden` class while the kiosk is up.
    const viewport = document.querySelector('div.fixed.bottom-4.right-20');
    expect(viewport?.className).toContain('hidden');

    // Cleanup restores the viewport when the kiosk unmounts (e.g. user
    // navigates back to the main app).
    unmount();
    const viewportAfter = document.querySelector('div.fixed.bottom-4.right-20');
    // After unmount the toast container is gone with the provider; the
    // important guarantee is the suppression flag was untoggled, which the
    // ToastContext tests pin directly. Here we only assert no crash on
    // unmount during cleanup.
    expect(viewportAfter).toBeNull();
  });
});
