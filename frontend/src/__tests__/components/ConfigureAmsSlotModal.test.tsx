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

  it('sends PFUS setting_id as tray_info_idx when cloud detail has filament_id: null (#1053)', async () => {
    // Cloud returns a user preset that inherits from a generic Bambu base and
    // has no distinct filament_id of its own — this is how Bambu Cloud responds
    // for custom presets built on top of "Generic ABS @BBL H2D" etc.
    (api.getCloudSettingDetail as ReturnType<typeof vi.fn>).mockResolvedValue({
      filament_id: null,
      base_id: 'GFSB99_07',
      name: '# Overture Matte PLA @BBL H2D',
    });

    const slotInfo = {
      ...defaultProps.slotInfo,
      savedPresetId: 'PFUScd84f663d2c2ef',
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} />);

    await waitFor(() => {
      expect(screen.getByText('# Overture Matte PLA @BBL H2D')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Configure Slot/i }));

    await waitFor(() => {
      expect(api.configureAmsSlot).toHaveBeenCalled();
    });

    const payload = (api.configureAmsSlot as ReturnType<typeof vi.fn>).mock.calls[0][3];
    // Before the fix, this collapsed to 'GFB99' (Generic ABS's filament_id),
    // which made OrcaSlicer/BambuStudio Sync Filaments resolve to "Generic ABS".
    expect(payload.tray_info_idx).toBe('PFUScd84f663d2c2ef');
    expect(payload.setting_id).toBe('PFUScd84f663d2c2ef');
  });

  it('uses cloud detail filament_id when present', async () => {
    (api.getCloudSettingDetail as ReturnType<typeof vi.fn>).mockResolvedValue({
      filament_id: 'P285e239',
      base_id: 'GFSB99_07',
      name: '# Overture Matte PLA @BBL H2D',
    });

    const slotInfo = {
      ...defaultProps.slotInfo,
      savedPresetId: 'PFUScd84f663d2c2ef',
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} />);

    await waitFor(() => {
      expect(screen.getByText('# Overture Matte PLA @BBL H2D')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Configure Slot/i }));

    await waitFor(() => {
      expect(api.configureAmsSlot).toHaveBeenCalled();
    });

    const payload = (api.configureAmsSlot as ReturnType<typeof vi.fn>).mock.calls[0][3];
    expect(payload.tray_info_idx).toBe('P285e239');
    expect(payload.setting_id).toBe('PFUScd84f663d2c2ef');
  });

  it('sends short GF filament_id for Bambu GFS* presets (cloud detail not consulted)', async () => {
    // Bambu-provided presets (GFS*) convert the setting_id → filament_id locally.
    // The cloud detail endpoint must NOT be consulted for them; the rewrite that
    // fixed #1053 preserves this pre-existing shortcut.
    const slotInfo = {
      ...defaultProps.slotInfo,
      savedPresetId: 'GFSL05_09',
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} />);

    await waitFor(() => {
      expect(screen.getByText('Bambu PLA Basic @BBL X1C')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Configure Slot/i }));

    await waitFor(() => {
      expect(api.configureAmsSlot).toHaveBeenCalled();
    });

    const payload = (api.configureAmsSlot as ReturnType<typeof vi.fn>).mock.calls[0][3];
    expect(payload.tray_info_idx).toBe('GFL05');
    expect(payload.setting_id).toBe('GFSL05_09');
    expect(api.getCloudSettingDetail).not.toHaveBeenCalled();
  });

  it('keeps default PFUS tray_info_idx when cloud detail fetch fails', async () => {
    // Network/5xx from /cloud/settings/{id} must not abort the configure flow
    // nor leave tray_info_idx empty — we fall back to the setting_id default.
    (api.getCloudSettingDetail as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('cloud unreachable')
    );

    const slotInfo = {
      ...defaultProps.slotInfo,
      savedPresetId: 'PFUScd84f663d2c2ef',
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} />);

    await waitFor(() => {
      expect(screen.getByText('# Overture Matte PLA @BBL H2D')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Configure Slot/i }));

    await waitFor(() => {
      expect(api.configureAmsSlot).toHaveBeenCalled();
    });

    const payload = (api.configureAmsSlot as ReturnType<typeof vi.fn>).mock.calls[0][3];
    expect(payload.tray_info_idx).toBe('PFUScd84f663d2c2ef');
    expect(payload.setting_id).toBe('PFUScd84f663d2c2ef');
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

  it('treats Bambu cloud rename @BBL A1M as a match for A1 Mini (#1649)', async () => {
    // Bambu cloud shifted A1 Mini filament profiles from
    // "Bambu PLA Basic @BBL A1 Mini ..." to the terse "@BBL A1M" mid-2026.
    // Without an alias-aware compare, the model filter strips every cloud
    // profile from the picker when the user selects an A1 Mini printer.
    (api.getCloudSettings as ReturnType<typeof vi.fn>).mockResolvedValue({
      filament: [
        { setting_id: 'GFA00_A1M', name: 'Bambu PLA Basic @BBL A1M', filament_id: 'GFA00' },
        { setting_id: 'GFA00_A1', name: 'Bambu PLA Basic @BBL A1', filament_id: 'GFA00' },
      ],
    });
    render(<ConfigureAmsSlotModal {...defaultProps} printerModel="A1 Mini" />);
    await waitFor(() => {
      expect(screen.getByText('Bambu PLA Basic @BBL A1M')).toBeInTheDocument();
    });
    // The A1 (non-mini) preset must still be filtered out — the alias
    // table must not collapse two physically distinct printers.
    expect(screen.queryByText('Bambu PLA Basic @BBL A1')).not.toBeInTheDocument();
  });

  it('still filters cross-model cloud profiles when the printer is A1 Mini', async () => {
    // Sanity check that the alias addition didn't accidentally widen the
    // matcher: an X1C cloud preset stays hidden when the picker is for an
    // A1 Mini printer.
    render(<ConfigureAmsSlotModal {...defaultProps} printerModel="A1 Mini" />);
    await waitFor(() => {
      expect(screen.getByText(/Configure AMS/)).toBeInTheDocument();
    });
    expect(screen.queryByText('Bambu PLA Basic @BBL X1C')).not.toBeInTheDocument();
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

  it('preset row expands inline on hover so the full name is readable (#1237)', async () => {
    // Long preset names (e.g. "SUNLU PETG GLOW IN THE DARK GEN2 @Bambu Lab H2C 0.4 nozzle")
    // get visually truncated; the row un-truncates on hover via group-hover so the
    // nozzle suffix is readable without waiting on the browser's title-tooltip delay,
    // and the title attribute remains as a fallback for assistive tech / touch.
    render(<ConfigureAmsSlotModal {...defaultProps} />);
    await waitFor(() => {
      const fullName = 'Bambu PLA Basic @BBL X1C';
      const span = screen.getByText(fullName);
      expect(span).toHaveAttribute('title', fullName);
      expect(span).toHaveClass('truncate');
      expect(span).toHaveClass('group-hover:whitespace-normal');
      expect(span).toHaveClass('group-hover:break-all');
      expect(span.closest('button')).toHaveClass('group');
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

  it('surfaces a K-profile whose name does not match the preset when filament_id agrees (#1688)', async () => {
    // Spool was edited with slicer_filament = "GFSL05_09" (the setting_id form
    // for Bambu PLA Basic). The printer has a *custom* K-profile saved on the
    // same filament_id, but the user named it something that doesn't include
    // "PLA". Pre-fix, the name-only filter dropped it; the id-match path now
    // surfaces it because both sides normalise to "GFL05".
    (api.getKProfiles as ReturnType<typeof vi.fn>).mockResolvedValue({
      profiles: [
        {
          slot_id: 3,
          extruder_id: 0,
          nozzle_id: 'HH00-0.4',
          nozzle_diameter: '0.4',
          filament_id: 'GFL05',
          name: 'my-custom-tune',
          k_value: '0.025',
          n_coef: '0',
          ams_id: 0,
          tray_id: 0,
          setting_id: '',
        },
      ],
    });
    const slotInfo = {
      ...defaultProps.slotInfo,
      savedPresetId: 'GFSL05_09', // setting_id form for Bambu PLA Basic
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} />);

    await waitFor(() => {
      // Renders as an <option> on the K-profile select even though
      // "my-custom-tune" doesn't contain "PLA" anywhere.
      expect(screen.getByRole('option', { name: /my-custom-tune/ })).toBeInTheDocument();
    });
  });

  it("always includes the slot's currently-active K-profile when name and id don't match (#1689)", async () => {
    // Reporter scenario: spool assigned under "Generic PLA" but the slot has
    // a custom K-profile (filament_id "GFG98" = PETG-something) actively
    // selected via cali_idx. Pre-fix the modal showed "default 0.020"; the
    // safety net now surfaces the active profile so Configure Slot reflects
    // what the printer is actually using.
    (api.getKProfiles as ReturnType<typeof vi.fn>).mockResolvedValue({
      profiles: [
        {
          slot_id: 7, // matches caliIdx below
          extruder_id: 0,
          nozzle_id: 'HH00-0.4',
          nozzle_diameter: '0.4',
          filament_id: 'GFG98', // unrelated to "Generic PLA"
          name: 'unrelated-petg-tune',
          k_value: '0.030',
          n_coef: '0',
          ams_id: 0,
          tray_id: 0,
          setting_id: '',
        },
      ],
    });
    const slotInfo = {
      ...defaultProps.slotInfo,
      savedPresetId: 'GFSL05_09', // Generic PLA preset
      caliIdx: 7,
      extruderId: 0,
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} />);

    await waitFor(() => {
      expect(screen.getByRole('option', { name: /unrelated-petg-tune/ })).toBeInTheDocument();
    });
  });

  it("surfaces the slot's active K-profile when no preset is resolvable (#1689 follow-up)", async () => {
    // Repro from Spionkiller01: slot is physically loaded but unconfigured —
    // tray_type='', tray_info_idx='', no slot_preset_mappings row — so
    // selectedPresetInfo resolves to null. Before the patch the main matcher's
    // early return on !selectedPresetInfo skipped past the cali_idx safety net
    // entirely; on reopen the dropdown went back to default 0.020 even though
    // the printer still holds the active profile at cali_idx=6.
    (api.getKProfiles as ReturnType<typeof vi.fn>).mockResolvedValue({
      profiles: [
        {
          slot_id: 6,
          extruder_id: 0,
          nozzle_id: 'HH00-0.4',
          nozzle_diameter: '0.4',
          filament_id: 'GFG98',
          name: 'active-on-unconfigured-slot',
          k_value: '0.030',
          n_coef: '0',
          ams_id: 0,
          tray_id: 0,
          setting_id: '',
        },
      ],
    });
    const slotInfo = {
      ...defaultProps.slotInfo,
      trayType: '',
      traySubBrands: '',
      caliIdx: 6,
      extruderId: 0,
      // savedPresetId intentionally omitted — no preset bound yet
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} />);

    await waitFor(() => {
      expect(screen.getByRole('option', { name: /active-on-unconfigured-slot/ })).toBeInTheDocument();
    });
  });

  it('does not include the active K-profile when caliIdx is 0 or null (#1689 guard)', async () => {
    // cali_idx == 0 / null means no profile is active (printer default 0.020).
    // The safety net only triggers for activeIdx > 0 — otherwise unrelated
    // profiles whose slot_id happens to equal 0 would leak in.
    (api.getKProfiles as ReturnType<typeof vi.fn>).mockResolvedValue({
      profiles: [
        {
          slot_id: 0,
          extruder_id: 0,
          nozzle_id: 'HH00-0.4',
          nozzle_diameter: '0.4',
          filament_id: 'GFG98',
          name: 'should-not-appear',
          k_value: '0.030',
          n_coef: '0',
          ams_id: 0,
          tray_id: 0,
          setting_id: '',
        },
      ],
    });
    const slotInfo = {
      ...defaultProps.slotInfo,
      savedPresetId: 'GFSL05_09',
      caliIdx: 0,
      extruderId: 0,
    };
    render(<ConfigureAmsSlotModal {...defaultProps} slotInfo={slotInfo} />);

    await waitFor(() => {
      expect(screen.getByText(/Configure AMS/)).toBeInTheDocument();
    });
    expect(screen.queryByRole('option', { name: /should-not-appear/ })).not.toBeInTheDocument();
  });
});
