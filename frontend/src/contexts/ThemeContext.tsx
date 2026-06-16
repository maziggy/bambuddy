import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import { useAuth } from './AuthContext';

type ThemeMode = 'light' | 'dark' | 'system';
type ThemeStyle = 'classic' | 'glow' | 'vibrant';
type DarkBackground = 'neutral' | 'warm' | 'cool' | 'oled' | 'slate' | 'forest';
type LightBackground = 'neutral' | 'warm' | 'cool';
type ThemeAccent = 'green' | 'teal' | 'blue' | 'orange' | 'purple' | 'red';

interface ThemeContextType {
  mode: ThemeMode;
  resolvedMode: 'light' | 'dark';
  // Dark mode settings
  darkStyle: ThemeStyle;
  darkBackground: DarkBackground;
  darkAccent: ThemeAccent;
  // Light mode settings
  lightStyle: ThemeStyle;
  lightBackground: LightBackground;
  lightAccent: ThemeAccent;
  // Actions
  toggleMode: () => void;
  setMode: (mode: ThemeMode) => void;
  setDarkStyle: (style: ThemeStyle) => void;
  setDarkBackground: (background: DarkBackground) => void;
  setDarkAccent: (accent: ThemeAccent) => void;
  setLightStyle: (style: ThemeStyle) => void;
  setLightBackground: (background: LightBackground) => void;
  setLightAccent: (accent: ThemeAccent) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Auth-aware: read state from AuthContext so the initial getSettings()
  // sync waits until we know whether (a) auth is enabled and (b) there
  // is a logged-in user. Without this the fetch fires on the login page
  // and returns 401 — harmless (the catch swallows it) but noisy in the
  // network panel and wasteful. ThemeProvider is mounted inside
  // AuthProvider in App.tsx specifically so this hook is callable.
  const { authEnabled, user, loading: authLoading } = useAuth();

  // Mode
  const [mode, setModeState] = useState<ThemeMode>(() => {
    const stored = localStorage.getItem('theme-mode') as ThemeMode | null;
    const legacy = localStorage.getItem('theme') as ThemeMode | null;
    return stored || legacy || 'dark';
  });

