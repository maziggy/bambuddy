// A cell starting with one of these is read as a formula by spreadsheet apps,
// including the full-width forms some normalise to ASCII.
const FORMULA_PREFIXES = ['=', '+', '-', '@', '＝', '＋', '－', '＠'];

/**
 * One CSV cell, quoted. Text that starts like a formula gets a leading quote
 * so a spreadsheet shows it as typed; leading whitespace is looked past, and
 * a value starting with a tab or line break is quoted too. Numbers are
 * written as they are.
 */
export function csvCell(value: unknown): string {
  let text = String(value ?? '');
  if (typeof value === 'string' && text !== '') {
    const startsLikeFormula = FORMULA_PREFIXES.some((p) => text.trimStart().startsWith(p));
    if ('\t\r\n'.includes(text[0]) || startsLikeFormula) text = `'${text}`;
  }
  return `"${text.replace(/"/g, '""')}"`;
}
