import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react';

interface UsePageFileDropOptions {
  /** Called when files are dropped that pass the extension filter. */
  onFiles: (files: File[]) => void;
  /** Called when a drop event had files but none matched `extensions`. */
  onRejected?: () => void;
  /** Lowercase extensions including the dot (e.g. ['.3mf']). Omit to accept all. */
  extensions?: string[];
  /** Disable the drop zone entirely (e.g. when the user lacks upload permission). */
  disabled?: boolean;
}

interface UsePageFileDropResult {
  isDraggingOver: boolean;
  dragHandlers: {
    onDragOver: (e: DragEvent) => void;
    onDragEnter: (e: DragEvent) => void;
    onDragLeave: (e: DragEvent) => void;
    onDrop: (e: DragEvent) => void;
  };
}

/**
 * Page-wide drag-and-drop file zone. Survives the three cancel paths that
 * dragLeave alone misses: drag-out-of-window, Escape during drag, and drag
 * release outside any drop target. Each fix is captured by a separate test
 * case in usePageFileDrop.test.tsx.
 */
export function usePageFileDrop({
  onFiles,
  onRejected,
  extensions,
  disabled = false,
}: UsePageFileDropOptions): UsePageFileDropResult {
  const [isDraggingOver, setIsDraggingOver] = useState(false);

  const onFilesRef = useRef(onFiles);
  const onRejectedRef = useRef(onRejected);
  const extensionsRef = useRef(extensions);
  useEffect(() => { onFilesRef.current = onFiles; }, [onFiles]);
  useEffect(() => { onRejectedRef.current = onRejected; }, [onRejected]);
  useEffect(() => { extensionsRef.current = extensions; }, [extensions]);

  const handleDragOver = useCallback((e: DragEvent) => {
    if (disabled) return;
    e.preventDefault();
    if (e.dataTransfer.types.includes('Files')) {
      setIsDraggingOver(true);
    }
  }, [disabled]);

  const handleDragEnter = useCallback((e: DragEvent) => {
    if (disabled) return;
    e.preventDefault();
    if (e.dataTransfer.types.includes('Files')) {
      setIsDraggingOver(true);
    }
  }, [disabled]);

  const handleDragLeave = useCallback((e: DragEvent) => {
    if (disabled) return;
    e.preventDefault();
    const wrapper = e.currentTarget as Node;
    const next = e.relatedTarget as Node | null;
    if (!next || !wrapper.contains(next)) {
      setIsDraggingOver(false);
    }
  }, [disabled]);

  const handleDrop = useCallback((e: DragEvent) => {
    if (disabled) return;
    e.preventDefault();
    setIsDraggingOver(false);

    const all = Array.from(e.dataTransfer.files);
    if (all.length === 0) return;

    const exts = extensionsRef.current;
    const matched = exts && exts.length > 0
      ? all.filter(f => exts.some(ext => f.name.toLowerCase().endsWith(ext)))
      : all;

    if (matched.length > 0) {
      onFilesRef.current(matched);
    } else {
      onRejectedRef.current?.();
    }
  }, [disabled]);

  useEffect(() => {
    if (!isDraggingOver) return;
    const reset = () => setIsDraggingOver(false);
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') reset();
    };
    document.addEventListener('drop', reset);
    document.addEventListener('dragend', reset);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('drop', reset);
      document.removeEventListener('dragend', reset);
      document.removeEventListener('keydown', handleKey);
    };
  }, [isDraggingOver]);

  return {
    isDraggingOver,
    dragHandlers: {
      onDragOver: handleDragOver,
      onDragEnter: handleDragEnter,
      onDragLeave: handleDragLeave,
      onDrop: handleDrop,
    },
  };
}
