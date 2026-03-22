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
});
