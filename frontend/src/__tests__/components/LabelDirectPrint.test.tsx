import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { LabelTemplatePickerModal } from '../../components/LabelTemplatePickerModal';
import { ApiError, api, setAuthToken } from '../../api/client';

const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: mockShowToast }) };
});

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  api: {
    printSpoolLabels: vi.fn(),
    printSpoolmanSpoolLabels: vi.fn(),
    printProjectLabels: vi.fn(),
    printLabelsToPrinter: vi.fn(),
    getLabelPrintJob: vi.fn(),
    previewSpoolLabel: vi.fn(),
    previewSpoolmanSpoolLabel: vi.fn(),
    previewProjectLabel: vi.fn(),
    getSettings: vi.fn(),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getCurrentUser: vi.fn(),
    getUiSettings: vi.fn(),
    getLabelTemplates: vi.fn().mockResolvedValue([]),
  },
}));

const PNG_BLOB = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: 'image/png' });
const SPOOLS = [
  { id: 1, material: 'PLA', subtype: 'Basic', brand: 'Polymaker', color_name: 'Red', rgba: 'FF0000FF' },
  { id: 2, material: 'PETG', subtype: null, brand: 'Sunlu', color_name: 'Blue', rgba: '0000FFFF' },
];
const CONFIGURED = {
  label_printer_type: 'brother_ql',
  label_printer_host: '192.168.1.50',
  label_printer_model: 'QL-820NWB',
  label_printer_tape: '50',
  label_printer_configured: true,
};

function openModal(ids = [1]) {
  return render(
    <LabelTemplatePickerModal isOpen onClose={vi.fn()} availableSpools={SPOOLS} initialSelectedIds={ids} spoolmanMode={false} />,
  );
}
// The print request is accepted as a job; the job poll then reports it done.
function acceptJob(total: number) {
  vi.mocked(api.printLabelsToPrinter).mockResolvedValue({ job_id: 'j1', total });
  vi.mocked(api.getLabelPrintJob).mockResolvedValue({ job_id: 'j1', state: 'done', printed: total, total, error: null });
}
const pickTemplate = (v: string) => fireEvent.change(screen.getByTestId('label-template-select'), { target: { value: v } });

