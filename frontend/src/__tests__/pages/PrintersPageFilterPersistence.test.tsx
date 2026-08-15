/**
 * The Printers page's status and location filters survive navigation (#2833).
 *
 * Every other preference on that page already persisted -- sort order, card
 * size, view mode, collapsed sections, hide-disconnected -- while these two
 * were plain useState, so picking a location and coming back showed everything
 * again.
 *
 * Persisting a filter needs a way out, though: the location dropdown is only
 * rendered while some printer has a location, so a saved value that no longer
 * matches anything would hide every printer *and* the control that undoes it.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
  location: 'Workshop',
};

const officePrinter = {
  ...basePrinter,
  id: 2,
  name: 'P1S Backup',
  serial_number: '00W00A123456789',
  model: 'P1S',
  location: 'Office',
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

const pickFromDropdown = async (current: RegExp, option: RegExp) => {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: current }));
  await user.click(await screen.findByRole('button', { name: option }));
};

describe('PrintersPage filter persistence', () => {
  beforeEach(() => {
    installMemoryLocalStorage();
  });

  afterEach(() => {
    store.clear();
    vi.mocked(localStorage.getItem).mockReset();
    vi.mocked(localStorage.setItem).mockReset();
    vi.mocked(localStorage.removeItem).mockReset();
  });

  describe('location', () => {
    it('remembers the chosen location', async () => {
      mockApi([workshopPrinter, officePrinter]);
      render(<PrintersPage />);
      await screen.findByText('X1 Carbon');

      await pickFromDropdown(/All Locations/i, /^Workshop$/);

      await waitFor(() => {
        expect(localStorage.getItem('printerLocationFilter')).toBe('Workshop');
      });
    });

    it('applies the remembered location on a fresh mount', async () => {
      localStorage.setItem('printerLocationFilter', 'Workshop');
      mockApi([workshopPrinter, officePrinter]);

      render(<PrintersPage />);

      await screen.findByText('X1 Carbon');
      expect(screen.queryByText('P1S Backup')).not.toBeInTheDocument();
    });

    it('survives the printers query resolving', async () => {
      // The list is undefined while the query is in flight, so the available
      // locations start out empty. Reacting to that would drop the saved
      // filter on every single page load.
      localStorage.setItem('printerLocationFilter', 'Workshop');
      mockApi([workshopPrinter, officePrinter]);

      render(<PrintersPage />);
      await screen.findByText('X1 Carbon');

      await waitFor(() => {
        expect(localStorage.getItem('printerLocationFilter')).toBe('Workshop');
      });
    });

    it('falls back to all when the saved location no longer exists', async () => {
      // Renamed, cleared, or its last printer deleted. The dropdown is gone
      // with it, so leaving the filter set would empty the page for good.
      localStorage.setItem('printerLocationFilter', 'Workshop');
      mockApi([{ ...officePrinter, id: 3, name: 'Only Printer' }]);

      render(<PrintersPage />);

      await screen.findByText('Only Printer');
      await waitFor(() => {
        expect(localStorage.getItem('printerLocationFilter')).toBe('all');
      });
    });

    it('falls back to all when no printer has a location at all', async () => {
      localStorage.setItem('printerLocationFilter', 'Workshop');
      mockApi([{ ...workshopPrinter, location: null }]);

      render(<PrintersPage />);

      await screen.findByText('X1 Carbon');
      await waitFor(() => {
        expect(localStorage.getItem('printerLocationFilter')).toBe('all');
      });
    });
  });

  describe('status', () => {
    it('remembers the chosen status', async () => {
      mockApi([workshopPrinter, officePrinter]);
      render(<PrintersPage />);
      await screen.findByText('X1 Carbon');

      await pickFromDropdown(/All Statuses/i, /^Idle$/i);

      await waitFor(() => {
        expect(localStorage.getItem('printerStatusFilter')).toBe('idle');
      });
    });

    it('applies the remembered status on a fresh mount', async () => {
      localStorage.setItem('printerStatusFilter', 'printing');
      mockApi([workshopPrinter]);

      render(<PrintersPage />);

      // Both printers report IDLE, so a printing filter matches none of them.
      await waitFor(() => {
        expect(screen.queryByText('X1 Carbon')).not.toBeInTheDocument();
      });
    });

    it('ignores a saved status the dropdown does not offer', async () => {
      // A value from an older or newer build would otherwise match nothing,
      // with nothing on screen to explain it.
      localStorage.setItem('printerStatusFilter', 'teleporting');
      mockApi([workshopPrinter]);

      render(<PrintersPage />);

      await screen.findByText('X1 Carbon');
    });
  });

  describe('search is deliberately not persisted', () => {
    it('does not save what was typed', async () => {
      mockApi([workshopPrinter, officePrinter]);
      render(<PrintersPage />);
      await screen.findByText('X1 Carbon');

      const user = userEvent.setup();
      await user.type(screen.getByPlaceholderText(/search/i), 'Carbon');

      await waitFor(() => {
        expect(screen.queryByText('P1S Backup')).not.toBeInTheDocument();
      });
      expect([...store.keys()].some(key => key.toLowerCase().includes('search'))).toBe(false);
    });
  });
});
