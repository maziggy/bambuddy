/**
 * The slicer certificate card offers a script per platform with this
 * instance's CA written into it (Settings -> Virtual Printer).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { VirtualPrinterList } from '../../components/VirtualPrinterList';

vi.mock('../../api/client', () => ({
  multiVirtualPrinterApi: {
    list: vi.fn().mockResolvedValue({ printers: [], models: {} }),
    getCaCertificate: vi.fn().mockResolvedValue({ pem: 'PEM', fingerprint_sha256: 'AB:CD', not_valid_after: '' }),
    getCaCertificateInstaller: vi.fn(),
  },
  virtualPrinterApi: {
    getSettings: vi.fn().mockResolvedValue({ archive_name_source: 'metadata' }),
    updateSettings: vi.fn(),
  },
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getPrinters: vi.fn().mockResolvedValue([]),
  },
}));

const mockDownload = vi.fn();
vi.mock('../../utils/clipboard', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../utils/clipboard')>()),
  downloadTextFile: (...args: unknown[]) => mockDownload(...args),
}));

import { multiVirtualPrinterApi } from '../../api/client';

describe('VirtualPrinterList - slicer certificate scripts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it.each([
    ['Windows', 'windows', 'bambuddy-slicer-cert.cmd'],
    ['Linux / macOS', 'unix', 'bambuddy-slicer-cert.sh'],
  ])('%s downloads the script the backend made', async (label, platform, filename) => {
    vi.mocked(multiVirtualPrinterApi.getCaCertificateInstaller).mockResolvedValue({ filename, content: 'SCRIPT' });
    const user = userEvent.setup();
    render(<VirtualPrinterList />);

    await user.click(await screen.findByRole('button', { name: label }));

    await waitFor(() => expect(mockDownload).toHaveBeenCalledWith('SCRIPT', filename, 'application/octet-stream'));
    expect(multiVirtualPrinterApi.getCaCertificateInstaller).toHaveBeenCalledWith(platform);
  });
});
