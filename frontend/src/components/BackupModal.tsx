import { useEffect, useState } from 'react';
import { Download, X, Settings, Bell, FileText, Plug, Printer, Palette, Wrench, Archive, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent } from './Card';
import { Button } from './Button';

interface BackupCategory {
  id: string;
  labelKey: string;
  defaultLabel: string;
  icon: React.ReactNode;
  default: boolean;
  description: string;
}

const BACKUP_CATEGORIES: BackupCategory[] = [
  {
    id: 'settings',
    labelKey: 'backup.categories.settings',
    defaultLabel: 'App Settings',
    icon: <Settings className="w-4 h-4" />,
    default: true,
    description: 'Language, theme, update preferences',
  },
  {
    id: 'notifications',
    labelKey: 'backup.categories.notifications',
    defaultLabel: 'Notification Providers',
    icon: <Bell className="w-4 h-4" />,
    default: true,
    description: 'ntfy, Pushover, Discord, etc.',
  },
  {
    id: 'templates',
    labelKey: 'backup.categories.templates',
    defaultLabel: 'Notification Templates',
    icon: <FileText className="w-4 h-4" />,
    default: true,
    description: 'Custom message templates',
  },
  {
    id: 'smart_plugs',
    labelKey: 'backup.categories.smartPlugs',
    defaultLabel: 'Smart Plugs',
    icon: <Plug className="w-4 h-4" />,
    default: true,
    description: 'Tasmota plug configurations',
  },
  {
    id: 'printers',
    labelKey: 'backup.categories.printers',
    defaultLabel: 'Printers',
    icon: <Printer className="w-4 h-4" />,
    default: false,
    description: 'Printer info (access codes excluded)',
  },
  {
    id: 'filaments',
    labelKey: 'backup.categories.filaments',
    defaultLabel: 'Filament Inventory',
    icon: <Palette className="w-4 h-4" />,
    default: false,
    description: 'Filament types and costs',
  },
  {
    id: 'maintenance',
    labelKey: 'backup.categories.maintenance',
    defaultLabel: 'Maintenance Types',
    icon: <Wrench className="w-4 h-4" />,
    default: false,
    description: 'Custom maintenance schedules',
  },
  {
    id: 'archives',
    labelKey: 'backup.categories.archives',
    defaultLabel: 'Print Archives',
    icon: <Archive className="w-4 h-4" />,
    default: false,
    description: 'All print data + files (3MF, thumbnails, photos)',
  },
];

interface BackupModalProps {
  onClose: () => void;
  onExport: (categories: Record<string, boolean>) => Promise<void>;
}

export function BackupModal({ onClose, onExport }: BackupModalProps) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    BACKUP_CATEGORIES.forEach((cat) => {
      initial[cat.id] = cat.default;
    });
    return initial;
  });
  const [isExporting, setIsExporting] = useState(false);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const toggleCategory = (id: string) => {
    setSelected((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const selectAll = () => {
    const all: Record<string, boolean> = {};
    BACKUP_CATEGORIES.forEach((cat) => {
      all[cat.id] = true;
    });
    setSelected(all);
  };

  const selectNone = () => {
    const none: Record<string, boolean> = {};
    BACKUP_CATEGORIES.forEach((cat) => {
      none[cat.id] = false;
    });
    setSelected(none);
  };

  const selectedCount = Object.values(selected).filter(Boolean).length;

  const handleExport = async () => {
    setIsExporting(true);
    try {
      await onExport(selected);
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={isExporting ? undefined : onClose}
    >
      <Card className="w-full max-w-lg" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent className="p-0">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-full bg-bambu-green/20 text-bambu-green">
                <Download className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-white">
                  {t('backup.exportTitle', { defaultValue: 'Export Backup' })}
                </h3>
                <p className="text-sm text-bambu-gray">
                  {t('backup.selectCategories', { defaultValue: 'Select data to include' })}
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-2 hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Quick actions */}
          <div className="flex gap-2 px-4 pt-4">
            <button
              onClick={selectAll}
              disabled={isExporting}
              className="text-sm text-bambu-green hover:text-bambu-green/80 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {t('common.selectAll', { defaultValue: 'Select All' })}
            </button>
            <span className="text-bambu-gray">|</span>
            <button
              onClick={selectNone}
              disabled={isExporting}
              className="text-sm text-bambu-gray hover:text-white disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {t('common.selectNone', { defaultValue: 'Select None' })}
            </button>
          </div>

          {/* Categories */}
          <div className={`p-4 space-y-2 max-h-[400px] overflow-y-auto ${isExporting ? 'opacity-50 pointer-events-none' : ''}`}>
            {BACKUP_CATEGORIES.map((category) => (
              <label
                key={category.id}
                className={`flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-colors ${
                  selected[category.id]
                    ? 'bg-bambu-green/10 border border-bambu-green/30'
                    : 'bg-bambu-dark hover:bg-bambu-dark-tertiary border border-transparent'
                }`}
              >
                <input
                  type="checkbox"
                  checked={selected[category.id]}
                  onChange={() => toggleCategory(category.id)}
                  disabled={isExporting}
                  className="w-4 h-4 rounded border-bambu-gray bg-bambu-dark text-bambu-green focus:ring-bambu-green focus:ring-offset-0"
                />
                <div className={`${selected[category.id] ? 'text-bambu-green' : 'text-bambu-gray'}`}>
                  {category.icon}
                </div>
                <div className="flex-1">
                  <div className="text-white text-sm font-medium">
                    {t(category.labelKey, { defaultValue: category.defaultLabel })}
                  </div>
                  <div className="text-xs text-bambu-gray">{category.description}</div>
                </div>
              </label>
            ))}
          </div>

          {/* Archive warning */}
          {selected.archives && (
            <div className="mx-4 mb-2 p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/30">
              <div className="flex items-start gap-2 text-sm">
                <Archive className="w-4 h-4 text-yellow-500 mt-0.5 flex-shrink-0" />
                <div className="text-yellow-200">
                  <span className="font-medium">ZIP file will be created.</span>
                  <span className="text-yellow-200/70"> Includes all 3MF files, thumbnails, timelapses, and photos. This may take a while and result in a large file.</span>
                </div>
              </div>
            </div>
          )}

          {/* Footer */}
          <div className="flex items-center justify-between p-4 border-t border-bambu-dark-tertiary">
            <span className="text-sm text-bambu-gray">
              {t('backup.selectedCount', {
                count: selectedCount,
                defaultValue: `${selectedCount} categories selected`,
              })}
            </span>
            <div className="flex gap-3">
              <Button variant="secondary" onClick={onClose} disabled={isExporting}>
                {t('common.cancel', { defaultValue: 'Cancel' })}
              </Button>
              <Button
                onClick={handleExport}
                disabled={selectedCount === 0 || isExporting}
                className="bg-bambu-green hover:bg-bambu-green-dark disabled:opacity-50 disabled:cursor-not-allowed min-w-[100px]"
              >
                {isExporting ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    {t('backup.exporting', { defaultValue: 'Exporting...' })}
                  </>
                ) : (
                  <>
                    <Download className="w-4 h-4 mr-2" />
                    {t('backup.export', { defaultValue: 'Export' })}
                  </>
                )}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
