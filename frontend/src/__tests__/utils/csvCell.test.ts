import { describe, it, expect } from 'vitest';
import { csvCell } from '../../utils/csvCell';

// The shopping list CSV shows typed text (brand, colour, note) as it was
// typed when opened in a spreadsheet.
describe('csvCell', () => {
  it.each([
    ['Bambu Lab', '"Bambu Lab"'],
    ['a = b', '"a = b"'],
    ['=HYPERLINK("https://elsewhere.example","open")', '"\'=HYPERLINK(""https://elsewhere.example"",""open"")"'],
    ['+1 extra', '"\'+1 extra"'],
    ['-x', '"\'-x"'],
    ['@SUM(A1)', '"\'@SUM(A1)"'],
    ['  =1+1', '"\'  =1+1"'],
    ['\t=1+1', '"\'\t=1+1"'],
    ['\nsecond line', '"\'\nsecond line"'],
    ['＝1+1', '"\'＝1+1"'],
    ['', '""'],
  ])('writes %j as %s', (value, expected) => {
    expect(csvCell(value)).toBe(expected);
  });

  it('writes numbers as they are, negative ones too', () => {
    expect(csvCell(-5)).toBe('"-5"');
    expect(csvCell(1200)).toBe('"1200"');
  });

  it('writes null and undefined as empty cells', () => {
    expect(csvCell(null)).toBe('""');
    expect(csvCell(undefined)).toBe('""');
  });
});