  // System preference detection
  const [systemPreference, setSystemPreference] = useState<'light' | 'dark'>(() => {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => {
      setSystemPreference(e.matches ? 'dark' : 'light');
    };
    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, []);

  // Resolved mode: what's actually applied (always 'light' or 'dark')
  const resolvedMode: 'light' | 'dark' = mode === 'system' ? systemPreference : mode;

  // Dark mode settings
  const [darkStyle, setDarkStyleState] = useState<ThemeStyle>(() => {
    return (localStorage.getItem('dark-style') as ThemeStyle) || 'vibrant';
  });
  const [darkBackground, setDarkBackgroundState] = useState<DarkBackground>(() => {
    return (localStorage.getItem('dark-background') as DarkBackground) || 'cool';
  });
  const [darkAccent, setDarkAccentState] = useState<ThemeAccent>(() => {
    return (localStorage.getItem('dark-accent') as ThemeAccent) || 'green';
  });

  // Light mode settings
  const [lightStyle, setLightStyleState] = useState<ThemeStyle>(() => {
    return (localStorage.getItem('light-style') as ThemeStyle) || 'classic';
  });
  const [lightBackground, setLightBackgroundState] = useState<LightBackground>(() => {
    return (localStorage.getItem('light-background') as LightBackground) || 'neutral';
  });
  const [lightAccent, setLightAccentState] = useState<ThemeAccent>(() => {
    return (localStorage.getItem('light-accent') as ThemeAccent) || 'green';
  });

  // Sync from API once auth state is known. Same gate shape as
  // useStreamTokenSync / ColorCatalogProvider: wait for AuthContext to
  // settle, then only fetch when we can actually expect a 200 (auth
  // disabled, or auth enabled with a logged-in user).
  useEffect(() => {
    if (authLoading) return;
    if (authEnabled && user === null) return;
    api.getSettings().then((settings) => {
      // Dark settings
      if (settings.dark_style) {
        setDarkStyleState(settings.dark_style as ThemeStyle);
        localStorage.setItem('dark-style', settings.dark_style);
      }
      if (settings.dark_background) {
        setDarkBackgroundState(settings.dark_background as DarkBackground);
        localStorage.setItem('dark-background', settings.dark_background);
      }
      if (settings.dark_accent) {
        setDarkAccentState(settings.dark_accent as ThemeAccent);
        localStorage.setItem('dark-accent', settings.dark_accent);
      }
      // Light settings
      if (settings.light_style) {
        setLightStyleState(settings.light_style as ThemeStyle);
        localStorage.setItem('light-style', settings.light_style);
      }
      if (settings.light_background) {
        setLightBackgroundState(settings.light_background as LightBackground);
        localStorage.setItem('light-background', settings.light_background);
      }
      if (settings.light_accent) {
        setLightAccentState(settings.light_accent as ThemeAccent);
        localStorage.setItem('light-accent', settings.light_accent);
      }
    }).catch(() => {});
    // Re-fetch when auth state transitions (e.g. login completes); the
    // gate above short-circuits subsequent calls once we already have a
    // valid sync.
  }, [authLoading, authEnabled, user]);

  // Apply theme classes based on current mode
  useEffect(() => {
    const root = document.documentElement;

    // Remove all theme classes
    root.classList.remove(
      'dark',
      'style-classic', 'style-glow', 'style-vibrant',
      'bg-neutral', 'bg-warm', 'bg-cool', 'bg-oled', 'bg-slate', 'bg-forest',
      'accent-green', 'accent-teal', 'accent-blue', 'accent-orange', 'accent-purple', 'accent-red'
    );

    // Apply based on resolved mode
    if (resolvedMode === 'dark') {
      root.classList.add('dark');
      root.classList.add(`style-${darkStyle}`);
      root.classList.add(`bg-${darkBackground}`);
      root.classList.add(`accent-${darkAccent}`);
    } else {
      root.classList.add(`style-${lightStyle}`);
      root.classList.add(`bg-${lightBackground}`);
      root.classList.add(`accent-${lightAccent}`);
    }

    localStorage.setItem('theme-mode', mode);
    localStorage.removeItem('theme');
  }, [mode, resolvedMode, darkStyle, darkBackground, darkAccent, lightStyle, lightBackground, lightAccent]);

  const toggleMode = () => setModeState(prev => {
    if (prev === 'dark') return 'light';
    if (prev === 'light') return 'system';
    return 'dark';
  });
  const setMode = (m: ThemeMode) => setModeState(m);

  // Dark setters
  const setDarkStyle = (v: ThemeStyle) => {
    setDarkStyleState(v);
    localStorage.setItem('dark-style', v);
    api.updateSettings({ dark_style: v }).catch(() => {});
  };
  const setDarkBackground = (v: DarkBackground) => {
    setDarkBackgroundState(v);
    localStorage.setItem('dark-background', v);
    api.updateSettings({ dark_background: v }).catch(() => {});
  };
  const setDarkAccent = (v: ThemeAccent) => {
    setDarkAccentState(v);
    localStorage.setItem('dark-accent', v);
    api.updateSettings({ dark_accent: v }).catch(() => {});
  };

  // Light setters
  const setLightStyle = (v: ThemeStyle) => {
    setLightStyleState(v);
    localStorage.setItem('light-style', v);
    api.updateSettings({ light_style: v }).catch(() => {});
  };
  const setLightBackground = (v: LightBackground) => {
    setLightBackgroundState(v);
    localStorage.setItem('light-background', v);
    api.updateSettings({ light_background: v }).catch(() => {});
  };
  const setLightAccent = (v: ThemeAccent) => {
    setLightAccentState(v);
    localStorage.setItem('light-accent', v);
    api.updateSettings({ light_accent: v }).catch(() => {});
  };

  return (
    <ThemeContext.Provider value={{
      mode,
      resolvedMode,
      darkStyle, darkBackground, darkAccent,
      lightStyle, lightBackground, lightAccent,
      toggleMode, setMode,
      setDarkStyle, setDarkBackground, setDarkAccent,
      setLightStyle, setLightBackground, setLightAccent,
    }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error('useTheme must be used within ThemeProvider');
  return context;
}

export type { ThemeMode, ThemeStyle, DarkBackground, LightBackground, ThemeAccent };
