/**
 * PrintModal: filament override from the inventory (#3590). A pick made from
 * the "In inventory, not loaded" group is saved with its variant id; an
 * existing one survives an edit; Spoolman mode offers "Loaded now" only.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrintModal } from '../../components/PrintModal';
import { setAuthToken } from '../../api/client';

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
// The install-wide "Show inventory colours" setting as the mocked backend stores it (#3590).
let settingsStore: { filament_override_inventory: boolean; spoolman_enabled?: boolean };
let settingsWrites: Array<Record<string, unknown>>;
let inventoryRequests: number;

function mockBackend(
  opts: { loaded?: unknown[]; inventory?: unknown; reqs?: unknown[]; flag?: boolean; spoolman?: boolean } = {},
) {
  patched = [];
  settingsWrites = [];
  inventoryRequests = 0;
  settingsStore = { filament_override_inventory: opts.flag ?? true, spoolman_enabled: opts.spoolman ?? false };
  const save = async ({ request }: { request: Request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    settingsWrites.push(body);
    settingsStore = { ...settingsStore, ...body } as typeof settingsStore;
    return HttpResponse.json(settingsStore);
  };
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json(settingsStore)),
    http.put('/api/v1/settings/', save),
    http.patch('/api/v1/settings/', save),
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
    http.get('/api/v1/printers/inventory-filaments', () => {
      inventoryRequests += 1;
      return HttpResponse.json(opts.inventory ?? INVENTORY);
    }),
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

  // An entry whose own name is not what the hex is called in the catalogue.
  const EARTH_BROWN = { ...INVENTORY.filaments[0], color: '#7D6556', rgba: '7D6556FF', color_name: 'Earth Brown' };
  const STORED_EARTH_BROWN = {
    slot_id: 1,
    type: 'PLA',
    color: '#7D6556',
    color_name: 'Earth Brown',
    tray_info_idx: 'GFA05',
    force_color_match: true,
  };

  it('saves an inventory pick under the colour name of its inventory entry', async () => {
    mockBackend({ inventory: { inventory_available: true, filaments: [EARTH_BROWN] } });
    const user = userEvent.setup();
    renderEdit();
    fireEvent.click(await screen.findByRole('combobox', { name: /filament override for/i }));
    const option = await waitFor(() => {
      const el = within(screen.getByRole('listbox'))
        .getAllByRole('option')
        .find((o) => o.getAttribute('data-value') === 'PLA|#7D6556|GFA05');
      if (!el) throw new Error('inventory row missing');
      return el;
    });
    expect(option.textContent).toContain('Matte PLA (Earth Brown)');
    fireEvent.click(option);
    await submit(user);

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0].filament_overrides).toEqual([expect.objectContaining(STORED_EARTH_BROWN)]);
    expect(patched[0].filament_overrides?.[0]).not.toHaveProperty('colorName');
  });

  it.each([
    ['its inventory entry still there', [EARTH_BROWN]],
    ['its inventory entry gone', []],
  ])('keeps the stored colour name when the item is saved unchanged, with %s', async (_label, filaments) => {
    mockBackend({ inventory: { inventory_available: true, filaments } });
    const user = userEvent.setup();
    renderEdit([STORED_EARTH_BROWN]);
    const trigger = await screen.findByRole('combobox', { name: /filament override for/i });
    await waitFor(() => expect(trigger.textContent).toContain('PLA (Earth Brown)'));
    await submit(user);

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0].filament_overrides).toEqual([expect.objectContaining(STORED_EARTH_BROWN)]);
  });

  it('a switch to a loaded row names the colour from its hex again', async () => {
    mockBackend({ inventory: { inventory_available: true, filaments: [EARTH_BROWN] } });
    const user = userEvent.setup();
    renderEdit([STORED_EARTH_BROWN]);
    const trigger = await screen.findByRole('combobox', { name: /filament override for/i });
    await waitFor(() => expect(trigger.textContent).toContain('Earth Brown'));
    fireEvent.click(trigger);
    fireEvent.click(
      within(screen.getByRole('listbox'))
        .getAllByRole('option')
        .find((o) => o.getAttribute('data-value') === 'PLA|#FF0000')!,
    );
    await submit(user);

    await waitFor(() => expect(patched).toHaveLength(1));
    const [entry] = patched[0].filament_overrides ?? [];
    expect(entry).toMatchObject({ slot_id: 1, color: '#FF0000' });
    expect(entry.color_name).not.toBe('Earth Brown');
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
    // The inventory row only exists once the setting and the inventory have loaded.
    fireEvent.click(trigger);
    const row = await waitFor(() => {
      const el = within(screen.getByRole('listbox'))
        .getAllByRole('option')
        .find((o) => o.getAttribute('data-value') === 'PLA|#0000FF|GFA05');
      if (!el) throw new Error('inventory row missing');
      return el;
    });
    fireEvent.click(row);
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

describe('PrintModal "Show inventory colours" setting (#3590)', () => {
  afterEach(() => setAuthToken(null));

  const optionValues = () =>
    within(screen.getByRole('listbox'))
      .getAllByRole('option')
      .map((o) => o.getAttribute('data-value'));
  const checkbox = () => screen.findByRole('checkbox', { name: /show inventory colours/i });
  const openPicker = async () => {
    fireEvent.click(await screen.findByRole('combobox', { name: /filament override for/i }));
  };

  it('off: no inventory request, loaded rows only, no headers, hints or warnings', async () => {
    mockBackend({ flag: false });
    renderEdit();
    await openPicker();
    expect(optionValues().filter(Boolean)).toEqual(['PLA|#FF0000']);
    expect(screen.queryAllByRole('group')).toHaveLength(0);
    expect(screen.queryByText(/spools/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(inventoryRequests).toBe(0);
    expect(await checkbox()).not.toBeChecked();
  });

  it('on: the inventory is requested and offered in its own group', async () => {
    mockBackend({ flag: true });
    renderEdit();
    await openPicker();
    await waitFor(() => expect(optionValues()).toContain('PLA|#0000FF|GFA05'));
    expect(screen.getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toEqual([
      'Loaded now',
      'In inventory, not loaded',
    ]);
    expect(inventoryRequests).toBeGreaterThan(0);
    expect(await checkbox()).toBeChecked();
  });

  it('toggling saves the setting and the saved value is there when the dialog is reopened', async () => {
    mockBackend({ flag: false });
    const user = userEvent.setup();
    const first = renderEdit();
    await user.click(await checkbox());
    await waitFor(() => expect(settingsWrites).toEqual([{ filament_override_inventory: true }]));
    // The list reacts at once.
    await openPicker();
    await waitFor(() => expect(optionValues()).toContain('PLA|#0000FF|GFA05'));
    first.unmount();

    renderEdit();
    expect(await checkbox()).toBeChecked();
    await openPicker();
    await waitFor(() => expect(optionValues()).toContain('PLA|#0000FF|GFA05'));
  });

  it('a user without settings:update sees a disabled checkbox and sends nothing', async () => {
    mockBackend({ flag: false });
    setAuthToken('test-token', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () =>
        HttpResponse.json({
          id: 2,
          username: 'operator',
          is_admin: false,
          permissions: ['settings:read', 'queue:read', 'queue:create', 'queue:update', 'printers:read', 'inventory:read'],
        }),
      ),
    );
    const user = userEvent.setup();
    renderEdit();
    const box = await checkbox();
    await waitFor(() => expect(box).toBeDisabled());
    expect(box.closest('label')).toHaveAttribute('title', 'Only users who may change settings can switch this.');
    await user.click(box);
    expect(settingsWrites).toEqual([]);
  });

  it('a user without inventory:read gets no inventory request, group or checkbox', async () => {
    // The route answers 403 without inventory:read, so the dialog must not ask.
    mockBackend({ flag: true });
    setAuthToken('test-token', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () =>
        HttpResponse.json({
          id: 2,
          username: 'operator',
          is_admin: false,
          permissions: ['settings:read', 'settings:update', 'queue:read', 'queue:create', 'queue:update', 'printers:read'],
        }),
      ),
      http.get('/api/v1/printers/inventory-filaments', () => {
        inventoryRequests += 1;
        return HttpResponse.json({ detail: 'Missing required permissions' }, { status: 403 });
      }),
    );
    renderEdit([{ slot_id: 1, type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05', force_color_match: true }]);
    const trigger = await screen.findByRole('combobox', { name: /filament override for/i });
    // The stored override is still shown, as a saved row.
    await waitFor(() => expect(trigger.textContent).not.toMatch(/original filament/i));
    fireEvent.click(trigger);
    expect(optionValues().filter(Boolean)).toEqual(['PLA|#FF0000', 'PLA|#0000FF|GFA05']);
    expect(
      within(screen.getByRole('listbox'))
        .getAllByRole('group')
        .map((g) => g.getAttribute('aria-label')),
    ).toEqual(['Saved with this job']);
    expect(screen.queryByText(/spools/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /show inventory colours/i })).not.toBeInTheDocument();
    expect(inventoryRequests).toBe(0);
  });

  it('off: a stored inventory override stays as "Saved with this job" and in the payload', async () => {
    mockBackend({ flag: false });
    const user = userEvent.setup();
    renderEdit([{ slot_id: 1, type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05', force_color_match: true }]);
    const trigger = await screen.findByRole('combobox', { name: /filament override for/i });
    await waitFor(() => expect(trigger.textContent).not.toMatch(/original filament/i));
    fireEvent.click(trigger);
    const selected = within(screen.getByRole('listbox'))
      .getAllByRole('option')
      .find((o) => o.getAttribute('data-value') === 'PLA|#0000FF|GFA05');
    expect(selected).toBeDefined();
    expect(selected).toHaveAttribute('aria-selected', 'true');
    expect(
      within(screen.getByRole('listbox'))
        .getAllByRole('group')
        .map((g) => g.getAttribute('aria-label')),
    ).toEqual(['Saved with this job']);
    expect(inventoryRequests).toBe(0);
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' });
    await submit(user);

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0].filament_overrides).toEqual([
      expect.objectContaining({ slot_id: 1, color: '#0000FF', tray_info_idx: 'GFA05', force_color_match: true }),
    ]);
  });

  it('Spoolman mode hides the checkbox and requests no inventory', async () => {
    mockBackend({ flag: true, spoolman: true });
    renderEdit();
    await screen.findByRole('combobox', { name: /filament override for/i });
    expect(screen.queryByRole('checkbox', { name: /show inventory colours/i })).not.toBeInTheDocument();
    expect(inventoryRequests).toBe(0);
  });
});
