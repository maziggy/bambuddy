import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolmanFilamentPicker } from '../../components/spool-form/SpoolmanFilamentPicker';
import type { SpoolmanFilamentEntry } from '../../api/client';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
  }),
}));

const FILAMENTS: SpoolmanFilamentEntry[] = [
  {
    id: 1,
    name: 'PLA Basic',
    material: 'PLA',
    color_hex: 'FF0000',
    color_name: 'Red',
    weight: 1000,
    spool_weight: 196,
    vendor: { id: 1, name: 'Bambu Lab' },
  },
  {
    id: 2,
    name: 'PETG',
    material: 'PETG',
    color_hex: '00FF00',
    color_name: 'Green',
    weight: 1000,
    spool_weight: null,
    vendor: { id: 2, name: 'Bambu Lab' },
  },
  {
    id: 3,
    name: 'ABS Basic',
    material: 'ABS',
    color_hex: null,
    color_name: null,
    weight: 1000,
    spool_weight: 250,
    vendor: null,
  },
];

function openDropdown() {
  const trigger = screen.getByRole('button', { name: /spoolman/i });
  fireEvent.click(trigger);
}

describe('SpoolmanFilamentPicker', () => {
  it('renders the trigger button with catalog label when nothing selected', () => {
    render(
      <SpoolmanFilamentPicker
        filaments={FILAMENTS}
        isLoading={false}
        selectedId={null}
        onSelect={vi.fn()}
      />
    );
    expect(screen.getByText('inventory.spoolmanFilamentCatalog')).toBeTruthy();
  });

  it('shows all filaments when dropdown is opened', () => {
    render(
      <SpoolmanFilamentPicker
        filaments={FILAMENTS}
        isLoading={false}
        selectedId={null}
        onSelect={vi.fn()}
      />
    );
    openDropdown();
    expect(screen.getByText(/Bambu Lab — PLA Basic/)).toBeTruthy();
    expect(screen.getByText(/Bambu Lab — PETG/)).toBeTruthy();
    expect(screen.getByText(/ABS Basic/)).toBeTruthy();
  });

  it('filters by search term (material)', () => {
    render(
      <SpoolmanFilamentPicker
        filaments={FILAMENTS}
        isLoading={false}
        selectedId={null}
        onSelect={vi.fn()}
      />
    );
    openDropdown();
    const searchInput = screen.getByPlaceholderText('inventory.pickFromSpoolmanCatalog');
    fireEvent.change(searchInput, { target: { value: 'PETG' } });
    expect(screen.getAllByText(/PETG/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/PLA Basic/)).toBeNull();
    expect(screen.queryByText(/ABS Basic/)).toBeNull();
  });

  it('filters by vendor name', () => {
    render(
      <SpoolmanFilamentPicker
        filaments={FILAMENTS}
        isLoading={false}
        selectedId={null}
        onSelect={vi.fn()}
      />
    );
    openDropdown();
    const searchInput = screen.getByPlaceholderText('inventory.pickFromSpoolmanCatalog');
    fireEvent.change(searchInput, { target: { value: 'Bambu' } });
    // ABS Basic has no vendor — should be filtered out
    expect(screen.queryByText(/ABS Basic/)).toBeNull();
  });

  it('calls onSelect with the correct filament when an item is clicked', () => {
    const onSelect = vi.fn();
    render(
      <SpoolmanFilamentPicker
        filaments={FILAMENTS}
        isLoading={false}
        selectedId={null}
        onSelect={onSelect}
      />
    );
    openDropdown();
    fireEvent.click(screen.getByText(/Bambu Lab — PLA Basic/));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith(FILAMENTS[0]);
  });

  it('shows empty state when no filaments match search', () => {
    render(
      <SpoolmanFilamentPicker
        filaments={FILAMENTS}
        isLoading={false}
        selectedId={null}
        onSelect={vi.fn()}
      />
    );
    openDropdown();
    const searchInput = screen.getByPlaceholderText('inventory.pickFromSpoolmanCatalog');
    fireEvent.change(searchInput, { target: { value: 'xyzzy-not-found' } });
    expect(screen.getByText('inventory.noSpoolmanFilaments')).toBeTruthy();
  });

  it('shows loading spinner when isLoading is true', () => {
    render(
      <SpoolmanFilamentPicker
        filaments={[]}
        isLoading={true}
        selectedId={null}
        onSelect={vi.fn()}
      />
    );
    // Trigger button shows spinner (Loader2 icon with animate-spin)
    const spinners = document.querySelectorAll('.animate-spin');
    expect(spinners.length).toBeGreaterThan(0);
  });

  it('shows selected filament name in trigger button', () => {
    render(
      <SpoolmanFilamentPicker
        filaments={FILAMENTS}
        isLoading={false}
        selectedId={1}
        onSelect={vi.fn()}
      />
    );
    // Should display the selected filament in the trigger
    expect(screen.getByText(/Bambu Lab — PLA Basic/)).toBeTruthy();
  });

  it('filters by color_name', () => {
    render(
      <SpoolmanFilamentPicker
        filaments={FILAMENTS}
        isLoading={false}
        selectedId={null}
        onSelect={vi.fn()}
      />
    );
    openDropdown();
    const searchInput = screen.getByPlaceholderText('inventory.pickFromSpoolmanCatalog');
    fireEvent.change(searchInput, { target: { value: 'Green' } });
    // PETG has color_name 'Green' — should match
    expect(screen.getAllByText(/PETG/).length).toBeGreaterThan(0);
    // PLA Basic has color_name 'Red' — should be filtered out
    expect(screen.queryByText(/PLA Basic/)).toBeNull();
  });
});
