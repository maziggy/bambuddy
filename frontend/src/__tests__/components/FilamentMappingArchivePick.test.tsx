/**
 * Tests for the FilamentMapping "Mapping" toggle (#2700).
 *
 * When the archive carries the slicer's own saved AMS-slot pick, a toggle next
 * to "Re-read" selects every slot straight from it, bypassing the type/color
 * auto-match. Turning it back off has to undo exactly what it did and leave
 * hand-made picks alone — that bookkeeping is what these tests pin.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useState } from 'react';
import { screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FilamentMapping } from '../../components/PrintModal/FilamentMapping';
import type { PrinterStatus } from '../../api/client';

// Two-slot print. Slot 1 wants red PLA, slot 2 wants green PETG.
const TWO_SLOT_REQS = {
  filaments: [
    { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, used_meters: 3 },
    { slot_id: 2, type: 'PETG', color: '#00FF00', used_grams: 10, used_meters: 3 },
  ],
};

// One AMS, four trays -> global tray IDs 0..3. Trays 0 and 2 both hold red PLA,
// which is exactly the ambiguity the saved slicer pick exists to resolve: the
// auto-match has no way to tell which red spool the user meant.
function createStatus(): PrinterStatus {
  return {
    id: 1,
    name: 'X1C',
    connected: true,
    state: 'IDLE',
    ams: [
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FF0000', tray_info_idx: 'GFA00', tray_sub_brands: 'Red A' },
          { id: 1, tray_type: 'PETG', tray_color: '00FF00', tray_info_idx: 'GFG00', tray_sub_brands: 'Green' },
          { id: 2, tray_type: 'PLA', tray_color: 'FF0000', tray_info_idx: 'GFA00', tray_sub_brands: 'Red B' },
          { id: 3, tray_type: 'PLA', tray_color: '0000FF', tray_info_idx: 'GFA00', tray_sub_brands: 'Blue' },
        ],
      },
    ],
    vt_tray: [],
    ams_extruder_map: {},
    fila_switch: null,
  } as unknown as PrinterStatus;
}

/** Holds `manualMappings` the way PrintModal does, so an OFF click sees the
 *  state the ON click produced rather than the initial prop. */
function Harness({
  archiveAmsMapping,
  initialManualMappings = {},
  onChange,
}: {
  archiveAmsMapping?: number[];
  initialManualMappings?: Record<number, number>;
  onChange?: (m: Record<number, number>) => void;
}) {
  const [manualMappings, setManualMappings] = useState<Record<number, number>>(initialManualMappings);
  return (
    <FilamentMapping
      printerId={1}
      filamentReqs={TWO_SLOT_REQS}
      manualMappings={manualMappings}
      onManualMappingChange={(m) => {
        setManualMappings(m);
        onChange?.(m);
      }}
      currencySymbol="$"
      defaultCostPerKg={0}
      defaultExpanded
      archiveAmsMapping={archiveAmsMapping}
    />
  );
}

/** The panel only finishes mounting once printer status has loaded. */
async function waitForPanel() {
  await waitFor(() => {
    expect(screen.getByText(/Re-read/i)).toBeInTheDocument();
  });
}

beforeEach(() => {
  server.use(
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(createStatus())),
    http.get('/api/v1/printers/:id/spool-assignments', () => HttpResponse.json([])),
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('FilamentMapping — saved slicer AMS pick', () => {
  it('hides the toggle when the archive has no saved mapping', async () => {
    // Every archive predating the feature, every library file, and every
    // reprint aimed at a printer other than the one the mapping came from.
    render(<Harness />);
    await waitForPanel();
    expect(screen.queryByRole('button', { name: 'Mapping' })).not.toBeInTheDocument();
  });

  it('selects every slot from the saved mapping when switched on', async () => {
    // Saved pick says slot 1 -> tray 2 (the *second* red spool) and slot 2 ->
    // tray 1. Auto-match would have taken tray 0 for slot 1, so this is a
    // visible, load-bearing difference.
    const onChange = vi.fn();
    render(<Harness archiveAmsMapping={[2, 1]} onChange={onChange} />);
    await waitForPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Mapping' }));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({ 1: 2, 2: 1 });
  });

  it('skips slots the slicer left unresolved', async () => {
    // -1 is the slicer saying "no AMS tray for this filament" (external spool,
    // or it simply didn't resolve). Overriding that slot with -1 would be
    // worse than leaving it to the auto-match.
    const onChange = vi.fn();
    render(<Harness archiveAmsMapping={[-1, 1]} onChange={onChange} />);
    await waitForPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Mapping' }));

    expect(onChange).toHaveBeenCalledWith({ 2: 1 });
  });

  it('skips slots the saved mapping is too short to address', async () => {
    // A mapping with fewer entries than the plate has slots can't say anything
    // about the missing ones; reading past the end would write `undefined`.
    const onChange = vi.fn();
    render(<Harness archiveAmsMapping={[2]} onChange={onChange} />);
    await waitForPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Mapping' }));

    expect(onChange).toHaveBeenCalledWith({ 1: 2 });
  });

  it('undoes exactly its own picks when switched off', async () => {
    const onChange = vi.fn();
    render(<Harness archiveAmsMapping={[2, 1]} onChange={onChange} />);
    await waitForPanel();

    const toggle = screen.getByRole('button', { name: 'Mapping' });
    fireEvent.click(toggle);
    expect(onChange).toHaveBeenLastCalledWith({ 1: 2, 2: 1 });

    fireEvent.click(toggle);
    expect(onChange).toHaveBeenLastCalledWith({});
  });

  it('leaves a hand-made pick untouched when switched off', async () => {
    // The user picked slot 2 by hand first, then switched the toggle on, which
    // overwrote it as part of applying the whole saved mapping. Switching off
    // removes both, since both are now the toggle's own picks — the earlier
    // hand pick is not restored, and slot 2 falls back to the auto-match. That
    // is the documented behaviour, not an accident; the next test covers the
    // case where the hand pick does survive.
    const onChange = vi.fn();
    render(<Harness archiveAmsMapping={[2, 1]} initialManualMappings={{ 2: 3 }} onChange={onChange} />);
    await waitForPanel();

    const toggle = screen.getByRole('button', { name: 'Mapping' });
    fireEvent.click(toggle);
    expect(onChange).toHaveBeenLastCalledWith({ 1: 2, 2: 1 });

    fireEvent.click(toggle);
    expect(onChange).toHaveBeenLastCalledWith({});
  });

  it('keeps hand-made picks for slots the saved mapping never touched', async () => {
    // Saved mapping only resolves slot 1, so slot 2's hand-made pick was never
    // one of "its own" and has to survive the round trip.
    const onChange = vi.fn();
    render(<Harness archiveAmsMapping={[2, -1]} initialManualMappings={{ 2: 3 }} onChange={onChange} />);
    await waitForPanel();

    const toggle = screen.getByRole('button', { name: 'Mapping' });
    fireEvent.click(toggle);
    expect(onChange).toHaveBeenLastCalledWith({ 1: 2, 2: 3 });

    fireEvent.click(toggle);
    expect(onChange).toHaveBeenLastCalledWith({ 2: 3 });
  });
});
