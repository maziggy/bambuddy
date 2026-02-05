/**
 * Tests for the ModelViewerModal component.
 * Tests fullscreen toggle, plate selector, object counts, and tab switching.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { ModelViewerModal } from '../../components/ModelViewerModal';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// Mock ModelViewer and GcodeViewer to avoid WebGL/Three.js issues in tests
vi.mock('../../components/ModelViewer', () => ({
  ModelViewer: ({ className }: { className?: string }) => (
    <div data-testid="model-viewer" className={className}>
      Model Viewer Mock
    </div>
  ),
}));

vi.mock('../../components/GcodeViewer', () => ({
  GcodeViewer: ({ className }: { className?: string }) => (
    <div data-testid="gcode-viewer" className={className}>
      G-code Viewer Mock
    </div>
  ),
}));

const mockCapabilities = {
  has_model: true,
  has_gcode: true,
  has_source: false,
  build_volume: { x: 256, y: 256, z: 256 },
  filament_colors: ['#00ae42'],
};

const mockPlatesResponse = {
  is_multi_plate: true,
  plates: [
    {
      index: 1,
      name: 'Plate 1',
      has_thumbnail: true,
      thumbnail_url: '/api/v1/archives/1/plates/1/thumbnail',
      print_time_seconds: 3600,
      filament_used_grams: 50.5,
      object_count: 3,
      objects: ['Cube', 'Sphere', 'Cylinder'],
      filaments: [{ color: '#00ae42', type: 'PLA', name: 'Bambu PLA Basic' }],
    },
    {
      index: 2,
      name: 'Plate 2',
      has_thumbnail: true,
      thumbnail_url: '/api/v1/archives/1/plates/2/thumbnail',
      print_time_seconds: 1800,
      filament_used_grams: 25.0,
      object_count: 2,
      objects: ['Base', 'Cover'],
      filaments: [{ color: '#ff0000', type: 'PLA', name: 'Red PLA' }],
    },
  ],
};

const mockSinglePlateResponse = {
  is_multi_plate: false,
  plates: [
    {
      index: 1,
      name: null,
      has_thumbnail: false,
      thumbnail_url: null,
      print_time_seconds: 7200,
      filament_used_grams: 100.0,
      object_count: 5,
      objects: ['Model 1', 'Model 2', 'Model 3', 'Model 4', 'Model 5'],
      filaments: [],
    },
  ],
};

describe('ModelViewerModal', () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/archives/:id/capabilities', () => {
        return HttpResponse.json(mockCapabilities);
      }),
      http.get('/api/v1/archives/:id/plates', () => {
        return HttpResponse.json(mockPlatesResponse);
      })
    );
  });

  describe('rendering', () => {
    it('renders the modal with title', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model.3mf"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Test Model.3mf')).toBeInTheDocument();
    });

    it('renders Open in Slicer button', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Open in Slicer')).toBeInTheDocument();
      });
    });

    it('shows loading spinner while fetching capabilities', () => {
      server.use(
        http.get('/api/v1/archives/:id/capabilities', async () => {
          await new Promise((r) => setTimeout(r, 100));
          return HttpResponse.json(mockCapabilities);
        })
      );

      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      const loader = document.querySelector('.animate-spin');
      expect(loader).toBeInTheDocument();
    });
  });

  describe('tabs', () => {
    it('renders 3D Model and G-code tabs', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('3D Model')).toBeInTheDocument();
        expect(screen.getByText('G-code Preview')).toBeInTheDocument();
      });
    });

    it('shows not available label when model is not available', async () => {
      server.use(
        http.get('/api/v1/archives/:id/capabilities', () => {
          return HttpResponse.json({
            ...mockCapabilities,
            has_model: false,
          });
        })
      );

      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('(not available)')).toBeInTheDocument();
      });
    });

    it('shows not sliced label when gcode is not available', async () => {
      server.use(
        http.get('/api/v1/archives/:id/capabilities', () => {
          return HttpResponse.json({
            ...mockCapabilities,
            has_gcode: false,
          });
        })
      );

      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('(not sliced)')).toBeInTheDocument();
      });
    });

    it('disables tab when capability is not available', async () => {
      server.use(
        http.get('/api/v1/archives/:id/capabilities', () => {
          return HttpResponse.json({
            ...mockCapabilities,
            has_gcode: false,
          });
        })
      );

      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        const gcodeTab = screen.getByText('G-code Preview').closest('button');
        expect(gcodeTab).toBeDisabled();
      });
    });
  });

  describe('fullscreen', () => {
    it('renders fullscreen toggle button', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        // Look for the maximize icon button
        const buttons = screen.getAllByRole('button');
        const fullscreenButton = buttons.find(
          (btn) => btn.querySelector('.lucide-maximize-2') || btn.title === 'Enter fullscreen'
        );
        expect(fullscreenButton).toBeInTheDocument();
      });
    });
  });

  describe('object count', () => {
    it('displays object count for multi-plate files', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        // Total objects across both plates = 3 + 2 = 5
        // The header shows "All Plates: 5 objects" in a span
        const objectCountBadge = screen.getByText(/All Plates.*5 objects/);
        expect(objectCountBadge).toBeInTheDocument();
      });
    });

    it('updates object count when plate is selected', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Plate 1')).toBeInTheDocument();
      });

      // Click on Plate 1
      fireEvent.click(screen.getByText('Plate 1'));

      await waitFor(() => {
        // Plate 1 has 3 objects - header should update to show "Plate 1: 3 objects"
        const objectCountBadge = screen.getByText(/Plate 1.*3 objects/);
        expect(objectCountBadge).toBeInTheDocument();
      });
    });
  });

  describe('plate selector', () => {
    it('shows plates panel for multi-plate files', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Plates')).toBeInTheDocument();
        // Use getAllByText for "All Plates" since it appears in header and panel
        const allPlatesElements = screen.getAllByText('All Plates');
        expect(allPlatesElements.length).toBeGreaterThan(0);
        expect(screen.getByText('2 plates')).toBeInTheDocument();
      });
    });

    it('shows individual plate buttons', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Plate 1')).toBeInTheDocument();
        expect(screen.getByText('Plate 2')).toBeInTheDocument();
      });
    });

    it('shows object count for each plate', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        // Each plate shows its object count in the grid
        expect(screen.getByText('3 objects')).toBeInTheDocument();
        expect(screen.getByText('2 objects')).toBeInTheDocument();
      });
    });

    it('hides plates panel for single-plate files', async () => {
      server.use(
        http.get('/api/v1/archives/:id/plates', () => {
          return HttpResponse.json(mockSinglePlateResponse);
        })
      );

      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        // Should show object count but not plate selector
        expect(screen.getByText(/5 objects/)).toBeInTheDocument();
      });

      // Plates panel should not be shown for single plate
      expect(screen.queryByText('2 plates')).not.toBeInTheDocument();
    });

    it('selects All Plates by default', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        // Find the All Plates button in the grid (the one with "2 plates" sibling text)
        const platesCountText = screen.getByText('2 plates');
        const allPlatesButton = platesCountText.closest('button');
        // The selected button should have the green border class
        expect(allPlatesButton).toHaveClass('border-bambu-green');
      });
    });

    it('allows plate selection via click', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Plate 1')).toBeInTheDocument();
      });

      // Click on Plate 1 - this should not throw
      const plate1Button = screen.getByText('Plate 1').closest('button');
      expect(plate1Button).toBeInTheDocument();
      fireEvent.click(plate1Button!);

      // After clicking, the header should show Plate 1 info
      await waitFor(() => {
        expect(screen.getByText(/Plate 1.*3 objects/)).toBeInTheDocument();
      });
    });
  });

  describe('close behavior', () => {
    it('calls onClose when X button is clicked', async () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      const closeButton = screen.getAllByRole('button').find(
        (btn) => btn.querySelector('.lucide-x')
      );

      if (closeButton) {
        fireEvent.click(closeButton);
        expect(mockOnClose).toHaveBeenCalled();
      }
    });

    it('calls onClose when Escape key is pressed', () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      fireEvent.keyDown(window, { key: 'Escape' });
      expect(mockOnClose).toHaveBeenCalled();
    });

    it('calls onClose when backdrop is clicked', () => {
      render(
        <ModelViewerModal
          archiveId={1}
          title="Test Model"
          onClose={mockOnClose}
        />
      );

      const backdrop = document.querySelector('.fixed.inset-0');
      if (backdrop) {
        fireEvent.click(backdrop);
        expect(mockOnClose).toHaveBeenCalled();
      }
    });
  });

  describe('library file mode', () => {
    it('renders for library file', async () => {
      server.use(
        http.get('/api/v1/library/files/:id/plates', () => {
          return HttpResponse.json(mockSinglePlateResponse);
        })
      );

      render(
        <ModelViewerModal
          libraryFileId={1}
          title="Library Model.3mf"
          fileType="3mf"
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Library Model.3mf')).toBeInTheDocument();

      await waitFor(() => {
        expect(screen.getByText('3D Model')).toBeInTheDocument();
      });
    });

    it('disables Open in Slicer for non-3mf library files', async () => {
      render(
        <ModelViewerModal
          libraryFileId={1}
          title="Model.stl"
          fileType="stl"
          onClose={mockOnClose}
        />
      );

      await waitFor(() => {
        const slicerButton = screen.getByText('Open in Slicer').closest('button');
        expect(slicerButton).toBeDisabled();
      });
    });
  });
});
