// Custom label sheets (#3296): the form's draft, and the same "does it fit"
// check the server makes in backend.app.services.label_renderer.SheetLayout,
// so the dialog can explain a problem while the user types instead of after
// a 422.

import type { LabelPageSize, LabelSheetLayout } from '../api/client';

// Mirrors of the renderer's limits (label_renderer.py), number for number, so
// the dialog and the server never disagree about a layout.
export const PAGE_SIZES_MM: Record<LabelPageSize, [number, number]> = {
  a4: [210, 297],
  letter: [215.9, 279.4],
};
// What the label layout can fill: the full layout from 20 mm tall, and room
// for a four-digit spool ID beside the swatch and QR from 40 mm wide.
export const MIN_LABEL_W_MM = 40;
export const MIN_LABEL_H_MM = 20;
export const MAX_LENGTH_MM = 300;
export const MAX_COLS = 20;
export const MAX_ROWS = 40;
const FIT_TOLERANCE_MM = 0.01;
// PNG export of sheets is capped at pages × dpi (MAX_PNG_SHEET_PAGES_X_DPI in labels.py).
export const MAX_PNG_SHEET_PAGES_X_DPI = 15000;

/** Pages ``labelCount`` labels take on a sheet of ``capacity``, from ``startingPosition``. */
export function sheetPageCount(capacity: number, labelCount: number, startingPosition = 1): number {
  return Math.ceil((startingPosition - 1 + labelCount) / capacity);
}

/** A length in millimetres, written the way the reader's language writes numbers. */
export function formatMm(mm: number, language: string): string {
  return new Intl.NumberFormat(language, { maximumFractionDigits: 2 }).format(mm);
}

export const LAYOUT_NUMBER_FIELDS = [
  'label_w_mm',
  'label_h_mm',
  'cols',
  'rows',
  'top_mm',
  'left_mm',
  'col_gap_mm',
  'row_gap_mm',
] as const;
export type LayoutNumberField = (typeof LAYOUT_NUMBER_FIELDS)[number];

/** The form as typed: numbers stay strings so a half-typed "3." isn't lost. */
export type LayoutDraft = { page: LabelPageSize } & Record<LayoutNumberField, string>;

/** A reasonable starting point: a 3 × 7 grid on A4 (the Avery L7160). */
export const DEFAULT_LAYOUT: LabelSheetLayout = {
  page: 'a4',
  label_w_mm: 63.5,
  label_h_mm: 38.1,
  cols: 3,
  rows: 7,
  top_mm: 15.15,
  left_mm: 7,
  col_gap_mm: 2.5,
  row_gap_mm: 0,
};

export function draftFromLayout(layout: LabelSheetLayout): LayoutDraft {
  const draft = { page: layout.page } as LayoutDraft;
  for (const field of LAYOUT_NUMBER_FIELDS) draft[field] = String(layout[field]);
  return draft;
}

/** The layout a draft describes, or null while a field is blank or not a number. */
export function parseDraft(draft: LayoutDraft): LabelSheetLayout | null {
  const layout = { page: draft.page } as LabelSheetLayout;
  for (const field of LAYOUT_NUMBER_FIELDS) {
    const text = draft[field].trim().replace(',', '.');
    const value = Number(text);
    if (text === '' || !Number.isFinite(value)) return null;
    layout[field] = value;
  }
  return layout;
}

export function isLayoutDraft(value: unknown): value is LayoutDraft {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return (
    (v.page === 'a4' || v.page === 'letter') && LAYOUT_NUMBER_FIELDS.every((f) => typeof v[f] === 'string')
  );
}

export interface LayoutProblem {
  key: string;
  fallback: string;
  /** Lengths in mm and counts; the dialog formats them for the reader's language. */
  params?: Record<string, number>;
}

/** Width and height the grid takes up, margins included. */
export function gridExtentMm(layout: LabelSheetLayout): [number, number] {
  return [
    layout.left_mm + layout.cols * layout.label_w_mm + (layout.cols - 1) * layout.col_gap_mm,
    layout.top_mm + layout.rows * layout.label_h_mm + (layout.rows - 1) * layout.row_gap_mm,
  ];
}

/** Why a draft can't print, or null when it can. Same rules as the server. */
export function layoutProblem(draft: LayoutDraft): LayoutProblem | null {
  const layout = parseDraft(draft);
  if (!layout) {
    return { key: 'incomplete', fallback: 'Fill in every field with a number.' };
  }
  // Same order as SheetLayout.problem(), so both name the same problem first.
  if (layout.label_w_mm < MIN_LABEL_W_MM || layout.label_h_mm < MIN_LABEL_H_MM) {
    return {
      key: 'tooSmall',
      fallback: 'Labels must be at least {{minWidth}} mm wide and {{minHeight}} mm tall, or the text doesn’t fit.',
      params: { minWidth: MIN_LABEL_W_MM, minHeight: MIN_LABEL_H_MM },
    };
  }
  const lengths = [layout.label_w_mm, layout.label_h_mm, layout.top_mm, layout.left_mm, layout.col_gap_mm, layout.row_gap_mm];
  if (Math.max(...lengths) > MAX_LENGTH_MM) {
    return {
      key: 'tooLong',
      fallback: 'Sizes, margins and gaps can be at most {{max}} mm.',
      params: { max: MAX_LENGTH_MM },
    };
  }
  if (!Number.isInteger(layout.cols) || layout.cols < 1 || layout.cols > MAX_COLS) {
    return { key: 'columns', fallback: 'Columns must be a whole number from 1 to {{max}}.', params: { max: MAX_COLS } };
  }
  if (!Number.isInteger(layout.rows) || layout.rows < 1 || layout.rows > MAX_ROWS) {
    return { key: 'rows', fallback: 'Rows must be a whole number from 1 to {{max}}.', params: { max: MAX_ROWS } };
  }
  if (Math.min(layout.top_mm, layout.left_mm, layout.col_gap_mm, layout.row_gap_mm) < 0) {
    return { key: 'negative', fallback: 'Margins and gaps can’t be negative.' };
  }
  const [pageW, pageH] = PAGE_SIZES_MM[layout.page];
  const [gridW, gridH] = gridExtentMm(layout);
  if (gridW > pageW + FIT_TOLERANCE_MM) {
    return {
      key: 'tooWide',
      fallback: 'The labels are {{size}} mm wide with the left margin, but the page is {{page}} mm.',
      params: { size: gridW, page: pageW },
    };
  }
  if (gridH > pageH + FIT_TOLERANCE_MM) {
    return {
      key: 'tooTall',
      fallback: 'The labels are {{size}} mm tall with the top margin, but the page is {{page}} mm.',
      params: { size: gridH, page: pageH },
    };
  }
  return null;
}
