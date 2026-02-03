/**
 * Tests for the ConfirmModal component.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, fireEvent, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { ConfirmModal } from '../../components/ConfirmModal';

describe('ConfirmModal', () => {
  const defaultProps = {
    title: 'Confirm Action',
    message: 'Are you sure you want to proceed?',
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
  };

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders title', () => {
      render(<ConfirmModal {...defaultProps} />);
      expect(screen.getByText('Confirm Action')).toBeInTheDocument();
    });

    it('renders message', () => {
      render(<ConfirmModal {...defaultProps} />);
      expect(
        screen.getByText('Are you sure you want to proceed?')
      ).toBeInTheDocument();
    });

    it('renders default button text', () => {
      render(<ConfirmModal {...defaultProps} />);
      expect(screen.getByText('Confirm')).toBeInTheDocument();
      expect(screen.getByText('Cancel')).toBeInTheDocument();
    });

    it('renders custom button text', () => {
      render(
        <ConfirmModal
          {...defaultProps}
          confirmText="Delete"
          cancelText="Go Back"
        />
      );
      expect(screen.getByText('Delete')).toBeInTheDocument();
      expect(screen.getByText('Go Back')).toBeInTheDocument();
    });
  });

  describe('interactions', () => {
    it('calls onConfirm when confirm button is clicked', async () => {
      const user = userEvent.setup();
      const onConfirm = vi.fn();
      render(<ConfirmModal {...defaultProps} onConfirm={onConfirm} />);

      await user.click(screen.getByText('Confirm'));
      expect(onConfirm).toHaveBeenCalledTimes(1);
    });

    it('calls onCancel when cancel button is clicked', async () => {
      const user = userEvent.setup();
      const onCancel = vi.fn();
      render(<ConfirmModal {...defaultProps} onCancel={onCancel} />);

      await user.click(screen.getByText('Cancel'));
      expect(onCancel).toHaveBeenCalledTimes(1);
    });

    it('calls onCancel when clicking backdrop', async () => {
      const user = userEvent.setup();
      const onCancel = vi.fn();
      const { container } = render(
        <ConfirmModal {...defaultProps} onCancel={onCancel} />
      );

      // Click on the backdrop (first div with fixed class)
      const backdrop = container.querySelector('.fixed');
      if (backdrop) {
        await user.click(backdrop);
        expect(onCancel).toHaveBeenCalledTimes(1);
      }
    });

    it('does not call onCancel when clicking modal content', async () => {
      const user = userEvent.setup();
      const onCancel = vi.fn();
      render(<ConfirmModal {...defaultProps} onCancel={onCancel} />);

      // Click on the title inside the modal
      await user.click(screen.getByText('Confirm Action'));
      expect(onCancel).not.toHaveBeenCalled();
    });

    it('calls onCancel when Escape key is pressed', () => {
      const onCancel = vi.fn();
      render(<ConfirmModal {...defaultProps} onCancel={onCancel} />);

      fireEvent.keyDown(window, { key: 'Escape' });
      expect(onCancel).toHaveBeenCalledTimes(1);
    });
  });

  describe('variants', () => {
    it('renders default variant', () => {
      render(<ConfirmModal {...defaultProps} variant="default" />);
      expect(screen.getByText('Confirm Action')).toBeInTheDocument();
    });

    it('renders danger variant', () => {
      render(<ConfirmModal {...defaultProps} variant="danger" />);
      expect(screen.getByText('Confirm Action')).toBeInTheDocument();
    });

    it('renders warning variant', () => {
      render(<ConfirmModal {...defaultProps} variant="warning" />);
      expect(screen.getByText('Confirm Action')).toBeInTheDocument();
    });
  });

  describe('loading state', () => {
    it('shows loading text when isLoading is true', () => {
      render(<ConfirmModal {...defaultProps} isLoading={true} loadingText="Deleting..." />);
      expect(screen.getByText('Deleting...')).toBeInTheDocument();
    });

    it('shows default loading text when loadingText not provided', () => {
      render(<ConfirmModal {...defaultProps} isLoading={true} />);
      expect(screen.getByText('Loading...')).toBeInTheDocument();
    });

    it('disables buttons when loading', () => {
      render(<ConfirmModal {...defaultProps} isLoading={true} />);
      const buttons = screen.getAllByRole('button');
      buttons.forEach(button => {
        expect(button).toBeDisabled();
      });
    });

    it('does not call onCancel when clicking backdrop while loading', async () => {
      const user = userEvent.setup();
      const onCancel = vi.fn();
      const { container } = render(
        <ConfirmModal {...defaultProps} onCancel={onCancel} isLoading={true} />
      );

      const backdrop = container.querySelector('.fixed');
      if (backdrop) {
        await user.click(backdrop);
        expect(onCancel).not.toHaveBeenCalled();
      }
    });

    it('does not call onCancel on Escape key while loading', () => {
      const onCancel = vi.fn();
      render(<ConfirmModal {...defaultProps} onCancel={onCancel} isLoading={true} />);

      fireEvent.keyDown(window, { key: 'Escape' });
      expect(onCancel).not.toHaveBeenCalled();
    });
  });
});
