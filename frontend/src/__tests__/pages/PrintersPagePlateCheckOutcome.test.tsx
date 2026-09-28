/**
 * The plate-check Decision panel must derive its rendering from `outcome`,
 * not from `is_empty` alone (PR #2894 review point 1's UI half).
 *
 * Before this fix, a fail-open AI result (`outcome: 'unavailable'`) still
 * rendered the green "Plate appears empty" verdict at 0% confidence, which
 * reads as a real pass when the AI backend actually never produced one. The
 * fix: outcome === 'unavailable' renders an amber no-verdict state instead,
 * with no green verdict box and no Decision matrix (there is no decision to
 * show); outcome === 'degraded' renders the normal verdict plus a small
 * muted note; outcome absent or 'ok' is unchanged from before.
 *
 * Also covers review point 2's UI half: the Confidence row reads
 * `ai_confidence` for the AI backend (not the fail-open `confidence`
 * placeholder), and a null `difference_percent` (always null for AI results)
 * is omitted rather than rendered as "NaN%".
 */
import { describe, it, expect, afterEach } from 'vitest';
import { screen, waitFor, fireEvent, cleanup, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrintersPage } from '../../pages/PrintersPage';

const mockPrinter = {
  id: 1,
  name: 'X1C',
  ip_address: '192.168.1.100',
  serial_number: '01P00A000000001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'stainless_steel',
  location: 'Workshop',
  auto_archive: true,
  plate_detection_enabled: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const mockStatus = {
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -29,
  speed_level: 2,
  vt_tray: [],
  ams: [],
  // true so handleOpenPlateManagement's chamber_light === false branch (which
  // awaits a real 2.5s setTimeout to let the light warm up) never triggers.
  chamber_light: true,
};

type PlateResultOverrides = Partial<{
  is_empty: boolean;
  confidence: number;
  difference_percent: number | null;
  message: string;
  needs_calibration: boolean;
  backend: 'opencv' | 'ai';
  ai_reason: string | null;
  outcome: 'ok' | 'unavailable' | 'degraded';
  ai_confidence: number | null;
}>;

function mockPlateCheckResult(overrides: PlateResultOverrides) {
  const base = {
    is_empty: true,
    confidence: 0,
    difference_percent: null as number | null,
    message: 'plate check message',
    needs_calibration: false,
    has_debug_image: false,
  };
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(mockStatus)),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/camera/check-plate', () =>
      HttpResponse.json({ ...base, ...overrides }),
    ),
    http.get('/api/v1/printers/:id/camera/plate-detection/references', () =>
      HttpResponse.json({ references: [], max_references: 5 }),
    ),
    http.post('/api/v1/printers/:id/chamber-light', () =>
      HttpResponse.json({ success: true, message: 'ok' }),
    ),
  );
}

/**
 * Renders the page, opens printer 1's plate-check modal, and returns its
 * container element — several verdict strings ("Plate appears empty",
 * "Objects detected on plate", confidence percentages) also appear elsewhere
 * on the page, so assertions must be scoped to the modal via `within()`.
 */
async function openPlateCheckModal(): Promise<HTMLElement> {
  render(<PrintersPage />);
  await waitFor(() => expect(document.getElementById('printer-card-1')).not.toBeNull());
  const button = await screen.findByTitle('Manage plate detection calibration');
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  const heading = await screen.findByText('Build Plate Check');
  const modal = heading.closest('.rounded-xl');
  if (!modal) throw new Error('plate check modal container not found');
  return modal as HTMLElement;
}

afterEach(() => {
  server.resetHandlers();
  cleanup();
});

describe('PrintersPage — plate check Decision panel outcome rendering', () => {
  it('outcome "unavailable" renders the amber no-verdict state, not the green empty verdict', async () => {
    mockPlateCheckResult({
      backend: 'ai',
      outcome: 'unavailable',
      ai_reason: 'AI backend timed out after 3 retries',
      is_empty: true,
      confidence: 0,
    });
    const modal = await openPlateCheckModal();

    expect(within(modal).getByText('AI check unavailable — no verdict')).toBeInTheDocument();
    expect(within(modal).getByText('AI backend timed out after 3 retries')).toBeInTheDocument();
    // The old fail-open green verdict must not appear alongside it.
    expect(within(modal).queryByText('Plate appears empty')).not.toBeInTheDocument();
    // No Decision matrix either — there is no real decision to show.
    expect(within(modal).queryByText('Decision')).not.toBeInTheDocument();
  });

  it('outcome absent (legacy payload) renders the pre-existing green/decision-matrix UI unchanged', async () => {
    mockPlateCheckResult({
      backend: 'opencv',
      is_empty: true,
      confidence: 0.91,
      difference_percent: 12.3,
      // outcome intentionally omitted — simulates a server predating this field.
    });
    const modal = await openPlateCheckModal();

    // Appears twice: the top status box and the Decision matrix's Verdict row.
    expect(within(modal).getAllByText('Plate appears empty')).toHaveLength(2);
    expect(within(modal).getByText('Decision')).toBeInTheDocument();
    expect(within(modal).getByText('91%')).toBeInTheDocument();
    expect(within(modal).getByText('12.3%')).toBeInTheDocument();
    expect(within(modal).queryByText('AI check unavailable — no verdict')).not.toBeInTheDocument();
  });

  it('outcome "degraded" renders the normal verdict plus a muted reduced-JSON-mode note', async () => {
    mockPlateCheckResult({
      backend: 'ai',
      outcome: 'degraded',
      is_empty: false,
      ai_confidence: 0.75,
    });
    const modal = await openPlateCheckModal();

    // Appears twice: the top status box and the Decision matrix's Verdict row.
    expect(within(modal).getAllByText('Objects detected on plate')).toHaveLength(2);
    expect(within(modal).getByText('Decision')).toBeInTheDocument();
    expect(within(modal).getByText('Reduced JSON mode — run Test connection to re-probe.')).toBeInTheDocument();
  });

  it('shows ai_confidence (not the fail-open confidence field) for the AI backend', async () => {
    mockPlateCheckResult({
      backend: 'ai',
      outcome: 'ok',
      is_empty: true,
      confidence: 0, // fail-open placeholder — must NOT be what's displayed
      ai_confidence: 0.62,
    });
    const modal = await openPlateCheckModal();

    expect(within(modal).getByText('62%')).toBeInTheDocument();
    expect(within(modal).queryByText('0%')).not.toBeInTheDocument();
  });

  it('omits the difference row instead of showing NaN% when difference_percent is null', async () => {
    mockPlateCheckResult({
      backend: 'opencv',
      outcome: 'ok',
      is_empty: true,
      confidence: 0.5,
      difference_percent: null,
    });
    const modal = await openPlateCheckModal();

    expect(within(modal).getByText('50%')).toBeInTheDocument();
    expect(within(modal).queryByText(/Pixel difference/i)).not.toBeInTheDocument();
    expect(within(modal).queryByText(/NaN/i)).not.toBeInTheDocument();
  });
});
