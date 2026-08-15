/**
 * The printer-card thumbnail has to survive an image the browser already has (#2826).
 *
 * The URL is cache-busted on the print *name*, which does not change while a
 * print runs. So navigating away from the printers page and back re-mounts
 * with a byte-identical `src`, which the browser serves from its in-memory
 * cache -- no network request at all, which is why the reporter's Network
 * panel was empty while the thumbnail sat on the placeholder.
 *
 * `loaded` used to be settable only by `onLoad`, while a mount effect reset it
 * to false unconditionally. For a cache hit those two are racing tasks with no
 * ordering between them, and when `load` won, the effect undid it -- and never
 * ran again, because the URL does not change again during the print. That is
 * why it reproduced 100% for the reporter and not at all on the maintainer's
 * machine.
 *
 * jsdom never loads images or fires `load`, so the race itself cannot be
 * staged here. What these tests pin is the invariant that makes the race
 * unwinnable either way: the component must read the element's own state
 * instead of assuming nothing has loaded yet.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { CoverImage } from '../../pages/PrintersPage';

vi.mock('../../hooks/useCameraStreamToken', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('../../hooks/useCameraStreamToken');
  return { ...actual, withStreamToken: (u: string) => u };
});

/** Present an <img> the way a memory-cache hit does: already complete. */
function stubAlreadyComplete(naturalWidth = 640) {
  Object.defineProperty(HTMLImageElement.prototype, 'complete', {
    get: () => true,
    configurable: true,
  });
  Object.defineProperty(HTMLImageElement.prototype, 'naturalWidth', {
    get: () => naturalWidth,
    configurable: true,
  });
}

function restoreImg() {
  // @ts-expect-error removing the test-only prototype overrides
  delete HTMLImageElement.prototype.complete;
  // @ts-expect-error removing the test-only prototype overrides
  delete HTMLImageElement.prototype.naturalWidth;
}

const URL_ = '/api/v1/printers/1/cover';

describe('CoverImage with an image the browser already has', () => {
  afterEach(restoreImg);

  it('shows it instead of the placeholder', async () => {
    stubAlreadyComplete();

    const { container } = render(<CoverImage url={URL_} printName="Benchy" />);

    await waitFor(() => {
      expect(container.querySelector('img')!.className).toContain('block');
    });
    expect(container.querySelector('img')!.className).not.toContain('hidden');
  });

  it('treats it as loaded across a remount with the same print', async () => {
    stubAlreadyComplete();

    // First visit: warms the cache in a real browser.
    const first = render(<CoverImage url={URL_} printName="Benchy" />);
    await waitFor(() => expect(first.container.querySelector('img')!.className).toContain('block'));
    first.unmount();

    // Navigating back. Same print name means a byte-identical URL, so nothing
    // is fetched -- this is the mount that used to come back blank.
    const second = render(<CoverImage url={URL_} printName="Benchy" />);

    await waitFor(() => {
      expect(second.container.querySelector('img')!.className).toContain('block');
    });
  });

  it('makes it clickable, not just visible', async () => {
    // `loaded` also gates the click-to-enlarge overlay, so a stuck `false`
    // left the thumbnail inert as well as invisible.
    stubAlreadyComplete();

    const { container } = render(<CoverImage url={URL_} printName="Benchy" />);

    await waitFor(() => {
      expect(container.querySelector('div')!.className).toContain('cursor-pointer');
    });
  });
});

describe('CoverImage when nothing is cached', () => {
  beforeEach(restoreImg);

  it('waits behind the placeholder until the image arrives', () => {
    // jsdom leaves `complete` false and never fires `load`, which is exactly
    // the cold-cache state: the placeholder is correct until it resolves.
    const { container } = render(<CoverImage url={URL_} printName="Benchy" />);

    expect(container.querySelector('img')!.className).toContain('hidden');
  });

  it('still reveals the image when onLoad fires', async () => {
    const { container } = render(<CoverImage url={URL_} printName="Benchy" />);
    const img = container.querySelector('img')!;

    img.dispatchEvent(new Event('load'));

    await waitFor(() => expect(img.className).toContain('block'));
  });

  it('falls back to the placeholder when the image fails', async () => {
    const { container } = render(<CoverImage url={URL_} printName="Benchy" />);

    container.querySelector('img')!.dispatchEvent(new Event('error'));

    await waitFor(() => expect(container.querySelector('img')).toBeNull());
  });

  it('shows the placeholder when there is no cover at all', () => {
    const { container } = render(<CoverImage url={null} printName="Benchy" />);

    expect(container.querySelector('img')).toBeNull();
  });
});

describe('CoverImage when the print changes', () => {
  afterEach(restoreImg);

  it('re-evaluates rather than carrying the previous print forward', async () => {
    // The reset-on-change behaviour the effect was added for in the first
    // place still has to hold: a new print name is a new URL, and a fresh
    // element that is not yet complete must go back behind the placeholder.
    stubAlreadyComplete();
    const { container, rerender } = render(<CoverImage url={URL_} printName="Benchy" />);
    await waitFor(() => expect(container.querySelector('img')!.className).toContain('block'));

    restoreImg();
    rerender(<CoverImage url={URL_} printName="Something Else" />);

    await waitFor(() => {
      expect(container.querySelector('img')!.className).toContain('hidden');
    });
  });

  it('keeps the cache-buster tied to the print name', () => {
    stubAlreadyComplete();
    const { container, rerender } = render(<CoverImage url={URL_} printName="Benchy" />);
    const first = container.querySelector('img')!.getAttribute('src');

    rerender(<CoverImage url={URL_} printName="Other" />);
    const second = container.querySelector('img')!.getAttribute('src');

    expect(first).toContain('v=Benchy');
    expect(second).toContain('v=Other');
    expect(first).not.toEqual(second);
  });

  it('reuses the URL for the same print, which is what makes the cache hit', () => {
    // Pinning the precondition, not an incidental detail: if this ever became
    // unique per mount the bug would vanish and so would the caching, and the
    // tests above would silently stop covering anything.
    stubAlreadyComplete();
    const a = render(<CoverImage url={URL_} printName="Benchy" />);
    const first = a.container.querySelector('img')!.getAttribute('src');
    a.unmount();

    const b = render(<CoverImage url={URL_} printName="Benchy" />);
    const second = b.container.querySelector('img')!.getAttribute('src');

    expect(second).toEqual(first);
  });
});

describe('CoverImage with a broken cached image', () => {
  afterEach(restoreImg);

  it('does not treat a zero-width complete image as loaded', async () => {
    // `complete` is also true for an image that failed. Width is what
    // separates "decoded and ready" from "finished, with nothing to show".
    stubAlreadyComplete(0);

    const { container } = render(<CoverImage url={URL_} printName="Benchy" />);

    await waitFor(() => expect(container.querySelector('img')).not.toBeNull());
    expect(container.querySelector('img')!.className).toContain('hidden');
  });
});
