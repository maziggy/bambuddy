import { useState, useEffect } from 'react';
import { X, ChevronLeft, ChevronRight, Download, Trash2 } from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';

interface PhotoGalleryModalProps {
  archiveId: number;
  archiveName: string;
  photos: string[];
  onClose: () => void;
  onDelete?: (filename: string) => void;
}

export function PhotoGalleryModal({
  archiveId,
  archiveName,
  photos,
  onClose,
  onDelete,
}: PhotoGalleryModalProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowLeft') setCurrentIndex((i) => Math.max(0, i - 1));
      if (e.key === 'ArrowRight') setCurrentIndex((i) => Math.min(photos.length - 1, i + 1));
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, photos.length]);

  // Reset index if photos change
  useEffect(() => {
    if (currentIndex >= photos.length) {
      setCurrentIndex(Math.max(0, photos.length - 1));
    }
  }, [photos.length, currentIndex]);

  if (photos.length === 0) {
    onClose();
    return null;
  }

  const currentPhoto = photos[currentIndex];
  const photoUrl = api.getArchivePhotoUrl(archiveId, currentPhoto);

  const handleDownload = () => {
    const link = document.createElement('a');
    link.href = photoUrl;
    link.download = `${archiveName}_photo_${currentIndex + 1}.jpg`;
    link.click();
  };

  const handleDelete = () => {
    if (onDelete) {
      setShowDeleteConfirm(true);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/90 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="relative w-full h-full flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 bg-black/50">
          <div>
            <h2 className="text-lg font-semibold text-white">{archiveName}</h2>
            <p className="text-sm text-bambu-gray">
              Photo {currentIndex + 1} of {photos.length}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={handleDownload}>
              <Download className="w-4 h-4" />
              Download
            </Button>
            {onDelete && (
              <Button variant="secondary" size="sm" onClick={handleDelete} className="text-red-400 hover:text-red-300">
                <Trash2 className="w-4 h-4" />
              </Button>
            )}
            <button
              onClick={onClose}
              className="p-2 text-bambu-gray hover:text-white transition-colors"
            >
              <X className="w-6 h-6" />
            </button>
          </div>
        </div>

        {/* Image */}
        <div className="flex-1 min-h-0 flex items-center justify-center p-4 relative overflow-hidden">
          {/* Previous button */}
          {currentIndex > 0 && (
            <button
              onClick={() => setCurrentIndex((i) => i - 1)}
              className="absolute left-4 z-10 p-3 bg-black/50 hover:bg-black/70 rounded-full transition-colors"
            >
              <ChevronLeft className="w-8 h-8 text-white" />
            </button>
          )}

          {/* Image */}
          <img
            src={photoUrl}
            alt={`Photo ${currentIndex + 1}`}
            className="max-w-full max-h-full object-contain rounded-lg"
            style={{ maxHeight: 'calc(100vh - 200px)' }}
          />

          {/* Next button */}
          {currentIndex < photos.length - 1 && (
            <button
              onClick={() => setCurrentIndex((i) => i + 1)}
              className="absolute right-4 z-10 p-3 bg-black/50 hover:bg-black/70 rounded-full transition-colors"
            >
              <ChevronRight className="w-8 h-8 text-white" />
            </button>
          )}
        </div>

        {/* Thumbnails */}
        {photos.length > 1 && (
          <div className="flex justify-center gap-2 p-4 bg-black/50">
            {photos.map((photo, index) => (
              <button
                key={photo}
                onClick={() => setCurrentIndex(index)}
                className={`w-16 h-16 rounded-lg overflow-hidden border-2 transition-colors ${
                  index === currentIndex
                    ? 'border-bambu-green'
                    : 'border-transparent hover:border-bambu-gray'
                }`}
              >
                <img
                  src={api.getArchivePhotoUrl(archiveId, photo)}
                  alt={`Thumbnail ${index + 1}`}
                  className="w-full h-full object-cover"
                />
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Delete Confirmation Modal */}
      {showDeleteConfirm && (
        <ConfirmModal
          title="Delete Photo"
          message="Delete this photo? This cannot be undone."
          confirmText="Delete"
          variant="danger"
          onConfirm={() => {
            onDelete?.(currentPhoto);
            setShowDeleteConfirm(false);
          }}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}
    </div>
  );
}
