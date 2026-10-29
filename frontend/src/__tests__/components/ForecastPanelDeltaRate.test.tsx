/**
 * The forecast's rate when there is not enough usage history (#3407): each
 * spool's grams spread over the time it was in use, counted over the last 90
 * days. Spoolman mode always used it, and it counted shelf time as use.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
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

// The reporter's table, all registered 120 days ago: A 07-01..07-19 and B
// 08-28..10-07 used up (archived, dated by the last use as Spoolman's mapping
// does), C in use since 10-07. Same numbers as test_stock_forecast_delta_rate_3407.py.
const sm = (id: number, used: number, first: number, last: number, archived: boolean) =>
  spool(id, {
    label_weight: 1000,
    weight_used: used,
    created_at: ago(120),
    first_used: ago(first),
    last_used: ago(last),
    archived_at: archived ? ago(last) : null,
  });
const A = sm(1, 1000, 119, 101, true);
const B = sm(2, 1000, 61, 21, true);
const C = sm(3, 100, 21, 2, false);

describe('ForecastPanel: the rate without enough usage history (#3407)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.getSettings.mockResolvedValue({ forecast_global_lead_time_days: 0 });
    mocked.getUiSettings.mockResolvedValue({ forecast_global_lead_time_days: 0 });
    mocked.getAuthStatus.mockResolvedValue({ auth_enabled: false, requires_setup: false });
    mocked.getSkuSettings.mockResolvedValue([]);
    mocked.getAllUsageHistory.mockResolvedValue([]);
    mocked.getShoppingList.mockResolvedValue([]);
  });

  it("the reporter's SKU: 1100 g over the last 90 days, not 1100 g over 120", async () => {
    render(<ForecastPanel spools={[A, B, C]} spoolmanMode />);
    // 1100 / 90 = 12.2; it showed 9.2.
    expect(await screen.findByText('12.2g/d')).toBeInTheDocument();
  });

  it('shelf time before the first use is not use', async () => {
    render(<ForecastPanel spools={[C]} spoolmanMode />);
    // 100 g since the first use 21 days ago, not since registering 120 days ago.
    expect(await screen.findByText('4.8g/d')).toBeInTheDocument();
  });

  it("a spool's grams count as far as its use falls inside the window", async () => {
    // Scenario J of the backend's parity table: 90 of the older spool's 400 g.
    const old = spool(1, { label_weight: 1000, weight_used: 400, created_at: ago(400) });
    const young = spool(2, { label_weight: 1000, weight_used: 100, created_at: ago(5) });
    render(<ForecastPanel spools={[old, young]} />);
    expect(await screen.findByText('2.1g/d')).toBeInTheDocument();
  });

  it('a spool ends at its last use', async () => {
    // Used from 150 to 60 days ago: 30 of its 90 days of use, 300 of its 900 g,
    // fall inside the window. Same as the backend's test.
    render(<ForecastPanel spools={[sm(1, 900, 150, 60, false)]} spoolmanMode />);
    expect(await screen.findByText('3.3g/d')).toBeInTheDocument();
  });

  it('a spool of one print is not dropped', async () => {
    // First and last use the same moment: 200 g at once, 10 days ago.
    render(<ForecastPanel spools={[sm(1, 200, 10, 10, false)]} spoolmanMode />);
    expect(await screen.findByText('20.0g/d')).toBeInTheDocument();
  });

  it("a built-in spool's first use is its earliest usage record", async () => {
    // One day of history: no trend, but it says when the spool came into use.
    mocked.getAllUsageHistory.mockResolvedValue([
      { id: 1, spool_id: 1, created_at: ago(30), weight_used: 100 } as SpoolUsageRecord,
    ]);
    render(<ForecastPanel spools={[spool(1, { label_weight: 1000, weight_used: 300, created_at: ago(120) })]} />);
    expect(await screen.findByText('10.0g/d')).toBeInTheDocument();
  });

  it('a spool archived long ago and never used does not stretch the window', async () => {
    const never = spool(9, { label_weight: 1000, weight_used: 0, created_at: ago(400), archived_at: ago(400) });
    render(<ForecastPanel spools={[never, C]} spoolmanMode />);
    // C alone: 100 g over the 21 days since its first use, not over 90.
    expect(await screen.findByText('4.8g/d')).toBeInTheDocument();
  });

  it("an earlier spool's rows under a reused id are not this spool's", async () => {
    // Spool 6 registered 15 days ago; rows from 40 and 30 days ago name id 6
    // but belong to a spool Spoolman deleted before handing the id on.
    const reused = spool(6, { label_weight: 1000, weight_used: 60, created_at: ago(15), first_used: ago(15) });
    mocked.getAllUsageHistory.mockResolvedValue(
      [40, 30, 10, 5].map(
        (d, i) => ({ id: i + 1, spool_id: null, spoolman_spool_id: 6, created_at: ago(d), weight_used: 30 }) as SpoolUsageRecord,
      ),
    );
    render(<ForecastPanel spools={[reused]} spoolmanMode />);
    // Only its own two rows: 30 g over the 5 days between them.
    expect(await screen.findByText('6.0g/d')).toBeInTheDocument();
  });

  it('a SKU not used lately shows a rate of 0 without dividing by it', async () => {
    mocked.getSkuSettings.mockResolvedValue([
      {
        material: 'PLA', subtype: 'Matte', brand: 'Polymaker', color_name: 'Red',
        lead_time_days: 7, safety_margin_value: 200, safety_margin_unit: 'g', alerts_snoozed: false,
      },
    ]);
    const idle = spool(1, { label_weight: 1000, weight_used: 300, created_at: ago(300), last_used: ago(120) });
    render(<ForecastPanel spools={[idle]} />);

    fireEvent.click(await screen.findByText('0.0g/d'));
    expect(await screen.findByText('200')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('Infinity');
    // Nor in the hint behind the info icon.
    expect(document.body.innerHTML).not.toContain('Infinity');
  });
});
