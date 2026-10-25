/**
 * The timelapse editor's Save starts a re-encode job on the server; the editor
 * shows its progress, can cancel it, and reports how it ended.
 */
import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { TimelapseEditorModal } from '../../components/TimelapseEditorModal';
import { api } from '../../api/client';
import type { TimelapseJob } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getTimelapseInfo: vi.fn(),
      getTimelapseThumbnails: vi.fn(),
      getTimelapseJob: vi.fn(),
      processTimelapse: vi.fn(),
      cancelTimelapseJob: vi.fn(),
    },
  };
});

const job = (overrides: Partial<TimelapseJob>): TimelapseJob => ({
  status: 'running',
  progress: null,
  output_path: null,
  message: null,
  error: null,
  ...overrides,
});

function open() {
  const onClose = vi.fn();
  const onSave = vi.fn();
  render(<TimelapseEditorModal archiveId={5} timelapseSrc="/t.mp4" onClose={onClose} onSave={onSave} />);
  return { onClose, onSave };
}

const POLL = { timeout: 3000 };

describe('TimelapseEditorModal re-encode job', () => {
  beforeEach(() => {
    vi.mocked(api.getTimelapseInfo).mockResolvedValue({
      duration: 10,
      width: 320,
      height: 240,
      fps: 30,
      codec: 'h264',
      file_size: 1000,
      has_audio: false,
    });
    vi.mocked(api.getTimelapseThumbnails).mockResolvedValue({ thumbnails: [], timestamps: [] });
    vi.mocked(api.getTimelapseJob).mockReset().mockResolvedValue(null);
    vi.mocked(api.processTimelapse).mockReset().mockResolvedValue(job({}));
    vi.mocked(api.cancelTimelapseJob).mockReset().mockResolvedValue(job({ status: 'cancelled' }));
  });

  it('keeps a portrait video short enough to leave the controls on screen (#3281)', async () => {
    open();
    await screen.findByRole('button', { name: /save/i });
    const video = document.querySelector('video');
    expect(video?.className).toMatch(/max-h-\[55dvh\]/);
    expect(video?.className).toContain('object-contain');
  });

  it('Save starts the job, shows its progress and closes with the result', async () => {
    vi.mocked(api.getTimelapseJob)
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(job({ progress: 0.4 }))
      .mockResolvedValue(job({ status: 'completed', progress: 1, message: 'Timelapse replaced successfully' }));
    const { onClose, onSave } = open();

    await userEvent.click(await screen.findByRole('button', { name: /save/i }));

    expect(api.processTimelapse).toHaveBeenCalledWith(
      5,
      expect.objectContaining({ saveMode: 'replace', audioVolume: 0.8 }),
      undefined
    );
    expect(await screen.findByText('Processing… 40%', {}, POLL)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /processing/i })).toBeDisabled();
    await waitFor(() => expect(onClose).toHaveBeenCalled(), POLL);
    expect(onSave).toHaveBeenCalled();
    expect(screen.getByText('Timelapse replaced successfully')).toBeInTheDocument();
  });

  it('Cancel stops the job and keeps the editor open', async () => {
    vi.mocked(api.getTimelapseJob).mockResolvedValue(job({ progress: 0.2 }));
    const { onClose, onSave } = open();

    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }));

    expect(api.cancelTimelapseJob).toHaveBeenCalledWith(5);
    expect(await screen.findByText('Processing cancelled')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save/i })).toBeEnabled();
    expect(onClose).not.toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('a failed job says why and keeps the editor open', async () => {
    vi.mocked(api.getTimelapseJob)
      .mockResolvedValueOnce(job({}))
      .mockResolvedValue(job({ status: 'failed', error: 'Video processing failed' }));
    const { onClose } = open();

    expect(await screen.findByText('Video processing failed', {}, POLL)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('a reopened editor shows a job still running', async () => {
    vi.mocked(api.getTimelapseJob).mockResolvedValue(job({ progress: 0.75 }));
    open();

    expect(await screen.findByText('Processing… 75%')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /processing/i })).toBeDisabled();
    expect(api.processTimelapse).not.toHaveBeenCalled();
  });

  it('an earlier finished job is not reported again', async () => {
    vi.mocked(api.getTimelapseJob).mockResolvedValue(job({ status: 'completed', message: 'Saved as old.mp4' }));
    const { onClose } = open();

    await screen.findByRole('button', { name: /save/i });
    await waitFor(() => expect(api.getTimelapseJob).toHaveBeenCalled());
    expect(screen.queryByText('Saved as old.mp4')).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('reopened after the job ended meanwhile, the editor stays open and quiet', async () => {
    vi.mocked(api.getTimelapseJob).mockResolvedValue(job({ progress: 0.5 }));
    const onClose = vi.fn();
    // Keeps the cache across the reopening, like the app's client (the test
    // client drops it at once).
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    function Viewer() {
      const [editing, setEditing] = useState(true);
      return (
        <QueryClientProvider client={client}>
          <button onClick={() => setEditing((e) => !e)}>toggle editor</button>
          {editing && <TimelapseEditorModal archiveId={5} timelapseSrc="/t.mp4" onClose={onClose} />}
        </QueryClientProvider>
      );
    }
    render(<Viewer />);
    expect(await screen.findByText('Processing… 50%')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'toggle editor' }));
    vi.mocked(api.getTimelapseJob).mockResolvedValue(job({ status: 'completed', message: 'Timelapse replaced successfully' }));
    await userEvent.click(screen.getByRole('button', { name: 'toggle editor' }));

    await waitFor(() => expect(screen.getByRole('button', { name: /save/i })).toBeEnabled());
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByText('Timelapse replaced successfully')).not.toBeInTheDocument();
  });
});
