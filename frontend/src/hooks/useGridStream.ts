import { useState, useEffect, useRef, useCallback } from 'react';
import CameraGridDecoderWorker from '../workers/cameraGridDecoder.worker?worker';
import { getAuthToken } from '../api/client';
import { formatFileSize } from '../utils/file';

// Grid stream constants
const GRID_FRAME_HEADER_SIZE = 8;          // [4B printer_id LE][4B length LE]
const GRID_INITIAL_RECONNECT_DELAY = 2000; // 2s
const GRID_MAX_RECONNECT_DELAY = 30000;    // 30s
const STALE_CAMERA_TIMEOUT = 45_000;       // 45s without frames → mark as error
const STALE_FRAME_THRESHOLD = 3_000;       // 3s without frames → blur canvas

export interface GridStreamStats {
  bw: string;
  active: number;
  total: number;
  uptime: string;
}

const EMPTY_STATS: GridStreamStats = { bw: '', active: 0, total: 0, uptime: '' };

interface UseGridStreamOptions {
  printerIdsKey: string;
  gridParamsKey: string;
  restartKey: number;
}

interface UseGridStreamReturn {
  canvasRefs: React.MutableRefObject<Map<number, React.RefObject<HTMLCanvasElement | null>>>;
  loadingSet: Set<number>;
  errorSet: Set<number>;
  degradedSet: Set<number>;
  staleSet: Set<number>;
  reconnectingSet: Set<number>;
  reconnectCountdown: number;
  reconnectAttempt: number;
  subscribeStats: (cb: () => void) => () => void;
  getStatsSnapshot: () => GridStreamStats;
  handleVisibilityChange: (printerId: number, visible: boolean) => void;
}

