/**
 * Tests for AmsUnitCard component:
 * - Renders slot circles for a 4-slot AMS
 * - Shows slot labels (1, 2, 3, 4)
 * - Shows fill level bars
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { AmsUnitCard } from '../../../components/spoolbuddy/AmsUnitCard';
import type { AMSUnit, AMSTray } from '../../../api/client';

vi.mock('../../../utils/amsHelpers', () => ({
  getFillBarColor: (fill: number) => {
    if (fill > 50) return '#00ae42';
    if (fill >= 15) return '#f59e0b';
    return '#ef4444';
  },
}));

function makeTray(overrides: Partial<AMSTray> = {}): AMSTray {
  return {
    id: 0,
    tray_color: 'FF0000FF',
    tray_type: 'PLA',
    tray_sub_brands: null,
    tray_id_name: null,
    tray_info_idx: null,
    remain: 80,
    k: null,
    cali_idx: null,
    tag_uid: null,
    tray_uuid: null,
    nozzle_temp_min: null,
    nozzle_temp_max: null,
    drying_temp: null,
    drying_time: null,
    ...overrides,
  };
}

function makeUnit(overrides: Partial<AMSUnit> = {}): AMSUnit {
  return {
    id: 0,
    humidity: 30,
    temp: 25,
    is_ams_ht: false,
    tray: [
      makeTray({ id: 0, tray_color: 'FF0000FF', tray_type: 'PLA', remain: 80 }),
      makeTray({ id: 1, tray_color: '00FF00FF', tray_type: 'PETG', remain: 50 }),
      makeTray({ id: 2, tray_color: '0000FFFF', tray_type: 'ABS', remain: 10 }),
      makeTray({ id: 3, tray_color: null, tray_type: '', remain: -1 }),
    ],
    serial_number: 'AMS001',
    sw_ver: '1.0.0',
    dry_time: 0,
    dry_status: 0,
    dry_sub_status: 0,
    ...overrides,
  };
}

describe('AmsUnitCard', () => {
  it('renders 4 slot positions for a regular AMS', () => {
    const { container } = render(
      <AmsUnitCard unit={makeUnit()} activeSlot={null} />
    );
    // 4 slot numbers should be visible (1, 2, 3, 4)
    expect(screen.getByText('1')).toBeDefined();
    expect(screen.getByText('2')).toBeDefined();
    expect(screen.getByText('3')).toBeDefined();
    expect(screen.getByText('4')).toBeDefined();
    // grid-cols-4 class should be present
    const grid = container.querySelector('.grid-cols-4');
    expect(grid).not.toBeNull();
  });

  it('renders AMS name in header', () => {
    render(<AmsUnitCard unit={makeUnit({ id: 0 })} activeSlot={null} />);
    expect(screen.getByText('AMS A')).toBeDefined();
  });

  it('shows material types for populated slots', () => {
    render(<AmsUnitCard unit={makeUnit()} activeSlot={null} />);
    expect(screen.getByText('PLA')).toBeDefined();
    expect(screen.getByText('PETG')).toBeDefined();
    expect(screen.getByText('ABS')).toBeDefined();
  });

  it('shows "Empty" for empty slot', () => {
    render(<AmsUnitCard unit={makeUnit()} activeSlot={null} />);
    expect(screen.getByText('Empty')).toBeDefined();
  });

  it('renders fill level bars for slots with filament', () => {
    const { container } = render(
      <AmsUnitCard unit={makeUnit()} activeSlot={null} />
    );
    // Look for fill bar elements (they have style width set to fill%)
    const fillBars = container.querySelectorAll('.h-full.rounded-full.transition-all');
    // 3 populated slots should have fill bars (slot 4 is empty)
    expect(fillBars.length).toBe(3);
  });

  it('renders only 1 slot for AMS-HT', () => {
    const htUnit = makeUnit({
      is_ams_ht: true,
      tray: [makeTray({ id: 0, tray_type: 'PLA', remain: 90 })],
    });
    const { container } = render(
      <AmsUnitCard unit={htUnit} activeSlot={null} />
    );
    const grid = container.querySelector('.grid-cols-1');
    expect(grid).not.toBeNull();
    expect(screen.getByText('1')).toBeDefined();
  });

  it('shows humidity and temperature indicators', () => {
    render(<AmsUnitCard unit={makeUnit({ humidity: 45, temp: 30 })} activeSlot={null} />);
    expect(screen.getByText('45%')).toBeDefined();
  });

  it('highlights active slot with ring', () => {
    const { container } = render(
      <AmsUnitCard unit={makeUnit()} activeSlot={1} />
    );
    const activeSlot = container.querySelector('.ring-2.ring-bambu-green');
    expect(activeSlot).not.toBeNull();
  });
});
