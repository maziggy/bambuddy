/**
 * The print dialog's slot pickers can be read on a phone held upright (#3294).
 *
 * Two things hid them. The open list took the width of its button and nothing
 * more, and the button had almost no width: on a nozzle-rack printer the
 * required-filament cell has a 210px floor, which on a ~400px portrait screen
 * left the slot button about 55px -- a swatch and a chevron. Only turning the
 * phone sideways made the slot names readable.
 *
 * Now the list is as wide as its rows want, never narrower than its button and
 * never wider than the screen, and it moves left rather than running off the
 * right edge. Below the `sm` breakpoint the picker gets a line of its own
 * under the required filament. jsdom does no layout, so these pin the numbers
 * and classes that make it fit.
 */

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { screen, fireEvent, cleanup } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { SlotPicker } from '../../components/PrintModal/SlotPicker';
import { FilamentMapping } from '../../components/PrintModal/FilamentMapping';
import { FilamentOverride } from '../../components/PrintModal/FilamentOverride';
import { PrinterSelector } from '../../components/PrintModal/PrinterSelector';
import type { PrinterMappingResult } from '../../hooks/useMultiPrinterFilamentMapping';
import type { Printer, PrinterStatus } from '../../api/client';

const SCREEN = 412;
const LIST_WIDTH = 380;

function rect(left: number, width: number): DOMRect {
  return { left, width, top: 300, bottom: 324, right: left + width, height: 24, x: left, y: 300, toJSON: () => ({}) } as DOMRect;
}

describe('SlotPicker list on a narrow screen', () => {
  let natural = LIST_WIDTH;

  beforeEach(() => {
    natural = LIST_WIDTH;
    // A screen SCREEN wide; a page scrollbar would make innerWidth larger.
    vi.spyOn(document.documentElement, 'clientWidth', 'get').mockReturnValue(SCREEN);
    // The list is as wide as its rows want, within the limits written on it --
    // what a browser's layout does with width: max-content.
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('role') !== 'listbox') return 0;
      const min = parseFloat(this.style.minWidth) || 0;
      const max = parseFloat(this.style.maxWidth) || Infinity;
      return Math.min(Math.max(natural, min), max);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    cleanup();
  });

  function openAt(triggerRect: DOMRect) {
    render(
      <SlotPicker
        value=""
        options={[{ value: '1', label: 'A2: Polymaker PLA Matte (Charcoal Black)', meta: ' - 602g left', hex: '#2F2E30' }]}
        placeholder="-- Select slot --"
        ariaLabel="Slot for PLA"
        onChange={() => {}}
      />,
    );
    const trigger = screen.getByRole('combobox');
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue(triggerRect);
    fireEvent.click(trigger);
    return screen.getByRole('listbox');
  }

  it('is as wide as its rows want, not as narrow as its button', () => {
    const list = openAt(rect(320, 55));
    expect(list.style.width).toBe('max-content');
    expect(list.style.minWidth).toBe('55px');
  });

  it('is never wider than the screen, less a margin', () => {
    const list = openAt(rect(320, 55));
    expect(list.style.maxWidth).toBe(`${SCREEN - 16}px`);
  });

  it('moves left rather than running off the right edge', () => {
    const list = openAt(rect(320, 55));
    expect(list.style.left).toBe(`${SCREEN - 8 - LIST_WIDTH}px`);
  });

  it('stays under its button when it fits', () => {
    const list = openAt(rect(16, 300));
    expect(list.style.left).toBe('16px');
    expect(list.style.minWidth).toBe('300px');
  });

  it('is kept on screen at the width of its button, when that is wider than its rows', () => {
    natural = 200;
    const list = openAt(rect(150, 300));
    expect(list.style.left).toBe(`${SCREEN - 8 - 300}px`);
  });

  it('is kept inside the margin on both sides when its rows want more than the screen', () => {
    natural = 1000;
    const list = openAt(rect(320, 55));
    // It starts at the left margin rather than off the left edge.
    expect(list.style.left).toBe('8px');
  });

  it('leaves out the scrollbar', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: SCREEN + 15 });
    try {
      const list = openAt(rect(320, 55));
      expect(list.style.maxWidth).toBe(`${SCREEN - 16}px`);
      expect(list.style.left).toBe(`${SCREEN - 8 - LIST_WIDTH}px`);
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 });
    }
  });
});

/** The grid row that holds a slot picker. */
function rowOf(picker: HTMLElement): HTMLElement {
  return picker.parentElement as HTMLElement;
}

