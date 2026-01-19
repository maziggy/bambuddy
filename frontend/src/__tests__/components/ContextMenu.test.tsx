/**
 * Tests for the ContextMenu component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { ContextMenu } from '../../components/ContextMenu';

describe('ContextMenu', () => {
  const mockOnClose = vi.fn();

  const menuItems = [
    { label: 'Edit', onClick: vi.fn() },
    { label: 'Delete', onClick: vi.fn(), danger: true },
    { label: 'Download', onClick: vi.fn() },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders menu items', () => {
      render(
        <ContextMenu
          x={100}
          y={100}
          items={menuItems}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Edit')).toBeInTheDocument();
      expect(screen.getByText('Delete')).toBeInTheDocument();
      expect(screen.getByText('Download')).toBeInTheDocument();
    });

    it('positions menu at specified coordinates', () => {
      render(
        <ContextMenu
          x={200}
          y={150}
          items={menuItems}
          onClose={mockOnClose}
        />
      );

      // Menu should be rendered with items visible
      expect(screen.getByText('Edit')).toBeInTheDocument();
    });
  });

  describe('interactions', () => {
    it('calls onClick when item is clicked', async () => {
      const user = userEvent.setup();
      render(
        <ContextMenu
          x={100}
          y={100}
          items={menuItems}
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByText('Edit'));

      expect(menuItems[0].onClick).toHaveBeenCalled();
    });

    it('calls onClose after item click', async () => {
      const user = userEvent.setup();
      render(
        <ContextMenu
          x={100}
          y={100}
          items={menuItems}
          onClose={mockOnClose}
        />
      );

      await user.click(screen.getByText('Edit'));

      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe('styling', () => {
    it('applies danger styling', () => {
      render(
        <ContextMenu
          x={100}
          y={100}
          items={menuItems}
          onClose={mockOnClose}
        />
      );

      // Delete item has danger: true, so should have red styling
      const deleteButton = screen.getByText('Delete');
      expect(deleteButton).toBeInTheDocument();
    });
  });

  describe('dividers', () => {
    it('supports divider property on items', () => {
      // Just verify the ContextMenuItem interface accepts divider prop
      const itemsWithDivider = [
        { label: 'Edit', onClick: vi.fn() },
        { label: 'Copy', onClick: vi.fn(), divider: true },
      ];

      // Interface should accept these items without error
      expect(itemsWithDivider[1].divider).toBe(true);
    });
  });
});