export function useGridStream({ printerIdsKey, gridParamsKey, restartKey }: UseGridStreamOptions): UseGridStreamReturn {
  const canvasRefs = useRef<Map<number, React.RefObject<HTMLCanvasElement | null>>>(new Map());

  const [loadingSet, setLoadingSet] = useState<Set<number>>(new Set());
  const [errorSet, setErrorSet] = useState<Set<number>>(new Set());
  const [degradedSet, setDegradedSet] = useState<Set<number>>(new Set());
  const [staleSet, setStaleSet] = useState<Set<number>>(new Set());
  // Stats via ref + subscriber pattern (avoids re-rendering entire tree every 1s)
  const statsRef = useRef<GridStreamStats>(EMPTY_STATS);
  const statsSubscribers = useRef(new Set<() => void>());
  const subscribeStats = useCallback((cb: () => void) => {
    statsSubscribers.current.add(cb);
    return () => { statsSubscribers.current.delete(cb); };
  }, []);
  const getStatsSnapshot = useCallback(() => statsRef.current, []);

  // Reconnect state — per-printer set so individual cards clear as frames arrive
  const [reconnectingSet, setReconnectingSet] = useState<Set<number>>(new Set());
  const [reconnectCountdown, setReconnectCountdown] = useState(0);
  const reconnectAttemptsRef = useRef(0);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const countdownIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Mutable counters — updated in the stream loop, read by the stats interval
  const bytesRef = useRef(0);
  const activeCamsRef = useRef(new Set<number>());

  // Worker ref — one worker per grid stream lifetime (mutable for restart)
  const workerRef = useRef<Worker | null>(null);

  // Pipeline diagnostics — tracks frame flow through each stage
  const pipelineRef = useRef({
    chunksReceived: 0,
    framesParsed: 0,
    framesSentToWorker: 0,
    framesFromWorker: 0,
    framesDrawn: 0,
    workerDecodeErrors: 0,
    lastChunkTime: 0,
    lastWorkerFrameTime: 0,
    lastParseTime: 0,
    workerRestarts: 0,
    stallPingPending: false,
  });

  // Ensure refs exist for all connected printers, clean up stale ones
  useEffect(() => {
    const ids = printerIdsKey ? printerIdsKey.split(',').map(Number) : [];
    const connectedIds = new Set(ids);
    for (const id of ids) {
      if (!canvasRefs.current.has(id)) {
        canvasRefs.current.set(id, { current: null });
      }
    }
    for (const id of canvasRefs.current.keys()) {
      if (!connectedIds.has(id)) {
        canvasRefs.current.delete(id);
      }
    }
  }, [printerIdsKey]);

  // Visibility callback — forward to worker
  const handleVisibilityChange = useCallback((printerId: number, visible: boolean) => {
    workerRef.current?.postMessage({ type: 'visibility', printerId, visible });
  }, []);

  useEffect(() => {
    const ids = printerIdsKey ? printerIdsKey.split(',').map(Number) : [];
    if (ids.length === 0) return;

    setLoadingSet(new Set(ids));
    setErrorSet(new Set());
    setReconnectingSet(new Set());
    setReconnectCountdown(0);
    reconnectAttemptsRef.current = 0;
    setReconnectAttempt(0);

    bytesRef.current = 0;
    activeCamsRef.current = new Set();
    let t0 = performance.now();

    // Spin up worker for off-thread JPEG decoding
    let worker = new CameraGridDecoderWorker();
    workerRef.current = worker;

    // Seed worker with all printer IDs as visible — IntersectionObserver only
    // fires on changes, so already-visible cards won't re-notify a new worker.
    for (const id of ids) {
      worker.postMessage({ type: 'visibility', printerId: id, visible: true });
    }

    // Cache canvas 2D contexts — getContext is expensive to call per-frame
    const ctxCache = new Map<number, CanvasRenderingContext2D>();
    // Track canvas dimensions to avoid resetting every frame
    const dimCache = new Map<number, string>();
    // Track which printers have delivered at least one frame
    const loadedPrinters = new Set<number>();
    // Track last frame time per printer for stale camera detection
    const lastFrameTime = new Map<number, number>();
    // Throttle "canvas ref null" logging — once per printer per 5s
    const nullCanvasLogTime = new Map<number, number>();

    const pipeline = pipelineRef.current;

    // Named handler so we can reattach after worker restart
    function handleWorkerMessage(e: MessageEvent): void {
      const { type } = e.data;

      if (type === 'decodeError') {
        pipeline.workerDecodeErrors++;
        console.debug(
          `[grid] Worker decode error: printerId=${e.data.printerId} totalErrors=${e.data.totalErrors} totalSuccess=${e.data.totalSuccess} visible=${e.data.visibleCount}`,
        );
        return;
      }

      if (type === 'pong') {
        pipeline.stallPingPending = false;
        console.debug(
          `[grid] Worker pong: visible=${e.data.visibleCount} pending=${e.data.pendingCount} decoding=${e.data.decodingCount} errors=${e.data.totalDecodeErrors} success=${e.data.totalDecodeSuccess}`,
        );
        return;
      }

      if (type !== 'frame') return;
      const pid = e.data.printerId as number;
      const bitmap = e.data.bitmap as ImageBitmap;
      pipeline.framesFromWorker++;
      pipeline.lastWorkerFrameTime = performance.now();

      // A decoded frame proves this printer is alive — always update tracking
      // and clear error/reconnecting/loading state, even if canvas isn't mounted
      activeCamsRef.current.add(pid);
      lastFrameTime.set(pid, performance.now());

      setErrorSet(prev => {
        if (!prev.has(pid)) return prev;
        const next = new Set(prev);
        next.delete(pid);
        return next;
      });
      setReconnectingSet(prev => {
        if (!prev.has(pid)) return prev;
        const next = new Set(prev);
        next.delete(pid);
        return next;
      });
      if (!loadedPrinters.has(pid)) {
        loadedPrinters.add(pid);
        setLoadingSet(prev => {
          if (!prev.has(pid)) return prev;
          const next = new Set(prev);
          next.delete(pid);
          return next;
        });
      }

      // Draw to canvas if ref is available — otherwise just drop the bitmap
      const ref = canvasRefs.current.get(pid);
      const canvas = ref?.current;
      if (!canvas) {
        const now = performance.now();
        const lastLog = nullCanvasLogTime.get(pid) ?? 0;
        if (now - lastLog > 5000) {
          console.debug(`[grid] Canvas ref null for printer ${pid}`);
          nullCanvasLogTime.set(pid, now);
        }
        bitmap.close();
        return;
      }

      // Only reset canvas dimensions when they actually change
      const dimKey = `${bitmap.width}x${bitmap.height}`;
      if (dimCache.get(pid) !== dimKey) {
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        dimCache.set(pid, dimKey);
        ctxCache.delete(pid); // context invalidated
      }
      let ctx = ctxCache.get(pid);
      if (!ctx) {
        ctx = canvas.getContext('2d')!;
        ctxCache.set(pid, ctx);
      }
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close();
      pipeline.framesDrawn++;
    }

    worker.onmessage = handleWorkerMessage;

    let active = true;
    const controllerRef = { current: new AbortController() };
    let readerRef: ReadableStreamDefaultReader<Uint8Array> | null = null;

    // Compute stats every second
    const DEGRADED_THRESHOLD = 10_000; // 10s without frames → degraded
    const statsInterval = setInterval(() => {
      if (!active) return;
      const bytes = bytesRef.current;
      bytesRef.current = 0;
      const elapsed = Math.floor((performance.now() - t0) / 1000);
      const mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
      const ss = String(elapsed % 60).padStart(2, '0');

      statsRef.current = {
        bw: `${formatFileSize(bytes)}/s`,
        active: activeCamsRef.current.size,
        total: ids.length,
        uptime: `${mm}:${ss}`,
      };
      statsSubscribers.current.forEach(cb => cb());

      // Detect degraded / stale cameras based on last frame time
      const now = performance.now();
      const errorIds: number[] = [];
      const newDegraded = new Set<number>();
      const newStale = new Set<number>();
      for (const id of ids) {
        const last = lastFrameTime.get(id);
        if (!last || !loadedPrinters.has(id)) continue;
        const gap = now - last;
        if (gap > STALE_CAMERA_TIMEOUT) {
          errorIds.push(id);
          loadedPrinters.delete(id); // allow first-frame detection to recover it
        } else if (gap > DEGRADED_THRESHOLD) {
          newDegraded.add(id);
          newStale.add(id);
        } else if (gap > STALE_FRAME_THRESHOLD) {
          newStale.add(id);
        }
      }
      if (errorIds.length > 0) {
        setErrorSet(prev => {
          const next = new Set(prev);
          for (const id of errorIds) next.add(id);
          return next;
        });
      }
      setDegradedSet(prev => {
        if (prev.size === newDegraded.size && [...prev].every(id => newDegraded.has(id))) return prev;
        return newDegraded;
      });
      setStaleSet(prev => {
        if (prev.size === newStale.size && [...prev].every(id => newStale.has(id))) return prev;
        return newStale;
      });
    }, 1000);

    // Pipeline stats logging — every 10s
    let statsLogCounter = 0;
    const pipelineStatsInterval = setInterval(() => {
      if (!active) return;
      statsLogCounter++;
      if (statsLogCounter % 10 === 0) {
        console.debug(
          `[grid] Pipeline: chunks=${pipeline.chunksReceived} parsed=${pipeline.framesParsed} →worker=${pipeline.framesSentToWorker} ←worker=${pipeline.framesFromWorker} drawn=${pipeline.framesDrawn} errors=${pipeline.workerDecodeErrors} restarts=${pipeline.workerRestarts}`,
        );
      }
    }, 1000);

    // Worker health monitor — detect and recover from stalled worker
    const MAX_WORKER_RESTARTS = 3;
    const healthInterval = setInterval(() => {
      if (!active) return;
      const now = performance.now();
      const dataFlowing = pipeline.lastParseTime > 0 && (now - pipeline.lastParseTime) < 5000;
      const workerSilent =
        (pipeline.lastWorkerFrameTime > 0 && (now - pipeline.lastWorkerFrameTime) > 15000) ||
        (pipeline.framesSentToWorker > 20 && pipeline.framesFromWorker === 0);

      if (dataFlowing && workerSilent) {
        if (!pipeline.stallPingPending) {
          // First detection: ping the worker to check if it's alive
          pipeline.stallPingPending = true;
          console.debug(
            `[grid] Worker stall detected — pinging. sent=${pipeline.framesSentToWorker} received=${pipeline.framesFromWorker} lastWorkerFrame=${pipeline.lastWorkerFrameTime > 0 ? Math.round(now - pipeline.lastWorkerFrameTime) + 'ms ago' : 'never'}`,
          );
          workerRef.current?.postMessage({ type: 'ping' });
        } else if (pipeline.workerRestarts < MAX_WORKER_RESTARTS) {
          // Ping was sent but stall persists — restart the worker
          pipeline.workerRestarts++;
          console.debug(`[grid] Restarting worker (attempt ${pipeline.workerRestarts}/${MAX_WORKER_RESTARTS})`);

          worker.terminate();
          worker = new CameraGridDecoderWorker();
          workerRef.current = worker;
          worker.onmessage = handleWorkerMessage;

          // Re-seed visibility for all printer IDs
          for (const id of ids) {
            worker.postMessage({ type: 'visibility', printerId: id, visible: true });
          }

          // Reset worker-related pipeline counters
          pipeline.framesFromWorker = 0;
          pipeline.framesSentToWorker = 0;
          pipeline.workerDecodeErrors = 0;
          pipeline.lastWorkerFrameTime = 0;
          pipeline.stallPingPending = false;
        }
      } else {
        // Worker is healthy or no data flowing — reset stall tracking
        pipeline.stallPingPending = false;
      }
    }, 5000);

    async function startMultiplexedStream() {
      while (active) {
      // Growing buffer: pre-allocate and double when full
      let buf = new Uint8Array(256 * 1024);
      let bufLen = 0;

      // Single resettable stall timer — cancels the reader if no data arrives in 45s.
      // Declared outside try so catch can clear it on error (prevents leaked timers
      // from cancelling a new connection's reader after reconnect).
      let stallTimer: ReturnType<typeof setTimeout> | null = null;
      const resetStallTimer = () => {
        if (stallTimer !== null) clearTimeout(stallTimer);
        stallTimer = setTimeout(() => {
          readerRef?.cancel().catch(() => {});
        }, 45_000);
      };

      try {
        const gridHeaders: Record<string, string> = {};
        const token = getAuthToken();
        if (token) gridHeaders['Authorization'] = `Bearer ${token}`;
        const streamUrl = `/api/v1/printers/camera/grid-stream?ids=${ids.join(',')}`;
        const res = await fetch(streamUrl, { signal: controllerRef.current.signal, headers: gridHeaders });
        if (!res.ok || !res.body) {
          throw new Error(`HTTP ${res.status}`);
        }

        // Connection successful — reset reconnect state, uptime, and first-frame tracker
        const wasReconnecting = reconnectAttemptsRef.current > 0;
        reconnectAttemptsRef.current = 0;
        setReconnectAttempt(0);

        setReconnectingSet(new Set());
        setReconnectCountdown(0);
        t0 = performance.now();
        loadedPrinters.clear();
        if (!wasReconnecting) {
          // Only show per-camera loading spinners on initial connect
          setLoadingSet(new Set(ids));
        }
        setErrorSet(new Set());
        setDegradedSet(new Set());
        setStaleSet(new Set());

        // Reset pipeline counters on reconnect
        pipeline.chunksReceived = 0;
        pipeline.framesParsed = 0;
        pipeline.framesSentToWorker = 0;
        pipeline.framesFromWorker = 0;
        pipeline.framesDrawn = 0;
        pipeline.workerDecodeErrors = 0;
        pipeline.lastChunkTime = 0;
        pipeline.lastWorkerFrameTime = 0;
        pipeline.lastParseTime = 0;
        pipeline.stallPingPending = false;

        const reader = res.body.getReader();
        readerRef = reader;
        resetStallTimer();

        while (active) {
          const { done, value } = await reader.read();
          if (!active) break;
          resetStallTimer();
          if (done) break;

          // Grow buffer if needed
          if (bufLen + value.length > buf.length) {
            let newSize = buf.length;
            while (newSize < bufLen + value.length) newSize *= 2;
            const MAX_BUFFER_SIZE = 10 * 1024 * 1024; // 10 MB
            if (newSize > MAX_BUFFER_SIZE) {
              console.error('Grid stream: buffer exceeded 10 MB, resetting');
              bufLen = 0;
              break;
            }
            const newBuf = new Uint8Array(newSize);
            newBuf.set(buf.subarray(0, bufLen));
            buf = newBuf;
          }
          buf.set(value, bufLen);
          bufLen += value.length;
          bytesRef.current += value.length;

          // Pipeline: track chunks and detect data gaps
          const chunkNow = performance.now();
          if (pipeline.lastChunkTime > 0 && (chunkNow - pipeline.lastChunkTime) > 3000) {
            console.debug(
              `[grid] Data gap: ${Math.round(chunkNow - pipeline.lastChunkTime)}ms since last chunk`,
            );
          }
          pipeline.chunksReceived++;
          pipeline.lastChunkTime = chunkNow;

          // Parse binary frames: [4B printer_id LE][4B length LE][jpeg_data]
          let offset = 0;
          while (offset + GRID_FRAME_HEADER_SIZE <= bufLen) {
            const view = new DataView(buf.buffer, buf.byteOffset + offset, GRID_FRAME_HEADER_SIZE);
            const printerId = view.getUint32(0, true);
            const jpegLen = view.getUint32(4, true);

            // Sanity check: cap at 10 MB to prevent corrupt headers from growing the buffer unboundedly
            if (jpegLen > 10_000_000) {
              console.error(`Grid stream: corrupt frame header (jpegLen=${jpegLen}), resetting buffer`);
              bufLen = 0;
              break;
            }

            if (offset + GRID_FRAME_HEADER_SIZE + jpegLen > bufLen) break; // incomplete frame

            // Copy JPEG bytes into a standalone ArrayBuffer, then transfer to worker.
            // Using Uint8Array.slice() ensures the copy is detached from buf.buffer,
            // so the postMessage transfer won't risk detaching the shared read buffer.
            const jpeg = new Uint8Array(buf.buffer, buf.byteOffset + offset + GRID_FRAME_HEADER_SIZE, jpegLen).slice().buffer;
            lastFrameTime.set(printerId, performance.now());
            pipeline.framesParsed++;
            pipeline.framesSentToWorker++;
            pipeline.lastParseTime = performance.now();
            // Use workerRef so worker replacement (restart) takes effect immediately
            workerRef.current!.postMessage(
              { type: 'frame', printerId, jpeg },
              [jpeg], // transfer ownership — no second copy
            );
            offset += GRID_FRAME_HEADER_SIZE + jpegLen;
          }

          // Compact: shift remaining bytes to front
          if (offset > 0) {
            buf.copyWithin(0, offset, bufLen);
            bufLen -= offset;
          }
        }

        if (stallTimer !== null) clearTimeout(stallTimer);

        // Stream ended cleanly (server closed) — treat as recoverable
        if (active) throw new Error('Stream ended');
      } catch (e: unknown) {
        if (stallTimer !== null) clearTimeout(stallTimer);

        if (e instanceof DOMException && e.name === 'AbortError') return;
        if (!active) return;

        // --- Exponential backoff reconnect (no hard cap — never give up) ---
        const attempt = reconnectAttemptsRef.current;

        const delay = Math.min(
          GRID_INITIAL_RECONNECT_DELAY * Math.pow(2, attempt),
          GRID_MAX_RECONNECT_DELAY,
        );
        reconnectAttemptsRef.current = attempt + 1;
        setReconnectAttempt(attempt + 1);
        setReconnectingSet(new Set(ids));

        // Countdown timer
        let remaining = Math.ceil(delay / 1000);
        setReconnectCountdown(remaining);
        if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current);
        countdownIntervalRef.current = setInterval(() => {
          remaining -= 1;
          if (remaining <= 0) {
            if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current);
            countdownIntervalRef.current = null;
            setReconnectCountdown(0);
          } else {
            setReconnectCountdown(remaining);
          }
        }, 1000);

        // Wait then retry
        await new Promise(resolve => setTimeout(resolve, delay));
        if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current);
        countdownIntervalRef.current = null;

        if (!active) return;
        loadedPrinters.clear();
        // Don't reset loadingSet here — the reconnect overlay already shows
        // the user we're reconnecting. Individual loading spinners would flicker.
        controllerRef.current = new AbortController();
        readerRef = null;
      }
      } // end while(active)
    }

    startMultiplexedStream();

    const startupTimeout = setTimeout(() => {
      const stillLoading = ids.filter(id => !loadedPrinters.has(id));
      if (stillLoading.length > 0) {
        setErrorSet(prev => {
          const next = new Set(prev);
          for (const id of stillLoading) next.add(id);
          return next;
        });
      }
      setLoadingSet(new Set());
    }, STALE_CAMERA_TIMEOUT);

    const onBeforeUnload = () => controllerRef.current.abort();
    window.addEventListener('beforeunload', onBeforeUnload);

    return () => {
      active = false;
      readerRef?.cancel().catch(() => {});
      controllerRef.current.abort();
      clearInterval(statsInterval);
      clearInterval(pipelineStatsInterval);
      clearInterval(healthInterval);
      clearTimeout(startupTimeout);
      if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current);
      countdownIntervalRef.current = null;
      window.removeEventListener('beforeunload', onBeforeUnload);
      workerRef.current?.postMessage({ type: 'clear' });
      workerRef.current?.terminate();
      workerRef.current = null;
    };
  }, [printerIdsKey, gridParamsKey, restartKey]);

  return {
    canvasRefs,
    loadingSet,
    errorSet,
    degradedSet,
    staleSet,
    reconnectingSet,
    reconnectCountdown,
    reconnectAttempt,
    subscribeStats,
    getStatsSnapshot,
    handleVisibilityChange,
  };
}
