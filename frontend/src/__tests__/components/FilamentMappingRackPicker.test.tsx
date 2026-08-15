/**
 * The per-group rack position picker in the print dialog (#1784).
 *
 * On an H2C the rack carriage hosts six hotends and the 3MF does not say which
 * one a filament group should use — proven by dispatching one plate twice from
 * BambuStudio with different picks and finding the two files identical bar
 * float noise. So the dialog has to ask, and the answer travels to the printer
 * as `nozzle_mapping`.
 *
 * The picker is per *group*, not per slot: two slots sharing a group share one
 * hotend and cannot point at different positions.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FilamentMapping } from '../../components/PrintModal/FilamentMapping';
import type { PrinterStatus } from '../../api/client';

// The maintainer's own plate: three PLA filaments in groups 2/0/1, with groups
// 1 and 2 on the rack and group 0 on the fixed hotend.
const group = (over = {}) => ({
  on_rack: true,
  nozzle_diameter: '0.40',
  volume_type: 'High Flow',
  filament_color: '',
  ...over,
});

const benchyReqs = {
  filaments: [
    { slot_id: 1, type: 'PLA', color: '#DE4343', used_grams: 7, used_meters: 2.1,
      group_id: 2, group: group({ filament_color: '#DE4343' }) },
    { slot_id: 2, type: 'PLA', color: '#F4EE2A', used_grams: 7, used_meters: 2.2,
      group_id: 0, group: group({ on_rack: false, filament_color: '#F4EE2A' }) },
    { slot_id: 3, type: 'PLA', color: '#0078BF', used_grams: 10, used_meters: 3.3,
      group_id: 1, group: group({ filament_color: '#0078BF' }) },
  ],
};

const rackSlot = (id: number, over = {}) => ({
  id,
  nozzle_type: 'HH01',
  nozzle_diameter: '0.4',
  wear: null,
  stat: null,
  max_temp: 300,
  serial_number: '',
  filament_color: '',
  filament_id: '',
  filament_type: '',
  ...over,
});

function createStatus(nozzleRack: unknown[]): PrinterStatus {
  return {
    id: 1,
    name: 'H2C-1',
    connected: true,
    state: 'IDLE',
    ams: [
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'DE4343', tray_info_idx: 'GFA01' },
          { id: 1, tray_type: 'PLA', tray_color: 'F4EE2A', tray_info_idx: 'GFA00' },
          { id: 2, tray_type: 'PLA', tray_color: '0078BF', tray_info_idx: 'GFA01' },
        ],
      },
    ],
    vt_tray: [],
    ams_extruder_map: {},
    nozzle_rack: nozzleRack,
    ...{},
  } as unknown as PrinterStatus;
}

/** Full rack, plus the fixed carriage the printer always reports. */
const fullRack = [1, 2, 3, 4, 5, 6].map((p) => rackSlot(15 + p)).concat(rackSlot(1));

function mount(props: Record<string, unknown> = {}, rack = fullRack) {
  server.use(
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(createStatus(rack))),
    http.get('/api/v1/printers/:id/spool-assignments', () => HttpResponse.json([])),
  );
  return render(
    <FilamentMapping
      printerId={1}
      filamentReqs={benchyReqs}
      manualMappings={{}}
      onManualMappingChange={() => {}}
      currencySymbol="$"
      defaultCostPerKg={20}
      defaultExpanded
      {...props}
    />,
  );
}

