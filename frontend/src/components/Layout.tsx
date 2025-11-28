import { useState, useEffect, useCallback } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { Printer, Archive, BarChart3, Cloud, Settings, Sun, Moon, ChevronLeft, ChevronRight, Keyboard, Github } from 'lucide-react';
import { useTheme } from '../contexts/ThemeContext';
import { KeyboardShortcutsModal } from './KeyboardShortcutsModal';

const navItems = [
  { to: '/', icon: Printer, label: 'Printers' },
  { to: '/archives', icon: Archive, label: 'Archives' },
  { to: '/stats', icon: BarChart3, label: 'Statistics' },
  { to: '/cloud', icon: Cloud, label: 'Cloud Profiles' },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

export function Layout() {
  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();
  const [sidebarExpanded, setSidebarExpanded] = useState(() => {
    const stored = localStorage.getItem('sidebarExpanded');
    return stored !== 'false';
  });
  const [showShortcuts, setShowShortcuts] = useState(false);

  useEffect(() => {
    localStorage.setItem('sidebarExpanded', String(sidebarExpanded));
  }, [sidebarExpanded]);

  // Global keyboard shortcuts for navigation
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    const target = e.target as HTMLElement;
    // Ignore if typing in an input/textarea
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
      return;
    }

    // Number keys for navigation (1-4)
    if (!e.metaKey && !e.ctrlKey && !e.altKey) {
      switch (e.key) {
        case '1':
          e.preventDefault();
          navigate('/');
          break;
        case '2':
          e.preventDefault();
          navigate('/archives');
          break;
        case '3':
          e.preventDefault();
          navigate('/stats');
          break;
        case '4':
          e.preventDefault();
          navigate('/cloud');
          break;
        case '5':
          e.preventDefault();
          navigate('/settings');
          break;
        case '?':
          e.preventDefault();
          setShowShortcuts(true);
          break;
        case 'Escape':
          setShowShortcuts(false);
          break;
      }
    }
  }, [navigate]);

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside
        className={`${sidebarExpanded ? 'w-64' : 'w-16'} bg-bambu-dark-secondary border-r border-bambu-dark-tertiary flex flex-col fixed inset-y-0 left-0 z-30 transition-all duration-300`}
      >
        {/* Logo */}
        <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-center overflow-hidden">
          <div className={`${sidebarExpanded ? '' : 'w-10 h-10 overflow-hidden'}`}>
            <img
              src={theme === 'dark' ? '/img/bambusy_logo_dark.png' : '/img/bambusy_logo_light.png'}
              alt="Bambusy"
              className={sidebarExpanded ? 'h-16 w-auto' : 'h-10 w-auto max-w-none'}
            />
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-2">
          <ul className="space-y-2">
            {navItems.map(({ to, icon: Icon, label }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  className={({ isActive }) =>
                    `flex items-center ${sidebarExpanded ? 'gap-3 px-4' : 'justify-center px-2'} py-3 rounded-lg transition-colors ${
                      isActive
                        ? 'bg-bambu-green text-white'
                        : 'text-bambu-gray-light hover:bg-bambu-dark-tertiary hover:text-white'
                    }`
                  }
                  title={!sidebarExpanded ? label : undefined}
                >
                  <Icon className="w-5 h-5 flex-shrink-0" />
                  {sidebarExpanded && <span>{label}</span>}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        {/* Collapse toggle */}
        <button
          onClick={() => setSidebarExpanded(!sidebarExpanded)}
          className="p-2 mx-2 mb-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white flex items-center justify-center"
          title={sidebarExpanded ? 'Collapse sidebar' : 'Expand sidebar'}
        >
          {sidebarExpanded ? (
            <ChevronLeft className="w-5 h-5" />
          ) : (
            <ChevronRight className="w-5 h-5" />
          )}
        </button>

        {/* Footer */}
        <div className="p-2 border-t border-bambu-dark-tertiary">
          <div className={`flex items-center ${sidebarExpanded ? 'justify-between px-2' : 'flex-col gap-2'}`}>
            {sidebarExpanded && <span className="text-sm text-bambu-gray">v0.1.1</span>}
            <div className="flex items-center gap-1">
              <a
                href="https://github.com/maziggy/bambusy"
                target="_blank"
                rel="noopener noreferrer"
                className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                title="View on GitHub"
              >
                <Github className="w-5 h-5" />
              </a>
              <button
                onClick={() => setShowShortcuts(true)}
                className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                title="Keyboard shortcuts (?)"
              >
                <Keyboard className="w-5 h-5" />
              </button>
              <button
                onClick={toggleTheme}
                className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              >
                {theme === 'dark' ? (
                  <Sun className="w-5 h-5" />
                ) : (
                  <Moon className="w-5 h-5" />
                )}
              </button>
            </div>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className={`flex-1 bg-bambu-dark overflow-auto ${sidebarExpanded ? 'ml-64' : 'ml-16'} transition-all duration-300`}>
        <Outlet />
      </main>

      {/* Keyboard Shortcuts Modal */}
      {showShortcuts && <KeyboardShortcutsModal onClose={() => setShowShortcuts(false)} />}
    </div>
  );
}
