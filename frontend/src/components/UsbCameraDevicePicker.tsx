import { useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';

interface UsbCameraDevicePickerProps {
  printerName: string;
  /** The saved device path, '' when none is set. */
  value: string;
  onSelect: (device: string) => void;
  onEnterManually: () => void;
}

/** Picks a USB camera from the video devices the server can see. */
export function UsbCameraDevicePicker({ printerName, value, onSelect, onEnterManually }: UsbCameraDevicePickerProps) {
  const { t } = useTranslation();
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ['usb-cameras'],
    queryFn: api.getUsbCameras,
    // A camera plugged in since the last look should show up on the next one.
    staleTime: 0,
  });
  const cameras = data?.cameras ?? [];
  // A saved camera that is unplugged stays selected rather than being cleared.
  const missing = value !== '' && !isLoading && !cameras.some((camera) => camera.device === value);

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <select
          aria-label={`${t('settings.usbCameraDevice')}: ${printerName}`}
          value={value}
          onChange={(e) => onSelect(e.target.value)}
          disabled={isLoading}
          className="flex-1 min-w-0 px-2 py-1.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
        >
          {isLoading ? (
            <option value={value}>{t('settings.usbCameraSearching')}</option>
          ) : (
            <>
              {value === '' && <option value="" disabled>{t('settings.usbCameraSelect')}</option>}
              {cameras.map((camera) => (
                <option key={camera.device} value={camera.device}>
                  {camera.name} ({camera.device})
                </option>
              ))}
              {missing && <option value={value}>{t('settings.usbCameraNotFound', { device: value })}</option>}
            </>
          )}
        </select>
        <button
          type="button"
          onClick={() => refetch()}
          disabled={isFetching}
          aria-label={t('settings.usbCameraRefresh')}
          title={t('settings.usbCameraRefresh')}
          className="p-1.5 rounded text-bambu-gray hover:text-white disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${isFetching ? 'animate-spin' : ''}`} />
        </button>
      </div>
      {isError && <p className="text-xs text-red-400">{t('settings.usbCameraListFailed')}</p>}
      {data && cameras.length === 0 && (
        <p className="text-xs text-bambu-gray">
          {t('settings.usbCameraNoneFound')}{' '}
          <code className="text-bambu-green">devices: - /dev/video0:/dev/video0</code>
        </p>
      )}
      <button type="button" onClick={onEnterManually} className="text-xs text-bambu-green hover:underline">
        {t('settings.usbCameraEnterManually')}
      </button>
    </div>
  );
}
