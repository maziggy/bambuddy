/**
 * Tests for how a colour mismatch is explained in the Filament Mapping panel
 * (#2941).
 *
 * The report: "the color is Blue PLA, and it reports the selected filament does
 * not match when it's blue PLA as well." The comparison was right -- the slicer
 * profile asked for a near-pure `#0028FF` and the slot held Bambu's navy Blue
 * `#0A2989`, which differ by 118 in the blue channel alone -- but both hexes
 * resolve to the name "Blue", so the panel printed that name on either side of a
 * mismatch warning and gave the user nothing to reconcile it with.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor, cleanup } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FilamentMapping } from '../../components/PrintModal/FilamentMapping';
import type { PrinterStatus } from '../../api/client';

// The reporter's slicer profile: a pure-ish blue no pigment reaches.
const SLICER_BLUE = '#0028FF';
// Bambu PLA Basic "Blue" -- a dark navy, and what the AMS actually held.
const BAMBU_BLUE = '0A2989';

const blueReqs = {
  filaments: [{ slot_id: 1, type: 'PLA', color: SLICER_BLUE, used_grams: 18, used_meters: 6 }],
};

function statusWithTray(trayColor: string): PrinterStatus {
  return {
    id: 1,
    name: 'P1S-1',
    connected: true,
    state: 'IDLE',
    ams: [
      {
        id: 0,
        tray: [{ id: 3, tray_type: 'PLA', tray_color: trayColor, tray_info_idx: 'GFA00' }],
      },
    ],
    vt_tray: [],
    ams_extruder_map: {},
  } as PrinterStatus;
}

function renderMapping() {
  render(
    <FilamentMapping
      printerId={1}
      filamentReqs={blueReqs}
      manualMappings={{}}
      onManualMappingChange={() => {}}
      currencySymbol="$"
      defaultCostPerKg={0}
      defaultExpanded
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('FilamentMapping — explaining a colour mismatch', () => {
  it('names both colours by hex when they share a name', async () => {
    server.use(
      http.get('/api/v1/printers/:id/spool-assignments', () => HttpResponse.json([])),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(statusWithTray(BAMBU_BLUE))),
    );

    renderMapping();

    // Without the hex the tooltip read "Same type, different color" against two
    // labels both saying "Blue", which is the complaint in the report. Both
    // sides are now qualified, in one string, so the difference is readable
    // without the user having to compare two swatches by eye.
    const warning = await screen.findByTitle(/different color/i);
    const title = warning.getAttribute('title') ?? '';
    expect(title).toMatch(/Blue \(#0028FF\)/);
    expect(title).toMatch(/Blue \(#0A2989\)/);

    // The required-filament swatch carries the same qualified label, so the
    // two tooltips in the row cannot disagree about what was asked for.
    expect(screen.getByTitle(/^Required:/).getAttribute('title')).toMatch(/Blue \(#0028FF\)/);
  });

  it('still flags the mismatch in the panel header', async () => {
    server.use(
      http.get('/api/v1/printers/:id/spool-assignments', () => HttpResponse.json([])),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(statusWithTray(BAMBU_BLUE))),
    );

    renderMapping();

    // The warning itself is correct and stays -- this issue was about being
    // able to see why, not about silencing it.
    await waitFor(() => {
      expect(screen.getByText(/Color mismatch/)).toBeInTheDocument();
    });
  });

  it('reports a ready mapping when the slot really does hold that colour', async () => {
    server.use(
      http.get('/api/v1/printers/:id/spool-assignments', () => HttpResponse.json([])),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(statusWithTray('0028FF'))),
    );

    renderMapping();

    await waitFor(() => {
      expect(screen.getByText(/Ready/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Color mismatch/)).not.toBeInTheDocument();
  });
});
