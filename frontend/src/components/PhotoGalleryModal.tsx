import { useState, useEffect } from 'react';
import { X, ChevronLeft, ChevronRight, Download, Trash2 } from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';

// Archive photos resolve through `archiveId`; any other owner (a library
// file, #3077) passes `getPhotoUrl` instead. Exactly one of the two is
// required so a caller cannot silently end up requesting archive 0.
type PhotoSource =
  | { archiveId: number; getPhotoUrl?: undefined }
  | { archiveId?: undefined; getPhotoUrl: (filename: string) => string };

type PhotoGalleryModalProps = PhotoSource & {
  archiveName: string;
  photos: string[];
  // Which photo to open on. A caller that opens the gallery from a per-photo
  // grid passes the clicked index (#3077); one with a single "view photos"
  // button leaves it at the first.
  initialIndex?: number;
  onClose: () => void;
  onDelete?: (filename: string) => void;
};

export function PhotoGalleryModal(props: PhotoGalleryModalProps) {
  const { archiveName, photos, initialIndex, onClose, onDelete } = props;
  const [currentIndex, setCurrentIndex] = useState(() =>
    Math.min(Math.max(initialIndex ?? 0, 0), Math.max(photos.length - 1, 0))
  );
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  // Keyboard navigation. Stands down while the delete confirmation is up:
  // that has its own Escape handler, so one press would cancel the prompt and
  // close the gallery underneath it, and arrow keys would move the selection
  // out from under a confirmation already naming a photo.
  useEffect(() => {
    if (showDeleteConfirm) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowLeft') setCurrentIndex((i) => Math.max(0, i - 1));
      if (e.key === 'ArrowRight') setCurrentIndex((i) => Math.min(photos.length - 1, i + 1));
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, photos.length, showDeleteConfirm]);

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

  const resolvePhotoUrl = (filename: string) =>
    props.getPhotoUrl ? props.getPhotoUrl(filename) : api.getArchivePhotoUrl(props.archiveId, filename);

  const currentPhoto = photos[currentIndex];
  const photoUrl = resolvePhotoUrl(currentPhoto);

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

  // The gallery can be rendered inside another modal's overlay, and that
  // overlay closes on a backdrop click (#3077). Dismissing the gallery must
  // not bubble up and take the parent — and its unsaved edits — with it.
  const handleBackdropClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onClose();
  };

  return (
    <div
      className="fixed inset-0 bg-black/90 flex items-center justify-center z-50"
      onClick={handleBackdropClick}
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
                  src={resolvePhotoUrl(photo)}
                  alt={`Thumbnail ${index + 1}`}
                  className="w-full h-full object-cover"
                />
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Delete Confirmation Modal. Wrapped so that cancelling it by clicking
          its backdrop cancels only the confirmation — the click would
          otherwise reach the gallery backdrop below it and close the gallery
          as well. */}
      {showDeleteConfirm && (
        <div onClick={(e) => e.stopPropagation()}>
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
        </div>
      )}
    </div>
  );
}
