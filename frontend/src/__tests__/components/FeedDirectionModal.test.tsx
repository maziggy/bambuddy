/**
 * Tests for FeedDirectionModal — the "which hotend?" question a Filament Track
 * Switch forces onto every load.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { FeedDirectionModal } from '../../components/FeedDirectionModal';
import type { ExtruderSlot } from '../../api/client';

const empty: ExtruderSlot = { ams_id: null, slot_id: null, has_filament: false };

describe('FeedDirectionModal', () => {
  const defaultProps = {
    slotLabel: 'A3',
    amsId: 0,
    slotId: 2,
    extruderSlots: { '0': empty, '1': empty },
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
  };

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('names the slot being loaded', () => {
    render(<FeedDirectionModal {...defaultProps} />);
    expect(screen.getByText('Load A3 to which nozzle?')).toBeInTheDocument();
  });

  it('will not confirm until a hotend is picked', async () => {
    const user = userEvent.setup();
    render(<FeedDirectionModal {...defaultProps} />);

    // No default selection: an unattended Enter must not feed an arbitrary
    // hotend, which is why BambuStudio starts with neither radio checked.
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /Left nozzle/ }));

    expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled();
  });

  it('reports the left hotend as extruder 1 and the right as 0', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<FeedDirectionModal {...defaultProps} onConfirm={onConfirm} />);

    await user.click(screen.getByRole('button', { name: /Left nozzle/ }));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));

    expect(onConfirm).toHaveBeenCalledWith(1);

    await user.click(screen.getByRole('button', { name: /Right nozzle/ }));
    await user.click(screen.getByRole('button', { name: 'Confirm' }));

    expect(onConfirm).toHaveBeenLastCalledWith(0);
  });

  it('disables a hotend already fed from this very slot', () => {
    render(
      <FeedDirectionModal
        {...defaultProps}
        extruderSlots={{
          '0': { ams_id: 0, slot_id: 2, has_filament: true },
          '1': empty,
        }}
      />
    );

    expect(screen.getByRole('button', { name: /Right nozzle/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Left nozzle/ })).toBeEnabled();
  });

  it('leaves both hotends offered when the loaded slot is a different one', () => {
    render(
      <FeedDirectionModal
        {...defaultProps}
        extruderSlots={{
          '0': { ams_id: 1, slot_id: 2, has_filament: true },
          '1': empty,
        }}
      />
    );

    expect(screen.getByRole('button', { name: /Right nozzle/ })).toBeEnabled();
  });

  it('cancels on Escape', async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    render(<FeedDirectionModal {...defaultProps} onCancel={onCancel} />);

    await user.keyboard('{Escape}');

    expect(onCancel).toHaveBeenCalled();
  });
});
