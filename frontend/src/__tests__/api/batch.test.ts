/**
 * The per-id read batcher (api/batch.ts): what goes out, and who gets what back.
 */
import { describe, it, expect, vi } from 'vitest';
import { batched, batchesSettled, byField, byId } from '../../api/batch';

class StatusError extends Error {
  constructor(public status: number) {
    super(`HTTP ${status}`);
  }
}

function setup(options: { bulk?: (ids: number[]) => Promise<Map<number, string>> } = {}) {
  const single = vi.fn(async (id: number) => `single-${id}`);
  const many = vi.fn(
    options.bulk ?? (async (ids: number[]) => new Map(ids.map((id) => [id, `bulk-${id}`] as [number, string]))),
  );
  return { single, many, read: batched(single, many) };
}

describe('batched', () => {
  it('sends a lone id as the single request', async () => {
    const { single, many, read } = setup();

    await expect(read(7)).resolves.toBe('single-7');
    expect(single).toHaveBeenCalledWith(7);
    expect(many).not.toHaveBeenCalled();
  });

  it('sends ids asked for together as one bulk request, each caller getting its own answer', async () => {
    const { single, many, read } = setup();

    const answers = await Promise.all([read(1), read(2), read(3)]);

    expect(answers).toEqual(['bulk-1', 'bulk-2', 'bulk-3']);
    expect(many).toHaveBeenCalledTimes(1);
    expect(many).toHaveBeenCalledWith([1, 2, 3]);
    expect(single).not.toHaveBeenCalled();
  });

  it('asks for a repeated id once and answers every caller', async () => {
    const { many, read } = setup();

    const answers = await Promise.all([read(1), read(2), read(1)]);

    expect(answers).toEqual(['bulk-1', 'bulk-2', 'bulk-1']);
    expect(many).toHaveBeenCalledWith([1, 2]);
  });

  it('catches calls arriving a few milliseconds apart, as interval refetches do', async () => {
    const { many, read } = setup();

    const first = read(1);
    await new Promise((resolve) => setTimeout(resolve, 3));
    const second = read(2);

    await expect(Promise.all([first, second])).resolves.toEqual(['bulk-1', 'bulk-2']);
    expect(many).toHaveBeenCalledTimes(1);
  });

  it('starts a new request for calls after the window closed', async () => {
    const { many, single, read } = setup();

    await Promise.all([read(1), read(2)]);
    await read(3);

    expect(many).toHaveBeenCalledTimes(1);
    expect(single).toHaveBeenCalledWith(3);
  });

  it('asks the single request for an id the bulk answer left out, passing on its error', async () => {
    const { single, read } = setup({ bulk: async () => new Map([[1, 'bulk-1']]) });
    single.mockImplementation(async () => {
      throw new StatusError(404);
    });

    const [kept, left] = await Promise.allSettled([read(1), read(2)]);

    expect(kept).toEqual({ status: 'fulfilled', value: 'bulk-1' });
    expect(left.status).toBe('rejected');
    expect((left as PromiseRejectedResult).reason.status).toBe(404);
    expect(single).toHaveBeenCalledWith(2);
    expect(single).not.toHaveBeenCalledWith(1);
  });

  it('keeps answering the rest when an id in the middle is left out', async () => {
    const { read } = setup({ bulk: async () => new Map([[1, 'bulk-1'], [3, 'bulk-3']]) });

    await expect(Promise.all([read(1), read(2), read(3)])).resolves.toEqual(['bulk-1', 'single-2', 'bulk-3']);
  });

  it.each([404, 405, 422, 500, 503])('falls back to single requests when the bulk route answers %i', async (status) => {
    const { single, read } = setup({
      bulk: async () => {
        throw new StatusError(status);
      },
    });

    await expect(Promise.all([read(1), read(2)])).resolves.toEqual(['single-1', 'single-2']);
    expect(single).toHaveBeenCalledTimes(2);
  });

  it.each([401, 403])('rejects every caller, sending no single requests, on a %i refusal', async (status) => {
    const { single, read } = setup({
      bulk: async () => {
        throw new StatusError(status);
      },
    });

    const results = await Promise.allSettled([read(1), read(2)]);

    expect(results.map((r) => r.status)).toEqual(['rejected', 'rejected']);
    expect((results[0] as PromiseRejectedResult).reason.status).toBe(status);
    expect(single).not.toHaveBeenCalled();
  });

  it('falls back to single requests when the bulk request never got an answer', async () => {
    const { single, read } = setup({
      bulk: async () => {
        throw new TypeError('Failed to fetch');
      },
    });

    await expect(Promise.all([read(1), read(2)])).resolves.toEqual(['single-1', 'single-2']);
    expect(single).toHaveBeenCalledTimes(2);
  });

  it('confines a failure the server has for one id to that id', async () => {
    const { single, read } = setup({
      bulk: async () => {
        throw new StatusError(500);
      },
    });
    single.mockImplementation(async (id: number) => {
      if (id === 2) throw new StatusError(500);
      return `single-${id}`;
    });

    const results = await Promise.allSettled([read(1), read(2), read(3)]);

    expect(results.map((r) => r.status)).toEqual(['fulfilled', 'rejected', 'fulfilled']);
  });

  it('splits a very long id list into several bulk requests', async () => {
    const { many, read } = setup();

    await Promise.all(Array.from({ length: 450 }, (_, i) => read(i + 1)));

    expect(many.mock.calls.map(([ids]) => ids.length)).toEqual([200, 200, 50]);
  });

  it('reports when every started batch has been answered', async () => {
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { read } = setup({
      bulk: async (ids) => {
        await held;
        return new Map(ids.map((id) => [id, `bulk-${id}`] as [number, string]));
      },
    });
    let settled = false;

    const reads = Promise.all([read(1), read(2)]);
    const waiting = batchesSettled().then(() => {
      settled = true;
    });
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(settled).toBe(false);

    release();
    await waiting;
    expect(settled).toBe(true);
    await reads;
  });
});

describe('byId / byField', () => {
  it('turns string-keyed JSON into a number-keyed map', () => {
    expect(byId({ '1': 'a', '22': 'b' })).toEqual(
      new Map([
        [1, 'a'],
        [22, 'b'],
      ]),
    );
  });

  it('keys rows by their own id', () => {
    const rows = [{ id: 4 }, { id: 9 }];
    expect(byField(rows, (r) => r.id)).toEqual(
      new Map([
        [4, rows[0]],
        [9, rows[1]],
      ]),
    );
  });
});
