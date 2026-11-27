/**
 * An archive printed more than once (a reprint, or each plate of a multi-plate
 * file) shows what all its prints used and cost, as Statistics adds them up
 * (#3609). The archive's own figures are the whole file's filament and the
 * first print's energy, which side by side matched nothing.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { render } from '../utils';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';

const base = {
  printer_id: 1,
  printer_name: 'X2D',
  print_time_seconds: 36000,
  thumbnail_path: null,
  notes: null,
  rating: null,
  project_id: null,
  project_name: null,
  project_color: null,
  print_count: 1,
  tags: '',
  has_f3d: false,
  status: 'completed',
  started_at: '2026-11-10T10:00:00Z',
  completed_at: '2026-11-10T20:00:00Z',
  created_at: '2026-11-10T10:00:00Z',
  updated_at: '2026-11-10T10:00:00Z',
  successful_run_count: 0,
  failed_run_count: 0,
};

const archives = [
  {
    ...base,
    id: 1,
    filename: 'snowman.gcode.3mf',
    print_name: 'Snowman',
    // The whole file and the first plate's energy.
    filament_used_grams: 837,
    cost: 45.17,
    energy_kwh: 0.2,
    energy_cost: 0.24,
    run_count: 7,
    successful_run_count: 7,
    total_filament_actual_grams: 832,
    total_cost: 47.51,
    total_energy_kwh: 3.095,
    total_energy_cost: 3.93,
  },
  {
    ...base,
    id: 2,
    filename: 'single.gcode.3mf',
    print_name: 'Single',
    filament_used_grams: 12,
    cost: 0.5,
    energy_kwh: 0.05,
    energy_cost: 0.06,
    run_count: 1,
    successful_run_count: 1,
    total_filament_actual_grams: 12,
    total_cost: 0.5,
    total_energy_kwh: 0.05,
    total_energy_cost: 0.06,
  },
  {
    ...base,
    id: 3,
    filename: 'unplugged.gcode.3mf',
    print_name: 'Unplugged',
    filament_used_grams: 30,
    cost: 1.2,
    energy_kwh: 0.1,
    energy_cost: 0.12,
    run_count: 2,
    successful_run_count: 2,
    total_filament_actual_grams: 60,
    total_cost: 2.4,
    total_energy_kwh: null,
    total_energy_cost: null,
  },
];

function cardOf(name: string): HTMLElement {
  return screen.getByText(name).closest('[data-archive-id]') as HTMLElement;
}

describe('ArchivesPage run totals (#3609)', () => {
  beforeEach(() => {
    setAuthToken(null);
    server.use(
      http.get('/api/v1/archives/', () => HttpResponse.json(archives)),
      http.get('/api/v1/archives/stats', () =>
        HttpResponse.json({ total_archives: 3, total_print_time_seconds: 0, total_filament_grams: 0 })
      ),
      http.get('/api/v1/printers/', () => HttpResponse.json([{ id: 1, name: 'X2D' }])),
      http.get('/api/v1/projects/', () => HttpResponse.json([])),
      http.get('/api/v1/archives/tags', () => HttpResponse.json([]))
    );
  });

  it('shows the totals of all prints on an archive printed plate by plate', async () => {
    render(<ArchivesPage />);
    await waitFor(() => expect(screen.getAllByTestId('archive-cost')).toHaveLength(3));

    const card = within(cardOf('Snowman'));
    expect(card.getByTestId('archive-grams').textContent).toBe('832.0g');
    expect(card.getByTestId('archive-cost').textContent).toMatch(/47\.51$/);
    expect(card.getByTestId('archive-cost')).toHaveAttribute('title', 'Total of 7 prints');
    expect(card.getByTestId('archive-energy-cost').textContent).toMatch(/3\.93$/);
  });

  it('keeps the archive figures for a single print', async () => {
    render(<ArchivesPage />);
    await waitFor(() => expect(screen.getAllByTestId('archive-cost')).toHaveLength(3));

    const card = within(cardOf('Single'));
    expect(card.getByTestId('archive-cost').textContent).toMatch(/0\.50$/);
    expect(card.getByTestId('archive-cost')).not.toHaveAttribute('title');
  });

  it("shows no energy rather than one print's when no print was measured", async () => {
    render(<ArchivesPage />);
    await waitFor(() => expect(screen.getAllByTestId('archive-cost')).toHaveLength(3));

    const card = within(cardOf('Unplugged'));
    expect(card.getByTestId('archive-cost').textContent).toMatch(/2\.40$/);
    expect(card.queryByTestId('archive-energy-cost')).toBeNull();
  });
});
