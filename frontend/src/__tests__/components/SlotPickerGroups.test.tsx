/**
 * SlotPicker group headers (#3590): the override picker shows "Loaded now" and
 * "In inventory, not loaded" as non-selectable headers over the rows.
 */

import { describe, it, expect, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SlotPicker, type SlotPickerOption } from '../../components/PrintModal/SlotPicker';

const GROUPED: SlotPickerOption[] = [
  { value: 'a', label: 'Loaded A', group: 'Loaded now' },
  { value: 'b', label: 'Loaded B', group: 'Loaded now' },
  { value: 'c', label: 'Stock C', group: 'In inventory, not loaded', meta: ' · 2 spools · 1.4 kg' },
];

function renderPicker(options: SlotPickerOption[], onChange = vi.fn()) {
  render(<SlotPicker value="" options={options} placeholder="-- original --" ariaLabel="Override" onChange={onChange} />);
  return { onChange };
}

describe('SlotPicker groups', () => {
  it('renders each group header once, labelling its options', async () => {
    const user = userEvent.setup();
    renderPicker(GROUPED);
    await user.click(screen.getByRole('combobox'));

    const groups = screen.getAllByRole('group');
    expect(groups.map((g) => g.getAttribute('aria-label'))).toEqual(['Loaded now', 'In inventory, not loaded']);
    expect(within(groups[0]).getAllByRole('option')).toHaveLength(2);
    expect(within(groups[1]).getAllByRole('option')).toHaveLength(1);
    expect(document.querySelectorAll('[data-group-header]')).toHaveLength(2);
  });

  it('headers are not options and cannot be chosen', async () => {
    const user = userEvent.setup();
    const { onChange } = renderPicker(GROUPED);
    await user.click(screen.getByRole('combobox'));

    // placeholder + 3 options, no row for the headers
    expect(screen.getAllByRole('option')).toHaveLength(4);
    await user.click(screen.getByText('In inventory, not loaded'));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('listbox')).toBeInTheDocument();
  });

  it('arrow keys move over options only and Enter picks the active one', async () => {
    const user = userEvent.setup();
    const { onChange } = renderPicker(GROUPED);
    const trigger = screen.getByRole('combobox');
    trigger.focus();
    await user.keyboard('{ArrowDown}'); // open on the placeholder
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}'); // a, b, c
    expect(trigger.getAttribute('aria-activedescendant')).toMatch(/-3$/);
    await user.keyboard('{Enter}');
    expect(onChange).toHaveBeenCalledWith('c');
  });

  it('shows the meta after the label', async () => {
    const user = userEvent.setup();
    renderPicker(GROUPED);
    await user.click(screen.getByRole('combobox'));
    expect(screen.getByRole('option', { name: /Stock C/ }).textContent).toContain('2 spools · 1.4 kg');
  });

  it('renders the flat list without groups when none are given', async () => {
    const user = userEvent.setup();
    renderPicker([{ value: 'a', label: 'A' }]);
    await user.click(screen.getByRole('combobox'));
    expect(screen.queryAllByRole('group')).toHaveLength(0);
    expect(document.querySelectorAll('[data-group-header]')).toHaveLength(0);
  });

  it('scrolls the active row into view on arrow-key navigation', async () => {
    const original = Element.prototype.scrollIntoView;
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    try {
      const user = userEvent.setup();
      renderPicker(GROUPED);
      const trigger = screen.getByRole('combobox');
      trigger.focus();
      await user.keyboard('{ArrowDown}{ArrowDown}');
      scroll.mockClear();
      await user.keyboard('{ArrowDown}');
      expect(scroll).toHaveBeenCalledWith({ block: 'nearest' });
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });

  it('works where scrollIntoView does not exist', async () => {
    const original = Element.prototype.scrollIntoView;
    // @ts-expect-error simulate jsdom without the method
    Element.prototype.scrollIntoView = undefined;
    try {
      const user = userEvent.setup();
      renderPicker(GROUPED);
      screen.getByRole('combobox').focus();
      await user.keyboard('{ArrowDown}{ArrowDown}');
      expect(screen.getByRole('listbox')).toBeInTheDocument();
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});
