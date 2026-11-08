import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import type { LabelPageSize, LabelSheetLayout } from '../api/client';
import {
  MAX_COLS,
  MAX_ROWS,
  PAGE_SIZES_MM,
  formatMm,
  parseDraft,
  type LayoutDraft,
  type LayoutNumberField,
  type LayoutProblem,
} from '../utils/labelSheetLayout';

// The form for a custom label sheet, and a to-scale drawing of it (#3296).

const NUMBER_INPUTS: { field: LayoutNumberField; i18nKey: string; fallback: string; unit: boolean }[] = [
  { field: 'label_w_mm', i18nKey: 'labelWidth', fallback: 'Label width', unit: true },
  { field: 'label_h_mm', i18nKey: 'labelHeight', fallback: 'Label height', unit: true },
  { field: 'cols', i18nKey: 'columns', fallback: 'Columns', unit: false },
  { field: 'rows', i18nKey: 'rows', fallback: 'Rows', unit: false },
  { field: 'top_mm', i18nKey: 'topMargin', fallback: 'Top margin', unit: true },
  { field: 'left_mm', i18nKey: 'leftMargin', fallback: 'Left margin', unit: true },
  { field: 'col_gap_mm', i18nKey: 'columnGap', fallback: 'Gap between columns', unit: true },
  { field: 'row_gap_mm', i18nKey: 'rowGap', fallback: 'Gap between rows', unit: true },
];

/** Every label of ``layout`` drawn on its page. Labels that run off the page
 *  are red; the positions a starting position leaves blank are faded. */
export function LabelSheetDiagram({
  layout,
  startingPosition = 1,
}: {
  layout: LabelSheetLayout;
  startingPosition?: number;
}) {
  const { t } = useTranslation();
  const [pageW, pageH] = PAGE_SIZES_MM[layout.page];
  const cells = [];
  for (let row = 0; row < layout.rows; row++) {
    for (let col = 0; col < layout.cols; col++) {
      const x = layout.left_mm + col * (layout.label_w_mm + layout.col_gap_mm);
      const y = layout.top_mm + row * (layout.label_h_mm + layout.row_gap_mm);
      const offPage = x + layout.label_w_mm > pageW + 0.01 || y + layout.label_h_mm > pageH + 0.01;
      const skipped = row * layout.cols + col < startingPosition - 1;
      cells.push(
        <rect
          key={`${row}-${col}`}
          data-testid={offPage ? 'label-sheet-cell-off-page' : 'label-sheet-cell'}
          x={x}
          y={y}
          width={layout.label_w_mm}
          height={layout.label_h_mm}
          rx={1}
          className={
            offPage
              ? 'fill-red-400/40 stroke-red-500'
              : skipped
                ? 'fill-transparent stroke-gray-300'
                : 'fill-bambu-green/25 stroke-bambu-green'
          }
          strokeWidth={0.6}
        />,
      );
    }
  }
  return (
    <svg
      data-testid="label-sheet-diagram"
      role="img"
      aria-label={t('inventory.labels.custom.diagram', 'Drawing of the sheet with every label in place')}
      // Room around the page so a grid that runs off it still shows.
      viewBox={`-4 -4 ${pageW + 8} ${pageH + 8}`}
      className="h-48 w-auto mx-auto overflow-visible"
    >
      <rect x={0} y={0} width={pageW} height={pageH} className="fill-white stroke-gray-400" strokeWidth={0.6} />
      {cells}
    </svg>
  );
}

export function LabelSheetLayoutEditor({
  draft,
  onChange,
  problem,
  startingPosition,
}: {
  draft: LayoutDraft;
  onChange: (draft: LayoutDraft) => void;
  problem: LayoutProblem | null;
  startingPosition?: number;
}) {
  const { t, i18n } = useTranslation();
  const hintId = useId();
  const problemId = useId();
  const layout = parseDraft(draft);
  // Lengths and counts in the reader's own number format (70,5 mm in German).
  const problemParams = problem?.params
    ? Object.fromEntries(Object.entries(problem.params).map(([k, v]) => [k, formatMm(v, i18n.language)]))
    : undefined;
  // Drawn while it is a sane grid, overflow included: that is what the drawing
  // is for. A grid past the limits is only explained, not drawn box by box.
  const drawable =
    layout !== null &&
    Number.isInteger(layout.cols) &&
    Number.isInteger(layout.rows) &&
    layout.cols >= 1 &&
    layout.cols <= MAX_COLS &&
    layout.rows >= 1 &&
    layout.rows <= MAX_ROWS &&
    layout.label_w_mm > 0 &&
    layout.label_h_mm > 0;
  const inputClass =
    'w-full px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:outline-none focus:border-bambu-green';

  return (
    <fieldset data-testid="label-sheet-editor" className="space-y-3">
      <legend className="text-sm font-medium text-white">
        {t('inventory.labels.custom.layoutTitle', 'Sheet layout')}
      </legend>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        <label className="col-span-2 space-y-0.5">
          <span className="text-xs text-bambu-gray">{t('inventory.labels.custom.page', 'Page')}</span>
          <select
            data-testid="label-sheet-page"
            value={draft.page}
            onChange={(e) => onChange({ ...draft, page: e.target.value as LabelPageSize })}
            className={inputClass}
          >
            <option value="a4">{t('inventory.labels.custom.pageA4', 'A4 (210 × 297 mm)')}</option>
            <option value="letter">{t('inventory.labels.custom.pageLetter', 'US Letter (215.9 × 279.4 mm)')}</option>
          </select>
        </label>
        {NUMBER_INPUTS.map(({ field, i18nKey, fallback, unit }) => (
          <label key={field} className="space-y-0.5">
            <span className="text-xs text-bambu-gray">
              {t(`inventory.labels.custom.${i18nKey}`, fallback)}
              {unit && ' (mm)'}
            </span>
            <input
              data-testid={`label-sheet-${field}`}
              type="text"
              inputMode={unit ? 'decimal' : 'numeric'}
              value={draft[field]}
              onChange={(e) => onChange({ ...draft, [field]: e.target.value })}
              aria-describedby={problem ? `${hintId} ${problemId}` : hintId}
              aria-invalid={problem ? true : undefined}
              className={inputClass}
            />
          </label>
        ))}
      </div>
      <p id={hintId} className="text-xs text-bambu-gray">
        {t(
          'inventory.labels.custom.measureHint',
          'Measure from the top-left corner of the page: margins to the first label, gaps between neighbouring labels.',
        )}
      </p>
      {problem && (
        <p id={problemId} data-testid="label-sheet-problem" aria-live="polite" className="text-xs text-red-400">
          {t(`inventory.labels.custom.problems.${problem.key}`, problem.fallback, problemParams)}
        </p>
      )}
      {drawable && layout && <LabelSheetDiagram layout={layout} startingPosition={startingPosition} />}
    </fieldset>
  );
}
