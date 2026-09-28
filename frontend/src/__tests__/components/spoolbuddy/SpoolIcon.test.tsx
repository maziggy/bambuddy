/**
 * Tests for SpoolIcon component:
 * - Renders SVG when not empty (with correct color)
 * - Renders dashed circle when isEmpty=true
 * - Respects size prop
 */

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import React from 'react';
import { SpoolIcon } from '../../../components/spoolbuddy/SpoolIcon';

describe('SpoolIcon', () => {
  it('renders SVG when not empty', () => {
    const { container } = render(<SpoolIcon color="#FF0000" isEmpty={false} />);
    const svg = container.querySelector('svg');
    expect(svg).not.toBeNull();
  });

  it('renders SVG with correct color in fill', () => {
    const { container } = render(<SpoolIcon color="#00AE42" isEmpty={false} />);
    const circles = container.querySelectorAll('circle');
    // First circle has the color as fill
    expect(circles[0].getAttribute('fill')).toBe('#00AE42');
  });

  it('renders dashed circle when isEmpty=true', () => {
    const { container } = render(<SpoolIcon color="#FF0000" isEmpty={true} />);
    // No SVG, should be a div with border-dashed
    const svg = container.querySelector('svg');
    expect(svg).toBeNull();
    const div = container.firstElementChild as HTMLElement;
    expect(div.className).toContain('border-dashed');
  });

  it('uses default size of 32', () => {
    const { container } = render(<SpoolIcon color="#FF0000" isEmpty={false} />);
    const svg = container.querySelector('svg');
    expect(svg!.getAttribute('width')).toBe('32');
    expect(svg!.getAttribute('height')).toBe('32');
  });

  it('respects custom size prop', () => {
    const { container } = render(<SpoolIcon color="#FF0000" isEmpty={false} size={64} />);
    const svg = container.querySelector('svg');
    expect(svg!.getAttribute('width')).toBe('64');
    expect(svg!.getAttribute('height')).toBe('64');
  });

  it('respects custom size prop for empty spool', () => {
    const { container } = render(<SpoolIcon color="#FF0000" isEmpty={true} size={48} />);
    const div = container.firstElementChild as HTMLElement;
    expect(div.style.width).toBe('48px');
    expect(div.style.height).toBe('48px');
  });

  describe('a spool with extra colours or an effect (#3033)', () => {
    const dualColour = { rgba: '044482FF', extra_colors: '044482,f8d008', effect_type: 'dual-color' };

    it('paints the disc with the Filament page background instead of a flat fill', () => {
      const { container, getAllByTestId, getByTestId } = render(
        <SpoolIcon color="#044482" isEmpty={false} size={100} spool={dualColour} />,
      );
      expect(container.querySelector('svg')).toBeNull();
      // Painted once, so the colour bands run straight through both rings.
      const paint = getAllByTestId('spool-paint');
      expect(paint).toHaveLength(1);
      // Hard-split bars, both colours present.
      expect(paint[0].style.backgroundImage).toContain('linear-gradient(to right');
      expect(paint[0].style.backgroundImage).toContain('#f8d008');
      // The inner ring keeps the SVG disc's darker depth.
      expect(getByTestId('spool-shade').style.backgroundColor).toBe('rgba(0, 0, 0, 0.15)');
      const box = container.firstElementChild as HTMLElement;
      expect(box.style.width).toBe('100px');
    });

    it('paints an effect on a single-colour spool', () => {
      const { getAllByTestId } = render(
        <SpoolIcon color="#111111" isEmpty={false} spool={{ rgba: '111111FF', effect_type: 'galaxy' }} />,
      );
      const outer = getAllByTestId('spool-paint')[0];
      // Effect overlay on top of the colour layer on top of the checkerboard.
      expect(outer.style.backgroundImage.split('gradient(').length).toBeGreaterThan(3);
    });

    it('keeps the flat SVG disc for a plain spool', () => {
      const { container, queryAllByTestId } = render(
        <SpoolIcon color="#00AE42" isEmpty={false} spool={{ rgba: '00AE42FF', extra_colors: null, effect_type: null }} />,
      );
      expect(queryAllByTestId('spool-paint')).toHaveLength(0);
      expect(container.querySelector('circle')!.getAttribute('fill')).toBe('#00AE42');
    });

    it('still shows the empty state for an empty slot', () => {
      const { container, queryAllByTestId } = render(
        <SpoolIcon color="#044482" isEmpty={true} spool={dualColour} />,
      );
      expect(queryAllByTestId('spool-paint')).toHaveLength(0);
      expect((container.firstElementChild as HTMLElement).className).toContain('border-dashed');
    });
  });
});
