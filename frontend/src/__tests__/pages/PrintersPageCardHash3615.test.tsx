/**
 * /printers#printer-card-<id> (linked from the queue "To do" list, #3615)
 * scrolls to that card once the printers are loaded; in S mode it opens it.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { waitFor, screen, fireEvent } from '@testing-library/react';
import { Link } from 'react-router-dom';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const basePrinter = {
  ip_address: '192.168.1.100',
  access_code: '12345678',
  enabled: true,
  is_active: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const workshopPrinter = {
  ...basePrinter,
  id: 1,
  name: 'X1 Carbon',
  serial_number: '00M09A350100001',
  model: 'X1C',
  location: null,
};

const officePrinter = {
  ...basePrinter,
  id: 2,
  name: 'P1S Backup',
  serial_number: '00W00A123456789',
  model: 'P1S',
  location: null,
};

const mockStatus = {
  connected: true,
  state: 'IDLE',
  awaiting_plate_clear: false,
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  vt_tray: [],
};

const mockApi = (printers: unknown[]) => {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json(printers)),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(mockStatus)),
    http.get('/api/v1/settings/ui-preferences', () => HttpResponse.json({})),
    http.get('/api/v1/queue/', () => HttpResponse.json([]))
  );
};

/**
 * The shared setup replaces localStorage with bare vi.fn()s that store nothing,
 * so a test written against the real API asserts against a stub that always
 * answers undefined -- and every "it remembered" assertion passes for the wrong
 * reason. Give this file a store that actually holds what it is given.
 */
const store = new Map<string, string>();

const installMemoryLocalStorage = () => {
  store.clear();
  vi.mocked(localStorage.getItem).mockImplementation((key: string) => store.get(key) ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((key: string, value: string) => {
    store.set(key, String(value));
  });
  vi.mocked(localStorage.removeItem).mockImplementation((key: string) => {
    store.delete(key);
  });
};


describe('PrintersPage card hash (#3615)', () => {
  beforeEach(() => {
    installMemoryLocalStorage();
    window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  });

  afterEach(() => {
    window.history.pushState(null, '', '/');
    store.clear();
    vi.mocked(localStorage.getItem).mockReset();
    vi.mocked(localStorage.setItem).mockReset();
    vi.mocked(localStorage.removeItem).mockReset();
  });

  it('scrolls to the linked card once printers are loaded', async () => {
    localStorage.setItem('printerCardSize', '2');
    window.history.pushState(null, '', '/printers#printer-card-2');
    mockApi([workshopPrinter, officePrinter]);
    render(<PrintersPage />);
    await waitFor(() => expect(window.scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'smooth' })));
  });

  it('opens the compact card in S mode', async () => {
    localStorage.setItem('printerCardSize', '1');
    window.history.pushState(null, '', '/printers#printer-card-2');
    mockApi([workshopPrinter, officePrinter]);
    render(<PrintersPage />);
    await waitFor(() => expect(localStorage.getItem('printerCardSize')).toBe('2'));
    await waitFor(() => expect(window.scrollTo).toHaveBeenCalled());
  });

  it('scrolls again when the same link is followed a second time', async () => {
    localStorage.setItem('printerCardSize', '2');
    mockApi([workshopPrinter, officePrinter]);
    render(
      <>
        <Link to="/printers#printer-card-2">go to card</Link>
        <PrintersPage />
      </>,
    );
    await screen.findByText('P1S Backup');
    fireEvent.click(screen.getByText('go to card'));
    await waitFor(() => expect(window.scrollTo).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText('go to card'));
    await waitFor(() => expect(window.scrollTo).toHaveBeenCalledTimes(2));
  });

  it('does nothing without a hash', async () => {
    localStorage.setItem('printerCardSize', '1');
    mockApi([workshopPrinter, officePrinter]);
    render(<PrintersPage />);
    await new Promise((r) => setTimeout(r, 100));
    expect(localStorage.getItem('printerCardSize')).toBe('1');
    expect(window.scrollTo).not.toHaveBeenCalled();
  });
});
