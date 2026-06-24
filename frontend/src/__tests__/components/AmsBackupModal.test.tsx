/**
 * Render tests for the AMS Filament Backup modal (#1762).
 *
 * Modal now renders one SVG ring per backup pair (BambuStudio Auto Refill
 * style); lone slots are intentionally suppressed.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';

import { render } from '../utils';
import { AmsBackupModal } from '../../components/AmsBackupModal';

function makeAmsUnits() {
  return [
    {
      id: 0,
      tray: [
        { id: 0, tray_type: 'PLA', tray_sub_brands: 'PLA Basic', tray_color: '#000000', tray_info_idx: 'GFA00' },
        { id: 1, tray_type: 'PETG', tray_sub_brands: 'PETG HF', tray_color: '#0000FF', tray_info_idx: 'GFG99' },
      ],
    },
    {
      id: 1,
      tray: [
        { id: 0, tray_type: 'PLA', tray_sub_brands: 'PLA Basic', tray_color: '#000000', tray_info_idx: 'GFA00' },
      ],
    },
  ];
}

describe('AmsBackupModal', () => {
  it('returns null when isOpen=false', () => {
    const { container } = render(
      <AmsBackupModal
        isOpen={false}
        state={true}
        amsUnits={makeAmsUnits()}
        amsExtruderMap={undefined}
        isDualNozzle={false}
        canToggle
        pending={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(container.querySelector('[data-testid="ams-backup-modal"]')).toBeNull();
  });

  it('renders a backup ring for each pair and OMITS lone slots', async () => {
    render(
      <AmsBackupModal
        isOpen
        state={true}
        amsUnits={makeAmsUnits()}
        amsExtruderMap={undefined}
        isDualNozzle={false}
        canToggle
        pending={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    // PLA Basic — pair: rendered in centre of its ring
    expect(await screen.findByText('PLA Basic')).toBeInTheDocument();
    // PETG HF — lone, must NOT appear (no longer listed)
    expect(screen.queryByText('PETG HF')).not.toBeInTheDocument();
  });

  it('closes on Escape keypress while open', async () => {
    const onClose = vi.fn();
    render(
      <AmsBackupModal
        isOpen
        state={true}
        amsUnits={makeAmsUnits()}
        amsExtruderMap={undefined}
        isDualNozzle={false}
        canToggle
        pending={false}
        onToggle={vi.fn()}
        onClose={onClose}
      />,
    );
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does NOT fire onClose on Escape when closed (listener unmounts)', () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <AmsBackupModal
        isOpen
        state={true}
        amsUnits={makeAmsUnits()}
        amsExtruderMap={undefined}
        isDualNozzle={false}
        canToggle
        pending={false}
        onToggle={vi.fn()}
        onClose={onClose}
      />,
    );
    rerender(
      <AmsBackupModal
        isOpen={false}
        state={true}
        amsUnits={makeAmsUnits()}
        amsExtruderMap={undefined}
        isDualNozzle={false}
        canToggle
        pending={false}
        onToggle={vi.fn()}
        onClose={onClose}
      />,
    );
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('toggle reflects the ON state and fires onToggle(false) when clicked', async () => {
    const onToggle = vi.fn();
    render(
      <AmsBackupModal
        isOpen
        state={true}
        amsUnits={makeAmsUnits()}
        amsExtruderMap={undefined}
        isDualNozzle={false}
        canToggle
        pending={false}
        onToggle={onToggle}
        onClose={vi.fn()}
      />,
    );

    const toggle = await screen.findByRole('switch');
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledWith(false);
  });

  it('toggle is disabled when state is unknown (A1 family)', async () => {
    render(
      <AmsBackupModal
        isOpen
        state={null}
        amsUnits={[]}
        amsExtruderMap={undefined}
        isDualNozzle={false}
        canToggle
        pending={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const toggle = await screen.findByRole('switch');
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/Unsupported/i)).toBeInTheDocument();
  });

  it('toggle is disabled when the user lacks printers:control', async () => {
    render(
      <AmsBackupModal
        isOpen
        state={true}
        amsUnits={makeAmsUnits()}
        amsExtruderMap={undefined}
        isDualNozzle={false}
        canToggle={false}
        pending={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const toggle = await screen.findByRole('switch');
    expect(toggle).toBeDisabled();
  });

  it('shows a no-pairs empty state when AMS has no backup pair', async () => {
    render(
      <AmsBackupModal
        isOpen
        state={true}
        // Just one slot, can't form any pair.
        amsUnits={[
          { id: 0, tray: [{ id: 0, tray_type: 'PLA', tray_sub_brands: 'PLA Basic', tray_color: '#000', tray_info_idx: 'GFA00' }] },
        ]}
        amsExtruderMap={undefined}
        isDualNozzle={false}
        canToggle
        pending={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(await screen.findByText(/No backup pairs/i)).toBeInTheDocument();
  });

  it('on dual-extruder with distinct map values, renders R / L badges on each ring', async () => {
    render(
      <AmsBackupModal
        isOpen
        state={true}
        amsUnits={[
          { id: 0, tray: [{ id: 0, tray_type: 'PLA', tray_sub_brands: 'PLA Basic', tray_color: '#000', tray_info_idx: 'GFA00' }] },
          { id: 1, tray: [{ id: 0, tray_type: 'PLA', tray_sub_brands: 'PLA Basic', tray_color: '#000', tray_info_idx: 'GFA00' }] },
          { id: 2, tray: [{ id: 0, tray_type: 'PETG', tray_sub_brands: 'PETG HF', tray_color: '#0FF', tray_info_idx: 'GFG99' }] },
          { id: 3, tray: [{ id: 0, tray_type: 'PETG', tray_sub_brands: 'PETG HF', tray_color: '#0FF', tray_info_idx: 'GFG99' }] },
        ]}
        // AMS 0+1 on right (ex 0), AMS 2+3 on left (ex 1) → two pairs, one per side.
        amsExtruderMap={{ '0': 0, '1': 0, '2': 1, '3': 1 }}
        isDualNozzle
        canToggle
        pending={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    // Both rings should be present (PLA Basic and PETG HF in the centres).
    expect(await screen.findByText('PLA Basic')).toBeInTheDocument();
    expect(screen.getByText('PETG HF')).toBeInTheDocument();
    // Both extruder badges visible.
    expect(screen.getByText('R')).toBeInTheDocument();
    expect(screen.getByText('L')).toBeInTheDocument();
  });

  it('collapses to single section (no R/L badges) when isDualNozzle=true but map has one distinct value', async () => {
    render(
      <AmsBackupModal
        isOpen
        state={true}
        amsUnits={makeAmsUnits()}
        amsExtruderMap={{ '0': 0, '1': 0 }}
        isDualNozzle
        canToggle
        pending={false}
        onToggle={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByText('R')).not.toBeInTheDocument();
    expect(screen.queryByText('L')).not.toBeInTheDocument();
  });
});
