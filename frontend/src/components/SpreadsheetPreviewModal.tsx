import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FileSpreadsheet, Loader2, X } from 'lucide-react';
import { api, getAuthToken } from '../api/client';
import { formatFileSize } from '../utils/file';

// Parsing an arbitrarily large workbook would freeze the tab — anything over
// this size (or beyond the row/column caps) falls back to a truncation notice.
export const SPREADSHEET_PREVIEW_MAX_BYTES = 20 * 1024 * 1024;
const MAX_ROWS = 500;
const MAX_COLS = 40;

interface SheetGrid {
  name: string;
  rows: string[][];
  totalRows: number;
  totalCols: number;
}

interface SpreadsheetPreviewModalProps {
  libraryFileId: number;
  filename: string;
  /** csv | xlsx | ods */
  fileType: string;
  fileSize: number;
  onClose: () => void;
  /** Called once with a 256px PNG of the first sheet, for the grid thumbnail (#2976). */
  onSnapshot?: (blob: Blob) => void;
}

// Spreadsheet-style column letters: 0 -> A, 25 -> Z, 26 -> AA, ...
function columnLabel(index: number): string {
  let label = '';
  let i = index;
  while (i >= 0) {
    label = String.fromCharCode(65 + (i % 26)) + label;
    i = Math.floor(i / 26) - 1;
  }
  return label;
}

// Mini table rendered onto a canvas as the grid thumbnail. Dark background to
// match the STL thumbnails the grid already shows.
function drawSheetSnapshot(rows: string[][]): Promise<Blob | null> {
  const size = 256;
  const cols = 5;
  const rowCount = 9;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) return Promise.resolve(null);

  const cellW = size / cols;
  const cellH = size / rowCount;
  ctx.fillStyle = '#1a1a1a';
  ctx.fillRect(0, 0, size, size);
  ctx.fillStyle = 'rgba(0, 174, 66, 0.25)';
  ctx.fillRect(0, 0, size, cellH);
  ctx.strokeStyle = '#333333';
  ctx.lineWidth = 1;
  for (let c = 1; c < cols; c++) {
    ctx.beginPath();
    ctx.moveTo(c * cellW + 0.5, 0);
    ctx.lineTo(c * cellW + 0.5, size);
    ctx.stroke();
  }
  for (let r = 1; r < rowCount; r++) {
    ctx.beginPath();
    ctx.moveTo(0, r * cellH + 0.5);
    ctx.lineTo(size, r * cellH + 0.5);
    ctx.stroke();
  }
  ctx.fillStyle = '#d4d4d4';
  ctx.font = '11px sans-serif';
  ctx.textBaseline = 'middle';
  for (let r = 0; r < rowCount; r++) {
    for (let c = 0; c < cols; c++) {
      const text = rows[r]?.[c];
      if (!text) continue;
      ctx.fillText(String(text), c * cellW + 4, r * cellH + cellH / 2, cellW - 8);
    }
  }
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
}

