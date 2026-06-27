/**
 * Tests for the HMSErrorModal component.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { HMSErrorModal } from '../../components/HMSErrorModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import type { HMSError } from '../../api/client';

// Error code 0300_400C = "The task was canceled." (known code in the database)
const knownError: HMSError = {
  attr: 0x0300,
  code: '0x400C',
  severity: 2,
};

// Error code FFFF_FFFF = unknown (not in the database)
const unknownError: HMSError = {
  attr: 0xFFFF,
  code: '0xFFFF',
  severity: 1,
};

// Unknown code (not in the database) that nonetheless carries user actions —
// e.g. H2C 0500_809C "Build plate not properly positioned". Must NOT be hidden. (#1840)
const actionableUnknownError: HMSError = {
  attr: 0x0500809C,
  code: '0x809C',
  severity: 3,
  actions: ['IGNORE_RESUME', 'PROBLEM_SOLVED_RESUME'],
  full_code: '0500809C',
};

describe('HMSErrorModal', () => {
  const defaultProps = {
    printerName: 'Test Printer',
    errors: [knownError],
    onClose: vi.fn(),
    printerId: 1,
    hasPermission: vi.fn().mockReturnValue(true) as unknown as (permission: 'printers:control') => boolean,
  };

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders the modal title with printer name', () => {
      render(<HMSErrorModal {...defaultProps} />);
      expect(screen.getByText('Errors - Test Printer')).toBeInTheDocument();
    });

    it('shows error description for known error codes', () => {
      render(<HMSErrorModal {...defaultProps} />);
      expect(screen.getByText('The task was canceled.')).toBeInTheDocument();
    });

    it('shows no errors message when all errors are unknown', () => {
      render(<HMSErrorModal {...defaultProps} errors={[unknownError]} />);
      expect(screen.getByText('No errors')).toBeInTheDocument();
    });

    it('shows no errors message when errors array is empty', () => {
      render(<HMSErrorModal {...defaultProps} errors={[]} />);
      expect(screen.getByText('No errors')).toBeInTheDocument();
    });

    it('shows unknown errors that carry actions (never hides an actionable fault)', () => {
      render(<HMSErrorModal {...defaultProps} errors={[actionableUnknownError]} />);
      expect(screen.queryByText('No errors')).not.toBeInTheDocument();
      expect(screen.getByText('[0500-809C]')).toBeInTheDocument();
      expect(screen.getByText('Ignore this and Resume')).toBeInTheDocument();
      expect(screen.getByText('Problem Solved and Resume')).toBeInTheDocument();
    });

    it('shows a fallback description for actionable codes missing from the catalog', () => {
      render(<HMSErrorModal {...defaultProps} errors={[actionableUnknownError]} />);
      expect(
        screen.getByText('Unrecognized HMS code — open the Bambu Lab Wiki for details.'),
      ).toBeInTheDocument();
    });
  });

  describe('clear errors button', () => {
    it('shows clear button when there are known errors', () => {
      render(<HMSErrorModal {...defaultProps} />);
      expect(screen.getByText('Clear Errors')).toBeInTheDocument();
    });

    it('hides clear button when there are no known errors', () => {
      render(<HMSErrorModal {...defaultProps} errors={[]} />);
      expect(screen.queryByText('Clear Errors')).not.toBeInTheDocument();
    });

    it('hides clear button when all errors are unknown codes', () => {
      render(<HMSErrorModal {...defaultProps} errors={[unknownError]} />);
      expect(screen.queryByText('Clear Errors')).not.toBeInTheDocument();
    });

    it('disables clear button when user lacks permission', () => {
      const noPermission = vi.fn().mockReturnValue(false) as unknown as (permission: 'printers:control') => boolean;
      render(<HMSErrorModal {...defaultProps} hasPermission={noPermission} />);
      expect(screen.getByText('Clear Errors').closest('button')).toBeDisabled();
    });

    it('calls API and closes modal on successful clear', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();

      server.use(
        http.post('/api/v1/printers/1/hms/clear', () => {
          return HttpResponse.json({ success: true, message: 'HMS errors cleared' });
        })
      );

      render(<HMSErrorModal {...defaultProps} onClose={onClose} />);

      await user.click(screen.getByText('Clear Errors'));

      await waitFor(() => {
        expect(onClose).toHaveBeenCalledTimes(1);
      });
    });

    it('shows error toast on failed clear', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();

      server.use(
        http.post('/api/v1/printers/1/hms/clear', () => {
          return HttpResponse.json({ detail: 'Failed' }, { status: 500 });
        })
      );

      render(<HMSErrorModal {...defaultProps} onClose={onClose} />);

      await user.click(screen.getByText('Clear Errors'));

      await waitFor(() => {
        expect(onClose).not.toHaveBeenCalled();
      });
    });
  });

  describe('interactions', () => {
    it('calls onClose when X button is clicked', async () => {
      const user = userEvent.setup();
      const onClose = vi.fn();
      render(<HMSErrorModal {...defaultProps} onClose={onClose} />);

      // The X button is the button with the X icon in the header
      const closeButtons = screen.getAllByRole('button');
      // First button is the X close button in the header
      await user.click(closeButtons[0]);
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('calls onClose when Escape key is pressed', () => {
      const onClose = vi.fn();
      render(<HMSErrorModal {...defaultProps} onClose={onClose} />);

      fireEvent.keyDown(window, { key: 'Escape' });
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });
});
