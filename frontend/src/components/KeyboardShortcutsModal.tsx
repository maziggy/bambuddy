import { X, Keyboard } from 'lucide-react';
import { Card, CardContent } from './Card';

interface KeyboardShortcutsModalProps {
  onClose: () => void;
}

const shortcuts = [
  { category: 'Navigation', items: [
    { keys: ['1'], description: 'Go to Printers' },
    { keys: ['2'], description: 'Go to Archives' },
    { keys: ['3'], description: 'Go to Statistics' },
    { keys: ['4'], description: 'Go to Cloud Profiles' },
    { keys: ['5'], description: 'Go to Settings' },
  ]},
  { category: 'Archives', items: [
    { keys: ['/'], description: 'Focus search' },
    { keys: ['U'], description: 'Open upload modal' },
    { keys: ['Esc'], description: 'Clear selection / blur input' },
    { keys: ['Right-click'], description: 'Context menu on cards' },
  ]},
  { category: 'General', items: [
    { keys: ['?'], description: 'Show this help' },
  ]},
];

function KeyBadge({ children }: { children: string }) {
  return (
    <kbd className="px-2 py-1 text-xs font-mono bg-bambu-dark border border-bambu-dark-tertiary rounded text-white">
      {children}
    </kbd>
  );
}

export function KeyboardShortcutsModal({ onClose }: KeyboardShortcutsModalProps) {
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
                      <span className="text-white text-sm">{shortcut.description}</span>
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
