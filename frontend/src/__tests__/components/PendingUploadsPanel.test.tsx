/**
 * Tests for PendingUploadsPanel — review-card name resolution (#1152 follow-up).
 *
 * The panel renders ``upload.display_name`` (the resolved name that mirrors
 * what the eventual archive's ``print_name`` will be) and falls back to
 * ``upload.filename`` when the display_name is missing — guards against a
 * pending row created before the column landed (or a serialiser bug) showing
 * a blank card.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { render } from '../utils';
import { PendingUploadsPanel } from '../../components/PendingUploadsPanel';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

interface MockUpload {
  id: number;
  filename: string;
  display_name: string;
  file_size: number;
  source_ip: string | null;
  status: string;
  tags: string | null;
  notes: string | null;
  project_id: number | null;
  uploaded_at: string;
}

const baseUpload: MockUpload = {
  id: 1,
  filename: 'Plate_1.gcode.3mf',
  display_name: 'My Resolved Name',
  file_size: 12345,
  source_ip: '192.168.1.50',
  status: 'pending',
  tags: null,
  notes: null,
  project_id: null,
  uploaded_at: '2026-05-01T10:00:00Z',
};

describe('PendingUploadsPanel — display_name', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/projects/', () => HttpResponse.json([])),
    );
  });

  it('renders the resolved display_name on the review card', async () => {
    server.use(http.get('/api/v1/pending-uploads/', () => HttpResponse.json([baseUpload])));

    const { findByText } = render(<PendingUploadsPanel />);
    expect(await findByText('My Resolved Name')).toBeInTheDocument();
  });

  it('falls back to filename when display_name is an empty string', async () => {
    // Defensive: a bug in the resolver, a pre-migration row that was somehow
    // re-fetched, or a partial JSON deserialisation must not produce a blank
    // review card. The frontend keeps showing _something_ the user can click.
    server.use(
      http.get('/api/v1/pending-uploads/', () =>
        HttpResponse.json([{ ...baseUpload, display_name: '' }]),
      ),
    );

    const { findByText } = render(<PendingUploadsPanel />);
    expect(await findByText('Plate_1.gcode.3mf')).toBeInTheDocument();
  });

  it('exposes the raw filename via tooltip so the user can see what arrived over FTP', async () => {
    server.use(http.get('/api/v1/pending-uploads/', () => HttpResponse.json([baseUpload])));

    const { findByText } = render(<PendingUploadsPanel />);
    const nameEl = await findByText('My Resolved Name');
    expect(nameEl.getAttribute('title')).toBe('Plate_1.gcode.3mf');
  });
});
