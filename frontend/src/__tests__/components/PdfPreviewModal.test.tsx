/**
 * Tests for PdfPreviewModal (#2976).
 *
 * pdf.js cannot rasterise inside jsdom (no real canvas), so the library is
 * mocked at the module boundary; the tests cover the modal's own logic —
 * loading, page navigation, and error/size fallbacks.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PdfPreviewModal } from '../../components/PdfPreviewModal';

const pdfjsMocks = vi.hoisted(() => {
  const render = vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() }));
  const getPage = vi.fn(async () => ({
    getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
    render,
  }));
  const getDocument = vi.fn(() => ({
    promise: Promise.resolve({ numPages: 3, getPage }),
    destroy: vi.fn(),
  }));
  return { render, getPage, getDocument };
});

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: pdfjsMocks.getDocument,
}));

vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: 'pdf.worker.min.mjs' }));

vi.mock('../../api/client', () => ({
  api: {
    getLibraryFileDownloadUrl: vi.fn((id: number) => `http://test/library/files/${id}/download`),
  },
  getAuthToken: () => null,
}));

const mockOnClose = vi.fn();

function renderModal(props: Partial<Parameters<typeof PdfPreviewModal>[0]> = {}) {
  return render(
    <PdfPreviewModal
      libraryFileId={7}
      filename="drawing.pdf"
      fileSize={1024}
      onClose={mockOnClose}
      {...props}
    />,
  );
}

describe('PdfPreviewModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(new Uint8Array([1, 2, 3]), { status: 200 })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the page indicator once the document loads', async () => {
    renderModal();
    expect(await screen.findByText('Page 1 of 3')).toBeInTheDocument();
    expect(pdfjsMocks.render).toHaveBeenCalled();
  });

  it('navigates between pages', async () => {
    const user = userEvent.setup();
    renderModal();
    await screen.findByText('Page 1 of 3');

    await user.click(screen.getByRole('button', { name: 'Next page' }));
    expect(await screen.findByText('Page 2 of 3')).toBeInTheDocument();
    expect(pdfjsMocks.getPage).toHaveBeenLastCalledWith(2);

    await user.click(screen.getByRole('button', { name: 'Previous page' }));
    expect(await screen.findByText('Page 1 of 3')).toBeInTheDocument();
  });

  it('shows an error message when the document cannot be parsed', async () => {
    pdfjsMocks.getDocument.mockReturnValueOnce({ promise: Promise.reject(new Error('bad pdf')), destroy: vi.fn() } as never);
    renderModal();
    expect(await screen.findByText('This file cannot be previewed.')).toBeInTheDocument();
  });

  it('refuses oversized files without fetching them', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    renderModal({ fileSize: 500 * 1024 * 1024 });

    expect(await screen.findByText(/too large to preview/)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
