/**
 * Tests for the FilamentMapping component's Filament Track Switch (FTS)
 * handling (#1162).
 *
 * The FTS accessory routes any AMS slot to either extruder dynamically. When
 * present (printer status `fila_switch.installed === true`), the per-extruder
 * dropdown filter must be suppressed — otherwise the print modal's filament
 * dropdown is empty since the printer reports info bits 8-11 = 0xE
 * (uninitialized) for every AMS unit.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, cleanup } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FilamentMapping } from '../../components/PrintModal/FilamentMapping';
import type { PrinterStatus } from '../../api/client';

const mockFilamentReqs = {
  filaments: [
    // Required filament asks for the LEFT extruder (nozzle_id=1).
    // Without FTS the dropdown filter would only allow slots with extruderId=1.
    { slot_id: 1, type: 'PETG', color: '#00FF00', used_grams: 25, used_meters: 8.5, nozzle_id: 1 },
  ],
};

function createStatus(overrides: Partial<PrinterStatus>): PrinterStatus {
  return {
    id: 1,
    name: 'X2D',
    connected: true,
    state: 'IDLE',
    ams: [
      {
        id: 0,
        // Realistic FTS-installed bundle: AMS reports extruder bits 8-11 = 0xE,
        // so ams_extruder_map ends up empty.
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FF0000', tray_info_idx: 'GFA00', tray_sub_brands: 'Bambu PLA' },
          { id: 1, tray_type: 'PETG', tray_color: '00FF00', tray_info_idx: 'GFG00', tray_sub_brands: 'Bambu PETG' },
        ],
      },
    ],
    vt_tray: [],
    ams_extruder_map: {},
    ...overrides,
  } as PrinterStatus;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('FilamentMapping — FTS routing', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/:id/spool-assignments', () => HttpResponse.json([])),
    );
  });

  it('shows all loaded slots in the dropdown when FTS is installed', async () => {
    server.use(
      http.get(
        '/api/v1/printers/:id/status',
        () =>
          HttpResponse.json(
            createStatus({
              fila_switch: {
                installed: true,
                in_slots: [-1, 1],
                out_extruders: [0, 1],
                stat: 0,
                info: 2,
              },
            }),
          ),
      ),
    );

    render(
      <FilamentMapping
        printerId={1}
        filamentReqs={mockFilamentReqs}
        manualMappings={{}}
        onManualMappingChange={() => {}}
        currencySymbol="$"
        defaultCostPerKg={0}
        defaultExpanded
      />,
    );

    // Both PLA and PETG slots must appear in the dropdown despite ams_extruder_map
    // being empty and the requirement asking for nozzle 1. Without the FTS guard
    // the dropdown would render only the "-- Select slot --" placeholder.
    await waitFor(() => {
      expect(screen.getByText(/Bambu PLA/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Bambu PETG/)).toBeInTheDocument();

    // The slot currently fed into a track gets an [L]/[R] badge. AMS-0 slot 1
    // (global tray ID 1) is in fila_switch.in_slots[1], whose track terminates
    // at extruder 1 → the LEFT-nozzle short label appears in that option.
    const petgOption = screen.getByText(/Bambu PETG/);
    expect(petgOption.textContent).toMatch(/\[L\]/);

    // AMS-0 slot 0 (global tray ID 0) is NOT currently fed into any track —
    // FTS routes it on demand, so no badge.
    const plaOption = screen.getByText(/Bambu PLA/);
    expect(plaOption.textContent).not.toMatch(/\[[LR]\]/);
  });

  it('still applies the per-nozzle filter when FTS is null', async () => {
    server.use(
      http.get(
        '/api/v1/printers/:id/status',
        () =>
          HttpResponse.json(
            createStatus({
              fila_switch: null,
              ams_extruder_map: { '0': 0 },  // AMS 0 → right nozzle (extruder 0)
            }),
          ),
      ),
    );

    render(
      <FilamentMapping
        printerId={1}
        filamentReqs={mockFilamentReqs}
        manualMappings={{}}
        onManualMappingChange={() => {}}
        currencySymbol="$"
        defaultCostPerKg={0}
        defaultExpanded
      />,
    );

    // Required nozzle is 1 (LEFT) but AMS 0 is on extruder 0 (RIGHT) — neither
    // slot should appear in the dropdown.
    await waitFor(() => {
      // Wait for component to render — the slot label should NOT be present
      expect(screen.queryByText(/Bambu PLA/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Bambu PETG/)).not.toBeInTheDocument();
    });
  });
});
