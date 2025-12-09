import { useCallback, useRef } from 'react';

interface LongPressOptions {
  onLongPress: (e: React.TouchEvent | React.MouseEvent) => void;
  onClick?: () => void;
  delay?: number;
}

export function useLongPress({ onLongPress, onClick, delay = 500 }: LongPressOptions) {
  const timeoutRef = useRef<number | null>(null);
  const targetRef = useRef<EventTarget | null>(null);
  const longPressTriggered = useRef(false);

  const start = useCallback(
    (e: React.TouchEvent | React.MouseEvent) => {
      longPressTriggered.current = false;
      targetRef.current = e.target;
      timeoutRef.current = window.setTimeout(() => {
        longPressTriggered.current = true;
        onLongPress(e);
      }, delay);
    },
    [onLongPress, delay]
  );

  const clear = useCallback(
    (e: React.TouchEvent | React.MouseEvent, shouldTriggerClick = true) => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      if (shouldTriggerClick && !longPressTriggered.current && onClick && targetRef.current === e.target) {
        onClick();
      }
    },
    [onClick]
  );

  return {
    onMouseDown: start,
    onMouseUp: (e: React.MouseEvent) => clear(e, true),
    onMouseLeave: (e: React.MouseEvent) => clear(e, false),
    onTouchStart: start,
    onTouchEnd: (e: React.TouchEvent) => clear(e, true),
  };
}
