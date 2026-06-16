/**
 * Tests for the shared filament-label resolution hook (#1718 round 3).
 *
 * Round 3 extracted the three-query resolution machinery out of
 * ``FilamentOverride`` so the printer-mode ``FilamentMapping`` could share
 * the same label logic without drift. Both panels are integration-tested
 * already, but the hook deserves direct coverage so future edits don't break
 * a subtle contract (positional output alignment, fallback chain, query
 * dedup when the SKU is unknown).
 */

import { describe, it, expect, afterEach } from 'vitest';
import { renderHook, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { type ReactNode } from 'react';
import { server } from '../mocks/server';
import { extractMaterialHint, useFilamentLabels } from '../../components/PrintModal/useFilamentLabels';

function makeWrapper() {
  // Fresh QueryClient per renderHook so cached data from one test doesn't
  // bleed into the next — the hook keys queries on (hex, materialHint), so a
  // stale "PLA Matte → Charcoal" cache entry would silently mask a misrouted
  // request in a later test.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

afterEach(() => {
  cleanup();
});

describe('extractMaterialHint', () => {
  it('strips the leading brand token from multi-word names', () => {
    expect(extractMaterialHint('Bambu PLA Matte')).toBe('PLA Matte');
    expect(extractMaterialHint('PolyLite ABS')).toBe('ABS');
    expect(extractMaterialHint('Bambu PLA-CF')).toBe('PLA-CF');
  });

  it('returns single-word names unchanged so "PLA" stays "PLA"', () => {
    // The catalog's ``material`` column has plain "PLA" entries; passing the
    // single token through means the by-material lookup can still match.
    expect(extractMaterialHint('PLA')).toBe('PLA');
    expect(extractMaterialHint('PETG-HF')).toBe('PETG-HF');
  });

  it('collapses interior whitespace and trims edges', () => {
    expect(extractMaterialHint('  Bambu   PLA   Matte  ')).toBe('PLA Matte');
  });

  it('returns "" when the input is blank', () => {
    // Empty material is the same priority-fallback case as omitting the param.
    expect(extractMaterialHint('')).toBe('');
    expect(extractMaterialHint('   ')).toBe('');
  });
});

describe('useFilamentLabels', () => {
  it('returns [] for undefined or empty inputs', () => {
    const { result, rerender } = renderHook(
      ({ reqs }: { reqs: undefined | Array<{ type: string; color: string }> }) =>
        useFilamentLabels(reqs),
      { wrapper: makeWrapper(), initialProps: { reqs: undefined } },
    );
    expect(result.current).toEqual([]);

    rerender({ reqs: [] });
    expect(result.current).toEqual([]);
  });

  it('resolves tray_info_idx → sub-brand via the builtin map', async () => {
    server.use(
      http.get('/api/v1/cloud/builtin-filaments', () =>
        HttpResponse.json([{ filament_id: 'GFA01', name: 'Bambu PLA Matte' }]),
      ),
      http.get('/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
      http.get('/api/v1/inventory/colors/by-material', () =>
        HttpResponse.json({ color_name: 'Charcoal' }),
      ),
    );

    const { result } = renderHook(
      () => useFilamentLabels([{ type: 'PLA', color: '#000000', tray_info_idx: 'GFA01' }]),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current[0]?.resolvedName).toBe('Bambu PLA Matte');
      expect(result.current[0]?.colorLabel).toBe('Charcoal');
    });
  });

  it('prefers the cloud user-preset name over the builtin entry for the same id', async () => {
    // Round 2 contract: same id in both maps → cloud wins because the user-
    // authored name is the more specific label.
    server.use(
      http.get('/api/v1/cloud/builtin-filaments', () =>
        HttpResponse.json([{ filament_id: 'GFA00', name: 'Bambu PLA Basic' }]),
      ),
      http.get('/api/v1/cloud/filament-id-map', () =>
        HttpResponse.json({ GFA00: 'My House PLA' }),
      ),
      http.get('/api/v1/inventory/colors/by-material', () =>
        HttpResponse.json({ color_name: null }),
      ),
    );

    const { result } = renderHook(
      () => useFilamentLabels([{ type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA00' }]),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current[0]?.resolvedName).toBe('My House PLA');
    });
  });

  it('falls back to req.type when the SKU is unknown to both maps', async () => {
    server.use(
      http.get('/api/v1/cloud/builtin-filaments', () => HttpResponse.json([])),
      http.get('/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
      http.get('/api/v1/inventory/colors/by-material', () =>
        HttpResponse.json({ color_name: null }),
      ),
    );

    const { result } = renderHook(
      () => useFilamentLabels([{ type: 'PETG-HF', color: '#00FF00', tray_info_idx: 'GFXXX' }]),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current[0]?.resolvedName).toBe('PETG-HF');
    });
  });

  it('falls back colorLabel to getColorName(hex) when the by-material lookup returns null', async () => {
    server.use(
      http.get('/api/v1/cloud/builtin-filaments', () =>
        HttpResponse.json([{ filament_id: 'GFA01', name: 'Bambu PLA Matte' }]),
      ),
      http.get('/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
      http.get('/api/v1/inventory/colors/by-material', () =>
        HttpResponse.json({ color_name: null }),
      ),
    );

    const { result } = renderHook(
      () => useFilamentLabels([{ type: 'PLA', color: '#FF0000', tray_info_idx: 'GFA01' }]),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      // Anything non-empty from getColorName is fine; the critical contract
      // is "never returns the empty string / null in the colorLabel".
      expect(result.current[0]?.colorLabel).toBeTruthy();
      expect(result.current[0]?.colorLabel).not.toBe('null');
    });
  });

  it('keeps positional alignment across slots with different (hex, material) tuples', async () => {
    // Regression guard for the position-indexed output contract: labels[i]
    // MUST correspond to reqs[i]. If useQueries answered out-of-order or
    // dedup'd same-hex slots, FilamentMapping would render a PLA Matte
    // Charcoal slot as PLA Basic Black (and vice versa).
    server.use(
      http.get('/api/v1/cloud/builtin-filaments', () =>
        HttpResponse.json([
          { filament_id: 'GFA00', name: 'Bambu PLA Basic' },
          { filament_id: 'GFA01', name: 'Bambu PLA Matte' },
        ]),
      ),
      http.get('/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
      http.get('/api/v1/inventory/colors/by-material', ({ request }) => {
        const material = new URL(request.url).searchParams.get('material');
        if (material === 'PLA Matte') return HttpResponse.json({ color_name: 'Charcoal' });
        if (material === 'PLA Basic') return HttpResponse.json({ color_name: 'Black' });
        return HttpResponse.json({ color_name: null });
      }),
    );

    const { result } = renderHook(
      () =>
        useFilamentLabels([
          { type: 'PLA', color: '#000000', tray_info_idx: 'GFA01' }, // PLA Matte
          { type: 'PLA', color: '#000000', tray_info_idx: 'GFA00' }, // PLA Basic
        ]),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      expect(result.current[0]?.resolvedName).toBe('Bambu PLA Matte');
      expect(result.current[0]?.colorLabel).toBe('Charcoal');
      expect(result.current[1]?.resolvedName).toBe('Bambu PLA Basic');
      expect(result.current[1]?.colorLabel).toBe('Black');
    });
  });

  it('skips the by-material query when the slot has no hex (enabled: !!color)', async () => {
    // Defensive: 3MFs occasionally leave the color attribute blank. The
    // query is gated on truthy color so we don't fire a request that we
    // know can't disambiguate anything.
    let byMaterialCalls = 0;
    server.use(
      http.get('/api/v1/cloud/builtin-filaments', () =>
        HttpResponse.json([{ filament_id: 'GFA01', name: 'Bambu PLA Matte' }]),
      ),
      http.get('/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
      http.get('/api/v1/inventory/colors/by-material', () => {
        byMaterialCalls += 1;
        return HttpResponse.json({ color_name: null });
      }),
    );

    const { result } = renderHook(
      () => useFilamentLabels([{ type: 'PLA', color: '', tray_info_idx: 'GFA01' }]),
      { wrapper: makeWrapper() },
    );

    await waitFor(() => {
      // The sub-brand half resolves from the builtin map even without a hex.
      expect(result.current[0]?.resolvedName).toBe('Bambu PLA Matte');
    });
    expect(byMaterialCalls).toBe(0);
  });
});
