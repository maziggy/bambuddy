/**
 * Used-up spools still count towards a SKU's rate (#3399): an archived spool is
 * not stock, but one archived in the last 90 days adds what it was used for.
 * The backend's stock_forecast applies the same rule to the stock alerts.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { ForecastPanel } from '../../components/ForecastPanel';
import { api } from '../../api/client';
import type { InventorySpool, SpoolUsageRecord } from '../../api/client';

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return {
    ...actual,
    useAuth: () => ({ hasPermission: () => true, hasAnyPermission: () => true, authEnabled: false, user: null }),
  };
});

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSettings: vi.fn(),
      getUiSettings: vi.fn(),
      getAuthStatus: vi.fn(),
      getSkuSettings: vi.fn(),
      getAllUsageHistory: vi.fn(),
      getShoppingList: vi.fn(),
      addToShoppingList: vi.fn(),
      removeFromShoppingList: vi.fn(),
      clearShoppingList: vi.fn(),
      updateShoppingListStatus: vi.fn(),
      bulkCreateSpools: vi.fn(),
      upsertSkuSettings: vi.fn(),
      updateSettings: vi.fn(),
    },
  };
});

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const spool = (id: number, over: Partial<InventorySpool> = {}): InventorySpool => ({
  id,
  material: 'PLA',
  subtype: 'Matte',
  brand: 'Polymaker',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 750,
  core_weight: 250,
  core_weight_catalog_id: null,
  weight_used: 100,
  slicer_filament: null,
  slicer_filament_name: null,
  nozzle_temp_min: null,
  nozzle_temp_max: null,
  note: null,
  added_full: null,
  last_used: null,
  encode_time: null,
  tag_uid: null,
  tray_uuid: null,
  data_origin: 'manual',
  tag_type: null,
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-01T00:00:00Z',
  k_profiles: [],
  cost_per_kg: null,
  last_scale_weight: null,
  last_weighed_at: null,
  weight_locked: false,
  category: 'Active',
  low_stock_threshold_pct: null,
  ...over,
});

const DAY = 86400000;
const ago = (days: number) => new Date(Date.now() - days * DAY).toISOString();

// The reporter's case: 16 g on the spool that is left, 900 g on one archived since
// (with 100 g still on it, which is not stock).
const stock = spool(1, { label_weight: 1000, weight_used: 16, created_at: ago(20) });
const usedUp = (archivedDaysAgo: number) =>
  spool(2, { label_weight: 1000, weight_used: 900, created_at: ago(100), archived_at: ago(archivedDaysAgo) });

const usage = (id: number, spoolId: number, daysAgo: number, grams: number): SpoolUsageRecord =>
  ({ id, spool_id: spoolId, created_at: ago(daysAgo), weight_used: grams }) as SpoolUsageRecord;

describe('ForecastPanel: used-up spools (#3399)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSettings.mockResolvedValue({ forecast_global_lead_time_days: 0 });
    mocked.getUiSettings.mockResolvedValue({ forecast_global_lead_time_days: 0 });
    mocked.getAuthStatus.mockResolvedValue({ auth_enabled: false, requires_setup: false });
    mocked.getSkuSettings.mockResolvedValue([]);
    mocked.getAllUsageHistory.mockResolvedValue([]);
    mocked.getShoppingList.mockResolvedValue([]);
  });

  it('counts a spool archived 10 days ago towards the rate, not the stock', async () => {
    render(<ForecastPanel spools={[stock, usedUp(10)]} />);

    // (16 + 900) g over the 100 days since the older spool, not 16 g over 20.
    expect(await screen.findByText('9.2g/d')).toBeInTheDocument();
    expect(screen.getByText('984g')).toBeInTheDocument();
  });

  it('leaves out a spool archived over 90 days ago', async () => {
    render(<ForecastPanel spools={[stock, usedUp(91)]} />);

    expect(await screen.findByText('0.8g/d')).toBeInTheDocument();
  });

  it('does not forecast a SKU whose spools are all archived', async () => {
    render(<ForecastPanel spools={[usedUp(10)]} />);

    await waitFor(() => expect(mocked.getAllUsageHistory).toHaveBeenCalled());
    expect(screen.queryByText(/g\/d$/)).toBeNull();
    expect(screen.queryByText('Polymaker')).toBeNull();
  });

  it('uses the usage history of a recently archived spool', async () => {
    // The stock spool alone has one day of history, too little for a trend.
    mocked.getAllUsageHistory.mockResolvedValue([usage(1, 1, 2, 16), usage(2, 2, 20, 300), usage(3, 2, 10, 700)]);
    render(<ForecastPanel spools={[stock, usedUp(5)]} />);

    expect(await screen.findByText('Trend')).toBeInTheDocument();
  });
});
