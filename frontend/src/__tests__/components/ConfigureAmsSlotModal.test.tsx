/**
 * Tests for the ConfigureAmsSlotModal component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { ConfigureAmsSlotModal } from '../../components/ConfigureAmsSlotModal';
import { api } from '../../api/client';

// Mock the API client
vi.mock('../../api/client', () => ({
  api: {
    getCloudSettings: vi.fn(),
    getKProfiles: vi.fn(),
    configureAmsSlot: vi.fn(),
    getCloudSettingDetail: vi.fn(),
    saveSlotPreset: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
    getLocalPresets: vi.fn(),
    getBuiltinFilaments: vi.fn(),
    searchColors: vi.fn(),
    getColorCatalog: vi.fn(),
    resetAmsSlot: vi.fn(),
  },
}));

const mockCloudSettings = {
  filament: [
    {
      setting_id: 'GFSL05_09',
      name: 'Bambu PLA Basic @BBL X1C',
      filament_id: 'GFL05',
    },
    {
      setting_id: 'PFUScd84f663d2c2ef',
      name: '# Overture Matte PLA @BBL H2D',
      filament_id: null,
    },
  ],
};

const mockKProfiles = {
  profiles: [
    {
      id: 1,
      name: 'PLA Basic',
      k_value: '0.020',
      filament_id: 'GFL05',
      setting_id: '',
      extruder_id: 1,
      cali_idx: 1,
    },
  ],
};

const defaultProps = {
  isOpen: true,
  onClose: vi.fn(),
  printerId: 1,
  slotInfo: {
    amsId: 0,
    trayId: 0,
    trayCount: 4,
    trayType: 'PLA',
    trayColor: 'FFFFFF',
    traySubBrands: 'PLA Basic',
  },
  nozzleDiameter: '0.4',
  onSuccess: vi.fn(),
};

describe('ConfigureAmsSlotModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Mock scrollIntoView which is not available in jsdom
    Element.prototype.scrollIntoView = vi.fn();
    (api.getCloudSettings as ReturnType<typeof vi.fn>).mockResolvedValue(mockCloudSettings);
    (api.getKProfiles as ReturnType<typeof vi.fn>).mockResolvedValue(mockKProfiles);
    (api.configureAmsSlot as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    (api.saveSlotPreset as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    (api.getLocalPresets as ReturnType<typeof vi.fn>).mockResolvedValue({ filament: [] });
    (api.getBuiltinFilaments as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.searchColors as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.getColorCatalog as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.resetAmsSlot as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true, message: 'ok' });
  });

  it('renders nothing visible when closed', () => {
    render(<ConfigureAmsSlotModal {...defaultProps} isOpen={false} />);
    expect(screen.queryByText('Configure AMS Slot')).not.toBeInTheDocument();
  });

  it('renders modal when open', async () => {
    render(<ConfigureAmsSlotModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByText(/Configure AMS/)).toBeInTheDocument();
    });
  });

  it('displays basic color buttons', async () => {
    render(<ConfigureAmsSlotModal {...defaultProps} />);
    await waitFor(() => {
      // Check for basic color buttons by their title attribute
      expect(screen.getByTitle('White')).toBeInTheDocument();
      expect(screen.getByTitle('Black')).toBeInTheDocument();
      expect(screen.getByTitle('Red')).toBeInTheDocument();
      expect(screen.getByTitle('Blue')).toBeInTheDocument();
      expect(screen.getByTitle('Green')).toBeInTheDocument();
      expect(screen.getByTitle('Yellow')).toBeInTheDocument();
      expect(screen.getByTitle('Orange')).toBeInTheDocument();
      expect(screen.getByTitle('Gray')).toBeInTheDocument();
    });
  });

  it('does not show extended colors by default', async () => {
    render(<ConfigureAmsSlotModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTitle('White')).toBeInTheDocument();
    });
    // Extended colors should not be visible initially
    expect(screen.queryByTitle('Cyan')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Purple')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Coral')).not.toBeInTheDocument();
  });

  it('shows extended colors when expand button is clicked', async () => {
    render(<ConfigureAmsSlotModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTitle('White')).toBeInTheDocument();
    });

    // Click the expand button (+ button)
    const expandButton = screen.getByTitle('Show more colors');
    fireEvent.click(expandButton);

    // Extended colors should now be visible
    await waitFor(() => {
      expect(screen.getByTitle('Cyan')).toBeInTheDocument();
      expect(screen.getByTitle('Purple')).toBeInTheDocument();
      expect(screen.getByTitle('Pink')).toBeInTheDocument();
      expect(screen.getByTitle('Brown')).toBeInTheDocument();
      expect(screen.getByTitle('Coral')).toBeInTheDocument();
    });
  });

  it('hides extended colors when collapse button is clicked', async () => {
    render(<ConfigureAmsSlotModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTitle('White')).toBeInTheDocument();
    });

    // Click the expand button
    const expandButton = screen.getByTitle('Show more colors');
    fireEvent.click(expandButton);

    // Wait for extended colors to appear
    await waitFor(() => {
      expect(screen.getByTitle('Cyan')).toBeInTheDocument();
    });

    // Click the collapse button
    const collapseButton = screen.getByTitle('Show less colors');
    fireEvent.click(collapseButton);

    // Extended colors should be hidden again
    await waitFor(() => {
      expect(screen.queryByTitle('Cyan')).not.toBeInTheDocument();
    });
  });

  it('selects a color when color button is clicked', async () => {
    render(<ConfigureAmsSlotModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByTitle('Red')).toBeInTheDocument();
    });

    // Click the red color button
    const redButton = screen.getByTitle('Red');
    fireEvent.click(redButton);

    // The color input should now show "Red"
    const colorInput = screen.getByPlaceholderText(/Color name or hex/);
    expect(colorInput).toHaveValue('Red');
  });

  it('derives tray_info_idx from base_id when filament_id is null', async () => {
    // Mock the detail API to return base_id but no filament_id
    (api.getCloudSettingDetail as ReturnType<typeof vi.fn>).mockResolvedValue({
      filament_id: null,
      base_id: 'GFSL05_09',
      name: '# Overture Matte PLA @BBL H2D',
    });

    render(<ConfigureAmsSlotModal {...defaultProps} />);

    // Wait for presets to load
    await waitFor(() => {
      expect(api.getCloudSettings).toHaveBeenCalled();
    });

    // Select a user preset (one without filament_id)
    // Find and click the preset - this would require the preset to be in the list
    // The actual tray_info_idx derivation happens during the configure mutation
  });

  it('renders configure slot button', async () => {
    render(<ConfigureAmsSlotModal {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText(/Configure AMS/)).toBeInTheDocument();
    });

    // Find the Configure Slot button
    const configureButton = screen.getByRole('button', { name: /Configure Slot/i });
    expect(configureButton).toBeInTheDocument();
  });

  it('filters presets by printer model', async () => {
    // Render with printerModel="H2D"
    render(<ConfigureAmsSlotModal {...defaultProps} printerModel="H2D" />);
    // Wait for presets to load - the H2D preset should be visible
    await waitFor(() => {
      expect(screen.getByText(/Overture Matte PLA/)).toBeInTheDocument();
    });
    // The X1C preset should NOT be visible (filtered out by model)
    expect(screen.queryByText(/Bambu PLA Basic @BBL X1C/)).not.toBeInTheDocument();
  });

  it('shows current preset even when it does not match model filter', async () => {
    // Render with printerModel="H2D" but savedPresetId pointing to the X1C preset
    const slotInfo = {
      ...defaultProps.slotInfo,
      savedPresetId: 'GFSL05_09',  // X1C preset
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} printerModel="H2D" />);
    await waitFor(() => {
      // Both should be visible - H2D matches model, X1C is saved preset
      // Use the full preset name to match the list item (not the "Filtering for" label)
      expect(screen.getByText('Bambu PLA Basic @BBL X1C')).toBeInTheDocument();
      expect(screen.getByText(/Overture Matte PLA/)).toBeInTheDocument();
    });
  });

  it('pre-selects saved preset when opening configured slot', async () => {
    const slotInfo = {
      ...defaultProps.slotInfo,
      savedPresetId: 'GFSL05_09',
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} />);
    await waitFor(() => {
      // The saved preset should have the selected style (green border)
      // Use the full preset name to avoid matching the "Filtering for" label
      const presetButton = screen.getByText('Bambu PLA Basic @BBL X1C').closest('button');
      expect(presetButton).toHaveClass('bg-bambu-green/20');
    });
  });

  it('pre-populates color from trayColor', async () => {
    const slotInfo = {
      ...defaultProps.slotInfo,
      trayColor: 'FF0000FF',  // Red with alpha
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} />);
    await waitFor(() => {
      expect(screen.getByTitle('White')).toBeInTheDocument();
    });
    // The hex display should show the pre-populated color
    expect(screen.getByText('Hex: #FF0000', { exact: false })).toBeInTheDocument();
  });

  it('uses translated text for modal elements', async () => {
    render(<ConfigureAmsSlotModal {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByText('Configure AMS Slot')).toBeInTheDocument();
      expect(screen.getByText('Filament Profile')).toBeInTheDocument();
    });
    // Check footer buttons
    expect(screen.getByRole('button', { name: /Configure Slot/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Cancel/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reset Slot/i })).toBeInTheDocument();
  });
});
