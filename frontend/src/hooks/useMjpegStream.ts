import { useState, useRef, useCallback, useEffect } from 'react';
import { getAuthToken } from '../api/client';

const API_BASE = '/api/v1';
const JPEG_SOI = 0xffd8;
const JPEG_EOI = 0xffd9;

interface UseMjpegStreamOptions {
  /** Stream URL path (relative to API_BASE, e.g. `/printers/1/camera/stream?fps=15`) */
  url: string;
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  enabled?: boolean;
  onFirstFrame?: () => void;
  onError?: () => void;
}

interface UseMjpegStreamReturn {
  isLoading: boolean;
  hasError: boolean;
  isConnected: boolean;
  restart: () => void;
  stop: () => void;
}

export function useMjpegStream({
  url,
  canvasRef,
  enabled = true,
  onFirstFrame,
  onError,
}: UseMjpegStreamOptions): UseMjpegStreamReturn {
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [isConnected, setIsConnected] = useState(false);

  const controllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const generationRef = useRef(0);

  const stopStream = useCallback(() => {
    if (controllerRef.current) {
      controllerRef.current.abort();
      controllerRef.current = null;
    }
  }, []);

  const startStream = useCallback((gen: number) => {
    stopStream();

    if (!enabled) return;

    const controller = new AbortController();
    controllerRef.current = controller;

    setIsLoading(true);
    setHasError(false);

    const fullUrl = `${API_BASE}${url}`;
    const headers: Record<string, string> = {};
    const token = getAuthToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    (async () => {
      try {
        const response = await fetch(fullUrl, {
          headers,
          signal: controller.signal,
          cache: 'no-store',
        });

        if (!response.ok || !response.body) {
          throw new Error(`Stream failed: ${response.status}`);
        }

        const reader = response.body.getReader();
        let buffer = new Uint8Array(0);
        let firstFrameDelivered = false;

        while (true) {
          if (gen !== generationRef.current) break;

          const { done, value } = await reader.read();
          if (done) break;

          // Append chunk to buffer
          const newBuf = new Uint8Array(buffer.length + value.length);
          newBuf.set(buffer);
          newBuf.set(value, buffer.length);
          buffer = newBuf;

          // Extract complete JPEG frames
          while (buffer.length >= 4) {
            // Find SOI marker (0xFF 0xD8)
            let soiIdx = -1;
            for (let i = 0; i < buffer.length - 1; i++) {
              if (buffer[i] === 0xff && buffer[i + 1] === (JPEG_SOI & 0xff)) {
                soiIdx = i;
                break;
              }
            }
            if (soiIdx === -1) {
              // Keep last byte (could be 0xFF)
              buffer = buffer.length > 1 ? buffer.slice(buffer.length - 1) : buffer;
              break;
            }

            // Trim before SOI
            if (soiIdx > 0) {
              buffer = buffer.slice(soiIdx);
            }

            // Find EOI marker (0xFF 0xD9)
            let eoiIdx = -1;
            for (let i = 2; i < buffer.length - 1; i++) {
              if (buffer[i] === 0xff && buffer[i + 1] === (JPEG_EOI & 0xff)) {
                eoiIdx = i;
                break;
              }
            }
            if (eoiIdx === -1) break; // Incomplete frame

            // Extract JPEG frame
            const frame = buffer.slice(0, eoiIdx + 2);
            buffer = buffer.slice(eoiIdx + 2);

            // Draw to canvas
            if (gen !== generationRef.current) break;
            try {
              const blob = new Blob([frame], { type: 'image/jpeg' });
              const bitmap = await createImageBitmap(blob);

              if (gen !== generationRef.current) {
                bitmap.close();
                break;
              }

              const canvas = canvasRef.current;
              if (canvas) {
                const ctx = canvas.getContext('2d');
                if (ctx) {
                  if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
                    canvas.width = bitmap.width;
                    canvas.height = bitmap.height;
                  }
                  ctx.drawImage(bitmap, 0, 0);
                }
              }
              bitmap.close();

              if (!firstFrameDelivered && mountedRef.current) {
                firstFrameDelivered = true;
                setIsLoading(false);
                setIsConnected(true);
                onFirstFrame?.();
              }
            } catch {
              // Invalid JPEG — skip
            }
          }
        }

        // Stream ended naturally
        if (mountedRef.current && gen === generationRef.current) {
          setIsConnected(false);
          onError?.();
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        if (mountedRef.current && gen === generationRef.current) {
          setIsLoading(false);
          setHasError(true);
          setIsConnected(false);
          onError?.();
        }
      }
    })();
  }, [url, enabled, canvasRef, onFirstFrame, onError, stopStream]);

  const restart = useCallback(() => {
    generationRef.current += 1;
    startStream(generationRef.current);
  }, [startStream]);

  // Start/stop based on enabled
  useEffect(() => {
    mountedRef.current = true;
    if (enabled) {
      generationRef.current += 1;
      startStream(generationRef.current);
    } else {
      stopStream();
      setIsConnected(false);
    }
    return () => {
      mountedRef.current = false;
      stopStream();
    };
  }, [enabled, url, startStream, stopStream]);

  return { isLoading, hasError, isConnected, restart, stop: stopStream };
}
