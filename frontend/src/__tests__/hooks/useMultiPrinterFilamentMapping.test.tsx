/**
 * Regression tests for the cross-printer AMS mapping bug (#2799).
 *
 * A mapping entry is a global tray ID, and those only mean something on the
 * printer they were resolved against. The shared mapping panel only renders for
 * a single selected printer, so its edits belong to that printer alone — but
 * every selected printer used to receive them, and the match badge was computed
 * from them too. Two failures fall out of that: a printer prints from whatever
 * sits in the borrowed tray, and the panel reports a match it is not going to
 * submit.
 *
 * The reproduction is the real incident: the file needs light-blue PETG in slot
 * 1 and dark-blue PETG in slot 3. P2S-4 holds them in trays 2 and 1, P2S-5 in
 * trays 0 and 2, with ASA in tray 1.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { PrinterStatus } from '../../api/client';

const statusByPrinter: Record<number, PrinterStatus> = {};

vi.mock('@tanstack/react-query', () => ({
  useQueries: ({ queries }: { queries: { queryKey: unknown[] }[] }) => queries.map((q) => ({
    data: statusByPrinter[q.queryKey[1] as number],
    isLoading: false,
  })),
}));

vi.mock('../../api/client', () => ({ api: { getPrinterStatus: vi.fn() } }));

const { useMultiPrinterFilamentMapping } = await import('../../hooks/useMultiPrinterFilamentMapping');

const status = (trays: { type: string; color: string }[]): PrinterStatus =>
  ({
    ams: [{ id: 0, tray: trays.map((t, id) => ({ id, tray_type: t.type, tray_color: t.color })) }],
  }) as unknown as PrinterStatus;

// Same spools, different slot order.
const P2S_4 = status([
  { type: 'ASA', color: '161616FF' },
  { type: 'PETG', color: '2850E0FF' },
  { type: 'PETG', color: '76D9F4FF' },
]);
const P2S_5 = status([
  { type: 'PETG', color: '76D9F4FF' },
  { type: 'ASA', color: '161616FF' },
  { type: 'PETG', color: '2850E0FF' },
]);

const FILAMENT_REQS = {
  filaments: [
    { slot_id: 1, type: 'PETG', color: '#76D9F4' },
    { slot_id: 3, type: 'PETG', color: '#2850E0' },
  ],
} as never;

const printers = [
  { id: 9, name: 'P2S-4' },
  { id: 10, name: 'P2S-5' },
] as never;

function render(
  selected: number[],
  defaultMappings: Record<number, number>,
  defaultMappingsPrinterId: number | null,
) {
  statusByPrinter[9] = P2S_4;
  statusByPrinter[10] = P2S_5;
  return renderHook(() =>
    useMultiPrinterFilamentMapping(
      selected,
      printers,
      FILAMENT_REQS,
      defaultMappings,
      {},
      vi.fn(),
      false,
      undefined,
      defaultMappingsPrinterId,
    ),
  );
}

describe('useMultiPrinterFilamentMapping — per-printer mapping (#2799)', () => {
  afterEach(() => {
    statusByPrinter[9] = P2S_4;
    statusByPrinter[10] = P2S_5;
  });

  it('maps each printer against its own trays when nothing was overridden', () => {
    const { result } = render([9, 10], {}, null);

    expect(result.current.getFinalMapping(9)).toEqual([2, -1, 1]);
    expect(result.current.getFinalMapping(10)).toEqual([0, -1, 2]);
  });

  it('does not lend one printer\'s shared mapping to another', () => {
    // Authored while only P2S-4 was selected: slot 1 -> tray 2, slot 3 -> tray 1.
    const shared = { 1: 2, 3: 1 };
    const { result } = render([9, 10], shared, 9);

    // Correct on the printer it was authored against...
    expect(result.current.getFinalMapping(9)).toEqual([2, -1, 1]);
    // ...and NOT applied to the other, where tray 1 is ASA.
    expect(result.current.getFinalMapping(10)).toEqual([0, -1, 2]);
  });

  it('keeps the shared mapping on the printer it was authored against', () => {
    // A deliberate choice that auto-matching would not make on its own:
    // slot 3 pinned to tray 0 (ASA) on P2S-4.
    const { result } = render([9, 10], { 3: 0 }, 9);

    expect(result.current.getFinalMapping(9)?.[2]).toBe(0);
    // The other printer is unaffected and still matches on type+colour.
    expect(result.current.getFinalMapping(10)).toEqual([0, -1, 2]);
  });

  it('reports a match badge derived from the mapping it will actually submit', () => {
    // Before the fix the badge for P2S-5 was computed from P2S-4's tray IDs and
    // claimed a full match while a different mapping went out.
    const { result } = render([9, 10], { 1: 2, 3: 1 }, 9);

    const p2s5 = result.current.printerResults.find((r) => r.printerId === 10)!;
    expect(p2s5.matchStatus).toBe('full');
    expect(p2s5.exactMatches).toBe(2);
    // The badge's own input agrees with the submitted mapping.
    expect(result.current.getFinalMapping(10)).toEqual([0, -1, 2]);
  });

  it('does not hand shared mappings to anyone when their owner is unknown', () => {
    // Editing a model-based queue item seeds the shared mappings from its
    // stored ams_mapping while its printer_id is null, so there is no owner.
    // "Unknown" must not read as "every printer".
    const { result } = render([9, 10], { 1: 2, 3: 1 }, null);

    expect(result.current.getFinalMapping(9)).toEqual([2, -1, 1]);
    expect(result.current.getFinalMapping(10)).toEqual([0, -1, 2]);
  });

  it('flags a printer that cannot satisfy a slot', () => {
    // Restored in afterEach — the next test to be added here would otherwise
    // inherit this two-tray P2S-5 and fail for a reason that is not its own.
    statusByPrinter[10] = status([
      { type: 'PETG', color: '76D9F4FF' },
      { type: 'ASA', color: '161616FF' },
    ]);
    const { result } = renderHook(() =>
      useMultiPrinterFilamentMapping([10], printers, FILAMENT_REQS, {}, {}, vi.fn(), false, undefined, null),
    );

    const p2s5 = result.current.printerResults[0];
    expect(p2s5.missingTypes).toBe(1);
    expect(p2s5.matchStatus).toBe('missing');
    expect(result.current.allPrintersReady).toBe(false);
    expect(result.current.getFinalMapping(10)?.[2]).toBe(-1);
  });
});
