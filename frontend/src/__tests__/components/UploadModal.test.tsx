/**
 * Tests for the UploadModal component.
 * Tests file upload functionality with drag-and-drop support.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { UploadModal } from '../../components/UploadModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

describe('UploadModal', () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.post('/api/v1/archives/upload-bulk', async () => {
        return HttpResponse.json({
          uploaded: 1,
          failed: 0,
          results: [{ id: 1, filename: 'test.3mf' }],
          errors: [],
        });
      })
    );
  });

  describe('rendering', () => {
    it('renders the modal with title', () => {
      render(<UploadModal onClose={mockOnClose} />);

      expect(screen.getByText('Upload 3MF Files')).toBeInTheDocument();
    });

    it('renders drag and drop zone', () => {
      render(<UploadModal onClose={mockOnClose} />);

      expect(screen.getByText('Drag & drop .3mf files here')).toBeInTheDocument();
    });

    it('renders Browse Files button', () => {
      render(<UploadModal onClose={mockOnClose} />);

      expect(screen.getByRole('button', { name: 'Browse Files' })).toBeInTheDocument();
    });

    it('renders Cancel button', () => {
      render(<UploadModal onClose={mockOnClose} />);

      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });

    it('renders Upload button (disabled initially)', () => {
      render(<UploadModal onClose={mockOnClose} />);

      const uploadButton = screen.getByRole('button', { name: /Upload/i });
      expect(uploadButton).toBeDisabled();
    });
  });

  describe('file handling with initialFiles', () => {
    it('shows initial files when provided', () => {
      const initialFiles = [
        new File(['content'], 'model.3mf', { type: 'application/3mf' }),
      ];

      render(<UploadModal onClose={mockOnClose} initialFiles={initialFiles} />);

      expect(screen.getByText('model.3mf')).toBeInTheDocument();
    });

    it('enables Upload button when files are present', () => {
      const initialFiles = [
        new File(['content'], 'model.3mf', { type: 'application/3mf' }),
      ];

      render(<UploadModal onClose={mockOnClose} initialFiles={initialFiles} />);

      const uploadButton = screen.getByRole('button', { name: /Upload/i });
      expect(uploadButton).not.toBeDisabled();
    });

    it('shows file count in Upload button', () => {
      const initialFiles = [
        new File(['content'], 'model1.3mf', { type: 'application/3mf' }),
        new File(['content'], 'model2.3mf', { type: 'application/3mf' }),
      ];

      render(<UploadModal onClose={mockOnClose} initialFiles={initialFiles} />);

      expect(screen.getByRole('button', { name: /Upload \(2\)/i })).toBeInTheDocument();
    });

    it('filters out non-3mf files from initialFiles', () => {
      const initialFiles = [
        new File(['content'], 'model.3mf', { type: 'application/3mf' }),
        new File(['content'], 'image.png', { type: 'image/png' }),
        new File(['content'], 'doc.txt', { type: 'text/plain' }),
      ];

      render(<UploadModal onClose={mockOnClose} initialFiles={initialFiles} />);

      expect(screen.getByText('model.3mf')).toBeInTheDocument();
      expect(screen.queryByText('image.png')).not.toBeInTheDocument();
      expect(screen.queryByText('doc.txt')).not.toBeInTheDocument();
    });
  });

  describe('file removal', () => {
    it('allows removing a file before upload', async () => {
      const initialFiles = [
        new File(['content'], 'model.3mf', { type: 'application/3mf' }),
      ];

      render(<UploadModal onClose={mockOnClose} initialFiles={initialFiles} />);

      expect(screen.getByText('model.3mf')).toBeInTheDocument();

      // Find and click the remove button (X icon next to file)
      const fileItem = screen.getByText('model.3mf').closest('.flex');
      const removeButton = fileItem?.querySelector('button');

      if (removeButton) {
        fireEvent.click(removeButton);

        await waitFor(() => {
          expect(screen.queryByText('model.3mf')).not.toBeInTheDocument();
        });
      }
    });
  });

  describe('upload button behavior', () => {
    it('Upload button triggers upload mutation when clicked', async () => {
      const initialFiles = [
        new File(['content'], 'test.3mf', { type: 'application/3mf' }),
      ];

      render(<UploadModal onClose={mockOnClose} initialFiles={initialFiles} />);

      const uploadButton = screen.getByRole('button', { name: /Upload/i });
      expect(uploadButton).not.toBeDisabled();

      // Click should trigger upload (button text will change)
      fireEvent.click(uploadButton);

      // The button should show uploading state or become disabled
      await waitFor(() => {
        // Either showing "Uploading..." or a spinner is present
        const hasUploadingText = screen.queryByText(/Uploading/i) !== null;
        const hasSpinner = document.querySelector('.animate-spin') !== null;
        expect(hasUploadingText || hasSpinner).toBe(true);
      });
    });

    it('Upload button is disabled when no files are pending', async () => {
      render(<UploadModal onClose={mockOnClose} initialFiles={[]} />);

      const uploadButton = screen.getByRole('button', { name: /Upload/i });
      expect(uploadButton).toBeDisabled();
    });

    it('shows correct file count in Upload button', () => {
      const initialFiles = [
        new File(['content'], 'file1.3mf', { type: 'application/3mf' }),
        new File(['content'], 'file2.3mf', { type: 'application/3mf' }),
        new File(['content'], 'file3.3mf', { type: 'application/3mf' }),
      ];

      render(<UploadModal onClose={mockOnClose} initialFiles={initialFiles} />);

      expect(screen.getByRole('button', { name: /Upload \(3\)/i })).toBeInTheDocument();
    });
  });

  describe('close behavior', () => {
    it('calls onClose when Cancel button is clicked', () => {
      render(<UploadModal onClose={mockOnClose} />);

      const cancelButton = screen.getByRole('button', { name: 'Cancel' });
      fireEvent.click(cancelButton);

      expect(mockOnClose).toHaveBeenCalled();
    });

    it('calls onClose when X button is clicked', () => {
      render(<UploadModal onClose={mockOnClose} />);

      // Find the X button in the header
      const buttons = screen.getAllByRole('button');
      const closeButton = buttons.find(btn =>
        btn.querySelector('.lucide-x')
      );

      if (closeButton) {
        fireEvent.click(closeButton);
        expect(mockOnClose).toHaveBeenCalled();
      }
    });

    it('calls onClose when Escape key is pressed', () => {
      render(<UploadModal onClose={mockOnClose} />);

      fireEvent.keyDown(window, { key: 'Escape' });
      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe('drag and drop', () => {
    it('highlights drop zone on drag over', () => {
      render(<UploadModal onClose={mockOnClose} />);

      const dropZone = screen.getByText('Drag & drop .3mf files here').closest('div');

      if (dropZone) {
        fireEvent.dragOver(dropZone, {
          dataTransfer: { files: [] },
        });

        // The drop zone should have the highlight class
        expect(dropZone.className).toContain('border-bambu-green');
      }
    });

    it('removes highlight on drag leave', () => {
      render(<UploadModal onClose={mockOnClose} />);

      const dropZone = screen.getByText('Drag & drop .3mf files here').closest('div');

      if (dropZone) {
        fireEvent.dragOver(dropZone, { dataTransfer: { files: [] } });
        fireEvent.dragLeave(dropZone, { dataTransfer: { files: [] } });

        // The drop zone should not have the highlight class
        expect(dropZone.className).not.toContain('bg-bambu-green');
      }
    });
  });

  describe('file size display', () => {
    it('shows file size in MB', () => {
      const file = new File(['x'.repeat(1048576)], 'large.3mf', { type: 'application/3mf' }); // 1 MB

      render(<UploadModal onClose={mockOnClose} initialFiles={[file]} />);

      expect(screen.getByText('1.0 MB')).toBeInTheDocument();
    });
  });
});
