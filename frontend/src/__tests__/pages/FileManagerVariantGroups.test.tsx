/**
 * A group of versions is one card / row in the File Manager (#3628).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { setAuthToken } from '../../api/client';
import { VariantSelectionBox } from '../../components/fileManager/VariantSelectionBox';

vi.mock('../../components/ModelViewerModal', () => ({
  ModelViewerModal: () => <div />,
}));

type Item = Record<string, unknown>;

function item(id: number, filename: string, over: Item = {}): Item {
  return {
    id,
    folder_id: null,
    is_external: false,
    filename,
    file_type: 'gcode.3mf',
    file_size: 1048576,
    thumbnail_path: null,
    print_count: 0,
    duplicate_count: 0,
    created_by_id: 7,
    created_by_username: 'operator1',
    created_at: '2024-01-01T00:00:00Z',
    fs_modified_at: null,
    print_name: null,
    print_time_seconds: null,
    filament_used_grams: null,
    sliced_for_model: null,
    tags: [],
    variant_group_id: null,
    variant_count: 0,
    variant_group_name: null,
    variant_position: null,
    variant_elsewhere: false,
    folder_name: null,
    ...over,
  };
}

const vase = (id: number, model: string, position: number, over: Item = {}) =>
  item(id, `vase_${model}.gcode.3mf`, {
    variant_group_id: 7,
    variant_count: 3,
    variant_group_name: 'Vase',
    variant_position: position,
    sliced_for_model: model,
    print_time_seconds: 3600 * (position + 1),
    filament_used_grams: 40 + position,
    ...over,
  });

const baseFiles: Item[] = [
  vase(21, 'X1C', 0),
  vase(22, 'P1S', 1),
  vase(23, 'A1', 2, { variant_elsewhere: true, folder_id: 9, folder_name: 'Archive' }),
  item(30, 'plain.stl', { file_type: 'stl' }),
  // Its other versions are not visible to this user: a plain file, no badge.
  item(31, 'solo.gcode.3mf', { variant_group_id: 8, variant_count: 1, variant_position: 0, sliced_for_model: 'H2D' }),
];

let requests: URL[] = [];

function serve(files: Item[] = baseFiles) {
  requests = [];
  server.use(
    http.get('/api/v1/library/folders', () => HttpResponse.json([])),
    http.get('/api/v1/library/files', ({ request }) => {
      requests.push(new URL(request.url));
      return HttpResponse.json(files);
    }),
    http.get('/api/v1/library/stats', () =>
      HttpResponse.json({ total_files: 5, total_folders: 0, total_size_bytes: 1, disk_free_bytes: 1e12, disk_total_bytes: 1e13 }),
    ),
    http.get('/api/v1/settings/', () => HttpResponse.json({ check_updates: false, library_disk_warning_gb: 5 })),
    http.get('/api/v1/projects/', () => HttpResponse.json([])),
    http.get('/api/v1/archives/', () => HttpResponse.json([])),
  );
}

const mockAuthUser = (permissions: string[]) => {
  setAuthToken('test-token', 'session');
  server.use(
    http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
    http.get('*/api/v1/auth/me', () =>
      HttpResponse.json({ id: 7, username: 'operator1', is_admin: false, permissions }),
    ),
    http.get('/api/v1/users/', () => HttpResponse.json([])),
  );
};

const card = () => screen.getByTestId('variant-group-card');

