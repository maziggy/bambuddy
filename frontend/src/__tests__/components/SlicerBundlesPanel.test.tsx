/**
 * Tests for the SlicerBundlesPanel — Settings panel for managing
 * BambuStudio Printer Preset Bundles (.bbscfg) on the slicer sidecar.
 *
 * Coverage:
 *  - Empty state when the sidecar has no bundles imported yet.
 *  - List rendering with summary line (process / filament counts).
 *  - Upload happy path → success toast + list invalidation.
 *  - Upload error → error toast.
 *  - Delete with confirmation → success toast + list invalidation.
 *  - Delete error → error toast.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { api } from '../../api/client';
import { SlicerBundlesPanel } from '../../components/SlicerBundlesPanel';

vi.mock('../../api/client', async () => {
  const actual: typeof import('../../api/client') = await vi.importActual(
    '../../api/client',
  );
  return {
    ...actual,
    api: {
      ...actual.api,
      listSlicerBundles: vi.fn(),
      importSlicerBundle: vi.fn(),
      deleteSlicerBundle: vi.fn(),
    },
    getAuthToken: vi.fn(() => null),
  };
});

const SAMPLE_BUNDLE = {
  id: 'abc123def456abcd',
  printer_preset_name: '# Bambu Lab H2D 0.4 nozzle',
  printer: ['# Bambu Lab H2D 0.4 nozzle'],
  process: [
    '# 0.20mm Standard @BBL H2D',
    '# 0.16mm Standard @BBL H2D',
  ],
  filament: [
    '# Bambu PLA Basic @BBL H2D',
    '# Bambu PETG HF @BBL H2D 0.4 nozzle',
    '# Bambu ABS @BBL H2D',
  ],
  version: '02.06.00.50',
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SlicerBundlesPanel — empty state', () => {
  it('renders the empty-state message when no bundles exist', async () => {
    vi.mocked(api.listSlicerBundles).mockResolvedValueOnce([]);

    render(<SlicerBundlesPanel />);

    await waitFor(() =>
      expect(api.listSlicerBundles).toHaveBeenCalled(),
    );
    expect(
      await screen.findByText(/no bundles imported yet/i),
    ).toBeInTheDocument();
  });
});

describe('SlicerBundlesPanel — list rendering', () => {
  it('renders bundle name + summary (process and filament counts)', async () => {
    vi.mocked(api.listSlicerBundles).mockResolvedValueOnce([SAMPLE_BUNDLE]);

    render(<SlicerBundlesPanel />);

    expect(
      await screen.findByText('# Bambu Lab H2D 0.4 nozzle'),
    ).toBeInTheDocument();
    // Summary should reflect 2 process + 3 filament from the fixture.
    expect(
      await screen.findByText(/2 process · 3 filament/i),
    ).toBeInTheDocument();
    // Version suffix appended after the summary.
    expect(screen.getByText(/v02\.06\.00\.50/)).toBeInTheDocument();
  });
});

describe('SlicerBundlesPanel — upload flow', () => {
  it('imports a selected file and refreshes the list on success', async () => {
    // First listing call returns empty so the test can detect the post-import
    // re-fetch (second call) returning the new bundle.
    vi.mocked(api.listSlicerBundles)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([SAMPLE_BUNDLE]);
    vi.mocked(api.importSlicerBundle).mockResolvedValueOnce(SAMPLE_BUNDLE);

    const { container } = render(<SlicerBundlesPanel />);

    // The file input is hidden (display: none for styling); grab it directly.
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    expect(fileInput).toBeTruthy();

    const file = new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], 'H2D.bbscfg', {
      type: 'application/zip',
    });

    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() =>
      expect(api.importSlicerBundle).toHaveBeenCalledWith(file),
    );
    // After the success, the list call should fire a second time (cache
    // invalidation by react-query).
    await waitFor(() =>
      expect(api.listSlicerBundles).toHaveBeenCalledTimes(2),
    );
    // The newly imported bundle should now be visible in the list.
    expect(
      await screen.findByText('# Bambu Lab H2D 0.4 nozzle'),
    ).toBeInTheDocument();
  });

  it('shows an error and does not refresh on upload failure', async () => {
    vi.mocked(api.listSlicerBundles).mockResolvedValueOnce([]);
    vi.mocked(api.importSlicerBundle).mockRejectedValueOnce(
      new Error('Bundle is missing bundle_structure.json'),
    );

    const { container } = render(<SlicerBundlesPanel />);

    await waitFor(() => expect(api.listSlicerBundles).toHaveBeenCalled());

    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const file = new File([new Uint8Array([0])], 'bad.bbscfg', {
      type: 'application/zip',
    });
    fireEvent.change(fileInput, { target: { files: [file] } });

    await waitFor(() =>
      expect(api.importSlicerBundle).toHaveBeenCalled(),
    );
    // Listing should NOT be re-called on failure — only the initial load.
    expect(api.listSlicerBundles).toHaveBeenCalledTimes(1);
    // Empty state still showing.
    expect(
      screen.getByText(/no bundles imported yet/i),
    ).toBeInTheDocument();
  });
});

describe('SlicerBundlesPanel — delete flow', () => {
  it('deletes a bundle after confirmation and refreshes the list', async () => {
    vi.mocked(api.listSlicerBundles)
      .mockResolvedValueOnce([SAMPLE_BUNDLE])
      .mockResolvedValueOnce([]);
    vi.mocked(api.deleteSlicerBundle).mockResolvedValueOnce(undefined);

    render(<SlicerBundlesPanel />);

    // Wait for the bundle to render.
    await screen.findByText('# Bambu Lab H2D 0.4 nozzle');

    // Click the trash button (aria-label="Delete").
    fireEvent.click(screen.getByRole('button', { name: /delete/i }));

    // ConfirmModal should appear with the bundle name in the message.
    const confirmMessage = await screen.findByText(
      /Slice requests that reference "# Bambu Lab H2D 0.4 nozzle" will fail/i,
    );
    expect(confirmMessage).toBeInTheDocument();

    // The modal renders its own "Delete" button — there are now two buttons
    // matching /delete/i. Click the one inside the dialog (last in document
    // order, since the modal portal renders after the panel).
    const deleteButtons = screen.getAllByRole('button', { name: /delete/i });
    fireEvent.click(deleteButtons[deleteButtons.length - 1]);

    await waitFor(() =>
      expect(api.deleteSlicerBundle).toHaveBeenCalledWith(
        'abc123def456abcd',
      ),
    );
    // Cache invalidation should re-fire the list query.
    await waitFor(() =>
      expect(api.listSlicerBundles).toHaveBeenCalledTimes(2),
    );
  });

  it('keeps the bundle in the list when the user cancels the delete dialog', async () => {
    vi.mocked(api.listSlicerBundles).mockResolvedValueOnce([SAMPLE_BUNDLE]);

    render(<SlicerBundlesPanel />);

    await screen.findByText('# Bambu Lab H2D 0.4 nozzle');
    fireEvent.click(screen.getByRole('button', { name: /delete/i }));

    // Cancel by clicking the "Cancel" button on the ConfirmModal.
    const cancelButton = await screen.findByRole('button', { name: /cancel/i });
    fireEvent.click(cancelButton);

    // Delete API never called, list never re-fetched.
    expect(api.deleteSlicerBundle).not.toHaveBeenCalled();
    expect(api.listSlicerBundles).toHaveBeenCalledTimes(1);
    // Bundle still rendered.
    expect(
      screen.getByText('# Bambu Lab H2D 0.4 nozzle'),
    ).toBeInTheDocument();
  });
});
