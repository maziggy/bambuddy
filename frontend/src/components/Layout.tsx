import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Printer, Archive, Calendar, BarChart3, Cloud, Settings, Sun, Moon, ChevronLeft, ChevronRight, Keyboard, Github, GripVertical, ArrowUpCircle, Wrench, FolderKanban, FolderOpen, X, Menu, Info, Plug, Bug, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useTheme } from '../contexts/ThemeContext';
import { KeyboardShortcutsModal } from './KeyboardShortcutsModal';
import { SwitchbarPopover } from './SwitchbarPopover';
import { useQuery } from '@tanstack/react-query';
import { api, supportApi, pendingUploadsApi } from '../api/client';
import { getIconByName } from './IconPicker';
import { useIsMobile } from '../hooks/useIsMobile';

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
  { id: 'projects', to: '/projects', icon: FolderKanban, labelKey: 'nav.projects' },
  { id: 'files', to: '/files', icon: FolderOpen, labelKey: 'nav.files' },
  { id: 'settings', to: '/settings', icon: Settings, labelKey: 'nav.settings' },
];

// Get unified sidebar order from localStorage
function getSidebarOrder(): string[] {
  const stored = localStorage.getItem('sidebarOrder');
  if (stored) {
    try {
      return JSON.parse(stored);
    } catch {
      return defaultNavItems.map(i => i.id);
    }
  }
  return defaultNavItems.map(i => i.id);
}

// Save unified sidebar order to localStorage
function saveSidebarOrder(order: string[]) {
  localStorage.setItem('sidebarOrder', JSON.stringify(order));
}

