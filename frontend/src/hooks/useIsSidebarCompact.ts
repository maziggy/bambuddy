import { useState, useEffect } from 'react';

const SIDEBAR_COMPACT_BREAKPOINT = 1144;

export function useIsSidebarCompact(): boolean {
  const [isCompact, setIsCompact] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth < SIDEBAR_COMPACT_BREAKPOINT : false
  );

  useEffect(() => {
    const mediaQuery = window.matchMedia(`(max-width: ${SIDEBAR_COMPACT_BREAKPOINT - 1}px)`);

    const handleChange = (e: MediaQueryListEvent) => {
      setIsCompact(e.matches);
    };

    setIsCompact(mediaQuery.matches);

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, []);

  return isCompact;
}
