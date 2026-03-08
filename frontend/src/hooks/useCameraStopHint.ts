import { useEffect, useCallback } from 'react';
import { getAuthToken } from '../api/client';

/**
 * Sends a camera stop hint to the backend on unmount and beforeunload.
 * Prevents orphaned ffmpeg processes when the viewer navigates away.
 */
export function useCameraStopHint(printerId: number) {
  const sendStop = useCallback(() => {
    if (printerId <= 0) return;
    const headers: Record<string, string> = {};
    const token = getAuthToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    fetch(`/api/v1/printers/${printerId}/camera/stop`, {
      method: 'POST',
      keepalive: true,
      headers,
    }).catch(() => {});
  }, [printerId]);

  useEffect(() => {
    if (printerId <= 0) return;
    window.addEventListener('beforeunload', sendStop);
    return () => {
      window.removeEventListener('beforeunload', sendStop);
      sendStop();
    };
  }, [printerId, sendStop]);
}
