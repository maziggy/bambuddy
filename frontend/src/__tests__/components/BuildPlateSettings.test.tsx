/**
 * Settings → Build Plates (#1306): opt-in toggle, the plate types you own and
 * the patterns under them.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { BuildPlateSettings } from '../../components/BuildPlateSettings';
import type { BuildPlateConfig, BuildPlateConfigInput } from '../../api/client';

const type = (key: string, label: string, owned: boolean, over = {}) => ({
  key, label, owned, installed_on: [] as number[], waiting_jobs: 0, ...over,
});

function makeConfig(over: Partial<BuildPlateConfig> = {}): BuildPlateConfig {
  return {
    types: [
      type('cool_plate', 'Cool Plate', false),
      type('smooth_pei', 'Smooth PEI Plate', true),
      type('textured_pei', 'Textured PEI Plate', true),
    ],
    patterns: [
      { base_type: 'smooth_pei', name: 'Carbon Fiber', label: 'Smooth PEI Plate · Carbon Fiber', installed_on: [], waiting_jobs: 0 },
    ],
    pattern_suggestions: ['Carbon Fiber', 'Starry'],
    ...over,
  };
}

function serve(config: BuildPlateConfig) {
  const saved = vi.fn();
  server.use(
    http.get('/api/v1/build-plates', () => HttpResponse.json(config)),
    http.put('/api/v1/build-plates', async ({ request }) => {
      const body = (await request.json()) as BuildPlateConfigInput;
      saved(body);
      return HttpResponse.json(config);
    }),
  );
  return saved;
}

const typeCheckbox = async (label: string) =>
  (await screen.findByText(label)).closest('label')!.querySelector('input[type="checkbox"]') as HTMLInputElement;

describe('BuildPlateSettings', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows only the toggle while tracking is off', () => {
    serve(makeConfig());
    render(<BuildPlateSettings enabled={false} onToggle={vi.fn()} />);
    expect(screen.getByText('Enable build plate tracking')).toBeInTheDocument();
    expect(screen.queryByText('Which plates do you own?')).not.toBeInTheDocument();
  });

  it('reports the toggle to the settings page', async () => {
    serve(makeConfig());
    const onToggle = vi.fn();
    render(<BuildPlateSettings enabled={false} onToggle={onToggle} />);
    await userEvent.click(screen.getByRole('checkbox', { name: 'Enable build plate tracking' }));
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it('lists every plate type once, with patterns under the ticked ones', async () => {
    serve(makeConfig());
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    expect(await typeCheckbox('Cool Plate')).not.toBeChecked();
    expect(await typeCheckbox('Smooth PEI Plate')).toBeChecked();
    expect(screen.getByText('Carbon Fiber')).toBeInTheDocument();
  });

  it('ticking a type saves straight away', async () => {
    const saved = serve(makeConfig());
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    await userEvent.click(await typeCheckbox('Cool Plate'));
    await waitFor(() =>
      expect(saved).toHaveBeenCalledWith({
        types: ['smooth_pei', 'textured_pei', 'cool_plate'],
        patterns: [{ base_type: 'smooth_pei', name: 'Carbon Fiber' }],
      }),
    );
  });

  it('adds a pattern under a type', async () => {
    const saved = serve(makeConfig({ patterns: [] }));
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    await screen.findByText('Smooth PEI Plate');
    // One "Add pattern" per ticked type; Smooth PEI is the first ticked one.
    await userEvent.click(screen.getAllByRole('button', { name: 'Add pattern' })[0]);
    await userEvent.type(screen.getByRole('combobox', { name: 'Add pattern' }), 'Starry');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(saved).toHaveBeenCalledWith({
        types: ['smooth_pei', 'textured_pei'],
        patterns: [{ base_type: 'smooth_pei', name: 'Starry' }],
      }),
    );
  });

  it('removes an unused pattern without asking', async () => {
    const saved = serve(makeConfig());
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Remove Smooth PEI Plate · Carbon Fiber' }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith({ types: ['smooth_pei', 'textured_pei'], patterns: [] }));
  });

  it('asks before removing a pattern in use', async () => {
    const config = makeConfig();
    config.patterns[0] = { ...config.patterns[0], installed_on: [4], waiting_jobs: 1 };
    const saved = serve(config);
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Remove Smooth PEI Plate · Carbon Fiber' }));

    expect(await screen.findByText('Remove pattern')).toBeInTheDocument();
    expect(saved).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith({ types: ['smooth_pei', 'textured_pei'], patterns: [] }));
  });

  it('asks before unticking a type that printers or queued jobs use', async () => {
    const config = makeConfig();
    config.types[2] = type('textured_pei', 'Textured PEI Plate', true, { installed_on: [4], waiting_jobs: 2 });
    const saved = serve(config);
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    await userEvent.click(await typeCheckbox('Textured PEI Plate'));

    expect(await screen.findByText('Plate in use')).toBeInTheDocument();
    expect(screen.getByText(/2 queued job/)).toBeInTheDocument();
    expect(saved).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Untick anyway' }));
    await waitFor(() =>
      expect(saved).toHaveBeenCalledWith({
        types: ['smooth_pei'],
        patterns: [{ base_type: 'smooth_pei', name: 'Carbon Fiber' }],
      }),
    );
  });

  it('says recorded plates are cleared when tracking is switched back on', async () => {
    serve(makeConfig());
    const onToggle = vi.fn();
    const { rerender } = render(<BuildPlateSettings enabled={false} onToggle={onToggle} />);
    await userEvent.click(screen.getByRole('checkbox', { name: 'Enable build plate tracking' }));
    expect(onToggle).toHaveBeenCalledWith(true);

    rerender(<BuildPlateSettings enabled onToggle={onToggle} />);
    expect(screen.getByText(/will be cleared when you save/)).toBeInTheDocument();
  });

  it('does not show the note when tracking was already on', async () => {
    serve(makeConfig());
    render(<BuildPlateSettings enabled onToggle={vi.fn()} />);
    await screen.findByText('Smooth PEI Plate');
    expect(screen.queryByText(/will be cleared when you save/)).not.toBeInTheDocument();
  });
});
