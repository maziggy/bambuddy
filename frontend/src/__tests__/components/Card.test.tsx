/**
 * Tests for the Card components.
 */

import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { Card, CardHeader, CardContent } from '../../components/Card';

describe('Card', () => {
  it('renders children', () => {
    render(<Card>Test Content</Card>);
    expect(screen.getByText('Test Content')).toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(<Card className="custom-class">Content</Card>);
    expect(container.firstChild).toHaveClass('custom-class');
  });

  it('applies default styling classes', () => {
    const { container } = render(<Card>Content</Card>);
    expect(container.firstChild).toHaveClass('bg-bambu-dark-secondary');
    expect(container.firstChild).toHaveClass('rounded-xl');
    expect(container.firstChild).toHaveClass('border');
  });

  it('handles click events', () => {
    const handleClick = vi.fn();
    render(<Card onClick={handleClick}>Clickable</Card>);
    fireEvent.click(screen.getByText('Clickable'));
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it('handles context menu events', () => {
    const handleContextMenu = vi.fn();
    render(<Card onContextMenu={handleContextMenu}>Right-clickable</Card>);
    fireEvent.contextMenu(screen.getByText('Right-clickable'));
    expect(handleContextMenu).toHaveBeenCalledTimes(1);
  });
});

describe('CardHeader', () => {
  it('renders children', () => {
    render(<CardHeader>Header Content</CardHeader>);
    expect(screen.getByText('Header Content')).toBeInTheDocument();
  });

  it('applies border styling', () => {
    const { container } = render(<CardHeader>Header</CardHeader>);
    expect(container.firstChild).toHaveClass('border-b');
    expect(container.firstChild).toHaveClass('px-6');
    expect(container.firstChild).toHaveClass('py-4');
  });

  it('applies custom className', () => {
    const { container } = render(
      <CardHeader className="custom-header">Header</CardHeader>
    );
    expect(container.firstChild).toHaveClass('custom-header');
  });
});

describe('CardContent', () => {
  it('renders children', () => {
    render(<CardContent>Body Content</CardContent>);
    expect(screen.getByText('Body Content')).toBeInTheDocument();
  });

  it('applies padding styling', () => {
    const { container } = render(<CardContent>Content</CardContent>);
    expect(container.firstChild).toHaveClass('p-3');
  });

  it('applies custom className', () => {
    const { container } = render(
      <CardContent className="custom-content">Content</CardContent>
    );
    expect(container.firstChild).toHaveClass('custom-content');
  });
});

describe('Card composition', () => {
  it('composes Card with Header and Content', () => {
    render(
      <Card>
        <CardHeader>My Header</CardHeader>
        <CardContent>My Content</CardContent>
      </Card>
    );

    expect(screen.getByText('My Header')).toBeInTheDocument();
    expect(screen.getByText('My Content')).toBeInTheDocument();
  });
});
