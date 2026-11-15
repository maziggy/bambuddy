/**
 * Printer card plate swap (#1306).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { InstalledPlateSelector } from '../../components/InstalledPlateSelector';
import type { BuildPlateConfig, Printer } from '../../api/client';

const printer = { id: 4, name: 'P1S-1', installed_plate_type: null, installed_plate_pattern: null } as unknown as Printer;

const type = (key: string, label: string, owned: boolean) => ({ key, label, owned, installed_on: [], waiting_jobs: 0 });

const config: BuildPlateConfig = {
  types: [
    type('cool_plate', 'Cool Plate', false),
    type('smooth_pei', 'Smooth PEI Plate', true),
    type('textured_pei', 'Textured PEI Plate', true),
  ],
  patterns: [
    { base_type: 'smooth_pei', name: 'Carbon Fiber', label: 'Smooth PEI Plate · Carbon Fiber', installed_on: [], waiting_jobs: 0 },
  ],
  pattern_suggestions: [],
};

function mockSettings(tracking: boolean) {
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json({ build_plate_tracking_enabled: tracking })),
    http.get('/api/v1/build-plates', () => HttpResponse.json(config)),
  );
}

function captureSwap() {
  const swapped = vi.fn();
  server.use(
    http.put('/api/v1/printers/4/installed-plate', async ({ request }) => {
      swapped(await request.json());
      return HttpResponse.json(printer);
    }),
  );
  return swapped;
}

describe('InstalledPlateSelector', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders nothing while tracking is off', async () => {
    mockSettings(false);
    const { container } = render(<InstalledPlateSelector printer={printer} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(container.querySelector('select')).toBeNull();
  });

  it('lists the owned plates once each, with patterns after their type', async () => {
    mockSettings(true);
    render(<InstalledPlateSelector printer={printer} />);
    await screen.findByRole('option', { name: 'Textured PEI Plate' });
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Plate not tracked',
      'Smooth PEI Plate',
      'Smooth PEI Plate · Carbon Fiber',
      'Textured PEI Plate',
    ]);
    // No group headers repeating the plate names.
    expect(document.querySelector('optgroup')).toBeNull();
  });

  it('records the type and pattern picked', async () => {
    mockSettings(true);
    const swapped = captureSwap();
    render(<InstalledPlateSelector printer={printer} />);
    const select = await screen.findByRole('combobox', { name: 'Installed build plate' });
    await screen.findByRole('option', { name: 'Smooth PEI Plate · Carbon Fiber' });

    await userEvent.selectOptions(select, 'Smooth PEI Plate · Carbon Fiber');
    await waitFor(() => expect(swapped).toHaveBeenCalledWith({ plate_type: 'smooth_pei', pattern: 'Carbon Fiber' }));

    await userEvent.selectOptions(select, 'Plate not tracked');
    await waitFor(() => expect(swapped).toHaveBeenLastCalledWith({ plate_type: null, pattern: null }));
  });

  it('shows the fitted plate even when its type has been unticked', async () => {
    mockSettings(true);
    render(<InstalledPlateSelector printer={{ ...printer, installed_plate_type: 'cool_plate' }} />);
    const select = (await screen.findByRole('combobox', { name: 'Installed build plate' })) as HTMLSelectElement;
    await screen.findByRole('option', { name: 'Cool Plate' });
    expect(select.selectedOptions[0].textContent).toBe('Cool Plate');
  });

  it('asks before recording a plate swap while the printer is printing', async () => {
    mockSettings(true);
    const swapped = captureSwap();
    server.use(http.get('/api/v1/printers/4/status', () => HttpResponse.json({ connected: true, state: 'RUNNING' })));
    render(<InstalledPlateSelector printer={printer} />);
    const select = await screen.findByRole('combobox', { name: 'Installed build plate' });
    await screen.findByRole('option', { name: 'Textured PEI Plate' });
    // Let the status query land before choosing.
    await new Promise((r) => setTimeout(r, 50));

    await userEvent.selectOptions(select, 'Textured PEI Plate');
    expect(await screen.findByText('A print is running')).toBeInTheDocument();
    expect(swapped).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Change plate' }));
    await waitFor(() => expect(swapped).toHaveBeenCalledWith({ plate_type: 'textured_pei', pattern: null }));
  });
});
