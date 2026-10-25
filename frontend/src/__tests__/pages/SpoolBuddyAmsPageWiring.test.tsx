/**
 * Wiring tests for SpoolBuddyAmsPage slot actions: Unassign (built-in
 * inventory), Unassign (Spoolman slot-only assignment) and Unlink (Spoolman
 * tag link) must each call their own API with the picked slot's ids.
 * Harness mirrors SpoolBuddyAmsPage.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, Outlet } from 'react-router-dom';
import { ToastProvider } from '../../contexts/ToastContext';
import { SpoolBuddyAmsPage } from '../../pages/spoolbuddy/SpoolBuddyAmsPage';

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => ({ hasPermission: () => true }) };
});
vi.mock('../../components/AssignSpoolModal', () => ({ AssignSpoolModal: () => null }));
vi.mock('../../components/LinkSpoolModal', () => ({ LinkSpoolModal: () => null }));
const configureProps = vi.hoisted(() => [] as Array<Record<string, unknown>>);
vi.mock('../../components/ConfigureAmsSlotModal', () => ({
  ConfigureAmsSlotModal: (props: Record<string, unknown>) => {
    configureProps.push(props);
    return null;
  },
}));

const mutations = {
  unassignSpool: vi.fn(),
  unassignSpoolmanSlot: vi.fn(),
  unlinkSpool: vi.fn(),
};
let apiResponses: Record<string, unknown> = {};
let spoolmanStatusValue = { enabled: false, connected: false };

vi.mock('../../api/client', () => ({
  api: new Proxy({} as Record<string, unknown>, {
    get: (_t, p: string) => {
      if (p === 'getSpoolmanStatus') return () => Promise.resolve(spoolmanStatusValue);
      if (p in mutations) return mutations[p as keyof typeof mutations];
      if (p in apiResponses) return () => Promise.resolve(apiResponses[p]);
      return () => Promise.resolve(null);
    },
  }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string | Record<string, unknown>) =>
      typeof fallback === 'string' ? fallback : key,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: vi.fn() }) };
});

const outletContext = {
  selectedPrinterId: 3,
  setSelectedPrinterId: vi.fn(),
  sbState: {
    weight: null, weightStable: false, rawAdc: null, matchedSpool: null, unknownTagUid: null,
    unknownTrayUuid: null, deviceOnline: true, deviceId: 'dev-1', remainingWeight: null, netWeight: null,
  },
  setAlert: vi.fn(),
  displayBrightness: 100,
  setDisplayBrightness: vi.fn(),
  displayBlankTimeout: 0,
  setDisplayBlankTimeout: vi.fn(),
};

function renderPage() {
  function Wrapper() {
    return <Outlet context={outletContext} />;
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <ToastProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/spoolbuddy/ams']}>
          <Routes>
            <Route element={<Wrapper />}>
              <Route path="spoolbuddy/ams" element={<SpoolBuddyAmsPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ToastProvider>,
  );
}

// Third slot is a non-RFID PLA spool (no tray_uuid), so Assign/Unassign are offered.
function printerStatus() {
  const blank = { tray_type: '' };
  return {
    connected: true,
    state: 'IDLE',
    ams: [{
      id: 1, humidity: 30, temp: 25,
      tray: [
        { id: 0, ...blank },
        { id: 1, ...blank },
        { id: 2, tray_type: 'PLA', tray_color: '00FF00FF', tray_sub_brands: 'PLA Basic', remain: 50 },
        { id: 3, ...blank },
      ],
    }],
    vt_tray: [],
    tray_now: 255,
    active_extruder: 0,
  };
}

async function openSlot(n: number) {
  const slots = await screen.findAllByTestId('ams-slot');
  fireEvent.click(slots[n - 1]);
}

describe('SpoolBuddyAmsPage slot-action wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutations.unassignSpool.mockResolvedValue({ success: true });
    mutations.unassignSpoolmanSlot.mockResolvedValue({ success: true });
    mutations.unlinkSpool.mockResolvedValue({ success: true, message: 'ok' });
    spoolmanStatusValue = { enabled: false, connected: false };
    configureProps.length = 0;
    apiResponses = {
      getPrinterStatus: printerStatus(),
      getPrinter: { id: 3, name: 'P', serial_number: 'SN3', nozzle_count: 1 },
      getSlotPresets: {},
      getSettings: {},
      getLinkedSpools: { linked: {} },
      getAssignments: [],
      getSpoolmanSlotAssignments: [],
      getSpoolmanInventorySpools: [],
    };
  });

  it('built-in: Unassign sends unassignSpool(printerId, amsId, trayId) of the picked slot', async () => {
    apiResponses.getAssignments = [
      {
        id: 77, spool_id: 12, printer_id: 3, ams_id: 1, tray_id: 2,
        spool: { id: 12, material: 'PLA', label_weight: 1000, weight_used: 100, rgba: '00FF00FF' },
      },
    ];
    renderPage();
    await openSlot(3);

    fireEvent.click(await screen.findByText('Remove inventory spool from this slot'));

    await waitFor(() => expect(mutations.unassignSpool).toHaveBeenCalledTimes(1));
    expect(mutations.unassignSpool).toHaveBeenCalledWith(3, 1, 2);
    expect(mutations.unassignSpoolmanSlot).not.toHaveBeenCalled();
    expect(mutations.unlinkSpool).not.toHaveBeenCalled();
  });

  it('Spoolman slot-only assignment: Unassign sends unassignSpoolmanSlot(spoolmanSpoolId)', async () => {
    spoolmanStatusValue = { enabled: true, connected: true };
    apiResponses.getSpoolmanSlotAssignments = [
      { printer_id: 3, ams_id: 1, tray_id: 2, spoolman_spool_id: 555 },
    ];
    apiResponses.getSpoolmanInventorySpools = [
      { id: 555, material: 'PLA', label_weight: 1000, weight_used: 200 },
    ];
    renderPage();
    await openSlot(3);

    fireEvent.click(await screen.findByText('Remove inventory spool from this slot'));

    await waitFor(() => expect(mutations.unassignSpoolmanSlot).toHaveBeenCalledTimes(1));
    expect(mutations.unassignSpoolmanSlot).toHaveBeenCalledWith(555);
    expect(mutations.unassignSpool).not.toHaveBeenCalled();
    expect(mutations.unlinkSpool).not.toHaveBeenCalled();
  });

  it('Spoolman tag-linked spool: Unlink sends unlinkSpool(spoolId) only', async () => {
    spoolmanStatusValue = { enabled: true, connected: true };
    const status = printerStatus();
    (status.ams[0].tray[2] as Record<string, unknown>).tray_uuid = 'AABBCCDDEEFF00112233445566778899';
    apiResponses.getPrinterStatus = status;
    apiResponses.getLinkedSpools = {
      linked: {
        AABBCCDDEEFF00112233445566778899: {
          id: 314, material: 'PLA', color_name: 'Green', rgba: '00FF00FF',
          remaining_weight: 500, filament_weight: 1000,
        },
      },
    };
    renderPage();
    await openSlot(3);

    fireEvent.click(await screen.findByText('Remove Spoolman link from this slot'));

    await waitFor(() => expect(mutations.unlinkSpool).toHaveBeenCalledTimes(1));
    expect(mutations.unlinkSpool).toHaveBeenCalledWith(314);
    expect(mutations.unassignSpoolmanSlot).not.toHaveBeenCalled();
    expect(mutations.unassignSpool).not.toHaveBeenCalled();
  });

  it('closing the picker without choosing an action sends nothing', async () => {
    apiResponses.getAssignments = [
      { id: 77, spool_id: 12, printer_id: 3, ams_id: 1, tray_id: 2, spool: { id: 12, material: 'PLA' } },
    ];
    renderPage();
    await openSlot(3);
    await screen.findByText('Remove inventory spool from this slot');

    // Backdrop click closes the picker.
    const backdrop = document.querySelector('.fixed.inset-0.z-50 > .absolute.inset-0') as HTMLElement;
    fireEvent.click(backdrop);

    await waitFor(() =>
      expect(screen.queryByText('Remove inventory spool from this slot')).not.toBeInTheDocument(),
    );
    expect(mutations.unassignSpool).not.toHaveBeenCalled();
  });

  // The slot dialog refuses a reset while a print is loaded; it needs the state.
  it("Configure hands the slot dialog the printer's state", async () => {
    apiResponses.getPrinterStatus = { ...printerStatus(), state: 'RUNNING' };
    renderPage();
    await openSlot(3);

    fireEvent.click(await screen.findByText('Set filament preset, K-profile, and color'));

    await waitFor(() => expect(configureProps.length).toBeGreaterThan(0));
    expect(configureProps[configureProps.length - 1]).toMatchObject({ printerId: 3, printerState: 'RUNNING' });
  });
});
