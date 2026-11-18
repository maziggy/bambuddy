/**
 * Tests for the FilamentOverride component.
 *
 * FilamentOverride allows users to override the 3MF's original filament
 * choices with filaments available across printers of the selected model.
 */

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';
import { FilamentOverride } from '../../components/PrintModal/FilamentOverride';
import type { FilamentReqsData } from '../../components/PrintModal/types';

const defaultFilamentReqs: FilamentReqsData = {
  filaments: [
    { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5 },
  ],
};

const defaultAvailable = [
  { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic', extruder_id: null },
  { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', tray_sub_brands: 'PLA Basic', extruder_id: null },
  { type: 'PETG', color: '#0000FF', tray_info_idx: 'GFG00', tray_sub_brands: 'PETG Basic', extruder_id: null },
];

const mockOnChange = vi.fn();

/**
 * Open an override picker and hand back its rows.
 *
 * These assertions used to read `select.querySelectorAll('option')`. The
 * control gained a swatch per choice (#3159), which an `<option>` cannot
 * render, so it is a listbox now: its rows exist only while it is open, and
 * each carries its identity on `data-value` the way `<option value>` did.
 */
function openPicker(index = 0): HTMLElement[] {
  fireEvent.click(screen.getAllByRole('combobox')[index]);
  return within(screen.getByRole('listbox')).getAllByRole('option');
}

/** The identities the picker offers, in order — the old `option.value` list. */
function optionValues(index = 0): (string | null)[] {
  return openPicker(index).map((o) => o.getAttribute('data-value'));
}

/**
 * What the closed control reads as. With nothing overridden this is the
 * "original filament" row, which is where the #1718 label resolution lands —
 * previously read off `option[value=""]`.
 */
function triggerText(index = 0): string {
  return screen.getAllByRole('combobox')[index].textContent ?? '';
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('FilamentOverride', () => {
  describe('rendering', () => {
    it('returns null when filamentReqs is undefined', () => {
      render(
        <FilamentOverride
          filamentReqs={undefined}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      expect(screen.queryByText('Filament Override')).not.toBeInTheDocument();
    });

    it('returns null when filaments array is empty', () => {
      render(
        <FilamentOverride
          filamentReqs={{ filaments: [] }}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      expect(screen.queryByText('Filament Override')).not.toBeInTheDocument();
    });

    it('returns null when availableFilaments is empty', () => {
      render(
        <FilamentOverride
          filamentReqs={defaultFilamentReqs}
          availableFilaments={[]}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      expect(screen.queryByText('Filament Override')).not.toBeInTheDocument();
    });

    it('renders filament slot with type and grams', () => {
      render(
        <FilamentOverride
          filamentReqs={defaultFilamentReqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      // The grams text "(25g)" is in a nested span within the type label
      expect(screen.getByText('(25g)')).toBeInTheDocument();
      // "Filament Override" heading confirms the section renders
      expect(screen.getByText('Filament Override')).toBeInTheDocument();
    });

    it('renders override dropdown for each slot', () => {
      const twoSlotReqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5 },
          { slot_id: 2, type: 'PLA', color: '#00FF00', used_grams: 10, used_meters: 3.2 },
        ],
      };

      render(
        <FilamentOverride
          filamentReqs={twoSlotReqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      const selects = screen.getAllByRole('combobox');
      expect(selects).toHaveLength(2);
    });
  });

  describe('type filtering', () => {
    it('only shows same-type filaments in dropdown', () => {
      render(
        <FilamentOverride
          filamentReqs={defaultFilamentReqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      // 1 default "Original" row + 2 PLA rows (not PETG)
      const values = optionValues();
      expect(values).toHaveLength(3);
      expect(values).not.toContain('PETG|#0000FF');
    });

    it('shows all same-type options regardless of color', () => {
      const threeColorAvailable = [
        { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic', extruder_id: null },
        { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', tray_sub_brands: 'PLA Basic', extruder_id: null },
        { type: 'PLA', color: '#FFFFFF', tray_info_idx: 'GFA02', tray_sub_brands: 'PLA Basic', extruder_id: null },
      ];

      render(
        <FilamentOverride
          filamentReqs={defaultFilamentReqs}
          availableFilaments={threeColorAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      // 1 default "Original" row + 3 PLA colour rows
      expect(optionValues()).toHaveLength(4);
    });
  });

  describe('subtype display', () => {
    it('shows tray_sub_brands in dropdown options when available', () => {
      const subtypeAvailable = [
        { type: 'PLA', color: '#000000', tray_info_idx: 'GFL99', tray_sub_brands: 'PLA Basic', extruder_id: null },
        { type: 'PLA', color: '#000000', tray_info_idx: 'GFL05', tray_sub_brands: 'PLA Matte', extruder_id: null },
      ];

      render(
        <FilamentOverride
          filamentReqs={defaultFilamentReqs}
          availableFilaments={subtypeAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      const optionTexts = openPicker().map((o) => o.textContent);

      // Should show "PLA Basic" and "PLA Matte", not just "PLA"
      expect(optionTexts.some((t) => t?.includes('PLA Basic'))).toBe(true);
      expect(optionTexts.some((t) => t?.includes('PLA Matte'))).toBe(true);
    });

    it('falls back to type when tray_sub_brands is empty', () => {
      const noSubtypeAvailable = [
        { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', tray_sub_brands: '', extruder_id: null },
      ];

      render(
        <FilamentOverride
          filamentReqs={defaultFilamentReqs}
          availableFilaments={noSubtypeAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      // Non-default row should show "PLA" as the type fallback
      const nonDefault = openPicker().filter((o) => o.getAttribute('data-value') !== '');
      expect(nonDefault[0].textContent).toContain('PLA');
    });
  });

  describe('nozzle filtering', () => {
    it('filters by extruder_id when nozzle_id is set', () => {
      const nozzleReqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5, nozzle_id: 0 },
        ],
      };

      const dualExtruderAvailable = [
        { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic', extruder_id: 0 },
        { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', tray_sub_brands: 'PLA Basic', extruder_id: 1 },
      ];

      render(
        <FilamentOverride
          filamentReqs={nozzleReqs}
          availableFilaments={dualExtruderAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      // 1 default + 1 PLA with extruder_id=0 (extruder_id=1 is filtered out)
      const values = optionValues();
      expect(values).toHaveLength(2);
      expect(values).toContain('PLA|#FF0000');
      expect(values).not.toContain('PLA|#00FF00');
    });

    it('shows all filaments when nozzle_id is undefined', () => {
      const noNozzleReqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5 },
        ],
      };

      const mixedExtruderAvailable = [
        { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic', extruder_id: 0 },
        { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', tray_sub_brands: 'PLA Basic', extruder_id: 1 },
      ];

      render(
        <FilamentOverride
          filamentReqs={noNozzleReqs}
          availableFilaments={mixedExtruderAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      // 1 default + 2 PLA rows (no nozzle filtering)
      expect(optionValues()).toHaveLength(3);
    });

    it('includes filaments with null extruder_id', () => {
      const nozzleReqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5, nozzle_id: 0 },
        ],
      };

      const mixedAvailable = [
        { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic', extruder_id: 0 },
        { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', tray_sub_brands: 'PLA Basic', extruder_id: null },
        { type: 'PLA', color: '#FFFFFF', tray_info_idx: 'GFA02', tray_sub_brands: 'PLA Basic', extruder_id: 1 },
      ];

      render(
        <FilamentOverride
          filamentReqs={nozzleReqs}
          availableFilaments={mixedAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      // 1 default + extruder_id=0 + extruder_id=null (extruder_id=1 filtered out)
      const values = optionValues();
      expect(values).toHaveLength(3);
      expect(values).toContain('PLA|#FF0000');
      expect(values).toContain('PLA|#00FF00');
      expect(values).not.toContain('PLA|#FFFFFF');
    });
  });

  describe('interactions', () => {
    it('calls onChange when selecting an override', () => {
      render(
        <FilamentOverride
          filamentReqs={defaultFilamentReqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      const row = openPicker().find((o) => o.getAttribute('data-value') === 'PLA|#00FF00');
      fireEvent.click(row as HTMLElement);

      expect(mockOnChange).toHaveBeenCalledWith({
        1: { type: 'PLA', color: '#00FF00' },
      });
    });

    it('calls onChange to remove override when selecting original', () => {
      const activeOverrides = {
        1: { type: 'PLA', color: '#00FF00' },
      };

      render(
        <FilamentOverride
          filamentReqs={defaultFilamentReqs}
          availableFilaments={defaultAvailable}
          overrides={activeOverrides}
          onChange={mockOnChange}
        />
      );

      const row = openPicker().find((o) => o.getAttribute('data-value') === '');
      fireEvent.click(row as HTMLElement);

      expect(mockOnChange).toHaveBeenCalledWith({});
    });
  });

  describe('colour swatches (#3159)', () => {
    it('draws the colour of every filament it offers, and of the original', () => {
      render(
        <FilamentOverride
          filamentReqs={defaultFilamentReqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      const rows = openPicker();
      // The original-filament row plus the two same-type candidates: model
      // mode pools across printers, so there is no slot binding behind an
      // entry and each swatch is the plain tray colour.
      expect(rows).toHaveLength(3);
      for (const row of rows) {
        expect(within(row).getByTestId('filament-swatch')).toBeInTheDocument();
      }
    });

    it('prints each candidate hex and marks the one the slice asked for', () => {
      render(
        <FilamentOverride
          filamentReqs={defaultFilamentReqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />
      );

      const rows = openPicker();
      const listbox = screen.getByRole('listbox');
      expect(within(listbox).getByText('#00FF00')).toBeInTheDocument();
      // #FF0000 twice: the original-filament row states what the slice asked
      // for, and one candidate carries it.
      expect(within(listbox).getAllByText('#FF0000')).toHaveLength(2);

      // The marker belongs on the candidate, not on the original — it answers
      // "which of these is the colour the slice asked for", which is the
      // judgement the colour names cannot be trusted to make.
      const marked = rows.filter((r) => within(r).queryByTitle('Exact colour match'));
      expect(marked).toHaveLength(1);
      expect(marked[0].getAttribute('data-value')).toBe('PLA|#FF0000');
    });
  });

  describe('original-label SKU resolution (#1718)', () => {
    it('uses the builtin filament name when tray_info_idx maps to a known SKU', async () => {
      // Stamped by Bambu Studio when slicing with PLA Matte Charcoal: 3MF
      // carries type=PLA + the GFA01 SKU. Without resolution the label
      // collapses to "PLA (Black)" which was Sam's bug.
      server.use(
        http.get('/api/v1/cloud/builtin-filaments', () =>
          HttpResponse.json([{ filament_id: 'GFA01', name: 'Bambu PLA Matte' }]),
        ),
        http.get('/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
      );

      const reqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#1A1A1A', used_grams: 25, used_meters: 8.5, tray_info_idx: 'GFA01' },
        ],
      };

      render(
        <FilamentOverride
          filamentReqs={reqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />,
      );

      // Wait for the queries to resolve and the resolved label to land on the
      // control, which reads as the "original filament" row while nothing is
      // overridden. Reading the closed trigger rather than the open list keeps
      // this clear of the swatch tooltip, which carries the same text.
      await waitFor(() => expect(triggerText()).toMatch(/Bambu PLA Matte/));
    });

    it('prefers the cloud user-preset name over the builtin entry for the same id', async () => {
      // Cloud user-preset names are more specific than the builtin fallback —
      // e.g. a user has renamed GFA00 to "My House PLA".
      server.use(
        http.get('/api/v1/cloud/builtin-filaments', () =>
          HttpResponse.json([{ filament_id: 'GFA00', name: 'Bambu PLA Basic' }]),
        ),
        http.get('/api/v1/cloud/filament-id-map', () =>
          HttpResponse.json({ GFA00: 'My House PLA' }),
        ),
      );

      const reqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5, tray_info_idx: 'GFA00' },
        ],
      };

      render(
        <FilamentOverride
          filamentReqs={reqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />,
      );

      await waitFor(() => expect(triggerText()).toMatch(/My House PLA/));
      // The builtin fallback must NOT bleed through anywhere — neither the
      // placeholder option nor the tooltip.
      expect(screen.queryByText(/Bambu PLA Basic/)).not.toBeInTheDocument();
    });

    it('uses the material-disambiguated catalogue color name (PLA Matte Charcoal — #1718 round 2)', async () => {
      // Sam's exact case: 3MF carries hex #000000 + tray_info_idx GFA01.
      // Without material context, /colors/map collapses #000000 to "Black"
      // (PLA Basic wins the priority race). The override panel must pass
      // the derived material hint "PLA Matte" through to /colors/by-material
      // so the user sees "Charcoal" — the actually-sliced color.
      server.use(
        http.get('/api/v1/cloud/builtin-filaments', () =>
          HttpResponse.json([{ filament_id: 'GFA01', name: 'Bambu PLA Matte' }]),
        ),
        http.get('/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
        http.get('/api/v1/inventory/colors/by-material', ({ request }) => {
          const url = new URL(request.url);
          const hex = url.searchParams.get('hex');
          const material = url.searchParams.get('material');
          if (hex === '#000000' && material === 'PLA Matte') {
            return HttpResponse.json({ color_name: 'Charcoal' });
          }
          return HttpResponse.json({ color_name: null });
        }),
      );

      const reqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#000000', used_grams: 25, used_meters: 8.5, tray_info_idx: 'GFA01' },
        ],
      };

      render(
        <FilamentOverride
          filamentReqs={reqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />,
      );

      await waitFor(() => expect(triggerText()).toMatch(/Bambu PLA Matte \(Charcoal\)/));
    });

    it('disambiguates per slot when two slots share a hex but differ in material', async () => {
      // Regression guard: the per-slot useQueries dispatch must key on
      // (hex, material) so a "PLA Matte Charcoal" slot does not adopt the
      // "PLA Basic Black" slot's answer.
      server.use(
        http.get('/api/v1/cloud/builtin-filaments', () =>
          HttpResponse.json([
            { filament_id: 'GFA00', name: 'Bambu PLA Basic' },
            { filament_id: 'GFA01', name: 'Bambu PLA Matte' },
          ]),
        ),
        http.get('/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
        http.get('/api/v1/inventory/colors/by-material', ({ request }) => {
          const url = new URL(request.url);
          const material = url.searchParams.get('material');
          if (material === 'PLA Matte') return HttpResponse.json({ color_name: 'Charcoal' });
          if (material === 'PLA Basic') return HttpResponse.json({ color_name: 'Black' });
          return HttpResponse.json({ color_name: null });
        }),
      );

      const reqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#000000', used_grams: 25, used_meters: 8.5, tray_info_idx: 'GFA01' },
          { slot_id: 2, type: 'PLA', color: '#000000', used_grams: 10, used_meters: 3.2, tray_info_idx: 'GFA00' },
        ],
      };

      render(
        <FilamentOverride
          filamentReqs={reqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />,
      );

      await waitFor(() => {
        expect(screen.getAllByRole('combobox')).toHaveLength(2);
        expect(triggerText(0)).toMatch(/Bambu PLA Matte \(Charcoal\)/);
        expect(triggerText(1)).toMatch(/Bambu PLA Basic \(Black\)/);
      });
    });

    it('falls back to getColorName(hex) when the by-material lookup returns null', async () => {
      // Any time the catalogue has no entry for the hex (or the endpoint is
      // unreachable), the placeholder must still render — the HSL-bucket
      // fallback is strictly better than a blank.
      server.use(
        http.get('/api/v1/cloud/builtin-filaments', () =>
          HttpResponse.json([{ filament_id: 'GFA01', name: 'Bambu PLA Matte' }]),
        ),
        http.get('/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
        http.get('/api/v1/inventory/colors/by-material', () =>
          HttpResponse.json({ color_name: null }),
        ),
      );

      const reqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5, tray_info_idx: 'GFA01' },
        ],
      };

      render(
        <FilamentOverride
          filamentReqs={reqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />,
      );

      // Wait for the builtin lookup to land so we know the row mounted; the
      // colour fallback to getColorName for #FF0000 produces "Red"-shaped text.
      await waitFor(() => {
        expect(triggerText()).toMatch(/Bambu PLA Matte/);
        expect(triggerText()).not.toMatch(/null/);
      });
    });

    it('falls back to the raw type when the SKU is unknown to both maps', async () => {
      // Unknown ids must not break rendering — the original "PLA" label is
      // still better than a blank.
      server.use(
        http.get('/api/v1/cloud/builtin-filaments', () => HttpResponse.json([])),
        http.get('/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
      );

      const reqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5, tray_info_idx: 'GFXXX' },
        ],
      };

      render(
        <FilamentOverride
          filamentReqs={reqs}
          availableFilaments={defaultAvailable}
          overrides={{}}
          onChange={mockOnChange}
        />,
      );

      // (25g) is the easiest signal the row mounted at all; once it's there,
      // assert the placeholder option carries the raw type.
      await waitFor(() => {
        expect(screen.getByText('(25g)')).toBeInTheDocument();
      });
      expect(triggerText()).toMatch(/PLA \(/);
    });
  });
});

describe('FilamentOverride — filament rules of the user\'s group (#3522)', () => {
  beforeEach(() => {
    setAuthToken('test-token', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () =>
        HttpResponse.json({
          id: 2,
          username: 'student',
          is_admin: false,
          permissions: [],
          filament_policies: [
            { group: 'Students', mode: 'block', rules: [{ match: 'filament_id', value: 'GFG03' }] },
          ],
        }),
      ),
    );
  });

  afterEach(() => {
    setAuthToken(null);
  });

  it('lists a filament the group may not use, greyed out with the reason', async () => {
    render(
      <FilamentOverride
        filamentReqs={{ filaments: [{ slot_id: 1, type: 'PETG', color: '#FFFFFF', used_grams: 10, used_meters: 3 }] }}
        availableFilaments={[
          { type: 'PETG', color: '#000000', tray_info_idx: 'GFG03', tray_sub_brands: 'PETG Matte', extruder_id: null },
          { type: 'PETG', color: '#0000FF', tray_info_idx: 'GFG99', tray_sub_brands: 'PETG Other', extruder_id: null },
        ]}
        overrides={{}}
        onChange={mockOnChange}
      />,
    );

    fireEvent.click(screen.getByRole('combobox'));
    const listbox = await screen.findByRole('listbox');
    // The policies arrive with /auth/me, after the first render.
    await waitFor(() =>
      expect(within(listbox).getByRole('option', { name: /PETG Matte/ })).toHaveAttribute('aria-disabled', 'true'),
    );
    const blocked = within(listbox).getByRole('option', { name: /PETG Matte/ });
    expect(blocked).toHaveAttribute('title', "Your group can't use PETG (GFG03)");
    expect(within(listbox).getByRole('option', { name: /PETG Other/ })).not.toHaveAttribute('aria-disabled');

    fireEvent.click(blocked);
    expect(mockOnChange).not.toHaveBeenCalled();
  });

  it('greys out an inventory filament the group may not use (#3590)', async () => {
    render(
      <FilamentOverride
        filamentReqs={{ filaments: [{ slot_id: 1, type: 'PETG', color: '#FFFFFF', used_grams: 10, used_meters: 3 }] }}
        availableFilaments={[
          { type: 'PETG', color: '#0000FF', tray_info_idx: 'GFG99', tray_sub_brands: 'PETG Other', extruder_id: null },
        ]}
        inventoryFilaments={[
          {
            type: 'PETG',
            color: '#000000',
            tray_info_idx: 'GFG03',
            subtype: 'Matte',
            color_name: 'Black',
            rgba: '000000FF',
            extra_colors: null,
            effect_type: null,
            spool_count: 1,
            remaining_grams: 800,
          },
        ]}
        inventoryEnabled
        overrides={{}}
        onChange={mockOnChange}
      />,
    );

    fireEvent.click(screen.getByRole('combobox'));
    const listbox = await screen.findByRole('listbox');
    await waitFor(() =>
      expect(within(listbox).getByRole('option', { name: /Matte PETG/ })).toHaveAttribute('aria-disabled', 'true'),
    );
    fireEvent.click(within(listbox).getByRole('option', { name: /Matte PETG/ }));
    expect(mockOnChange).not.toHaveBeenCalled();
  });
});

describe('FilamentOverride inventory group (#3590)', () => {
  const inv = (over: Record<string, unknown> = {}) => ({
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
    ...over,
  });

  const renderInv = (
    inventory: ReturnType<typeof inv>[],
    extra: Partial<React.ComponentProps<typeof FilamentOverride>> = {},
  ) => {
    const onForce = vi.fn();
    render(
      <FilamentOverride
        filamentReqs={defaultFilamentReqs}
        availableFilaments={defaultAvailable}
        inventoryFilaments={inventory}
        inventoryEnabled
        overrides={{}}
        onChange={mockOnChange}
        onForceColorMatchChange={onForce}
        {...extra}
      />,
    );
    return { onForce };
  };

  it('offers the loaded and the inventory group with the stock hint', () => {
    renderInv([inv()]);
    const rows = openPicker();
    const texts = rows.map((r) => r.textContent ?? '');
    expect(rows.map((r) => r.getAttribute('data-value'))).toContain('PLA|#0000FF|GFA05');
    expect(texts.some((t) => t.includes('Matte PLA (Blue)') && t.includes('2 spools · 1.4 kg'))).toBe(true);
    expect(screen.getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toEqual([
      'Loaded now',
      'In inventory, not loaded',
    ]);
  });

  it('without the setting a stored inventory override still sits under "Saved with this job", and nothing else is grouped', () => {
    render(
      <FilamentOverride
        filamentReqs={defaultFilamentReqs}
        availableFilaments={defaultAvailable}
        inventoryFilaments={[]}
        overrides={{ 1: { type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05' } }}
        onChange={mockOnChange}
      />,
    );
    expect(optionValues()).toContain('PLA|#0000FF|GFA05');
    expect(screen.getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toEqual(['Saved with this job']);
  });

  it('draws the inventory checkbox only when asked to, and reports a change', () => {
    const onChange = vi.fn();
    const { unmount } = render(
      <FilamentOverride
        filamentReqs={defaultFilamentReqs}
        availableFilaments={defaultAvailable}
        overrides={{}}
        onChange={mockOnChange}
      />,
    );
    expect(screen.queryByRole('checkbox', { name: /show inventory colours/i })).not.toBeInTheDocument();
    unmount();
    render(
      <FilamentOverride
        filamentReqs={defaultFilamentReqs}
        availableFilaments={defaultAvailable}
        overrides={{}}
        onChange={mockOnChange}
        inventoryOption={{ checked: false, disabled: false, onChange }}
      />,
    );
    fireEvent.click(screen.getByRole('checkbox', { name: /show inventory colours/i }));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it('formats grams below a kilogram and the singular / none hints', () => {
    renderInv([
      inv({ color: '#111111', remaining_grams: 350, spool_count: 1, tray_info_idx: '' }),
      inv({ color: '#222222', remaining_grams: 0, spool_count: 0, tray_info_idx: '' }),
    ]);
    const texts = openPicker().map((r) => r.textContent ?? '');
    expect(texts.some((t) => t.includes('1 spool · 350 g'))).toBe(true);
    expect(texts.some((t) => t.includes('none left in inventory'))).toBe(true);
  });

  it('leaves out inventory entries whose type and colour are already loaded', () => {
    renderInv([inv({ color: '#FF0000', tray_info_idx: 'GFA09' }), inv()]);
    const values = optionValues();
    expect(values).not.toContain('PLA|#FF0000|GFA09');
    expect(values).toContain('PLA|#0000FF|GFA05');
  });

  it('only offers entries of the slot type and shows no headers without inventory rows', () => {
    renderInv([inv({ type: 'PETG' })]);
    openPicker();
    expect(screen.queryAllByRole('group')).toHaveLength(0);
  });

  it('shows the panel when nothing is loaded but the inventory has entries', () => {
    renderInv([inv()], { availableFilaments: [] });
    expect(screen.getByText('Filament Override')).toBeInTheDocument();
    expect(optionValues()).toContain('PLA|#0000FF|GFA05');
  });

  it('is hidden when neither loaded nor inventory entries exist', () => {
    renderInv([], { availableFilaments: [] });
    expect(screen.queryByText('Filament Override')).not.toBeInTheDocument();
  });

  it('picking an inventory entry carries the variant and turns force colour on', () => {
    const { onForce } = renderInv([inv()]);
    fireEvent.click(openPicker().find((r) => r.getAttribute('data-value') === 'PLA|#0000FF|GFA05')!);
    expect(mockOnChange).toHaveBeenCalledWith({
      1: { type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05', fromInventory: true },
    });
    expect(onForce).toHaveBeenCalledWith(1, true, true);
  });

  it('an inventory entry without a variant sends no idx', () => {
    renderInv([inv({ tray_info_idx: '' })]);
    fireEvent.click(openPicker().find((r) => r.getAttribute('data-value') === 'PLA|#0000FF|')!);
    expect(mockOnChange).toHaveBeenCalledWith({ 1: { type: 'PLA', color: '#0000FF', fromInventory: true } });
  });

  it('picking a loaded row neither sets an idx nor touches force colour', () => {
    const { onForce } = renderInv([inv()]);
    fireEvent.click(openPicker().find((r) => r.getAttribute('data-value') === 'PLA|#00FF00')!);
    expect(mockOnChange).toHaveBeenCalledWith({ 1: { type: 'PLA', color: '#00FF00' } });
    expect(onForce).not.toHaveBeenCalled();
  });

  it('shows the stored inventory override on the trigger', () => {
    renderInv([inv()], { overrides: { 1: { type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05' } } });
    expect(triggerText()).toContain('Matte PLA (Blue)');
  });

  it('still shows a stored override whose inventory entry is gone', () => {
    renderInv([], {
      availableFilaments: defaultAvailable,
      overrides: { 1: { type: 'PLA', color: '#123456', tray_info_idx: 'GFA05' } },
    });
    expect(triggerText()).toContain('PLA');
    expect(triggerText()).not.toContain('Original filament');
  });

  it('parses a legacy TYPE|COLOR loaded value as before', () => {
    renderInv([inv()], { overrides: { 1: { type: 'PLA', color: '#00FF00' } } });
    expect(triggerText()).toContain('PLA Basic');
  });

  it('warns when the stock is short for the job times the copies', () => {
    renderInv([inv({ remaining_grams: 60 })], {
      quantity: 3, // 25 g x 3 = 75 g
      overrides: { 1: { type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05' } },
    });
    expect(screen.getByRole('alert').textContent).toContain('75 g');
    expect(screen.getByRole('alert').textContent).toContain('60 g');
  });

  it('does not warn when there is enough', () => {
    renderInv([inv({ remaining_grams: 100 })], {
      quantity: 3,
      overrides: { 1: { type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05' } },
    });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('says only that none is left when the colour is not forced', () => {
    renderInv([inv({ remaining_grams: 0, spool_count: 0 })], {
      overrides: { 1: { type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05' } },
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/None of this filament is left/);
    expect(text).not.toMatch(/wait/);
  });

  it('adds that the job will wait when none is left and the colour is forced', () => {
    renderInv([inv({ remaining_grams: 0, spool_count: 0 })], {
      forceColorMatch: { 1: true },
      overrides: { 1: { type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05' } },
    });
    expect(screen.getByRole('alert').textContent).toMatch(/None of this filament is left.*will wait/);
  });

  it('shows only the spool count, and no warning, when the weight is unknown', () => {
    renderInv([inv({ remaining_grams: 0, spool_count: 2, tray_info_idx: '' })], {
      quantity: 5,
      overrides: { 1: { type: 'PLA', color: '#0000FF' } },
    });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    const texts = openPicker().map((r) => r.textContent ?? '');
    expect(texts.some((t) => t.includes('2 spools') && !t.includes(' g') && !t.includes('kg'))).toBe(true);
  });

  it('shows "1 spool" for a single spool of unknown weight', () => {
    renderInv([inv({ remaining_grams: 0, spool_count: 1, tray_info_idx: '' })]);
    const texts = openPicker().map((r) => r.textContent ?? '');
    expect(texts.some((t) => t.includes('1 spool') && !t.includes('kg'))).toBe(true);
  });

  it('warns against the total demand handed in, not only this slot', () => {
    renderInv([inv({ remaining_grams: 60 })], {
      inventoryDemand: { 'PLA|#0000FF|GFA05': 90 },
      overrides: { 1: { type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05' } },
    });
    expect(screen.getByRole('alert').textContent).toContain('90 g');
  });

  it('shows the stored override on the picker when colours carry an alpha byte', () => {
    renderInv([inv({ color: '#0000FF' })], {
      overrides: { 1: { type: 'PLA', color: '#0000FFFF', tray_info_idx: 'GFA05' } },
    });
    expect(triggerText()).toContain('Matte PLA (Blue)');
  });

  it('lands a stored 8-digit colour on the loaded row, not the placeholder', () => {
    renderInv([], {
      availableFilaments: [{ type: 'PLA', color: '#00FF00FF', tray_info_idx: 'GFA01', tray_sub_brands: 'PLA Basic', extruder_id: null }],
      overrides: { 1: { type: 'pla', color: '#00ff00' } },
    });
    expect(triggerText()).toContain('PLA Basic');
    expect(triggerText()).not.toContain('Original filament');
  });

  it('puts a vanished stored override in its own group, without an inventory header', () => {
    renderInv([], {
      overrides: { 1: { type: 'PLA', color: '#123456' } },
    });
    openPicker();
    expect(screen.getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toEqual([
      'Loaded now',
      'Saved with this job',
    ]);
  });

  it('a stored variant never lands on a loaded row of another idx (#3590)', () => {
    renderInv([], {
      overrides: { 1: { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA09' } },
    });
    expect(triggerText()).not.toContain('PLA Basic');
    openPicker();
    expect(screen.getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toEqual([
      'Loaded now',
      'Saved with this job',
    ]);
  });

  it('a stored variant does not land on an idx-less inventory entry', () => {
    renderInv([inv({ tray_info_idx: '' })], {
      overrides: { 1: { type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05' } },
    });
    expect(triggerText()).not.toContain('Matte');
    openPicker();
    expect(screen.getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toContain('Saved with this job');
  });

  it('a stored 8-digit colour with a variant lands on the loaded row with that idx', () => {
    renderInv([], {
      availableFilaments: [{ type: 'PLA', color: '#00FF00FF', tray_info_idx: 'GFA01', tray_sub_brands: 'PLA Basic', extruder_id: null }],
      overrides: { 1: { type: 'PLA', color: '#00ff00ff', tray_info_idx: 'GFA01' } },
    });
    expect(triggerText()).toContain('PLA Basic');
  });

  it('releases an automatic force-colour tick on reset and on a loaded pick', () => {
    const onRelease = vi.fn();
    renderInv([inv()], {
      onAutoForceColorRelease: onRelease,
      overrides: { 1: { type: 'PLA', color: '#0000FF', tray_info_idx: 'GFA05' } },
    });
    fireEvent.click(screen.getByTitle('Reset to original'));
    expect(onRelease).toHaveBeenCalledWith(1);
    onRelease.mockClear();
    fireEvent.click(openPicker().find((r) => r.getAttribute('data-value') === 'PLA|#00FF00')!);
    expect(onRelease).toHaveBeenCalledWith(1);
  });

  it('does not tick again, or mark it automatic, when the box is already ticked', () => {
    const { onForce } = renderInv([inv()], { forceColorMatch: { 1: true } });
    fireEvent.click(openPicker().find((r) => r.getAttribute('data-value') === 'PLA|#0000FF|GFA05')!);
    expect(onForce).not.toHaveBeenCalled();
  });

  it('formats the stock hint in the UI language', async () => {
    const { default: i18n } = await import('../../i18n');
    await i18n.changeLanguage('de');
    try {
      renderInv([inv()]);
      expect(openPicker().some((r) => (r.textContent ?? '').includes('1,4 kg'))).toBe(true);
    } finally {
      await i18n.changeLanguage('en');
    }
  });

  it('shows only the loaded list without inventory entries (Spoolman mode)', () => {
    renderInv([]);
    openPicker();
    expect(screen.queryAllByRole('group')).toHaveLength(0);
  });
});
