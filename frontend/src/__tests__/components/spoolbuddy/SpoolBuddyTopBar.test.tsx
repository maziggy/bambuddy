/**
 * Tests for SpoolBuddyTopBar component:
 * - Renders the logo image
 * - Renders the printer selector
 * - Shows backend status indicator
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SpoolBuddyTopBar } from '../../../components/spoolbuddy/SpoolBuddyTopBar';

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
    getSettings: vi.fn().mockResolvedValue({ time_format: 'system' }),
  },
}));

vi.mock('../../../utils/date', () => ({
  formatTimeOnly: () => '12:00',
}));

vi.mock('lucide-react', () => ({
  WifiOff: (props: Record<string, unknown>) => <span data-testid="wifi-off" {...props} />,
}));

function renderTopBar(deviceOnline = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={qc}>
      <SpoolBuddyTopBar
        selectedPrinterId={null}
        onPrinterChange={vi.fn()}
        deviceOnline={deviceOnline}
      />
    </QueryClientProvider>
  );
}

describe('SpoolBuddyTopBar', () => {
  it('renders the logo image', () => {
    renderTopBar();
    const img = screen.getByAltText('SpoolBuddy');
    expect(img).toBeDefined();
    expect(img.getAttribute('src')).toBe('/img/spoolbuddy_logo_dark_small.png');
  });

  it('renders the printer selector', () => {
    renderTopBar();
    // Select element with "No printers online" fallback
    const select = screen.getByRole('combobox');
    expect(select).toBeDefined();
  });

  it('shows offline status when device is offline', () => {
    renderTopBar(false);
    expect(screen.getByText('Offline')).toBeDefined();
    expect(screen.getByTestId('wifi-off')).toBeDefined();
  });

  it('shows backend status when device is online', () => {
    renderTopBar(true);
    expect(screen.getByText('Backend')).toBeDefined();
  });

  it('shows clock time', () => {
    renderTopBar();
    expect(screen.getByText('12:00')).toBeDefined();
  });
});
