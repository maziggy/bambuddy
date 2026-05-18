/**
 * Tests for billing-related PrintModal request payloads.
 */

import React from 'react';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { server } from '../mocks/server';
import { ThemeProvider } from '../../contexts/ThemeContext';

const mockShowToast = vi.fn();
const mockUseAuth = {
  user: { id: 1, username: 'finance-user', permissions: ['cost_centers:read_own', 'printers:control'] },
  authEnabled: true,
  requiresSetup: false,
  loading: false,
  isAdmin: false,
  login: vi.fn(),
  loginWithToken: vi.fn(),
  logout: vi.fn(),
  refreshUser: vi.fn(),
  refreshAuth: vi.fn(),
  hasPermission: vi.fn((permission: string) => permission === 'cost_centers:read_own' || permission === 'printers:control'),
  hasAnyPermission: vi.fn(() => true),
  hasAllPermissions: vi.fn(() => true),
  canModify: vi.fn(() => true),
};

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return {
    ...actual,
    useAuth: () => mockUseAuth,
  };
});

vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return {
    ...actual,
    useToast: () => ({ showToast: mockShowToast }),
  };
});

import { PrintModal } from '../../components/PrintModal';

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ThemeProvider>{ui}</ThemeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

const mockPrinters = [
  { id: 1, name: 'X1 Carbon', model: 'X1C', ip_address: '192.168.1.100', enabled: true, is_active: true },
];

const mockQueueItem = {
  id: 9,
  printer_id: 1,
  archive_id: 1,
  position: 1,
  scheduled_time: null,
  require_previous_success: false,
  auto_off_after: false,
  gcode_injection: false,
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
  archive_name: 'Billing Print',
  archive_thumbnail: null,
  printer_name: 'X1 Carbon',
  print_time_seconds: 3600,
  batch_id: null,
  batch_name: null,
  cost_center_id: 42,
  estimated_cost: 12.34,
};

describe('PrintModal billing payloads', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/settings/', () => {
        return HttpResponse.json({
          currency: 'USD',
          default_filament_cost: 25,
          billing_enabled: true,
          default_bed_levelling: true,
          default_flow_cali: false,
          default_vibration_cali: true,
          default_layer_inspect: false,
          default_timelapse: false,
          stagger_group_size: 2,
          stagger_interval_minutes: 5,
          per_printer_mapping_expanded: false,
          date_format: 'system',
          time_format: 'system',
        });
      }),
      http.get('/api/v1/printers/', () => HttpResponse.json(mockPrinters)),
      http.get('/api/v1/printers/:id/status', () => {
        return HttpResponse.json({ connected: true, state: 'IDLE', ams: [], vt_tray: [] });
      }),
      http.get('/api/v1/archives/:id', () => {
        return HttpResponse.json({ id: 1, sliced_for_model: null });
      }),
      http.get('/api/v1/archives/:id/plates', () => {
        return HttpResponse.json({ is_multi_plate: false, plates: [] });
      }),
      http.get('/api/v1/archives/:id/filament-requirements', () => {
        return HttpResponse.json({ filaments: [] });
      }),
      http.get('/api/v1/finance/cost-centers/mine', () => {
        return HttpResponse.json([
          {
            id: 42,
            name: 'Lab',
            is_private: false,
            owner_user_id: null,
            is_active: true,
            total_balance: 0,
            total_budget: 100,
            monthly_budget: 100,
            budget_mode: 'monthly',
            budget_limit: 100,
            budget_used: 0,
            budget_available: 88,
            can_print: true,
          },
        ]);
      }),
      http.patch('/api/v1/queue/:id', async ({ request }) => {
        const body = await request.json() as Record<string, unknown>;
        expect(body.cost_center_id).toBe(42);
        expect(body.estimated_cost).toBe(12.34);
        return HttpResponse.json({ id: 9, status: 'pending' });
      })
    );
  });

  it('includes the selected cost center and estimate when saving an edit-queue item', async () => {
    const user = userEvent.setup();

    renderWithProviders(
      <PrintModal
        mode="edit-queue-item"
        archiveId={1}
        archiveName="Billing Print"
        queueItem={mockQueueItem as never}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Lab')).not.toBeNull();
    });

    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalled();
    });
  });
});
