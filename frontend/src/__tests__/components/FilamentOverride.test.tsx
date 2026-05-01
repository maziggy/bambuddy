/**
 * Tests for the FilamentOverride component.
 *
 * FilamentOverride allows users to override the 3MF's original filament
 * choices with filaments available across printers of the selected model.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, fireEvent, cleanup } from '@testing-library/react';
import { render } from '../utils';
import { FilamentOverride } from '../../components/PrintModal/FilamentOverride';
import type { FilamentReqsData } from '../../components/PrintModal/types';

const defaultFilamentReqs: FilamentReqsData = {
  filaments: [
    { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5 },
  ],
};

const defaultAvailable = [
  { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', extruder_id: null },
  { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', extruder_id: null },
  { type: 'PETG', color: '#0000FF', tray_info_idx: 'GFG00', extruder_id: null },
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
        { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', extruder_id: null },
        { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', extruder_id: null },
        { type: 'PLA', color: '#FFFFFF', tray_info_idx: 'GFA02', extruder_id: null },
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

  describe('nozzle filtering', () => {
    it('filters by extruder_id when nozzle_id is set', () => {
      const nozzleReqs: FilamentReqsData = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 25, used_meters: 8.5, nozzle_id: 0 },
        ],
      };

      const dualExtruderAvailable = [
        { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', extruder_id: 0 },
        { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', extruder_id: 1 },
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
        { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', extruder_id: 0 },
        { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', extruder_id: 1 },
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
        { type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00', extruder_id: 0 },
        { type: 'PLA', color: '#00FF00', tray_info_idx: 'GFA01', extruder_id: null },
        { type: 'PLA', color: '#FFFFFF', tray_info_idx: 'GFA02', extruder_id: 1 },
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
});
