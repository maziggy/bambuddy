import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ArchiveMediaDownloadModal } from '../../components/ArchiveMediaDownloadModal';
import { api } from '../../api/client';

const showToast = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

vi.mock('../../api/client', () => ({
  api: {
    getArchivePrinterMedia: vi.fn(),
    downloadArchiveTimelapse: vi.fn(),
    downloadPrinterFilesAsZip: vi.fn(),
  },
}));

const media = {
  archive_id: 1,
  printer_id: 1,
  local_timelapse: null,
  remote_files: [{
    name: 'video.mp4',
    path: '/timelapse/video.mp4',
    size: 1024,
    mtime: '2026-08-18T10:00:00Z',
    kind: 'timelapse' as const,
  }],
  warnings: [],
};

describe('ArchiveMediaDownloadModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getArchivePrinterMedia).mockResolvedValue(media);
  });

  it('does not reselect a manually deselected single file when query data refreshes', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ArchiveMediaDownloadModal
          archiveId={1}
          archiveName="Test print"
          printerName="Printer"
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    const filename = await screen.findByText('video.mp4');
    const downloadButton = screen.getByRole('button', { name: /Download selected \(1\)/i });
    expect(downloadButton).toBeEnabled();

    fireEvent.click(filename.closest('button')!);
    expect(screen.getByRole('button', { name: /Download selected \(0\)/i })).toBeDisabled();

    await act(async () => {
      queryClient.setQueryData(['archive-printer-media', 1], {
        ...media,
        remote_files: [...media.remote_files],
      });
    });

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Download selected \(0\)/i })).toBeDisabled();
    });
  });

  it('prunes a selected path that disappears during refetch', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ArchiveMediaDownloadModal
          archiveId={1}
          archiveName="Test print"
          printerName="Printer"
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('button', { name: /Download selected \(1\)/i })).toBeEnabled();
    await act(async () => {
      queryClient.setQueryData(['archive-printer-media', 1], { ...media, remote_files: [] });
    });

    await waitFor(() => expect(screen.queryByRole('button', { name: /Download selected/i })).not.toBeInTheDocument());
  });

  it('shows unavailable warnings even when no media was found', async () => {
    vi.mocked(api.getArchivePrinterMedia).mockResolvedValue({
      ...media,
      remote_files: [],
      warnings: ['timelapse_unavailable', 'ipcam_unavailable'],
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ArchiveMediaDownloadModal
          archiveId={1}
          archiveName="Test print"
          printerName="Printer"
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/No timelapse or IP camera chunks were found/)).toBeInTheDocument();
    expect(screen.getByText('The timelapse directory could not be read')).toBeInTheDocument();
    expect(screen.getByText('The IP camera directory could not be read')).toBeInTheDocument();
  });

  it('shows how far the preparation has got', async () => {
    // The file browser has shown per-file progress from this same call all
    // along; without it the archive side is a spinner for as long as the
    // transfer takes, which for a few /ipcam chunks is minutes.
    vi.mocked(api.downloadPrinterFilesAsZip).mockImplementation(
      (_printerId, _paths, _sizes, _filename, _asZip, _signal, onProgress) => {
        onProgress?.(1, 2);
        return new Promise(() => {});
      },
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ArchiveMediaDownloadModal
          archiveId={1}
          archiveName="Test print"
          printerName="Printer"
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Download selected \(1\)/i }));

    expect(await screen.findByText('1/2')).toBeInTheDocument();
  });

  it('cancels an in-flight printer preparation when the modal unmounts', async () => {
    let observedSignal: AbortSignal | undefined;
    vi.mocked(api.downloadPrinterFilesAsZip).mockImplementation(
      (_printerId, _paths, _sizes, _filename, _asZip, signal) => {
        observedSignal = signal;
        return new Promise((_resolve, reject) => {
          signal?.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')));
        });
      },
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <ArchiveMediaDownloadModal
          archiveId={1}
          archiveName="Test print"
          printerName="Printer"
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Download selected \(1\)/i }));
    await waitFor(() => expect(api.downloadPrinterFilesAsZip).toHaveBeenCalledOnce());
    unmount();

    expect(observedSignal?.aborted).toBe(true);
  });

  it('reports a local timelapse token failure', async () => {
    vi.mocked(api.getArchivePrinterMedia).mockResolvedValue({
      ...media,
      local_timelapse: { name: 'attached.mp4', size: 42 },
      remote_files: [],
    });
    vi.mocked(api.downloadArchiveTimelapse).mockRejectedValue(new Error('token expired'));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ArchiveMediaDownloadModal
          archiveId={1}
          archiveName="Test print"
          printerName="Printer"
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /^Download$/i }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      'Download failed: token expired',
      'error',
    ));
  });
});
