/**
 * Tests for the EditQueueItemModal component.
 *
 * These tests focus on:
 * - Basic rendering and modal controls
 * - Print options (bed levelling, flow calibration, etc.)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { EditQueueItemModal } from '../../components/EditQueueItemModal';
import type { PrintQueueItem, Printer } from '../../api/client';

// Mock the API client to prevent actual API calls
vi.mock('../../api/client', async () => {
  const actual = await vi.importActual('../../api/client');
  return {
    ...actual,
    fetchArchivePlates: vi.fn().mockResolvedValue([]),
    fetchFilamentRequirements: vi.fn().mockResolvedValue([]),
  };
});

// Mock data
const createMockPrinter = (overrides: Partial<Printer> = {}): Printer => ({
  id: 1,
  name: 'Test Printer',
  ip_address: '192.168.1.100',
  serial_number: 'TESTSERIAL0001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  created_at: '2024-01-01T00:00:00Z',
  ...overrides,
});

const createMockQueueItem = (overrides: Partial<PrintQueueItem> = {}): PrintQueueItem => ({
  id: 1,
  printer_id: 1,
  archive_id: 1,
  position: 1,
  scheduled_time: null,
  require_previous_success: false,
  auto_off_after: false,
  manual_start: false,
  ams_mapping: null,
  plate_id: null,
  bed_levelling: true,
  flow_cali: false,
  vibration_cali: true,
  layer_inspect: false,
  timelapse: false,
  use_ams: true,
  status: 'pending',
  started_at: null,
  completed_at: null,
  error_message: null,
  created_at: '2024-01-01T00:00:00Z',
  archive_name: 'Test Print',
  archive_thumbnail: null,
  printer_name: 'Test Printer',
  print_time_seconds: 3600,
  ...overrides,
});

describe('EditQueueItemModal', () => {
  const mockOnClose = vi.fn();
  const mockOnSave = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders the modal with title', () => {
      const item = createMockQueueItem();
      const printers = [createMockPrinter()];

      render(
        <EditQueueItemModal
          item={item}
          printers={printers}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByText('Edit Queue Item')).toBeInTheDocument();
    });

    it('shows printer selector label', () => {
      const item = createMockQueueItem();
      const printers = [createMockPrinter({ name: 'My Printer' })];

      render(
        <EditQueueItemModal
          item={item}
          printers={printers}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      // The printer label should be present
      expect(screen.getByText('Printer')).toBeInTheDocument();
    });

    it('shows print options toggle', () => {
      const item = createMockQueueItem();
      const printers = [createMockPrinter()];

      render(
        <EditQueueItemModal
          item={item}
          printers={printers}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByText('Print Options')).toBeInTheDocument();
    });
  });

  describe('print options', () => {
    it('has print options toggle button', () => {
      const item = createMockQueueItem();
      const printers = [createMockPrinter()];

      render(
        <EditQueueItemModal
          item={item}
          printers={printers}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      // Print Options toggle should be present
      expect(screen.getByText('Print Options')).toBeInTheDocument();
    });

    it('print options toggle is clickable', async () => {
      const user = userEvent.setup();
      const item = createMockQueueItem();
      const printers = [createMockPrinter()];

      render(
        <EditQueueItemModal
          item={item}
          printers={printers}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      // Click should not throw an error
      const printOptionsButton = screen.getByText('Print Options');
      await user.click(printOptionsButton);

      // The button should still be in the document after clicking
      expect(screen.getByText('Print Options')).toBeInTheDocument();
    });
  });

  describe('modal controls', () => {
    it('has save button', () => {
      const item = createMockQueueItem();
      const printers = [createMockPrinter()];

      render(
        <EditQueueItemModal
          item={item}
          printers={printers}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      const saveButton = screen.getByRole('button', { name: /save/i });
      expect(saveButton).toBeInTheDocument();
    });

    it('has cancel button', () => {
      const item = createMockQueueItem();
      const printers = [createMockPrinter()];

      render(
        <EditQueueItemModal
          item={item}
          printers={printers}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      const cancelButton = screen.getByRole('button', { name: /cancel/i });
      expect(cancelButton).toBeInTheDocument();
    });

    it('calls onClose when cancel button is clicked', async () => {
      const user = userEvent.setup();
      const item = createMockQueueItem();
      const printers = [createMockPrinter()];

      render(
        <EditQueueItemModal
          item={item}
          printers={printers}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      const cancelButton = screen.getByRole('button', { name: /cancel/i });
      await user.click(cancelButton);

      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe('queue options', () => {
    it('shows queue only option', () => {
      const item = createMockQueueItem();
      const printers = [createMockPrinter()];

      render(
        <EditQueueItemModal
          item={item}
          printers={printers}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByText('Queue Only')).toBeInTheDocument();
    });

    it('shows power off option', () => {
      const item = createMockQueueItem();
      const printers = [createMockPrinter()];

      render(
        <EditQueueItemModal
          item={item}
          printers={printers}
          onClose={mockOnClose}
          onSave={mockOnSave}
        />
      );

      expect(screen.getByText(/power off/i)).toBeInTheDocument();
    });
  });
});
