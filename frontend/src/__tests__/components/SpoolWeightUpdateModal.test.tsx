import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolWeightUpdateModal } from '../../components/SpoolWeightUpdateModal';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

const defaultProps = {
  isOpen: true,
  filamentName: 'PLA Basic',
  oldWeight: 250,
  newWeight: 196,
  onConfirm: vi.fn(),
  onClose: vi.fn(),
};

describe('SpoolWeightUpdateModal', () => {
  it('renders filament name, old weight, and new weight', () => {
    render(<SpoolWeightUpdateModal {...defaultProps} />);

    expect(screen.getByText('settings.catalog.updateSpoolWeight')).toBeTruthy();
    expect(screen.getByText(/PLA Basic/)).toBeTruthy();
    expect(screen.getByText(/250g → 196g/)).toBeTruthy();
  });

  it('renders option labels', () => {
    render(<SpoolWeightUpdateModal {...defaultProps} />);

    expect(screen.getByText('settings.catalog.applyToAllSpools')).toBeTruthy();
    expect(screen.getByText('settings.catalog.keepExistingSpoolWeight')).toBeTruthy();
  });

  it('option B (apply to all) is selected by default', () => {
    render(<SpoolWeightUpdateModal {...defaultProps} />);

    const radios = screen.getAllByRole('radio') as HTMLInputElement[];
    // First radio = apply-to-all (Option B, keepExisting=false)
    expect(radios[0].checked).toBe(true);
    expect(radios[1].checked).toBe(false);
  });

  it('calls onConfirm(false) when option B is selected and Confirm clicked', () => {
    const onConfirm = vi.fn();
    render(<SpoolWeightUpdateModal {...defaultProps} onConfirm={onConfirm} />);

    fireEvent.click(screen.getByText('common.confirm'));

    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it('calls onConfirm(true) when option A is selected and Confirm clicked', () => {
    const onConfirm = vi.fn();
    render(<SpoolWeightUpdateModal {...defaultProps} onConfirm={onConfirm} />);

    const radios = screen.getAllByRole('radio');
    fireEvent.click(radios[1]); // Option A: keep existing

    fireEvent.click(screen.getByText('common.confirm'));

    expect(onConfirm).toHaveBeenCalledWith(true);
  });

  it('calls onClose on Cancel click', () => {
    const onClose = vi.fn();
    render(<SpoolWeightUpdateModal {...defaultProps} onClose={onClose} />);

    fireEvent.click(screen.getByText('common.cancel'));

    expect(onClose).toHaveBeenCalled();
  });

  it('does not call onConfirm on Cancel click', () => {
    const onConfirm = vi.fn();
    render(<SpoolWeightUpdateModal {...defaultProps} onConfirm={onConfirm} />);

    fireEvent.click(screen.getByText('common.cancel'));

    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('renders dash when oldWeight is null', () => {
    render(<SpoolWeightUpdateModal {...defaultProps} oldWeight={null} />);

    expect(screen.getByText(/— → 196g/)).toBeTruthy();
  });

  it('returns null when isOpen is false', () => {
    render(<SpoolWeightUpdateModal {...defaultProps} isOpen={false} />);

    expect(screen.queryByText('settings.catalog.updateSpoolWeight')).toBeNull();
  });
});
