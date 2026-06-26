/**
 * Tests for FolderReadmePanel (#1268).
 */

import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { FolderReadmePanel } from '../../components/FolderReadmePanel';
import { server } from '../mocks/server';

describe('FolderReadmePanel', () => {
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
});
