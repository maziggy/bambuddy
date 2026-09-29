/**
 * Tests for PreviewModalShell (#2976) — the backdrop, panel size and header
 * every file preview shares.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PreviewModalShell } from '../../components/PreviewModalShell';
import { usePreviewFullscreen } from '../../hooks/usePreviewFullscreen';

const mockOnClose = vi.fn();

function Harness({ closeOnBackdropClick }: { closeOnBackdropClick?: boolean }) {
  const fullscreen = usePreviewFullscreen();
  return (
    <PreviewModalShell
      title="drawing.pdf"
      fullscreen={fullscreen}
      onClose={mockOnClose}
      closeOnBackdropClick={closeOnBackdropClick}
      actions={<button type="button">Zoom in</button>}
    >
      <div data-testid="preview-body" onDoubleClick={fullscreen.toggleFullscreen}>
        body
      </div>
    </PreviewModalShell>
  );
}

function panelOf(): HTMLElement {
  return screen.getByText('drawing.pdf').closest('.flex-col') as HTMLElement;
}

describe('PreviewModalShell', () => {
  const requestFullscreen = vi.fn();
  const exitFullscreen = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    requestFullscreen.mockResolvedValue(undefined);
    exitFullscreen.mockResolvedValue(undefined);
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

  // The browser flips fullscreenElement and fires fullscreenchange; the mocks
  // do neither, so the test plays the browser's part.
  function browserEntersFullscreen(panel: Element | null) {
    (document as { fullscreenElement: Element | null }).fullscreenElement = panel;
    act(() => {
      document.dispatchEvent(new Event('fullscreenchange'));
    });
  }

  it('gives the panel the size every preview shares', () => {
    render(<Harness />);
    const panel = panelOf();

    expect(panel.className).toContain('w-[min(1800px,96vw)]');
    expect(panel.className).toContain('h-[94vh]');
    expect(panel.className).toContain('rounded-lg');
  });

  it('renders the title, the preview actions and the close button', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    expect(screen.getByText('drawing.pdf')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Zoom in' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });

  it('goes fullscreen on a double-click in the body and fills the screen', () => {
    render(<Harness />);
    const panel = panelOf();

    fireEvent.doubleClick(screen.getByTestId('preview-body'));
    expect(requestFullscreen).toHaveBeenCalledTimes(1);
    expect(requestFullscreen.mock.instances[0]).toBe(panel);

    browserEntersFullscreen(panel);
    expect(panel.className).toContain('max-w-none');
    expect(panel.className).not.toContain('w-[min(1800px,96vw)]');
    expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeInTheDocument();
  });

  it('toggles fullscreen from the header button', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: 'Fullscreen' }));
    expect(requestFullscreen).toHaveBeenCalledTimes(1);

    browserEntersFullscreen(panelOf());
    await user.click(screen.getByRole('button', { name: 'Exit fullscreen' }));
    expect(exitFullscreen).toHaveBeenCalledTimes(1);
  });

  it('closes on Escape, but leaves it to the browser while fullscreen', () => {
    render(<Harness />);

    browserEntersFullscreen(panelOf());
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(mockOnClose).not.toHaveBeenCalled();

    browserEntersFullscreen(null);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });

  it('closes on a backdrop click only where the preview asks for it', async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Harness />);
    const backdrop = panelOf().parentElement as HTMLElement;

    await user.click(backdrop);
    expect(mockOnClose).not.toHaveBeenCalled();
    unmount();

    render(<Harness closeOnBackdropClick />);
    await user.click(panelOf().parentElement as HTMLElement);
    expect(mockOnClose).toHaveBeenCalledTimes(1);

    // A click inside the panel is not a backdrop click.
    await user.click(screen.getByTestId('preview-body'));
    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });
});
