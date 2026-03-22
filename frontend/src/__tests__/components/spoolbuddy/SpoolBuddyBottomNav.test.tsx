/**
 * Tests for SpoolBuddyBottomNav component:
 * - Renders 4 nav items (Dashboard, AMS, Write, Settings)
 * - NavLinks have correct paths
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { SpoolBuddyBottomNav } from '../../../components/spoolbuddy/SpoolBuddyBottomNav';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

function renderNav() {
  return render(
    <MemoryRouter initialEntries={['/spoolbuddy']}>
      <SpoolBuddyBottomNav />
    </MemoryRouter>
  );
}

describe('SpoolBuddyBottomNav', () => {
  it('renders 4 nav items', () => {
    renderNav();
    expect(screen.getByText('Dashboard')).toBeDefined();
    expect(screen.getByText('AMS')).toBeDefined();
    expect(screen.getByText('Write')).toBeDefined();
    expect(screen.getByText('Settings')).toBeDefined();
  });

  it('has correct link for Dashboard', () => {
    renderNav();
    const link = screen.getByText('Dashboard').closest('a');
    expect(link!.getAttribute('href')).toBe('/spoolbuddy');
  });

  it('has correct link for AMS', () => {
    renderNav();
    const link = screen.getByText('AMS').closest('a');
    expect(link!.getAttribute('href')).toBe('/spoolbuddy/ams');
  });

  it('has correct link for Write', () => {
    renderNav();
    const link = screen.getByText('Write').closest('a');
    expect(link!.getAttribute('href')).toBe('/spoolbuddy/write-tag');
  });

  it('has correct link for Settings', () => {
    renderNav();
    const link = screen.getByText('Settings').closest('a');
    expect(link!.getAttribute('href')).toBe('/spoolbuddy/settings');
  });
});
