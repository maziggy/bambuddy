/**
 * Tests for SpreadsheetPreviewModal (#2976).
 *
 * CSV parsing uses the real papaparse and XLSX parsing the real SheetJS —
 * only the network fetch is stubbed, so the tests cover the actual parse
 * paths the preview relies on.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as XLSX from 'xlsx';
import { SpreadsheetPreviewModal } from '../../components/SpreadsheetPreviewModal';

vi.mock('../../api/client', () => ({
  api: {
    getLibraryFileDownloadUrl: vi.fn((id: number) => `http://test/library/files/${id}/download`),
  },
  getAuthToken: () => null,
}));

const mockOnClose = vi.fn();

function stubFetchWith(bytes: ArrayBuffer | Uint8Array | string) {
  const body = typeof bytes === 'string' ? new TextEncoder().encode(bytes) : bytes;
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(body as BodyInit, { status: 200 })),
  );
}

function renderModal(props: Partial<Parameters<typeof SpreadsheetPreviewModal>[0]> = {}) {
  return render(
    <SpreadsheetPreviewModal
      libraryFileId={42}
      filename="parts.csv"
      fileType="csv"
      fileSize={1234}
      onClose={mockOnClose}
      {...props}
    />,
  );
}

describe('SpreadsheetPreviewModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders CSV cells as a read-only grid', async () => {
    stubFetchWith('Article,Qty\nM3 screw,12\nBearing 608,4\n');
    renderModal();

    expect(await screen.findByText('M3 screw')).toBeInTheDocument();
    expect(screen.getByText('Bearing 608')).toBeInTheDocument();
    expect(screen.getByText('Qty')).toBeInTheDocument();
  });

  it('shows a truncation notice for long CSV files', async () => {
    const rows = Array.from({ length: 600 }, (_, i) => `row${i},${i}`).join('\n');
    stubFetchWith(`name,value\n${rows}\n`);
    renderModal();

    expect(await screen.findByText('row0')).toBeInTheDocument();
    expect(screen.getByText(/Showing the first/)).toBeInTheDocument();
    expect(screen.queryByText('row599')).not.toBeInTheDocument();
  });

  it('renders XLSX workbooks with one tab per sheet', async () => {
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(
      workbook,
      XLSX.utils.aoa_to_sheet([
        ['Part', 'Price'],
        ['Nozzle', '12.50'],
      ]),
      'Parts',
    );
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([['SupplierList']]), 'Suppliers');
    const bytes = XLSX.write(workbook, { type: 'array', bookType: 'xlsx' }) as ArrayBuffer;
    stubFetchWith(bytes);

    const user = userEvent.setup();
    renderModal({ filename: 'bom.xlsx', fileType: 'xlsx' });

    expect(await screen.findByText('Nozzle')).toBeInTheDocument();
    // Both sheets appear as tabs; switching shows the second sheet's content.
    await user.click(screen.getByRole('button', { name: 'Suppliers' }));
    expect(await screen.findByText('SupplierList')).toBeInTheDocument();
    expect(screen.queryByText('Nozzle')).not.toBeInTheDocument();
  });

  it('shows an error message for a broken workbook', async () => {
    // A truncated ZIP: SheetJS recognises the PK magic, then fails to parse.
    // (Plain text bytes would be leniently read as CSV, not rejected.)
    stubFetchWith(new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x01, 0x02, 0x03]));
    renderModal({ filename: 'broken.xlsx', fileType: 'xlsx' });

    expect(await screen.findByText('This file cannot be previewed.')).toBeInTheDocument();
  });

  it('refuses oversized files without fetching them', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    renderModal({ fileSize: 100 * 1024 * 1024 });

    expect(await screen.findByText(/too large to preview/)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
