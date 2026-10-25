/**
 * Sidebar switchbar: every on/off goes through a confirm modal, and the
 * control request carries the id of the plug that was clicked.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { api } from '../../api/client';
import type { SmartPlug, SmartPlugStatus } from '../../api/client';
import { SwitchbarPopover } from '../../components/SwitchbarPopover';

function plug(id: number, name: string, extra: Partial<SmartPlug> = {}): SmartPlug {
  return { id, name, plug_type: 'tasmota', show_in_switchbar: true, ...extra } as unknown as SmartPlug;
}

function rowFor(name: string): HTMLElement {
  // The row container holding the plug name and its two buttons
  return screen.getByText(name).closest('div.flex.items-center.justify-between') as HTMLElement;
}

function confirmButton(name: 'Turn On' | 'Turn Off'): HTMLElement {
  const btn = screen.getAllByRole('button', { name }).find((b) => !b.hasAttribute('title'));
  if (!btn) throw new Error(`confirm button ${name} not found`);
  return btn;
}

describe('SwitchbarPopover wiring', () => {
  let control: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.spyOn(api, 'getSmartPlugs').mockResolvedValue([
      plug(11, 'Plug Alpha'),
      plug(22, 'Plug Beta'),
      plug(33, 'Hidden Plug', { show_in_switchbar: false }),
      plug(44, 'Meter Plug', { plug_type: 'mqtt' }),
    ]);
    vi.spyOn(api, 'getSmartPlugStatus').mockImplementation(async (id: number) =>
      ({
        state: id === 44 ? null : 'ON',
        reachable: true,
        device_name: null,
        energy: { power: id === 44 ? 120 : null },
      }) as unknown as SmartPlugStatus,
    );
    control = vi.spyOn(api, 'controlSmartPlug').mockResolvedValue({ success: true } as never);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('turning off the clicked plug asks first, then sends controlSmartPlug(id, "off")', async () => {
    const user = userEvent.setup();
    render(<SwitchbarPopover onClose={() => {}} />);

    await screen.findByText('Plug Beta');
    const row = rowFor('Plug Beta');
    const offBtn = within(row).getByTitle('Turn Off');
    await waitFor(() => expect(offBtn).not.toBeDisabled());

    await user.click(offBtn);
    // Confirm modal is shown, nothing sent yet
    expect(await screen.findByText(/are you sure you want to turn off "Plug Beta"/i)).toBeInTheDocument();
    expect(control).not.toHaveBeenCalled();

    await user.click(confirmButton('Turn Off'));
    await waitFor(() => expect(control).toHaveBeenCalledTimes(1));
    expect(control).toHaveBeenCalledWith(22, 'off');
  });

  it('turning on sends controlSmartPlug(id, "on") for that plug only', async () => {
    const user = userEvent.setup();
    render(<SwitchbarPopover onClose={() => {}} />);

    await screen.findByText('Plug Alpha');
    const onBtn = within(rowFor('Plug Alpha')).getByTitle('Turn On');
    await waitFor(() => expect(onBtn).not.toBeDisabled());
    await user.click(onBtn);
    await user.click(confirmButton('Turn On'));

    await waitFor(() => expect(control).toHaveBeenCalledWith(11, 'on'));
    expect(control).toHaveBeenCalledTimes(1);
  });

  it('cancelling the confirm modal sends nothing', async () => {
    const user = userEvent.setup();
    render(<SwitchbarPopover onClose={() => {}} />);

    await screen.findByText('Plug Alpha');
    const offBtn = within(rowFor('Plug Alpha')).getByTitle('Turn Off');
    await waitFor(() => expect(offBtn).not.toBeDisabled());
    await user.click(offBtn);
    await screen.findByText(/are you sure you want to turn off "Plug Alpha"/i);
    await user.click(screen.getByRole('button', { name: /cancel/i }));

    await waitFor(() =>
      expect(screen.queryByText(/are you sure you want to turn off/i)).not.toBeInTheDocument(),
    );
    expect(control).not.toHaveBeenCalled();
  });

  it('lists only switchbar plugs and gives MQTT (monitor-only) plugs no switch buttons', async () => {
    render(<SwitchbarPopover onClose={() => {}} />);

    await screen.findByText('Plug Alpha');
    expect(screen.queryByText('Hidden Plug')).not.toBeInTheDocument();
    const meterRow = rowFor('Meter Plug');
    expect(within(meterRow).queryByTitle('Turn On')).not.toBeInTheDocument();
    expect(within(meterRow).queryByTitle('Turn Off')).not.toBeInTheDocument();
  });
});
