import { useState, useRef, useCallback, useEffect } from 'react';
import { getAuthToken } from '../api/client';

const API_BASE = '/api/v1';
const JPEG_SOI_HI = 0xff;
const JPEG_SOI_LO = 0xd8;
const JPEG_EOI_LO = 0xd9;

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
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  const generationRef = useRef(0);
  const onFirstFrameRef = useRef(onFirstFrame);
  const onErrorRef = useRef(onError);
  onFirstFrameRef.current = onFirstFrame;
  onErrorRef.current = onError;

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
        // Pre-allocated doubling buffer to avoid O(n²) copies
        let buf = new Uint8Array(256 * 1024);
        let writePos = 0;
        let firstFrameDelivered = false;

        while (true) {
          if (gen !== generationRef.current) break;

          const { done, value } = await reader.read();
          if (done) break;

          // Append chunk — grow by doubling when needed
          if (writePos + value.length > buf.length) {
            let newSize = buf.length;
            while (newSize < writePos + value.length) newSize *= 2;
            const next = new Uint8Array(newSize);
            next.set(buf.subarray(0, writePos));
            buf = next;
          }
          buf.set(value, writePos);
          writePos += value.length;

          // Extract complete JPEG frames
          while (writePos >= 4) {
            // Find SOI marker (0xFF 0xD8)
            let soiIdx = -1;
            for (let i = 0; i < writePos - 1; i++) {
              if (buf[i] === JPEG_SOI_HI && buf[i + 1] === JPEG_SOI_LO) {
                soiIdx = i;
                break;
              }
            }
            if (soiIdx === -1) {
              // Keep last byte (could be 0xFF)
              if (writePos > 1) {
                buf[0] = buf[writePos - 1];
                writePos = 1;
              }
              break;
            }

            // Trim before SOI
            if (soiIdx > 0) {
              buf.copyWithin(0, soiIdx, writePos);
              writePos -= soiIdx;
            }

            // Find EOI marker (0xFF 0xD9)
            let eoiIdx = -1;
            for (let i = 2; i < writePos - 1; i++) {
              if (buf[i] === JPEG_SOI_HI && buf[i + 1] === JPEG_EOI_LO) {
                eoiIdx = i;
                break;
              }
            }
            if (eoiIdx === -1) break; // Incomplete frame

            // Extract JPEG frame
            const frame = buf.slice(0, eoiIdx + 2);
            buf.copyWithin(0, eoiIdx + 2, writePos);
            writePos -= (eoiIdx + 2);

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
                onFirstFrameRef.current?.();
              }
            } catch {
              // Invalid JPEG — skip
            }
          }
        }

        // Stream ended naturally
        if (mountedRef.current && gen === generationRef.current) {
          setIsConnected(false);
          onErrorRef.current?.();
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        if (mountedRef.current && gen === generationRef.current) {
          setIsLoading(false);
          setHasError(true);
          setIsConnected(false);
          onErrorRef.current?.();
        }
      }
    })();
  }, [url, enabled, canvasRef, stopStream]);

  const restart = useCallback(() => {
    generationRef.current += 1;
    startStream(generationRef.current);
  }, [startStream]);

  // Start/stop based on enabled
  useEffect(() => {
    if (enabled) {
      generationRef.current += 1;
      startStream(generationRef.current);
    } else {
      stopStream();
      setIsConnected(false);
    }
    return () => {
      stopStream();
    };
  }, [enabled, url, startStream, stopStream]);

  return { isLoading, hasError, isConnected, restart, stop: stopStream };
}
