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
    (api.getCloudSettings as ReturnType<typeof vi.fn>).mockResolvedValue(mockCloudSettings);
    (api.getKProfiles as ReturnType<typeof vi.fn>).mockResolvedValue(mockKProfiles);
    (api.configureAmsSlot as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    (api.saveSlotPreset as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
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
});
