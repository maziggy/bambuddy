/**
 * Tests for FolderReadmePanel (#1268).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { FolderReadmePanel } from '../../components/FolderReadmePanel';
import { server } from '../mocks/server';

describe('FolderReadmePanel', () => {
  beforeEach(() => {
    // localStorage is a vi.fn() mock in setup.ts (no real persistence) and
    // calls/return values leak across tests — reset it so each test starts
    // with the collapse preference unset (expanded).
    vi.mocked(localStorage.getItem).mockReturnValue(null);
    vi.mocked(localStorage.setItem).mockClear();
  });

  it('renders nothing when the folder has no markdown (404)', async () => {
    server.use(
      http.get('/api/v1/library/folders/:id/readme', () =>
        HttpResponse.json({ detail: 'No markdown' }, { status: 404 }),
      ),
    );
    render(<FolderReadmePanel folderId={1} />);
    // Wait briefly so the query has time to resolve, then confirm no panel
    // chrome leaked into the DOM (the test render util mounts toast/provider
    // wrappers, so we can't assert `container.firstChild === null`).
    await waitFor(() => {
      expect(screen.queryByText('Truncated')).not.toBeInTheDocument();
      expect(document.querySelector('button[type="button"] svg.lucide-file-text')).toBeNull();
    });
  });

  it('renders markdown content and the filename when present', async () => {
    server.use(
      http.get('/api/v1/library/folders/:id/readme', () =>
        HttpResponse.json({
          filename: 'README.md',
          content: '# Robot model\n\nA cute robot.',
          truncated: false,
        }),
      ),
    );
    render(<FolderReadmePanel folderId={42} />);
    expect(await screen.findByText('README.md')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Robot model' })).toBeInTheDocument();
    expect(screen.getByText('A cute robot.')).toBeInTheDocument();
  });

  it('shows a Truncated chip when the API flags the content as clipped', async () => {
    server.use(
      http.get('/api/v1/library/folders/:id/readme', () =>
        HttpResponse.json({
          filename: 'description.md',
          content: 'very long content',
          truncated: true,
        }),
      ),
    );
    render(<FolderReadmePanel folderId={7} />);
    expect(await screen.findByText('Truncated')).toBeInTheDocument();
  });

  it('collapses to a reopen control and hides the content, persisting the choice (#2520)', async () => {
    server.use(
      http.get('/api/v1/library/folders/:id/readme', () =>
        HttpResponse.json({
          filename: 'README.md',
          content: '# Robot model\n\nA cute robot.',
          truncated: false,
        }),
      ),
    );
    const user = userEvent.setup();
    render(<FolderReadmePanel folderId={99} />);

    // Expanded by default: content visible.
    expect(await screen.findByRole('heading', { name: 'Robot model' })).toBeInTheDocument();

    // Collapse hides the markdown body...
    await user.click(screen.getByRole('button', { name: 'Hide README' }));
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Robot model' })).not.toBeInTheDocument();
    });
    // ...and offers a reopen control + persists the choice.
    expect(screen.getAllByRole('button', { name: 'Show README' }).length).toBeGreaterThan(0);
    expect(localStorage.setItem).toHaveBeenCalledWith('fileManager.readmeCollapsed', '1');
  });

  it('starts collapsed when the persisted preference is collapsed (#2520)', async () => {
    vi.mocked(localStorage.getItem).mockImplementation((k) =>
      k === 'fileManager.readmeCollapsed' ? '1' : null,
    );
    server.use(
      http.get('/api/v1/library/folders/:id/readme', () =>
        HttpResponse.json({
          filename: 'README.md',
          content: '# Robot model\n\nA cute robot.',
          truncated: false,
        }),
      ),
    );
    render(<FolderReadmePanel folderId={5} />);

    // Reopen control is present; the markdown body is not rendered.
    expect((await screen.findAllByRole('button', { name: 'Show README' })).length).toBeGreaterThan(0);
    expect(screen.queryByRole('heading', { name: 'Robot model' })).not.toBeInTheDocument();
  });

  /**
   * The panel renders GFM through `remarkGfmNoAutolink` rather than
   * `remark-gfm`, because `remark-gfm` reaches a regex lookbehind that iOS
   * 16.0-16.3 cannot parse and that blanked the entire app (#2971). These pin
   * the two halves of that trade: every GFM feature a README uses still works,
   * and the one feature we gave up stays given up on purpose rather than
   * creeping back in with a future dependency bump.
   */
  describe('GFM support without autolink literals (#2971)', () => {
    const withReadme = (content: string) =>
      server.use(
        http.get('/api/v1/library/folders/:id/readme', () =>
          HttpResponse.json({ filename: 'README.md', content, truncated: false }),
        ),
      );

    it('renders GFM tables', async () => {
      withReadme('| Part | Filament |\n| --- | --- |\n| Body | PLA |');
      render(<FolderReadmePanel folderId={11} />);
      expect(await screen.findByRole('columnheader', { name: 'Part' })).toBeInTheDocument();
      expect(screen.getByRole('columnheader', { name: 'Filament' })).toBeInTheDocument();
      expect(screen.getByRole('cell', { name: 'Body' })).toBeInTheDocument();
    });

    it('renders GFM strikethrough', async () => {
      withReadme('Print at ~~0.2mm~~ 0.16mm.');
      const { container } = render(<FolderReadmePanel folderId={12} />);
      await waitFor(() => {
        expect(container.querySelector('del')).toHaveTextContent('0.2mm');
      });
    });

    it('renders GFM task lists', async () => {
      withReadme('- [x] Sliced\n- [ ] Printed');
      render(<FolderReadmePanel folderId={13} />);
      const boxes = await screen.findAllByRole('checkbox');
      expect(boxes).toHaveLength(2);
      expect(boxes[0]).toBeChecked();
      expect(boxes[1]).not.toBeChecked();
    });

    it('renders GFM footnotes', async () => {
      withReadme('Supports supports[^1]\n\n[^1]: Tree, 0.4mm.');
      const { container } = render(<FolderReadmePanel folderId={14} />);
      await waitFor(() => {
        expect(container.querySelector('a[href="#user-content-fn-1"]')).toBeInTheDocument();
      });
      expect(screen.getByText(/Tree, 0.4mm./)).toBeInTheDocument();
    });

    it('keeps core-markdown links working', async () => {
      withReadme('See [the model](https://example.com/model) and <https://example.com/raw>.');
      const { container } = render(<FolderReadmePanel folderId={15} />);
      expect(await screen.findByRole('link', { name: 'the model' })).toHaveAttribute(
        'href',
        'https://example.com/model',
      );
      expect(container.querySelector('a[href="https://example.com/raw"]')).toBeInTheDocument();
    });

    it('leaves a bare URL as plain text - the deliberate cost of dropping autolink literals', async () => {
      withReadme('Grab it from https://example.com/model today.');
      const { container } = render(<FolderReadmePanel folderId={16} />);
      // Wait for the body to render before asserting on an absence.
      expect(await screen.findByText(/Grab it from/)).toBeInTheDocument();
      expect(container.querySelector('a')).toBeNull();
    });

    it('leaves a bare email as plain text', async () => {
      withReadme('Questions to nobody@example.com please.');
      const { container } = render(<FolderReadmePanel folderId={17} />);
      expect(await screen.findByText(/Questions to/)).toBeInTheDocument();
      expect(container.querySelector('a')).toBeNull();
    });
  });
});
