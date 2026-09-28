/**
 * File details entry points and indicators on the File Manager (#3077).
 *
 * The card kebab and the list row both offer "File details"; a card or row
 * shows a globe (external link, opens in a new tab), a sticky-note icon
 * (has notes) and a photo count only when the listing says so.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

vi.mock('../../components/LibraryFileDetailsModal', () => ({
  LibraryFileDetailsModal: ({ file }: { file: { filename: string } }) => (
    <div data-testid="details-modal">{file.filename}</div>
  ),
}));

const base = {
  file_path: '/library/x',
  file_size: 1048576,
  folder_id: null,
  thumbnail_path: null,
  print_name: null,
  print_time_seconds: null,
  print_count: 0,
  duplicate_count: 0,
  created_at: '2024-01-01T00:00:00Z',
};

const files = [
  {
    ...base,
    id: 1,
    filename: 'documented.stl',
    file_type: 'stl',
    external_url: 'https://www.printables.com/model/1',
    has_notes: true,
    photo_count: 3,
  },
  { ...base, id: 2, filename: 'bare.stl', file_type: 'stl', external_url: null, has_notes: false, photo_count: 0 },
];

function serve() {
  server.use(
    http.get('/api/v1/library/folders', () => HttpResponse.json([])),
    http.get('/api/v1/library/files', () => HttpResponse.json(files)),
    http.get('/api/v1/library/stats', () =>
      HttpResponse.json({
        total_files: files.length,
        total_folders: 0,
        total_size_bytes: 1,
        disk_free_bytes: 1,
        disk_total_bytes: 2,
      }),
    ),
  );
}

function cardFor(filename: string): HTMLElement {
  return screen.getByText(filename).closest('.group') as HTMLElement;
}

function rowFor(filename: string): HTMLElement {
  return screen.getByText(filename).closest('div[class*="grid-cols-"]') as HTMLElement;
}

describe('FileManagerPage — file details (#3077)', () => {
  beforeEach(() => {
    serve();
  });

  afterEach(() => {
    (localStorage.getItem as ReturnType<typeof vi.fn>).mockReset();
  });

  describe('grid view', () => {
    it('opens the details modal from the card menu', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => expect(screen.getByText('bare.stl')).toBeInTheDocument());
      const card = cardFor('bare.stl');
      const kebab = card.querySelector('.lucide-ellipsis-vertical')?.closest('button') as HTMLButtonElement;
      await user.click(kebab);
      await user.click(within(card).getByText('File details'));

      expect(await screen.findByTestId('details-modal')).toHaveTextContent('bare.stl');
    });

    it('shows the link, notes and photo indicators only when the listing carries them', async () => {
      render(<FileManagerPage />);

      await waitFor(() => expect(screen.getByText('documented.stl')).toBeInTheDocument());

      const documented = cardFor('documented.stl');
      const globe = within(documented).getByLabelText('Open link');
      expect(globe).toHaveAttribute('href', 'https://www.printables.com/model/1');
      expect(globe).toHaveAttribute('target', '_blank');
      expect(within(documented).getByLabelText('Has notes')).toBeInTheDocument();
      expect(within(documented).getByLabelText('3 photos')).toHaveTextContent('3');

      const bare = cardFor('bare.stl');
      expect(within(bare).queryByLabelText('Open link')).not.toBeInTheDocument();
      expect(within(bare).queryByLabelText('Has notes')).not.toBeInTheDocument();
      expect(within(bare).queryByLabelText(/photos?$/)).not.toBeInTheDocument();
    });

    it('offers the link in the card menu only when the file has one', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => expect(screen.getByText('documented.stl')).toBeInTheDocument());

      const documented = cardFor('documented.stl');
      await user.click(documented.querySelector('.lucide-ellipsis-vertical')?.closest('button') as HTMLButtonElement);
      expect(within(documented).getByText('Open link')).toBeInTheDocument();
      await user.keyboard('{Escape}');

      const bare = cardFor('bare.stl');
      await user.click(bare.querySelector('.lucide-ellipsis-vertical')?.closest('button') as HTMLButtonElement);
      expect(within(bare).getByText('File details')).toBeInTheDocument();
      expect(within(bare).queryByText('Open link')).not.toBeInTheDocument();
    });

    it('opens the stored link without handing the new tab a window.opener', async () => {
      // The URL comes from whoever owns the file, so the page it opens must
      // not get a handle back on Bambuddy's window.
      const open = vi.spyOn(window, 'open').mockReturnValue(null);
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => expect(screen.getByText('documented.stl')).toBeInTheDocument());

      const documented = cardFor('documented.stl');
      await user.click(documented.querySelector('.lucide-ellipsis-vertical')?.closest('button') as HTMLButtonElement);
      await user.click(within(documented).getByText('Open link'));

      expect(open).toHaveBeenCalledWith('https://www.printables.com/model/1', '_blank', 'noopener,noreferrer');
      open.mockRestore();
    });
  });

  describe('list view', () => {
    beforeEach(() => {
      (localStorage.getItem as ReturnType<typeof vi.fn>).mockImplementation((key: string) =>
        key === 'library-view-mode' ? 'list' : null,
      );
    });

    it('opens the details modal from the row action', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);

      await waitFor(() => expect(screen.getByText('bare.stl')).toBeInTheDocument());
      await user.click(within(rowFor('bare.stl')).getByTitle('File details'));

      expect(await screen.findByTestId('details-modal')).toHaveTextContent('bare.stl');
    });

    it('shows the indicators next to the name', async () => {
      render(<FileManagerPage />);

      await waitFor(() => expect(screen.getByText('documented.stl')).toBeInTheDocument());

      const documented = rowFor('documented.stl');
      expect(within(documented).getByLabelText('Open link')).toHaveAttribute(
        'href',
        'https://www.printables.com/model/1',
      );
      expect(within(documented).getByLabelText('Has notes')).toBeInTheDocument();
      expect(within(documented).getByTitle('3 photos')).toHaveTextContent('3');

      const bare = rowFor('bare.stl');
      expect(within(bare).queryByLabelText('Open link')).not.toBeInTheDocument();
      expect(within(bare).queryByLabelText('Has notes')).not.toBeInTheDocument();
    });
  });
});