beforeEach(() => {
  vi.clearAllMocks();
  const store = new Map<string, string>();
  vi.mocked(localStorage.getItem).mockImplementation((key) => store.get(key) ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((key, value) => void store.set(key, String(value)));
  vi.mocked(api.previewSpoolLabel).mockResolvedValue(PNG_BLOB);
  vi.mocked(api.getSettings).mockResolvedValue(CONFIGURED as never);
  vi.mocked(api.getAuthStatus).mockResolvedValue({ auth_enabled: false } as never);
  Object.defineProperty(window.URL, 'createObjectURL', { value: vi.fn(() => 'blob:mock'), configurable: true });
  Object.defineProperty(window.URL, 'revokeObjectURL', { value: vi.fn(), configurable: true });
});

describe('direct label printing in the labels dialog (#3612)', () => {
  it('has no Print button without a configured label printer', async () => {
    vi.mocked(api.getSettings).mockResolvedValue({ label_printer_type: '', label_printer_host: '' } as never);
    openModal();
    await screen.findByTestId('label-print-button');
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    expect(screen.queryByTestId('label-direct-print-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('label-direct-hint')).not.toBeInTheDocument();
  });

  it('shows Print disabled with a hint for a sheet template', async () => {
    openModal();
    const btn = await screen.findByTestId('label-direct-print-button');
    expect(btn).toBeDisabled();
    expect(screen.getByTestId('label-direct-hint')).toHaveTextContent('50 mm');
  });

  it('roll format defaults to the printer tape and 40 mm, and previews with that roll', async () => {
    openModal();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    expect(screen.getByTestId('label-roll-tape')).toHaveValue('50');
    expect(screen.getByTestId('label-roll-length')).toHaveValue(40);
    await waitFor(() =>
      expect(api.previewSpoolLabel).toHaveBeenCalledWith(
        expect.objectContaining({ template: 'roll', roll: { tape: '50', length_mm: 40 } }),
        expect.anything(),
      ),
    );
    expect(screen.getByTestId('label-direct-print-button')).toBeEnabled();
  });

  it('disables Print when the roll tape differs from the printer tape', async () => {
    openModal();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.change(screen.getByTestId('label-roll-tape'), { target: { value: '62' } });
    expect(screen.getByTestId('label-direct-print-button')).toBeDisabled();
    expect(screen.getByTestId('label-direct-hint')).toHaveTextContent('50 mm');
  });

  it('rejects a roll length outside 20..200 mm', async () => {
    openModal();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.change(screen.getByTestId('label-roll-length'), { target: { value: '10' } });
    expect(screen.getByTestId('label-roll-length-invalid')).toBeInTheDocument();
    expect(screen.getByTestId('label-direct-print-button')).toBeDisabled();
  });

  it('prints the selected spools as a monochrome roll and toasts the count', async () => {
    acceptJob(2);
    openModal([1, 2]);
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.change(screen.getByTestId('label-roll-length'), { target: { value: '60' } });
    fireEvent.click(screen.getByTestId('label-direct-print-button'));
    await waitFor(() => expect(api.printLabelsToPrinter).toHaveBeenCalled());
    const [source, body] = vi.mocked(api.printLabelsToPrinter).mock.calls[0];
    expect(source).toBe('inventory');
    expect(body).toMatchObject({
      spool_ids: [1, 2],
      template: 'roll',
      roll: { tape: '50', length_mm: 60 },
      monochrome: true,
    });
    expect(body).not.toHaveProperty('format');
    expect(body).not.toHaveProperty('dpi');
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith(expect.stringContaining('2'), 'success'));
  });

  it('shows the backend message as an error toast when printing fails', async () => {
    vi.mocked(api.printLabelsToPrinter).mockRejectedValue(new ApiError('Label printer reports: cover open', 502));
    openModal();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.click(screen.getByTestId('label-direct-print-button'));
    await waitFor(() =>
      expect(mockShowToast).toHaveBeenCalledWith(expect.stringContaining('cover open'), 'error'),
    );
  });

  it('downloads a roll as a PDF with the roll in the request', async () => {
    vi.mocked(api.printSpoolLabels).mockResolvedValue(new Blob(['x'], { type: 'application/pdf' }));
    vi.spyOn(window, 'open').mockImplementation(() => ({}) as Window);
    openModal();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.click(screen.getByTestId('label-print-button'));
    await waitFor(() =>
      expect(api.printSpoolLabels).toHaveBeenCalledWith(
        expect.objectContaining({ template: 'roll', roll: { tape: '50', length_mm: 40 }, format: 'pdf' }),
      ),
    );
  });

  it('shows the preview as monochrome and locks the option while the roll goes to the printer', async () => {
    vi.mocked(api.printSpoolLabels).mockResolvedValue(new Blob(['x'], { type: 'application/pdf' }));
    vi.spyOn(window, 'open').mockImplementation(() => ({}) as Window);
    openModal();
    await screen.findByTestId('label-direct-print-button');
    // A sheet format: the user's own choice, not locked.
    expect(screen.getByTestId('label-monochrome')).not.toBeChecked();
    expect(screen.getByTestId('label-monochrome')).toBeEnabled();
    pickTemplate('roll');
    await waitFor(() => expect(screen.getByTestId('label-monochrome')).toBeChecked());
    expect(screen.getByTestId('label-monochrome')).toBeDisabled();
    await waitFor(() =>
      expect(api.previewSpoolLabel).toHaveBeenLastCalledWith(
        expect.objectContaining({ template: 'roll', monochrome: true }),
        expect.anything(),
      ),
    );
    // The PDF download keeps the user's own choice.
    fireEvent.click(screen.getByTestId('label-print-button'));
    await waitFor(() =>
      expect(api.printSpoolLabels).toHaveBeenCalledWith(expect.objectContaining({ template: 'roll', monochrome: false })),
    );
    // Another tape does not go to the printer: the option is free again.
    fireEvent.change(screen.getByTestId('label-roll-tape'), { target: { value: '62' } });
    await waitFor(() => expect(screen.getByTestId('label-monochrome')).toBeEnabled());
    expect(screen.getByTestId('label-monochrome')).not.toBeChecked();
  });

  it('sends no starting_position in a direct print body', async () => {
    acceptJob(1);
    openModal();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.click(screen.getByTestId('label-direct-print-button'));
    await waitFor(() => expect(api.printLabelsToPrinter).toHaveBeenCalled());
    expect(vi.mocked(api.printLabelsToPrinter).mock.calls[0][1]).not.toHaveProperty('starting_position');
  });

  it('disables Print with a hint for more than 100 labels', async () => {
    const many = Array.from({ length: 101 }, (_, i) => ({ id: i + 1, material: 'PLA', brand: 'X', color_name: 'Red', rgba: 'FF0000FF' }));
    render(
      <LabelTemplatePickerModal
        isOpen
        onClose={vi.fn()}
        availableSpools={many as never}
        initialSelectedIds={many.map((m) => m.id)}
        spoolmanMode={false}
      />,
    );
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.change(screen.getByTestId('label-roll-length'), { target: { value: '20' } });
    expect(screen.getByTestId('label-direct-print-button')).toBeDisabled();
    expect(screen.getByTestId('label-direct-hint')).toHaveTextContent('At most 100 labels');
  });

  it('disables Print with a hint when the tape would exceed 4000 mm', async () => {
    const some = Array.from({ length: 21 }, (_, i) => ({ id: i + 1, material: 'PLA', brand: 'X', color_name: 'Red', rgba: 'FF0000FF' }));
    render(
      <LabelTemplatePickerModal
        isOpen
        onClose={vi.fn()}
        availableSpools={some as never}
        initialSelectedIds={some.map((m) => m.id)}
        spoolmanMode={false}
      />,
    );
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.change(screen.getByTestId('label-roll-length'), { target: { value: '200' } });
    expect(screen.getByTestId('label-direct-print-button')).toBeDisabled();
    expect(screen.getByTestId('label-direct-hint')).toHaveTextContent('4200 mm');
    fireEvent.change(screen.getByTestId('label-roll-length'), { target: { value: '190' } });
    expect(screen.getByTestId('label-direct-print-button')).toBeEnabled();
  });

  it('closes the dialog once the job is accepted and lets the toast follow it', async () => {
    acceptJob(1);
    const onClose = vi.fn();
    render(
      <LabelTemplatePickerModal isOpen onClose={onClose} availableSpools={SPOOLS} initialSelectedIds={[1]} spoolmanMode={false} />,
    );
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.click(screen.getByTestId('label-direct-print-button'));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Printing 1 label…', 'info'));
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Printed 1 label', 'success'));
  });

  it('explains a bare 504 on the print request', async () => {
    vi.mocked(api.printLabelsToPrinter).mockRejectedValue(new ApiError('HTTP 504', 504));
    openModal();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.click(screen.getByTestId('label-direct-print-button'));
    await waitFor(() =>
      expect(mockShowToast).toHaveBeenCalledWith(expect.stringContaining('timed out on the way to Bambuddy'), 'error'),
    );
  });

  it('uses the singular toast text for one label', async () => {
    acceptJob(1);
    openModal();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.click(screen.getByTestId('label-direct-print-button'));
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Printed 1 label', 'success'));
  });

  it('uses the plural toast text for several labels', async () => {
    acceptJob(2);
    openModal([1, 2]);
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.click(screen.getByTestId('label-direct-print-button'));
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Printed 2 labels', 'success'));
  });
});

function signIn(username: string, permissions: string[]) {
  setAuthToken('test-token');
  vi.mocked(api.getAuthStatus).mockResolvedValue({ auth_enabled: true, requires_setup: false } as never);
  vi.mocked(api.getCurrentUser).mockResolvedValue({
    id: 2,
    username,
    is_admin: false,
    is_active: true,
    permissions,
  } as never);
  vi.mocked(api.getUiSettings).mockResolvedValue({ label_printer_configured: true, label_printer_tape: '50' } as never);
}

describe('direct label printing for a user without settings:read (#3612)', () => {
  beforeEach(() => signIn('operator', ['inventory:read', 'inventory:update']));
  afterEach(() => setAuthToken(null));

  it('reads the ui-flags settings and enables Print only for the matching tape', async () => {
    // GET /settings would say "not configured" here: only ui-flags enable Print.
    vi.mocked(api.getSettings).mockResolvedValue({ label_printer_type: '', label_printer_host: '' } as never);
    openModal();
    const btn = await screen.findByTestId('label-direct-print-button');
    expect(api.getUiSettings).toHaveBeenCalled();
    expect(btn).toBeDisabled();
    pickTemplate('roll');
    await waitFor(() => expect(screen.getByTestId('label-direct-print-button')).toBeEnabled());
    expect(screen.getByTestId('label-roll-tape')).toHaveValue('50');
    fireEvent.change(screen.getByTestId('label-roll-tape'), { target: { value: '29' } });
    expect(screen.getByTestId('label-direct-print-button')).toBeDisabled();
    expect(screen.getByTestId('label-direct-hint')).toHaveTextContent('50 mm');
    fireEvent.change(screen.getByTestId('label-roll-tape'), { target: { value: '50' } });
    expect(screen.getByTestId('label-direct-print-button')).toBeEnabled();
  });

  it('shows no Print button when ui-flags say no printer is configured', async () => {
    vi.mocked(api.getUiSettings).mockResolvedValue({ label_printer_configured: false, label_printer_tape: '' } as never);
    openModal();
    await screen.findByTestId('label-print-button');
    await waitFor(() => expect(api.getUiSettings).toHaveBeenCalled());
    expect(screen.queryByTestId('label-direct-print-button')).not.toBeInTheDocument();
  });

});

// Project labels (#3655) print on the label printer like spool labels.
describe('direct printing of a project label (#3612)', () => {
  const PROJECT = { id: 7, name: 'Bag', color: null };
  const openProject = (onClose = vi.fn()) => {
    render(<LabelTemplatePickerModal isOpen onClose={onClose} project={PROJECT} />);
    return onClose;
  };

  beforeEach(() => {
    vi.mocked(api.previewProjectLabel).mockResolvedValue(PNG_BLOB);
  });
  afterEach(() => setAuthToken(null));

  it('offers the roll format and previews it in black and white on the printer tape', async () => {
    openProject();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    expect(screen.getByTestId('label-roll-tape')).toHaveValue('50');
    await waitFor(() =>
      expect(api.previewProjectLabel).toHaveBeenLastCalledWith(
        expect.objectContaining({ project_id: 7, template: 'roll', roll: { tape: '50', length_mm: 40 }, monochrome: true }),
        expect.anything(),
      ),
    );
    expect(screen.getByTestId('label-monochrome')).toBeDisabled();
  });

  it('prints one item per label as a monochrome roll and toasts the count', async () => {
    acceptJob(3);
    const onClose = openProject();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.change(screen.getByTestId('project-label-quantity'), { target: { value: '12' } });
    fireEvent.change(screen.getByTestId('project-label-note'), { target: { value: ' Order 1042 ' } });
    fireEvent.change(screen.getByTestId('project-label-copies'), { target: { value: '3' } });
    fireEvent.click(screen.getByTestId('label-direct-print-button'));
    await waitFor(() => expect(api.printLabelsToPrinter).toHaveBeenCalledTimes(1));
    const item = { project_id: 7, quantity: 12, note: 'Order 1042' };
    expect(api.printLabelsToPrinter).toHaveBeenCalledWith('projects', {
      items: [item, item, item],
      template: 'roll',
      roll: { tape: '50', length_mm: 40 },
      monochrome: true,
      fields: ['name', 'note', 'due_date', 'color', 'qr', 'quantity'],
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    await waitFor(() => expect(api.getLabelPrintJob).toHaveBeenCalledWith('j1'));
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Printed 3 labels', 'success'));
    expect(api.printProjectLabels).not.toHaveBeenCalled();
  });

  it('Print is disabled for a sheet, another tape or a bad quantity', async () => {
    openProject();
    const btn = await screen.findByTestId('label-direct-print-button');
    expect(btn).toBeDisabled(); // the default stock is not a roll
    expect(screen.getByTestId('label-direct-hint')).toHaveTextContent('50 mm');
    pickTemplate('roll');
    expect(btn).toBeEnabled();
    fireEvent.change(screen.getByTestId('project-label-quantity'), { target: { value: '0' } });
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByTestId('project-label-quantity'), { target: { value: '' } });
    expect(btn).toBeEnabled();
    fireEvent.change(screen.getByTestId('label-roll-tape'), { target: { value: '62' } });
    expect(btn).toBeDisabled();
    // The other tape can still be downloaded.
    expect(screen.getByTestId('label-print-button')).toBeEnabled();
    fireEvent.click(btn);
    expect(api.printLabelsToPrinter).not.toHaveBeenCalled();
  });

  it('Print is disabled with a hint when the labels need more tape than one print takes', async () => {
    openProject();
    const btn = await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.change(screen.getByTestId('label-roll-length'), { target: { value: '200' } });
    fireEvent.change(screen.getByTestId('project-label-copies'), { target: { value: '21' } });
    expect(btn).toBeDisabled();
    expect(screen.getByTestId('label-direct-hint')).toHaveTextContent(
      'These labels need 4200 mm of tape; at most 4000 mm fit in one print. Print fewer labels or use a shorter label.',
    );
    fireEvent.change(screen.getByTestId('project-label-copies'), { target: { value: '20' } });
    expect(btn).toBeEnabled();
  });

  it('downloads a roll with the roll in the request', async () => {
    vi.mocked(api.printProjectLabels).mockResolvedValue(new Blob(['%PDF'], { type: 'application/pdf' }));
    vi.spyOn(window, 'open').mockImplementation(() => ({}) as Window);
    openProject();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.click(screen.getByTestId('label-print-button'));
    await waitFor(() =>
      expect(api.printProjectLabels).toHaveBeenCalledWith(
        expect.objectContaining({ template: 'roll', roll: { tape: '50', length_mm: 40 }, format: 'pdf' }),
      ),
    );
    expect(api.printLabelsToPrinter).not.toHaveBeenCalled();
  });

  it('shows an error toast and stays open when the print is refused', async () => {
    vi.mocked(api.printLabelsToPrinter).mockRejectedValue(new Error('The printer has 62 mm tape loaded'));
    const onClose = openProject();
    await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    fireEvent.click(screen.getByTestId('label-direct-print-button'));
    await waitFor(() =>
      expect(mockShowToast).toHaveBeenCalledWith('Could not print: The printer has 62 mm tape loaded', 'error'),
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it('has no Print button without a configured label printer, and keeps the roll for the download', async () => {
    vi.mocked(api.getSettings).mockResolvedValue({ label_printer_type: '', label_printer_host: '' } as never);
    openProject();
    await screen.findByTestId('label-print-button');
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    expect(screen.queryByTestId('label-direct-print-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('label-direct-hint')).not.toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Roll/ })).toBeInTheDocument();
  });

  it('has no Print button without projects:update, also with inventory:update', async () => {
    signIn('stock', ['projects:read', 'inventory:read', 'inventory:update']);
    openProject();
    await screen.findByTestId('label-print-button');
    await waitFor(() => expect(api.getUiSettings).toHaveBeenCalled());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByTestId('label-direct-print-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('label-direct-hint')).not.toBeInTheDocument();
    // Nothing goes to the printer, so the monochrome option is not locked on.
    pickTemplate('roll');
    expect(screen.queryByTestId('label-direct-print-button')).not.toBeInTheDocument();
    expect(screen.getByTestId('label-monochrome')).toBeEnabled();
    expect(screen.getByTestId('label-print-button')).toBeEnabled();
  });

  it('has the Print button with projects:update and no inventory permission', async () => {
    signIn('maker', ['projects:read', 'projects:update']);
    acceptJob(1);
    openProject();
    const btn = await screen.findByTestId('label-direct-print-button');
    pickTemplate('roll');
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await waitFor(() =>
      expect(api.printLabelsToPrinter).toHaveBeenCalledWith('projects', expect.objectContaining({ template: 'roll' })),
    );
  });
});

describe('direct label printing needs inventory:update (#3612)', () => {
  afterEach(() => setAuthToken(null));

  // The settings are in once the query has answered and React has rendered.
  async function settingsLoaded() {
    await screen.findByTestId('label-print-button');
    await waitFor(() => expect(api.getUiSettings).toHaveBeenCalled());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it('has no Print button with inventory:read only, and the download stays', async () => {
    signIn('viewer', ['inventory:read']);
    openModal();
    await settingsLoaded();
    expect(screen.queryByTestId('label-direct-print-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('label-direct-hint')).not.toBeInTheDocument();
    pickTemplate('roll');
    expect(screen.queryByTestId('label-direct-print-button')).not.toBeInTheDocument();
    // Nothing goes to the printer, so the monochrome option is not locked on.
    expect(screen.getByTestId('label-monochrome')).toBeEnabled();
    expect(screen.getByTestId('label-print-button')).toBeEnabled();
    expect(api.printLabelsToPrinter).not.toHaveBeenCalled();
  });

  it('has the Print button with inventory:update', async () => {
    signIn('operator', ['inventory:read', 'inventory:update']);
    acceptJob(1);
    openModal();
    await settingsLoaded();
    pickTemplate('roll');
    fireEvent.click(await screen.findByTestId('label-direct-print-button'));
    await waitFor(() => expect(api.printLabelsToPrinter).toHaveBeenCalledTimes(1));
  });
});
