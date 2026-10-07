/**
 * The Archives material filter offers each material once and matches a
 * multi-material archive under every one of its materials, whichever separator
 * its `filament_type` was written with (#3262).
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';

const base = {
  printer_id: 1,
  printer_name: 'H2S',
  print_time_seconds: 3600,
  filament_used_grams: 9,
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
  started_at: '2026-10-07T10:00:00Z',
  completed_at: '2026-10-07T11:00:00Z',
  created_at: '2026-10-07T10:00:00Z',
  updated_at: '2026-10-07T11:00:00Z',
};

const archives = [
  { ...base, id: 1, filename: 'a.gcode.3mf', print_name: 'FromSpools', filament_type: 'PLA Basic,PLA' },
  { ...base, id: 2, filename: 'b.gcode.3mf', print_name: 'FromSlicer', filament_type: 'PLA, PLA-S' },
  { ...base, id: 3, filename: 'c.gcode.3mf', print_name: 'PetgOnly', filament_type: 'PETG' },
];

const materialSelect = () =>
  screen.getAllByRole('combobox').find((el) => el.querySelector('option[value=""]')?.textContent === 'All Materials')!;

describe('Archives material filter (#3262)', () => {
  beforeEach(() => {
    localStorage.removeItem('archiveFilterMaterial');
    setAuthToken(null);
    server.use(
      http.get('/api/v1/archives/', () => HttpResponse.json(archives)),
      http.get('/api/v1/archives/stats', () =>
        HttpResponse.json({
          total_archives: 3,
          total_print_time_seconds: 10800,
          total_filament_grams: 27,
          prints_this_week: 3,
          prints_this_month: 3,
        })
      ),
      http.get('/api/v1/printers/', () => HttpResponse.json([{ id: 1, name: 'H2S' }])),
      http.get('/api/v1/projects/', () => HttpResponse.json([])),
      http.get('/api/v1/archives/tags', () => HttpResponse.json([]))
    );
  });

  it('lists each material once, not the joined string', async () => {
    render(<ArchivesPage />);
    await waitFor(() => expect(screen.getByText('PetgOnly')).toBeInTheDocument());

    const options = Array.from(materialSelect().querySelectorAll('option')).map((o) => o.getAttribute('value'));
    expect(options).toEqual(['', 'PETG', 'PLA', 'PLA Basic', 'PLA-S']);
  });

  it('matches archives under each of their materials, whatever the separator', async () => {
    render(<ArchivesPage />);
    await waitFor(() => expect(screen.getByText('PetgOnly')).toBeInTheDocument());

    fireEvent.change(materialSelect(), { target: { value: 'PLA' } });

    await waitFor(() => expect(screen.queryByText('PetgOnly')).not.toBeInTheDocument());
    expect(screen.getByText('FromSpools')).toBeInTheDocument();
    expect(screen.getByText('FromSlicer')).toBeInTheDocument();
  });

  it('drops a saved joined material from before the fix instead of hiding everything', async () => {
    localStorage.setItem('archiveFilterMaterial', 'PLA Basic,PLA');

    render(<ArchivesPage />);

    await waitFor(() => expect(screen.getByText('PetgOnly')).toBeInTheDocument());
    expect(screen.getByText('FromSpools')).toBeInTheDocument();
    expect((materialSelect() as HTMLSelectElement).value).toBe('');
  });
});
