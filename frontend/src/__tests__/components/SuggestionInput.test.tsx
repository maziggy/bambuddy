/**
 * The styled suggestion field behind the spool form's Owner (#1301): the look
 * of the Material field, but free text first.
 */

import React, { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { render } from '../utils';
import { SuggestionInput } from '../../components/spool-form/SuggestionInput';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const OPTIONS = ['Alice', 'alina', 'Bob'];

function Harness({ initial = '', onChange = vi.fn() }: { initial?: string; onChange?: (v: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <div>
      <SuggestionInput
        id="owner"
        value={value}
        options={OPTIONS}
        placeholder="owner"
        onChange={(next) => {
          setValue(next);
          onChange(next);
        }}
      />
      <button type="button">elsewhere</button>
    </div>
  );
}

describe('SuggestionInput', () => {
  it('lists every suggestion when focused with nothing typed', () => {
    render(<Harness />);
    fireEvent.focus(screen.getByPlaceholderText('owner'));

    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Alice', 'alina', 'Bob', 'elsewhere']);
  });

  it('narrows the list as the user types, ignoring case', () => {
    render(<Harness />);
    const input = screen.getByPlaceholderText('owner');
    fireEvent.change(input, { target: { value: 'ali' } });

    expect(screen.getByRole('button', { name: 'Alice' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'alina' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Bob' })).toBeNull();
  });

  it('lists everyone on focus even when the field already holds a name', () => {
    render(<Harness initial="Bob" />);
    fireEvent.focus(screen.getByPlaceholderText('owner'));

    expect(screen.getByRole('button', { name: 'Alice' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Bob' })).toBeTruthy();
  });

  it('takes a suggestion into the field and closes the list', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.focus(screen.getByPlaceholderText('owner'));
    fireEvent.click(screen.getByRole('button', { name: 'Bob' }));

    expect(onChange).toHaveBeenLastCalledWith('Bob');
    expect(screen.getByPlaceholderText('owner')).toHaveValue('Bob');
    expect(screen.queryByRole('button', { name: 'Alice' })).toBeNull();
  });

  it('keeps a name that is in no list, without needing to confirm it', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('owner'), { target: { value: 'A walk-in customer' } });

    // The value is the typed text at once: leaving the field for Save keeps it.
    expect(onChange).toHaveBeenLastCalledWith('A walk-in customer');
    expect(screen.getByText('inventory.noResults')).toBeTruthy();
  });

  it('closes on Escape and on a click elsewhere', () => {
    render(<Harness />);
    const input = screen.getByPlaceholderText('owner');

    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('button', { name: 'Alice' })).toBeNull();

    fireEvent.focus(input);
    expect(screen.getByRole('button', { name: 'Alice' })).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole('button', { name: 'elsewhere' }));
    expect(screen.queryByRole('button', { name: 'Alice' })).toBeNull();
  });

  it('shows no list when there is nothing to suggest', () => {
    render(<SuggestionInput id="x" value="" options={[]} onChange={vi.fn()} placeholder="p" />);
    fireEvent.focus(screen.getByPlaceholderText('p'));

    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByText('inventory.noResults')).toBeNull();
  });
});
