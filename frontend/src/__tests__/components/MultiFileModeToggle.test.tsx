/** Mode toggle for several selected sliced files (#3462) and the retitled candidate list. */

import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { MultiFileModeToggle } from '../../components/PrintModal/MultiFileModeToggle';
import { VariantCandidates } from '../../components/PrintModal/VariantCandidates';

describe('MultiFileModeToggle', () => {
  it('renders a named radiogroup with two radios following value', () => {
    render(<MultiFileModeToggle value="separate" onChange={() => {}} blockedModel={null} />);
    expect(screen.getByRole('radiogroup', { name: 'What the selected files become' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Alternatives' })).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByRole('radio', { name: 'Separate jobs' })).toHaveAttribute('aria-checked', 'true');
  });

  it('calls onChange("separate") when Separate jobs is clicked', async () => {
    const onChange = vi.fn();
    render(<MultiFileModeToggle value="alternatives" onChange={onChange} blockedModel={null} />);
    await userEvent.click(screen.getByRole('radio', { name: 'Separate jobs' }));
    expect(onChange).toHaveBeenCalledWith('separate');
  });

  it('disables Alternatives and shows the blocked text when blockedModel is set', async () => {
    const onChange = vi.fn();
    render(<MultiFileModeToggle value="separate" onChange={onChange} blockedModel="A1" />);
    const alt = screen.getByRole('radio', { name: 'Alternatives' });
    expect(alt).toBeDisabled();
    expect(screen.getByText(/More than one file is sliced for A1\./)).toBeInTheDocument();
    expect(screen.queryByText(/One queue item per file/)).not.toBeInTheDocument();
    await userEvent.click(alt);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('offers neither mode while disabled', async () => {
    // The dialog disables it while the files are being queued.
    const onChange = vi.fn();
    render(<MultiFileModeToggle value="separate" onChange={onChange} blockedModel={null} disabled />);
    const alt = screen.getByRole('radio', { name: 'Alternatives' });
    expect(alt).toBeDisabled();
    expect(screen.getByRole('radio', { name: 'Separate jobs' })).toBeDisabled();
    await userEvent.click(alt);
    expect(onChange).not.toHaveBeenCalled();
    // The hint stays: it still says what is being queued.
    expect(screen.getByText(/One queue item per file/)).toBeInTheDocument();
  });

  it('shows the hint of the selected mode when not blocked', () => {
    const { rerender } = render(<MultiFileModeToggle value="alternatives" onChange={() => {}} blockedModel={null} />);
    expect(screen.getByText(/The first matching printer to free up/)).toBeInTheDocument();
    rerender(<MultiFileModeToggle value="separate" onChange={() => {}} blockedModel={null} />);
    expect(screen.getByText(/One queue item per file/)).toBeInTheDocument();
  });
});

describe('VariantCandidates title/help props', () => {
  const props = {
    candidates: [{ id: 1, filename: 'a.gcode.3mf', sliced_for_model: 'X1C' }],
    onReorder: () => {},
    plateByFile: {},
    onPlateChange: () => {},
  };

  it('shows a custom title and help instead of the defaults', () => {
    render(<VariantCandidates {...props} title="Queue order" help="Custom help line" />);
    expect(screen.getByText('Queue order')).toBeInTheDocument();
    expect(screen.getByText('Custom help line')).toBeInTheDocument();
    expect(screen.queryByText('Printer alternatives')).not.toBeInTheDocument();
  });

  it('renders no help paragraph for an empty help', () => {
    // The mode toggle above the list explains the mode; a second line here
    // would repeat it.
    render(<VariantCandidates {...props} help="" />);
    expect(screen.queryByText(/One job, one queue slot/)).not.toBeInTheDocument();
    const section = screen.getByText('Printer alternatives').parentElement!.parentElement!;
    expect(section.querySelector('p')).toBeNull();
  });

  it('keeps the read-only note when help is empty', () => {
    render(<VariantCandidates {...props} readOnly readOnlyNote="Set when queued" help="" />);
    expect(screen.getByText('Set when queued')).toBeInTheDocument();
  });
});
