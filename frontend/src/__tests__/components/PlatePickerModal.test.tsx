/**
 * Tests for PlatePickerModal.
 *
 * The modal lets the user pick a plate before the GCode viewer opens.
 * Only shown for multi-plate archives with sliced gcode.
 */

import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PlatePickerModal } from '../../components/PlatePickerModal';
import type { PlateMetadata } from '../../types/plates';

const makePlate = (overrides: Partial<PlateMetadata>): PlateMetadata => ({
  index: 1,
  name: null,
  objects: [],
  object_count: 0,
  has_thumbnail: false,
  thumbnail_url: null,
  print_time_seconds: null,
  filament_used_grams: null,
  filaments: [],
  ...overrides,
});

describe('PlatePickerModal', () => {
  it('renders one row per plate with the plate label', () => {
    const plates = [makePlate({ index: 1 }), makePlate({ index: 2 }), makePlate({ index: 3 })];
    render(<PlatePickerModal plates={plates} onSelect={() => {}} onClose={() => {}} />);

    // Each plate index gets its own row — check all three are present.
    expect(screen.getByText(/plate 1/i)).toBeInTheDocument();
    expect(screen.getByText(/plate 2/i)).toBeInTheDocument();
    expect(screen.getByText(/plate 3/i)).toBeInTheDocument();
  });

  it('renders the plate name alongside the index when set', () => {
    const plates = [makePlate({ index: 4, name: 'Spinner Nose' })];
    render(<PlatePickerModal plates={plates} onSelect={() => {}} onClose={() => {}} />);

    // Label combines the plate number with the user-defined name.
    expect(screen.getByText(/spinner nose/i)).toBeInTheDocument();
  });

  it('passes the clicked plate index to onSelect', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const plates = [makePlate({ index: 7 }), makePlate({ index: 12 })];
    render(<PlatePickerModal plates={plates} onSelect={onSelect} onClose={() => {}} />);

    await user.click(screen.getByText(/plate 12/i));

    // The handler receives the raw plate index — that's what the URL param
    // needs (so `?plate=12` maps to the archive's plate_12.gcode).
    expect(onSelect).toHaveBeenCalledWith(12);
  });

  it('calls onClose when the backdrop is clicked', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<PlatePickerModal plates={[makePlate({})]} onSelect={() => {}} onClose={onClose} />);

    // The outermost div is the backdrop; clicking it fires onClose.
    // Plate rows stop propagation so they can't accidentally close the modal.
    const backdrop = document.querySelector('[class*="fixed"]') as HTMLElement;
    expect(backdrop).toBeTruthy();
    await user.click(backdrop);

    expect(onClose).toHaveBeenCalled();
  });

  it('falls back to a layer-icon placeholder when a plate has no thumbnail', () => {
    const plates = [makePlate({ index: 1, has_thumbnail: false, thumbnail_url: null })];
    render(<PlatePickerModal plates={plates} onSelect={() => {}} onClose={() => {}} />);

    // No <img> rendered for the thumbnail; the placeholder div takes its slot.
    // This guards against a regression where a missing-thumbnail plate
    // accidentally renders a broken-image icon instead of the fallback.
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('shows the thumbnail image when the plate has one', () => {
    const plates = [
      makePlate({
        index: 1,
        has_thumbnail: true,
        thumbnail_url: '/api/v1/archives/42/plate-thumbnail/1',
      }),
    ];
    render(<PlatePickerModal plates={plates} onSelect={() => {}} onClose={() => {}} />);

    // The <img> is present and its src was transformed by withStreamToken,
    // which appends ?token=... even on a bare placeholder — we just want the
    // base path preserved.
    const img = screen.getByAltText(/plate 1/i) as HTMLImageElement;
    expect(img.src).toContain('/api/v1/archives/42/plate-thumbnail/1');
  });
});
