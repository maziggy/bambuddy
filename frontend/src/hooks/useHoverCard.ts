import { useState, useRef, useEffect, useCallback } from 'react';

interface UseHoverCardOptions {
  showDelay?: number;
  hideDelay?: number;
  headerHeight?: number;
  keepOpen?: boolean;
}

interface UseHoverCardReturn {
  isVisible: boolean;
  setIsVisible: React.Dispatch<React.SetStateAction<boolean>>;
  position: 'top' | 'bottom';
  triggerRef: React.RefObject<HTMLDivElement | null>;
  cardRef: React.RefObject<HTMLDivElement | null>;
  handleMouseEnter: () => void;
  handleMouseLeave: () => void;
}

export function useHoverCard({
  showDelay = 80,
  hideDelay = 100,
  headerHeight = 56,
  keepOpen = false,
}: UseHoverCardOptions = {}): UseHoverCardReturn {
  const [isVisible, setIsVisible] = useState(false);
  const [position, setPosition] = useState<'top' | 'bottom'>('top');
  const triggerRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (isVisible && triggerRef.current && cardRef.current) {
      const triggerRect = triggerRef.current.getBoundingClientRect();
      const cardHeight = cardRef.current.offsetHeight;
      const spaceAbove = triggerRect.top - headerHeight;
      const spaceBelow = window.innerHeight - triggerRect.bottom;
      if (spaceAbove < cardHeight + 12 && spaceBelow > spaceAbove) {
        setPosition('bottom');
      } else {
        setPosition('top');
      }
    }
  }, [isVisible, headerHeight]);

  const handleMouseEnter = useCallback(() => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(true), showDelay);
  }, [showDelay]);

  const handleMouseLeave = useCallback(() => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    if (!keepOpen) {
      timeoutRef.current = setTimeout(() => setIsVisible(false), hideDelay);
    }
  }, [hideDelay, keepOpen]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  return {
    isVisible,
    setIsVisible,
    position,
    triggerRef,
    cardRef,
    handleMouseEnter,
    handleMouseLeave,
  };
}
