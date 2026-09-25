/**
 * Tests for the FilamentTrends widget's print counting.
 *
 * An archive edited down to 0 items produced nothing (#3051). The count here
 * used to read `quantity || 1`, which turned that 0 back into one print and
 * left this widget contradicting the project page.
 */

import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import { FilamentTrends } from '../../components/FilamentTrends';
import type { ArchiveSlim } from '../../api/client';

const archive = (overrides: Partial<ArchiveSlim>): ArchiveSlim => ({
  printer_id: 1,
  print_name: 'Benchy',
  print_time_seconds: 3600,
  actual_time_seconds: 3600,
  filament_used_grams: 20,
  filament_type: 'PLA',
  filament_color: '#00ae42',
  status: 'completed',
  started_at: '2026-09-06T10:00:00Z',
  completed_at: '2026-09-06T11:00:00Z',
  cost: 1,
  energy_kwh: 0.1,
  energy_cost: 0.02,
  quantity: 1,
  created_at: '2026-09-06T10:00:00Z',
  ...overrides,
});

/** The "<n> prints" caption under the summary heading. */
const printCount = () =>
  screen
    .getAllByText((_, el) => el?.tagName === 'P' && /^\d+ prints$/.test(el.textContent ?? ''))
    .map((el) => el.textContent)[0];

describe('FilamentTrends print count (#3051)', () => {
  it('counts a ruined plate as zero prints, not one', () => {
    render(<FilamentTrends archives={[archive({ quantity: 0 }), archive({ quantity: 3 })]} />);

    expect(printCount()).toBe('3 prints');
  });

  it('still treats a missing quantity as a single print', () => {
    const noQuantity = archive({});
    delete (noQuantity as Partial<ArchiveSlim>).quantity;

    render(<FilamentTrends archives={[noQuantity as ArchiveSlim]} />);

    expect(printCount()).toBe('1 prints');
  });
});
