/**
 * Tests for the HMSErrorModal component.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import {
  HMSErrorModal,
  filterKnownHMSErrors,
  filterUncountedHMSErrors,
  isSevereHMSError,
} from '../../components/HMSErrorModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import type { HMSError } from '../../api/client';

// The backend resolves each fault's text from the catalogue generated out of
// Bambu Studio and sends it as `description` (#2728); the fixtures carry it the
// way the status response does. The frontend has no table of its own.

// 0300_400C, "The task was canceled."
const knownError: HMSError = {
  attr: 0x0300,
  code: '0x400C',
  severity: 1,
  description: 'The task was canceled.',
};

// A code Bambu publishes no text for: description null, no actions.
const unknownError: HMSError = {
  attr: 0xFFFF,
  code: '0xFFFF',
  severity: 1,
  description: null,
};

// 0700_8011, AMS filament runout (#2587).
const runoutError: HMSError = {
  attr: 0x0700,
  code: '0x8011',
  severity: 2,
  description: 'AMS filament ran out. Please insert a new filament into the same AMS slot.',
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

    it('lists a fault without text collapsed instead of hiding it (#2728)', () => {
      render(<HMSErrorModal {...defaultProps} errors={[unknownError]} />);
      expect(screen.queryByText('No errors')).not.toBeInTheDocument();
      expect(screen.getByText('Also reported, not counted (1)')).toBeInTheDocument();
      expect(screen.getByText('[FFFF-FFFF]')).toBeInTheDocument();
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

    it('offers clear when the printer holds only faults without text', () => {
      // The printer is still holding them, and clearing is how they go away.
      render(<HMSErrorModal {...defaultProps} errors={[unknownError]} />);
      expect(screen.getByText('Clear Errors')).toBeInTheDocument();
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
      // Bambu's own text, which says to update Studio or Handy.
      description: 'MQTT Command verification failed. Please update Studio (including the network plugin) or Handy.',
    };

    it('surfaces the error in Bambuddy\'s words, not Bambu\'s', () => {
      render(<HMSErrorModal {...defaultProps} errors={[verifyFailedError]} />);
      expect(screen.queryByText('No errors')).not.toBeInTheDocument();
      expect(screen.getByText(/could not verify it/i)).toBeInTheDocument();
      expect(screen.queryByText(/update Studio/i)).not.toBeInTheDocument();
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

  describe('severity is Bambu\'s alert level (#2728)', () => {
    it.each([
      [1, 'Error'],
      [2, 'Warning'],
      [3, 'Notice'],
      [0, 'Unknown level'],
      [6, 'Unknown level'],
    ])('labels level %i as %s', (severity, label) => {
      render(<HMSErrorModal {...defaultProps} errors={[{ ...knownError, severity }]} />);
      expect(screen.getByText(label)).toBeInTheDocument();
    });

    it('treats stopping and pausing faults as severe, and nothing else', () => {
      expect([0, 1, 2, 3, 4, 6].map((severity) => isSevereHMSError({ ...knownError, severity }))).toEqual([
        false, true, true, false, false, false,
      ]);
    });
  });

  describe('what counts (#2728)', () => {
    // The reporter's three P2S faults as the backend now sends them. Bambu
    // describes the first; it lists the other two with no text.
    const p2s: HMSError[] = [
      {
        attr: 83886848,
        code: '0x2000e',
        severity: 2,
        full_code: '050003000002000E',
        description: "Some modules are incompatible with the printer's firmware version.",
      },
      { attr: 83887616, code: '0x20070', severity: 2, full_code: '0500060000020070', description: null },
      { attr: 83886592, code: '0x3000a', severity: 3, full_code: '050002000003000A', description: null },
    ];

    it('counts a fault Bambu describes and leaves the others out of the count', () => {
      expect(filterKnownHMSErrors(p2s).map((e) => e.full_code)).toEqual(['050003000002000E']);
      expect(filterUncountedHMSErrors(p2s).map((e) => e.full_code)).toEqual([
        '0500060000020070',
        '050002000003000A',
      ]);
    });

    it('still counts an actionable fault without text, so its buttons render', () => {
      const actionable: HMSError = { ...unknownError, actions: ['IGNORE_RESUME'] };
      expect(filterKnownHMSErrors([actionable])).toHaveLength(1);
      expect(filterUncountedHMSErrors([actionable])).toHaveLength(0);
    });

    it('shows every fault the printer holds, counted ones first', () => {
      render(<HMSErrorModal {...defaultProps} errors={p2s} />);
      expect(screen.getByText('[0500-0300-0002-000E]')).toBeInTheDocument();
      expect(screen.getByText(/Some modules are incompatible/)).toBeInTheDocument();
      expect(screen.getByText('Also reported, not counted (2)')).toBeInTheDocument();
      expect(screen.getByText('[0500-0600-0002-0070]')).toBeInTheDocument();
      expect(screen.getByText('[0500-0200-0003-000A]')).toBeInTheDocument();
    });

    it('does not count an hms[] notice without actions, but lists it with its text', () => {
      // 0300-9700-0003-0001: a printer can hold this through a whole print.
      const topCover: HMSError = {
        attr: 0x03009700,
        code: '0x30001',
        severity: 3,
        full_code: '0300970000030001',
        description: 'The top cover is open.',
      };
      expect(filterKnownHMSErrors([topCover])).toHaveLength(0);
      render(<HMSErrorModal {...defaultProps} errors={[topCover]} />);
      expect(screen.getByText('Also reported, not counted (1)')).toBeInTheDocument();
      expect(screen.getByText('The top cover is open.')).toBeInTheDocument();
    });

    it('counts an hms[] notice that offers actions', () => {
      const notice: HMSError = {
        attr: 0x03009700,
        code: '0x30001',
        severity: 3,
        full_code: '0300970000030001',
        description: 'The top cover is open.',
        actions: ['OK_BUTTON'],
      };
      expect(filterKnownHMSErrors([notice])).toHaveLength(1);
    });

    it('still counts a print_error prompt at the notice level', () => {
      const prompt: HMSError = {
        attr: 0x1880c003,
        code: '0xc003',
        severity: 3,
        full_code: '1880C003',
        description: 'Unable to start drying.',
      };
      expect(filterKnownHMSErrors([prompt])).toHaveLength(1);
    });

    it('never counts level 0', () => {
      expect(filterKnownHMSErrors([{ ...knownError, severity: 0 }])).toHaveLength(0);
    });

    it('has no collapsed group when everything is described', () => {
      render(<HMSErrorModal {...defaultProps} errors={[knownError]} />);
      expect(screen.queryByTestId('hms-uncounted')).not.toBeInTheDocument();
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
