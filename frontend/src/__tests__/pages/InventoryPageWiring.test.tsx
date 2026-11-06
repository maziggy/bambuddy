/**
 * Wiring tests for InventoryPage's inventory-changing actions in BOTH modes:
 * per-spool delete / archive / restore / reset-counter, the bulk actions on
 * a 3-spool selection, and "sync to scale weight". Spoolman mode must route
 * to the *Spoolman* APIs and built-in mode to the local ones, every
 * destructive action must wait for confirmation, and cancel sends nothing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import { render } from '../utils';
import InventoryPageRouter from '../../pages/InventoryPage';
import { api, spoolbuddyApi } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  const fn = () => vi.fn();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSettings: fn(),
      getUiSettings: fn(),
      getSpoolmanSettings: fn(),
      getSpools: fn(),
      getSpoolmanInventorySpools: fn(),
      getAssignments: fn(),
      getSpoolmanSlotAssignments: fn(),
      getSpoolCatalog: fn(),
      getLocations: fn(),
      getLocationHASensors: fn(),
      getLocationHASensorReadings: fn(),
      // local
      deleteSpool: fn(),
      archiveSpool: fn(),
      restoreSpool: fn(),
      resetSpoolConsumedCounter: fn(),
      bulkDeleteSpools: fn(),
      bulkArchiveSpools: fn(),
      bulkRestoreSpools: fn(),
      bulkUpdateSpools: fn(),
      bulkResetSpoolConsumedCounter: fn(),
      // spoolman
      deleteSpoolmanInventorySpool: fn(),
      archiveSpoolmanInventorySpool: fn(),
      restoreSpoolmanInventorySpool: fn(),
      resetSpoolmanInventorySpoolConsumedCounter: fn(),
      bulkDeleteSpoolmanInventorySpools: fn(),
      bulkArchiveSpoolmanInventorySpools: fn(),
      bulkRestoreSpoolmanInventorySpools: fn(),
      bulkUpdateSpoolmanInventorySpools: fn(),
      bulkResetSpoolmanInventorySpoolConsumedCounter: fn(),
      syncSpoolmanSpoolWeight: fn(),
    },
    spoolbuddyApi: {
      ...actual.spoolbuddyApi,
      updateSpoolWeight: vi.fn(),
    },
  };
});

const m = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const sb = spoolbuddyApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const LOCAL = {
  delete: 'deleteSpool',
  archive: 'archiveSpool',
  restore: 'restoreSpool',
  reset: 'resetSpoolConsumedCounter',
  bulkDelete: 'bulkDeleteSpools',
  bulkArchive: 'bulkArchiveSpools',
  bulkRestore: 'bulkRestoreSpools',
  bulkUpdate: 'bulkUpdateSpools',
  bulkReset: 'bulkResetSpoolConsumedCounter',
} as const;
const SPOOLMAN = {
  delete: 'deleteSpoolmanInventorySpool',
  archive: 'archiveSpoolmanInventorySpool',
  restore: 'restoreSpoolmanInventorySpool',
  reset: 'resetSpoolmanInventorySpoolConsumedCounter',
  bulkDelete: 'bulkDeleteSpoolmanInventorySpools',
  bulkArchive: 'bulkArchiveSpoolmanInventorySpools',
  bulkRestore: 'bulkRestoreSpoolmanInventorySpools',
  bulkUpdate: 'bulkUpdateSpoolmanInventorySpools',
  bulkReset: 'bulkResetSpoolmanInventorySpoolConsumedCounter',
} as const;
const ALL_MUTATIONS = [...Object.values(LOCAL), ...Object.values(SPOOLMAN), 'syncSpoolmanSpoolWeight'];

function spool(id: number, over: Record<string, unknown> = {}) {
  return {
    id,
    material: 'PLA',
    subtype: null,
    brand: `Brand${id}`,
    color_name: 'Red',
    rgba: 'FF0000FF',
    label_weight: 1000,
    core_weight: 250,
    weight_used: 100 * id,
    weight_used_baseline: 0,
    slicer_filament: null,
    slicer_filament_name: null,
    nozzle_temp_min: null,
    nozzle_temp_max: null,
    note: null,
    added_full: null,
    last_used: null,
    encode_time: null,
    tag_uid: null,
    tray_uuid: null,
    data_origin: null,
    tag_type: null,
    archived_at: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    k_profiles: [],
    cost_per_kg: null,
    last_scale_weight: null,
    last_weighed_at: null,
    ...over,
  };
}

/** Table row of the spool whose brand is unique per spool. */
async function row(id: number): Promise<HTMLElement> {
  // The brand also appears in filter dropdowns; take the one inside a table row.
  return waitFor(() => {
    const tr = screen
      .getAllByText(`Brand${id}`)
      .map((el) => el.closest('tbody tr'))
      .find(Boolean);
    expect(tr).toBeTruthy();
    return tr as HTMLElement;
  });
}