// Check if an ID is an external link
function isExternalLinkId(id: string): boolean {
  return id.startsWith('ext-');
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
  const { mode, toggleMode } = useTheme();
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [sidebarExpanded, setSidebarExpanded] = useState(() => {
    const stored = localStorage.getItem('sidebarExpanded');
    return stored !== 'false';
  });
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showSwitchbar, setShowSwitchbar] = useState(false);
  const [sidebarOrder, setSidebarOrder] = useState<string[]>(getSidebarOrder);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
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

  // Fetch external links for sidebar
  const { data: externalLinks } = useQuery({
    queryKey: ['external-links'],
    queryFn: api.getExternalLinks,
  });

  // Fetch smart plugs to check for switchbar items
  const { data: smartPlugs } = useQuery({
    queryKey: ['smart-plugs'],
    queryFn: api.getSmartPlugs,
    staleTime: 30 * 1000, // 30 seconds
  });

  const hasSwitchbarPlugs = smartPlugs?.some(p => p.show_in_switchbar) ?? false;

  // Check debug logging state
  const { data: debugLoggingState } = useQuery({
    queryKey: ['debugLogging'],
    queryFn: supportApi.getDebugLoggingState,
    staleTime: 60 * 1000, // 1 minute
    refetchInterval: 60 * 1000, // Refresh every minute
  });

  // Fetch pending queue items count for badge
  const { data: queueItems } = useQuery({
    queryKey: ['queue', 'pending'],
    queryFn: () => api.getQueue(undefined, 'pending'),
    staleTime: 5 * 1000, // 5 seconds
    refetchInterval: 5 * 1000, // Refresh every 5 seconds
    refetchOnWindowFocus: true,
  });
  const pendingQueueCount = queueItems?.length ?? 0;

  // Fetch pending uploads count for archive badge (virtual printer review items)
  const { data: pendingUploadsData } = useQuery({
    queryKey: ['pending-uploads', 'count'],
    queryFn: pendingUploadsApi.getCount,
    staleTime: 5 * 1000, // 5 seconds
    refetchInterval: 5 * 1000, // Refresh every 5 seconds
    refetchOnWindowFocus: true,
  });
  const pendingUploadsCount = pendingUploadsData?.count ?? 0;

  // Calculate debug duration client-side for real-time updates
  const [debugDuration, setDebugDuration] = useState<number | null>(null);
  useEffect(() => {
    if (!debugLoggingState?.enabled || !debugLoggingState.enabled_at) {
      setDebugDuration(null);
      return;
    }
    const enabledAt = new Date(debugLoggingState.enabled_at).getTime();
    const updateDuration = () => {
      setDebugDuration(Math.floor((Date.now() - enabledAt) / 1000));
    };
    updateDuration();
    const interval = setInterval(updateDuration, 1000);
    return () => clearInterval(interval);
  }, [debugLoggingState?.enabled, debugLoggingState?.enabled_at]);

  // Build the unified sidebar items list - memoized to prevent re-renders
  const navItemsMap = useMemo(() => new Map(defaultNavItems.map(item => [item.id, item])), []);
  const extLinksMap = useMemo(() => new Map((externalLinks || []).map(link => [`ext-${link.id}`, link])), [externalLinks]);

  // Compute the ordered sidebar: include stored order + any new items
  const orderedSidebarIds = (() => {
    const result: string[] = [];
    const seen = new Set<string>();

    // Add items in stored order
    for (const id of sidebarOrder) {
      if (navItemsMap.has(id) || extLinksMap.has(id)) {
        result.push(id);
        seen.add(id);
      }
    }

    // Add any new internal nav items not in stored order
    for (const item of defaultNavItems) {
      if (!seen.has(item.id)) {
        result.push(item.id);
        seen.add(item.id);
      }
    }

    // Add any new external links not in stored order
    for (const link of externalLinks || []) {
      const extId = `ext-${link.id}`;
      if (!seen.has(extId)) {
        result.push(extId);
        seen.add(extId);
      }
    }

    return result;
  })();

  // Unified drag handlers
  const handleDragStart = (e: React.DragEvent, id: string) => {
    setDraggedId(id);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', id);
  };

  const handleDragOver = (e: React.DragEvent, id: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverId(id);
  };

  const handleDragLeave = () => {
    setDragOverId(null);
  };

  const handleDrop = (e: React.DragEvent, targetId: string) => {
    e.preventDefault();
    if (draggedId === null || draggedId === targetId) {
      setDraggedId(null);
      setDragOverId(null);
      return;
    }

    const currentOrder = [...orderedSidebarIds];
    const draggedIndex = currentOrder.indexOf(draggedId);
    const targetIndex = currentOrder.indexOf(targetId);

    if (draggedIndex === -1 || targetIndex === -1) {
      setDraggedId(null);
      setDragOverId(null);
      return;
    }

    // Reorder
    currentOrder.splice(draggedIndex, 1);
    currentOrder.splice(targetIndex, 0, draggedId);

    // Save to localStorage and update state
    setSidebarOrder(currentOrder);
    saveSidebarOrder(currentOrder);

    setDraggedId(null);
    setDragOverId(null);
  };

  const handleDragEnd = () => {
    setDraggedId(null);
    setDragOverId(null);
  };

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

  // Close mobile drawer on navigation
  useEffect(() => {
    if (isMobile) {
      setMobileDrawerOpen(false);
    }
  }, [location.pathname, isMobile]);

  // Global keyboard shortcuts for navigation
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    const target = e.target as HTMLElement;
    // Ignore if typing in an input/textarea
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
      return;
    }

    // Number keys for navigation (1-9) - follows sidebar order including external links
    if (!e.metaKey && !e.ctrlKey && !e.altKey) {
      const keyNum = parseInt(e.key);
      if (keyNum >= 1 && keyNum <= orderedSidebarIds.length && keyNum <= 9) {
        const id = orderedSidebarIds[keyNum - 1];
        e.preventDefault();

        if (isExternalLinkId(id)) {
          // External link - navigate to iframe page
          const linkId = id.replace('ext-', '');
          navigate(`/external/${linkId}`);
        } else {
          // Internal nav item
          const navItem = navItemsMap.get(id);
          if (navItem) {
            navigate(navItem.to);
          }
        }
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
  }, [navigate, orderedSidebarIds, navItemsMap]);

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  return (
    <div className="flex min-h-screen">
      {/* Mobile Header */}
      {isMobile && (
        <header className="fixed top-0 left-0 right-0 z-40 h-14 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary flex items-center px-4">
          <button
            onClick={() => setMobileDrawerOpen(true)}
            className="p-2 -ml-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
            aria-label="Open menu"
          >
            <Menu className="w-6 h-6 text-white" />
          </button>
          <img
            src={mode === 'dark' ? '/img/bambuddy_logo_dark_transparent.png' : '/img/bambuddy_logo_light.png'}
            alt="Bambuddy"
            className="h-8 ml-3"
          />
        </header>
      )}

      {/* Mobile Drawer Backdrop */}
      {isMobile && mobileDrawerOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-40 transition-opacity"
          onClick={() => setMobileDrawerOpen(false)}
        />
      )}

      {/* Sidebar / Mobile Drawer */}
      <aside
        className={`bg-bambu-dark-secondary border-r border-bambu-dark-tertiary flex flex-col transition-all duration-300 ${
          isMobile
            ? `fixed inset-y-0 left-0 z-50 w-72 transform ${mobileDrawerOpen ? 'translate-x-0' : '-translate-x-full'}`
            : `fixed inset-y-0 left-0 z-30 ${sidebarExpanded ? 'w-64' : 'w-16'}`
        }`}
      >
        {/* Logo */}
        <div className={`border-b border-bambu-dark-tertiary flex items-center justify-center ${isMobile || sidebarExpanded ? 'p-4' : 'p-2'}`}>
          <img
            src={mode === 'dark' ? '/img/bambuddy_logo_dark_transparent.png' : '/img/bambuddy_logo_light.png'}
            alt="Bambuddy"
            className={isMobile || sidebarExpanded ? 'h-16 w-auto' : 'h-8 w-8 object-cover object-left'}
          />
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-2">
          <ul className="space-y-2">
            {orderedSidebarIds.map((id) => {
              const isExternal = isExternalLinkId(id);

              if (isExternal) {
                // Render external link
                const link = extLinksMap.get(id);
                if (!link) return null;

                const LinkIcon = link.custom_icon ? null : getIconByName(link.icon);
                return (
                  <li
                    key={id}
                    draggable
                    onDragStart={(e) => handleDragStart(e, id)}
                    onDragOver={(e) => handleDragOver(e, id)}
                    onDragLeave={handleDragLeave}
                    onDrop={(e) => handleDrop(e, id)}
                    onDragEnd={handleDragEnd}
                    className={`relative ${
                      draggedId === id ? 'opacity-50' : ''
                    } ${
                      dragOverId === id && draggedId !== id
                        ? 'before:absolute before:left-0 before:right-0 before:top-0 before:h-0.5 before:bg-bambu-green'
                        : ''
                    }`}
                  >
                    <NavLink
                      to={`/external/${link.id}`}
                      className={({ isActive }) =>
                        `flex items-center ${isMobile || sidebarExpanded ? 'gap-3 px-4' : 'justify-center px-2'} py-3 rounded-lg transition-colors group ${
                          isActive
                            ? 'bg-bambu-green text-white'
                            : 'text-bambu-gray-light hover:bg-bambu-dark-tertiary hover:text-white'
                        }`
                      }
                      title={!isMobile && !sidebarExpanded ? link.name : undefined}
                    >
                      {sidebarExpanded && !isMobile && (
                        <GripVertical className="w-4 h-4 flex-shrink-0 opacity-0 group-hover:opacity-50 cursor-grab active:cursor-grabbing -ml-1" />
                      )}
                      {link.custom_icon ? (
                        <img
                          src={`/api/v1/external-links/${link.id}/icon`}
                          alt=""
                          className={`w-5 h-5 flex-shrink-0 ${mode === 'dark' ? 'invert opacity-[0.65]' : 'opacity-60'}`}
                        />
                      ) : (
                        LinkIcon && <LinkIcon className="w-5 h-5 flex-shrink-0" />
                      )}
                      {(isMobile || sidebarExpanded) && <span>{link.name}</span>}
                    </NavLink>
                  </li>
                );
              } else {
                // Render internal nav item
                const navItem = navItemsMap.get(id);
                if (!navItem) return null;

                const { to, icon: Icon, labelKey } = navItem;
                const showQueueBadge = id === 'queue' && pendingQueueCount > 0;
                const showArchiveBadge = id === 'archives' && pendingUploadsCount > 0;
                const badgeCount = showQueueBadge ? pendingQueueCount : showArchiveBadge ? pendingUploadsCount : 0;
                const showBadge = showQueueBadge || showArchiveBadge;

                return (
                  <li
                    key={id}
                    draggable
                    onDragStart={(e) => handleDragStart(e, id)}
                    onDragOver={(e) => handleDragOver(e, id)}
                    onDragLeave={handleDragLeave}
                    onDrop={(e) => handleDrop(e, id)}
                    onDragEnd={handleDragEnd}
                    className={`relative ${
                      draggedId === id ? 'opacity-50' : ''
                    } ${
                      dragOverId === id && draggedId !== id
                        ? 'before:absolute before:left-0 before:right-0 before:top-0 before:h-0.5 before:bg-bambu-green'
                        : ''
                    }`}
                  >
                    <NavLink
                      to={to}
                      className={({ isActive }) =>
                        `flex items-center ${isMobile || sidebarExpanded ? 'gap-3 px-4' : 'justify-center px-2'} py-3 rounded-lg transition-colors group ${
                          isActive
                            ? 'bg-bambu-green text-white'
                            : 'text-bambu-gray-light hover:bg-bambu-dark-tertiary hover:text-white'
                        }`
                      }
                      title={!isMobile && !sidebarExpanded ? t(labelKey) : undefined}
                    >
                      {sidebarExpanded && !isMobile && (
                        <GripVertical className="w-4 h-4 flex-shrink-0 opacity-0 group-hover:opacity-50 cursor-grab active:cursor-grabbing -ml-1" />
                      )}
                      <div className="relative">
                        <Icon className="w-5 h-5 flex-shrink-0" />
                        {showBadge && (
                          <span className={`absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1 flex items-center justify-center text-[10px] font-bold rounded-full ${
                            showArchiveBadge ? 'bg-blue-500 text-white' : 'bg-yellow-500 text-black'
                          }`}>
                            {badgeCount > 99 ? '99+' : badgeCount}
                          </span>
                        )}
                      </div>
                      {(isMobile || sidebarExpanded) && <span>{t(labelKey)}</span>}
                    </NavLink>
                  </li>
                );
              }
            })}
          </ul>
        </nav>

        {/* Collapse toggle - hide on mobile */}
        {!isMobile && (
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
        )}

        {/* Footer */}
        <div className="p-2 border-t border-bambu-dark-tertiary">
          {isMobile || sidebarExpanded ? (
            <div className="flex flex-col gap-2 px-2">
              {/* Top row: icons */}
              <div className="flex items-center justify-center gap-1">
                {hasSwitchbarPlugs && (
                  <div className="relative">
                    <button
                      onMouseEnter={() => setShowSwitchbar(true)}
                      className={`p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors ${
                        showSwitchbar ? 'text-bambu-green' : 'text-bambu-gray-light hover:text-white'
                      }`}
                      title={t('nav.smartSwitches', { defaultValue: 'Smart Switches' })}
                    >
                      <Plug className="w-5 h-5" />
                    </button>
                    {showSwitchbar && (
                      <SwitchbarPopover onClose={() => setShowSwitchbar(false)} />
                    )}
                  </div>
                )}
                <NavLink
                  to="/system"
                  className={({ isActive }) =>
                    `p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors ${
                      isActive ? 'text-bambu-green' : 'text-bambu-gray-light hover:text-white'
                    }`
                  }
                  title={t('nav.system')}
                >
                  <Info className="w-5 h-5" />
                </NavLink>
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
                  onClick={toggleMode}
                  className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                  title={mode === 'dark' ? t('nav.switchToLight') : t('nav.switchToDark')}
                >
                  {mode === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
                </button>
              </div>
              {/* Bottom row: version */}
              <div className="flex items-center justify-center gap-2">
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
              {hasSwitchbarPlugs && (
                <div className="relative">
                  <button
                    onMouseEnter={() => setShowSwitchbar(true)}
                    className={`p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors ${
                      showSwitchbar ? 'text-bambu-green' : 'text-bambu-gray-light hover:text-white'
                    }`}
                    title={t('nav.smartSwitches', { defaultValue: 'Smart Switches' })}
                  >
                    <Plug className="w-5 h-5" />
                  </button>
                  {showSwitchbar && (
                    <SwitchbarPopover onClose={() => setShowSwitchbar(false)} />
                  )}
                </div>
              )}
              <NavLink
                to="/system"
                className={({ isActive }) =>
                  `p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors ${
                    isActive ? 'text-bambu-green' : 'text-bambu-gray-light hover:text-white'
                  }`
                }
                title={t('nav.system')}
              >
                <Info className="w-5 h-5" />
              </NavLink>
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
                onClick={toggleMode}
                className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
                title={mode === 'dark' ? t('nav.switchToLight') : t('nav.switchToDark')}
              >
                {mode === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className={`flex-1 bg-bambu-dark overflow-auto transition-all duration-300 ${
        isMobile ? 'mt-14' : sidebarExpanded ? 'ml-64' : 'ml-16'
      }`}>
        {/* Debug logging indicator */}
        {debugLoggingState?.enabled && (
          <div className="bg-amber-500/20 border-b border-amber-500/30 px-4 py-2 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm">
              <Bug className="w-4 h-4 text-amber-500 animate-pulse" />
              <span className="text-amber-200">
                {t('support.debugLoggingActive', { defaultValue: 'Debug logging is active' })}
                {debugDuration !== null && (
                  <span className="text-amber-300/70 ml-2">
                    ({Math.floor(debugDuration / 60)}m {debugDuration % 60}s)
                  </span>
                )}
              </span>
              <button
                onClick={() => navigate('/system')}
                className="text-amber-400 hover:text-amber-300 font-medium underline ml-2"
              >
                {t('support.manageLogs', { defaultValue: 'Manage' })}
              </button>
            </div>
          </div>
        )}
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
      {showShortcuts && (
        <KeyboardShortcutsModal
          onClose={() => setShowShortcuts(false)}
          sidebarItems={orderedSidebarIds.map(id => {
            if (isExternalLinkId(id)) {
              const extLink = extLinksMap.get(id);
              return extLink ? { type: 'external' as const, label: extLink.name } : null;
            } else {
              const navItem = navItemsMap.get(id);
              return navItem ? { type: 'nav' as const, label: navItem.labelKey, labelKey: navItem.labelKey } : null;
            }
          }).filter(Boolean) as { type: 'nav' | 'external'; label: string; labelKey?: string }[]}
        />
      )}
    </div>
  );
}
