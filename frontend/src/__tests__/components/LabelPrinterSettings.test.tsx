import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { LabelPrinterSettings } from '../../components/LabelPrinterSettings';
import { api, ApiError } from '../../api/client';

const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: mockShowToast }) };
});

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  api: {
    getSettings: vi.fn(),
    updateSettings: vi.fn(),
    testLabelPrinter: vi.fn(),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
}));

const OFF = { label_printer_type: '', label_printer_host: '', label_printer_model: 'QL-820NWB', label_printer_tape: '50' };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(OFF as never);
  vi.mocked(api.updateSettings).mockResolvedValue(OFF as never);
});

describe('LabelPrinterSettings (#3612)', () => {
  it('disables address, model and tape while the type is Off, and enables them with a printer type', async () => {
    render(<LabelPrinterSettings />);
    const host = await screen.findByTestId('label-printer-host');
    expect(host).toBeDisabled();
    expect(screen.getByTestId('label-printer-model')).toBeDisabled();
    expect(screen.getByTestId('label-printer-tape')).toBeDisabled();
    expect(screen.getByTestId('label-printer-test')).toBeDisabled();
    fireEvent.change(screen.getByTestId('label-printer-type'), { target: { value: 'brother_ql' } });
    expect(host).toBeEnabled();
    expect(screen.getByTestId('label-printer-model')).toBeEnabled();
    expect(screen.getByTestId('label-printer-tape')).toBeEnabled();
  });

  it('offers the defaults and the supported tapes', async () => {
    render(<LabelPrinterSettings />);
    expect(await screen.findByTestId('label-printer-model')).toHaveValue('QL-820NWB');
    expect(screen.getByTestId('label-printer-tape')).toHaveValue('50');
    const values = Array.from(screen.getByTestId('label-printer-tape').querySelectorAll('option')).map((o) => o.value);
    expect(values).toEqual(['12', '29', '38', '50', '54', '62']);
  });

  it('saves changed fields with the label_printer_ keys after a pause', async () => {
    render(<LabelPrinterSettings />);
    fireEvent.change(await screen.findByTestId('label-printer-type'), { target: { value: 'brother_ql' } });
    fireEvent.change(screen.getByTestId('label-printer-host'), { target: { value: ' 192.168.1.50 ' } });
    fireEvent.change(screen.getByTestId('label-printer-tape'), { target: { value: '62' } });
    await waitFor(() => expect(api.updateSettings).toHaveBeenCalled(), { timeout: 3000 });
    expect(api.updateSettings).toHaveBeenLastCalledWith({
      label_printer_type: 'brother_ql',
      label_printer_host: '192.168.1.50',
      label_printer_tape: '62',
    });
  });

  it('test print sends the unsaved values and shows what the printer answered', async () => {
    vi.mocked(api.testLabelPrinter).mockResolvedValue({
      status: { media_width_mm: 50, media_type: 'continuous', errors: [] },
    });
    render(<LabelPrinterSettings />);
    fireEvent.change(await screen.findByTestId('label-printer-type'), { target: { value: 'brother_ql' } });
    fireEvent.change(screen.getByTestId('label-printer-host'), { target: { value: 'label.local' } });
    fireEvent.click(screen.getByTestId('label-printer-test'));
    await waitFor(() =>
      expect(api.testLabelPrinter).toHaveBeenCalledWith({
        type: 'brother_ql',
        host: 'label.local',
        model: 'QL-820NWB',
        tape: '50',
      }),
    );
    const result = await screen.findByTestId('label-printer-test-result');
    expect(result).toHaveTextContent('50');
    expect(result).toHaveTextContent('continuous');
  });

  it('shows the error from a failed test print inline', async () => {
    vi.mocked(api.testLabelPrinter).mockRejectedValue(new Error('Label printer not reachable at label.local:9100'));
    render(<LabelPrinterSettings />);
    fireEvent.change(await screen.findByTestId('label-printer-type'), { target: { value: 'brother_ql' } });
    fireEvent.change(screen.getByTestId('label-printer-host'), { target: { value: 'label.local' } });
    fireEvent.click(screen.getByTestId('label-printer-test'));
    expect(await screen.findByTestId('label-printer-test-result')).toHaveTextContent('not reachable at label.local:9100');
  });

  it('shows the 422 detail of a rejected test print inline', async () => {
    vi.mocked(api.testLabelPrinter).mockRejectedValue(
      new ApiError('Label printer address: 169.254.169.254 is not a permitted address', 422),
    );
    render(<LabelPrinterSettings />);
    fireEvent.change(await screen.findByTestId('label-printer-type'), { target: { value: 'brother_ql' } });
    fireEvent.change(screen.getByTestId('label-printer-host'), { target: { value: '169.254.169.254' } });
    fireEvent.click(screen.getByTestId('label-printer-test'));
    expect(await screen.findByTestId('label-printer-test-result')).toHaveTextContent('not a permitted address');
  });

  it('flushes a pending change when the card unmounts instead of dropping it', async () => {
    const { unmount } = render(<LabelPrinterSettings />);
    fireEvent.change(await screen.findByTestId('label-printer-type'), { target: { value: 'brother_ql' } });
    fireEvent.change(screen.getByTestId('label-printer-host'), { target: { value: '192.168.1.50' } });
    expect(api.updateSettings).not.toHaveBeenCalled();
    unmount();
    expect(api.updateSettings).toHaveBeenCalledTimes(1);
    expect(api.updateSettings).toHaveBeenCalledWith({
      label_printer_type: 'brother_ql',
      label_printer_host: '192.168.1.50',
    });
  });

  it('does not save twice when the debounce already fired before unmount', async () => {
    const { unmount } = render(<LabelPrinterSettings />);
    fireEvent.change(await screen.findByTestId('label-printer-type'), { target: { value: 'brother_ql' } });
    await waitFor(() => expect(api.updateSettings).toHaveBeenCalledTimes(1), { timeout: 3000 });
    unmount();
    expect(api.updateSettings).toHaveBeenCalledTimes(1);
  });
});
