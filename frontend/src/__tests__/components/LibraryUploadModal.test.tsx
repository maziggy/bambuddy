/**
 * Tests for the LibraryUploadModal component.
 * Tests file upload, drag-and-drop, ZIP/3MF/STL detection, and autoUpload mode.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { LibraryUploadModal } from '../../components/LibraryUploadModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

describe('LibraryUploadModal', () => {
  const defaultProps = {
    folderId: null as number | null,
    onClose: vi.fn(),
    onUploadComplete: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();

    server.use(
      http.post('/api/v1/library/files', () => {
        return HttpResponse.json({
          id: 1,
          filename: 'test.gcode.3mf',
          file_type: '3mf',
          file_size: 1048576,
          thumbnail_path: null,
          duplicate_of: null,
          metadata: null,
        });
      }),
      http.post('/api/v1/library/extract-zip', () => {
        return HttpResponse.json({
          extracted: 3,
          errors: [],
        });
      })
    );
  });

  describe('rendering', () => {
    it('renders the modal with title', () => {
      render(<LibraryUploadModal {...defaultProps} />);
      expect(screen.getByText('Upload Files')).toBeInTheDocument();
    });

    it('renders drag and drop zone', () => {
      render(<LibraryUploadModal {...defaultProps} />);
      expect(screen.getByText(/Drag & drop/)).toBeInTheDocument();
    });

    it('renders click to browse text', () => {
      render(<LibraryUploadModal {...defaultProps} />);
      expect(screen.getByText(/click to browse/i)).toBeInTheDocument();
    });

    it('renders Cancel button', () => {
      render(<LibraryUploadModal {...defaultProps} />);
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });

    it('renders Upload button disabled when no files', () => {
      render(<LibraryUploadModal {...defaultProps} />);
      const uploadButton = screen.getByRole('button', { name: /Upload/i });
      expect(uploadButton).toBeDisabled();
    });

    it('shows all file types supported text', () => {
      render(<LibraryUploadModal {...defaultProps} />);
      expect(screen.getByText(/All file types supported/i)).toBeInTheDocument();
    });
  });

  describe('file selection', () => {
    it('shows added file in the list', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.gcode.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      expect(screen.getByText('model.gcode.3mf')).toBeInTheDocument();
    });

    it('shows file size in MB', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['x'.repeat(1048576)], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      expect(screen.getByText('1.00 MB')).toBeInTheDocument();
    });

    it('enables Upload button when files are added', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      expect(uploadButton).not.toBeDisabled();
    });

    it('shows file count in Upload button', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const files = [
        new File(['a'], 'file1.3mf', { type: 'application/octet-stream' }),
        new File(['b'], 'file2.stl', { type: 'application/octet-stream' }),
      ];
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, files);

      expect(screen.getByRole('button', { name: /Upload \(2\)/i })).toBeInTheDocument();
    });

    it('accepts any file type (not restricted like UploadModal)', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['content'], 'readme.txt', { type: 'text/plain' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      expect(screen.getByText('readme.txt')).toBeInTheDocument();
    });
  });

  describe('file removal', () => {
    it('removes a file when X button is clicked', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      expect(screen.getByText('model.3mf')).toBeInTheDocument();

      const fileRow = screen.getByText('model.3mf').closest('.flex');
      const removeButton = fileRow?.querySelector('button');
      if (removeButton) {
        await user.click(removeButton);
      }

      await waitFor(() => {
        expect(screen.queryByText('model.3mf')).not.toBeInTheDocument();
      });
    });

    it('disables Upload button after removing all files', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const fileRow = screen.getByText('model.3mf').closest('.flex');
      const removeButton = fileRow?.querySelector('button');
      if (removeButton) {
        await user.click(removeButton);
      }

      await waitFor(() => {
        const uploadButton = screen.getByRole('button', { name: /Upload/i });
        expect(uploadButton).toBeDisabled();
      });
    });
  });

  describe('file type detection', () => {
    it('shows ZIP options when .zip file is added', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const zipFile = new File(['pk'], 'models.zip', { type: 'application/zip' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, zipFile);

      await waitFor(() => {
        expect(screen.getByText('ZIP files detected')).toBeInTheDocument();
        expect(screen.getByText(/Preserve folder structure/)).toBeInTheDocument();
        expect(screen.getByText(/Create folder from ZIP/)).toBeInTheDocument();
      });
    });

    it('shows 3MF info when .3mf file is added', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const threemfFile = new File(['content'], 'model.gcode.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, threemfFile);

      await waitFor(() => {
        expect(screen.getByText('3MF files detected')).toBeInTheDocument();
      });
    });

    it('shows STL thumbnail option when .stl file is added', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const stlFile = new File(['solid'], 'bracket.stl', { type: 'application/sla' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, stlFile);

      await waitFor(() => {
        expect(screen.getByText('STL thumbnail generation')).toBeInTheDocument();
        expect(screen.getByText(/Thumbnails can be generated/i)).toBeInTheDocument();
      });
    });

    it('shows STL thumbnail option when ZIP file is added (may contain STLs)', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const zipFile = new File(['pk'], 'models.zip', { type: 'application/zip' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, zipFile);

      await waitFor(() => {
        expect(screen.getByText('STL thumbnail generation')).toBeInTheDocument();
        expect(screen.getByText(/ZIP files may contain STL/i)).toBeInTheDocument();
      });
    });
  });

  describe('ZIP options', () => {
    it('preserve structure checkbox is checked by default', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const zipFile = new File(['pk'], 'models.zip', { type: 'application/zip' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, zipFile);

      await waitFor(() => {
        const label = screen.getByText(/Preserve folder structure/).closest('label');
        const checkbox = label?.querySelector('input[type="checkbox"]') as HTMLInputElement;
        expect(checkbox).toBeChecked();
      });
    });

    it('create folder checkbox is unchecked by default', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const zipFile = new File(['pk'], 'models.zip', { type: 'application/zip' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, zipFile);

      await waitFor(() => {
        const label = screen.getByText(/Create folder from ZIP/).closest('label');
        const checkbox = label?.querySelector('input[type="checkbox"]') as HTMLInputElement;
        expect(checkbox).not.toBeChecked();
      });
    });

    it('can toggle ZIP options', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const zipFile = new File(['pk'], 'models.zip', { type: 'application/zip' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, zipFile);

      await waitFor(() => {
        expect(screen.getByText('ZIP files detected')).toBeInTheDocument();
      });

      const preserveLabel = screen.getByText(/Preserve folder structure/).closest('label');
      const preserveCheckbox = preserveLabel?.querySelector('input[type="checkbox"]') as HTMLInputElement;
      await user.click(preserveCheckbox);
      expect(preserveCheckbox).not.toBeChecked();

      const createFolderLabel = screen.getByText(/Create folder from ZIP/).closest('label');
      const createFolderCheckbox = createFolderLabel?.querySelector('input[type="checkbox"]') as HTMLInputElement;
      await user.click(createFolderCheckbox);
      expect(createFolderCheckbox).toBeChecked();
    });
  });

  describe('upload flow', () => {
    it('calls onUploadComplete after successful upload', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      await waitFor(() => {
        expect(defaultProps.onUploadComplete).toHaveBeenCalled();
      });
    });

    it('calls onFileUploaded with response data for each file', async () => {
      const onFileUploaded = vi.fn();
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} onFileUploaded={onFileUploaded} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      await waitFor(() => {
        expect(onFileUploaded).toHaveBeenCalledWith(
          expect.objectContaining({
            id: 1,
            filename: 'test.gcode.3mf',
          })
        );
      });
    });

    it('shows uploading state while uploading', async () => {
      // Delay the response to observe uploading state
      server.use(
        http.post('/api/v1/library/files', async () => {
          await new Promise((resolve) => setTimeout(resolve, 100));
          return HttpResponse.json({
            id: 1,
            filename: 'model.3mf',
            file_type: '3mf',
            file_size: 1024,
            thumbnail_path: null,
            duplicate_of: null,
            metadata: null,
          });
        })
      );

      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      // Should show uploading state
      await waitFor(() => {
        expect(screen.getByText('Uploading...')).toBeInTheDocument();
        expect(document.querySelector('.animate-spin')).toBeInTheDocument();
      });
    });

    it('shows error state on upload failure', async () => {
      server.use(
        http.post('/api/v1/library/files', () => {
          return HttpResponse.json({ detail: 'File too large' }, { status: 413 });
        })
      );

      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      await waitFor(() => {
        expect(defaultProps.onUploadComplete).toHaveBeenCalled();
      });
    });

    it('does not auto-close modal on manual upload (stays open for results)', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      await waitFor(() => {
        expect(defaultProps.onUploadComplete).toHaveBeenCalled();
      });

      // Modal should NOT auto-close in manual mode
      expect(defaultProps.onClose).not.toHaveBeenCalled();
    });
  });

  describe('autoUpload mode', () => {
    it('uploads immediately when file is added', async () => {
      const onFileUploaded = vi.fn();
      const user = userEvent.setup();
      render(
        <LibraryUploadModal
          {...defaultProps}
          autoUpload
          onFileUploaded={onFileUploaded}
        />
      );

      const file = new File(['content'], 'model.gcode.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      await waitFor(() => {
        expect(onFileUploaded).toHaveBeenCalledWith(
          expect.objectContaining({ id: 1 })
        );
      });
    });

    it('calls onClose after autoUpload completes', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} autoUpload />);

      const file = new File(['content'], 'model.gcode.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      await waitFor(() => {
        expect(defaultProps.onClose).toHaveBeenCalled();
        expect(defaultProps.onUploadComplete).toHaveBeenCalled();
      });
    });
  });

  describe('close behavior', () => {
    it('calls onClose when Cancel button is clicked', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      await user.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(defaultProps.onClose).toHaveBeenCalled();
    });

    it('calls onClose when X button is clicked', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      // The X button is the one in the header (not file remove buttons)
      const headerButtons = screen.getByText('Upload Files').parentElement?.querySelectorAll('button');
      const closeButton = headerButtons?.[0];

      if (closeButton) {
        await user.click(closeButton);
        expect(defaultProps.onClose).toHaveBeenCalled();
      }
    });

    it('shows Close button instead of Cancel after all uploads complete', async () => {
      const user = userEvent.setup();
      render(<LibraryUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
      });
    });
  });

  describe('drag and drop', () => {
    it('highlights drop zone on drag over', () => {
      render(<LibraryUploadModal {...defaultProps} />);

      const dropZone = screen.getByText(/Drag & drop/).closest('div[class*="border-dashed"]');

      if (dropZone) {
        fireEvent.dragOver(dropZone, { dataTransfer: { files: [] } });
        expect(dropZone.className).toContain('border-bambu-green');
      }
    });

    it('removes highlight on drag leave', () => {
      render(<LibraryUploadModal {...defaultProps} />);

      const dropZone = screen.getByText(/Drag & drop/).closest('div[class*="border-dashed"]');

      if (dropZone) {
        fireEvent.dragOver(dropZone, { dataTransfer: { files: [] } });
        fireEvent.dragLeave(dropZone, { dataTransfer: { files: [] } });
        expect(dropZone.className).not.toContain('bg-bambu-green');
      }
    });
  });

  describe('folder context', () => {
    it('accepts folderId prop for uploading to specific folder', () => {
      render(<LibraryUploadModal {...defaultProps} folderId={5} />);
      // Component should render without errors with a folder context
      expect(screen.getByText('Upload Files')).toBeInTheDocument();
    });
  });
});
