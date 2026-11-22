import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { render } from '../utils';
import { FilamentAccessCard } from '../../components/FilamentAccessCard';

type CardProps = ComponentProps<typeof FilamentAccessCard>;

const card = (props: Partial<CardProps> = {}) => (
  <FilamentAccessCard enabled mode="block" rules={[]} onChange={vi.fn()} {...props} />
);

describe('FilamentAccessCard', () => {
  it('turns the restriction on', async () => {
    const onChange = vi.fn();
    render(card({ enabled: false, onChange }));
    await userEvent.click(screen.getByRole('checkbox', { name: /limit filaments/i }));
    expect(onChange).toHaveBeenCalledWith({ enabled: true, mode: 'block', rules: [] });
  });

  it('adds a material rule from the input', async () => {
    const onChange = vi.fn();
    render(card({ onChange }));
    await userEvent.type(screen.getByLabelText(/rule value/i), 'petg');
    await userEvent.click(screen.getByRole('button', { name: /add rule/i }));
    expect(onChange).toHaveBeenCalledWith({
      enabled: true,
      mode: 'block',
      rules: [{ match: 'material', value: 'PETG' }],
    });
  });

  it('removes a rule', async () => {
    const onChange = vi.fn();
    render(card({ mode: 'allow', rules: [{ match: 'filament_id', value: 'GFG03' }], onChange }));
    await userEvent.click(screen.getByRole('button', { name: /remove GFG03/i }));
    expect(onChange).toHaveBeenCalledWith({ enabled: true, mode: 'allow', rules: [] });
  });

  it('shows the admin note instead of controls when disabled', () => {
    render(card({ enabled: false, disabled: true }));
    expect(screen.queryByRole('checkbox', { name: /limit filaments/i })).toBeNull();
    expect(screen.getByText(/administrators can always use every filament/i)).toBeInTheDocument();
  });

  it('warns that an empty allow list blocks every filament', () => {
    const { rerender } = render(card({ mode: 'allow' }));
    expect(screen.getByRole('status')).toHaveTextContent(/an allow list blocks every filament/i);
    rerender(card({ mode: 'allow', rules: [{ match: 'material', value: 'PLA' }] }));
    expect(screen.queryByText(/an allow list blocks every filament/i)).toBeNull();
  });

  it('does not report typed text the admin can no longer see', async () => {
    const pending = vi.fn();
    const allowCard = (enabled: boolean) => card({ enabled, mode: 'allow', onPendingRulesChange: pending });
    const { rerender } = render(allowCard(true));
    await userEvent.type(screen.getByLabelText(/rule value/i), 'pla');
    expect(pending).toHaveBeenLastCalledWith([{ match: 'material', value: 'PLA' }]);

    // Another match kind: the text typed for the old one goes.
    await userEvent.selectOptions(screen.getByDisplayValue('Material type'), 'filament_id');
    expect(screen.getByLabelText(/rule value/i)).toHaveValue('');
    expect(pending).toHaveBeenLastCalledWith([]);

    // Unticked, the input is hidden: nothing typed there is saved.
    await userEvent.type(screen.getByLabelText(/rule value/i), 'GFG03');
    rerender(allowCard(false));
    expect(pending).toHaveBeenLastCalledWith([]);
    rerender(allowCard(true));
    expect(pending).toHaveBeenLastCalledWith([]);
  });
});
