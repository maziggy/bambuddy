/**
 * useElementFullscreen (#2976): Fullscreen API where the browser grants it,
 * the viewport-filling fallback everywhere else.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useRef } from 'react';
import { useElementFullscreen } from '../../hooks/useElementFullscreen';

function Panel() {
  const ref = useRef<HTMLDivElement>(null);
  const { isFullscreen, toggleFullscreen, apiAvailable } = useElementFullscreen(ref);
  return (
    <div ref={ref} data-testid="panel" data-fullscreen={String(isFullscreen)} data-api={String(apiAvailable)}>
      <button onClick={toggleFullscreen}>toggle</button>
    </div>
  );
}

const requestFullscreen = vi.fn();
const exitFullscreen = vi.fn();

function installApi() {
  Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: true });
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null, writable: true });
  Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: exitFullscreen });
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value: requestFullscreen });
}

function removeApi() {
  delete (document as { fullscreenEnabled?: boolean }).fullscreenEnabled;
  delete (document as { fullscreenElement?: Element | null }).fullscreenElement;
  delete (document as { exitFullscreen?: () => Promise<void> }).exitFullscreen;
  delete (HTMLElement.prototype as { requestFullscreen?: () => Promise<void> }).requestFullscreen;
}

// The mocks neither flip fullscreenElement nor fire fullscreenchange, so the
// tests play the browser's part.
function browserSetsFullscreen(element: Element | null) {
  (document as { fullscreenElement: Element | null }).fullscreenElement = element;
  act(() => {
    document.dispatchEvent(new Event('fullscreenchange'));
  });
}

describe('useElementFullscreen', () => {
  beforeEach(() => {
    requestFullscreen.mockReset().mockResolvedValue(undefined);
    exitFullscreen.mockReset().mockResolvedValue(undefined);
  });

  afterEach(() => {
    removeApi();
  });

  it('requests fullscreen on the element and follows fullscreenchange', async () => {
    installApi();
    render(<Panel />);
    const panel = screen.getByTestId('panel');
    expect(panel.dataset.api).toBe('true');

    fireEvent.click(screen.getByText('toggle'));
    expect(requestFullscreen).toHaveBeenCalledTimes(1);
    expect(requestFullscreen.mock.instances[0]).toBe(panel);
    // Nothing changes until the browser confirms.
    expect(panel.dataset.fullscreen).toBe('false');

    browserSetsFullscreen(panel);
    expect(panel.dataset.fullscreen).toBe('true');

    fireEvent.click(screen.getByText('toggle'));
    expect(exitFullscreen).toHaveBeenCalledTimes(1);

    browserSetsFullscreen(null);
    expect(panel.dataset.fullscreen).toBe('false');
  });

  it('ignores another element going fullscreen', () => {
    installApi();
    render(<Panel />);
    const panel = screen.getByTestId('panel');

    browserSetsFullscreen(document.createElement('video'));
    expect(panel.dataset.fullscreen).toBe('false');
  });

  it('falls back to the viewport-filling layout when the request is refused', async () => {
    installApi();
    requestFullscreen.mockRejectedValue(new TypeError('Permissions check failed'));
    render(<Panel />);
    const panel = screen.getByTestId('panel');

    await act(async () => {
      fireEvent.click(screen.getByText('toggle'));
    });
    expect(panel.dataset.fullscreen).toBe('true');

    // Leaving the fallback never calls the API.
    fireEvent.click(screen.getByText('toggle'));
    expect(panel.dataset.fullscreen).toBe('false');
    expect(exitFullscreen).not.toHaveBeenCalled();
  });

  it('falls back where the Fullscreen API is missing', () => {
    render(<Panel />);
    const panel = screen.getByTestId('panel');
    expect(panel.dataset.api).toBe('false');

    fireEvent.click(screen.getByText('toggle'));
    expect(panel.dataset.fullscreen).toBe('true');
    fireEvent.click(screen.getByText('toggle'));
    expect(panel.dataset.fullscreen).toBe('false');
  });

  it('leaves fullscreen when the panel unmounts while fullscreen', () => {
    installApi();
    const { unmount } = render(<Panel />);
    const panel = screen.getByTestId('panel');

    fireEvent.click(screen.getByText('toggle'));
    browserSetsFullscreen(panel);

    unmount();
    expect(exitFullscreen).toHaveBeenCalledTimes(1);
  });
});
