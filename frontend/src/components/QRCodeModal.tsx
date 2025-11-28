import { X, Download } from 'lucide-react';
import { Button } from './Button';
import { api } from '../api/client';

interface QRCodeModalProps {
  archiveId: number;
  archiveName: string;
  onClose: () => void;
}

export function QRCodeModal({ archiveId, archiveName, onClose }: QRCodeModalProps) {
  const qrCodeUrl = api.getArchiveQRCodeUrl(archiveId, 300);

  const handleDownload = () => {
    const link = document.createElement('a');
    link.href = qrCodeUrl;
    link.download = `${archiveName}_qrcode.png`;
    link.click();
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-sm"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">QR Code</h2>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 flex flex-col items-center">
          <p className="text-sm text-bambu-gray mb-4 text-center truncate max-w-full">
            {archiveName}
          </p>
          <div className="bg-white p-4 rounded-lg mb-4">
            <img
              src={qrCodeUrl}
              alt="QR Code"
              className="w-64 h-64"
            />
          </div>
          <p className="text-xs text-bambu-gray mb-4 text-center">
            Scan to open this archive
          </p>
          <Button onClick={handleDownload} className="w-full">
            <Download className="w-4 h-4" />
            Download QR Code
          </Button>
        </div>
      </div>
    </div>
  );
}
