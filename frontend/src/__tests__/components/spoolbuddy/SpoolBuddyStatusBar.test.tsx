/**
 * Tests for SpoolBuddyStatusBar component:
 * - Shows "System Ready" with green when no alert
 * - Shows warning message with amber styling
 * - Shows error message with red styling
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { SpoolBuddyStatusBar } from '../../../components/spoolbuddy/SpoolBuddyStatusBar';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

describe('SpoolBuddyStatusBar', () => {
  it('shows "System Ready" when no alert', () => {
    render(<SpoolBuddyStatusBar />);
    expect(screen.getByText('System Ready')).toBeDefined();
  });

  it('uses green status LED when no alert', () => {
    const { container } = render(<SpoolBuddyStatusBar />);
    const led = container.querySelector('.rounded-full.animate-pulse');
    expect(led!.className).toContain('bg-bambu-green');
  });

  it('shows warning message with amber styling', () => {
    const { container } = render(
      <SpoolBuddyStatusBar alert={{ type: 'warning', message: 'Low filament' }} />
    );
    expect(screen.getByText('Low filament')).toBeDefined();
    const led = container.querySelector('.rounded-full.animate-pulse');
    expect(led!.className).toContain('bg-amber-500');
    // Border should also be amber
    const bar = container.firstElementChild as HTMLElement;
    expect(bar.className).toContain('border-amber-500');
  });

  it('shows error message with red styling', () => {
    const { container } = render(
      <SpoolBuddyStatusBar alert={{ type: 'error', message: 'Connection lost' }} />
    );
    expect(screen.getByText('Connection lost')).toBeDefined();
    const led = container.querySelector('.rounded-full.animate-pulse');
    expect(led!.className).toContain('bg-red-500');
    const bar = container.firstElementChild as HTMLElement;
    expect(bar.className).toContain('border-red-500');
  });

  it('shows info alert with green styling', () => {
    const { container } = render(
      <SpoolBuddyStatusBar alert={{ type: 'info', message: 'Update available' }} />
    );
    expect(screen.getByText('Update available')).toBeDefined();
    const led = container.querySelector('.rounded-full.animate-pulse');
    expect(led!.className).toContain('bg-bambu-green');
  });
});
