/**
 * Tests for the project-view cache invalidation helper (#2731).
 *
 * The default staleTime is 60s, so a project page revisited within a minute of
 * deleting one of its prints serves the cached answer and keeps showing the
 * print. The user had to reload the page by hand. Deletes invalidated only
 * `['archives']`; the project-assign mutations only `['projects']`, which
 * refreshed the overview cards but never the detail page.
 */

import { describe, it, expect, vi } from 'vitest';
import type { QueryClient } from '@tanstack/react-query';
import { invalidateProjectViews, invalidateArchiveAndProjectViews } from '../../utils/projectQueries';

function mockClient() {
  const invalidateQueries = vi.fn().mockResolvedValue(undefined);
  return { client: { invalidateQueries } as unknown as QueryClient, invalidateQueries };
}

const keysFrom = (fn: ReturnType<typeof vi.fn>) => fn.mock.calls.map((c) => c[0].queryKey.join('/'));

describe('invalidateProjectViews', () => {
  it('refreshes every view whose contents depend on project membership', async () => {
    const { client, invalidateQueries } = mockClient();

    await invalidateProjectViews(client);

    expect(keysFrom(invalidateQueries).sort()).toEqual(
      ['project', 'project-archives', 'project-file-progress', 'project-timeline', 'projects'].sort(),
    );
  });

  it('uses bare prefixes so every cached project id is covered', async () => {
    const { client, invalidateQueries } = mockClient();

    await invalidateProjectViews(client);

    // ['project'] matches ['project', 42]; ['project', 42] would not match 43.
    for (const call of invalidateQueries.mock.calls) {
      expect(call[0].queryKey).toHaveLength(1);
    }
  });

  it('does not touch the archive list on its own', async () => {
    const { client, invalidateQueries } = mockClient();

    await invalidateProjectViews(client);

    expect(keysFrom(invalidateQueries)).not.toContain('archives');
  });
});

describe('invalidateArchiveAndProjectViews', () => {
  it('adds the archive list to the project views', async () => {
    const { client, invalidateQueries } = mockClient();

    await invalidateArchiveAndProjectViews(client);

    const keys = keysFrom(invalidateQueries);
    expect(keys).toContain('archives');
    expect(keys).toContain('project-archives');
    expect(keys).toContain('projects');
  });

  it('resolves only once every invalidation has settled', async () => {
    let settled = 0;
    const invalidateQueries = vi.fn().mockImplementation(
      () => new Promise<void>((resolve) => setTimeout(() => { settled += 1; resolve(); }, 0)),
    );

    await invalidateArchiveAndProjectViews({ invalidateQueries } as unknown as QueryClient);

    expect(settled).toBe(6);
  });
});
