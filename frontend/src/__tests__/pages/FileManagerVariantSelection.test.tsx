/**
 * Selecting, printing, deleting and dragging a version group (#3628, #3462).
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { setAuthToken } from '../../api/client';
import type { LibraryFileListItem } from '../../api/client';
import { planBulkPrint, promotePartialGroups, draggedMemberIds, selectableMembers, groupSelection } from '../../pages/fileManager/variantSelection';
import { groupLibraryRows, type GroupRow } from '../../pages/fileManager/variantGroups';
import { isValidFilesMove } from '../../utils/libraryFolders';

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

const member = (id: number, model: string, position: number, over: Item = {}) =>
  item(id, `vase_${model}.gcode.3mf`, {
    variant_group_id: 7,
    variant_count: 3,
    variant_group_name: 'Vase',
    variant_position: position,
    sliced_for_model: model,
    ...over,
  });

const folders = [
  { id: 1, name: 'Vases', parent_id: null, file_count: 0, project_id: null, archive_id: null, project_name: null, archive_name: null, latest_activity_at: null, children: [] },
];

function serve(files: Item[]) {
  server.use(
    http.get('/api/v1/library/folders', () => HttpResponse.json(folders)),
    http.get('/api/v1/library/files', () => HttpResponse.json(files)),
    http.get('/api/v1/library/stats', () =>
      HttpResponse.json({ total_files: 5, total_folders: 1, total_size_bytes: 1, disk_free_bytes: 1e12, disk_total_bytes: 1e13 }),
    ),
    http.get('/api/v1/settings/', () => HttpResponse.json({ check_updates: false, library_disk_warning_gb: 5 })),
    http.get('/api/v1/projects/', () => HttpResponse.json([])),
    http.get('/api/v1/archives/', () => HttpResponse.json([])),
    http.get('/api/v1/library/files/:id/plates', ({ params }) =>
      HttpResponse.json({ file_id: Number(params.id), filename: 'x', plates: [], is_multi_plate: false }),
    ),
  );
}

const toolbarPrint = (name: RegExp | string) => screen.findByRole('button', { name });

describe('selecting a version group', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => setAuthToken(null));

  it('prints a fully selected group as one job: the button says Print, not N files', async () => {
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1), item(30, 'plain.stl', { file_type: 'stl' })]);
    const user = userEvent.setup();
    render(<FileManagerPage />);
    const card = await screen.findByTestId('variant-group-card');
    await user.click(within(card).getByText('Vase'));
    expect(await screen.findByText('2 selected')).toBeInTheDocument();

    await user.click(await toolbarPrint(/^Print$/));
    expect(await screen.findByText(/vase_X1C\.gcode\.3mf \+1 more/)).toBeInTheDocument();
    // Alternatives only, not the choice between alternatives and separate jobs.
    expect(screen.queryByRole('radiogroup', { name: 'What the selected files become' })).not.toBeInTheDocument();
  });

  it('counts jobs when a group and another file are selected, and opens one dialog after the other', async () => {
    const posted: Array<Record<string, unknown>> = [];
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1), item(30, 'plain.gcode.3mf', { sliced_for_model: 'A1' })]);
    server.use(
      http.post('/api/v1/queue/', async ({ request }) => {
        posted.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ id: posted.length, status: 'pending' });
      }),
    );
    const user = userEvent.setup();
    render(<FileManagerPage />);
    const card = await screen.findByTestId('variant-group-card');
    await user.click(within(card).getByText('Vase'));
    await user.click(screen.getByText('plain.gcode.3mf').closest('[data-file-id]') as HTMLElement);
    expect(await screen.findByText('3 selected')).toBeInTheDocument();

    await user.click(await toolbarPrint('Print (1 group + 1 file)'));
    // The group first.
    expect(await screen.findByText(/vase_X1C\.gcode\.3mf \+1 more/)).toBeInTheDocument();
    const dialogPrint = () => screen.getAllByRole('button', { name: /^Print$/ }).at(-1) as HTMLElement;
    await waitFor(() => expect(dialogPrint()).toBeEnabled());
    await user.click(dialogPrint());

    await waitFor(() => expect(posted).toHaveLength(1));
    // Then the other file, with the group's versions no longer selected.
    await waitFor(() => expect(screen.getByText('1 selected')).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText(/\+1 more/)).not.toBeInTheDocument());
    expect((await screen.findAllByText('plain.gcode.3mf')).length).toBeGreaterThan(1);
  });

  it('asks before printing a group of which only some versions are selected', async () => {
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1)]);
    const user = userEvent.setup();
    render(<FileManagerPage />);
    const card = await screen.findByTestId('variant-group-card');
    await user.click(within(card).getByText('Vase'));
    expect(await screen.findByText('2 selected')).toBeInTheDocument();

    // A third version turns up while the two are selected: the group is now
    // only partly selected.
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1), member(23, 'A1', 2)]);
    window.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(within(screen.getByTestId('variant-group-card')).getByText('3 versions')).toBeInTheDocument());
    expect(within(screen.getByTestId('variant-group-card')).getByRole('checkbox')).toHaveAttribute('aria-checked', 'mixed');

    await user.click(await toolbarPrint('Print (2 files)'));
    expect(await screen.findByText('Print as one job?')).toBeInTheDocument();
    expect(screen.getByText(/2 of 3 versions of "Vase"/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Print as one job' }));
    expect(await screen.findByText(/vase_X1C\.gcode\.3mf \+2 more/)).toBeInTheDocument();
  });

  it('prints just the selected versions when asked to', async () => {
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1)]);
    const user = userEvent.setup();
    render(<FileManagerPage />);
    const card = await screen.findByTestId('variant-group-card');
    await user.click(within(card).getByText('Vase'));
    await screen.findByText('2 selected');
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1), member(23, 'A1', 2)]);
    window.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(within(screen.getByTestId('variant-group-card')).getByText('3 versions')).toBeInTheDocument());

    server.use(
      http.get('/api/v1/library/variant-groups/7', () =>
        HttpResponse.json({
          id: 7,
          name: 'Vase',
          members: [21, 22, 23].map((id, i) => ({ library_file_id: id, filename: `v${id}.gcode.3mf`, target_model: 'X1C', position: i })),
        }),
      ),
    );
    await user.click(await toolbarPrint('Print (2 files)'));
    await user.click(await screen.findByRole('button', { name: 'Print selection only' }));
    expect(await screen.findByText(/vase_X1C\.gcode\.3mf \+1 more/)).toBeInTheDocument();
  });
});

describe('print plan', () => {
  const group = (ids: number[], groupId = 7): GroupRow => {
    const files = ids.map((id, i) => item(id, `f${id}.gcode.3mf`, { variant_group_id: groupId, variant_position: i })) as unknown as LibraryFileListItem[];
    const [row] = groupLibraryRows(files, { query: '', filterType: 'all', filterUsername: '' }, { sortField: 'name', sortDirection: 'asc' });
    if (row.kind !== 'group') throw new Error('group expected');
    return row;
  };
  const byId = (rows: GroupRow[], extra: Item[] = []) => {
    const m = new Map<number, LibraryFileListItem>();
    for (const r of rows) for (const f of r.members) m.set(f.id, f);
    for (const f of extra) m.set(f.id as number, f as unknown as LibraryFileListItem);
    return m;
  };

  it('makes one job of a fully selected group and leaves other files loose', () => {
    const g = group([1, 2, 3]);
    const plan = planBulkPrint([1, 2, 3, 9], [g], byId([g], [item(9, 'x.gcode.3mf')]));
    expect(plan.fullGroups).toEqual([g]);
    expect(plan.partialGroups).toEqual([]);
    expect(plan.looseFiles.map((f) => f.id)).toEqual([9]);
    expect(plan.fullGroups).toHaveLength(1);
    expect(plan.looseFiles).toHaveLength(1);
  });

  it('reports a partly selected group and can promote it', () => {
    const g = group([1, 2, 3]);
    const plan = planBulkPrint([1, 2], [g], byId([g]));
    expect(plan.fullGroups).toEqual([]);
    expect(plan.partialGroups).toEqual([{ row: g, selected: 2 }]);
    expect(plan.looseFiles.map((f) => f.id)).toEqual([1, 2]);
    const promoted = promotePartialGroups(plan);
    expect(promoted.fullGroups).toEqual([g]);
    expect(promoted.looseFiles).toEqual([]);
    expect(promoted.fullGroups).toHaveLength(1);
  });

  it('makes one job of each fully selected group', () => {
    const a = group([1, 2], 7);
    const b = group([3, 4], 8);
    const plan = planBulkPrint([1, 2, 3, 4], [a, b, a], byId([a, b]));
    expect(plan.fullGroups.map((g) => g.id)).toEqual([7, 8]);
    expect(plan.looseFiles).toEqual([]);
  });

  it('counts a group as fully selected when its versions in this folder are', () => {
    const files = [
      item(1, 'a.gcode.3mf', { variant_group_id: 7, variant_position: 0 }),
      item(2, 'b.gcode.3mf', { variant_group_id: 7, variant_position: 1 }),
      item(3, 'c.gcode.3mf', { variant_group_id: 7, variant_position: 2, variant_elsewhere: true, folder_id: 9 }),
    ] as unknown as LibraryFileListItem[];
    const [g] = groupLibraryRows(files, { query: '', filterType: 'all', filterUsername: '' }, { sortField: 'name', sortDirection: 'asc' });
    if (g.kind !== 'group') throw new Error('group expected');
    expect(selectableMembers(g).map((m) => m.id)).toEqual([1, 2]);
    expect(groupSelection(g, [1])).toEqual({ count: 1, total: 2 });
    const plan = planBulkPrint([1, 2], [g], new Map(files.map((f) => [f.id, f])));
    expect(plan.fullGroups).toEqual([g]);
    expect(plan.partialGroups).toEqual([]);
  });

  it('ignores groups with nothing selected', () => {
    const g = group([1, 2]);
    expect(planBulkPrint([], [g], byId([g]))).toEqual({ fullGroups: [], partialGroups: [], looseFiles: [] });
  });
  it('judges a group listed in two columns by the pane that ticks it, never as partial', () => {
    // Alpha's column lists 21 (here) and 22 (elsewhere); Beta's pane lists 22
    // (here) and 21 (elsewhere). The merged file map has both as "here".
    const inAlpha = group([21, 22]);
    inAlpha.members[1] = { ...inAlpha.members[1], variant_elsewhere: true };
    const inBeta = group([21, 22]);
    inBeta.members[0] = { ...inBeta.members[0], variant_elsewhere: true };
    const files = byId([group([21, 22])]);
    for (const ids of [[21], [22], [21, 22]]) {
      const plan = planBulkPrint(ids, [inAlpha, inBeta], files);
      expect(plan.partialGroups).toEqual([]);
      expect(plan.fullGroups).toHaveLength(1);
      expect(plan.looseFiles).toEqual([]);
    }
    // The job still carries every visible version of the group.
    expect(planBulkPrint([22], [inAlpha, inBeta], files).fullGroups[0].members.map((m) => m.id)).toEqual([21, 22]);
  });
});

describe('dragging a group onto a folder', () => {
  const rowOf = (files: Item[]): GroupRow => {
    const [row] = groupLibraryRows(files as unknown as LibraryFileListItem[], { query: '', filterType: 'all', filterUsername: '' }, { sortField: 'name', sortDirection: 'asc' });
    if (row.kind !== 'group') throw new Error('group expected');
    return row;
  };

  it('moves only the versions in the current folder, outside external folders, that the user may change', () => {
    const row = rowOf([
      member(1, 'X1C', 0),
      member(2, 'P1S', 1, { variant_elsewhere: true, folder_id: 9 }),
      member(3, 'A1', 2, { is_external: true }),
      member(4, 'H2D', 3, { created_by_id: 99 }),
    ]);
    expect(draggedMemberIds(row, (f) => f.created_by_id === 7)).toEqual([1]);
    expect(draggedMemberIds(row, () => false)).toEqual([]);
  });

  const tree = [
    { id: 1, name: 'A', parent_id: null, is_external: false, can_write: true, children: [] },
    { id: 2, name: 'RO', parent_id: null, is_external: false, can_write: false, children: [] },
    { id: 3, name: 'Ext', parent_id: null, is_external: true, children: [] },
  ] as never;

  it('accepts a real move and refuses the rest', () => {
    expect(isValidFilesMove(tree, [{ folder_id: null }], 1)).toBe(true);
    expect(isValidFilesMove(tree, [{ folder_id: 1 }], 1)).toBe(false);
    expect(isValidFilesMove(tree, [{ folder_id: 1 }, { folder_id: null }], 1)).toBe(true);
    expect(isValidFilesMove(tree, [{ folder_id: 1 }], null)).toBe(true);
    expect(isValidFilesMove(tree, [{ folder_id: null }], null)).toBe(false);
    expect(isValidFilesMove(tree, [{ folder_id: null }], 2)).toBe(false);
    expect(isValidFilesMove(tree, [{ folder_id: null }], 3)).toBe(false);
    expect(isValidFilesMove(tree, [], 1)).toBe(false);
  });

  it('moves the dragged versions through the move endpoint when dropped on a folder', async () => {
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1, { variant_elsewhere: true, folder_id: 9 }), member(23, 'A1', 2)]);
    let body: { file_ids: number[]; folder_id: number | null } | null = null;
    server.use(
      http.post('/api/v1/library/files/move', async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json({ status: 'ok', moved: 2 });
      }),
    );
    render(<FileManagerPage />);
    const card = (await screen.findByTestId('variant-group-card')).querySelector('[draggable="true"]') as HTMLElement;
    expect(card).not.toBeNull();
    const target = (await screen.findAllByText('Vases'))[0].closest('[class*="group"], div') as HTMLElement;

    const store: Record<string, string> = {};
    const dataTransfer = {
      setData: (k: string, v: string) => { store[k] = v; },
      get types() { return Object.keys(store); },
      effectAllowed: '',
      dropEffect: '',
    };
    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.dragEnter(target, { dataTransfer });
    fireEvent.dragOver(target, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });

    await waitFor(() => expect(body).toEqual({ file_ids: [21, 23], folder_id: 1 }));
  });
});

describe('deleting a selected group', () => {
  beforeEach(() => localStorage.clear());

  it('counts the files, not the group', async () => {
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1), member(23, 'A1', 2)]);
    const user = userEvent.setup();
    render(<FileManagerPage />);
    const card = await screen.findByTestId('variant-group-card');
    await user.click(within(card).getByText('Vase'));
    await screen.findByText('3 selected');
    const toolbar = screen.getByText('3 selected').closest('div')!.parentElement as HTMLElement;
    await user.click(within(toolbar).getByRole('button', { name: /^Delete$/ }));
    expect(await screen.findByText('Delete 3 Files')).toBeInTheDocument();
  });
});


// A page with a group of two (21, 22), a group of two more (41, 42), a version
// of the first group in another folder, and a plain file.
const twoGroups = (extraForSecond: Item[] = []) => [
  member(21, 'X1C', 0),
  member(22, 'P1S', 1),
  member(23, 'A1', 2, { variant_elsewhere: true, folder_id: 9, folder_name: 'Archive' }),
  ...[41, 42].map((id, i) =>
    item(id, `cup_${id}.gcode.3mf`, {
      variant_group_id: 8,
      variant_count: 2,
      variant_group_name: 'Cup',
      variant_position: i,
      sliced_for_model: i ? 'P1S' : 'X1C',
    }),
  ),
  ...extraForSecond,
  item(30, 'plain.stl', { file_type: 'stl' }),
];
const cupMember = (id: number, position: number) =>
  item(id, `cup_${id}.gcode.3mf`, {
    variant_group_id: 8,
    variant_count: 3,
    variant_group_name: 'Cup',
    variant_position: position,
    sliced_for_model: 'A1',
  });
const cards = () => screen.getAllByTestId('variant-group-card');
// Cards are sorted by name: Cup before Vase.
const cardNamed = (name: string) => cards().find((c) => within(c).queryByText(name)) as HTMLElement;
const toolbarOf = () => screen.getByText(/ selected$/).closest('div')!.parentElement as HTMLElement;

describe('selection stays in this folder', () => {
  beforeEach(() => localStorage.clear());

  it('the card tick leaves a version of another folder out, and Delete never sends it', async () => {
    serve(twoGroups());
    let body: { file_ids: number[] } | null = null;
    server.use(
      http.post('/api/v1/library/bulk-delete', async ({ request }) => {
        body = (await request.json()) as { file_ids: number[] };
        return HttpResponse.json({ deleted_files: 2, deleted_folders: 0 });
      }),
    );
    const user = userEvent.setup();
    render(<FileManagerPage />);
    await screen.findAllByTestId('variant-group-card');
    await user.click(within(cardNamed('Vase')).getByText('Vase'));
    expect(await screen.findByText('2 selected')).toBeInTheDocument();
    expect(within(cardNamed('Vase')).getByRole('checkbox')).toHaveAttribute('aria-checked', 'true');

    await user.click(within(toolbarOf()).getByRole('button', { name: /^Delete$/ }));
    expect(await screen.findByText('Delete 2 Files')).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: 'Delete' }).at(-1)!);
    await waitFor(() => expect(body).toEqual({ file_ids: [21, 22], folder_ids: [] }));
  });

  it('Move sends only the versions in this folder, like a drag does', async () => {
    serve(twoGroups());
    let body: { file_ids: number[]; folder_id: number | null } | null = null;
    server.use(
      http.post('/api/v1/library/files/move', async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json({ status: 'ok', moved: 2 });
      }),
    );
    const user = userEvent.setup();
    render(<FileManagerPage />);
    await screen.findAllByTestId('variant-group-card');
    await user.click(within(cardNamed('Vase')).getByText('Vase'));
    await screen.findByText('2 selected');
    await user.click(within(toolbarOf()).getByRole('button', { name: /^Move$/ }));
    const modal = (await screen.findByText('Move 2 File(s)')).closest('div')!.parentElement as HTMLElement;
    await user.click(within(modal).getByRole('button', { name: 'Vases' }));
    await user.click(within(modal).getByRole('button', { name: /^Move$/ }));
    await waitFor(() => expect(body).toEqual({ file_ids: [21, 22], folder_id: 1 }));
  });

  it('Select all ticks this folder\'s files only, never a version listed from elsewhere', async () => {
    serve(twoGroups());
    const user = userEvent.setup();
    render(<FileManagerPage />);
    await screen.findAllByTestId('variant-group-card');
    await user.click(screen.getByRole('button', { name: /Select all/i }));
    // 21, 22, 41, 42 and the plain file; not 23.
    expect(await screen.findByText('5 selected')).toBeInTheDocument();
    expect(within(cardNamed('Vase')).getByRole('checkbox')).toHaveAttribute('aria-checked', 'true');
  });

  it('Select all leaves out versions a search hides', async () => {
    serve(twoGroups());
    const user = userEvent.setup();
    render(<FileManagerPage />);
    await screen.findAllByTestId('variant-group-card');
    await user.type(screen.getByPlaceholderText(/search/i), 'x1c');
    await waitFor(() => expect(screen.queryByText('plain.stl')).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /Select all/i }));
    // vase_X1C and cup_41 (its model is X1C but its name has no "x1c": not it).
    expect(await screen.findByText('1 selected')).toBeInTheDocument();
    expect(within(screen.getByTestId('variant-group-card')).getByRole('checkbox')).toHaveAttribute('aria-checked', 'mixed');
  });

  it('drops a tick from a version that turns out to be elsewhere', async () => {
    serve(twoGroups());
    const user = userEvent.setup();
    render(<FileManagerPage />);
    await screen.findAllByTestId('variant-group-card');
    await user.click(within(cardNamed('Vase')).getByText('Vase'));
    await screen.findByText('2 selected');
    // A filter then leaves 22 out of the listing: it is only listed as elsewhere.
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1, { variant_elsewhere: true, folder_id: 1, folder_name: 'Vases' })]);
    window.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(screen.getByText('1 selected')).toBeInTheDocument());
  });
});

describe('printing a selection with groups', () => {
  beforeEach(() => localStorage.clear());

  const selectBothGroups = async (user: ReturnType<typeof userEvent.setup>) => {
    await screen.findAllByTestId('variant-group-card');
    await user.click(within(cardNamed('Vase')).getByText('Vase'));
    await user.click(within(cardNamed('Cup')).getByText('Cup'));
    await screen.findByText('4 selected');
  };

  it('posts one queue item with every version as an alternative', async () => {
    const posted: Array<Record<string, unknown>> = [];
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1)]);
    server.use(
      http.get('/api/v1/library/variant-groups/7', () =>
        HttpResponse.json({
          id: 7,
          name: 'Vase',
          members: [
            { library_file_id: 21, filename: 'vase_X1C.gcode.3mf', target_model: 'X1C', position: 0 },
            { library_file_id: 22, filename: 'vase_P1S.gcode.3mf', target_model: 'P1S', position: 1 },
          ],
        }),
      ),
      http.post('/api/v1/queue/', async ({ request }) => {
        posted.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ id: 1, status: 'pending' });
      }),
    );
    const user = userEvent.setup();
    render(<FileManagerPage />);
    const card = await screen.findByTestId('variant-group-card');
    await user.click(within(card).getByText('Vase'));
    await user.click(await toolbarPrint(/^Print$/));
    await screen.findByText(/vase_X1C\.gcode\.3mf \+1 more/);
    const dialogPrint = () => screen.getAllByRole('button', { name: /^Print$/ }).at(-1) as HTMLElement;
    await waitFor(() => expect(dialogPrint()).toBeEnabled());
    await user.click(dialogPrint());
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].variants).toEqual([
      { library_file_id: 21, plate_id: null },
      { library_file_id: 22, plate_id: null },
    ]);
    expect(posted[0].library_file_id).toBeUndefined();
  });

  it('keeps a full group one job when only the selection is printed', async () => {
    serve(twoGroups());
    const user = userEvent.setup();
    render(<FileManagerPage />);
    await selectBothGroups(user);
    // The second group gains a third version: partly selected now.
    serve(twoGroups([cupMember(43, 2)]).map((f) => (f.id === 41 || f.id === 42 ? { ...f, variant_count: 3 } : f)));
    window.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(within(cardNamed('Cup')).getByText('3 versions')).toBeInTheDocument());

    await user.click(await toolbarPrint('Print (1 group + 2 files)'));
    await user.click(await screen.findByRole('button', { name: 'Print selection only' }));
    // The Vase group is one job (two files), not four files in one dialog.
    expect(await screen.findByText(/vase_X1C\.gcode\.3mf \+2 more/)).toBeInTheDocument();
    expect(screen.queryByText(/\+3 more/)).not.toBeInTheDocument();
  });

  it('prints a lone selected version on its own, without the rest of its group', async () => {
    serve([member(21, 'X1C', 0), member(22, 'P1S', 1)]);
    const user = userEvent.setup();
    render(<FileManagerPage />);
    const card = await screen.findByTestId('variant-group-card');
    await user.click(within(card).getByText('Vase'));
    await screen.findByText('2 selected');
    // 22 vanishes from the listing and 23 turns up: only 21 of the two is selected.
    serve([member(21, 'X1C', 0), member(23, 'A1', 1)]);
    server.use(
      http.get('/api/v1/library/variant-groups/7', () =>
        HttpResponse.json({
          id: 7,
          name: 'Vase',
          members: [21, 23].map((id, i) => ({ library_file_id: id, filename: `v${id}.gcode.3mf`, target_model: 'X1C', position: i })),
        }),
      ),
    );
    window.dispatchEvent(new Event('visibilitychange'));
    await waitFor(() => expect(within(screen.getByTestId('variant-group-card')).getByRole('checkbox')).toHaveAttribute('aria-checked', 'mixed'));
    await user.click(await toolbarPrint('Print'));
    await user.click(await screen.findByRole('button', { name: 'Print selection only' }));
    expect(await screen.findByText('vase_X1C.gcode.3mf')).toBeInTheDocument();
    expect(screen.queryByText(/\+1 more/)).not.toBeInTheDocument();
  });

  it('stops the run when the first dialog is cancelled: nothing queued, no second dialog', async () => {
    const posted: unknown[] = [];
    serve(twoGroups());
    server.use(
      http.post('/api/v1/queue/', async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json({ id: 1, status: 'pending' });
      }),
    );
    const user = userEvent.setup();
    render(<FileManagerPage />);
    await selectBothGroups(user);
    await user.click(await toolbarPrint('Print (2 jobs)'));
    expect(await screen.findByText(/vase_X1C\.gcode\.3mf \+2 more/)).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: 'Cancel' }).at(-1)!);

    await waitFor(() => expect(screen.queryByText(/\+2 more/)).not.toBeInTheDocument());
    // The run is over: the second group does not open on its own, and the
    // selection is as it was.
    await new Promise((r) => setTimeout(r, 200));
    expect(screen.queryByText(/cup_41\.gcode\.3mf \+1 more/)).not.toBeInTheDocument();
    expect(posted).toEqual([]);
    expect(screen.getByText('4 selected')).toBeInTheDocument();
  });

  it('opens the next group after the first is queued, with a full and a partial group in one selection', async () => {
    const posted: unknown[] = [];
    serve(twoGroups());
    server.use(
      http.post('/api/v1/queue/', async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json({ id: posted.length, status: 'pending' });
      }),
    );
    const user = userEvent.setup();
    render(<FileManagerPage />);
    await selectBothGroups(user);
    await user.click(await toolbarPrint('Print (2 jobs)'));
    await screen.findByText(/vase_X1C\.gcode\.3mf \+2 more/);
    const dialogPrint = () => screen.getAllByRole('button', { name: /^Print$/ }).at(-1) as HTMLElement;
    await waitFor(() => expect(dialogPrint()).toBeEnabled());
    await user.click(dialogPrint());
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(await screen.findByText(/cup_41\.gcode\.3mf \+1 more/)).toBeInTheDocument();
  });
});
