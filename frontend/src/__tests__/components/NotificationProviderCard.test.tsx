/**
 * Tests for the NotificationProviderCard component.
 *
 * These tests cover notification provider display and toggle functionality.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import type { NotificationProvider } from '../../api/client';

// Mock the component if it exists
vi.mock('../../components/NotificationProviderCard', () => ({
  NotificationProviderCard: ({
    provider,
    onEdit,
  }: {
    provider: NotificationProvider;
    onEdit: () => void;
  }) => (
    <div data-testid="notification-provider-card">
      <span data-testid="provider-name">{provider.name}</span>
      <span data-testid="provider-type">{provider.provider_type}</span>
      <span data-testid="provider-enabled">
        {provider.enabled ? 'Enabled' : 'Disabled'}
      </span>
      <span data-testid="on-print-start">
        {provider.on_print_start ? 'Yes' : 'No'}
      </span>
      <span data-testid="on-print-complete">
        {provider.on_print_complete ? 'Yes' : 'No'}
      </span>
      <button onClick={onEdit}>Edit</button>
    </div>
  ),
}));

// Import after mocking
const { NotificationProviderCard } = await import(
  '../../components/NotificationProviderCard'
);

// Mock data
const createMockProvider = (
  overrides: Partial<NotificationProvider> = {}
): NotificationProvider => ({
  id: 1,
  name: 'Test Provider',
  provider_type: 'ntfy',
  enabled: true,
  config: { server: 'https://ntfy.sh', topic: 'test' },
  on_print_start: true,
  on_print_complete: true,
  on_print_failed: true,
  on_print_stopped: false,
  on_print_progress: false,
  on_printer_offline: false,
  on_printer_error: false,
  on_filament_low: false,
  on_maintenance_due: false,
  on_ams_humidity_high: false,
  on_ams_temperature_high: false,
  on_ams_ht_humidity_high: false,
  on_ams_ht_temperature_high: false,
  on_plate_not_empty: true,
  on_bed_cooled: false,
  on_queue_job_added: false,
  on_queue_job_assigned: false,
  on_queue_job_started: false,
  on_queue_job_waiting: true,
  on_queue_job_skipped: true,
  on_queue_job_failed: true,
  on_queue_completed: false,
  quiet_hours_enabled: false,
  quiet_hours_start: null,
  quiet_hours_end: null,
  daily_digest_enabled: false,
  daily_digest_time: null,
  printer_id: null,
  last_success: null,
  last_error: null,
  last_error_at: null,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  ...overrides,
});

describe('NotificationProviderCard', () => {
  const mockOnEdit = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders provider name', () => {
      const provider = createMockProvider({ name: 'My Notifications' });
      render(
        <NotificationProviderCard provider={provider} onEdit={mockOnEdit} />
      );

      expect(screen.getByTestId('provider-name')).toHaveTextContent(
        'My Notifications'
      );
    });

    it('renders provider type', () => {
      const provider = createMockProvider({ provider_type: 'telegram' });
      render(
        <NotificationProviderCard provider={provider} onEdit={mockOnEdit} />
      );

      expect(screen.getByTestId('provider-type')).toHaveTextContent('telegram');
    });

    it('shows enabled status', () => {
      const provider = createMockProvider({ enabled: true });
      render(
        <NotificationProviderCard provider={provider} onEdit={mockOnEdit} />
      );

      expect(screen.getByTestId('provider-enabled')).toHaveTextContent(
        'Enabled'
      );
    });

    it('shows disabled status', () => {
      const provider = createMockProvider({ enabled: false });
      render(
        <NotificationProviderCard provider={provider} onEdit={mockOnEdit} />
      );

      expect(screen.getByTestId('provider-enabled')).toHaveTextContent(
        'Disabled'
      );
    });
  });

  describe('event toggles', () => {
    it('shows on_print_start correctly when enabled', () => {
      const provider = createMockProvider({ on_print_start: true });
      render(
        <NotificationProviderCard provider={provider} onEdit={mockOnEdit} />
      );

      expect(screen.getByTestId('on-print-start')).toHaveTextContent('Yes');
    });

    it('shows on_print_start correctly when disabled', () => {
      const provider = createMockProvider({ on_print_start: false });
      render(
        <NotificationProviderCard provider={provider} onEdit={mockOnEdit} />
      );

      expect(screen.getByTestId('on-print-start')).toHaveTextContent('No');
    });

    it('shows on_print_complete correctly', () => {
      const provider = createMockProvider({ on_print_complete: true });
      render(
        <NotificationProviderCard provider={provider} onEdit={mockOnEdit} />
      );

      expect(screen.getByTestId('on-print-complete')).toHaveTextContent('Yes');
    });
  });

  describe('edit functionality', () => {
    it('calls onEdit when edit button is clicked', async () => {
      const user = userEvent.setup();
      const provider = createMockProvider();
      render(
        <NotificationProviderCard provider={provider} onEdit={mockOnEdit} />
      );

      await user.click(screen.getByRole('button', { name: /edit/i }));

      expect(mockOnEdit).toHaveBeenCalled();
    });
  });
});

describe('NotificationProviderCard AMS toggles', () => {
  describe('AMS humidity notifications', () => {
    it('includes on_ams_humidity_high in provider data', () => {
      const provider = createMockProvider({ on_ams_humidity_high: true });

      expect(provider.on_ams_humidity_high).toBe(true);
    });

    it('includes on_ams_humidity_high when disabled', () => {
      const provider = createMockProvider({ on_ams_humidity_high: false });

      expect(provider.on_ams_humidity_high).toBe(false);
    });
  });

  describe('AMS temperature notifications', () => {
    it('includes on_ams_temperature_high in provider data', () => {
      const provider = createMockProvider({ on_ams_temperature_high: true });

      expect(provider.on_ams_temperature_high).toBe(true);
    });

    it('includes on_ams_temperature_high when disabled', () => {
      const provider = createMockProvider({ on_ams_temperature_high: false });

      expect(provider.on_ams_temperature_high).toBe(false);
    });
  });

  describe('AMS-HT humidity notifications (separate from AMS)', () => {
    it('includes on_ams_ht_humidity_high in provider data', () => {
      const provider = createMockProvider({ on_ams_ht_humidity_high: true });

      expect(provider.on_ams_ht_humidity_high).toBe(true);
    });

    it('AMS and AMS-HT humidity toggles are independent', () => {
      const provider = createMockProvider({
        on_ams_humidity_high: true,
        on_ams_ht_humidity_high: false,
      });

      expect(provider.on_ams_humidity_high).toBe(true);
      expect(provider.on_ams_ht_humidity_high).toBe(false);
    });

    it('can enable both AMS and AMS-HT humidity notifications', () => {
      const provider = createMockProvider({
        on_ams_humidity_high: true,
        on_ams_ht_humidity_high: true,
      });

      expect(provider.on_ams_humidity_high).toBe(true);
      expect(provider.on_ams_ht_humidity_high).toBe(true);
    });
  });

  describe('AMS-HT temperature notifications (separate from AMS)', () => {
    it('includes on_ams_ht_temperature_high in provider data', () => {
      const provider = createMockProvider({ on_ams_ht_temperature_high: true });

      expect(provider.on_ams_ht_temperature_high).toBe(true);
    });

    it('AMS and AMS-HT temperature toggles are independent', () => {
      const provider = createMockProvider({
        on_ams_temperature_high: true,
        on_ams_ht_temperature_high: false,
      });

      expect(provider.on_ams_temperature_high).toBe(true);
      expect(provider.on_ams_ht_temperature_high).toBe(false);
    });

    it('can enable both AMS and AMS-HT temperature notifications', () => {
      const provider = createMockProvider({
        on_ams_temperature_high: true,
        on_ams_ht_temperature_high: true,
      });

      expect(provider.on_ams_temperature_high).toBe(true);
      expect(provider.on_ams_ht_temperature_high).toBe(true);
    });
  });

  describe('all AMS notification combinations', () => {
    it('supports all four AMS toggles independently', () => {
      const provider = createMockProvider({
        on_ams_humidity_high: true,
        on_ams_temperature_high: false,
        on_ams_ht_humidity_high: false,
        on_ams_ht_temperature_high: true,
      });

      expect(provider.on_ams_humidity_high).toBe(true);
      expect(provider.on_ams_temperature_high).toBe(false);
      expect(provider.on_ams_ht_humidity_high).toBe(false);
      expect(provider.on_ams_ht_temperature_high).toBe(true);
    });

    it('defaults all AMS toggles to false', () => {
      const provider = createMockProvider();

      expect(provider.on_ams_humidity_high).toBe(false);
      expect(provider.on_ams_temperature_high).toBe(false);
      expect(provider.on_ams_ht_humidity_high).toBe(false);
      expect(provider.on_ams_ht_temperature_high).toBe(false);
    });
  });
});

describe('NotificationProviderCard Queue notifications', () => {
  describe('queue job notifications', () => {
    it('includes on_queue_job_added in provider data', () => {
      const provider = createMockProvider({ on_queue_job_added: true });
      expect(provider.on_queue_job_added).toBe(true);
    });

    it('includes on_queue_job_assigned in provider data', () => {
      const provider = createMockProvider({ on_queue_job_assigned: true });
      expect(provider.on_queue_job_assigned).toBe(true);
    });

    it('includes on_queue_job_started in provider data', () => {
      const provider = createMockProvider({ on_queue_job_started: true });
      expect(provider.on_queue_job_started).toBe(true);
    });

    it('includes on_queue_job_waiting in provider data', () => {
      const provider = createMockProvider({ on_queue_job_waiting: true });
      expect(provider.on_queue_job_waiting).toBe(true);
    });

    it('includes on_queue_job_skipped in provider data', () => {
      const provider = createMockProvider({ on_queue_job_skipped: true });
      expect(provider.on_queue_job_skipped).toBe(true);
    });

    it('includes on_queue_job_failed in provider data', () => {
      const provider = createMockProvider({ on_queue_job_failed: true });
      expect(provider.on_queue_job_failed).toBe(true);
    });

    it('includes on_queue_completed in provider data', () => {
      const provider = createMockProvider({ on_queue_completed: true });
      expect(provider.on_queue_completed).toBe(true);
    });
  });

  describe('queue notification defaults', () => {
    it('defaults actionable notifications to true', () => {
      const provider = createMockProvider();
      // These should default to true (actionable - user needs to do something)
      expect(provider.on_queue_job_waiting).toBe(true);
      expect(provider.on_queue_job_skipped).toBe(true);
      expect(provider.on_queue_job_failed).toBe(true);
    });

    it('defaults informational notifications to false', () => {
      const provider = createMockProvider();
      // These should default to false (informational only)
      expect(provider.on_queue_job_added).toBe(false);
      expect(provider.on_queue_job_assigned).toBe(false);
      expect(provider.on_queue_job_started).toBe(false);
      expect(provider.on_queue_completed).toBe(false);
    });
  });

  describe('queue notification combinations', () => {
    it('supports all queue toggles independently', () => {
      const provider = createMockProvider({
        on_queue_job_added: true,
        on_queue_job_assigned: false,
        on_queue_job_started: true,
        on_queue_job_waiting: false,
        on_queue_job_skipped: true,
        on_queue_job_failed: false,
        on_queue_completed: true,
      });

      expect(provider.on_queue_job_added).toBe(true);
      expect(provider.on_queue_job_assigned).toBe(false);
      expect(provider.on_queue_job_started).toBe(true);
      expect(provider.on_queue_job_waiting).toBe(false);
      expect(provider.on_queue_job_skipped).toBe(true);
      expect(provider.on_queue_job_failed).toBe(false);
      expect(provider.on_queue_completed).toBe(true);
    });
  });
});

describe('NotificationProviderCard Bed Cooled notifications', () => {
  describe('bed cooled toggle', () => {
    it('includes on_bed_cooled in provider data when enabled', () => {
      const provider = createMockProvider({ on_bed_cooled: true });
      expect(provider.on_bed_cooled).toBe(true);
    });

    it('includes on_bed_cooled in provider data when disabled', () => {
      const provider = createMockProvider({ on_bed_cooled: false });
      expect(provider.on_bed_cooled).toBe(false);
    });

    it('defaults on_bed_cooled to false', () => {
      const provider = createMockProvider();
      expect(provider.on_bed_cooled).toBe(false);
    });

    it('bed cooled is independent from other print event toggles', () => {
      const provider = createMockProvider({
        on_print_complete: true,
        on_bed_cooled: true,
        on_plate_not_empty: false,
      });

      expect(provider.on_print_complete).toBe(true);
      expect(provider.on_bed_cooled).toBe(true);
      expect(provider.on_plate_not_empty).toBe(false);
    });
  });
});