/** The rack pickers, in slot order. */
async function pickers() {
  return await waitFor(async () => {
    const found = await screen.findAllByLabelText('Rack position');
    expect(found.length).toBeGreaterThan(0);
    return found as HTMLSelectElement[];
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('FilamentMapping — nozzle rack position picker', () => {
  beforeEach(() => {
    server.use(http.get('/api/v1/printers/:id/spool-assignments', () => HttpResponse.json([])));
  });

  it('offers a picker for each rack-bound group and none for the fixed one', async () => {
    mount();
    const selects = await pickers();

    // Groups 2 and 1 are on the rack; group 0 is the fixed hotend and gets the
    // plain L badge instead.
    expect(selects).toHaveLength(2);
    expect(screen.getByText('L')).toBeInTheDocument();
  });

  it('offers all six positions so a missing one is greyed out, not absent', async () => {
    mount({}, [rackSlot(16), rackSlot(17), rackSlot(1)]);
    const [first] = await pickers();

    expect(first.querySelectorAll('option')).toHaveLength(6);
    const disabled = [...first.querySelectorAll('option')].filter((o) => (o as HTMLOptionElement).disabled);
    expect(disabled.map((o) => (o as HTMLOptionElement).value)).toEqual(['3', '4', '5', '6']);
  });

  it('disables a position holding the wrong nozzle', async () => {
    mount({}, [rackSlot(16), rackSlot(17, { nozzle_diameter: '0.6' }), rackSlot(1)]);
    const [first] = await pickers();

    const option = [...first.querySelectorAll('option')].find(
      (o) => (o as HTMLOptionElement).value === '2',
    ) as HTMLOptionElement;
    expect(option.disabled).toBe(true);
    expect(option.title).toBe('Holds a 0.6 HH01 nozzle; this filament needs 0.40 High Flow');
  });

  it('pre-selects the position already loaded with the group colour', async () => {
    // Reproduces BambuStudio's own pick for this plate — dispatched [16, 1, 17]
    // on 2026-08-14: red group to R1, blue group to R2.
    const coloured = [
      rackSlot(16, { filament_color: 'DE4343FF' }),
      rackSlot(17, { filament_color: '0078BFFF' }),
      ...[3, 4, 5, 6].map((p) => rackSlot(15 + p)),
      rackSlot(1),
    ];
    mount({}, coloured);
    const [red, blue] = await pickers();

    expect(red.value).toBe('1');
    expect(blue.value).toBe('2');
  });

  it('reports every group when one is changed, not just the edited one', async () => {
    // Sending only the edited group would let the dispatcher re-assign the
    // others around it and silently move a hotend the operator had accepted.
    const onChange = vi.fn();
    mount({ onNozzleRackChoiceChange: onChange });
    const [red] = await pickers();

    fireEvent.change(red, { target: { value: '4' } });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toEqual({ 1: 1, 2: 4 });
  });

  it('shows a saved pick rather than re-deriving one', async () => {
    mount({ nozzleRackChoice: { 2: 5, 1: 6 }, onNozzleRackChoiceChange: vi.fn() });
    const [red, blue] = await pickers();

    expect(red.value).toBe('5');
    expect(blue.value).toBe('6');
  });

  it('offers the mounted nozzle, which telemetry omits entirely', async () => {
    // #943: the firmware drops a rack id while that nozzle is on the carriage.
    // It is the likeliest pick of all — the last print left it there.
    const mounted = [
      ...[1, 3, 4, 5, 6].map((p) => rackSlot(15 + p)),
      rackSlot(1),
      rackSlot(0), // the rack carriage, holding position 2's nozzle
    ];
    mount({}, mounted);
    const [first] = await pickers();

    const option = [...first.querySelectorAll('option')].find(
      (o) => (o as HTMLOptionElement).value === '2',
    ) as HTMLOptionElement;
    expect(option.disabled).toBe(false);
  });

  it('renders no picker on a printer with no rack', async () => {
    // Two carriages and nothing at 16..21 -- an H2D, or an H2C mid-report.
    // The panel still maps filaments; it just offers no rack position, and
    // no L/R badge either, since this plate carries no nozzle_id.
    mount({}, [rackSlot(0), rackSlot(1)]);

    await waitFor(() => expect(screen.getAllByText(/PLA/).length).toBeGreaterThan(0));
    expect(screen.queryAllByLabelText('Rack position')).toHaveLength(0);
    expect(screen.queryByText('R')).not.toBeInTheDocument();
  });

  it('leaves a dual-nozzle plate on its L/R badges', async () => {
    // No group data at all — an H2D file. The pre-existing badge must survive.
    const h2dReqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 5, used_meters: 2, nozzle_id: 1 },
        { slot_id: 2, type: 'PLA', color: '#00FF00', used_grams: 5, used_meters: 2, nozzle_id: 0 },
      ],
    };
    mount({ filamentReqs: h2dReqs });

    await screen.findByText('L');
    expect(screen.getByText('R')).toBeInTheDocument();
    expect(screen.queryAllByLabelText('Rack position')).toHaveLength(0);
  });
});
