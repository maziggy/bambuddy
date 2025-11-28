import { useState, useCallback, useRef } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { Upload, X, File, CheckCircle, AlertCircle, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import type { BulkUploadResult } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface FileWithStatus {
  file: File;
  status: 'pending' | 'uploading' | 'success' | 'error';
  error?: string;
  archiveId?: number;
}

interface UploadModalProps {
  onClose: () => void;
  initialFiles?: File[];
}

export function UploadModal({ onClose, initialFiles }: UploadModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<FileWithStatus[]>(() =>
    initialFiles?.filter(f => f.name.endsWith('.3mf')).map(file => ({ file, status: 'pending' as const })) || []
  );
  const [isDragging, setIsDragging] = useState(false);
  const [selectedPrinter, setSelectedPrinter] = useState<number | undefined>();
  const [uploadResult, setUploadResult] = useState<BulkUploadResult | null>(null);

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const uploadMutation = useMutation({
    mutationFn: (filesToUpload: File[]) =>
      api.uploadArchivesBulk(filesToUpload, selectedPrinter),
    onSuccess: (result) => {
      setUploadResult(result);
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      queryClient.invalidateQueries({ queryKey: ['archiveStats'] });

      // Update file statuses based on result
      setFiles((prev) =>
        prev.map((f) => {
          const success = result.results.find((r) => r.filename === f.file.name);
          const error = result.errors.find((e) => e.filename === f.file.name);
          if (success) {
            return { ...f, status: 'success', archiveId: success.id };
          }
          if (error) {
            return { ...f, status: 'error', error: error.error };
          }
          return f;
        })
      );

      // Show toast
      if (result.failed === 0) {
        showToast(`${result.uploaded} file${result.uploaded !== 1 ? 's' : ''} uploaded`);
      } else if (result.uploaded === 0) {
        showToast(`Failed to upload ${result.failed} file${result.failed !== 1 ? 's' : ''}`, 'error');
      } else {
        showToast(`${result.uploaded} uploaded, ${result.failed} failed`, 'warning');
      }
    },
    onError: () => {
      setFiles((prev) =>
        prev.map((f) => ({ ...f, status: 'error', error: 'Upload failed' }))
      );
      showToast('Upload failed', 'error');
    },
  });

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);

    const droppedFiles = Array.from(e.dataTransfer.files).filter((f) =>
      f.name.endsWith('.3mf')
    );

    if (droppedFiles.length > 0) {
      setFiles((prev) => [
        ...prev,
        ...droppedFiles.map((file) => ({ file, status: 'pending' as const })),
      ]);
    }
  }, []);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(e.target.files || []).filter((f) =>
      f.name.endsWith('.3mf')
    );

    if (selectedFiles.length > 0) {
      setFiles((prev) => [
        ...prev,
        ...selectedFiles.map((file) => ({ file, status: 'pending' as const })),
      ]);
    }

    // Reset input so same file can be selected again
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, []);

  const removeFile = useCallback((index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleUpload = () => {
    if (files.length === 0) return;

    const pendingFiles = files.filter((f) => f.status === 'pending');
    if (pendingFiles.length === 0) return;

    setFiles((prev) =>
      prev.map((f) =>
        f.status === 'pending' ? { ...f, status: 'uploading' } : f
      )
    );

    uploadMutation.mutate(pendingFiles.map((f) => f.file));
  };

  const pendingCount = files.filter((f) => f.status === 'pending').length;
  const isUploading = uploadMutation.isPending;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-2xl max-h-[90vh] flex flex-col">
        <CardContent className="p-0 flex flex-col h-full">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <h2 className="text-xl font-semibold text-white">Upload 3MF Files</h2>
            <button
              onClick={onClose}
              className="text-bambu-gray hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Drop Zone */}
          <div className="p-4">
            <div
              className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
                isDragging
                  ? 'border-bambu-green bg-bambu-green/10'
                  : 'border-bambu-dark-tertiary hover:border-bambu-gray'
              }`}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            >
              <Upload className="w-12 h-12 mx-auto mb-4 text-bambu-gray" />
              <p className="text-white mb-2">
                Drag & drop .3mf files here
              </p>
              <p className="text-bambu-gray text-sm mb-4">or</p>
              <Button
                variant="secondary"
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading}
              >
                Browse Files
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".3mf"
                multiple
                className="hidden"
                onChange={handleFileSelect}
              />
            </div>
          </div>

          {/* Optional Printer Selection */}
          <div className="px-4 pb-4">
            <label className="block text-sm text-bambu-gray mb-2">
              Associate with printer (optional)
            </label>
            <select
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              value={selectedPrinter || ''}
              onChange={(e) =>
                setSelectedPrinter(e.target.value ? Number(e.target.value) : undefined)
              }
              disabled={isUploading}
            >
              <option value="">No printer</option>
              {printers?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          {/* File List */}
          {files.length > 0 && (
            <div className="px-4 pb-4 max-h-60 overflow-y-auto">
              <div className="space-y-2">
                {files.map((f, index) => (
                  <div
                    key={`${f.file.name}-${index}`}
                    className="flex items-center gap-3 p-3 bg-bambu-dark rounded-lg"
                  >
                    <File className="w-5 h-5 text-bambu-gray flex-shrink-0" />
                    <span className="flex-1 text-white text-sm truncate">
                      {f.file.name}
                    </span>
                    <span className="text-xs text-bambu-gray">
                      {(f.file.size / (1024 * 1024)).toFixed(1)} MB
                    </span>
                    {f.status === 'pending' && (
                      <button
                        onClick={() => removeFile(index)}
                        className="text-bambu-gray hover:text-red-400 transition-colors"
                        disabled={isUploading}
                      >
                        <X className="w-4 h-4" />
                      </button>
                    )}
                    {f.status === 'uploading' && (
                      <Loader2 className="w-4 h-4 text-bambu-green animate-spin" />
                    )}
                    {f.status === 'success' && (
                      <CheckCircle className="w-4 h-4 text-bambu-green" />
                    )}
                    {f.status === 'error' && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-red-400">{f.error}</span>
                        <AlertCircle className="w-4 h-4 text-red-400" />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Upload Result Summary */}
          {uploadResult && (
            <div className="px-4 pb-4">
              <div className="p-3 bg-bambu-dark rounded-lg">
                <p className="text-sm text-white">
                  <span className="text-bambu-green">{uploadResult.uploaded}</span> uploaded
                  {uploadResult.failed > 0 && (
                    <>, <span className="text-red-400">{uploadResult.failed}</span> failed</>
                  )}
                </p>
              </div>
            </div>
          )}

          {/* Footer */}
          <div className="flex gap-3 p-4 border-t border-bambu-dark-tertiary">
            <Button variant="secondary" onClick={onClose} className="flex-1">
              {uploadResult ? 'Close' : 'Cancel'}
            </Button>
            {!uploadResult && (
              <Button
                onClick={handleUpload}
                disabled={pendingCount === 0 || isUploading}
                className="flex-1"
              >
                {isUploading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Uploading...
                  </>
                ) : (
                  <>
                    <Upload className="w-4 h-4" />
                    Upload {pendingCount > 0 && `(${pendingCount})`}
                  </>
                )}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
