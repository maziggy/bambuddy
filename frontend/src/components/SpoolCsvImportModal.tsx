import { useState, useRef, type DragEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Upload, X, FileText, Loader2, CheckCircle, XCircle, MinusCircle, Wand2, AlertTriangle, Copy } from 'lucide-react';
import { api, type CsvImportPreview, type CsvImportRow } from '../api/client';
import { getSwatchStyle } from '../utils/colors';
import { Button } from './Button';

interface SpoolCsvImportModalProps {
  onClose: () => void;
  /** Called after a successful import so the page can refetch the inventory. */
  onImported: (created: number) => void;
}

/**
 * CSV import flow (#1576): pick a file → backend dry-run preview (per-row
 * valid/error/skipped, colours resolved) → user reviews → confirm imports only
 * the valid rows. Nothing is written until confirm.
 */
export function SpoolCsvImportModal({ onClose, onImported }: SpoolCsvImportModalProps) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [preview, setPreview] = useState<CsvImportPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadPreview = async (selected: File) => {
    setFile(selected);
    setPreview(null);
    setError(null);
    setLoading(true);
    try {
      const result = await api.importSpoolsCsvPreview(selected);
      setPreview(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('inventory.csv.previewError', 'Could not read the CSV file'));
    } finally {
      setLoading(false);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) loadPreview(selected);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) loadPreview(dropped);
  };

  const handleImport = async () => {
    if (!file) return;
    setImporting(true);
    setError(null);
    try {
      const result = await api.importSpoolsCsv(file);
      onImported(result.created);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('inventory.csv.importError', 'Import failed'));
      setImporting(false);
    }
  };

  const statusIcon = (status: CsvImportRow['status']) => {
    if (status === 'valid') return <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" />;
    if (status === 'error') return <XCircle className="w-4 h-4 text-red-500 flex-shrink-0" />;
    return <MinusCircle className="w-4 h-4 text-bambu-gray flex-shrink-0" />;
  };

  const validCount = preview?.valid_count ?? 0;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-3xl border border-bambu-dark-tertiary flex flex-col max-h-[90vh]">
        <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">{t('inventory.csv.modalTitle', 'Import spools from CSV')}</h2>
          <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded">
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        <div className="p-4 space-y-4 overflow-y-auto flex-1">
          {/* Drop zone / file picker */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={(e) => {
              e.preventDefault();
              setIsDragging(false);
            }}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors ${
              isDragging
                ? 'border-bambu-green bg-bambu-green/10'
                : 'border-bambu-dark-tertiary hover:border-bambu-green/50'
            }`}
          >
            <Upload className={`w-9 h-9 mx-auto mb-2 ${isDragging ? 'text-bambu-green' : 'text-bambu-gray'}`} />
            {file ? (
              <p className="text-white font-medium flex items-center justify-center gap-2">
                <FileText className="w-4 h-4" /> {file.name}
              </p>
            ) : (
              <>
                <p className="text-white font-medium">{t('inventory.csv.selectFile', 'Choose a CSV file or drag it here')}</p>
                <p className="text-xs text-bambu-gray/70 mt-1">{t('inventory.csv.dragHint', 'Header: material (required), brand, subtype, color_name, rgba, …')}</p>
              </>
            )}
          </div>
          <input ref={fileInputRef} type="file" accept=".csv,text/csv" className="hidden" onChange={handleFileSelect} />

          {loading && (
            <div className="flex items-center justify-center gap-2 text-bambu-gray py-4">
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('inventory.csv.parsing', 'Reading file…')}
            </div>
          )}

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg flex items-start gap-3">
              <XCircle className="w-5 h-5 text-red-400 mt-0.5 flex-shrink-0" />
              <p className="text-sm text-red-300 break-words">{error}</p>
            </div>
          )}

          {preview && (
            <>
              {/* Summary */}
              <div className="flex flex-wrap gap-3 text-sm">
                <span className="px-2 py-1 rounded bg-green-500/10 text-green-400">
                  {t('inventory.csv.validCount', '{{count}} valid', { count: preview.valid_count })}
                </span>
                <span className="px-2 py-1 rounded bg-red-500/10 text-red-400">
                  {t('inventory.csv.errorCount', '{{count}} error', { count: preview.error_count })}
                </span>
                <span className="px-2 py-1 rounded bg-bambu-dark text-bambu-gray">
                  {t('inventory.csv.skippedCount', '{{count}} skipped', { count: preview.skipped_count })}
                </span>
              </div>

              {preview.warnings.length > 0 && (
                <div className="p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg space-y-1">
                  {preview.warnings.map((w, i) => (
                    <p key={i} className="text-xs text-yellow-300">{w}</p>
                  ))}
                </div>
              )}

              {/* Preview table */}
              {preview.rows.length > 0 && (
                <div className="border border-bambu-dark-tertiary rounded-lg overflow-hidden">
                  <div className="max-h-72 overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-bambu-dark sticky top-0">
                        <tr className="text-left text-bambu-gray">
                          <th className="px-3 py-2 font-medium">{t('inventory.csv.colRow', 'Row')}</th>
                          <th className="px-3 py-2 font-medium">{t('inventory.csv.colStatus', 'Status')}</th>
                          <th className="px-3 py-2 font-medium">{t('inventory.material', 'Material')}</th>
                          <th className="px-3 py-2 font-medium">{t('inventory.brand', 'Brand')}</th>
                          <th className="px-3 py-2 font-medium">{t('inventory.csv.colColor', 'Color')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.rows.map((row) => (
                          <tr key={row.row_number} className="border-t border-bambu-dark-tertiary">
                            <td className="px-3 py-2 text-bambu-gray">{row.row_number}</td>
                            <td className="px-3 py-2">
                              <div className="flex items-center gap-1.5">
                                {statusIcon(row.status)}
                                {row.status === 'error' && row.reason && (
                                  <span className="text-xs text-red-400 break-words">{row.reason}</span>
                                )}
                              </div>
                            </td>
                            <td className="px-3 py-2 text-white">{row.material || '—'}</td>
                            <td className="px-3 py-2 text-white">{row.brand || '—'}</td>
                            <td className="px-3 py-2">
                              <div className="flex items-center gap-2">
                                {row.rgba && (
                                  <span
                                    className="inline-block w-4 h-4 rounded-full border border-bambu-dark-tertiary flex-shrink-0"
                                    style={getSwatchStyle(row.rgba)}
                                  />
                                )}
                                <span className="text-white">{row.color_name || '—'}</span>
                                {row.resolved_color && !row.cross_material_color && (
                                  <span title={t('inventory.csv.colorResolved', 'Color filled from catalog')}>
                                    <Wand2 className="w-3.5 h-3.5 text-bambu-green flex-shrink-0" />
                                  </span>
                                )}
                                {row.cross_material_color && (
                                  <span title={t('inventory.csv.colorCrossMaterial', 'Color taken from a different material — no exact match in catalog')}>
                                    <AlertTriangle className="w-3.5 h-3.5 text-yellow-500 flex-shrink-0" />
                                  </span>
                                )}
                                {row.duplicate_of_existing && (
                                  <span title={t('inventory.csv.duplicateExisting', 'A spool with this material, brand and color already exists — it will still be imported as a new spool')}>
                                    <Copy className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
                                  </span>
                                )}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <div className="p-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={importing}>
            {t('common.cancel')}
          </Button>
          <Button onClick={handleImport} disabled={!preview || validCount === 0 || importing}>
            {importing ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                {t('inventory.csv.importing', 'Importing…')}
              </>
            ) : validCount > 0 ? (
              t('inventory.csv.importValidRows', 'Import {{count}} valid rows', { count: validCount })
            ) : (
              t('inventory.csv.noValidRows', 'No valid rows')
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
