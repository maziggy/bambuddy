/**
 * Settings → Build Plates (#1306): opt-in toggle and the "which plates do you
 * own" checklist.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { BuildPlateSettings } from '../../components/BuildPlateSettings';

const plates = [
  {
    id: 1, builtin_key: 'smooth_pei', is_builtin: true, name: 'Smooth PEI Plate', base_type: 'smooth_pei',
    base_type_label: 'Smooth PEI / High Temp Plate', pattern: null, image: '/img/bed/bed_pei_cool.png',
    notes: null, enabled: true, sort_order: 50, installed_on: [], required_by_pending: 0,
  },
  {
    id: 2, builtin_key: '3d_effect_carbon_fiber', is_builtin: true, name: '3D Effect – Carbon Fiber',
    base_type: 'smooth_pei', base_type_label: 'Smooth PEI / High Temp Plate', pattern: 'Carbon Fiber',
    image: '/img/plates/3d_effect_carbon_fiber.jpg', notes: null, enabled: false, sort_order: 100, installed_on: [], required_by_pending: 0,
  },
  {
    id: 3, builtin_key: null, is_builtin: false, name: 'Gold PEI', base_type: 'smooth_pei',
    base_type_label: 'Smooth PEI / High Temp Plate', pattern: 'Gold', image: null,
    notes: null, enabled: true, sort_order: 200, installed_on: [], required_by_pending: 0,
  },
];

describe('BuildPlateSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/build-plates', () => HttpResponse.json(plates)),
      http.get('/api/v1/build-plates/bed-types', () =>
        HttpResponse.json([{ key: 'smooth_pei', label: 'Smooth PEI / High Temp Plate' }]),
      ),
    );
  });

  it('shows only the toggle while tracking is off', () => {
    render(<BuildPlateSettings enabled={false} onToggle={vi.fn()} />);
    expect(screen.getByText('Enable build plate tracking')).toBeInTheDocument();
    expect(screen.queryByText('Which plates do you own?')).not.toBeInTheDocument();
  });

  it('reports the toggle to the settings page', async () => {
    const onToggle = vi.fn();
    render(<BuildPlateSettings enabled={false} onToggle={onToggle} />);
    await userEvent.click(screen.getByRole('checkbox', { name: 'Enable build plate tracking' }));
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it('lists plates grouped by type with the 3D Effect preset unticked', async () => {
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    const carbon = await screen.findByText('3D Effect – Carbon Fiber');
    const card = carbon.closest('label')!;
    expect(card.querySelector('input[type="checkbox"]')).not.toBeChecked();
    expect(screen.getByText('Smooth PEI / High Temp Plate')).toBeInTheDocument();
  });

  it('ticking a plate saves it straight away', async () => {
    const patched = vi.fn();
    server.use(
      http.patch('/api/v1/build-plates/2', async ({ request }) => {
        patched(await request.json());
        return HttpResponse.json({ ...plates[1], enabled: true });
      }),
    );
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    const card = (await screen.findByText('3D Effect – Carbon Fiber')).closest('label')!;
    await userEvent.click(card.querySelector('input[type="checkbox"]')!);
    await waitFor(() => expect(patched).toHaveBeenCalledWith({ enabled: true }));
  });

  it('only custom plates can be deleted', async () => {
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    await screen.findByText('Gold PEI');
    expect(screen.getAllByRole('button', { name: 'Delete' })).toHaveLength(1);
  });

  it('adds a custom plate', async () => {
    const created = vi.fn();
    server.use(
      http.post('/api/v1/build-plates', async ({ request }) => {
        const body = await request.json();
        created(body);
        return HttpResponse.json({ ...plates[2], id: 9 }, { status: 201 });
      }),
    );
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    await userEvent.click(await screen.findByText('Add custom plate'));
    await userEvent.type(screen.getByPlaceholderText('e.g. Gold PEI'), 'Galaxy PEY');
    await userEvent.click(screen.getByRole('button', { name: 'Add plate' }));
    await waitFor(() =>
      expect(created).toHaveBeenCalledWith(expect.objectContaining({ name: 'Galaxy PEY', base_type: 'smooth_pei' })),
    );
  });

  it('asks before unticking a plate that queued jobs are waiting for', async () => {
    const patched = vi.fn();
    server.use(
      http.get('/api/v1/build-plates', () =>
        HttpResponse.json([{ ...plates[0], installed_on: [4], required_by_pending: 2 }]),
      ),
      http.patch('/api/v1/build-plates/1', async ({ request }) => {
        patched(await request.json());
        return HttpResponse.json({ ...plates[0], enabled: false });
      }),
    );
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    const card = (await screen.findByText('Smooth PEI Plate')).closest('label')!;
    await userEvent.click(card.querySelector('input[type="checkbox"]')!);

    expect(await screen.findByText('Plate in use')).toBeInTheDocument();
    expect(screen.getByText(/2 queued job/)).toBeInTheDocument();
    expect(patched).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Untick anyway' }));
    await waitFor(() => expect(patched).toHaveBeenCalledWith({ enabled: false }));
  });

  it('says recorded plates are cleared when tracking is switched back on', async () => {
    const onToggle = vi.fn();
    const { rerender } = render(<BuildPlateSettings enabled={false} onToggle={onToggle} />);
    await userEvent.click(screen.getByRole('checkbox', { name: 'Enable build plate tracking' }));
    expect(onToggle).toHaveBeenCalledWith(true);

    rerender(<BuildPlateSettings enabled onToggle={onToggle} />);
    expect(screen.getByText(/will be cleared when you save/)).toBeInTheDocument();
  });

  it('does not show the note when tracking was already on', async () => {
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    await screen.findByText('Smooth PEI Plate');
    expect(screen.queryByText(/will be cleared when you save/)).not.toBeInTheDocument();
  });
});