export function SpreadsheetPreviewModal({
  libraryFileId,
  filename,
  fileType,
  fileSize,
  onClose,
  onSnapshot,
}: SpreadsheetPreviewModalProps) {
  const { t } = useTranslation();
  const [sheets, setSheets] = useState<SheetGrid[] | null>(null);
  const [activeSheet, setActiveSheet] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const snapshotSentRef = useRef(false);
  const onSnapshotRef = useRef(onSnapshot);
  useEffect(() => {
    onSnapshotRef.current = onSnapshot;
  });

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setSheets(null);
    setError(null);
    setActiveSheet(0);

    if (fileSize > SPREADSHEET_PREVIEW_MAX_BYTES) {
      setError(t('fileManager.preview.tooLarge', { size: formatFileSize(fileSize) }));
      return;
    }

    const headers: HeadersInit = {};
    const token = getAuthToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    (async () => {
      const res = await fetch(api.getLibraryFileDownloadUrl(libraryFileId), { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buffer = await res.arrayBuffer();

      let parsed: SheetGrid[];
      if (fileType === 'csv') {
        // papaparse is loaded on demand so it stays out of the main bundle.
        const Papa = (await import('papaparse')).default;
        const text = new TextDecoder().decode(buffer);
        const result = Papa.parse<string[]>(text, { skipEmptyLines: false });
        const all = result.data.filter((row) => Array.isArray(row));
        // A trailing newline parses as one empty row — drop trailing blanks.
        while (all.length > 0 && all[all.length - 1].every((cell) => !cell)) {
          all.pop();
        }
        const totalRows = all.length;
        const totalCols = all.reduce((max, row) => Math.max(max, row.length), 0);
        const rows = all.slice(0, MAX_ROWS).map((row) => row.slice(0, MAX_COLS).map((cell) => cell ?? ''));
        parsed = [{ name: filename, rows, totalRows, totalCols }];
      } else {
        // SheetJS handles both XLSX and ODS; loaded on demand like papaparse.
        const XLSX = await import('xlsx');
        const workbook = XLSX.read(buffer, { dense: true });
        parsed = workbook.SheetNames.map((name) => {
          const ws = workbook.Sheets[name];
          const ref = ws?.['!ref'];
          if (!ws || !ref) return { name, rows: [], totalRows: 0, totalCols: 0 };
          const range = XLSX.utils.decode_range(ref);
          const totalRows = range.e.r - range.s.r + 1;
          const totalCols = range.e.c - range.s.c + 1;
          // Cap the extracted range instead of slicing afterwards, so a huge
          // sheet is never materialised in full.
          const capped = {
            s: range.s,
            e: {
              r: Math.min(range.e.r, range.s.r + MAX_ROWS - 1),
              c: Math.min(range.e.c, range.s.c + MAX_COLS - 1),
            },
          };
          const rows = XLSX.utils.sheet_to_json(ws, {
            header: 1,
            raw: false,
            defval: '',
            range: XLSX.utils.encode_range(capped),
          }) as string[][];
          return { name, rows, totalRows, totalCols };
        });
      }

      if (cancelled) return;
      setSheets(parsed);

      if (onSnapshotRef.current && !snapshotSentRef.current) {
        const first = parsed.find((sheet) => sheet.rows.length > 0);
        if (first) {
          snapshotSentRef.current = true;
          const blob = await drawSheetSnapshot(first.rows);
          if (blob && !cancelled) onSnapshotRef.current?.(blob);
        }
      }
    })().catch(() => {
      if (!cancelled) setError(t('fileManager.preview.error'));
    });

    return () => {
      cancelled = true;
    };
  }, [libraryFileId, fileType, fileSize, filename, t]);

  const sheet = sheets?.[activeSheet] ?? null;
  const rowsTruncated = sheet != null && sheet.totalRows > sheet.rows.length;
  const colsTruncated = sheet != null && sheet.totalCols > MAX_COLS;
  const shownCols = sheet == null ? 0 : Math.min(sheet.totalCols, MAX_COLS);

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-6xl h-[85vh] border border-bambu-dark-tertiary flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2 min-w-0">
            <FileSpreadsheet className="w-5 h-5 text-bambu-green flex-shrink-0" />
            <h2 className="text-lg font-semibold text-white truncate">{filename}</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Sheet tabs */}
        {sheets && sheets.length > 1 && (
          <div className="flex gap-1 px-4 pt-2 overflow-x-auto flex-shrink-0">
            {sheets.map((s, index) => (
              <button
                key={`${s.name}-${index}`}
                onClick={() => setActiveSheet(index)}
                className={`px-3 py-1.5 text-sm rounded-t whitespace-nowrap transition-colors ${
                  index === activeSheet
                    ? 'bg-bambu-dark text-white border border-b-0 border-bambu-dark-tertiary'
                    : 'text-bambu-gray hover:text-white hover:bg-bambu-dark/50'
                }`}
              >
                {s.name}
              </button>
            ))}
          </div>
        )}

        {/* Content */}
        <div className="flex-1 min-h-0 overflow-auto bg-bambu-dark rounded-b-lg">
          {error ? (
            <div className="h-full flex items-center justify-center p-6">
              <p className="text-bambu-gray text-center">{error}</p>
            </div>
          ) : !sheets ? (
            <div className="h-full flex items-center justify-center">
              <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
            </div>
          ) : !sheet || sheet.rows.length === 0 ? (
            <div className="h-full flex items-center justify-center p-6">
              <p className="text-bambu-gray">{t('fileManager.preview.emptySheet')}</p>
            </div>
          ) : (
            <table className="border-collapse text-xs">
              <thead>
                <tr>
                  <th className="sticky top-0 bg-bambu-dark-secondary border border-bambu-dark-tertiary px-2 py-1 text-bambu-gray font-medium w-10" />
                  {Array.from({ length: shownCols }, (_, c) => (
                    <th
                      key={c}
                      className="sticky top-0 bg-bambu-dark-secondary border border-bambu-dark-tertiary px-2 py-1 text-bambu-gray font-medium text-left min-w-[80px]"
                    >
                      {columnLabel(c)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sheet.rows.map((row, r) => (
                  <tr key={r}>
                    <td className="border border-bambu-dark-tertiary px-2 py-1 text-bambu-gray text-right bg-bambu-dark-secondary/50">
                      {r + 1}
                    </td>
                    {Array.from({ length: shownCols }, (_, c) => (
                      <td
                        key={c}
                        className="border border-bambu-dark-tertiary px-2 py-1 text-bambu-gray-light whitespace-nowrap max-w-[280px] overflow-hidden text-ellipsis"
                        title={row[c] || undefined}
                      >
                        {row[c] ?? ''}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Truncation notices */}
        {(rowsTruncated || colsTruncated) && sheet && (
          <div className="px-4 py-2 border-t border-bambu-dark-tertiary text-xs text-bambu-gray flex gap-4 flex-shrink-0">
            {rowsTruncated && (
              <span>{t('fileManager.preview.truncatedRows', { shown: sheet.rows.length, total: sheet.totalRows })}</span>
            )}
            {colsTruncated && (
              <span>{t('fileManager.preview.truncatedCols', { shown: shownCols, total: sheet.totalCols })}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
