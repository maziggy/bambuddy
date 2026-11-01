import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FilamentSlotDetails } from '../../components/FilamentSlotDetails';

describe('FilamentSlotDetails', () => {
  it('keeps the slot label readable on light and dark configured colors', () => {
    const { rerender } = render(
      <FilamentSlotDetails label="A1" color="#FFFF00FF" material="PLA" fill={66} empty={false} />,
    );
    expect(screen.getByText('A1')).toHaveStyle({ color: '#000' });
    expect(screen.getByText('A1').style.backgroundColor).toBe('');

    rerender(<FilamentSlotDetails label="A1" color="000000" material="PLA" fill={66} empty={false} />);
    expect(screen.getByText('A1')).toHaveStyle({ color: '#fff' });
  });

  it.each(['00000011', '00000000'])('keeps the slot label readable over a translucent swatch (%s)', (color) => {
    render(<FilamentSlotDetails label="A1" color={color} material="PLA" fill={66} empty={false} />);
    expect(screen.getByText('A1')).toHaveStyle({ color: '#fff', backgroundColor: '#111827' });
  });

  it('shows an empty spool as zero and an unknown remaining amount as a dash', () => {
    const { rerender } = render(
      <FilamentSlotDetails label="A1" color="387120FF" material="PLA" fill={0} empty={false} />,
    );
    expect(screen.getByText('0%')).toBeInTheDocument();

    rerender(<FilamentSlotDetails label="A1" color="387120FF" material="PLA" fill={null} empty={false} />);
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();
  });

  it('shows material, readable color name and brand, with full text available when space is narrow', () => {
    render(
      <FilamentSlotDetails
        label="HT-A"
        color="FF8000FF"
        material="PLA Silk+"
        colorName="Lemon Yellow and Sunny Orange"
        brand="SUNLU filament manufacturer"
        fill={14}
        empty={false}
      >
        <span>P1</span>
      </FilamentSlotDetails>,
    );

    expect(screen.getByText('HT-A')).toBeInTheDocument();
    expect(screen.getByText('PLA Silk+')).toBeInTheDocument();
    expect(screen.getByText('Lemon Yellow and Sunny Orange')).toHaveAttribute('title', 'Lemon Yellow and Sunny Orange');
    expect(screen.getByText('SUNLU filament manufacturer')).toHaveAttribute('title', 'SUNLU filament manufacturer');
    expect(screen.getByText('14%')).toBeInTheDocument();
    expect(screen.getByText('P1')).toBeInTheDocument();
  });

  it('does not present stale spool metadata or remaining amount in an empty slot', () => {
    render(
      <FilamentSlotDetails
        label="Ext-L"
        color="FFFF00FF"
        material="Empty"
        colorName="Lemon Yellow"
        brand="SUNLU"
        fill={75}
        empty
      />,
    );

    expect(screen.getByText('Ext-L')).toBeInTheDocument();
    expect(screen.getByText('Empty')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('Lemon Yellow')).not.toBeInTheDocument();
    expect(screen.queryByText('SUNLU')).not.toBeInTheDocument();
    expect(screen.queryByText('75%')).not.toBeInTheDocument();
  });

  it.each([-1, Number.NaN, Number.POSITIVE_INFINITY])('treats unavailable remaining data (%s) as unknown', (fill) => {
    render(<FilamentSlotDetails label="A1" color="FF0000FF" material="PLA" fill={fill} empty={false} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});
