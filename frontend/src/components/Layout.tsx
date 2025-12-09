import { useState, useEffect, useCallback, useRef } from 'react';
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Printer, Archive, Calendar, BarChart3, Cloud, Settings, Sun, Moon, ChevronLeft, ChevronRight, Keyboard, Github, GripVertical, ArrowUpCircle, Wrench, X, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useTheme } from '../contexts/ThemeContext';
import { KeyboardShortcutsModal } from './KeyboardShortcutsModal';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

interface NavItem {
  id: string;
  to: string;
  icon: LucideIcon;
  labelKey: string; // Translation key
}

export const defaultNavItems: NavItem[] = [
  { id: 'printers', to: '/', icon: Printer, labelKey: 'nav.printers' },
  { id: 'archives', to: '/archives', icon: Archive, labelKey: 'nav.archives' },
  { id: 'queue', to: '/queue', icon: Calendar, labelKey: 'nav.queue' },
  { id: 'stats', to: '/stats', icon: BarChart3, labelKey: 'nav.stats' },
  { id: 'profiles', to: '/profiles', icon: Cloud, labelKey: 'nav.profiles' },
  { id: 'maintenance', to: '/maintenance', icon: Wrench, labelKey: 'nav.maintenance' },
  { id: 'settings', to: '/settings', icon: Settings, labelKey: 'nav.settings' },
];

// Get ordered nav items from localStorage
function getOrderedNavItems(): NavItem[] {
  const stored = localStorage.getItem('sidebarOrder');
  if (stored) {
    try {
      const order: string[] = JSON.parse(stored);
      const itemMap = new Map(defaultNavItems.map(item => [item.id, item]));
      const ordered: NavItem[] = [];
      for (const id of order) {
        const item = itemMap.get(id);
        if (item) {
          ordered.push(item);
          itemMap.delete(id);
        }
      }
      // Add any new items that weren't in the stored order
      for (const item of itemMap.values()) {
        ordered.push(item);
      }
      return ordered;
    } catch {
      return defaultNavItems;
    }
  }
  return defaultNavItems;
}

// Save nav item order to localStorage
function saveNavOrder(items: NavItem[]) {
  localStorage.setItem('sidebarOrder', JSON.stringify(items.map(i => i.id)));
}

// Get default view from localStorage
export function getDefaultView(): string {
  return localStorage.getItem('defaultView') || '/';
}

// Save default view to localStorage
export function setDefaultView(path: string) {
  localStorage.setItem('defaultView', path);
}

