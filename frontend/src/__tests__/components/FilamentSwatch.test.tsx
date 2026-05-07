/**
 * Tests for the FilamentSwatch component (#1154).
 *
 * Covers the three independent inputs the swatch composes (rgba, extraColors,
 * effectType) and the buildFilamentBackground helper used to paint banners.
 */

import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import { FilamentSwatch } from '../../components/FilamentSwatch';
import { buildFilamentBackground } from '../../components/filamentSwatchHelpers';

describe('FilamentSwatch', () => {
  it('renders a solid swatch when only rgba is set', () => {
    render(<FilamentSwatch rgba="ff0000ff" effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    // Solid swatches are emitted as a 1-stop linear-gradient so the
    // checkerboard layer below is still visible through alpha.
    const bg = el.getAttribute('style') ?? '';
    expect(bg).toMatch(/linear-gradient/);
    expect(bg.toLowerCase()).toContain('#ff0000ff');
  });

  it('falls back to grey when nothing is set', () => {
    render(<FilamentSwatch effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    expect(el.style.backgroundImage.toLowerCase()).toContain('#808080');
  });

  it('renders a linear gradient when extraColors has multiple stops', () => {
    render(<FilamentSwatch rgba="ff0000ff" extraColors="ec984c,6cd4bc,a66eb9,d87694" effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    const bg = el.style.backgroundImage.toLowerCase();
    // Linear (not conic) for non-Multicolor subtype.
    expect(bg).toMatch(/linear-gradient/);
    expect(bg).toContain('#ec984c');
    expect(bg).toContain('#6cd4bc');
    expect(bg).toContain('#a66eb9');
    expect(bg).toContain('#d87694');
  });

  it('uses conic-gradient for Multicolor subtype', () => {
    render(
      <FilamentSwatch
        rgba="ff0000ff"
        extraColors="ec984c,6cd4bc,a66eb9"
        subtype="Multicolor"
        effectSize='table'
      />,
    );
    const el = screen.getByTestId('filament-swatch');
    expect(el.style.backgroundImage.toLowerCase()).toMatch(/conic-gradient/);
  });

  it('also uses conic-gradient when effectType is multicolor (catalog path)', () => {
    // Catalog entries don't have a `subtype`, so the multicolor effect_type
    // value also has to trigger conic rendering for parity with the spool path.
    render(<FilamentSwatch extraColors="ec984c,6cd4bc,a66eb9" effectType="multicolor"  effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    expect(el.style.backgroundImage.toLowerCase()).toMatch(/conic-gradient/);
  });

  it('layers an effect overlay on top of the colour layer for sparkle', () => {
    render(<FilamentSwatch rgba="ff0000ff" effectType="sparkle" effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    // Sparkle overlay is built from radial-gradient layers — confirm at least
    // one is in the composed background, ahead of the colour layer.
    expect(el.style.backgroundImage).toMatch(/radial-gradient/);
  });

  it('renders an overlay for silk variant', () => {
    // Silk gets a soft sheen overlay (added in #1154 follow-up).
    render(<FilamentSwatch rgba="ff0000ff" effectType="silk" effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    expect(el.style.backgroundImage).toMatch(/linear-gradient/);
  });

  it('treats categorical-only variants (gradient/dual-color) as labels without an overlay', () => {
    // No extra_colors set → swatch falls back to the solid colour layer; the
    // categorical effect value alone does not paint a sheen overlay.
    render(<FilamentSwatch rgba="ff0000ff" effectType="gradient" effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    // No radial-gradient (sparkle/glow) and no rainbow/sheen overlay either —
    // gradient/dual-color/tri-color are pure labels until extra_colors is set.
    expect(el.style.backgroundImage).not.toMatch(/radial-gradient/);
  });

  it('ignores unknown effect types instead of throwing', () => {
    render(<FilamentSwatch rgba="ff0000ff" effectType="not-a-real-variant" effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    expect(el.style.backgroundImage).not.toMatch(/radial-gradient/);
  });

  it('renders a checkerboard underneath so alpha is visible', () => {
    render(<FilamentSwatch rgba="ff000080" effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    // The component always appends a checkerboard layer last so semi-
    // transparent rgba values actually look transparent to the user.
    expect(el.style.backgroundImage).toMatch(/repeating-conic-gradient/);
  });

  it('skips invalid hex tokens in extraColors instead of throwing', () => {
    render(<FilamentSwatch extraColors="ff0000,not-hex,00ff00" effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    const bg = el.style.backgroundImage.toLowerCase();
    // The two valid stops survive; the garbage token is dropped.
    expect(bg).toContain('#ff0000');
    expect(bg).toContain('#00ff00');
    expect(bg).not.toContain('not-hex');
  });

  it('uses extra_colors for the title fallback when provided', () => {
    render(<FilamentSwatch extraColors="ff0000,00ff00" effectSize='table' />);
    const el = screen.getByTestId('filament-swatch');
    // Tooltip should show the comma-joined hex stops, not the (unset) rgba.
    expect(el.title.toLowerCase()).toContain('#ff0000');
    expect(el.title.toLowerCase()).toContain('#00ff00');
  });
});

describe('dual-color / tri-color hard-split bars (#1154 follow-up)', () => {
  // Bug: the original #1154 fix produced an identical
  // ``linear-gradient(135deg, A, B)`` for both Gradient and Dual Color
  // effects, so a "Dual Color" spool looked indistinguishable from a
  // "Gradient" one — both rendered as a smooth diagonal blend. Real
  // dual-colour spools have two visually distinct bars, not a blend.
  // These tests pin the corrected rendering: a horizontal hard split
  // for dual-color / tri-color, the original 135° smooth blend for
  // everything else.

  it('renders dual-color as a hard horizontal split, not a diagonal blend', () => {
    const bg = buildFilamentBackground({
      extraColors: '7f3696,006ec9',
      effectType: 'dual-color',
      effectSize: 'table',
    });
    const lower = bg.backgroundImage.toLowerCase();
    // Hard split direction — ``to right`` (or ``90deg``), never ``135deg``.
    expect(lower).toContain('to right');
    expect(lower).not.toContain('135deg');
    // Both colour stops present.
    expect(lower).toContain('#7f3696');
    expect(lower).toContain('#006ec9');
    // Each colour occupies its own segment via double-position stops, so
    // the colour change is a hard line rather than a blend region.
    expect(lower).toMatch(/#7f3696\s+0\.000%\s+50\.000%/);
    expect(lower).toMatch(/#006ec9\s+50\.000%\s+100\.000%/);
  });

  it('renders tri-color as three equal hard-split bars', () => {
    const bg = buildFilamentBackground({
      extraColors: 'ff0000,00ff00,0000ff',
      effectType: 'tri-color',
      effectSize: 'table',
    });
    const lower = bg.backgroundImage.toLowerCase();
    expect(lower).toContain('to right');
    // Each third gets its own contiguous segment.
    expect(lower).toMatch(/#ff0000\s+0\.000%\s+33\.333%/);
    expect(lower).toMatch(/#00ff00\s+33\.333%\s+66\.667%/);
    expect(lower).toMatch(/#0000ff\s+66\.667%\s+100\.000%/);
  });

  it('keeps the smooth 135° diagonal for the default Gradient effect', () => {
    const bg = buildFilamentBackground({
      extraColors: '7f3696,006ec9',
      effectType: 'gradient',
      effectSize: 'table',
    });
    const lower = bg.backgroundImage.toLowerCase();
    // Original visual preserved for non-dual / non-tri stops.
    expect(lower).toContain('135deg');
    expect(lower).not.toContain('to right');
    // Stops are concatenated without explicit positions — CSS does the
    // smooth blend across the diagonal.
    expect(lower).toContain('#7f3696');
    expect(lower).toContain('#006ec9');
  });

  it('regression: dual-color and gradient produce visually distinct backgrounds', () => {
    // Direct regression guard for the reporter's exact symptom — the two
    // effects must NOT collapse to the same CSS string. If a future refactor
    // accidentally drops the dual-color branch, this assertion fires before
    // anyone has to retest in a browser.
    const dual = buildFilamentBackground({
      extraColors: '7f3696,006ec9',
      effectType: 'dual-color',
      effectSize: 'table',
    });
    const grad = buildFilamentBackground({
      extraColors: '7f3696,006ec9',
      effectType: 'gradient',
      effectSize: 'table',
    });
    expect(dual.backgroundImage).not.toBe(grad.backgroundImage);
  });
});

describe('Sparkle prominence + checkerboard density (#1154 follow-up cosmetic)', () => {
  it('renders dense sparkle on card preset (at least 10 dots)', () => {
    // The original Sparkle pattern was 4 dots — too subtle on a 200×60px
    // banner. Now we use situation-aware dot counts: more dots for larger presets. 
    // Verify the card preset produces a dense pattern with at least 10 dots.
    render(<FilamentSwatch rgba="ff0000ff" effectType="sparkle" effectSize="card" />);
    const el = screen.getByTestId('filament-swatch');
    const radialCount = (el.style.backgroundImage.match(/radial-gradient/g) ?? []).length;
    expect(radialCount).toBeGreaterThanOrEqual(10);
  });

  it('uses fixed-pixel checkerboard tile so cell density is independent of swatch size', () => {
    // Without per-layer background-size, ``cover`` stretched the conic
    // gradient over the whole element and a card-sized banner only showed
    // 4 huge cells. Verify the checker layer carries an explicit pixel
    // tile size.
    const bg = buildFilamentBackground({ rgba: 'ff0000ff', effectSize: 'table' });
    const sizes = bg.backgroundSize.split(',').map((s) => s.trim());
    // Last layer is the checker; should be a fixed pixel tile, not 'cover'.
    expect(sizes[sizes.length - 1]).toMatch(/^\d+px(\s+\d+px)?$/);
    expect(sizes[sizes.length - 1]).not.toContain('cover');
  });

  it('limits sparkle dot count per size preset (table/card/bar)', () => {
    const tableBg = buildFilamentBackground({
      rgba: 'ff0000ff',
      effectType: 'sparkle',
      effectSize: 'table',
    });
    const cardBg = buildFilamentBackground({
      rgba: 'ff0000ff',
      effectType: 'sparkle',
      effectSize: 'card',
    });
    const barBg = buildFilamentBackground({
      rgba: 'ff0000ff',
      effectType: 'sparkle',
      effectSize: 'bar',
    });

    const countRadial = (css: string) => (css.match(/radial-gradient/g) ?? []).length;
    expect(countRadial(tableBg.backgroundImage)).toBe(5);
    expect(countRadial(cardBg.backgroundImage)).toBe(40);
    expect(countRadial(barBg.backgroundImage)).toBe(20);
  });

  it('scales sparkle dot radii by size preset while keeping seeded output deterministic', () => {
    const tableBg = buildFilamentBackground({
      rgba: 'ff0000ff',
      effectType: 'sparkle',
      effectSize: 'table',
    });
    const barBg = buildFilamentBackground({
      rgba: 'ff0000ff',
      effectType: 'sparkle',
      effectSize: 'bar',
    });

    const tableBgRepeat = buildFilamentBackground({
      rgba: 'ff0000ff',
      effectType: 'sparkle',
      effectSize: 'table',
    });

    const tableBgOther = buildFilamentBackground({
      rgba: '00ff00ff',
      effectType: 'sparkle',
      effectSize: 'table',
    });

    // Same seed must produce byte-identical overlay output.
    expect(tableBg.backgroundImage).toBe(tableBgRepeat.backgroundImage);

    // Different seeds must produce different overlay output.
    expect(tableBg.backgroundImage).not.toBe(tableBgOther.backgroundImage);

    // Radius grows for bar preset, preventing sparse-looking large banners.
    // Extract the first radius from each CSS string by looking for "0 Xpx, transparent Ypx"
    const tableR = tableBg.backgroundImage.match(/0[ ]+(\d+\.?\d*)px[,][ ]*transparent[ ]+(\d+\.?\d*)px/);
    const barR = barBg.backgroundImage.match(/0[ ]+(\d+\.?\d*)px[,][ ]*transparent[ ]+(\d+\.?\d*)px/);
    if (!tableR || !barR) {
      throw new Error(`Failed to extract radii: tableR=${tableR}, barR=${barR}`);
    }
    expect(Number(tableR[1])).toBeLessThan(Number(barR[1]));
    expect(Number(tableR[2])).toBeLessThan(Number(barR[2]));
  });
});

describe('buildFilamentBackground', () => {
  it('emits a CSS-style object with layered images and per-layer sizes', () => {
    const bg = buildFilamentBackground({
      rgba: 'ff0000ff',
      extraColors: 'aabbcc,ddeeff',
      effectType: 'matte',
      effectSize: 'table',
    });
    // Effect overlay → colour layer → checkerboard, in that order.
    expect(bg.backgroundImage).toMatch(/linear-gradient/);
    expect(bg.backgroundImage).toMatch(/repeating-conic-gradient/);
    expect(bg.backgroundImage.toLowerCase()).toContain('#aabbcc');
    expect(bg.backgroundImage.toLowerCase()).toContain('#ddeeff');
    // Per-layer sizes — three comma-separated values (effect/colour/checker)
    // in the same order. The checker has a fixed pixel tile so the cell
    // density doesn't scale with the element (#1154 follow-up).
    const sizeParts = bg.backgroundSize.split(',').map((s) => s.trim());
    expect(sizeParts).toHaveLength(3);
    expect(sizeParts[2]).toMatch(/\d+px/);
  });

  it('returns a usable solid background when only rgba is provided', () => {
    const bg = buildFilamentBackground({ rgba: '00ff00ff', effectSize: 'table' });
    expect(bg.backgroundImage.toLowerCase()).toContain('#00ff00ff');
    expect(bg.backgroundImage).toMatch(/repeating-conic-gradient/);
  });
});
