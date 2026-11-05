/**
 * In Spoolman mode the forecast reads no usage history (#3406). That history
 * is the built-in inventory's, keyed by its own spool ids: a Spoolman spool
 * whose id matches one, left over from before the switch, borrowed that
 * spool's prints. The rate is the consumption delta there, as in the
 * backend's stock alerts.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
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

// The reporter's repro: spool 6, registered 40 days ago, 100 g used (2.5 g/d).
const spoolman6 = spool(6, { label_weight: 1000, weight_used: 100, created_at: ago(40) });
// Seven prints of the built-in spool 6, from half a year ago.
const builtInHistory = [190, 187, 184, 181, 178, 175, 172].map(
  (daysAgo, i) => ({ id: i + 1, spool_id: 6, created_at: ago(daysAgo), weight_used: 120 }) as SpoolUsageRecord,
);

describe('ForecastPanel: usage history in Spoolman mode (#3406)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSettings.mockResolvedValue({ forecast_global_lead_time_days: 0 });
    mocked.getUiSettings.mockResolvedValue({ forecast_global_lead_time_days: 0 });
    mocked.getAuthStatus.mockResolvedValue({ auth_enabled: false, requires_setup: false });
    mocked.getSkuSettings.mockResolvedValue([]);
    mocked.getAllUsageHistory.mockResolvedValue(builtInHistory);
    mocked.getShoppingList.mockResolvedValue([]);
  });

  it("a Spoolman spool's rate is its own consumption, not a built-in spool's history", async () => {
    render(<ForecastPanel spools={[spoolman6]} spoolmanMode />);

    expect(await screen.findByText('2.5g/d')).toBeInTheDocument();
    expect(screen.queryByText('Trend')).toBeNull();
    // The Spoolman spools' own rows are asked for (#3407); built-in rows that
    // come back anyway name no Spoolman spool.
    expect(mocked.getAllUsageHistory).toHaveBeenCalledWith(5000, undefined, true);
  });

  it("the Spoolman spools' own usage rows give Spoolman mode the trend (#3407)", async () => {
    const spoolmanHistory = [30, 20, 10].map(
      (daysAgo, i) =>
        ({ id: i + 1, spool_id: null, spoolman_spool_id: 6, created_at: ago(daysAgo), weight_used: 30 }) as SpoolUsageRecord,
    );
    mocked.getAllUsageHistory.mockResolvedValue(spoolmanHistory);
    render(<ForecastPanel spools={[spoolman6]} spoolmanMode />);

    expect(await screen.findByText('Trend')).toBeInTheDocument();
    expect(screen.getByText('3.0g/d')).toBeInTheDocument();
  });

  it('history cached in built-in mode is not used after switching to Spoolman', async () => {
    // Same query key in both modes: a disabled query still hands out its cache.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['all-usage-history-forecast'], builtInHistory);
    render(
      <QueryClientProvider client={client}>
        <ForecastPanel spools={[spoolman6]} spoolmanMode />
      </QueryClientProvider>,
    );

    expect(await screen.findByText('2.5g/d')).toBeInTheDocument();
    expect(screen.queryByText('Trend')).toBeNull();
  });

  it('the built-in inventory still uses its history', async () => {
    render(<ForecastPanel spools={[spoolman6]} />);

    expect(await screen.findByText('Trend')).toBeInTheDocument();
    expect(screen.queryByText('2.5g/d')).toBeNull();
  });
});
