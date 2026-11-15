/**
 * Build plate photo with base-type fallback (#1306).
 */

import { describe, it, expect } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import { PlateImage } from '../../components/PlateImage';

describe('PlateImage', () => {
  it('shows the plate photo', () => {
    const { container } = render(<PlateImage plate={{ image: '/img/plates/cf.jpg', base_type: 'smooth_pei' }} />);
    expect(container.querySelector('img')).toHaveAttribute('src', '/img/plates/cf.jpg');
  });

  it('falls back to the base type icon when the photo is missing', () => {
    const { container } = render(<PlateImage plate={{ image: '/img/plates/missing.jpg', base_type: 'smooth_pei' }} />);
    fireEvent.error(container.querySelector('img')!);
    expect(container.querySelector('img')).toHaveAttribute('src', '/img/bed/bed_pei_cool.png');
  });

  it('uses the base type icon when there is no photo at all', () => {
    const { container } = render(<PlateImage plate={{ image: null, base_type: 'textured_pei' }} />);
    expect(container.querySelector('img')).toHaveAttribute('src', '/img/bed/bed_pei.png');
  });

  it('uses the caller fallback for an unknown type, and renders nothing without one', () => {
    const { container, rerender } = render(
      <PlateImage plate={{ image: null, base_type: 'mystery' }} fallbackSrc="/img/bed/bed_cool.png" />,
    );
    expect(container.querySelector('img')).toHaveAttribute('src', '/img/bed/bed_cool.png');
    rerender(<PlateImage plate={{ image: null, base_type: 'mystery' }} />);
    expect(container.querySelector('img')).toBeNull();
  });
});
