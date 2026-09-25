/**
 * How a preview is opened from the File Manager (#2976): double-click in the
 * grid and the list, the toolbar's Preview button, and the per-file menu.
 *
 * The preview modals themselves are stubbed — pdf.js, SheetJS and three.js
 * have their own tests and none of them render in jsdom.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { server } from '../mocks/server';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => mockNavigate,
}));

vi.mock('../../components/ModelViewerModal', () => ({
  ModelViewerModal: ({ title }: { title: string }) => <div data-testid="model-viewer-modal">{title}</div>,
}));
vi.mock('../../components/PdfPreviewModal', () => ({
  PdfPreviewModal: ({ filename }: { filename: string }) => <div data-testid="pdf-preview-modal">{filename}</div>,
}));
vi.mock('../../components/SpreadsheetPreviewModal', () => ({
  SpreadsheetPreviewModal: ({ filename }: { filename: string }) => (
    <div data-testid="sheet-preview-modal">{filename}</div>
  ),
}));
vi.mock('../../components/ImagePreviewModal', () => ({
  ImagePreviewModal: ({ filename }: { filename: string }) => <div data-testid="image-preview-modal">{filename}</div>,
}));

function libraryFile(overrides: Record<string, unknown>) {
  return {
    file_path: '/library/file',
    file_size: 4096,
    folder_id: null,
    thumbnail_path: null,
    print_name: null,
    print_time_seconds: null,
    print_count: 0,
    duplicate_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

const mockFiles = [
  libraryFile({ id: 1, filename: 'benchy.gcode.3mf', file_type: 'gcode.3mf' }),
  libraryFile({ id: 2, filename: 'bracket.stl', file_type: 'stl' }),
  libraryFile({ id: 3, filename: 'drawing.pdf', file_type: 'pdf' }),
  libraryFile({ id: 4, filename: 'parts.csv', file_type: 'csv' }),
  libraryFile({ id: 5, filename: 'photo.png', file_type: 'png', tags: [{ id: 21, name: 'reference', color: '#00ae42' }] }),
  libraryFile({ id: 6, filename: 'notes.md', file_type: 'md' }),
  libraryFile({ id: 7, filename: 'scan.tif', file_type: 'tif' }),
];

function card(name: string): HTMLElement {
  return screen.getByText(name).closest('div.group') as HTMLElement;
}

function row(name: string): HTMLElement {
  return screen.getByText(name).closest('div[class*="grid-cols-"]') as HTMLElement;
}

describe('FileManagerPage preview opening', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
    // localStorage is a module-global vi.fn mock (see __tests__/setup.ts), so
    // the view mode is programmed rather than written.
    (localStorage.getItem as ReturnType<typeof vi.fn>).mockReturnValue(null);
    server.use(
      http.get('/api/v1/library/folders', () => HttpResponse.json([])),
      http.get('/api/v1/library/files', () => HttpResponse.json(mockFiles)),
      http.get('/api/v1/library/stats', () =>
        HttpResponse.json({
          total_files: mockFiles.length,
          total_folders: 0,
          total_size_bytes: 1024,
          disk_free_bytes: 1024 * 1024,
          disk_total_bytes: 2048 * 1024,
        }),
      ),
      http.get('/api/v1/settings/', () => HttpResponse.json({ check_updates: false })),
      http.get('/api/v1/projects/', () => HttpResponse.json([])),
      http.get('/api/v1/archives/', () => HttpResponse.json([])),
    );
  });

  describe('double-click in the grid', () => {
    it('opens the image preview for a PNG and leaves the selection alone', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('photo.png');

      await user.dblClick(card('photo.png'));

      expect(await screen.findByTestId('image-preview-modal')).toHaveTextContent('photo.png');
      // The two clicks of a double-click toggle the selection twice.
      expect(screen.queryByText('1 selected')).not.toBeInTheDocument();
    });

    it('opens the 3D viewer for an STL', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('bracket.stl');

      await user.dblClick(card('bracket.stl'));

      expect(await screen.findByTestId('model-viewer-modal')).toHaveTextContent('bracket.stl');
    });

    it('sends a sliced file to the gcode viewer route', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('benchy.gcode.3mf');

      await user.dblClick(card('benchy.gcode.3mf'));

      expect(mockNavigate).toHaveBeenCalledWith('/gcode-viewer?library_file=1');
    });

    it('does nothing for a file with no preview', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('notes.md');

      await user.dblClick(card('notes.md'));

      expect(mockNavigate).not.toHaveBeenCalled();
      expect(screen.queryByTestId('image-preview-modal')).not.toBeInTheDocument();
      expect(screen.queryByTestId('pdf-preview-modal')).not.toBeInTheDocument();
      expect(screen.queryByTestId('model-viewer-modal')).not.toBeInTheDocument();
    });

    // The card's own controls are not "the row": stopping their click is not
    // enough, because dblclick is a separate native event (#2976).
    it('ignores a double-click on the card menu button', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('photo.png');

      const imageCard = card('photo.png');
      const kebab = imageCard.querySelector('.lucide-ellipsis-vertical')?.closest('button') as HTMLButtonElement;
      await user.dblClick(kebab);

      expect(screen.queryByTestId('image-preview-modal')).not.toBeInTheDocument();
    });

    // The chip's own click toggles the tag filter and re-renders the list, so
    // the dblclick is fired directly: what is under test is whether it bubbles
    // to the card, not what the two clicks before it did.
    it('ignores a double-click on a tag chip', async () => {
      render(<FileManagerPage />);
      await screen.findByText('photo.png');

      fireEvent.doubleClick(within(card('photo.png')).getByTitle('reference'));

      expect(screen.queryByTestId('image-preview-modal')).not.toBeInTheDocument();
    });
  });

  describe('the list view', () => {
    beforeEach(() => {
      (localStorage.getItem as ReturnType<typeof vi.fn>).mockImplementation((key: string) =>
        key === 'library-view-mode' ? 'list' : null,
      );
    });

    it('opens the document preview for a PDF', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('drawing.pdf');

      await user.dblClick(row('drawing.pdf'));

      expect(await screen.findByTestId('pdf-preview-modal')).toHaveTextContent('drawing.pdf');
    });

    it('opens the spreadsheet preview for a CSV', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('parts.csv');

      await user.dblClick(row('parts.csv'));

      expect(await screen.findByTestId('sheet-preview-modal')).toHaveTextContent('parts.csv');
    });

    it('offers an image file the same action-strip preview button as a document', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('photo.png');

      await user.click(within(row('photo.png')).getByTitle('Preview'));

      expect(await screen.findByTestId('image-preview-modal')).toHaveTextContent('photo.png');
    });

    it('ignores a double-click on the row action strip', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('photo.png');

      // An impatient double-tap on Rename must not also open the preview.
      await user.dblClick(within(row('photo.png')).getByTitle('Rename'));

      expect(screen.queryByTestId('image-preview-modal')).not.toBeInTheDocument();
    });

    it('ignores a double-click on the row tag cell', async () => {
      render(<FileManagerPage />);
      await screen.findByText('photo.png');

      fireEvent.doubleClick(within(row('photo.png')).getByTitle('reference'));

      expect(screen.queryByTestId('image-preview-modal')).not.toBeInTheDocument();
    });
  });

  describe('the toolbar Preview button', () => {
    it('appears for a single previewable selection and opens the preview', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('photo.png');

      await user.click(card('photo.png'));
      const preview = await screen.findByRole('button', { name: 'Preview' });
      await user.click(preview);

      expect(await screen.findByTestId('image-preview-modal')).toHaveTextContent('photo.png');
    });

    it('stays away for a multi-file selection', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('photo.png');

      await user.click(card('photo.png'));
      expect(await screen.findByRole('button', { name: 'Preview' })).toBeInTheDocument();

      await user.click(card('drawing.pdf'));
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Preview' })).not.toBeInTheDocument());
    });

    it('stays away for a file with no preview', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('notes.md');

      await user.click(card('notes.md'));

      expect(await screen.findByText('1 selected')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Preview' })).not.toBeInTheDocument();
    });
  });

  // The server thumbnails TIFF (PIL), but an <img> only decodes it on Safari,
  // so the preview is not offered rather than downloading 50 MB to fail (#2976).
  describe('a TIFF file', () => {
    it('gets no Preview entry in the card menu and no toolbar button', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('scan.tif');

      await user.click(card('scan.tif'));
      expect(await screen.findByText('1 selected')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Preview' })).not.toBeInTheDocument();

      const kebab = card('scan.tif').querySelector('.lucide-ellipsis-vertical')?.closest('button') as HTMLButtonElement;
      await user.click(kebab);
      expect(within(card('scan.tif')).queryByText('Preview')).not.toBeInTheDocument();
    });

    it('does nothing on double-click', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('scan.tif');

      await user.dblClick(card('scan.tif'));

      expect(screen.queryByTestId('image-preview-modal')).not.toBeInTheDocument();
      expect(mockNavigate).not.toHaveBeenCalled();
    });
  });

  it('offers an image file a Preview entry in the card menu', async () => {
    const user = userEvent.setup();
    render(<FileManagerPage />);
    await screen.findByText('photo.png');

    const imageCard = card('photo.png');
    const kebab = imageCard.querySelector('.lucide-ellipsis-vertical')?.closest('button') as HTMLButtonElement;
    await user.click(kebab);
    await user.click(within(imageCard).getByText('Preview'));

    expect(await screen.findByTestId('image-preview-modal')).toHaveTextContent('photo.png');
  });
});
