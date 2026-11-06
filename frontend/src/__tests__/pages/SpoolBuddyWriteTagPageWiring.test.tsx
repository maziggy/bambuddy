/**
 * Wiring tests for SpoolBuddyWriteTagPage: the write request targets the
 * picked spool on the online device, untag clears the tag on the right
 * backend, and "create N spools" goes through the bulk endpoint with K-profiles
 * saved per created spool -- in both built-in and Spoolman inventory modes.
 * Harness mirrors SpoolBuddyWriteTagPage.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Outlet } from 'react-router-dom';
import { ToastProvider } from '../../contexts/ToastContext';
import { SpoolBuddyWriteTagPage } from '../../pages/spoolbuddy/SpoolBuddyWriteTagPage';
import { api, spoolbuddyApi } from '../../api/client';

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => ({ hasPermission: () => true }) };
});

vi.mock('../../api/client', () => ({
  api: {
    getSpools: vi.fn(),
    createSpool: vi.fn(),
    bulkCreateSpools: vi.fn(),
    saveSpoolKProfiles: vi.fn(),
    linkTagToSpool: vi.fn(),
    getSpoolmanSettings: vi.fn(),
    getSpoolmanInventorySpools: vi.fn(),
    createSpoolmanInventorySpool: vi.fn(),
    bulkCreateSpoolmanInventorySpools: vi.fn(),
    saveSpoolmanKProfiles: vi.fn(),
    linkTagToSpoolmanSpool: vi.fn(),
    getSpoolCatalog: vi.fn().mockResolvedValue([]),
    getColorCatalog: vi.fn().mockResolvedValue([]),
    getLocalPresets: vi.fn().mockResolvedValue({ filament: [] }),
    getBuiltinFilaments: vi.fn().mockResolvedValue([]),
    getPrinters: vi.fn().mockResolvedValue([]),
    getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
    orcaCloudStatus: vi.fn().mockResolvedValue({ connected: false }),
  },
  spoolbuddyApi: {
    getDevices: vi.fn(),
    writeTag: vi.fn(),
    cancelWrite: vi.fn(),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) => (typeof fallback === 'string' ? fallback : key),
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

const mApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const mSb = spoolbuddyApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const outletContext = {
  selectedPrinterId: null,
  setSelectedPrinterId: vi.fn(),
  sbState: {
    weight: null, weightStable: false, rawAdc: null, matchedSpool: null, unknownTagUid: null,
    deviceOnline: true, deviceId: 'sb-1', remainingWeight: null, netWeight: null,
  },
  setAlert: vi.fn(),
  displayBrightness: 100,
  setDisplayBrightness: vi.fn(),
  displayBlankTimeout: 0,
  setDisplayBlankTimeout: vi.fn(),
};

function renderPage() {
  function OutletWrapper() {
    return <Outlet context={outletContext} />;
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={['/spoolbuddy/write-tag']}>
          <Routes>
            <Route element={<OutletWrapper />}>
              <Route path="spoolbuddy/write-tag" element={<SpoolBuddyWriteTagPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

function spoolmanMode(on: boolean) {
  mApi.getSpoolmanSettings.mockResolvedValue({
    spoolman_enabled: on ? 'true' : 'false',
    spoolman_url: on ? 'http://spoolman.test' : '',
  });
}

const untagged = { id: 31, material: 'PETG', brand: 'Acme', label_weight: 1000, weight_used: 0, tag_uid: null, tray_uuid: null, archived_at: null, rgba: '00FF00FF' };
const tagged = { id: 32, material: 'ABS', brand: 'Zeta', label_weight: 1000, weight_used: 0, tag_uid: '04AABBCCDD', tray_uuid: null, archived_at: null, rgba: '0000FFFF' };

async function openQuickAddFormWithQuantity(qty: number) {
  fireEvent.click(screen.getByText('New Spool'));
  await waitFor(() => expect(screen.getByText('Create Spool')).toBeDefined());
  const material = screen.getAllByRole('combobox').find((el) => el.querySelector('option[value="PLA"]'));
  fireEvent.change(material!, { target: { value: 'PLA' } });
  fireEvent.click(screen.getByText('Full'));
  await waitFor(() => expect(screen.getByText('Quick Add')).toBeDefined());
  const quickAddToggle = screen.getByText('Quick Add').parentElement?.querySelector('button');
  fireEvent.click(quickAddToggle as HTMLElement);
  const qtyLabel = await screen.findByText('inventory.quantity');
  const qtyInput = qtyLabel.parentElement!.querySelector('input') as HTMLInputElement;
  fireEvent.change(qtyInput, { target: { value: String(qty) } });
}

describe.each([
  ['built-in', false],
  ['Spoolman', true],
])('SpoolBuddyWriteTagPage wiring (%s inventory)', (_label, isSpoolman) => {
  beforeEach(() => {
    vi.clearAllMocks();
    spoolmanMode(isSpoolman);
    const list = [untagged, tagged];
    mApi.getSpools.mockResolvedValue(list);
    mApi.getSpoolmanInventorySpools.mockResolvedValue(list);
    mSb.getDevices.mockResolvedValue([{ device_id: 'sb-1', hostname: 'sb-1.local' }]);
    mSb.writeTag.mockResolvedValue({ status: 'queued' });
    mSb.cancelWrite.mockResolvedValue({ status: 'ok' });
    mApi.linkTagToSpool.mockResolvedValue({});
    mApi.linkTagToSpoolmanSpool.mockResolvedValue({});
    mApi.saveSpoolKProfiles.mockResolvedValue([]);
    mApi.saveSpoolmanKProfiles.mockResolvedValue([]);
    mApi.bulkCreateSpools.mockResolvedValue([{ id: 501 }, { id: 502 }, { id: 503 }]);
    mApi.bulkCreateSpoolmanInventorySpools.mockResolvedValue({ created: [{ id: 601 }, { id: 602 }, { id: 603 }], failed: [] });
    mApi.createSpool.mockResolvedValue({ id: 700, material: 'PLA' });
    mApi.createSpoolmanInventorySpool.mockResolvedValue({ id: 800, material: 'PLA' });
  });

  it('Write Tag queues writeTag(deviceId, selectedSpoolId); Cancel sends cancelWrite(deviceId)', async () => {
    renderPage();
    fireEvent.click(await screen.findByText('#31'));
    await waitFor(() => expect((screen.getByText('Write Tag') as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByText('Write Tag'));

    await waitFor(() => expect(mSb.writeTag).toHaveBeenCalledTimes(1));
    expect(mSb.writeTag).toHaveBeenCalledWith('sb-1', 31);

    fireEvent.click(await screen.findByText('Cancel'));
    await waitFor(() => expect(mSb.cancelWrite).toHaveBeenCalledWith('sb-1'));
  });

  it('Untag on the Replace tab clears the tag via the active backend only', async () => {
    renderPage();
    fireEvent.click(screen.getByText('Replace Tag'));
    fireEvent.click(await screen.findByText('#32'));
    fireEvent.click(await screen.findByText('Untag Selected Spool'));

    if (isSpoolman) {
      await waitFor(() =>
        expect(mApi.linkTagToSpoolmanSpool).toHaveBeenCalledWith(32, { tag_uid: '', tray_uuid: '' }),
      );
      expect(mApi.linkTagToSpool).not.toHaveBeenCalled();
    } else {
      await waitFor(() =>
        expect(mApi.linkTagToSpool).toHaveBeenCalledWith(32, { tag_uid: '', tray_uuid: '', data_origin: 'manual' }),
      );
      expect(mApi.linkTagToSpoolmanSpool).not.toHaveBeenCalled();
    }
    expect(mSb.writeTag).not.toHaveBeenCalled();
  });

  it('Create with quantity 3 uses the bulk endpoint with N=3 and leaves the created K-profiles alone', async () => {
    renderPage();
    await openQuickAddFormWithQuantity(3);
    const listFetch = isSpoolman ? mApi.getSpoolmanInventorySpools : mApi.getSpools;
    const fetchesBefore = listFetch.mock.calls.length;
    fireEvent.click(screen.getAllByText('Create Spool').at(-1)!);

    const bulk = isSpoolman ? mApi.bulkCreateSpoolmanInventorySpools : mApi.bulkCreateSpools;
    const otherBulk = isSpoolman ? mApi.bulkCreateSpools : mApi.bulkCreateSpoolmanInventorySpools;
    const saveK = isSpoolman ? mApi.saveSpoolmanKProfiles : mApi.saveSpoolKProfiles;
    const otherSaveK = isSpoolman ? mApi.saveSpoolKProfiles : mApi.saveSpoolmanKProfiles;

    await waitFor(() => expect(bulk).toHaveBeenCalledTimes(1));
    const [payload, qty] = bulk.mock.calls[0];
    expect(qty).toBe(3);
    expect(payload).toMatchObject({ material: 'PLA', tag_uid: null, tray_uuid: null });
    expect(otherBulk).not.toHaveBeenCalled();
    expect(mApi.createSpool).not.toHaveBeenCalled();
    expect(mApi.createSpoolmanInventorySpool).not.toHaveBeenCalled();

    // No PA profile picked -> nothing is saved: an empty save would clear the
    // product's profile default the create gave each spool (#1326).
    // The list refetch comes after the K-profile save would have.
    await waitFor(() => expect(listFetch.mock.calls.length).toBeGreaterThan(fetchesBefore));
    expect(saveK).not.toHaveBeenCalled();
    expect(otherSaveK).not.toHaveBeenCalled();
  });

  it('Create with quantity 1 uses the single-create endpoint', async () => {
    renderPage();
    await openQuickAddFormWithQuantity(1);
    const listFetch = isSpoolman ? mApi.getSpoolmanInventorySpools : mApi.getSpools;
    const fetchesBefore = listFetch.mock.calls.length;
    fireEvent.click(screen.getAllByText('Create Spool').at(-1)!);

    const single = isSpoolman ? mApi.createSpoolmanInventorySpool : mApi.createSpool;
    await waitFor(() => expect(single).toHaveBeenCalledTimes(1));
    expect(mApi.bulkCreateSpools).not.toHaveBeenCalled();
    expect(mApi.bulkCreateSpoolmanInventorySpools).not.toHaveBeenCalled();
    // No PA profile picked: the spool keeps the profile default the create gave it (#1326).
    // The list refetch comes after the K-profile save would have.
    await waitFor(() => expect(listFetch.mock.calls.length).toBeGreaterThan(fetchesBefore));
    expect(mApi.saveSpoolKProfiles).not.toHaveBeenCalled();
    expect(mApi.saveSpoolmanKProfiles).not.toHaveBeenCalled();
  });
});
