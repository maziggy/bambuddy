/**
 * The label dialog for a project's finished parts (#3655): the spool list gives
 * way to quantity, note and number of labels; sizes, sheets and output are shared.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, act } from '@testing-library/react';
import { render } from '../utils';
import { LabelTemplatePickerModal } from '../../components/LabelTemplatePickerModal';
import { api } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  api: {
    printProjectLabels: vi.fn(),
    previewProjectLabel: vi.fn(),
    printSpoolLabels: vi.fn(),
    previewSpoolLabel: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getCurrentUser: vi.fn(),
    getUiSettings: vi.fn().mockResolvedValue({}),
    getLabelTemplates: vi.fn(),
  },
}));

const PDF_BLOB = new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46])], { type: 'application/pdf' });
const PNG_BLOB = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: 'image/png' });
const PROJECT = { id: 7, name: 'Banana Slugs', color: '#FFD700' };
const DEFAULT_FIELDS = ['name', 'note', 'due_date', 'color', 'qr', 'quantity'];

let store: Map<string, string>;

beforeEach(() => {
  vi.clearAllMocks();
  store = new Map<string, string>();
  vi.mocked(localStorage.getItem).mockImplementation((key) => store.get(key) ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((key, value) => void store.set(key, String(value)));
  vi.mocked(api.previewProjectLabel).mockResolvedValue(PNG_BLOB);
  vi.mocked(api.printProjectLabels).mockResolvedValue(PDF_BLOB);
  vi.mocked(api.getLabelTemplates).mockResolvedValue([]);
  Object.defineProperty(window.URL, 'createObjectURL', { value: vi.fn(() => 'blob:mock'), configurable: true });
  Object.defineProperty(window.URL, 'revokeObjectURL', { value: vi.fn(), configurable: true });
  vi.spyOn(window, 'open').mockImplementation(() => ({}) as Window);
});

function openModal(onClose = vi.fn()) {
  render(<LabelTemplatePickerModal isOpen onClose={onClose} project={PROJECT} />);
  return onClose;
}

describe('LabelTemplatePickerModal for a project', () => {
  it('shows the project inputs and project lines instead of the spool list', () => {
    openModal();
    expect(screen.getByText('Print project label')).toBeInTheDocument();
    expect(screen.getByText('Banana Slugs')).toBeInTheDocument();
    expect(screen.getByTestId('project-label-quantity')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Search/i)).not.toBeInTheDocument();
    expect(screen.getByTestId('label-field-due_date')).toBeInTheDocument();
    expect(screen.queryByTestId('label-field-brand')).not.toBeInTheDocument();
  });

  it('previews with what was typed', async () => {
    openModal();
    fireEvent.change(screen.getByTestId('project-label-quantity'), { target: { value: '12' } });
    fireEvent.change(screen.getByTestId('project-label-note'), { target: { value: ' Order 1042 ' } });
    await waitFor(() =>
      expect(api.previewProjectLabel).toHaveBeenLastCalledWith(
        expect.objectContaining({ project_id: 7, quantity: 12, note: 'Order 1042', fields: DEFAULT_FIELDS }),
        expect.any(AbortSignal),
      ),
    );
  });

  it('prints one item per label', async () => {
    const onClose = openModal();
    fireEvent.change(screen.getByTestId('project-label-quantity'), { target: { value: '6' } });
    fireEvent.change(screen.getByTestId('project-label-copies'), { target: { value: '3' } });
    fireEvent.click(screen.getByTestId('label-print-button'));
    await waitFor(() => expect(api.printProjectLabels).toHaveBeenCalledTimes(1));
    const request = vi.mocked(api.printProjectLabels).mock.calls[0][0];
    expect(request.items).toEqual([
      { project_id: 7, quantity: 6, note: null },
      { project_id: 7, quantity: 6, note: null },
      { project_id: 7, quantity: 6, note: null },
    ]);
    expect(request).toMatchObject({ template: 'ams_holder_74x33', fields: DEFAULT_FIELDS, format: 'pdf' });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('a bad quantity or number of labels blocks printing', () => {
    openModal();
    fireEvent.change(screen.getByTestId('project-label-quantity'), { target: { value: '0' } });
    expect(screen.getByTestId('label-print-button')).toBeDisabled();
    fireEvent.change(screen.getByTestId('project-label-quantity'), { target: { value: '' } });
    expect(screen.getByTestId('label-print-button')).not.toBeDisabled();
    fireEvent.change(screen.getByTestId('project-label-copies'), { target: { value: '1.5' } });
    expect(screen.getByTestId('label-print-button')).toBeDisabled();
  });

  it('distinguishes unfinished numbers from an empty optional quantity', () => {
    openModal();
    const quantity = screen.getByRole('textbox', { name: 'Quantity in the bag' });
    for (const invalid of ['-', '1e2', '1.0', '100001']) {
      fireEvent.change(quantity, { target: { value: invalid } });
      expect(quantity).toHaveAttribute('aria-invalid', 'true');
      expect(screen.getByTestId('label-print-button')).toBeDisabled();
    }
    fireEvent.change(quantity, { target: { value: '100000' } });
    expect(screen.getByTestId('label-print-button')).toBeEnabled();
  });

  it('uses a saved sheet and starts the batch in the selected slot', async () => {
    const layout = { page: 'a4', label_w_mm: 70, label_h_mm: 36, rows: 8, cols: 3,
      top_mm: 4.5, left_mm: 0, col_gap_mm: 0, row_gap_mm: 0 } as const;
    vi.mocked(api.getLabelTemplates).mockResolvedValue([
      { id: 5, name: 'Parts bins', layout, created_at: '', updated_at: '' },
    ]);
    store.set('bambuddy-label-print-options', JSON.stringify({ template: 'saved:5' }));
    openModal();
    await screen.findByRole('option', { name: 'Parts bins' });
    fireEvent.change(screen.getByTestId('label-starting-position'), { target: { value: '24' } });
    await waitFor(() => expect(api.previewProjectLabel).toHaveBeenCalledWith(
      expect.objectContaining({ project_id: PROJECT.id, template: 'custom', layout, fields: DEFAULT_FIELDS }),
      expect.any(AbortSignal),
    ));
    fireEvent.click(screen.getByTestId('label-print-button'));
    await waitFor(() => expect(api.printProjectLabels).toHaveBeenCalledWith(
      expect.objectContaining({ template: 'custom', layout, starting_position: 24 }),
    ));
  });

  it('can print on built-in stock after a failed lookup and retains the saved preference', async () => {
    store.set('bambuddy-label-print-options', JSON.stringify({ template: 'saved:5' }));
    vi.mocked(api.getLabelTemplates).mockRejectedValue(new Error('Offline'));
    openModal();
    const print = screen.getByTestId('label-print-button');
    await waitFor(() => expect(print).toBeEnabled());
    expect(screen.getByTestId('label-template-select')).toHaveValue('ams_holder_74x33');
    expect(screen.getByText(/check the selected label size/i)).toBeVisible();
    expect(store.get('bambuddy-label-print-options')).toContain('saved:5');
    fireEvent.click(print);
    await waitFor(() => expect(api.printProjectLabels).toHaveBeenCalledWith(
      expect.objectContaining({ template: 'ams_holder_74x33' }),
    ));
  });

  it('explains the PNG sheet limit in terms of labels', async () => {
    openModal();
    fireEvent.change(screen.getByTestId('label-template-select'), { target: { value: 'custom' } });
    for (const field of ['rows', 'cols']) {
      fireEvent.change(screen.getByTestId(`label-sheet-${field}`), { target: { value: '1' } });
    }
    fireEvent.change(screen.getByTestId('project-label-copies'), { target: { value: '100' } });
    fireEvent.click(screen.getByTestId('label-format-png'));
    fireEvent.change(screen.getByTestId('label-dpi-select'), { target: { value: '600' } });
    expect(screen.getByTestId('label-png-too-large')).toHaveTextContent(/100.*600.*25.*fewer labels/);
    expect(screen.getByTestId('label-print-button')).toBeDisabled();
  });

  it('keeps the latest preview when an older response arrives last', async () => {
    let finish!: (blob: Blob) => void;
    vi.mocked(api.previewProjectLabel).mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    openModal();
    await waitFor(() => expect(api.previewProjectLabel).toHaveBeenCalledTimes(1));
    const signal = vi.mocked(api.previewProjectLabel).mock.calls[0][1];
    fireEvent.change(screen.getByTestId('project-label-note'), { target: { value: 'Bag 2' } });
    await screen.findByRole('img', { name: 'Label preview' });
    expect(signal?.aborted).toBe(true);
    await act(async () => finish(PNG_BLOB));
    expect(window.URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it('remembers project lines apart from spool lines', async () => {
    openModal();
    fireEvent.click(screen.getByTestId('label-field-parts'));
    const saved = JSON.parse(store.get('bambuddy-label-print-options') ?? '{}');
    expect(saved.projectFields.ams_holder_74x33).toEqual([...DEFAULT_FIELDS.slice(0, 2), 'parts', ...DEFAULT_FIELDS.slice(2)]);
    expect(saved.fields).toEqual({});
  });
});
