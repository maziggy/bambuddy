import { useState, useCallback, useRef, type RefObject } from 'react';

interface UseZoomPanOptions {
  containerRef: RefObject<HTMLElement | null>;
  defaultMaxPan?: { x: number; y: number };
}

export function useZoomPan({ containerRef, defaultMaxPan = { x: 300, y: 200 } }: UseZoomPanOptions) {
  const [zoomLevel, setZoomLevel] = useState(1);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const [lastTouchDistance, setLastTouchDistance] = useState<number | null>(null);
  const [lastTouchCenter, setLastTouchCenter] = useState<{ x: number; y: number } | null>(null);
  // Cache container dimensions during gestures to avoid forced reflows on every touch move
  const cachedContainerSize = useRef<{ width: number; height: number } | null>(null);

  const handleZoomIn = useCallback(() => {
    setZoomLevel(prev => Math.min(prev + 0.5, 4));
  }, []);

  const handleZoomOut = useCallback(() => {
    setZoomLevel(prev => {
      const newZoom = Math.max(prev - 0.5, 1);
      if (newZoom === 1) setPanOffset({ x: 0, y: 0 });
      return newZoom;
    });
  }, []);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    if (e.deltaY < 0) {
      handleZoomIn();
    } else {
      handleZoomOut();
    }
  }, [handleZoomIn, handleZoomOut]);

  const handleMouseDown = (e: React.MouseEvent) => {
    if (zoomLevel > 1) {
      e.preventDefault();
      setIsPanning(true);
      setPanStart({ x: e.clientX - panOffset.x, y: e.clientY - panOffset.y });
    }
  };

  const measureContainer = useCallback(() => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    cachedContainerSize.current = { width: rect.width, height: rect.height };
  }, [containerRef]);

  const getMaxPan = useCallback(() => {
    const size = cachedContainerSize.current;
    if (!size) {
      if (!containerRef.current) return defaultMaxPan;
      const rect = containerRef.current.getBoundingClientRect();
      const maxX = (rect.width * (zoomLevel - 1)) / 2;
      const maxY = (rect.height * (zoomLevel - 1)) / 2;
      return { x: Math.max(50, maxX), y: Math.max(50, maxY) };
    }
    const maxX = (size.width * (zoomLevel - 1)) / 2;
    const maxY = (size.height * (zoomLevel - 1)) / 2;
    return { x: Math.max(50, maxX), y: Math.max(50, maxY) };
  }, [containerRef, defaultMaxPan, zoomLevel]);

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isPanning && zoomLevel > 1) {
      const newX = e.clientX - panStart.x;
      const newY = e.clientY - panStart.y;
      const maxPan = getMaxPan();
      setPanOffset({
        x: Math.max(-maxPan.x, Math.min(maxPan.x, newX)),
        y: Math.max(-maxPan.y, Math.min(maxPan.y, newY)),
      });
    }
  };

  const handleMouseUp = useCallback(() => {
    setIsPanning(false);
  }, []);

  const getTouchDistance = (touches: React.TouchList) => {
    if (touches.length < 2) return 0;
    const dx = touches[0].clientX - touches[1].clientX;
    const dy = touches[0].clientY - touches[1].clientY;
    return Math.sqrt(dx * dx + dy * dy);
  };

  const getTouchCenter = (touches: React.TouchList) => {
    if (touches.length < 2) {
      return { x: touches[0].clientX, y: touches[0].clientY };
    }
    return {
      x: (touches[0].clientX + touches[1].clientX) / 2,
      y: (touches[0].clientY + touches[1].clientY) / 2,
    };
  };

  const handleTouchStart = (e: React.TouchEvent) => {
    measureContainer();
    if (e.touches.length === 2) {
      e.preventDefault();
      setLastTouchDistance(getTouchDistance(e.touches));
      setLastTouchCenter(getTouchCenter(e.touches));
    } else if (e.touches.length === 1 && zoomLevel > 1) {
      e.preventDefault();
      setIsPanning(true);
      setPanStart({
        x: e.touches[0].clientX - panOffset.x,
        y: e.touches[0].clientY - panOffset.y,
      });
    }
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    if (e.touches.length === 2 && lastTouchDistance !== null) {
      e.preventDefault();
      const newDistance = getTouchDistance(e.touches);
      const scale = newDistance / lastTouchDistance;

      setZoomLevel(prev => {
        const newZoom = Math.max(1, Math.min(4, prev * scale));
        if (newZoom === 1) {
          setPanOffset({ x: 0, y: 0 });
        }
        return newZoom;
      });

      setLastTouchDistance(newDistance);

      const newCenter = getTouchCenter(e.touches);
      if (lastTouchCenter) {
        const maxPan = getMaxPan();
        setPanOffset(prev => ({
          x: Math.max(-maxPan.x, Math.min(maxPan.x, prev.x + (newCenter.x - lastTouchCenter.x))),
          y: Math.max(-maxPan.y, Math.min(maxPan.y, prev.y + (newCenter.y - lastTouchCenter.y))),
        }));
      }
      setLastTouchCenter(newCenter);
    } else if (e.touches.length === 1 && isPanning && zoomLevel > 1) {
      e.preventDefault();
      const newX = e.touches[0].clientX - panStart.x;
      const newY = e.touches[0].clientY - panStart.y;
      const maxPan = getMaxPan();
      setPanOffset({
        x: Math.max(-maxPan.x, Math.min(maxPan.x, newX)),
        y: Math.max(-maxPan.y, Math.min(maxPan.y, newY)),
      });
    }
  };

  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    if (e.touches.length < 2) {
      setLastTouchDistance(null);
      setLastTouchCenter(null);
    }
    if (e.touches.length === 0) {
      setIsPanning(false);
      cachedContainerSize.current = null;
    }
  }, []);

  const resetZoom = useCallback(() => {
    setZoomLevel(1);
    setPanOffset({ x: 0, y: 0 });
  }, []);

  const transformStyle = {
    transform: `scale(${zoomLevel}) translate(${panOffset.x / zoomLevel}px, ${panOffset.y / zoomLevel}px)`,
    cursor: zoomLevel > 1 ? (isPanning ? 'grabbing' : 'grab') : 'default',
  };

  return {
    zoomLevel,
    setZoomLevel,
    panOffset,
    setPanOffset,
    isPanning,
    handleZoomIn,
    handleZoomOut,
    handleWheel,
    handleMouseDown,
    handleMouseMove,
    handleMouseUp,
    handleTouchStart,
    handleTouchMove,
    handleTouchEnd,
    resetZoom,
    transformStyle,
  };
}