export function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { theme, toggleTheme } = useTheme();
  const { t } = useTranslation();
  const [sidebarExpanded, setSidebarExpanded] = useState(() => {
    const stored = localStorage.getItem('sidebarExpanded');
    return stored !== 'false';
  });
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [navItems, setNavItems] = useState<NavItem[]>(getOrderedNavItems);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const hasRedirected = useRef(false);
  const [dismissedUpdateVersion, setDismissedUpdateVersion] = useState<string | null>(() =>
    sessionStorage.getItem('dismissedUpdateVersion')
  );

  // Check for updates
  const { data: versionInfo } = useQuery({
    queryKey: ['version'],
    queryFn: api.getVersion,
    staleTime: Infinity,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });

  const { data: updateCheck } = useQuery({
    queryKey: ['updateCheck'],
    queryFn: api.checkForUpdates,
    enabled: settings?.check_updates !== false,
    staleTime: 60 * 60 * 1000, // 1 hour
    refetchInterval: 60 * 60 * 1000, // Check every hour
  });

  // Show update banner if update available and not dismissed for this version
  const showUpdateBanner = updateCheck?.update_available &&
    updateCheck.latest_version &&
    updateCheck.latest_version !== dismissedUpdateVersion;

  const dismissUpdateBanner = () => {
    if (updateCheck?.latest_version) {
      sessionStorage.setItem('dismissedUpdateVersion', updateCheck.latest_version);
      setDismissedUpdateVersion(updateCheck.latest_version);
    }
  };

  // Redirect to default view on initial load
  useEffect(() => {
    if (!hasRedirected.current && location.pathname === '/') {
      const defaultView = getDefaultView();
      if (defaultView !== '/') {
        hasRedirected.current = true;
        navigate(defaultView, { replace: true });
      }
    }
  }, [location.pathname, navigate]);

  useEffect(() => {
    localStorage.setItem('sidebarExpanded', String(sidebarExpanded));
  }, [sidebarExpanded]);

  // Drag and drop handlers
  const handleDragStart = (e: React.DragEvent, index: number) => {
    setDraggedIndex(index);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(index));
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverIndex(index);
  };

  const handleDragLeave = () => {
    setDragOverIndex(null);
  };

  const handleDrop = (e: React.DragEvent, dropIndex: number) => {
    e.preventDefault();
    if (draggedIndex === null || draggedIndex === dropIndex) {
      setDraggedIndex(null);
      setDragOverIndex(null);
      return;
    }

    const newItems = [...navItems];
    const [draggedItem] = newItems.splice(draggedIndex, 1);
    newItems.splice(dropIndex, 0, draggedItem);

    setNavItems(newItems);
    saveNavOrder(newItems);
    setDraggedIndex(null);
    setDragOverIndex(null);
  };

  const handleDragEnd = () => {
    setDraggedIndex(null);
    setDragOverIndex(null);
  };

  // Global keyboard shortcuts for navigation
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    const target = e.target as HTMLElement;
    // Ignore if typing in an input/textarea
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
      return;
    }

    // Number keys for navigation (1-6) - follows sidebar order
    if (!e.metaKey && !e.ctrlKey && !e.altKey) {
      const keyNum = parseInt(e.key);
      if (keyNum >= 1 && keyNum <= navItems.length) {
        e.preventDefault();
        navigate(navItems[keyNum - 1].to);
        return;
      }

      switch (e.key) {
        case '?':
          e.preventDefault();
          setShowShortcuts(true);
          break;
        case 'Escape':
          setShowShortcuts(false);
          break;
      }
    }
  }, [navigate, navItems]);

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
        <div className={`border-b border-bambu-dark-tertiary flex items-center justify-center ${sidebarExpanded ? 'p-4' : 'p-2'}`}>
          <img
            src={theme === 'dark' ? '/img/bambuddy_logo_dark.png' : '/img/bambuddy_logo_light.png'}
            alt="Bambuddy"
            className={sidebarExpanded ? 'h-16 w-auto' : 'h-8 w-8 object-cover object-left'}
          />
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-2">
          <ul className="space-y-2">
            {navItems.map(({ id, to, icon: Icon, labelKey }, index) => (
              <li
                key={id}
                draggable
                onDragStart={(e) => handleDragStart(e, index)}
                onDragOver={(e) => handleDragOver(e, index)}
                onDragLeave={handleDragLeave}
                onDrop={(e) => handleDrop(e, index)}
                onDragEnd={handleDragEnd}
                className={`relative ${
                  draggedIndex === index ? 'opacity-50' : ''
                } ${
                  dragOverIndex === index && draggedIndex !== index
                    ? 'before:absolute before:left-0 before:right-0 before:top-0 before:h-0.5 before:bg-bambu-green'
                    : ''
                }`}
              >
                <NavLink
                  to={to}
                  className={({ isActive }) =>
                    `flex items-center ${sidebarExpanded ? 'gap-3 px-4' : 'justify-center px-2'} py-3 rounded-lg transition-colors group ${
                      isActive
                        ? 'bg-bambu-green text-white'
                        : 'text-bambu-gray-light hover:bg-bambu-dark-tertiary hover:text-white'
                    }`
                  }
                  title={!sidebarExpanded ? t(labelKey) : undefined}
                >
                  {sidebarExpanded && (
                    <GripVertical className="w-4 h-4 flex-shrink-0 opacity-0 group-hover:opacity-50 cursor-grab active:cursor-grabbing -ml-1" />
                  )}
                  <Icon className="w-5 h-5 flex-shrink-0" />
                  {sidebarExpanded && <span>{t(labelKey)}</span>}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        {/* Collapse toggle */}
        <button
          onClick={() => setSidebarExpanded(!sidebarExpanded)}
          className="p-2 mx-2 mb-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white flex items-center justify-center"
          title={sidebarExpanded ? t('nav.collapseSidebar') : t('nav.expandSidebar')}
        >
          {sidebarExpanded ? (
            <ChevronLeft className="w-5 h-5" />
          ) : (
            <ChevronRight className="w-5 h-5" />
          )}
        </button>

        {/* Footer */}
        <div className="p-2 border-t border-bambu-dark-tertiary">
          {sidebarExpanded ? (
            <div className="flex items-center justify-between px-2">
              <div className="flex items-center gap-2">
                <span className="text-sm text-bambu-gray">v{versionInfo?.version || '...'}</span>
                {updateCheck?.update_available && (
                  <button
                    onClick={() => navigate('/settings')}
                    className="flex items-center gap-1 text-xs text-bambu-green hover:text-bambu-green/80 transition-colors"
                    title={t('nav.updateAvailable', { version: updateCheck.latest_version })}
                  >
                    <ArrowUpCircle className="w-4 h-4" />
                    <span>{t('nav.update')}</span>
                  </button>
                )}
              </div>
              <div className="flex items-center gap-1">
                <a
                  href="https://github.com/maziggy/bambuddy"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                  title={t('nav.viewOnGithub')}
                >
                  <Github className="w-5 h-5" />
                </a>
                <button
                  onClick={() => setShowShortcuts(true)}
                  className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                  title={t('nav.keyboardShortcuts')}
                >
                  <Keyboard className="w-5 h-5" />
                </button>
                <button
                  onClick={toggleTheme}
                  className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                  title={theme === 'dark' ? t('nav.switchToLight') : t('nav.switchToDark')}
                >
                  {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-1">
              {updateCheck?.update_available && (
                <button
                  onClick={() => navigate('/settings')}
                  className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-green hover:text-bambu-green/80"
                  title={t('nav.updateAvailable', { version: updateCheck.latest_version })}
                >
                  <ArrowUpCircle className="w-5 h-5" />
                </button>
              )}
              <a
                href="https://github.com/maziggy/bambuddy"
                target="_blank"
                rel="noopener noreferrer"
                className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                title={t('nav.viewOnGithub')}
              >
                <Github className="w-5 h-5" />
              </a>
              <button
                onClick={() => setShowShortcuts(true)}
                className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                title={t('nav.keyboardShortcuts')}
              >
                <Keyboard className="w-5 h-5" />
              </button>
              <button
                onClick={toggleTheme}
                className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                title={theme === 'dark' ? t('nav.switchToLight') : t('nav.switchToDark')}
              >
                {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className={`flex-1 bg-bambu-dark overflow-auto ${sidebarExpanded ? 'ml-64' : 'ml-16'} transition-all duration-300`}>
        {/* Persistent update banner */}
        {showUpdateBanner && (
          <div className="bg-bambu-green/20 border-b border-bambu-green/30 px-4 py-2 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm">
              <ArrowUpCircle className="w-4 h-4 text-bambu-green" />
              <span>
                {t('nav.updateAvailableBanner', {
                  version: updateCheck?.latest_version,
                  defaultValue: `Version ${updateCheck?.latest_version} is available!`
                })}
              </span>
              <button
                onClick={() => navigate('/settings')}
                className="text-bambu-green hover:text-bambu-green/80 font-medium underline"
              >
                {t('nav.viewUpdate', { defaultValue: 'View update' })}
              </button>
            </div>
            <button
              onClick={dismissUpdateBanner}
              className="p-1 hover:bg-bambu-dark-tertiary rounded transition-colors"
              title={t('common.dismiss', { defaultValue: 'Dismiss' })}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}
        <Outlet />
      </main>

      {/* Keyboard Shortcuts Modal */}
      {showShortcuts && <KeyboardShortcutsModal onClose={() => setShowShortcuts(false)} navItems={navItems} />}
    </div>
  );
}
