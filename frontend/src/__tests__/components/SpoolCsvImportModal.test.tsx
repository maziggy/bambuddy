import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolCsvImportModal } from '../../components/SpoolCsvImportModal';
import { api, type CsvImportPreview } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    importSpoolsCsvPreview: vi.fn(),
    importSpoolsCsv: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
  },
}));

const preview: CsvImportPreview = {
  columns: ['material', 'brand', 'color_name', 'rgba'],
  total: 3,
  valid_count: 2,
  error_count: 1,
  skipped_count: 0,
  warnings: [],
  rows: [
    { row_number: 1, status: 'valid', reason: null, material: 'PLA', brand: 'Polymaker', color_name: 'White', rgba: 'ffffffff', resolved_color: false },
    { row_number: 2, status: 'error', reason: 'material is required', material: null, brand: 'Polymaker', color_name: 'X', rgba: null, resolved_color: false },
    { row_number: 3, status: 'valid', reason: null, material: 'PETG', brand: 'Brand', color_name: 'Jade', rgba: 'e8e8e8ff', resolved_color: true },
  ],
};

function selectFile() {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(['material\nPLA\n'], 'inventory.csv', { type: 'text/csv' });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

describe('SpoolCsvImportModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the preview table with per-row status after a file is chosen', async () => {
    vi.mocked(api.importSpoolsCsvPreview).mockResolvedValue(preview);

    render(<SpoolCsvImportModal onClose={vi.fn()} onImported={vi.fn()} />);
    selectFile();

    await waitFor(() => expect(api.importSpoolsCsvPreview).toHaveBeenCalledOnce());
    // Summary counts surfaced.
    expect(await screen.findByText('2 valid')).toBeInTheDocument();
    expect(screen.getByText('1 error')).toBeInTheDocument();
    // Error reason rendered inline.
    expect(screen.getByText('material is required')).toBeInTheDocument();
    // Import button reflects the valid count.
    expect(screen.getByText('Import 2 valid rows')).toBeInTheDocument();
  });

  it('imports only when there are valid rows and reports the created count', async () => {
    vi.mocked(api.importSpoolsCsvPreview).mockResolvedValue(preview);
    vi.mocked(api.importSpoolsCsv).mockResolvedValue({ created: 2, skipped: 0, errors: 1, error_rows: [] });
    const onImported = vi.fn();

    render(<SpoolCsvImportModal onClose={vi.fn()} onImported={onImported} />);
    selectFile();

    const importBtn = await screen.findByText('Import 2 valid rows');
    fireEvent.click(importBtn);

    await waitFor(() => expect(api.importSpoolsCsv).toHaveBeenCalledOnce());
    expect(onImported).toHaveBeenCalledWith(2);
  });

  it('disables import when no rows are valid', async () => {
    vi.mocked(api.importSpoolsCsvPreview).mockResolvedValue({
      ...preview,
      valid_count: 0,
      error_count: 1,
      rows: [preview.rows[1]],
    });

    render(<SpoolCsvImportModal onClose={vi.fn()} onImported={vi.fn()} />);
    selectFile();

    const noValid = await screen.findByText('No valid rows');
    expect(noValid.closest('button')).toBeDisabled();
  });
});
