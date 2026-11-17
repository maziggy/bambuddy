/**
 * PrintModal: filament override from the inventory (#3590). A pick made from
 * the "In inventory, not loaded" group is saved with its variant id; an
 * existing one survives an edit; Spoolman mode offers "Loaded now" only.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrintModal } from '../../components/PrintModal';

const INVENTORY = {
  inventory_available: true,
  filaments: [
    {
      type: 'PLA',
      color: '#0000FF',
      tray_info_idx: 'GFA05',
      subtype: 'Matte',
      color_name: 'Blue',
      rgba: '0000FFFF',
      extra_colors: null,
      effect_type: null,
      spool_count: 2,
      remaining_grams: 1400,
    },
  ],
};

type Patched = { filament_overrides?: Array<Record<string, unknown>> | null };
let patched: Patched[];

function mockBackend(opts: { loaded?: unknown[]; inventory?: unknown; reqs?: unknown[] } = {}) {
  patched = [];
  server.use(
    http.get('/api/v1/printers/', () =>
      HttpResponse.json([{ id: 1, name: 'X1C-1', model: 'X1C', ip_address: '10.0.0.1', is_active: true, enabled: true }]),
    ),
    http.get('/api/v1/printers/available-filaments', () =>
      HttpResponse.json(
        opts.loaded ?? [
          { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic', extruder_id: null },
        ],
      ),
    ),
    http.get('/api/v1/printers/inventory-filaments', () => HttpResponse.json(opts.inventory ?? INVENTORY)),
    http.get('/api/v1/archives/:id/plates', () => HttpResponse.json({ is_multi_plate: false, plates: [] })),
    http.get('/api/v1/archives/:id/filament-requirements', () =>
      HttpResponse.json({
        filaments: opts.reqs ?? [
          { slot_id: 1, type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', used_grams: 25, used_meters: 8 },
        ],
      }),
    ),
    http.patch('/api/v1/queue/:id', async ({ request }) => {
      patched.push((await request.json()) as Patched);
      return HttpResponse.json({ id: 1, status: 'pending' });
    }),
  );
}

const item = (overrides: unknown[] | null) =>
  ({
    id: 9,
    printer_id: null,
    target_model: 'X1C',
    status: 'pending',
    archive_id: 1,
    filament_overrides: overrides,
  }) as never;

const renderEdit = (overrides: unknown[] | null = null) =>
  render(
    <PrintModal mode="edit-queue-item" archiveId={1} archiveName="Job" queueItem={item(overrides)} onClose={() => {}} />,
  );

const submit = (user: ReturnType<typeof userEvent.setup>) =>
  user.click(document.querySelector('button[type="submit"]') as HTMLElement);

describe('PrintModal inventory override (#3590)', () => {
  beforeEach(() => mockBackend());

  it('saves an inventory pick with its variant and force colour', async () => {
    const user = userEvent.setup();
    renderEdit();
    const trigger = await screen.findByRole('combobox', { name: /filament override for/i });
    fireEvent.click(trigger);
    const option = await waitFor(() => {
      const el = within(screen.getByRole('listbox'))
        .getAllByRole('option')
        .find((o) => o.getAttribute('data-value') === 'PLA|#0000FF|GFA05');
      if (!el) throw new Error('inventory row missing');
      return el;
    });
    fireEvent.click(option);
    await submit(user);

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0].filament_overrides).toEqual([
      expect.objectContaining({ slot_id: 1, type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05', force_color_match: true }),
    ]);
  });

  it('keeps the stored variant of an inventory override when the item is saved unchanged', async () => {
    const user = userEvent.setup();
    renderEdit([{ slot_id: 1, type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05', force_color_match: true }]);
    const trigger = await screen.findByRole('combobox', { name: /filament override for/i });
    await waitFor(() => expect(trigger.textContent).toContain('Matte PLA (Blue)'));
    await submit(user);

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0].filament_overrides).toEqual([
      expect.objectContaining({ slot_id: 1, color: '#0000FF', tray_info_idx: 'GFA05', force_color_match: true }),
    ]);
  });

  it('an explicit variant-less inventory pick does not get the stored variant back', async () => {
    mockBackend({
      inventory: {
        inventory_available: true,
        filaments: [{ ...INVENTORY.filaments[0], tray_info_idx: '' }],
      },
    });
    const user = userEvent.setup();
    renderEdit([{ slot_id: 1, type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05', force_color_match: true }]);
    const trigger = await screen.findByRole('combobox', { name: /filament override for/i });
    fireEvent.click(trigger);
    const option = await waitFor(() => {
      const el = within(screen.getByRole('listbox'))
        .getAllByRole('option')
        .find((o) => o.getAttribute('data-value') === 'PLA|#0000FF|');
      if (!el) throw new Error('inventory row missing');
      return el;
    });
    fireEvent.click(option);
    await submit(user);

    await waitFor(() => expect(patched).toHaveLength(1));
    const [entry] = patched[0].filament_overrides ?? [];
    expect(entry).toMatchObject({ slot_id: 1, color: '#0000FF' });
    expect(entry).not.toHaveProperty('tray_info_idx');
    expect(entry).not.toHaveProperty('fromInventory');
  });

  it('shows a stored override even when its inventory entry no longer exists', async () => {
    mockBackend({ inventory: { inventory_available: true, filaments: [] } });
    renderEdit([{ slot_id: 1, type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05', force_color_match: true }]);
    const trigger = await screen.findByRole('combobox', { name: /filament override for/i });
    expect(trigger.textContent).not.toMatch(/original filament/i);
  });

  it('shows the panel from the inventory alone when nothing is loaded', async () => {
    mockBackend({ loaded: [] });
    renderEdit();
    expect(await screen.findByText('Filament Override')).toBeInTheDocument();
  });

  it('offers no inventory group in Spoolman mode', async () => {
    mockBackend({ inventory: { inventory_available: false, filaments: [] } });
    renderEdit();
    const trigger = await screen.findByRole('combobox', { name: /filament override for/i });
    await new Promise((r) => setTimeout(r, 100));
    fireEvent.click(trigger);
    const values = within(screen.getByRole('listbox'))
      .getAllByRole('option')
      .map((o) => o.getAttribute('data-value'));
    expect(values).not.toContain('PLA|#0000FF|GFA05');
    expect(screen.queryAllByRole('group')).toHaveLength(0);
  });

  const pickInventory = async (index = 0) => {
    const trigger = (await screen.findAllByRole('combobox', { name: /filament override for/i }))[index];
    fireEvent.click(trigger);
    fireEvent.click(
      within(screen.getByRole('listbox'))
        .getAllByRole('option')
        .find((o) => o.getAttribute('data-value') === 'PLA|#0000FF|GFA05')!,
    );
  };
  const forceBox = (index = 0) => screen.getAllByRole('checkbox', { name: /force color match/i })[index] as HTMLInputElement;

  it('unticks an automatic force colour again on reset', async () => {
    renderEdit();
    await pickInventory();
    await waitFor(() => expect(forceBox().checked).toBe(true));
    fireEvent.click(screen.getByTitle('Reset to original'));
    await waitFor(() => expect(forceBox().checked).toBe(false));
  });

  it('unticks an automatic force colour on a switch to a loaded row', async () => {
    renderEdit();
    await pickInventory();
    await waitFor(() => expect(forceBox().checked).toBe(true));
    fireEvent.click(screen.getByRole('combobox', { name: /filament override for/i }));
    fireEvent.click(
      within(screen.getByRole('listbox'))
        .getAllByRole('option')
        .find((o) => o.getAttribute('data-value') === 'PLA|#FF0000')!,
    );
    await waitFor(() => expect(forceBox().checked).toBe(false));
  });

  it('keeps the tick when the user changed the box after the automatic one', async () => {
    renderEdit();
    await pickInventory();
    await waitFor(() => expect(forceBox().checked).toBe(true));
    fireEvent.click(forceBox()); // off
    fireEvent.click(forceBox()); // on again, now by hand
    fireEvent.click(screen.getByTitle('Reset to original'));
    await waitFor(() => expect(forceBox().checked).toBe(true));
  });

  it('keeps a manual untick after an inventory pick', async () => {
    renderEdit();
    await pickInventory();
    await waitFor(() => expect(forceBox().checked).toBe(true));
    fireEvent.click(forceBox());
    expect(forceBox().checked).toBe(false);
  });

  it('warns against the total of all slots that picked the same entry', async () => {
    // Each slot needs 25 g x 1; the entry holds 40 g: fine for one, short for two.
    mockBackend({
      inventory: { inventory_available: true, filaments: [{ ...INVENTORY.filaments[0], remaining_grams: 40, spool_count: 1 }] },
      reqs: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', used_grams: 25, used_meters: 8 },
        { slot_id: 2, type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', used_grams: 25, used_meters: 8 },
      ],
    });
    renderEdit();
    await pickInventory(0);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await pickInventory(1);
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0));
    expect(screen.getAllByRole('alert')[0].textContent).toContain('50 g');
  });
});
