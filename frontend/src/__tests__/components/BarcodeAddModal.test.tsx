/**
 * Tests for BarcodeAddModal — the SpoolBuddy kiosk barcode add-to-inventory
 * state machine (screens B/C/D/E/F). Covers: matched scan → confirm, a
 * barcode-first scan with no tag → "Add Without Tag", an unmatched scan →
 * "Find This Filament", and that creating a spool sends the scanned barcode.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { BarcodeAddModal } from '../../components/spoolbuddy/BarcodeAddModal';
import type { ScannedBarcode } from '../../hooks/useSpoolBuddyState';

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
    createSpool: vi.fn().mockResolvedValue({ id: 1 }),
    createSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 1 }),
    linkTagToSpoolmanSpool: vi.fn().mockResolvedValue({ id: 1 }),
    lookupFilamentBarcode: vi.fn(),
    searchBarcodeCatalog: vi.fn().mockResolvedValue([]),
    getLocations: vi.fn().mockResolvedValue([]),
    createLocation: vi.fn(),
  },
}));

import { api } from '../../api/client';

function makeScan(over: Partial<ScannedBarcode> = {}): ScannedBarcode {
  return {
    barcode: '6975337031234',
    kind: 'gtin',
    symbology: 'ean-upc',
    valid: true,
    matched: true,
    source: 'ofd',
    material: 'PLA',
    brand: 'Polymaker',
    subtype: 'PolyTerra Matte',
    color_name: 'Charcoal Black',
    rgba: '3B3B3FFF',
    label_weight: 1000,
    nozzle_temp_min: 190,
    nozzle_temp_max: 230,
    is_refill: false,
    linked_codes: [],
    deviceId: 'sb-1',
    receivedAt: Date.now(),
    ...over,
  };
}

const baseProps = {
  isOpen: true,
  onClose: vi.fn(),
  trayUuid: null,
  spoolmanMode: false,
  spools: [],
  onCreated: vi.fn(),
  onFallbackQuickAdd: vi.fn(),
  clearScan: vi.fn(),
};

describe('BarcodeAddModal', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the confirm screen with filament + Add to Inventory when a matched scan has a tag', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    expect(await screen.findByText('Charcoal Black')).toBeInTheDocument();
    expect(screen.getByText(/Matched in Open Filament Database/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Add to Inventory$/i })).toBeInTheDocument();
  });

  it('offers "Add Without Tag" for a barcode-first scan (no tag on scale)', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid={null} scaleWeight={null} />,
    );
    expect(await screen.findByText('Charcoal Black')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Without Tag/i })).toBeInTheDocument();
  });

  it('routes a valid-but-unmatched scan to the no-match screen with Find This Filament', async () => {
    render(
      <BarcodeAddModal
        {...baseProps}
        scan={makeScan({ matched: false, source: null, material: null, brand: null, subtype: null, color_name: null, rgba: null, label_weight: null })}
        tagUid="0C1C8364"
        scaleWeight={1247}
      />,
    );
    expect(await screen.findByText(/No Match Found/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Find This Filament/i })).toBeInTheDocument();
  });

  it('ignores further scans while a resolved screen is showing (presentation-mode re-fires)', async () => {
    const { rerender } = render(
      <BarcodeAddModal {...baseProps} scan={makeScan({ receivedAt: 1000 })} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    expect(await screen.findByText('Charcoal Black')).toBeInTheDocument();

    // A second scan lands while the confirm screen is up — it must NOT
    // replace the resolved state.
    rerender(
      <BarcodeAddModal
        {...baseProps}
        scan={makeScan({ receivedAt: 2000, barcode: '9999999999990', color_name: 'Lava Red' })}
        tagUid="0C1C8364"
        scaleWeight={1247}
      />,
    );
    expect(screen.getByText('Charcoal Black')).toBeInTheDocument();
    expect(screen.queryByText('Lava Red')).not.toBeInTheDocument();
  });

  it('Rescan re-arms the gate: the next NEW scan applies (the dropped one does not replay)', async () => {
    const { rerender } = render(
      <BarcodeAddModal {...baseProps} scan={makeScan({ receivedAt: 1000 })} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    expect(await screen.findByText('Charcoal Black')).toBeInTheDocument();

    // Gated scan (consumed and dropped).
    rerender(
      <BarcodeAddModal
        {...baseProps}
        scan={makeScan({ receivedAt: 2000, barcode: '9999999999990', color_name: 'Lava Red' })}
        tagUid="0C1C8364"
        scaleWeight={1247}
      />,
    );

    // Rescan returns to the waiting screen — the dropped scan must not replay.
    fireEvent.click(screen.getByRole('button', { name: /^Rescan$/i }));
    expect(await screen.findByText(/Scan Barcode to Add/i)).toBeInTheDocument();
    expect(screen.queryByText('Lava Red')).not.toBeInTheDocument();

    // A genuinely new scan applies again.
    rerender(
      <BarcodeAddModal
        {...baseProps}
        scan={makeScan({ receivedAt: 3000, barcode: '8888888888880', color_name: 'Jade Green' })}
        tagUid="0C1C8364"
        scaleWeight={1247}
      />,
    );
    expect(await screen.findByText('Jade Green')).toBeInTheDocument();
  });

  it('creates a spool with the scanned barcode in the payload (local mode)', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    fireEvent.click(await screen.findByRole('button', { name: /^Add to Inventory$/i }));

    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.scanned_code).toBe('6975337031234');
    // The scan's AIM symbology hint rides along so backend routing keeps
    // the scan-time classification.
    expect(payload.scanned_symbology).toBe('ean-upc');
    expect(payload.material).toBe('PLA');
    expect(payload.tag_uid).toBe('0C1C8364');
    expect(payload.data_origin).toBe('barcode_scan');
    // A plain scan defers all code routing to the backend — no explicit codes.
    expect(payload.gtin_code).toBeNull();
  });

  it('marks the spool as bought-as-refill (+ zero core weight) when the toggle is on', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    // On the confirm screen, flip the "This is a refill" toggle, then add.
    fireEvent.click(await screen.findByRole('switch'));
    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));

    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.bought_as_refill).toBe(true);
    expect(payload.core_weight).toBe(0);
  });

  it('auto-arms the refill toggle when the scanned code is itself a refill (no user action)', async () => {
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan({ is_refill: true })} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    // The toggle should already be on from the backend-detected refill flag.
    const sw = await screen.findByRole('switch');
    expect(sw).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));

    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    const payload = (api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.bought_as_refill).toBe(true);
    expect(payload.core_weight).toBe(0);
  });

  it('warns when refill is on but the roll is too heavy for a bare refill', async () => {
    // 1300 g on a 1000 g-label roll: normal as a with-spool, impossible as a refill.
    render(<BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1300} />);
    await screen.findByText('Charcoal Black');
    // With-spool (default): no warning.
    expect(screen.queryByText(/Heavier than a bare refill/i)).not.toBeInTheDocument();
    // Flip to refill → the impossible-weight warning appears.
    fireEvent.click(screen.getByRole('switch'));
    expect(await screen.findByText(/Heavier than a bare refill/i)).toBeInTheDocument();
  });

  it('opens the Find step and renders search results without crashing', async () => {
    (api.searchBarcodeCatalog as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        source: 'ofd',
        spool_id: null,
        material: 'PLA',
        brand: 'Polymaker',
        subtype: 'PolyTerra Matte',
        color_name: 'Charcoal',
        rgba: '3B3B3FFF',
        label_weight: 1000,
        nozzle_temp_min: 190,
        nozzle_temp_max: 230,
        codes: [{ code: '6975337031234', kind: 'gtin', is_refill: false }],
      },
    ]);
    const unmatched = makeScan({
      matched: false, source: null, material: null, brand: null, subtype: null,
      color_name: null, rgba: null, label_weight: null,
    });
    render(<BarcodeAddModal {...baseProps} scan={unmatched} tagUid="0C1C8364" scaleWeight={1247} />);

    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    // Find step should render (no crash on the transition)
    const input = await screen.findByPlaceholderText(/polymaker charcoal/i);
    fireEvent.change(input, { target: { value: 'polymaker' } });
    // Debounced search result should render (this exercises the row + SourcePill)
    expect(await screen.findByText('Open Filament DB')).toBeInTheDocument();
  });

  it('distinguishes refill-SKU twins in Find results with a badge and the code', async () => {
    // Two SpoolmanDB entries for the same color — one with-spool, one refill —
    // used to render as identical rows with no way to tell them apart.
    const twin = {
      source: 'spoolmandb-community', spool_id: null, material: 'PLA', brand: 'Bambu Lab',
      subtype: 'PLA Pure', color_name: 'Baby Blue', rgba: '89CFF0FF', label_weight: 1000,
      nozzle_temp_min: null, nozzle_temp_max: null,
    };
    (api.searchBarcodeCatalog as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...twin, codes: [{ code: '6975337031111', kind: 'gtin', is_refill: false }] },
      { ...twin, codes: [{ code: '6975337032222', kind: 'gtin', is_refill: true }] },
    ]);
    const unmatched = makeScan({
      matched: false, source: null, material: null, brand: null, subtype: null,
      color_name: null, rgba: null, label_weight: null,
    });
    render(<BarcodeAddModal {...baseProps} scan={unmatched} tagUid="0C1C8364" scaleWeight={1247} />);

    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    const input = await screen.findByPlaceholderText(/polymaker charcoal/i);
    fireEvent.change(input, { target: { value: 'baby blue' } });

    // Each row shows its code, and only the all-refill row carries the badge.
    expect(await screen.findByText(/6975337032222/)).toBeInTheDocument();
    expect(screen.getByText(/6975337031111/)).toBeInTheDocument();
    expect(screen.getAllByText('Refill pack')).toHaveLength(1);
  });

  const mixedCodesRow = {
    source: 'spoolmandb-community', spool_id: null, material: 'PLA', brand: 'Bambu Lab',
    subtype: 'PLA Pure', color_name: 'Baby Blue', rgba: '89CFF0FF', label_weight: 1000,
    nozzle_temp_min: 190, nozzle_temp_max: 230,
    codes: [
      { code: '111', kind: 'gtin', is_refill: false },
      { code: '222', kind: 'gtin', is_refill: true },
    ],
  };

  async function openFindAndSearch() {
    const unmatched = makeScan({
      matched: false, source: null, material: null, brand: null, subtype: null,
      color_name: null, rgba: null, label_weight: null,
    });
    render(<BarcodeAddModal {...baseProps} scan={unmatched} tagUid="0C1C8364" scaleWeight={1247} />);
    fireEvent.click(await screen.findByRole('button', { name: /Find This Filament/i }));
    const input = await screen.findByPlaceholderText(/polymaker charcoal/i);
    fireEvent.change(input, { target: { value: 'baby blue' } });
    return screen.findByText('Baby Blue — PLA Pure');
  }

  it('find flow: select enables Confirm, and Back from confirm keeps all state', async () => {
    (api.searchBarcodeCatalog as ReturnType<typeof vi.fn>).mockResolvedValue([mixedCodesRow]);
    const rowTitle = await openFindAndSearch();

    const confirmBtn = screen.getByRole('button', { name: /^Confirm$/i });
    expect(confirmBtn).toBeDisabled();
    fireEvent.click(rowTitle);
    expect(confirmBtn).not.toBeDisabled();
    fireEvent.click(confirmBtn);

    // Reached via Find → the confirm screen offers Back, not Cancel.
    expect(await screen.findByText('Confirm New Spool')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Cancel$/i })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Back$/i }));

    // Query, results, and selection survive the round-trip.
    expect(await screen.findByDisplayValue('baby blue')).toBeInTheDocument();
    expect(screen.getByText('Baby Blue — PLA Pure')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Confirm$/i })).not.toBeDisabled();
  });

  it('find row Details discloses every code with its own refill flag', async () => {
    (api.searchBarcodeCatalog as ReturnType<typeof vi.fn>).mockResolvedValue([mixedCodesRow]);
    await openFindAndSearch();

    // Mixed-code row: no collapsed badge (it is not purely a refill entry) …
    expect(screen.queryByText('Refill pack')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /^Details$/i }));
    // … but the disclosure lists both codes, flagging only the refill one.
    expect(await screen.findByText('222')).toBeInTheDocument();
    expect(screen.getByText('111')).toBeInTheDocument();
    expect(screen.getAllByText('Refill pack')).toHaveLength(1);
    // Disclosure also selects the row.
    expect(screen.getByRole('button', { name: /^Confirm$/i })).not.toBeDisabled();
  });

  it('offers "Find This Filament" on the scan-waiting screen (B) and Back returns there', async () => {
    // No scan yet (NFC + weight only, no box/barcode) → the modal sits on screen B.
    render(<BarcodeAddModal {...baseProps} scan={null} tagUid="0C1C8364" scaleWeight={1247} />);
    expect(await screen.findByText('Scan Barcode to Add')).toBeInTheDocument();

    // The Find button jumps straight to the Find screen without needing a scan.
    fireEvent.click(screen.getByRole('button', { name: /Find This Filament/i }));
    expect(await screen.findByPlaceholderText(/polymaker charcoal/i)).toBeInTheDocument();

    // Back returns to screen B (not the no-match screen it defaults to).
    fireEvent.click(screen.getByRole('button', { name: /^Back$/i }));
    expect(await screen.findByText('Scan Barcode to Add')).toBeInTheDocument();
  });

  it('does not render modal content when closed', () => {
    render(
      <BarcodeAddModal {...baseProps} isOpen={false} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    expect(screen.queryByText('Charcoal Black')).not.toBeInTheDocument();
  });

  const spoolsWithLocations = [
    {
      id: 1, archived_at: null, created_at: '2026-08-20T10:00:00Z',
      location_id: 5, storage_location: 'Shelf A',
      material: 'PLA', brand: null, subtype: null, color_name: null, rgba: null,
      label_weight: 1000, barcode: null,
    },
    {
      id: 2, archived_at: null, created_at: '2026-08-22T10:00:00Z',
      location_id: 7, storage_location: 'Dry Box 1',
      material: 'PLA', brand: null, subtype: null, color_name: null, rgba: null,
      label_weight: 1000, barcode: null,
    },
  ] as unknown as import('../../api/client').InventorySpool[];

  it("defaults the location to the last added spool's and sends it in the payload", async () => {
    (api.getLocations as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: 5, name: 'Shelf A' }, { id: 7, name: 'Dry Box 1' },
    ]);
    render(
      <BarcodeAddModal {...baseProps} spools={spoolsWithLocations} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    // Chip pre-selects spool #2's location (newest created_at) with the hint.
    expect(await screen.findByText('Dry Box 1')).toBeInTheDocument();
    expect(screen.getByText(/last used/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));
    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    expect((api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0].location_id).toBe(7);
  });

  it('lets the user clear the location via the picker', async () => {
    (api.getLocations as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: 5, name: 'Shelf A' }, { id: 7, name: 'Dry Box 1' },
    ]);
    render(
      <BarcodeAddModal {...baseProps} spools={spoolsWithLocations} scan={makeScan()} tagUid="0C1C8364" scaleWeight={1247} />,
    );
    // Open the picker via the chip, pick "No location".
    fireEvent.click(await screen.findByRole('button', { name: /Dry Box 1/ }));
    fireEvent.click(await screen.findByRole('button', { name: /^No location$/ }));

    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));
    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    expect((api.createSpool as ReturnType<typeof vi.fn>).mock.calls[0][0].location_id).toBeNull();
  });

  it('shows the backend error and stays open when the create is rejected', async () => {
    // e.g. the duplicate-tag 409 guard: a stale tag already linked to another
    // spool must surface as a visible error, not a silently dead button.
    (api.createSpool as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('Tag 72DB77EB is already linked to spool #41'),
    );
    render(
      <BarcodeAddModal {...baseProps} scan={makeScan()} tagUid="72DB77EB" scaleWeight={1247} />,
    );
    fireEvent.click(await screen.findByRole('button', { name: /^Add to Inventory$/i }));

    expect(await screen.findByText(/already linked to spool #41/i)).toBeInTheDocument();
    expect(baseProps.onClose).not.toHaveBeenCalled();
    expect(baseProps.onCreated).not.toHaveBeenCalled();

    // A retry after the failure works and closes the modal.
    fireEvent.click(screen.getByRole('button', { name: /^Add to Inventory$/i }));
    await waitFor(() => expect(baseProps.onCreated).toHaveBeenCalledTimes(1));
  });
});
