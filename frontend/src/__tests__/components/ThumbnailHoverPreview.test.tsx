/**
 * The larger thumbnail shown while the pointer rests on a queue thumbnail
 * (#3313). Desktop only, and portalled so a batch group's overflow:hidden
 * cannot clip it.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ThumbnailHoverPreview } from '../../components/ThumbnailHoverPreview';

const realMatchMedia = window.matchMedia;

function pointer(fine: boolean) {
  window.matchMedia = ((query: string) => ({ ...realMatchMedia(query), matches: fine })) as typeof window.matchMedia;
}

function renderThumb(src: string | null, rect: Partial<DOMRect> = {}) {
  render(
    <div style={{ overflow: 'hidden' }} data-testid="clipping-parent">
      <ThumbnailHoverPreview src={src} className="thumb">
        <img src={src ?? ''} alt="thumb" />
      </ThumbnailHoverPreview>
    </div>,
  );
  const anchor = screen.getByAltText('thumb').parentElement as HTMLElement;
  anchor.getBoundingClientRect = () =>
    ({ left: 100, right: 156, top: 200, bottom: 256, width: 56, height: 56, ...rect }) as DOMRect;
  return anchor;
}

describe('ThumbnailHoverPreview', () => {
  afterEach(() => {
    window.matchMedia = realMatchMedia;
  });

  it('shows the image enlarged while hovered, outside any clipping parent', () => {
    pointer(true);
    const anchor = renderThumb('/thumb.png');

    fireEvent.mouseEnter(anchor);

    const preview = screen.getByTestId('thumbnail-hover-preview');
    expect(preview).toHaveAttribute('src', '/thumb.png');
    expect(preview).toHaveStyle({ width: '256px', height: '256px', left: '164px', top: '200px' });
    expect(screen.getByTestId('clipping-parent')).not.toContainElement(preview);

    fireEvent.mouseLeave(anchor);
    expect(screen.queryByTestId('thumbnail-hover-preview')).not.toBeInTheDocument();
  });

  it('opens to the left when there is no room on the right', () => {
    pointer(true);
    const anchor = renderThumb('/thumb.png', { left: window.innerWidth - 60, right: window.innerWidth - 4 });

    fireEvent.mouseEnter(anchor);

    expect(screen.getByTestId('thumbnail-hover-preview')).toHaveStyle({ left: `${window.innerWidth - 60 - 8 - 256}px` });
  });

  it('stays inside the viewport at the bottom edge', () => {
    pointer(true);
    const anchor = renderThumb('/thumb.png', { top: window.innerHeight - 30 });

    fireEvent.mouseEnter(anchor);

    expect(screen.getByTestId('thumbnail-hover-preview')).toHaveStyle({ top: `${window.innerHeight - 256 - 8}px` });
  });

  it('closes when the page scrolls', () => {
    pointer(true);
    const anchor = renderThumb('/thumb.png');
    fireEvent.mouseEnter(anchor);

    fireEvent.scroll(window);

    expect(screen.queryByTestId('thumbnail-hover-preview')).not.toBeInTheDocument();
  });

  it('shows nothing on a touch screen', () => {
    pointer(false);
    const anchor = renderThumb('/thumb.png');

    fireEvent.mouseEnter(anchor);

    expect(screen.queryByTestId('thumbnail-hover-preview')).not.toBeInTheDocument();
  });

  it('shows nothing without an image', () => {
    pointer(true);
    const anchor = renderThumb(null);

    fireEvent.mouseEnter(anchor);

    expect(screen.queryByTestId('thumbnail-hover-preview')).not.toBeInTheDocument();
  });
});
