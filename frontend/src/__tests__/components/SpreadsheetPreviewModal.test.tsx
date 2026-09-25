/**
 * Tests for SpreadsheetPreviewModal (#2976).
 *
 * CSV parsing uses the real papaparse and XLSX parsing the real SheetJS —
 * only the network fetch is stubbed, so the tests cover the actual parse
 * paths the preview relies on.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

  describe('fullscreen', () => {
    const requestFullscreen = vi.fn();
    const exitFullscreen = vi.fn();

    beforeEach(() => {
      requestFullscreen.mockReset().mockResolvedValue(undefined);
      exitFullscreen.mockReset().mockResolvedValue(undefined);
      Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: true });
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null, writable: true });
      Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: exitFullscreen });
      Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value: requestFullscreen });
    });

    afterEach(() => {
      delete (document as { fullscreenEnabled?: boolean }).fullscreenEnabled;
      delete (document as { fullscreenElement?: Element | null }).fullscreenElement;
      delete (document as { exitFullscreen?: () => Promise<void> }).exitFullscreen;
      delete (HTMLElement.prototype as { requestFullscreen?: () => Promise<void> }).requestFullscreen;
    });

    it('double-click on the sheet toggles fullscreen', async () => {
      stubFetchWith('Article,Qty\nM3 screw,12\n');
      renderModal();
      await screen.findByText('M3 screw');
      const content = screen.getByTestId('spreadsheet-preview-content');
      const panel = screen.getByText('parts.csv').closest('.flex-col') as HTMLElement;

      fireEvent.doubleClick(content);
      expect(requestFullscreen).toHaveBeenCalledTimes(1);
      expect(requestFullscreen.mock.instances[0]).toBe(panel);

      // The mocks do not flip fullscreenElement or fire fullscreenchange;
      // play the browser's part.
      (document as { fullscreenElement: Element | null }).fullscreenElement = panel;
      act(() => {
        document.dispatchEvent(new Event('fullscreenchange'));
      });
      expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeInTheDocument();

      fireEvent.doubleClick(content);
      expect(exitFullscreen).toHaveBeenCalledTimes(1);
    });

    it('offers a fullscreen button in the header', async () => {
      stubFetchWith('Article,Qty\nM3 screw,12\n');
      const user = userEvent.setup();
      renderModal();
      await screen.findByText('M3 screw');

      await user.click(screen.getByRole('button', { name: 'Fullscreen' }));
      expect(requestFullscreen).toHaveBeenCalledTimes(1);
    });
  });

  describe('grid thumbnail snapshot', () => {
    // The snapshot is drawn into an offscreen canvas, which jsdom cannot
    // back: both the 2D context and toBlob are stood in for.
    function stubCanvas2d(blob: Blob | null = new Blob(['png'], { type: 'image/png' })) {
      const context = {
        fillStyle: '',
        strokeStyle: '',
        lineWidth: 0,
        font: '',
        textBaseline: '',
        fillRect: vi.fn(),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        stroke: vi.fn(),
        fillText: vi.fn(),
      };
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
        context as unknown as CanvasRenderingContext2D,
      );
      vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((callback) => callback(blob));
      return context;
    }

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it('hands the first sheet to onSnapshot as a PNG', async () => {
      const context = stubCanvas2d();
      stubFetchWith('Article,Qty\nM3 screw,12\n');
      const onSnapshot = vi.fn();

      renderModal({ onSnapshot });
      await screen.findByText('M3 screw');

      await waitFor(() => expect(onSnapshot).toHaveBeenCalledTimes(1));
      expect((onSnapshot.mock.calls[0][0] as Blob).type).toBe('image/png');
      // The mini table is what makes it recognisable in the grid.
      expect(context.fillText).toHaveBeenCalledWith('M3 screw', expect.any(Number), expect.any(Number), expect.any(Number));
    });

    it('draws the first sheet that has rows, not an empty leading one', async () => {
      stubCanvas2d();
      const workbook = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([]), 'Cover');
      XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([['Part', 'Qty'], ['Hinge', '2']]), 'Bom');
      stubFetchWith(XLSX.write(workbook, { type: 'array', bookType: 'xlsx' }) as ArrayBuffer);
      const onSnapshot = vi.fn();

      renderModal({ onSnapshot, filename: 'bom.xlsx', fileType: 'xlsx' });
      // The empty Cover sheet opens first, so the tab strip is what says the
      // workbook has parsed.
      await screen.findByRole('button', { name: 'Bom' });

      await waitFor(() => expect(onSnapshot).toHaveBeenCalledTimes(1));
    });

    it('stays quiet for a sheet with no rows at all', async () => {
      stubCanvas2d();
      stubFetchWith('');
      const onSnapshot = vi.fn();

      renderModal({ onSnapshot });
      await screen.findByText('This sheet is empty');

      expect(onSnapshot).not.toHaveBeenCalled();
    });
  });
});