/** The confirm dialog is the last rendered "Cancel"-bearing dialog. */
function lastButton(name: string | RegExp): HTMLElement {
  const all = screen.getAllByRole('button', { name });
  return all[all.length - 1];
}

/** A bulk-toolbar button (per-row icon buttons share the names via title). */
function toolbarButton(name: RegExp): HTMLElement {
  const toolbar = screen.getByText('3 selected').parentElement as HTMLElement;
  return within(toolbar).getByRole('button', { name });
}

function expectOnlyCalled(name: string) {
  for (const other of ALL_MUTATIONS) {
    if (other !== name) expect(m[other], `${other} must not be called`).not.toHaveBeenCalled();
  }
}

describe.each([
  ['built-in', false, LOCAL],
  ['Spoolman', true, SPOOLMAN],
] as const)('InventoryPage wiring (%s inventory)', (_label, isSpoolman, API) => {
  let spools: ReturnType<typeof spool>[];

  beforeEach(() => {
    vi.clearAllMocks();
    spools = [spool(1), spool(2), spool(3)];
    m.getSettings.mockResolvedValue({ low_stock_threshold: 20, currency: 'USD', date_format: 'system' });
    m.getUiSettings.mockResolvedValue({ low_stock_threshold: 20, currency: 'USD', date_format: 'system' });
    m.getSpoolmanSettings.mockResolvedValue(
      isSpoolman
        ? { spoolman_enabled: 'true', spoolman_url: 'http://spoolman:7912' }
        : { spoolman_enabled: 'false', spoolman_url: '' },
    );
    // Only the active backend returns rows, so a row rendered before the
    // Spoolman-mode switch settles can never be the one a test clicks.
    m.getSpools.mockImplementation(() => Promise.resolve(isSpoolman ? [] : spools));
    m.getSpoolmanInventorySpools.mockImplementation(() => Promise.resolve(isSpoolman ? spools : []));
    m.getAssignments.mockResolvedValue([]);
    m.getSpoolmanSlotAssignments.mockResolvedValue([]);
    m.getSpoolCatalog.mockResolvedValue([]);
    m.getLocations.mockResolvedValue([]);
    m.getLocationHASensors.mockResolvedValue([]);
    m.getLocationHASensorReadings.mockResolvedValue([]);
    for (const k of [API.delete, API.archive, API.restore, API.reset]) m[k].mockResolvedValue({});
    m[API.bulkDelete].mockResolvedValue({ deleted: 3 });
    m[API.bulkArchive].mockResolvedValue({ archived: 3 });
    m[API.bulkRestore].mockResolvedValue({ restored: 3 });
    m[API.bulkUpdate].mockResolvedValue({ updated: 3 });
    m[API.bulkReset].mockResolvedValue({ reset: 3 });
    m.syncSpoolmanSpoolWeight.mockResolvedValue({});
    sb.updateSpoolWeight.mockResolvedValue({});
  });

  afterEach(() => {
    vi.mocked(localStorage.getItem).mockReset();
  });

  it('delete: confirm sends only the mode\'s delete for that spool id', async () => {
    render(<InventoryPageRouter />);
    fireEvent.click(within(await row(2)).getByTitle('Delete'));
    await screen.findByText('Are you sure you want to delete this spool? This cannot be undone.');
    expect(m[API.delete]).not.toHaveBeenCalled();

    fireEvent.click(lastButton('Delete'));
    await waitFor(() => expect(m[API.delete]).toHaveBeenCalledTimes(1));
    expect(m[API.delete].mock.calls[0][0]).toBe(2);
    expectOnlyCalled(API.delete);
  });

  it('delete: cancel sends nothing', async () => {
    render(<InventoryPageRouter />);
    fireEvent.click(within(await row(2)).getByTitle('Delete'));
    await screen.findByText('Are you sure you want to delete this spool? This cannot be undone.');
    fireEvent.click(lastButton('Cancel'));
    await waitFor(() =>
      expect(screen.queryByText('Are you sure you want to delete this spool? This cannot be undone.')).not.toBeInTheDocument(),
    );
    expectOnlyCalled('__none__');
  });

  it('archive: confirm sends only the mode\'s archive for that spool id', async () => {
    render(<InventoryPageRouter />);
    fireEvent.click(within(await row(3)).getByTitle('Archive'));
    await screen.findByText('Are you sure you want to archive this spool?');
    fireEvent.click(lastButton('Archive'));
    await waitFor(() => expect(m[API.archive]).toHaveBeenCalledTimes(1));
    expect(m[API.archive].mock.calls[0][0]).toBe(3);
    expectOnlyCalled(API.archive);
  });

  it('reset counter: confirm sends only the mode\'s reset for that spool id', async () => {
    render(<InventoryPageRouter />);
    fireEvent.click(within(await row(1)).getByTitle(/Zero the consumed-grams counter/));
    fireEvent.click(await waitFor(() => lastButton('Reset counter')));
    await waitFor(() => expect(m[API.reset]).toHaveBeenCalledTimes(1));
    expect(m[API.reset].mock.calls[0][0]).toBe(1);
    expectOnlyCalled(API.reset);
  });

  it('restore (archived spool, archived view) sends only the mode\'s restore', async () => {
    spools = [spool(1, { archived_at: '2026-04-01T00:00:00Z' })];
    render(<InventoryPageRouter />);
    fireEvent.click(await screen.findByRole('button', { name: 'Archived' }));
    fireEvent.click(within(await row(1)).getByTitle('Restore'));
    await waitFor(() => expect(m[API.restore]).toHaveBeenCalledTimes(1));
    expect(m[API.restore].mock.calls[0][0]).toBe(1);
    expectOnlyCalled(API.restore);
  });

  async function selectAllThree() {
    render(<InventoryPageRouter />);
    for (const id of [1, 2, 3]) {
      fireEvent.click(within(await row(id)).getByRole('checkbox', { name: 'Select row' }));
    }
  }

  it('bulk delete on a 3-spool selection sends the 3 ids after confirm; cancel sends nothing', async () => {
    await selectAllThree();
    fireEvent.click(toolbarButton(/^Delete$/));
    await screen.findByText('Delete selected spools');
    fireEvent.click(lastButton('Cancel'));
    await waitFor(() => expect(screen.queryByText('Delete selected spools')).not.toBeInTheDocument());
    expectOnlyCalled('__none__');

    fireEvent.click(toolbarButton(/^Delete$/));
    await screen.findByText('Delete selected spools');
    fireEvent.click(lastButton('Delete'));
    await waitFor(() => expect(m[API.bulkDelete]).toHaveBeenCalledTimes(1));
    expect([...m[API.bulkDelete].mock.calls[0][0]].sort()).toEqual([1, 2, 3]);
    expectOnlyCalled(API.bulkDelete);
  });

  it('bulk archive on a 3-spool selection sends the 3 ids after confirm', async () => {
    await selectAllThree();
    fireEvent.click(toolbarButton(/^Archive$/));
    await screen.findByText('Archive selected spools');
    fireEvent.click(lastButton('Archive'));
    await waitFor(() => expect(m[API.bulkArchive]).toHaveBeenCalledTimes(1));
    expect([...m[API.bulkArchive].mock.calls[0][0]].sort()).toEqual([1, 2, 3]);
    expectOnlyCalled(API.bulkArchive);
  });

  it('bulk reset usage on a 3-spool selection sends the 3 ids after confirm', async () => {
    await selectAllThree();
    fireEvent.click(toolbarButton(/^Reset usage$/));
    await screen.findByText('Reset usage on selected spools');
    fireEvent.click(lastButton('Reset counter'));
    await waitFor(() => expect(m[API.bulkReset]).toHaveBeenCalledTimes(1));
    expect([...m[API.bulkReset].mock.calls[0][0]].sort()).toEqual([1, 2, 3]);
    expectOnlyCalled(API.bulkReset);
  });

  it('bulk edit on a 3-spool selection sends (ids, patch) with only the ticked field', async () => {
    await selectAllThree();
    fireEvent.click(toolbarButton(/^Edit$/));
    const noteLabel = await screen.findByText('Note', { selector: 'label' });
    const fieldRow = noteLabel.closest('div.flex.items-start') as HTMLElement;
    fireEvent.click(within(fieldRow).getByRole('checkbox'));
    fireEvent.change(fieldRow.querySelector('textarea') as HTMLElement, { target: { value: 'batch 7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply to 3 spools' }));

    await waitFor(() => expect(m[API.bulkUpdate]).toHaveBeenCalledTimes(1));
    const [ids, patch] = m[API.bulkUpdate].mock.calls[0];
    expect([...ids].sort()).toEqual([1, 2, 3]);
    expect(patch).toEqual({ note: 'batch 7' });
    expectOnlyCalled(API.bulkUpdate);
  });

  it('sync to scale weight sends (spool.id, last_scale_weight) to the mode\'s endpoint', async () => {
    // setup.ts replaces localStorage with vi.fn stubs; feed the column config through it.
    const columns = JSON.stringify([{ id: 'weight_check', label: 'Weight Check', visible: true }]);
    vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
      key === 'bambuddy-inventory-columns' ? columns : null,
    );
    // Calculated gross = 1000-200+250 = 1050; scale 812 -> mismatch -> sync button.
    spools = [spool(1), spool(2, { last_scale_weight: 812 })];
    render(<InventoryPageRouter />);
    fireEvent.click(within(await row(2)).getByTitle('Sync: trust scale weight and reset tracking'));

    if (isSpoolman) {
      await waitFor(() => expect(m.syncSpoolmanSpoolWeight).toHaveBeenCalledWith(2, 812));
      expect(sb.updateSpoolWeight).not.toHaveBeenCalled();
    } else {
      await waitFor(() => expect(sb.updateSpoolWeight).toHaveBeenCalledWith(2, 812));
      expect(m.syncSpoolmanSpoolWeight).not.toHaveBeenCalled();
    }
  });
});