function expectStackedBelowSm(row: HTMLElement, wideColumns: string) {
  // One column for the swatch / arrow, the picker, one for the status icon.
  expect(row.className).toMatch(/(^|\s)grid-cols-\[16px_minmax\(0,1fr\)_\d+px\](\s|$)/);
  expect(row.className).toContain(`sm:grid-cols-[${wideColumns}]`);
  // The required filament takes the whole first line, so the arrow, the
  // picker and the status icon wrap onto the second.
  const required = row.children[1] as HTMLElement;
  expect(required.className).toMatch(/(^|\s)col-span-2(\s|$)/);
  expect(required.className).toContain('sm:col-span-1');
}

describe('mapping row on a narrow screen', () => {
  const tray = (id: number, color: string) => ({ id, tray_type: 'PLA', tray_color: color, tray_info_idx: 'GFA01' });

  function mount(status: Record<string, unknown>, filament: Record<string, unknown> = {}) {
    server.use(
      http.get('/api/v1/printers/:id/status', () =>
        HttpResponse.json({
          id: 1,
          name: 'H2C-1',
          connected: true,
          state: 'IDLE',
          ams: [{ id: 0, tray: [tray(0, 'DE4343'), tray(1, '2F2E30')] }],
          vt_tray: [],
          ams_extruder_map: {},
          ...status,
        } as unknown as PrinterStatus),
      ),
      http.get('/api/v1/printers/:id/spool-assignments', () => HttpResponse.json([])),
    );
    render(
      <FilamentMapping
        printerId={1}
        filamentReqs={{
          filaments: [{ slot_id: 1, type: 'PLA', color: '#DE4343', used_grams: 297.6, used_meters: 90, ...filament }],
        }}
        manualMappings={{}}
        onManualMappingChange={() => {}}
        currencySymbol="$"
        defaultCostPerKg={20}
        defaultExpanded
      />,
    );
  }

  afterEach(() => cleanup());

  it('puts the picker on its own line, and keeps the wide layout from sm up', async () => {
    mount({});
    const picker = await screen.findByRole('combobox', { name: /printer slot for/i });
    expectStackedBelowSm(rowOf(picker), '16px_minmax(70px,1fr)_auto_2fr_16px');
  });

  it('does the same on a nozzle-rack printer, where the squeeze was worst', async () => {
    const rackSlot = (id: number) => ({ id, nozzle_type: 'HH01', nozzle_diameter: '0.4', filament_color: '', filament_id: '', filament_type: '' });
    mount(
      { nozzle_rack: [rackSlot(16), rackSlot(17), rackSlot(1)] },
      { group_id: 1, group: { on_rack: true, nozzle_diameter: '0.40', volume_type: 'Standard', filament_color: '#DE4343' } },
    );
    await screen.findByLabelText('Rack position');
    expectStackedBelowSm(rowOf(screen.getByRole('combobox', { name: /printer slot for/i })), '16px_minmax(210px,1.4fr)_auto_2fr_16px');
  });
});

describe('override row on a narrow screen', () => {
  afterEach(() => cleanup());

  it('puts the picker on its own line, and keeps the wide layout from sm up', () => {
    render(
      <FilamentOverride
        filamentReqs={{ filaments: [{ slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5 }] }}
        availableFilaments={[
          { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', tray_sub_brands: 'PLA Basic', extruder_id: null },
        ]}
        overrides={{}}
        onChange={() => {}}
      />,
    );
    expectStackedBelowSm(rowOf(screen.getByRole('combobox')), '16px_minmax(70px,1fr)_auto_2fr_20px');
  });
});

describe('custom slot mapping of one of several printers, on a narrow screen', () => {
  afterEach(() => cleanup());

  it('puts the select on its own line, and keeps the wide layout from sm up', async () => {
    const printer = (id: number) => ({ id, name: `H2C-${id}`, model: 'H2C', ip_address: `192.0.2.${id}`, is_active: true }) as unknown as Printer;
    const result = (id: number): PrinterMappingResult => ({
      printerId: id,
      printerName: `H2C-${id}`,
      status: undefined,
      isLoading: false,
      loadedFilaments: [],
      autoMapping: undefined,
      finalMapping: undefined,
      matchStatus: 'missing',
      exactMatches: 0,
      typeOnlyMatches: 0,
      missingTypes: 1,
      totalSlots: 1,
      config: { useDefault: false, manualMappings: {}, autoConfigured: false },
    });
    render(
      <PrinterSelector
        printers={[printer(1), printer(2)]}
        selectedPrinterIds={[1, 2]}
        onMultiSelect={() => {}}
        allowMultiple
        printerMappingResults={[result(1), result(2)]}
        filamentReqs={{ filaments: [{ slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5 }] }}
        onAutoConfigurePrinter={() => {}}
        onUpdatePrinterConfig={() => {}}
      />,
    );
    const selects = await screen.findAllByRole('combobox');
    expect(selects.length).toBe(2);
    for (const select of selects) {
      expectStackedBelowSm(rowOf(select), '16px_minmax(70px,1fr)_auto_2fr_16px');
    }
  });
});
