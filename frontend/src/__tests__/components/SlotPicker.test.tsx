/**
 * Tests for SlotPicker — the filament chooser that replaced the Print /
 * Schedule dialog's native `<select>`s so each choice can carry its colour
 * (#3159).
 *
 * The point of the control is the swatch, so these pin that a colour is drawn
 * for every row and for the chosen one; that a slot with no inventory binding
 * still gets the tray's own colour; and that the keyboard still works, because
 * the `<select>` this replaced was operable without a mouse and losing that
 * would be a regression dressed up as a feature.
 */

import { describe, it, expect, vi } from 'vitest';
import { screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SlotPicker, type SlotPickerOption } from '../../components/PrintModal/SlotPicker';
import { displayHex } from '../../components/PrintModal/displayHex';

const OPTIONS: SlotPickerOption[] = [
  {
    value: '0',
    label: 'A1: Devil Design PLA Basic (Orange)',
    meta: ' - 820g left',
    rgba: '#FEC600',
    hex: '#FEC600',
  },
  {
    value: '1',
    // A slot configured on the printer with no inventory spool behind it: the
    // tray colour is all there is, and it still has to draw.
    label: 'A2: PLA (Black)',
    rgba: '#000000',
    hex: '#000000',
  },
  {
    value: '2',
    label: 'A3: eSUN PLA Silk (Rainbow)',
    rgba: '#FF0000',
    extraColors: '00FF00,0000FF',
    subtype: 'Multicolor',
    hex: '#FF0000',
    exactMatch: true,
  },
];

function renderPicker(props: Partial<React.ComponentProps<typeof SlotPicker>> = {}) {
  const onChange = props.onChange ?? vi.fn();
  render(
    <SlotPicker
      value=""
      options={OPTIONS}
      placeholder="-- Select slot --"
      ariaLabel="Printer slot for PLA"
      exactMatchLabel="Exact colour match"
      {...props}
      onChange={onChange}
    />,
  );
  return { onChange };
}

async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('combobox', { name: /printer slot for pla/i }));
  return await screen.findByRole('listbox');
}

describe('displayHex', () => {
  it('prints six digits for an opaque colour and eight for a see-through one', () => {
    // Alpha is kept only when it carries information: a Clear filament must
    // not read as if it were solid, and an opaque spool must not be given a
    // meaningless "FF" tail.
    expect(displayHex('#FEC600')).toBe('#FEC600');
    expect(displayHex('FEC600FF')).toBe('#FEC600');
    expect(displayHex('#00AAFF80')).toBe('#00AAFF80');
    expect(displayHex('#00000000')).toBe('#00000000');
  });

  it('prints nothing rather than something misleading', () => {
    expect(displayHex(null)).toBeNull();
    expect(displayHex('')).toBeNull();
    expect(displayHex('#ABC')).toBeNull();
    expect(displayHex('not-a-colour')).toBeNull();
  });
});

describe('SlotPicker', () => {
  it('draws a swatch for every offered slot', async () => {
    const user = userEvent.setup();
    renderPicker();

    const listbox = await open(user);
    // One per option. The placeholder has no colour and deliberately draws
    // nothing rather than a grey swatch, which would read as a grey spool.
    expect(within(listbox).getAllByTestId('filament-swatch')).toHaveLength(OPTIONS.length);
  });

  it('draws the chosen slot\'s colour on the closed control', async () => {
    renderPicker({ value: '1' });

    const trigger = screen.getByRole('combobox', { name: /printer slot for pla/i });
    const swatch = within(trigger).getByTestId('filament-swatch');
    expect(swatch).toBeInTheDocument();
    expect(trigger).toHaveTextContent('A2: PLA (Black)');
  });

  it('renders a multi-colour spool as a gradient, not its base colour', async () => {
    const user = userEvent.setup();
    renderPicker();

    const listbox = await open(user);
    const rainbow = within(listbox).getByRole('option', { name: /eSUN PLA Silk/ });
    const swatch = within(rainbow).getByTestId('filament-swatch');
    // `Multicolor` is what turns the stops into a conic sweep; a slot with no
    // extra stops would be a flat linear-gradient of one colour.
    expect(swatch.style.backgroundImage).toContain('conic-gradient');
    expect(swatch.style.backgroundImage).toContain('#00FF00');
  });

  it('shows each slot\'s hex and marks the one that matches exactly', async () => {
    const user = userEvent.setup();
    renderPicker();

    const listbox = await open(user);
    expect(within(listbox).getByText('#FEC600')).toBeInTheDocument();
    // The marker is what answers "is this a real mismatch or two names for the
    // same hex" without leaving the dialog.
    const exact = within(listbox).getByTitle('Exact colour match');
    expect(within(listbox).getByRole('option', { name: /eSUN PLA Silk/ })).toContainElement(exact);
  });

  it('picks a slot with the keyboard', async () => {
    const user = userEvent.setup();
    const { onChange } = renderPicker();

    const trigger = screen.getByRole('combobox', { name: /printer slot for pla/i });
    trigger.focus();
    // Row 0 is the placeholder, so two presses land on the second slot.
    await user.keyboard('{ArrowDown}');
    await screen.findByRole('listbox');
    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}');

    expect(onChange).toHaveBeenCalledWith('1');
    await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument());
  });

  it('closes on Escape without letting the dialog around it see the key', async () => {
    const user = userEvent.setup();
    const onDialogEscape = vi.fn();
    document.addEventListener('keydown', onDialogEscape);
    try {
      renderPicker();
      await open(user);
      await user.keyboard('{Escape}');

      await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument());
      // The picker stops propagation, so a dialog listening for Escape on the
      // document never sees it and stays open (#3159).
      expect(onDialogEscape).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener('keydown', onDialogEscape);
    }
  });

  it('reports the value the caller gave the row, not its position', async () => {
    const user = userEvent.setup();
    const { onChange } = renderPicker();

    const listbox = await open(user);
    await user.click(within(listbox).getByRole('option', { name: /Devil Design/ }));
    expect(onChange).toHaveBeenCalledWith('0');
  });

  it('offers the placeholder as a real choice so a slot can be cleared', async () => {
    const user = userEvent.setup();
    const { onChange } = renderPicker({ value: '1' });

    await user.click(screen.getByRole('combobox', { name: /printer slot for pla/i }));
    const listbox = await screen.findByRole('listbox');
    await user.click(within(listbox).getByRole('option', { name: /Select slot/ }));
    expect(onChange).toHaveBeenCalledWith('');
  });
});
