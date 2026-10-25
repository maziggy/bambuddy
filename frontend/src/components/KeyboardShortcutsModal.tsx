import { useEffect } from 'react';
import { X, Keyboard, ExternalLink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent } from './Card';

interface NavItem {
  id: string;
  to: string;
  labelKey: string;
}

interface SidebarItem {
  type: 'nav' | 'external';
  label: string;
  labelKey?: string;
}

interface KeyboardShortcutsModalProps {
  onClose: () => void;
  navItems?: NavItem[];
  sidebarItems?: SidebarItem[];
}

function getShortcuts(
  sidebarItems: SidebarItem[] | undefined,
  navItems: NavItem[] | undefined,
  t: (key: string) => string
) {
  // Use sidebarItems if provided (new format), otherwise fall back to navItems
  const navShortcuts = sidebarItems
    ? sidebarItems.slice(0, 9).map((item, index) => ({
        keys: [String(index + 1)],
        description: item.type === 'external'
          ? `Open ${item.label}`
          : `Go to ${item.labelKey ? t(item.labelKey) : item.label}`,
        isExternal: item.type === 'external',
      }))
    : navItems
    ? navItems.map((item, index) => ({
        keys: [String(index + 1)],
        description: `Go to ${t(item.labelKey)}`,
        isExternal: false,
      }))
    : [
        { keys: ['1'], description: 'Go to Printers', isExternal: false },
        { keys: ['2'], description: 'Go to Archives', isExternal: false },
        { keys: ['3'], description: 'Go to Queue', isExternal: false },
        { keys: ['4'], description: 'Go to Statistics', isExternal: false },
        { keys: ['5'], description: 'Go to Cloud Profiles', isExternal: false },
        { keys: ['6'], description: 'Go to Settings', isExternal: false },
      ];

  return [
    { category: 'Navigation', items: navShortcuts },
    { category: 'Archives', items: [
      { keys: ['/'], description: 'Focus search', isExternal: false },
      { keys: ['U'], description: 'Open upload modal', isExternal: false },
      { keys: ['Esc'], description: 'Clear selection / blur input', isExternal: false },
      { keys: ['Right-click'], description: 'Context menu on cards', isExternal: false },
    ]},
    { category: 'K-Profiles', items: [
      { keys: ['R'], description: 'Refresh profiles', isExternal: false },
      { keys: ['N'], description: 'New profile', isExternal: false },
      { keys: ['Esc'], description: 'Exit selection mode', isExternal: false },
    ]},
    { category: 'General', items: [
      { keys: ['C'], description: "Show a printer's camera", isExternal: false },
      { keys: ['?'], description: 'Show this help', isExternal: false },
    ]},
  ];
}

function KeyBadge({ children }: { children: string }) {
  return (
    <kbd className="px-2 py-1 text-xs font-mono bg-bambu-dark border border-bambu-dark-tertiary rounded text-white">
      {children}
    </kbd>
  );
}

export function KeyboardShortcutsModal({ onClose, navItems, sidebarItems }: KeyboardShortcutsModalProps) {
  const { t } = useTranslation();
  const shortcuts = getShortcuts(sidebarItems, navItems, t);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <Card className="w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <CardContent className="p-0">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <div className="flex items-center gap-2">
              <Keyboard className="w-5 h-5 text-bambu-green" />
              <h2 className="text-xl font-semibold text-white">Keyboard Shortcuts</h2>
            </div>
            <button
              onClick={onClose}
              className="text-bambu-gray hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Shortcuts List */}
          <div className="p-4 space-y-6 max-h-[60vh] overflow-y-auto">
            {shortcuts.map((section) => (
              <div key={section.category}>
                <h3 className="text-sm font-medium text-bambu-gray mb-3">{section.category}</h3>
                <div className="space-y-2">
                  {section.items.map((shortcut) => (
                    <div key={shortcut.description} className="flex items-center justify-between">
                      <span className="text-white text-sm flex items-center gap-1.5">
                        {shortcut.description}
                        {shortcut.isExternal && (
                          <ExternalLink className="w-3 h-3 text-bambu-gray" />
                        )}
                      </span>
                      <div className="flex gap-1">
                        {shortcut.keys.map((key) => (
                          <KeyBadge key={key}>{key}</KeyBadge>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Footer */}
          <div className="p-4 border-t border-bambu-dark-tertiary">
            <p className="text-xs text-bambu-gray text-center">
              Press <KeyBadge>Esc</KeyBadge> or click outside to close
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