describe('version groups in the File Manager', () => {
  beforeEach(() => {
    localStorage.clear();
    serve();
  });
  afterEach(() => {
    setAuthToken(null);
  });

  describe('grid view', () => {
    it('shows the group as one card with a version badge, not one card per version', async () => {
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');

      expect(screen.getAllByTestId('variant-group-card')).toHaveLength(1);
      expect(within(card()).getByText('Vase')).toBeInTheDocument();
      expect(within(card()).getByText('3 versions')).toBeInTheDocument();
      // The versions are behind the toggle, and none is a card of its own.
      expect(screen.queryByText('vase_X1C.gcode.3mf')).not.toBeInTheDocument();
      expect(within(card()).getByText('X1C · P1S · A1')).toBeInTheDocument();
    });

    it('asks the server for the members of other folders', async () => {
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');
      expect(requests.length).toBeGreaterThan(0);
      expect(requests.every((u) => u.searchParams.get('with_variant_members') === 'true')).toBe(true);
    });

    it('shows a group with a single visible version as a plain file, without a badge', async () => {
      render(<FileManagerPage />);
      await screen.findByText('solo.gcode.3mf');
      expect(screen.queryByText('1 versions')).not.toBeInTheDocument();
      expect(screen.getAllByTestId('variant-group-card')).toHaveLength(1);
    });

    it('lists the versions in a popover, with a hint for the one in another folder', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');

      const toggle = within(card()).getByRole('button', { name: 'Show versions' });
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await user.click(toggle);

      expect(toggle).toHaveAttribute('aria-expanded', 'true');
      const popover = screen.getByRole('dialog', { name: 'Versions of Vase' });
      expect(toggle.getAttribute('aria-controls')).toBe(popover.id);
      expect(card()).not.toContainElement(popover);
      const lines = within(popover).getAllByTestId('variant-member-line');
      expect(lines.map((l) => l.getAttribute('data-file-id'))).toEqual(['21', '22', '23']);
      expect(within(lines[0]).getByText('X1C')).toBeInTheDocument();
      expect(within(lines[0]).getByText('1h 0m')).toBeInTheDocument();
      expect(within(lines[0]).getByText('40 g')).toBeInTheDocument();
      // Only the version outside this folder carries the hint.
      expect(within(lines[2]).getByText('in Archive')).toBeInTheDocument();
      expect(within(lines[0]).queryByTestId('variant-elsewhere-hint')).not.toBeInTheDocument();
      // Expanding does not select the group.
      expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
    });

    it('selects the versions in this folder with the card and clears them with a second click', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');

      await user.click(within(card()).getByText('Vase'));
      expect(await screen.findByText('2 selected')).toBeInTheDocument();
      expect(within(card()).getByRole('checkbox')).toHaveAttribute('aria-checked', 'true');

      await user.click(within(card()).getByText('Vase'));
      await waitFor(() => expect(screen.queryByText('2 selected')).not.toBeInTheDocument());
    });

    it('keeps the selection when a card menu entry is chosen', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');
      expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();

      await user.click(within(card()).getByRole('button', { name: 'Actions for version group Vase' }));
      await user.click(await screen.findByRole('button', { name: 'Ungroup' }));
      expect(await screen.findByText('Ungroup versions')).toBeInTheDocument();
      expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
      expect(within(card()).getByRole('checkbox')).toHaveAttribute('aria-checked', 'false');
    });

    it('says in the delete confirm how many versions sit in other folders', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');
      await user.click(within(card()).getByRole('button', { name: 'Actions for version group Vase' }));
      await user.click(await screen.findByRole('button', { name: 'Delete all versions' }));
      expect(await screen.findByText(/all 3 versions of "Vase"\. 1 of them is in another folder\./)).toBeInTheDocument();
    });

    describe('versions popover', () => {
      async function open() {
        const user = userEvent.setup();
        render(<FileManagerPage />);
        await screen.findByText('plain.stl');
        const toggle = within(card()).getByRole('button', { name: 'Show versions' });
        await user.click(toggle);
        const popover = await screen.findByRole('dialog', { name: 'Versions of Vase' });
        return { user, toggle, popover };
      }
      const closed = () => expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

      it('does not change the card or its grid when opened', async () => {
        const gridClasses = () => card().parentElement!.className;
        const cardClasses = card;
        render(<FileManagerPage />);
        await screen.findByText('plain.stl');
        const before = { card: cardClasses().className, grid: gridClasses(), cards: card().parentElement!.children.length };
        await userEvent.setup().click(within(card()).getByRole('button', { name: 'Show versions' }));
        await screen.findByRole('dialog');
        expect(card().className).toBe(before.card);
        expect(card().className).not.toContain('col-span-full');
        expect(gridClasses()).toBe(before.grid);
        expect(card().parentElement!.children.length).toBe(before.cards);
        expect(within(card()).getAllByRole('heading', { level: 3 })).toHaveLength(1);
      });

      it('is a portal to the body under the card, not wider than 420px nor narrower than 340px', async () => {
        const { popover } = await open();
        expect(popover.parentElement!.parentElement).toBe(document.body);
        expect(popover.style.position).toBe('fixed');
        expect(popover).toHaveAttribute('data-testid', 'variant-versions-popover');
        const width = parseInt(popover.style.width, 10);
        expect(width).toBeGreaterThanOrEqual(340);
        expect(width).toBeLessThanOrEqual(420);
        expect(within(popover).getAllByTestId('variant-member-line')).toHaveLength(3);
        // No sheet chrome on a wide screen.
        expect(within(popover).queryByRole('button', { name: 'Close' })).not.toBeInTheDocument();
      });

      it('moves focus to the first control inside it', async () => {
        const { popover } = await open();
        expect(popover).toContainElement(document.activeElement as HTMLElement);
        expect(document.activeElement).toBe(within(popover).getAllByRole('button')[0]);
      });

      it('closes with a second click on the toggle', async () => {
        const { user, toggle } = await open();
        await user.click(toggle);
        closed();
        expect(toggle).toHaveAttribute('aria-expanded', 'false');
      });

      it('closes with Escape and returns focus to the toggle', async () => {
        const { user, toggle } = await open();
        await user.keyboard('{Escape}');
        closed();
        expect(toggle).toHaveFocus();
      });

      it('closes on a click outside, not on one inside', async () => {
        const { user, popover } = await open();
        await user.click(within(popover).getAllByTestId('variant-member-line')[0]);
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        await user.click(screen.getByText('plain.stl'));
        closed();
      });

      it('closes when Tab leaves it and returns focus to the toggle', async () => {
        const { user, toggle, popover } = await open();
        const buttons = within(popover).getAllByRole('button');
        buttons[buttons.length - 1].focus();
        await user.tab();
        closed();
        expect(toggle).toHaveFocus();
      });

      it('keeps Tab between its own controls', async () => {
        const { user, popover } = await open();
        await user.tab();
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(popover).toContainElement(document.activeElement as HTMLElement);
      });

      it('closes when the page scrolls, not when the popover does', async () => {
        const { popover } = await open();
        await new Promise((resolve) => setTimeout(resolve, 200));
        fireEvent.scroll(popover.lastElementChild as HTMLElement);
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        fireEvent.scroll(card().parentElement!);
        closed();
      });

      it('ignores the scroll its own opening causes', async () => {
        await open();
        fireEvent.scroll(document);
        fireEvent.scroll(card().parentElement!);
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });

      it('closes only a version menu with Escape, then itself with the next one', async () => {
        const { user, toggle, popover } = await open();
        await user.click(within(within(popover).getAllByTestId('variant-member-line')[0]).getByRole('button', { name: /^Actions for / }));
        await screen.findByRole('button', { name: 'File details' });
        await user.keyboard('{Escape}');
        expect(screen.queryByRole('button', { name: 'File details' })).not.toBeInTheDocument();
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        await user.keyboard('{Escape}');
        closed();
        expect(toggle).toHaveFocus();
      });

      it('stays open and follows a resize', async () => {
        const { popover } = await open();
        fireEvent(window, new Event('resize'));
        expect(screen.getByRole('dialog')).toBe(popover);
      });

      it('closes when the card starts a drag', async () => {
        await open();
        const draggable = card().querySelector('[draggable="true"]') as HTMLElement;
        fireEvent.dragStart(draggable, { dataTransfer: { setData: () => {}, effectAllowed: '' } });
        closed();
      });

      it('opens one group at a time', async () => {
        serve([
          ...baseFiles,
          item(41, 'cup_X1C.gcode.3mf', { variant_group_id: 9, variant_count: 2, variant_group_name: 'Cup', variant_position: 0, sliced_for_model: 'X1C' }),
          item(42, 'cup_P1S.gcode.3mf', { variant_group_id: 9, variant_count: 2, variant_group_name: 'Cup', variant_position: 1, sliced_for_model: 'P1S' }),
        ]);
        const user = userEvent.setup();
        render(<FileManagerPage />);
        await screen.findByText('plain.stl');
        const cards = screen.getAllByTestId('variant-group-card');
        expect(cards).toHaveLength(2);
        const vase1 = cards.find((c) => within(c).queryByText('Vase'))!;
        const cup = cards.find((c) => within(c).queryByText('Cup'))!;
        await user.click(within(vase1).getByRole('button', { name: 'Show versions' }));
        expect(await screen.findByRole('dialog', { name: 'Versions of Vase' })).toBeInTheDocument();
        await user.click(within(cup).getByRole('button', { name: 'Show versions' }));
        expect(await screen.findByRole('dialog', { name: 'Versions of Cup' })).toBeInTheDocument();
        expect(screen.getAllByRole('dialog')).toHaveLength(1);
        expect(within(vase1).getByRole('button', { name: 'Show versions' })).toHaveAttribute('aria-expanded', 'false');
      });

      it('disappears with the card when the view changes', async () => {
        const { user } = await open();
        await user.click(screen.getByTitle('List view'));
        await screen.findByTestId('variant-group-list-row');
        closed();
        await user.click(screen.getByTitle('Grid view'));
        await screen.findByTestId('variant-group-card');
        closed();
        expect(within(card()).getByRole('button', { name: 'Show versions' })).toHaveAttribute('aria-expanded', 'false');
      });

      it('runs a version menu inside it without closing it or selecting the group', async () => {
        const { user, popover } = await open();
        const line = within(popover).getAllByTestId('variant-member-line')[0];
        await user.click(within(line).getByRole('button', { name: /^Actions for / }));
        await user.click(await screen.findByRole('button', { name: 'File details' }));
        // The menu item acted, the popover is still there, nothing got selected.
        expect(screen.getByRole('dialog', { name: 'Versions of Vase' })).toBeInTheDocument();
        expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
        expect(within(card()).getByRole('checkbox')).toHaveAttribute('aria-checked', 'false');
      });

      it('keeps the card\'s own selection, print and menu as they were', async () => {
        const { user } = await open();
        await user.click(within(card()).getByText('Vase'));
        expect(await screen.findByText('2 selected')).toBeInTheDocument();
        closed();
      });

      describe('on a phone', () => {
        const width = window.innerWidth;
        beforeEach(() => {
          Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 390 });
        });
        afterEach(() => {
          Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width });
        });

        it('opens a bottom sheet with a heading, a close button and a backdrop', async () => {
          const { user, toggle, popover } = await open();
          expect(popover).toHaveAttribute('data-testid', 'variant-versions-sheet');
          expect(popover.className).toContain('inset-x-0');
          expect(popover.className).toContain('max-h-[70vh]');
          expect(within(popover).getByText('Versions of Vase')).toBeInTheDocument();
          expect(within(popover).getAllByTestId('variant-member-line')).toHaveLength(3);
          expect(screen.getByTestId('variant-versions-backdrop')).toBeInTheDocument();
          expect(card().className).not.toContain('col-span-full');
          await user.click(within(popover).getByRole('button', { name: 'Close' }));
          closed();
          expect(toggle).toHaveFocus();
        });

        it('stays open when the page or the list scrolls, and locks the page behind it', async () => {
          const { user } = await open();
          await new Promise((resolve) => setTimeout(resolve, 200));
          fireEvent.scroll(window);
          fireEvent.scroll(document);
          fireEvent.scroll(card().parentElement!);
          expect(screen.getByRole('dialog')).toBeInTheDocument();
          expect(document.body.style.overflow).toBe('hidden');
          await user.keyboard('{Escape}');
          closed();
          expect(document.body.style.overflow).toBe('');
        });

        it('closes with a click on the backdrop without selecting the card behind it', async () => {
          const { user } = await open();
          await user.click(screen.getByTestId('variant-versions-backdrop'));
          closed();
          expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
        });
      });
    });

    it('leaves an ungrouped file as it was', async () => {
      render(<FileManagerPage />);
      const plain = (await screen.findByText('plain.stl')).closest('[data-file-id]') as HTMLElement;
      expect(plain.getAttribute('data-file-id')).toBe('30');
      expect(within(plain).queryByText(/versions/)).not.toBeInTheDocument();
    });
  });

  describe('list view', () => {
    async function openList() {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');
      await user.click(screen.getByTitle('List view'));
      await screen.findByTestId('variant-group-list-row');
      return user;
    }

    it('folds the group into one row that expands with the keyboard', async () => {
      const user = await openList();
      const row = screen.getByTestId('variant-group-list-row');
      expect(screen.getAllByTestId('variant-group-list-row')).toHaveLength(1);
      expect(within(row).getByText('3 versions')).toBeInTheDocument();

      const toggle = within(row).getByRole('button', { name: /Show versions/ });
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      toggle.focus();
      await user.keyboard('{Enter}');
      expect(toggle).toHaveAttribute('aria-expanded', 'true');
      const panel = screen.getByRole('group', { name: 'Versions' });
      expect(toggle.getAttribute('aria-controls')).toBe(panel.id);
      expect(within(panel).getAllByTestId('variant-member-line')).toHaveLength(3);

      await user.keyboard(' ');
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      expect(screen.queryByRole('group', { name: 'Versions' })).not.toBeInTheDocument();
    });

    it('uses the file rows grid so the columns line up', async () => {
      await openList();
      const fileRow = screen.getAllByTestId('file-list-grid-row')[0];
      const groupRow = screen.getByTestId('variant-group-list-row');
      const gridOf = (el: HTMLElement) => el.className.split(/\s+/).find((c) => c.startsWith('grid-cols-'));
      expect(gridOf(groupRow)).toBe(gridOf(fileRow));
    });

    it('colours the type badge like a plain row when every version has one type', async () => {
      await openList();
      const row = screen.getByTestId('variant-group-list-row');
      const badge = within(row).getByText('GCODE.3MF');
      expect(badge.className).toContain('text-blue-700');
    });

    it('shows the Mixed badge in grey when the versions differ in type', async () => {
      serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1, { file_type: 'gcode' })]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await user.click(screen.getByTitle('List view'));
      const badge = within(await screen.findByTestId('variant-group-list-row')).getByText('Mixed');
      expect(badge.className).toContain('text-bambu-gray');
    });

    it('wraps the open panel and the row in one block, so both are as wide as the row', async () => {
      const user = await openList();
      const row = screen.getByTestId('variant-group-list-row');
      await user.click(within(row).getByRole('button', { name: /Show versions/ }));
      const panel = screen.getByRole('group', { name: 'Versions' });
      expect(panel.parentElement).toBe(row.parentElement);
      expect(row.parentElement!.className).toContain('min-w-min');
    });

    it('selects all versions from the row', async () => {
      const user = await openList();
      await user.click(within(screen.getByTestId('variant-group-list-row')).getByText('Vase'));
      expect(await screen.findByText('2 selected')).toBeInTheDocument();
    });
  });

  describe('columns view', () => {
    it('shows the group as one row whose versions open in a detail pane', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');
      await user.click(screen.getByTitle('Column view'));

      const row = await screen.findByTestId('variant-group-column-row');
      expect(within(row).getByText('3 versions')).toBeInTheDocument();
      expect(screen.queryByTestId('variant-detail-pane')).not.toBeInTheDocument();

      await user.click(within(row).getByRole('button', { name: /Show versions/ }));
      const pane = screen.getByTestId('variant-detail-pane');
      expect(within(pane).getAllByTestId('variant-member-line')).toHaveLength(3);
      expect(within(pane).getByText('in Archive')).toBeInTheDocument();
    });

    it('toggles all versions with Space on the focused row', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');
      await user.click(screen.getByTitle('Column view'));
      const pane = await screen.findByTestId('columns-view');
      pane.focus();
      // Rows in order: plain.stl, solo, Vase (group). ArrowRight focuses the first.
      await user.keyboard('{ArrowRight}{ArrowDown}{ArrowDown}');
      await user.keyboard(' ');
      expect(await screen.findByText('2 selected')).toBeInTheDocument();
    });
  });

  describe('columns view keyboard', () => {
    async function focusGroupRow() {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');
      await user.click(screen.getByTitle('Column view'));
      const pane = await screen.findByTestId('columns-view');
      pane.focus();
      // Rows in order: plain.stl, solo, Vase (group). ArrowRight focuses the first.
      await user.keyboard('{ArrowRight}{ArrowDown}{ArrowDown}');
      return user;
    }

    it('opens and closes the versions of the focused group with the arrow keys and Enter', async () => {
      const user = await focusGroupRow();
      expect(screen.queryByTestId('variant-detail-pane')).not.toBeInTheDocument();
      await user.keyboard('{ArrowRight}');
      expect(await screen.findByTestId('variant-detail-pane')).toBeInTheDocument();
      await user.keyboard('{ArrowLeft}');
      await waitFor(() => expect(screen.queryByTestId('variant-detail-pane')).not.toBeInTheDocument());
      await user.keyboard('{Enter}');
      expect(await screen.findByTestId('variant-detail-pane')).toBeInTheDocument();
      await user.keyboard('{Enter}');
      await waitFor(() => expect(screen.queryByTestId('variant-detail-pane')).not.toBeInTheDocument());
    });

    describe('one group visible in two columns', () => {
      // Version 21 sits in Alpha, version 22 in its subfolder Beta, which is
      // open: Alpha's column and the files pane both list the group row, and
      // its first version (21) is the lead of both.
      const tree = [
        {
          id: 1, name: 'Alpha', parent_id: null, file_count: 1, project_id: null, archive_id: null,
          project_name: null, archive_name: null, latest_activity_at: null,
          children: [
            {
              id: 2, name: 'Beta', parent_id: 1, file_count: 1, project_id: null, archive_id: null,
              project_name: null, archive_name: null, latest_activity_at: null, children: [],
            },
          ],
        },
      ];
      const inAlpha = [
        vase(21, 'X1C', 0, { variant_count: 2, folder_id: 1 }),
        vase(22, 'P1S', 1, { variant_count: 2, folder_id: 2, variant_elsewhere: true, folder_name: 'Beta' }),
      ];
      const inBeta = [
        vase(21, 'X1C', 0, { variant_count: 2, folder_id: 1, variant_elsewhere: true, folder_name: 'Alpha' }),
        vase(22, 'P1S', 1, { variant_count: 2, folder_id: 2 }),
      ];

      async function openBeta() {
        server.use(
          http.get('/api/v1/library/folders', () => HttpResponse.json(tree)),
          http.get('/api/v1/library/files', ({ request }) => {
            const folder = new URL(request.url).searchParams.get('folder_id');
            return HttpResponse.json(folder === '1' ? inAlpha : folder === '2' ? inBeta : []);
          }),
        );
        const user = userEvent.setup();
        render(<FileManagerPage />);
        await user.click(await screen.findByTitle('Column view'));
        const columns = within(await screen.findByTestId('columns-view'));
        await user.click(await columns.findByText('Alpha'));
        await user.click(await columns.findByText('Beta'));
        const level = within(screen.getByTestId('columns-level-folder-1'));
        const pane = within(screen.getByTestId('columns-files-pane'));
        await level.findByTestId('variant-group-column-row');
        await pane.findByTestId('variant-group-column-row');
        screen.getByTestId('columns-view').focus();
        return { user, level, pane };
      }

      it('acts on the focused pane\'s row only', async () => {
        const { user, level, pane } = await openBeta();
        await user.keyboard('{ArrowRight}');
        expect(pane.getByTestId('variant-group-column-row').firstElementChild!.className).toContain('ring-1');
        expect(level.getByTestId('variant-group-column-row').firstElementChild!.className).not.toContain('ring-1');

        await user.keyboard('{ArrowRight}');
        expect(await pane.findByTestId('variant-detail-pane')).toBeInTheDocument();
        expect(level.queryByTestId('variant-detail-pane')).not.toBeInTheDocument();

        await user.keyboard('{ArrowLeft}');
        await waitFor(() => expect(pane.queryByTestId('variant-detail-pane')).not.toBeInTheDocument());
        await user.keyboard('{Enter}');
        expect(await pane.findByTestId('variant-detail-pane')).toBeInTheDocument();
        expect(level.queryByTestId('variant-detail-pane')).not.toBeInTheDocument();
        await user.keyboard('{Enter}');
        await waitFor(() => expect(pane.queryByTestId('variant-detail-pane')).not.toBeInTheDocument());
      });

      it('prints a group ticked in full as one job, never asking about a partial tick', async () => {
        const { user, pane } = await openBeta();
        // The pane's tick selects Beta's version only; that is the whole group
        // as far as this pane is concerned.
        await user.click(pane.getByText('Vase'));
        expect(await screen.findByText('1 selected')).toBeInTheDocument();
        await user.click(screen.getByRole('button', { name: /^Print$/ }));
        expect(screen.queryByText('Print as one job?')).not.toBeInTheDocument();
        // Every visible version, as one job.
        expect(await screen.findByText(/vase_X1C\.gcode\.3mf \+1 more/)).toBeInTheDocument();
      });

      it('acts on the column\'s row when that is the one clicked', async () => {
        const { user, level, pane } = await openBeta();
        await user.click(level.getByText('Vase'));
        screen.getByTestId('columns-view').focus();
        await user.keyboard('{ArrowRight}');
        expect(await level.findByTestId('variant-detail-pane')).toBeInTheDocument();
        expect(pane.queryByTestId('variant-detail-pane')).not.toBeInTheDocument();
        await user.keyboard('{ArrowLeft}');
        await waitFor(() => expect(level.queryByTestId('variant-detail-pane')).not.toBeInTheDocument());
      });
    });

    it('leaves Enter and Space on a version\'s kebab and in its menu to the line', async () => {
      const user = await focusGroupRow();
      await user.keyboard('{ArrowRight}');
      const pane = await screen.findByTestId('variant-detail-pane');
      const line = within(pane).getAllByTestId('variant-member-line')[0];
      const kebab = within(line).getByRole('button', { name: /Actions for/ });
      kebab.focus();
      await user.keyboard('{Enter}');
      const download = await within(line).findByRole('button', { name: 'Download' });
      // Enter on the kebab opened the menu; it did not toggle the group.
      expect(screen.getByTestId('variant-detail-pane')).toBeInTheDocument();
      download.focus();
      await user.keyboard('{Enter}');
      expect(screen.getByTestId('variant-detail-pane')).toBeInTheDocument();
      expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
    });
  });

  describe('filter and sort', () => {
    it('keeps the group while one of its versions matches the search, with all its versions', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');
      await user.type(screen.getByPlaceholderText(/search/i), 'p1s');

      await waitFor(() => expect(screen.queryByText('plain.stl')).not.toBeInTheDocument());
      expect(within(card()).getByText('3 versions')).toBeInTheDocument();
    });

    it('drops the group when no version matches', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');
      await user.type(screen.getByPlaceholderText(/search/i), 'plain');

      await waitFor(() => expect(screen.queryByTestId('variant-group-card')).not.toBeInTheDocument());
      expect(screen.getByText('plain.stl')).toBeInTheDocument();
    });

    it('never matches on a version that is only listed because it sits elsewhere', async () => {
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByText('plain.stl');
      // vase_A1 is the elsewhere version; the other two do not contain "a1".
      await user.type(screen.getByPlaceholderText(/search/i), 'a1');
      await waitFor(() => expect(screen.queryByTestId('variant-group-card')).not.toBeInTheDocument());
    });

    it('sorts the group by its name', async () => {
      serve([
        ...baseFiles.filter((f) => f.id !== 30 && f.id !== 31),
        item(40, 'apple.stl', { file_type: 'stl' }),
        item(41, 'zebra.stl', { file_type: 'stl' }),
      ]);
      render(<FileManagerPage />);
      await screen.findByText('apple.stl');
      const order = Array.from(document.querySelectorAll('[data-file-id]')).map((el) => el.getAttribute('data-file-id'));
      expect(order).toEqual(['40', '21', '41']);
    });
  });

  describe('permissions', () => {
    it('offers Ungroup and Delete to someone who may change every version', async () => {
      mockAuthUser(['library:read_own', 'library:update_own', 'library:delete_own', 'queue:create']);
      serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1)]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await user.click(screen.getByTitle('List view'));
      const row = await screen.findByTestId('variant-group-list-row');
      expect(within(row).getByRole('button', { name: 'Ungroup' })).toBeEnabled();
      expect(within(row).getByRole('button', { name: 'Delete all versions' })).toBeEnabled();
    });

    it('disables Ungroup, Delete, rename and remove without update and delete permission', async () => {
      mockAuthUser(['library:read', 'library:read_own', 'queue:create']);
      serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1)]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await user.click(screen.getByTitle('List view'));
      const row = await screen.findByTestId('variant-group-list-row');

      expect(within(row).getByRole('button', { name: 'Ungroup' })).toBeDisabled();
      expect(within(row).getByRole('button', { name: 'Delete all versions' })).toBeDisabled();

      await user.click(within(row).getByRole('button', { name: /Show versions/ }));
      const line = screen.getAllByTestId('variant-member-line')[0];
      await user.click(within(line).getByRole('button', { name: /Actions for/ }));
      for (const name of ['Rename', 'Remove from group', 'Delete']) {
        expect(await screen.findByRole('button', { name })).toBeDisabled();
      }
      // The reason says what is missing for this action, not for renaming.
      expect(screen.getByRole('button', { name: 'Remove from group' })).toHaveAttribute(
        'title',
        'You need permission to edit this version to remove it from its group',
      );
      // Reading and printing stay available.
      expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled();
      expect(screen.getByRole('button', { name: 'Print this version only' })).toBeEnabled();
    });

    it('disables Ungroup when only some of the versions are the user\'s own', async () => {
      mockAuthUser(['library:read_own', 'library:update_own', 'queue:create']);
      serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1, { created_by_id: 99 })]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await user.click(screen.getByTitle('List view'));
      const row = await screen.findByTestId('variant-group-list-row');
      expect(within(row).getByRole('button', { name: 'Ungroup' })).toBeDisabled();
    });

    it('disables Print without queue:create', async () => {
      mockAuthUser(['library:read_own']);
      serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1)]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await user.click(screen.getByTitle('List view'));
      const row = await screen.findByTestId('variant-group-list-row');
      expect(within(row).getByRole('button', { name: 'Print all versions as one job' })).toBeDisabled();
    });
  });

  describe('group actions', () => {
    const twoVersions = () => serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1)]);

    it('ungroups after a confirm, through the delete-group endpoint', async () => {
      twoVersions();
      const calls: string[] = [];
      server.use(
        http.delete('/api/v1/library/variant-groups/:id', ({ params }) => {
          calls.push(String(params.id));
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await user.click(screen.getByRole('button', { name: 'Actions for version group Vase' }));
      await user.click(await screen.findByRole('button', { name: 'Ungroup' }));

      expect(await screen.findByText('Ungroup versions')).toBeInTheDocument();
      expect(calls).toEqual([]);
      await user.click(screen.getAllByRole('button', { name: 'Ungroup' }).at(-1)!);
      await waitFor(() => expect(calls).toEqual(['7']));
    });

    it('deletes every version after a confirm that counts them', async () => {
      twoVersions();
      let body: { file_ids: number[] } | null = null;
      server.use(
        http.post('/api/v1/library/bulk-delete', async ({ request }) => {
          body = (await request.json()) as { file_ids: number[] };
          return HttpResponse.json({ deleted_files: 2, deleted_folders: 0 });
        }),
      );
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await user.click(screen.getByRole('button', { name: 'Actions for version group Vase' }));
      await user.click(await screen.findByRole('button', { name: 'Delete all versions' }));

      expect(await screen.findByText('Delete 2 files')).toBeInTheDocument();
      expect(screen.getByText(/all 2 versions of "Vase"/)).toBeInTheDocument();
      await user.click(screen.getAllByRole('button', { name: 'Delete' }).at(-1)!);
      await waitFor(() => expect(body).toEqual({ file_ids: [21, 22], folder_ids: [] }));
    });

    it('removes one version from the group', async () => {
      twoVersions();
      const calls: string[] = [];
      server.use(
        http.delete('/api/v1/library/variant-groups/:id/members/:fileId', ({ params }) => {
          calls.push(`${params.id}/${params.fileId}`);
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await user.click(within(card()).getByRole('button', { name: 'Show versions' }));
      const line = screen.getAllByTestId('variant-member-line')[1];
      await user.click(within(line).getByRole('button', { name: /Actions for/ }));
      await user.click(await screen.findByRole('button', { name: 'Remove from group' }));
      await waitFor(() => expect(calls).toEqual(['7/22']));
    });

    it('prints the group as one job with the versions as alternatives', async () => {
      twoVersions();
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await user.click(screen.getByRole('button', { name: 'Actions for version group Vase' }));
      await user.click(await screen.findByRole('button', { name: 'Print all versions as one job' }));

      expect(await screen.findByText(/vase_X1C\.gcode\.3mf \+1 more/)).toBeInTheDocument();
      expect(screen.queryByRole('radiogroup', { name: 'What the selected files become' })).not.toBeInTheDocument();
    });

    it('prints a single version without offering the rest', async () => {
      twoVersions();
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await user.click(within(card()).getByRole('button', { name: 'Show versions' }));
      const line = screen.getAllByTestId('variant-member-line')[1];
      await user.click(within(line).getByRole('button', { name: /Actions for/ }));
      await user.click(await screen.findByRole('button', { name: 'Print this version only' }));

      expect((await screen.findAllByText('vase_P1S.gcode.3mf')).length).toBeGreaterThan(1);
      expect(screen.queryByText(/\+1 more/)).not.toBeInTheDocument();
    });
  });

  describe('printing a group', () => {
    const twoVersions = () => serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1)]);
    const group = (targets: Record<number, string>) => ({
      id: 7,
      name: 'Vase',
      members: [21, 22].map((id, i) => ({
        library_file_id: id,
        filename: `vase_${id}.gcode.3mf`,
        target_model: targets[id],
        position: i,
      })),
    });
    const printGroup = async (user: ReturnType<typeof userEvent.setup>) => {
      await user.click(screen.getByRole('button', { name: 'Actions for version group Vase' }));
      await user.click(await screen.findByRole('button', { name: 'Print all versions as one job' }));
    };

    it('uses the model the server dispatches to, not the one the list declares', async () => {
      twoVersions();
      server.use(http.get('/api/v1/library/variant-groups/7', () => HttpResponse.json(group({ 21: 'H2D', 22: 'P1S' }))));
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await printGroup(user);
      expect(await screen.findByText('H2D')).toBeInTheDocument();
      expect(screen.queryByText('X1C')).not.toBeInTheDocument();
    });

    it('falls back to the declared models when the group cannot be fetched', async () => {
      twoVersions();
      server.use(http.get('/api/v1/library/variant-groups/7', () => HttpResponse.json({ detail: 'no' }, { status: 500 })));
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await printGroup(user);
      expect(await screen.findByText('X1C')).toBeInTheDocument();
      expect(screen.getByText('P1S')).toBeInTheDocument();
    });

    it('keeps a version print started while the group is still loading', async () => {
      twoVersions();
      server.use(
        http.get('/api/v1/library/variant-groups/7', async () => {
          await new Promise((resolve) => setTimeout(resolve, 1500));
          return HttpResponse.json(group({ 21: 'H2D', 22: 'P1S' }));
        }),
      );
      const user = userEvent.setup();
      render(<FileManagerPage />);
      await screen.findByTestId('variant-group-card');
      await printGroup(user);
      await user.click(within(card()).getByRole('button', { name: 'Show versions' }));
      const line = screen.getAllByTestId('variant-member-line')[1];
      await user.click(within(line).getByRole('button', { name: /Actions for/ }));
      await user.click(await screen.findByRole('button', { name: 'Print this version only' }));
      expect((await screen.findAllByText('vase_P1S.gcode.3mf')).length).toBeGreaterThan(1);

      // The group's answer arrives late and must not replace the version dialog.
      await new Promise((resolve) => setTimeout(resolve, 1800));
      expect(screen.queryByText(/\+1 more/)).not.toBeInTheDocument();
      expect(screen.queryByText('H2D')).not.toBeInTheDocument();
    });
  });

  describe('edge cases', () => {
    it('copes with 40 versions and a very long group name', async () => {
      const long = 'Z'.repeat(200);
      serve(
        Array.from({ length: 40 }, (_, i) =>
          vase(100 + i, `M${i}`, i, { variant_count: 40, variant_group_name: long }),
        ),
      );
      const user = userEvent.setup();
      render(<FileManagerPage />);
      const c = await screen.findByTestId('variant-group-card');
      expect(within(c).getByText('40 versions')).toBeInTheDocument();
      // The title truncates instead of widening the card.
      expect(within(c).getByText(long).className).toContain('truncate');
      await user.click(within(c).getByRole('button', { name: 'Show versions' }));
      expect(screen.getAllByTestId('variant-member-line')).toHaveLength(40);
    });

    it('lists a version in an external folder like any other', async () => {
      serve([
        vase(21, 'X1C', 0),
        vase(22, 'P1S', 1, { is_external: true, variant_elsewhere: true, folder_id: 5, folder_name: 'NAS (read-only)' }),
      ]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      const c = await screen.findByTestId('variant-group-card');
      await user.click(within(c).getByRole('button', { name: 'Show versions' }));
      expect(screen.getByText('in NAS (read-only)')).toBeInTheDocument();
    });

    it('offers Preview in a version\'s menu', async () => {
      serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1)]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      const c = await screen.findByTestId('variant-group-card');
      await user.click(within(c).getByRole('button', { name: 'Show versions' }));
      await user.click(within(screen.getAllByTestId('variant-member-line')[0]).getByRole('button', { name: /Actions for/ }));
      expect(await screen.findByRole('button', { name: '3D Preview' })).toBeEnabled();
    });

    it('disables Preview in a version\'s menu without read permission', async () => {
      mockAuthUser(['library:read_own']);
      serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1)]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      const c = await screen.findByTestId('variant-group-card');
      await user.click(within(c).getByRole('button', { name: 'Show versions' }));
      await user.click(within(screen.getAllByTestId('variant-member-line')[0]).getByRole('button', { name: /Actions for/ }));
      expect(await screen.findByRole('button', { name: '3D Preview' })).toBeDisabled();
    });

    it('does not call a version in a named folder "in the library root", whatever the filter', async () => {
      serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1, { variant_elsewhere: true, folder_id: 4, folder_name: null })]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      const c = await screen.findByTestId('variant-group-card');
      await user.click(within(c).getByRole('button', { name: 'Show versions' }));
      expect(screen.getByText('in another folder')).toBeInTheDocument();
      expect(screen.queryByText('in the library root')).not.toBeInTheDocument();
    });

    it('says "in the library root" for an elsewhere version that is in no folder', async () => {
      serve([vase(21, 'X1C', 0, { folder_id: 3 }), vase(22, 'P1S', 1, { variant_elsewhere: true })]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      const c = await screen.findByTestId('variant-group-card');
      await user.click(within(c).getByRole('button', { name: 'Show versions' }));
      expect(screen.getByText('in the library root')).toBeInTheDocument();
    });

    it('does not open a preview when a version line is double-clicked', async () => {
      serve([vase(21, 'X1C', 0), vase(22, 'P1S', 1)]);
      const user = userEvent.setup();
      render(<FileManagerPage />);
      const c = await screen.findByTestId('variant-group-card');
      await user.click(within(c).getByRole('button', { name: 'Show versions' }));
      fireEvent.doubleClick(screen.getAllByTestId('variant-member-line')[0]);
      expect(screen.queryByTestId('model-viewer-modal')).not.toBeInTheDocument();
    });
  });
});

describe('VariantSelectionBox keyboard', () => {
  it('is a button that Space and Enter toggle, without reaching the row behind it', async () => {
    const onToggle = vi.fn();
    const onRow = vi.fn();
    const user = userEvent.setup();
    render(
      <div onClick={onRow}>
        <VariantSelectionBox selectedCount={0} total={2} label="Pick" onToggle={onToggle} />
      </div>,
    );
    const box = screen.getByRole('checkbox', { name: 'Pick' });
    expect(box.tagName).toBe('BUTTON');
    box.focus();
    await user.keyboard(' ');
    await user.keyboard('{Enter}');
    await user.click(box);
    expect(onToggle).toHaveBeenCalledTimes(3);
    expect(onRow).not.toHaveBeenCalled();
  });
});

describe('VariantSelectionBox', () => {
  it.each([
    [0, false],
    [1, 'mixed'],
    [3, true],
  ] as const)('reports %i of 3 selected as aria-checked=%s', (count, expected) => {
    render(<VariantSelectionBox selectedCount={count} total={3} label="Pick" onToggle={() => {}} />);
    const box = screen.getByRole('checkbox', { name: 'Pick' });
    expect(box.getAttribute('aria-checked')).toBe(String(expected));
  });
});
