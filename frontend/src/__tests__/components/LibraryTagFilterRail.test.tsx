/**
 * The File Manager's tag filter with a large catalog (#3411).
 *
 * 160 tags wrapped to a dozen rows of chips and left the file list a strip at
 * the bottom of the page. jsdom does no layout, so "the two rows overflow" is
 * stubbed through scrollHeight/clientHeight on the chip list.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { LibraryTag } from '../../api/client';
import { LibraryTagFilterRail, TAG_SEARCH_MIN_TAGS } from '../../components/LibraryTagFilterRail';

const tag = (id: number, name: string): LibraryTag => ({
  id,
  name,
  file_count: 1,
  created_at: '2026-01-01',
  updated_at: '2026-01-01',
});

const catalog = (n: number) => Array.from({ length: n }, (_, i) => tag(i + 1, `tag-${String(i + 1).padStart(3, '0')}`));

let overflow = false;

beforeEach(() => {
  overflow = false;
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(function (this: HTMLElement) {
    return this.dataset.testid === 'tag-filter-list' && overflow ? 200 : 40;
  });
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(() => 40);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderRail(tags: LibraryTag[], selectedIds: number[] = []) {
  const onToggle = vi.fn();
  const onClear = vi.fn();
  const view = render(
    <LibraryTagFilterRail tags={tags} selectedIds={selectedIds} onToggle={onToggle} onClear={onClear} />,
  );
  const rerender = (next: LibraryTag[]) =>
    view.rerender(<LibraryTagFilterRail tags={next} selectedIds={selectedIds} onToggle={onToggle} onClear={onClear} />);
  return { onToggle, onClear, rerender };
}

describe('LibraryTagFilterRail', () => {
  it('a small catalog shows every tag, with no toggle and no search', () => {
    renderRail(catalog(5));

    expect(screen.getAllByRole('button', { pressed: false })).toHaveLength(5);
    expect(screen.queryByRole('button', { name: /Show all/ })).toBeNull();
    expect(screen.queryByRole('searchbox')).toBeNull();
  });

  it('a large catalog is clamped to two rows until shown in full', async () => {
    overflow = true;
    renderRail(catalog(160));

    const list = screen.getByTestId('tag-filter-list');
    expect(list.className).toContain('max-h-[3.25rem]');
    expect(list.className).toContain('overflow-hidden');

    await userEvent.click(screen.getByRole('button', { name: 'Show all (160)' }));
    expect(list.className).toContain('max-h-[40vh]');
    expect(list.className).toContain('overflow-y-auto');

    await userEvent.click(screen.getByRole('button', { name: 'Show fewer' }));
    expect(list.className).toContain('max-h-[3.25rem]');
  });

  it('selected tags show first and outside the clamp', async () => {
    overflow = true;
    const tags = catalog(160);
    const { onToggle, onClear } = renderRail(tags, [150, 3]);

    const list = screen.getByTestId('tag-filter-list');
    const selected = screen.getAllByRole('button', { pressed: true });
    expect(selected.map(b => b.textContent)).toEqual(['tag-150', 'tag-003']);
    for (const chip of selected) expect(list.contains(chip)).toBe(false);
    expect(screen.getByRole('button', { name: 'Show all (158)' })).toBeTruthy();

    await userEvent.click(selected[0]);
    expect(onToggle).toHaveBeenCalledWith(150);
    await userEvent.click(screen.getByRole('button', { name: 'Clear all' }));
    expect(onClear).toHaveBeenCalled();
  });

  it('the search box narrows the unselected tags and leaves the selected ones', async () => {
    const tags = [...catalog(TAG_SEARCH_MIN_TAGS), tag(99, 'PETG-CF')];
    renderRail(tags, [1]);

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search tags' }), 'petg');

    expect(screen.getAllByRole('button', { pressed: false }).map(b => b.textContent)).toEqual(['PETG-CF']);
    expect(screen.getAllByRole('button', { pressed: true }).map(b => b.textContent)).toEqual(['tag-001']);
  });

  it('says so when no tag matches', async () => {
    renderRail(catalog(TAG_SEARCH_MIN_TAGS));

    await userEvent.type(screen.getByRole('searchbox', { name: 'Search tags' }), 'zzz');

    expect(screen.getByText('No tags match')).toBeTruthy();
    expect(screen.queryByTestId('tag-filter-list')).toBeNull();
  });

  it('no search box just below the threshold', () => {
    renderRail(catalog(TAG_SEARCH_MIN_TAGS - 1));
    expect(screen.queryByRole('searchbox')).toBeNull();
  });

  it('a click on an unselected tag selects it', async () => {
    const { onToggle } = renderRail(catalog(3));
    await userEvent.click(screen.getByRole('button', { name: 'tag-002' }));
    expect(onToggle).toHaveBeenCalledWith(2);
  });

  it('folding the list back shows its first rows, not where it was scrolled to', async () => {
    overflow = true;
    renderRail(catalog(160));
    await userEvent.click(screen.getByRole('button', { name: 'Show all (160)' }));
    const list = screen.getByTestId('tag-filter-list');
    list.scrollTop = 600;

    await userEvent.click(screen.getByRole('button', { name: 'Show fewer' }));

    expect(list.scrollTop).toBe(0);
  });

  it('tabbing into a chip the clamp hides shows them all', () => {
    overflow = true;
    vi.spyOn(HTMLElement.prototype, 'offsetTop', 'get').mockImplementation(function (this: HTMLElement) {
      return this.title === 'tag-150' ? 400 : 0;
    });
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(() => 22);
    renderRail(catalog(160));

    act(() => screen.getByRole('button', { name: 'tag-001' }).focus());
    expect(screen.queryByRole('button', { name: 'Show fewer' })).toBeNull();

    act(() => screen.getByRole('button', { name: 'tag-150' }).focus());
    expect(screen.getByRole('button', { name: 'Show fewer' })).toBeTruthy();
  });

  it('a rename that makes the tags overflow brings the toggle', () => {
    const tags = catalog(30);
    const { rerender } = renderRail(tags);
    expect(screen.queryByRole('button', { name: /Show all/ })).toBeNull();

    overflow = true;
    rerender(tags.map(tg => (tg.id === 7 ? { ...tg, name: 'a much longer name than before' } : tg)));

    expect(screen.getByRole('button', { name: 'Show all (30)' })).toBeTruthy();
  });

  it('a query left behind when the search box goes away stops filtering', async () => {
    const tags = catalog(TAG_SEARCH_MIN_TAGS);
    const { rerender } = renderRail(tags);
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search tags' }), 'zzz');
    expect(screen.getByText('No tags match')).toBeTruthy();

    rerender(tags.slice(1));

    expect(screen.queryByRole('searchbox')).toBeNull();
    expect(screen.getAllByRole('button', { pressed: false })).toHaveLength(TAG_SEARCH_MIN_TAGS - 1);
  });
});
