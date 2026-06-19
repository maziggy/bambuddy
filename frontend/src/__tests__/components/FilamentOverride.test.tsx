/**
 * Tests for the FilamentOverride component.
 *
 * FilamentOverride allows users to override the 3MF's original filament
 * choices with filaments available across printers of the selected model.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
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

      const select = screen.getByRole('combobox');
      const options = select.querySelectorAll('option');

      // 1 default "Original" option + 2 PLA options (not PETG)
      expect(options).toHaveLength(3);

      // Verify no PETG option values exist
      const optionValues = Array.from(options).map((o) => o.getAttribute('value'));
      expect(optionValues).not.toContain('PETG|#0000FF');
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

      const select = screen.getByRole('combobox');
      const options = select.querySelectorAll('option');

      // 1 default "Original" option + 3 PLA color options
      expect(options).toHaveLength(4);
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

      const select = screen.getByRole('combobox');
      const options = Array.from(select.querySelectorAll('option'));
      const optionTexts = options.map((o) => o.textContent);

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

      const select = screen.getByRole('combobox');
      const options = Array.from(select.querySelectorAll('option'));
      // Non-default option should show "PLA" as the type fallback
      const nonDefaultOptions = options.filter((o) => o.getAttribute('value') !== '');
      expect(nonDefaultOptions[0].textContent).toContain('PLA');
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

      const select = screen.getByRole('combobox');
      const options = select.querySelectorAll('option');

      // 1 default + 1 PLA with extruder_id=0 (extruder_id=1 is filtered out)
      expect(options).toHaveLength(2);

      const optionValues = Array.from(options).map((o) => o.getAttribute('value'));
      expect(optionValues).toContain('PLA|#FF0000');
      expect(optionValues).not.toContain('PLA|#00FF00');
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

      const select = screen.getByRole('combobox');
      const options = select.querySelectorAll('option');

      // 1 default + 2 PLA options (no nozzle filtering)
      expect(options).toHaveLength(3);
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

      const select = screen.getByRole('combobox');
      const options = select.querySelectorAll('option');

      // 1 default + extruder_id=0 + extruder_id=null (extruder_id=1 filtered out)
      expect(options).toHaveLength(3);

      const optionValues = Array.from(options).map((o) => o.getAttribute('value'));
      expect(optionValues).toContain('PLA|#FF0000');
      expect(optionValues).toContain('PLA|#00FF00');
      expect(optionValues).not.toContain('PLA|#FFFFFF');
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

      const select = screen.getByRole('combobox');
      fireEvent.change(select, { target: { value: 'PLA|#00FF00' } });

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

      const select = screen.getByRole('combobox');
      fireEvent.change(select, { target: { value: '' } });

      expect(mockOnChange).toHaveBeenCalledWith({});
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

      // Wait for the queries to resolve and the resolved label to land in
      // the dropdown's "original" placeholder option. The tooltip on the
      // color swatch carries the same text, so we scope to the option to
      // avoid the multi-match.
      await waitFor(() => {
        const select = screen.getByRole('combobox');
        const placeholder = select.querySelector('option[value=""]');
        expect(placeholder?.textContent).toMatch(/Bambu PLA Matte/);
      });
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

      await waitFor(() => {
        const select = screen.getByRole('combobox');
        const placeholder = select.querySelector('option[value=""]');
        expect(placeholder?.textContent).toMatch(/My House PLA/);
      });
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

      await waitFor(() => {
        const select = screen.getByRole('combobox');
        const placeholder = select.querySelector('option[value=""]');
        expect(placeholder?.textContent).toMatch(/Bambu PLA Matte \(Charcoal\)/);
      });
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
        const selects = screen.getAllByRole('combobox');
        expect(selects).toHaveLength(2);
        expect(selects[0].querySelector('option[value=""]')?.textContent).toMatch(/Bambu PLA Matte \(Charcoal\)/);
        expect(selects[1].querySelector('option[value=""]')?.textContent).toMatch(/Bambu PLA Basic \(Black\)/);
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
        const select = screen.getByRole('combobox');
        const placeholder = select.querySelector('option[value=""]');
        expect(placeholder?.textContent).toMatch(/Bambu PLA Matte/);
        expect(placeholder?.textContent).not.toMatch(/null/);
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
      const select = screen.getByRole('combobox');
      const placeholder = select.querySelector('option[value=""]');
      expect(placeholder?.textContent).toMatch(/PLA \(/);
    });
  });
});
