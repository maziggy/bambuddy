/**
 * Tests for the HMSErrorModal component.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { HMSErrorModal, filterKnownHMSErrors } from '../../components/HMSErrorModal';
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

// Error code 0700_8011 = AMS filament runout (#2587).
const runoutError: HMSError = {
  attr: 0x0700,
  code: '0x8011',
  severity: 2,
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

  describe('runout guidance (#2587)', () => {
    it('shows the generic runout text when no guidance is provided', () => {
      render(<HMSErrorModal {...defaultProps} errors={[runoutError]} />);
      expect(
        screen.getByText('AMS filament ran out. Please insert a new filament into the same AMS slot.')
      ).toBeInTheDocument();
    });

    it('names both the expected and ran-out slot when both are resolved', () => {
      render(
        <HMSErrorModal
          {...defaultProps}
          errors={[runoutError]}
          runoutGuidance={{ expectedSlotLabel: 'AMS-A · Slot 3', ranOutSlotLabel: 'AMS-A · Slot 2' }}
        />
      );
      const p = screen.getByText(/waiting for compatible filament/i);
      expect(p.textContent).toContain('AMS-A · Slot 3');
      expect(p.textContent).toContain('AMS-A · Slot 2');
      // The misleading "same slot" text must be gone.
      expect(screen.queryByText(/into the same AMS slot/i)).not.toBeInTheDocument();
    });

    it('names only the expected slot when the ran-out slot is unknown', () => {
      render(
        <HMSErrorModal
          {...defaultProps}
          errors={[runoutError]}
          runoutGuidance={{ expectedSlotLabel: 'AMS-A · Slot 3', ranOutSlotLabel: null }}
        />
      );
      const p = screen.getByText(/waiting for compatible filament/i);
      expect(p.textContent).toContain('AMS-A · Slot 3');
    });

    it('shows an honest fallback when the slot cannot be resolved', () => {
      render(
        <HMSErrorModal
          {...defaultProps}
          errors={[runoutError]}
          runoutGuidance={{ expectedSlotLabel: null, ranOutSlotLabel: null }}
        />
      );
      expect(screen.getByText(/could not determine which slot/i)).toBeInTheDocument();
    });

    it('does not apply runout guidance to non-runout errors', () => {
      render(
        <HMSErrorModal
          {...defaultProps}
          errors={[knownError]}
          runoutGuidance={{ expectedSlotLabel: 'AMS-A · Slot 3', ranOutSlotLabel: 'AMS-A · Slot 2' }}
        />
      );
      // 0300_400C keeps its own description; no slot injection.
      expect(screen.getByText('The task was canceled.')).toBeInTheDocument();
      expect(screen.queryByText(/waiting for compatible filament/i)).not.toBeInTheDocument();
    });
  });

  describe('MQTT command verification failed (#2732)', () => {
    // attr 0x05000500, code 0x00010007 — a real P1S on firmware 01.10.00.00.
    // getShortCode() collapses this to "0500_0007", which matches nothing, so
    // before #2732 filterKnownHMSErrors dropped the one error that explained
    // why the printer accepted every job and started none of them.
    const verifyFailedError: HMSError = {
      attr: 0x05000500,
      code: '0x10007',
      severity: 1,
      full_code: '0500050000010007',
    };

    it('surfaces the error instead of filtering it out', () => {
      render(<HMSErrorModal {...defaultProps} errors={[verifyFailedError]} />);
      expect(screen.queryByText('No errors')).not.toBeInTheDocument();
      expect(screen.getByText(/could not verify it/i)).toBeInTheDocument();
    });

    it('counts towards the known-error filter', () => {
      expect(filterKnownHMSErrors([verifyFailedError])).toHaveLength(1);
    });

    it('shows the remedy, not just the fault', () => {
      render(<HMSErrorModal {...defaultProps} errors={[verifyFailedError]} />);
      expect(screen.getByText(/Enable Developer Mode on the printer/i)).toBeInTheDocument();
    });

    it('displays the code the printer screen shows, not the truncated form', () => {
      render(<HMSErrorModal {...defaultProps} errors={[verifyFailedError]} />);
      expect(screen.getByText('[0500-0500-0001-0007]')).toBeInTheDocument();
      expect(screen.queryByText('[0500-0007]')).not.toBeInTheDocument();
    });

    it('leaves short-code errors on the two-group display', () => {
      render(<HMSErrorModal {...defaultProps} errors={[knownError]} />);
      expect(screen.getByText('[0300-400C]')).toBeInTheDocument();
    });

    it('does not add the remedy line to other errors', () => {
      render(<HMSErrorModal {...defaultProps} errors={[knownError]} />);
      expect(screen.queryByText(/Enable Developer Mode/i)).not.toBeInTheDocument();
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
