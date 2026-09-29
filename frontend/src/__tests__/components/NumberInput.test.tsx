/**
 * NumberInput keeps what was typed as a draft, so a number field can be
 * cleared and retyped (#3182). Clamping inside onChange used to snap an
 * emptied field straight back to its minimum.
 */
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { NumberInput } from '../../components/NumberInput';

function Harness({
  initial,
  onCommit,
  ...props
}: {
  initial: number;
  onCommit?: (v: number) => void;
  min?: number;
  max?: number;
  fallback?: number;
  integer?: boolean;
}) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <NumberInput
        aria-label="qty"
        value={value}
        onChange={(v) => {
          setValue(v);
          onCommit?.(v);
        }}
        {...props}
      />
      <output data-testid="committed">{value}</output>
    </>
  );
}

const input = () => screen.getByLabelText('qty') as HTMLInputElement;
const committed = () => screen.getByTestId('committed').textContent;

describe('NumberInput', () => {
  it('can be emptied and retyped without snapping back to the minimum', async () => {
    const user = userEvent.setup();
    render(<Harness initial={1} min={1} max={999} fallback={1} />);

    await user.click(input());
    await user.keyboard('{Backspace}');
    expect(input().value).toBe('');
    expect(committed()).toBe('1');

    await user.keyboard('6');
    expect(input().value).toBe('6');
    expect(committed()).toBe('6');
  });

  it('passes nothing up while the draft is below the minimum, then the finished number', async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<Harness initial={45} min={45} max={90} onCommit={onCommit} />);

    await user.clear(input());
    await user.keyboard('6');
    expect(input().value).toBe('6');
    expect(onCommit).not.toHaveBeenCalled();

    await user.keyboard('0');
    expect(input().value).toBe('60');
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenLastCalledWith(60);
  });

  it('clamps an out-of-range draft into range on blur', async () => {
    const user = userEvent.setup();
    render(<Harness initial={50} min={45} max={90} />);

    await user.clear(input());
    await user.keyboard('120');
    await user.tab();
    expect(input().value).toBe('90');
    expect(committed()).toBe('90');

    await user.clear(input());
    await user.keyboard('3');
    await user.tab();
    expect(input().value).toBe('45');
    expect(committed()).toBe('45');
  });

  it('commits the fallback when left empty', async () => {
    const user = userEvent.setup();
    render(<Harness initial={30} min={1} max={365} fallback={7} />);

    await user.clear(input());
    await user.tab();
    expect(input().value).toBe('7');
    expect(committed()).toBe('7');
  });

  it('reverts to the current value when left empty without a fallback', async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<Harness initial={40} min={30} max={65} onCommit={onCommit} />);

    await user.clear(input());
    await user.tab();
    expect(input().value).toBe('40');
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('accepts 0 where the range allows it', async () => {
    const user = userEvent.setup();
    render(<Harness initial={10} min={0} max={120} fallback={10} />);

    await user.clear(input());
    await user.keyboard('0');
    await user.tab();
    expect(input().value).toBe('0');
    expect(committed()).toBe('0');
  });

  it('keeps fractions when integer is false', async () => {
    const user = userEvent.setup();
    render(<Harness initial={28} min={0} max={60} integer={false} />);

    await user.clear(input());
    await user.keyboard('27.5');
    await user.tab();
    expect(input().value).toBe('27.5');
    expect(committed()).toBe('27.5');
  });

  it('follows a value changed from outside while not being edited', () => {
    const { rerender } = render(<NumberInput aria-label="qty" value={3} onChange={() => {}} />);
    expect(input().value).toBe('3');
    rerender(<NumberInput aria-label="qty" value={8} onChange={() => {}} />);
    expect(input().value).toBe('8');
  });

  it('still calls a caller-supplied onBlur', async () => {
    const user = userEvent.setup();
    const onBlur = vi.fn();
    render(<NumberInput aria-label="qty" value={3} onChange={() => {}} onBlur={onBlur} />);

    await user.click(input());
    await user.tab();
    expect(onBlur).toHaveBeenCalledTimes(1);
  });
});
