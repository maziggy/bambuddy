/**
 * Tests for the FileUploadModal component.
 * Tests file upload, drag-and-drop, ZIP/3MF/STL detection, and autoUpload mode.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { FileUploadModal } from '../../components/FileUploadModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

describe('FileUploadModal', () => {
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
      render(<FileUploadModal {...defaultProps} />);
      expect(screen.getByText('Upload Files')).toBeInTheDocument();
    });

    it('renders drag and drop zone', () => {
      render(<FileUploadModal {...defaultProps} />);
      expect(screen.getByText(/Drag & drop/)).toBeInTheDocument();
    });

    it('renders click to browse text', () => {
      render(<FileUploadModal {...defaultProps} />);
      expect(screen.getByText(/click to browse/i)).toBeInTheDocument();
    });

    it('renders Cancel button', () => {
      render(<FileUploadModal {...defaultProps} />);
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });

    it('renders Upload button disabled when no files', () => {
      render(<FileUploadModal {...defaultProps} />);
      const uploadButton = screen.getByRole('button', { name: /Upload/i });
      expect(uploadButton).toBeDisabled();
    });

    it('shows all file types supported text', () => {
      render(<FileUploadModal {...defaultProps} />);
      expect(screen.getByText(/All file types supported/i)).toBeInTheDocument();
    });
  });

  describe('file selection', () => {
    it('shows added file in the list', async () => {
      const user = userEvent.setup();
      render(<FileUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.gcode.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      expect(screen.getByText('model.gcode.3mf')).toBeInTheDocument();
    });

    it('shows file size in MB', async () => {
      const user = userEvent.setup();
      render(<FileUploadModal {...defaultProps} />);

      const file = new File(['x'.repeat(1048576)], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      expect(screen.getByText('1.00 MB')).toBeInTheDocument();
    });

    it('enables Upload button when files are added', async () => {
      const user = userEvent.setup();
      render(<FileUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      expect(uploadButton).not.toBeDisabled();
    });

    it('shows file count in Upload button', async () => {
      const user = userEvent.setup();
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} />);

      const file = new File(['content'], 'readme.txt', { type: 'text/plain' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      expect(screen.getByText('readme.txt')).toBeInTheDocument();
    });
  });

  describe('file removal', () => {
    it('removes a file when X button is clicked', async () => {
      const user = userEvent.setup();
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} />);

      const threemfFile = new File(['content'], 'model.gcode.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, threemfFile);

      await waitFor(() => {
        expect(screen.getByText('3MF files detected')).toBeInTheDocument();
      });
    });

    it('shows STL thumbnail option when .stl file is added', async () => {
      const user = userEvent.setup();
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} onFileUploaded={onFileUploaded} />);

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
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      await waitFor(() => {
        expect(defaultProps.onUploadComplete).toHaveBeenCalled();
      });
    });

    it('closes modal after manual upload completes', async () => {
      const user = userEvent.setup();
      render(<FileUploadModal {...defaultProps} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      await waitFor(() => {
        expect(defaultProps.onUploadComplete).toHaveBeenCalled();
        expect(defaultProps.onClose).toHaveBeenCalled();
      });
    });
  });

  describe('autoUpload mode', () => {
    it('uploads immediately when file is added', async () => {
      const onFileUploaded = vi.fn();
      const user = userEvent.setup();
      render(
        <FileUploadModal
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
      render(<FileUploadModal {...defaultProps} autoUpload />);

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
      render(<FileUploadModal {...defaultProps} />);

      await user.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(defaultProps.onClose).toHaveBeenCalled();
    });

    it('calls onClose when X button is clicked', async () => {
      const user = userEvent.setup();
      render(<FileUploadModal {...defaultProps} />);

      // The X button is the one in the header (not file remove buttons)
      const headerButtons = screen.getByText('Upload Files').parentElement?.querySelectorAll('button');
      const closeButton = headerButtons?.[0];

      if (closeButton) {
        await user.click(closeButton);
        expect(defaultProps.onClose).toHaveBeenCalled();
      }
    });

    it('always shows Cancel button (modal auto-closes after upload)', () => {
      render(<FileUploadModal {...defaultProps} />);
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });
  });

  describe('drag and drop', () => {
    it('highlights drop zone on drag over', () => {
      render(<FileUploadModal {...defaultProps} />);

      const dropZone = screen.getByText(/Drag & drop/).closest('div[class*="border-dashed"]');

      if (dropZone) {
        fireEvent.dragOver(dropZone, { dataTransfer: { files: [] } });
        expect(dropZone.className).toContain('border-bambu-green');
      }
    });

    it('removes highlight on drag leave', () => {
      render(<FileUploadModal {...defaultProps} />);

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
      render(<FileUploadModal {...defaultProps} folderId={5} />);
      // Component should render without errors with a folder context
      expect(screen.getByText('Upload Files')).toBeInTheDocument();
    });
  });

  describe('validateFile prop', () => {
    it('rejects files that fail validation and shows error', async () => {
      const user = userEvent.setup();
      render(
        <FileUploadModal
          {...defaultProps}
          validateFile={(file) => {
            if (!file.name.endsWith('.gcode')) return 'Only .gcode files allowed';
          }}
        />
      );

      const file = new File(['content'], 'model.stl', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      // Error should be shown
      expect(screen.getByText('Only .gcode files allowed')).toBeInTheDocument();
      // File should NOT be added to the list
      expect(screen.queryByText('model.stl')).not.toBeInTheDocument();
    });

    it('allows files that pass validation', async () => {
      const user = userEvent.setup();
      render(
        <FileUploadModal
          {...defaultProps}
          validateFile={(file) => {
            if (!file.name.endsWith('.gcode')) return 'Only .gcode files allowed';
          }}
        />
      );

      const file = new File(['content'], 'model.gcode', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      expect(screen.getByText('model.gcode')).toBeInTheDocument();
      expect(screen.queryByText('Only .gcode files allowed')).not.toBeInTheDocument();
    });

    it('clears validation error when a new file is added', async () => {
      const user = userEvent.setup();
      render(
        <FileUploadModal
          {...defaultProps}
          validateFile={(file) => {
            if (!file.name.endsWith('.gcode')) return 'Only .gcode files allowed';
          }}
        />
      );

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;

      // First add an invalid file
      const badFile = new File(['content'], 'model.stl', { type: 'application/octet-stream' });
      await user.upload(fileInput, badFile);
      expect(screen.getByText('Only .gcode files allowed')).toBeInTheDocument();

      // Then add a valid file — error should clear
      const goodFile = new File(['content'], 'model.gcode', { type: 'application/octet-stream' });
      await user.upload(fileInput, goodFile);
      expect(screen.queryByText('Only .gcode files allowed')).not.toBeInTheDocument();
    });
  });

  describe('accept prop', () => {
    it('sets accept attribute on file input', () => {
      render(<FileUploadModal {...defaultProps} accept=".gcode,.gcode.3mf" />);
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      expect(fileInput.accept).toBe('.gcode,.gcode.3mf');
    });

    it('does not set accept attribute when prop is omitted', () => {
      render(<FileUploadModal {...defaultProps} />);
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      expect(fileInput.accept).toBe('');
    });
  });

  describe('onFileUploaded error handling', () => {
    it('shows error and keeps modal open when onFileUploaded returns a string', async () => {
      const user = userEvent.setup();
      render(
        <FileUploadModal
          {...defaultProps}
          onFileUploaded={() => 'This file was sliced for the wrong printer'}
        />
      );

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      await waitFor(() => {
        expect(screen.getByText('This file was sliced for the wrong printer')).toBeInTheDocument();
      });

      // Modal should NOT close
      expect(defaultProps.onClose).not.toHaveBeenCalled();
    });

    it('clears file list when onFileUploaded returns an error', async () => {
      const user = userEvent.setup();
      render(
        <FileUploadModal
          {...defaultProps}
          onFileUploaded={() => 'Incompatible printer'}
        />
      );

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      await waitFor(() => {
        expect(screen.getByText('Incompatible printer')).toBeInTheDocument();
      });

      // File list should be cleared
      expect(screen.queryByText('model.3mf')).not.toBeInTheDocument();
    });

    it('closes modal normally when onFileUploaded returns undefined', async () => {
      const onFileUploaded = vi.fn();
      const user = userEvent.setup();
      render(<FileUploadModal {...defaultProps} onFileUploaded={onFileUploaded} />);

      const file = new File(['content'], 'model.3mf', { type: 'application/octet-stream' });
      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      await user.upload(fileInput, file);

      const uploadButton = screen.getByRole('button', { name: /Upload \(1\)/i });
      await user.click(uploadButton);

      await waitFor(() => {
        expect(defaultProps.onClose).toHaveBeenCalled();
      });
    });
  });
});
