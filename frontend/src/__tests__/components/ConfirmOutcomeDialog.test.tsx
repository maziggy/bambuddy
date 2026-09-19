/**
 * Tests for the post-print outcome confirmation dialog (#1898).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { ConfirmOutcomeDialog } from '../../components/ConfirmOutcomeDialog';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// The reject flow can hand off into the full PrintModal — not under test here.
vi.mock('../../components/PrintModal', () => ({
  PrintModal: () => <div data-testid="print-modal" />,
}));

const baseArchive = {
  id: 42,
  printer_id: 1,
  filename: 'bracket.gcode.3mf',
  file_path: 'archives/bracket.gcode.3mf',
  file_size: 1024,
  content_hash: null,
  thumbnail_path: null,
  timelapse_path: null,
  print_name: 'Bracket',
  print_time_seconds: 3600,
  filament_used_grams: 20,
  filament_type: 'PLA',
  filament_color: null,
  layer_height: 0.2,
  total_layers: 100,
  nozzle_diameter: 0.4,
  bed_temperature: 60,
  nozzle_temperature: 220,
  status: 'completed',
  started_at: null,
  completed_at: null,
  extra_data: null,
  makerworld_url: null,
  designer: null,
  external_url: null,
  is_favorite: false,
  tags: null,
  notes: null,
  cost: null,
  photos: ['finish_1.jpg'],
  failure_reason: null,
  user_verdict: null,
  confirm_requested: true,
  quantity: 1,
  energy_kwh: null,
  energy_cost: null,
  created_at: '2026-09-01T00:00:00Z',
  created_by_id: null,
  created_by_username: null,
  run_count: 1,
  last_run_at: null,
  total_filament_actual_grams: null,
  successful_run_count: 1,
  failed_run_count: 0,
};

describe('ConfirmOutcomeDialog', () => {
  let patchPayload: Record<string, unknown> | null;

  beforeEach(() => {
    patchPayload = null;
    server.use(
      http.get('/api/v1/archives/42', () => HttpResponse.json(baseArchive)),
      http.patch('/api/v1/archives/42', async ({ request }) => {
        patchPayload = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...baseArchive, ...patchPayload });
      }),
      http.get('/api/v1/settings/', () => HttpResponse.json({})),
    );
  });

  it('shows the finish photo and records a good verdict', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<ConfirmOutcomeDialog archiveId={42} onClose={onClose} />);

    await waitFor(() => {
      expect(screen.getByText('Bracket')).toBeInTheDocument();
    });
    // Finish photo (photos[0]) is rendered
    expect(screen.getByAltText('Bracket')).toBeInTheDocument();

    await user.click(screen.getByText('Good'));

    await waitFor(() => {
      expect(patchPayload).toEqual({ user_verdict: 'good' });
    });
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('records a reject with an optional reason', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<ConfirmOutcomeDialog archiveId={42} onClose={onClose} />);

    await waitFor(() => {
      expect(screen.getByText('Bracket')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Reject'));
    // Reason step appears
    const select = await screen.findByRole('combobox');
    await user.selectOptions(select, 'warping');
    await user.click(screen.getByText('Save'));

    await waitFor(() => {
      expect(patchPayload).toEqual({ user_verdict: 'reject', failure_reason: 'warping' });
    });
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('reject-and-reprint hands off into the print modal after saving', async () => {
    const user = userEvent.setup();
    render(<ConfirmOutcomeDialog archiveId={42} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Bracket')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Reject'));
    await user.click(await screen.findByText('Print again'));

    await waitFor(() => {
      expect(patchPayload).toEqual({ user_verdict: 'reject' });
    });
    await waitFor(() => {
      expect(screen.getByTestId('print-modal')).toBeInTheDocument();
    });
  });

  it('shows the already-decided state instead of the buttons', async () => {
    server.use(
      http.get('/api/v1/archives/42', () =>
        HttpResponse.json({ ...baseArchive, user_verdict: 'reject' })
      ),
    );
    render(<ConfirmOutcomeDialog archiveId={42} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('Already marked as rejected.')).toBeInTheDocument();
    });
    expect(screen.queryByText('Good')).not.toBeInTheDocument();
  });
});
