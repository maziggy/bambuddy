/**
 * Tall dialogs on short screens (#3664).
 *
 * The backdrop scrolls inside the visible area, stops the swipe at its edge,
 * and follows the visual viewport, which the Android keyboard shrinks while
 * fixed elements keep the full height. jsdom has no layout, so the classes
 * and the inline size that produce this are what is pinned here.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, fireEvent, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ScrollableModalOverlay } from '../../components/ScrollableModalOverlay';
import { PurgeArchivesModal } from '../../components/PurgeArchivesModal';
import { PurgeOldFilesModal } from '../../components/PurgeOldFilesModal';
import { CreateUserAdvancedAuthModal } from '../../components/CreateUserAdvancedAuthModal';

function scrollingBackdropOf(el: Element): HTMLElement | null {
  return el.closest('.overflow-y-auto.overscroll-contain');
}

class FakeVisualViewport extends EventTarget {
  offsetTop = 0;
  height = 680;
}

// jsdom has no visualViewport; a browser without one gets the plain backdrop.
function withVisualViewport(viewport: FakeVisualViewport | undefined) {
  vi.stubGlobal('visualViewport', viewport);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('ScrollableModalOverlay', () => {
  it('scrolls, stops the swipe at its edge and centres a short panel', () => {
    withVisualViewport(undefined);
    render(
      <ScrollableModalOverlay className="bg-black/50">
        <div>panel</div>
      </ScrollableModalOverlay>,
    );
    const backdrop = screen.getByText('panel').parentElement as HTMLElement;
    const classes = backdrop.className.split(/\s+/);
    expect(classes).toEqual(
      expect.arrayContaining(['fixed', 'inset-0', 'overflow-y-auto', 'overscroll-contain', 'z-50', 'bg-black/50']),
    );
    // Auto margins centre a short panel and resolve to zero for a tall one,
    // so its top stays reachable (items-center would cut it off).
    expect(classes).toContain('[&>*]:my-auto');
    expect(classes).not.toContain('justify-center');
    // Without a visual viewport it simply covers the screen.
    expect(backdrop.style.height).toBe('');
  });

  it('follows the visible area while the keyboard opens and pans', () => {
    const viewport = new FakeVisualViewport();
    withVisualViewport(viewport);
    render(
      <ScrollableModalOverlay>
        <div>panel</div>
      </ScrollableModalOverlay>,
    );
    const backdrop = screen.getByText('panel').parentElement as HTMLElement;
    expect(backdrop.style.height).toBe('680px');

    act(() => {
      viewport.height = 380;
      viewport.dispatchEvent(new Event('resize'));
    });
    expect(backdrop.style.height).toBe('380px');
    expect(backdrop.style.bottom).toBe('auto');

    act(() => {
      viewport.offsetTop = 120;
      viewport.dispatchEvent(new Event('scroll'));
    });
    expect(backdrop.style.top).toBe('120px');
  });

  it('stops following the viewport once the dialog closes', () => {
    const viewport = new FakeVisualViewport();
    const remove = vi.spyOn(viewport, 'removeEventListener');
    withVisualViewport(viewport);
    const { unmount } = render(
      <ScrollableModalOverlay>
        <div>panel</div>
      </ScrollableModalOverlay>,
    );
    unmount();
    expect(remove.mock.calls.map(([type]) => type).sort()).toEqual(['resize', 'scroll']);
  });

  it('passes clicks on the backdrop through, and a stacking class', () => {
    withVisualViewport(undefined);
    const onClick = vi.fn();
    render(
      <ScrollableModalOverlay onClick={onClick} zIndexClassName="z-[60]">
        <div>panel</div>
      </ScrollableModalOverlay>,
    );
    const backdrop = screen.getByText('panel').parentElement as HTMLElement;
    fireEvent.click(backdrop);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(backdrop.className.split(/\s+/)).toContain('z-[60]');
    expect(backdrop.className.split(/\s+/)).not.toContain('z-50');
  });
});

describe('dialogs that can be taller than the screen use it (#3664)', () => {
  it('Archives > Purge old', async () => {
    server.use(
      http.get('*/api/v1/archives/purge/preview', () =>
        HttpResponse.json({ count: 1, total_bytes: 1, sample_filenames: [], older_than_days: 90 }),
      ),
    );
    render(<PurgeArchivesModal onClose={vi.fn()} />);
    const cancel = await screen.findByRole('button', { name: /cancel/i });
    expect(scrollingBackdropOf(cancel)).not.toBeNull();
  });

  it('File Manager > Purge old', async () => {
    server.use(
      http.get('*/library/purge/preview', () =>
        HttpResponse.json({
          count: 1,
          total_bytes: 1,
          sample_filenames: [],
          older_than_days: 90,
          include_never_printed: true,
        }),
      ),
    );
    render(<PurgeOldFilesModal onClose={vi.fn()} />);
    const cancel = await screen.findByRole('button', { name: /cancel/i });
    expect(scrollingBackdropOf(cancel)).not.toBeNull();
  });

  it('Create User with advanced authentication', () => {
    render(
      <CreateUserAdvancedAuthModal
        formData={{ username: '', password: '', email: '', role: 'user', group_ids: [] }}
        setFormData={vi.fn()}
        groups={[]}
        onClose={vi.fn()}
        onCreate={vi.fn()}
        isCreating={false}
        isCreateButtonDisabled
      />,
    );
    const cancel = screen.getByRole('button', { name: /cancel/i });
    expect(scrollingBackdropOf(cancel)).not.toBeNull();
  });
});
