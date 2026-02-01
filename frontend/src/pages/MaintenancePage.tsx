import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Wrench,
  Loader2,
  Check,
  AlertTriangle,
  Clock,
  Plus,
  Trash2,
  ChevronDown,
  ChevronUp,
  Droplet,
  Flame,
  Ruler,
  Sparkles,
  Square,
  Cable,
  Edit3,
  RotateCcw,
  Calendar,
  Timer,
  Cog,
  Fan,
  Zap,
  Wind,
  Thermometer,
  Layers,
  Box,
  Target,
  RefreshCw,
  Settings,
  Filter,
  CircleDot,
  Printer,
  ExternalLink,
} from 'lucide-react';
import { api } from '../api/client';
import type { MaintenanceStatus, PrinterMaintenanceOverview, MaintenanceType, Permission } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { Toggle } from '../components/Toggle';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';

// Icon mapping for maintenance types
const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  Droplet,
  Flame,
  Ruler,
  Sparkles,
  Square,
  Cable,
  Wrench,
  Calendar,
  Timer,
  Cog,
  Fan,
  Zap,
  Wind,
  Thermometer,
  Layers,
  Box,
  Target,
  RefreshCw,
  Settings,
  Filter,
  CircleDot,
};

function getIcon(iconName: string | null) {
  if (!iconName) return Wrench;
  return iconMap[iconName] || Wrench;
}

function formatDuration(value: number, type: 'hours' | 'days', t: (key: string, opts?: Record<string, unknown>) => string): string {
  if (type === 'days') {
    if (value < 1) return t('maintenance.today');
    if (value === 1) return t('maintenance.duration.oneDay');
    if (value < 7) return t('maintenance.duration.days', { count: Math.round(value) });
    // Show weeks for anything under 6 months for better precision
    if (value < 180) return t('maintenance.duration.weeks', { count: Math.round(value / 7) });
    // 6+ months show as months
    return t('maintenance.duration.months', { count: Math.round(value / 30) });
  } else {
    // Print hours - convert to readable units
    if (value < 1) return `${Math.round(value * 60)}m`;
    if (value < 24) return `${value < 10 ? value.toFixed(1) : Math.round(value)}h`;
    // 24+ hours: show as days of print time
    const days = value / 24;
    if (days < 7) return `${days < 2 ? days.toFixed(1) : Math.round(days)}d`;
    // 7+ days: show as weeks of print time
    const weeks = days / 7;
    if (weeks < 12) return `${weeks < 2 ? weeks.toFixed(1) : Math.round(weeks)}w`;
    // 12+ weeks: show as months of print time
    return `${Math.round(weeks / 4)}mo`;
  }
}

// Map known maintenance type names (from DB) to i18n keys
const MAINTENANCE_TYPE_KEYS: Record<string, string> = {
  'Lubricate Linear Rails': 'maintenance.types.lubricateRails',
  'Clean Nozzle/Hotend': 'maintenance.types.cleanNozzle',
  'Check Belt Tension': 'maintenance.types.checkBelts',
  'Clean Build Plate': 'maintenance.types.cleanBuildPlate',
  'Check Extruder Gears': 'maintenance.types.checkExtruder',
  'Check Cooling Fans': 'maintenance.types.checkCooling',
  'General Inspection': 'maintenance.types.generalInspection',
  'Clean Carbon Rods': 'maintenance.types.cleanCarbonRods',
  'Check PTFE Tube': 'maintenance.types.checkPtfeTube',
  'Replace HEPA Filter': 'maintenance.types.replaceHepaFilter',
  'HEPA Filter': 'maintenance.types.replaceHepaFilter',
  'Replace Carbon Filter': 'maintenance.types.replaceCarbonFilter',
  'Carbon Filter': 'maintenance.types.replaceCarbonFilter',
  'Lubricate Left Nozzle Rail': 'maintenance.types.lubricateLeftNozzleRail',
  'Left Nozzle Rail': 'maintenance.types.lubricateLeftNozzleRail',
};

function translateTypeName(name: string, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const key = MAINTENANCE_TYPE_KEYS[name];
  return key ? t(key) : name;
}

function formatIntervalLabel(value: number, type: 'hours' | 'days', t: (key: string, opts?: Record<string, unknown>) => string): string {
  if (type === 'days') {
    if (value === 1) return t('maintenance.duration.oneDay');
    if (value === 7) return t('maintenance.duration.oneWeek');
    if (value === 14) return t('maintenance.duration.nWeeks', { count: 2 });
    if (value === 30) return t('maintenance.duration.oneMonth');
    if (value === 60) return t('maintenance.duration.nMonths', { count: 2 });
    if (value === 90) return t('maintenance.duration.nMonths', { count: 3 });
    if (value === 180) return t('maintenance.duration.nMonths', { count: 6 });
    if (value === 365) return t('maintenance.duration.oneYear');
    return t('maintenance.duration.days', { count: value });
  }
  return `${value}h`;
}

// Get Bambu Lab wiki URL for a maintenance task based on printer model
function getMaintenanceWikiUrl(typeName: string, printerModel: string | null): string | null {
  const model = (printerModel || '').toUpperCase().replace(/[- ]/g, '');

  // Helper to match model families
  const isX1 = model.includes('X1');
  const isP1 = model.includes('P1');
  const isA1Mini = model.includes('A1MINI');
  const isA1 = model.includes('A1') && !isA1Mini;
  const isH2D = model.includes('H2D');
  const isH2C = model.includes('H2C');
  const isH2S = model.includes('H2S');
  const isH2 = isH2D || isH2C || isH2S;
  const isP2S = model.includes('P2S');

  switch (typeName) {
    case 'Lubricate Linear Rails':
      if (isX1) return 'https://wiki.bambulab.com/en/x1/maintenance/basic-maintenance';
      if (isP1) return 'https://wiki.bambulab.com/en/p1/maintenance/p1p-maintenance';
      if (isA1Mini) return 'https://wiki.bambulab.com/en/a1-mini/maintenance/lubricate-y-axis';
      if (isA1) return 'https://wiki.bambulab.com/en/a1/maintenance/lubricate-y-axis';
      if (isH2) return 'https://wiki.bambulab.com/en/h2/maintenance/x-axis-lubrication';
      if (isP2S) return 'https://wiki.bambulab.com/en/p2s/maintenance/belt-tension'; // P2S maintenance page
      return 'https://wiki.bambulab.com/en/general/lead-screws-lubrication';

    case 'Clean Nozzle/Hotend':
      if (isX1 || isP1) return 'https://wiki.bambulab.com/en/x1/troubleshooting/nozzle-clog';
      if (isA1Mini || isA1) return 'https://wiki.bambulab.com/en/a1-mini/troubleshooting/nozzle-clog';
      if (isH2) return 'https://wiki.bambulab.com/en/h2/maintenance/nozzl-cold-pull-maintenance-and-cleaning';
      if (isP2S) return 'https://wiki.bambulab.com/en/p2s/maintenance/cold-pull-maintenance-hotend';
      return 'https://wiki.bambulab.com/en/x1/troubleshooting/nozzle-clog';

    case 'Check Belt Tension':
      if (isX1) return 'https://wiki.bambulab.com/en/x1/maintenance/belt-tension';
      if (isP1) return 'https://wiki.bambulab.com/en/p1/maintenance/p1p-maintenance';
      if (isA1Mini) return 'https://wiki.bambulab.com/en/a1-mini/maintenance/belt_tension';
      if (isA1) return 'https://wiki.bambulab.com/en/a1/maintenance/belt_tension';
      if (isH2D) return 'https://wiki.bambulab.com/en/h2/maintenance/belt-tension';
      if (isH2C) return 'https://wiki.bambulab.com/en/h2c/maintenance/belt-tension';
      if (isH2S) return 'https://wiki.bambulab.com/en/h2s/maintenance/belt-tension';
      if (isP2S) return 'https://wiki.bambulab.com/en/p2s/maintenance/belt-tension';
      return 'https://wiki.bambulab.com/en/x1/maintenance/belt-tension';

    case 'Clean Carbon Rods':
      // Only X1 and P1 series have carbon rods
      if (isX1 || isP1) return 'https://wiki.bambulab.com/en/general/carbon-rods-clearance';
      // A1, H2, P2S don't have carbon rods - return null
      if (isA1Mini || isA1 || isH2 || isP2S) return null;
      return 'https://wiki.bambulab.com/en/general/carbon-rods-clearance';

    case 'Clean Build Plate':
      // Same for all printers
      return 'https://wiki.bambulab.com/en/filament-acc/acc/pei-plate-clean-guide';

    case 'Check PTFE Tube':
      if (isX1 || isP1) return 'https://wiki.bambulab.com/en/x1/maintenance/replace-ptfe-tube';
      if (isA1Mini || isA1) return 'https://wiki.bambulab.com/en/a1-mini/maintenance/ptfe-tube';
      if (isH2D) return 'https://wiki.bambulab.com/en/h2/maintenance/replace-ptfe-tube-on-h2d-printer';
      if (isH2S) return 'https://wiki.bambulab.com/en/h2s/maintenance/replace-ptfe-tube-on-h2s-printer';
      if (isH2C) return 'https://wiki.bambulab.com/en/h2/maintenance/replace-ptfe-tube-on-h2d-printer'; // H2C uses H2D guide
      if (isP2S) return 'https://wiki.bambulab.com/en/x1/maintenance/replace-ptfe-tube'; // P2S uses similar PTFE
      return 'https://wiki.bambulab.com/en/x1/maintenance/replace-ptfe-tube';

    case 'Replace HEPA Filter':
    case 'HEPA Filter':
    case 'Replace Carbon Filter':
    case 'Carbon Filter':
      if (isH2) return 'https://wiki.bambulab.com/en/h2/maintenance/replace-smoke-purifier-air-filte';
      // X1/P1 use the activated carbon filter
      return 'https://wiki.bambulab.com/en/x1/maintenance/replace-carbon-filter';

    case 'Lubricate Left Nozzle Rail':
    case 'Left Nozzle Rail':
      // H2 series specific - dual nozzle system
      if (isH2) return 'https://wiki.bambulab.com/en/h2/maintenance/x-axis-lubrication';
      return null;

    default:
      // Custom maintenance types don't have wiki URLs
      return null;
  }
}

// Maintenance item card - cleaner, more visual design
function MaintenanceCard({
  item,
  onPerform,
  onToggle,
  hasPermission,
}: {
  item: MaintenanceStatus;
  onPerform: (id: number) => void;
  onToggle: (id: number, enabled: boolean) => void;
  hasPermission: (permission: Permission) => boolean;
}) {
  const { t } = useTranslation();
  const Icon = getIcon(item.maintenance_type_icon);
  const intervalType = item.interval_type || 'hours';

  // Calculate progress based on interval type
  const getProgress = () => {
    if (intervalType === 'days') {
      const daysSince = item.days_since_maintenance ?? 0;
      return Math.max(0, Math.min(100, (daysSince / item.interval_hours) * 100));
    }
    return Math.max(0, Math.min(100,
      ((item.interval_hours - item.hours_until_due) / item.interval_hours) * 100
    ));
  };

  const progressPercent = getProgress();

  const getStatusColor = () => {
    if (!item.enabled) return 'text-bambu-gray';
    if (item.is_due) return 'text-red-400';
    if (item.is_warning) return 'text-amber-400';
    return 'text-bambu-green';
  };

  const getProgressColor = () => {
    if (!item.enabled) return 'bg-bambu-gray/30';
    if (item.is_due) return 'bg-red-500';
    if (item.is_warning) return 'bg-amber-500';
    return 'bg-bambu-green';
  };

  const getBgColor = () => {
    if (!item.enabled) return 'bg-bambu-dark-secondary/50';
    if (item.is_due) return 'bg-red-500/5 border-red-500/20';
    if (item.is_warning) return 'bg-amber-500/5 border-amber-500/20';
    return 'bg-bambu-dark-secondary border-bambu-dark-tertiary';
  };

  const getStatusText = () => {
    if (!item.enabled) return t('common.disabled');

    if (intervalType === 'days') {
      const daysUntil = item.days_until_due ?? 0;
      if (item.is_due) return `${t('maintenance.overdue')} ${formatDuration(Math.abs(daysUntil), 'days', t)}`;
      if (item.is_warning) return `${t('maintenance.dueSoon')} ${formatDuration(daysUntil, 'days', t)}`;
      return `${formatDuration(daysUntil, 'days', t)} ${t('maintenance.left')}`;
    } else {
      if (item.is_due) return `${t('maintenance.overdue')} ${formatDuration(Math.abs(item.hours_until_due), 'hours', t)}`;
      if (item.is_warning) return `${t('maintenance.dueSoon')} ${formatDuration(item.hours_until_due, 'hours', t)}`;
      return `${formatDuration(item.hours_until_due, 'hours', t)} ${t('maintenance.left')}`;
    }
  };

  return (
    <div className={`rounded-xl border p-4 transition-all ${getBgColor()}`}>
      <div className="flex items-start gap-3">
        {/* Icon with status indicator */}
        <div className={`relative p-2.5 rounded-lg ${
          item.is_due ? 'bg-red-500/20' :
          item.is_warning ? 'bg-amber-500/20' :
          item.enabled ? 'bg-bambu-dark' : 'bg-bambu-dark/50'
        }`}>
          <Icon className={`w-5 h-5 ${getStatusColor()}`} />
          {item.enabled && (item.is_due || item.is_warning) && (
            <span className={`absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full ${
              item.is_due ? 'bg-red-500' : 'bg-amber-500'
            } animate-pulse`} />
          )}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className={`font-medium truncate ${item.enabled ? 'text-white' : 'text-bambu-gray'}`}>
              {translateTypeName(item.maintenance_type_name, t)}
            </h3>
            {intervalType === 'days' && (
              <span title={t('maintenance.timeBasedInterval')}>
                <Calendar className="w-3.5 h-3.5 text-bambu-gray shrink-0" />
              </span>
            )}
            {/* Wiki link - next to name */}
            {(() => {
              // Use custom wiki_url from type if available, otherwise use computed URL
              const wikiUrl = item.maintenance_type_wiki_url || getMaintenanceWikiUrl(item.maintenance_type_name, item.printer_model);
              return wikiUrl ? (
                <a
                  href={wikiUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-bambu-gray hover:text-bambu-green transition-colors shrink-0"
                  title={t('maintenance.viewDocumentation')}
                  onClick={(e) => e.stopPropagation()}
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              ) : null;
            })()}
          </div>

          {/* Progress bar */}
          <div className="mt-2 mb-1.5">
            <div className="w-full h-1.5 bg-bambu-dark rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${getProgressColor()}`}
                style={{ width: `${progressPercent}%` }}
              />
            </div>
          </div>

          {/* Status text */}
          <div className={`text-xs flex items-center gap-1 ${getStatusColor()}`}>
            {item.is_due && <AlertTriangle className="w-3 h-3" />}
            {item.is_warning && !item.is_due && <Clock className="w-3 h-3" />}
            {!item.is_due && !item.is_warning && item.enabled && <Check className="w-3 h-3" />}
            {getStatusText()}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 shrink-0">
          <span title={!hasPermission('maintenance:update') ? t('maintenance.noPermissionUpdate') : undefined}>
            <Toggle
              checked={item.enabled}
              onChange={(checked) => onToggle(item.id, checked)}
              disabled={!hasPermission('maintenance:update')}
            />
          </span>
          <Button
            size="sm"
            variant={item.is_due ? 'primary' : 'secondary'}
            onClick={() => onPerform(item.id)}
            disabled={!item.enabled || !hasPermission('maintenance:update')}
            title={!hasPermission('maintenance:update') ? t('maintenance.noPermissionPerform') : undefined}
            className="!px-3"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            {t('maintenance.reset')}
          </Button>
        </div>
      </div>
    </div>
  );
}

// Printer section with improved visual hierarchy
function PrinterSection({
  overview,
  onPerform,
  onToggle,
  onSetHours,
  hasPermission,
}: {
  overview: PrinterMaintenanceOverview;
  onPerform: (id: number) => void;
  onToggle: (id: number, enabled: boolean) => void;
  onSetHours: (printerId: number, hours: number) => void;
  hasPermission: (permission: Permission) => boolean;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(true);
  const [editingHours, setEditingHours] = useState(false);
  const [hoursInput, setHoursInput] = useState(overview.total_print_hours.toFixed(1));

  const sortedItems = [...overview.maintenance_items].sort((a, b) => {
    // Sort by urgency first, then by type
    if (a.is_due && !b.is_due) return -1;
    if (!a.is_due && b.is_due) return 1;
    if (a.is_warning && !b.is_warning) return -1;
    if (!a.is_warning && b.is_warning) return 1;
    return a.maintenance_type_id - b.maintenance_type_id;
  });

  const nextTask = sortedItems.find(item => item.enabled && (item.is_due || item.is_warning));

  const handleSaveHours = () => {
    const hours = parseFloat(hoursInput);
    if (!isNaN(hours) && hours >= 0) {
      onSetHours(overview.printer_id, hours);
      setEditingHours(false);
    }
  };

  return (
    <Card className="overflow-hidden">
      {/* Header */}
      <div className="p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h2 className="text-xl font-semibold text-white">{overview.printer_name}</h2>
            <div className="flex items-center gap-2">
              {overview.due_count > 0 && (
                <span className="px-2.5 py-1 bg-red-500/20 text-red-400 text-xs font-medium rounded-full flex items-center gap-1.5">
                  <AlertTriangle className="w-3 h-3" />
                  {t('maintenance.countOverdue', { count: overview.due_count })}
                </span>
              )}
              {overview.warning_count > 0 && (
                <span className="px-2.5 py-1 bg-amber-500/20 text-amber-400 text-xs font-medium rounded-full flex items-center gap-1.5">
                  <Clock className="w-3 h-3" />
                  {t('maintenance.countDueSoon', { count: overview.warning_count })}
                </span>
              )}
              {overview.due_count === 0 && overview.warning_count === 0 && (
                <span className="px-2.5 py-1 bg-bambu-green/20 text-bambu-green text-xs font-medium rounded-full flex items-center gap-1.5">
                  <Check className="w-3 h-3" />
                  {t('maintenance.allGood')}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-bambu-gray hover:text-white hover:bg-bambu-dark rounded-lg transition-colors"
          >
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            {expanded ? t('maintenance.collapse') : t('maintenance.expand')}
          </button>
        </div>

        {/* Quick stats row */}
        <div className="flex items-center gap-6 mt-4">
          {/* Print Hours */}
          <div className="flex items-center gap-3">
            <div className="p-2 bg-bambu-dark/50 rounded-lg">
              <Timer className="w-4 h-4 text-bambu-gray" />
            </div>
            {editingHours ? (
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  value={hoursInput}
                  onChange={(e) => setHoursInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleSaveHours();
                    if (e.key === 'Escape') setEditingHours(false);
                  }}
                  className="w-24 px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm"
                  min="0"
                  step="1"
                  autoFocus
                />
                <span className="text-xs text-bambu-gray">{t('common.hours')}</span>
                <Button size="sm" onClick={handleSaveHours}>{t('common.save')}</Button>
                <Button size="sm" variant="secondary" onClick={() => setEditingHours(false)}>{t('common.cancel')}</Button>
              </div>
            ) : (
              <button
                onClick={() => {
                  if (!hasPermission('maintenance:update')) return;
                  setHoursInput(Math.round(overview.total_print_hours).toString());
                  setEditingHours(true);
                }}
                className={`group ${!hasPermission('maintenance:update') ? 'cursor-not-allowed opacity-60' : ''}`}
                title={!hasPermission('maintenance:update') ? t('maintenance.noPermissionEditHours') : undefined}
              >
                <div className={`text-sm font-medium text-white ${hasPermission('maintenance:update') ? 'group-hover:text-bambu-green' : ''} transition-colors flex items-center gap-1`}>
                  {t('maintenance.nHours', { count: Math.round(overview.total_print_hours) })}
                  <Edit3 className={`w-3 h-3 text-bambu-gray ${hasPermission('maintenance:update') ? 'group-hover:text-bambu-green' : ''}`} />
                </div>
                <div className="text-xs text-bambu-gray">{t('maintenance.totalPrintTime')}</div>
              </button>
            )}
          </div>

          {/* Divider */}
          <div className="w-px h-10 bg-bambu-dark-tertiary" />

          {/* Next Maintenance */}
          {nextTask && (
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-lg ${
                nextTask.is_due ? 'bg-red-500/20' : 'bg-amber-500/20'
              }`}>
                {(() => {
                  const Icon = getIcon(nextTask.maintenance_type_icon);
                  return <Icon className={`w-4 h-4 ${nextTask.is_due ? 'text-red-400' : 'text-amber-400'}`} />;
                })()}
              </div>
              <div>
                <div className={`text-sm font-medium ${nextTask.is_due ? 'text-red-400' : 'text-amber-400'}`}>
                  {translateTypeName(nextTask.maintenance_type_name, t)}
                </div>
                <div className={`text-xs ${nextTask.is_due ? 'text-red-400/70' : 'text-amber-400/70'}`}>
                  {nextTask.is_due ? t('maintenance.overdue') : t('maintenance.dueSoon')}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Maintenance items */}
      {expanded && (
        <CardContent className="pt-0 border-t border-bambu-dark-tertiary">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 pt-4">
            {sortedItems.map((item) => (
              <MaintenanceCard
                key={item.id}
                item={item}
                onPerform={onPerform}
                onToggle={onToggle}
                hasPermission={hasPermission}
              />
            ))}
          </div>
        </CardContent>
      )}
    </Card>
  );
}

// Settings section - maintenance types configuration
function SettingsSection({
  overview,
  types,
  onUpdateInterval,
  onAddType,
  onUpdateType,
  onDeleteType,
  onAssignType,
  onRemoveItem,
  hasPermission,
}: {
  overview: PrinterMaintenanceOverview[] | undefined;
  types: MaintenanceType[];
  onUpdateInterval: (id: number, data: { custom_interval_hours?: number | null; custom_interval_type?: 'hours' | 'days' | null }) => void;
  onAddType: (data: { name: string; description?: string; default_interval_hours: number; interval_type: 'hours' | 'days'; icon?: string; wiki_url?: string | null }, printerIds: number[]) => void;
  onUpdateType: (id: number, data: { name?: string; default_interval_hours?: number; interval_type?: 'hours' | 'days'; icon?: string; wiki_url?: string | null }) => void;
  onDeleteType: (id: number) => void;
  onAssignType: (printerId: number, typeId: number) => void;
  onRemoveItem: (itemId: number) => void;
  hasPermission: (permission: Permission) => boolean;
}) {
  const { t } = useTranslation();
  const [editingInterval, setEditingInterval] = useState<number | null>(null);
  const [intervalInput, setIntervalInput] = useState('');
  const [intervalTypeInput, setIntervalTypeInput] = useState<'hours' | 'days'>('hours');
  const [showAddType, setShowAddType] = useState(false);
  const [newTypeName, setNewTypeName] = useState('');
  const [newTypeInterval, setNewTypeInterval] = useState('100');
  const [newTypeIntervalType, setNewTypeIntervalType] = useState<'hours' | 'days'>('hours');
  const [newTypeIcon, setNewTypeIcon] = useState('Wrench');
  const [newTypeWikiUrl, setNewTypeWikiUrl] = useState('');
  const [selectedPrinters, setSelectedPrinters] = useState<Set<number>>(new Set());
  const [expandedType, setExpandedType] = useState<number | null>(null);

  // Get unique printers from overview
  const printers = useMemo(() => {
    if (!overview) return [];
    return overview.map(o => ({ id: o.printer_id, name: o.printer_name }));
  }, [overview]);

  // Get which printers have a specific maintenance type assigned
  const getAssignedPrinters = (typeId: number) => {
    if (!overview) return [];
    return overview
      .filter(p => p.maintenance_items.some(item => item.maintenance_type_id === typeId))
      .map(p => ({
        printerId: p.printer_id,
        printerName: p.printer_name,
        itemId: p.maintenance_items.find(item => item.maintenance_type_id === typeId)?.id,
      }));
  };

  // Get printers that DON'T have a specific type assigned
  const getUnassignedPrinters = (typeId: number) => {
    if (!overview) return [];
    const assignedIds = new Set(getAssignedPrinters(typeId).map(p => p.printerId));
    return printers.filter(p => !assignedIds.has(p.id));
  };

  // Edit type state
  const [editingType, setEditingType] = useState<MaintenanceType | null>(null);
  const [editTypeName, setEditTypeName] = useState('');
  const [editTypeInterval, setEditTypeInterval] = useState('');
  const [editTypeIntervalType, setEditTypeIntervalType] = useState<'hours' | 'days'>('hours');
  const [editTypeIcon, setEditTypeIcon] = useState('Wrench');
  const [editTypeWikiUrl, setEditTypeWikiUrl] = useState('');

  const startEditType = (type: MaintenanceType) => {
    setEditingType(type);
    setEditTypeName(type.name);
    setEditTypeInterval(type.default_interval_hours.toString());
    setEditTypeIntervalType(type.interval_type || 'hours');
    setEditTypeIcon(type.icon || 'Wrench');
    setEditTypeWikiUrl(type.wiki_url || '');
  };

  const handleSaveEditType = () => {
    if (editingType && editTypeName.trim() && parseFloat(editTypeInterval) > 0) {
      onUpdateType(editingType.id, {
        name: editTypeName.trim(),
        default_interval_hours: parseFloat(editTypeInterval),
        interval_type: editTypeIntervalType,
        icon: editTypeIcon,
        wiki_url: editTypeWikiUrl.trim() || null,
      });
      setEditingType(null);
    }
  };

  const handleSaveInterval = (itemId: number, defaultInterval: number, defaultIntervalType: 'hours' | 'days') => {
    const newInterval = parseFloat(intervalInput);
    if (!isNaN(newInterval) && newInterval > 0) {
      const customInterval = Math.abs(newInterval - defaultInterval) < 0.01 ? null : newInterval;
      const customIntervalType = intervalTypeInput !== defaultIntervalType ? intervalTypeInput : null;
      onUpdateInterval(itemId, {
        custom_interval_hours: customInterval,
        custom_interval_type: customIntervalType
      });
    }
    setEditingInterval(null);
  };

  const handleAddType = (e: React.FormEvent) => {
    e.preventDefault();
    if (newTypeName.trim() && parseFloat(newTypeInterval) > 0 && selectedPrinters.size > 0) {
      onAddType({
        name: newTypeName.trim(),
        default_interval_hours: parseFloat(newTypeInterval),
        interval_type: newTypeIntervalType,
        icon: newTypeIcon,
        wiki_url: newTypeWikiUrl.trim() || null,
      }, Array.from(selectedPrinters));
      setNewTypeName('');
      setNewTypeInterval('100');
      setNewTypeIntervalType('hours');
      setNewTypeWikiUrl('');
      setSelectedPrinters(new Set());
      setShowAddType(false);
    }
  };

  const togglePrinterSelection = (printerId: number) => {
    setSelectedPrinters(prev => {
      const next = new Set(prev);
      if (next.has(printerId)) {
        next.delete(printerId);
      } else {
        next.add(printerId);
      }
      return next;
    });
  };

  const printerItems = overview?.map(p => ({
    printerId: p.printer_id,
    printerName: p.printer_name,
    items: p.maintenance_items.sort((a, b) => a.maintenance_type_id - b.maintenance_type_id),
  })).sort((a, b) => a.printerName.localeCompare(b.printerName)) || [];

  const systemTypes = types.filter(t => t.is_system);
  const customTypes = types.filter(t => !t.is_system);

  return (
    <div className="space-y-8">
      {/* Maintenance Types */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold text-white">{t('maintenance.maintenanceTypes')}</h2>
            <p className="text-sm text-bambu-gray mt-1">{t('maintenance.systemTypesDescription')}</p>
          </div>
          <Button
            onClick={() => setShowAddType(!showAddType)}
            disabled={!hasPermission('maintenance:create')}
            title={!hasPermission('maintenance:create') ? t('maintenance.noPermissionCreate') : undefined}
          >
            <Plus className="w-4 h-4" />
            {t('maintenance.addCustomType')}
          </Button>
        </div>

        {/* Add custom type form */}
        {showAddType && (
          <Card className="mb-6">
            <CardContent className="py-4">
              <form onSubmit={handleAddType}>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                  <div className="lg:col-span-2">
                    <label className="block text-xs text-bambu-gray mb-1.5">{t('common.name')}</label>
                    <input
                      type="text"
                      value={newTypeName}
                      onChange={(e) => setNewTypeName(e.target.value)}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                      placeholder={t('maintenance.namePlaceholder')}
                      autoFocus
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1.5">{t('maintenance.intervalType')}</label>
                    <select
                      value={newTypeIntervalType}
                      onChange={(e) => {
                        setNewTypeIntervalType(e.target.value as 'hours' | 'days');
                        // Set sensible default based on type
                        if (e.target.value === 'days') {
                          setNewTypeInterval('30');
                        } else {
                          setNewTypeInterval('100');
                        }
                      }}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                    >
                      <option value="hours">{t('maintenance.printHours')}</option>
                      <option value="days">{t('maintenance.calendarDays')}</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1.5">
                      {t('maintenance.intervalWithUnit', { unit: newTypeIntervalType === 'days' ? t('maintenance.days') : t('common.hours') })}
                    </label>
                    <input
                      type="number"
                      value={newTypeInterval}
                      onChange={(e) => setNewTypeInterval(e.target.value)}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                      min="1"
                    />
                  </div>
                </div>
                <div className="mt-4 flex items-end justify-between">
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1.5">{t('maintenance.icon')}</label>
                    <div className="flex gap-1">
                      {Object.keys(iconMap).map((iconName) => {
                        const IconComp = iconMap[iconName];
                        return (
                          <button
                            key={iconName}
                            type="button"
                            onClick={() => setNewTypeIcon(iconName)}
                            className={`p-2 rounded-lg transition-colors ${
                              newTypeIcon === iconName
                                ? 'bg-bambu-green text-white'
                                : 'bg-bambu-dark text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary'
                            }`}
                          >
                            <IconComp className="w-4 h-4" />
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
                {/* Wiki URL */}
                <div className="mt-4">
                  <label className="block text-xs text-bambu-gray mb-1.5">{t('maintenance.documentationLink')}</label>
                  <input
                    type="url"
                    value={newTypeWikiUrl}
                    onChange={(e) => setNewTypeWikiUrl(e.target.value)}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                    placeholder={t('maintenance.wikiUrlPlaceholder')}
                  />
                </div>
                {/* Printer selection */}
                <div className="mt-4">
                  <label className="block text-xs text-bambu-gray mb-1.5">{t('maintenance.assignToPrinters')}</label>
                  <div className="flex flex-wrap gap-2">
                    {printers.map(p => (
                      <button
                        key={p.id}
                        type="button"
                        onClick={() => togglePrinterSelection(p.id)}
                        className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                          selectedPrinters.has(p.id)
                            ? 'bg-bambu-green text-white'
                            : 'bg-bambu-dark text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary'
                        }`}
                      >
                        {p.name}
                      </button>
                    ))}
                  </div>
                  {selectedPrinters.size === 0 && (
                    <p className="text-xs text-orange-400 mt-1">{t('maintenance.selectAtLeastOnePrinter')}</p>
                  )}
                </div>
                <div className="mt-4 flex justify-end gap-2">
                  <Button type="button" variant="secondary" onClick={() => { setShowAddType(false); setSelectedPrinters(new Set()); }}>
                    {t('common.cancel')}
                  </Button>
                  <Button type="submit" disabled={!newTypeName.trim() || selectedPrinters.size === 0}>
                    {t('maintenance.addType')}
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>
        )}

        {/* Types grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {/* System types */}
          {systemTypes.map((type) => {
            const Icon = getIcon(type.icon);
            const intervalType = type.interval_type || 'hours';
            return (
              <div key={type.id} className="bg-bambu-dark-secondary rounded-xl p-4 border border-bambu-dark-tertiary">
                <div className="flex items-center gap-3">
                  <div className="p-2.5 bg-bambu-dark rounded-lg">
                    <Icon className="w-5 h-5 text-bambu-gray" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-white truncate">{translateTypeName(type.name, t)}</div>
                    <div className="text-xs text-bambu-gray mt-0.5 flex items-center gap-1">
                      {intervalType === 'days' ? <Calendar className="w-3 h-3" /> : <Timer className="w-3 h-3" />}
                      {formatIntervalLabel(type.default_interval_hours, intervalType, t)}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
          {/* Custom types */}
          {customTypes.map((type) => {
            const Icon = getIcon(type.icon);
            const intervalType = type.interval_type || 'hours';
            const isEditing = editingType?.id === type.id;

            if (isEditing) {
              return (
                <div key={type.id} className="bg-bambu-dark-secondary rounded-xl p-4 border border-bambu-green">
                  <div className="space-y-3">
                    <input
                      type="text"
                      value={editTypeName}
                      onChange={(e) => setEditTypeName(e.target.value)}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                      placeholder={t('common.name')}
                      autoFocus
                    />
                    <div className="flex gap-2">
                      <select
                        value={editTypeIntervalType}
                        onChange={(e) => setEditTypeIntervalType(e.target.value as 'hours' | 'days')}
                        className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                      >
                        <option value="hours">{t('maintenance.printHours')}</option>
                        <option value="days">{t('maintenance.calendarDays')}</option>
                      </select>
                      <input
                        type="number"
                        value={editTypeInterval}
                        onChange={(e) => setEditTypeInterval(e.target.value)}
                        className="w-24 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                        min="1"
                      />
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {Object.keys(iconMap).map((iconName) => {
                        const IconComp = iconMap[iconName];
                        return (
                          <button
                            key={iconName}
                            type="button"
                            onClick={() => setEditTypeIcon(iconName)}
                            className={`p-1.5 rounded transition-colors ${
                              editTypeIcon === iconName
                                ? 'bg-bambu-green text-white'
                                : 'bg-bambu-dark text-bambu-gray hover:text-white'
                            }`}
                          >
                            <IconComp className="w-3.5 h-3.5" />
                          </button>
                        );
                      })}
                    </div>
                    <input
                      type="url"
                      value={editTypeWikiUrl}
                      onChange={(e) => setEditTypeWikiUrl(e.target.value)}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                      placeholder={t('maintenance.docLinkPlaceholder')}
                    />
                    <div className="flex gap-2">
                      <Button size="sm" onClick={handleSaveEditType} disabled={!editTypeName.trim()}>
                        {t('common.save')}
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => setEditingType(null)}>
                        {t('common.cancel')}
                      </Button>
                    </div>
                  </div>
                </div>
              );
            }

            const assignedPrinters = getAssignedPrinters(type.id);
            const unassignedPrinters = getUnassignedPrinters(type.id);
            const isExpanded = expandedType === type.id;

            return (
              <div key={type.id} className="bg-bambu-dark-secondary rounded-xl p-4 border border-bambu-green/30">
                <div className="flex items-center gap-3">
                  <div className="p-2.5 bg-bambu-green/20 rounded-lg">
                    <Icon className="w-5 h-5 text-bambu-green" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-white truncate">{translateTypeName(type.name, t)}</span>
                      <span className="px-1.5 py-0.5 bg-bambu-green/20 text-bambu-green text-[10px] font-medium rounded">
                        {t('maintenance.custom')}
                      </span>
                    </div>
                    <div className="text-xs text-bambu-gray mt-0.5 flex items-center gap-1">
                      {intervalType === 'days' ? <Calendar className="w-3 h-3" /> : <Timer className="w-3 h-3" />}
                      {formatIntervalLabel(type.default_interval_hours, intervalType, t)}
                    </div>
                  </div>
                  <button
                    onClick={() => setExpandedType(isExpanded ? null : type.id)}
                    className={`px-2 py-1 rounded-lg border transition-colors flex items-center gap-1 ${
                      assignedPrinters.length > 0
                        ? 'border-bambu-green/50 bg-bambu-green/10 text-bambu-green hover:bg-bambu-green/20'
                        : 'border-orange-400/50 bg-orange-400/10 text-orange-400 hover:bg-orange-400/20'
                    }`}
                    title={t('maintenance.printersAssigned', { count: assignedPrinters.length })}
                  >
                    <Printer className="w-3 h-3" />
                    <span className="text-xs font-medium">{assignedPrinters.length}</span>
                    <ChevronDown className={`w-3 h-3 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                  </button>
                  <button
                    onClick={() => startEditType(type)}
                    disabled={!hasPermission('maintenance:update')}
                    title={!hasPermission('maintenance:update') ? t('maintenance.noPermissionEditTypes') : undefined}
                    className={`p-2 rounded-lg hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors ${!hasPermission('maintenance:update') ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <Edit3 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(t('maintenance.deleteTypeConfirm', { name: type.name }))) {
                        onDeleteType(type.id);
                      }
                    }}
                    disabled={!hasPermission('maintenance:delete')}
                    title={!hasPermission('maintenance:delete') ? t('maintenance.noPermissionDeleteTypes') : undefined}
                    className={`p-2 rounded-lg hover:bg-bambu-dark text-bambu-gray hover:text-red-400 transition-colors ${!hasPermission('maintenance:delete') ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>

                {/* Printer assignment management */}
                {isExpanded && (
                  <div className="mt-3 pt-3 border-t border-bambu-dark-tertiary">
                    <p className="text-xs text-bambu-gray mb-2">{t('maintenance.assignedToPrinters')}</p>
                    {assignedPrinters.length === 0 ? (
                      <p className="text-xs text-orange-400">{t('maintenance.noPrintersAssigned')}</p>
                    ) : (
                      <div className="flex flex-wrap gap-1 mb-2">
                        {assignedPrinters.map(p => (
                          <span
                            key={p.printerId}
                            className="inline-flex items-center gap-1 px-2 py-1 bg-bambu-dark rounded text-xs text-white"
                          >
                            {p.printerName}
                            <button
                              onClick={() => p.itemId && onRemoveItem(p.itemId)}
                              disabled={!hasPermission('maintenance:delete')}
                              title={!hasPermission('maintenance:delete') ? t('maintenance.noPermissionRemove') : t('maintenance.removeFromPrinter')}
                              className={`ml-1 ${hasPermission('maintenance:delete') ? 'hover:text-red-400' : 'opacity-50 cursor-not-allowed'}`}
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                    {unassignedPrinters.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        <span className="text-xs text-bambu-gray mr-1">{t('maintenance.addLabel')}</span>
                        {unassignedPrinters.map(p => (
                          <button
                            key={p.id}
                            onClick={() => onAssignType(p.id, type.id)}
                            disabled={!hasPermission('maintenance:create')}
                            title={!hasPermission('maintenance:create') ? t('maintenance.noPermissionAssign') : undefined}
                            className={`px-2 py-1 bg-bambu-dark rounded text-xs transition-colors ${hasPermission('maintenance:create') ? 'hover:bg-bambu-green/20 text-bambu-gray hover:text-bambu-green' : 'opacity-50 cursor-not-allowed text-bambu-gray'}`}
                          >
                            + {p.name}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Per-printer interval overrides */}
      {printerItems.length > 0 && (
        <div>
          <div className="mb-4">
            <h2 className="text-lg font-semibold text-white">{t('maintenance.intervalOverrides')}</h2>
            <p className="text-sm text-bambu-gray mt-1">{t('maintenance.customizeIntervals')}</p>
          </div>
          <div className="space-y-4">
            {printerItems.map((printer) => (
              <Card key={printer.printerId}>
                <CardContent className="py-4">
                  <h3 className="text-sm font-medium text-white mb-3">{printer.printerName}</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
                    {printer.items.map((item) => {
                      const Icon = getIcon(item.maintenance_type_icon);
                      const typeInfo = types.find(t => t.id === item.maintenance_type_id);
                      const defaultInterval = typeInfo?.default_interval_hours || item.interval_hours;
                      const defaultIntervalType = typeInfo?.interval_type || 'hours';
                      const intervalType = item.interval_type || 'hours';
                      const isEditing = editingInterval === item.id;

                      return (
                        <div key={item.id} className="flex items-center gap-2 p-2.5 bg-bambu-dark rounded-lg">
                          <Icon className="w-4 h-4 text-bambu-gray shrink-0" />
                          <span className="text-xs text-bambu-gray flex-1 truncate">{translateTypeName(item.maintenance_type_name, t)}</span>

                          {isEditing ? (
                            <div className="flex items-center gap-1">
                              {intervalTypeInput === 'days' ? (
                                <Calendar className="w-3.5 h-3.5 text-bambu-gray shrink-0" />
                              ) : (
                                <Timer className="w-3.5 h-3.5 text-bambu-gray shrink-0" />
                              )}
                              <select
                                value={intervalTypeInput}
                                onChange={(e) => setIntervalTypeInput(e.target.value as 'hours' | 'days')}
                                className="px-1.5 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-xs"
                              >
                                <option value="hours">{t('maintenance.printHours')}</option>
                                <option value="days">{t('maintenance.calendarDays')}</option>
                              </select>
                              <input
                                type="number"
                                value={intervalInput}
                                onChange={(e) => setIntervalInput(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') handleSaveInterval(item.id, defaultInterval, defaultIntervalType);
                                  if (e.key === 'Escape') setEditingInterval(null);
                                }}
                                className="w-16 px-2 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-xs"
                                min="1"
                              />
                              <Button size="sm" onClick={() => handleSaveInterval(item.id, defaultInterval, defaultIntervalType)}>{t('common.ok')}</Button>
                            </div>
                          ) : (
                            <button
                              onClick={() => {
                                if (!hasPermission('maintenance:update')) return;
                                setEditingInterval(item.id);
                                setIntervalInput(item.interval_hours.toString());
                                setIntervalTypeInput(intervalType);
                              }}
                              disabled={!hasPermission('maintenance:update')}
                              title={!hasPermission('maintenance:update') ? t('maintenance.noPermissionEditIntervals') : undefined}
                              className={`px-2 py-1 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-xs font-medium text-white transition-colors flex items-center gap-1 ${hasPermission('maintenance:update') ? 'hover:bg-bambu-dark-secondary hover:border-bambu-green' : 'opacity-50 cursor-not-allowed'}`}
                            >
                              {intervalType === 'days' ? <Calendar className="w-3 h-3" /> : <Timer className="w-3 h-3" />}
                              {formatIntervalLabel(item.interval_hours, intervalType, t)}
                              <Edit3 className="w-3 h-3 text-bambu-gray" />
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}

      {printerItems.length === 0 && (
        <Card>
          <CardContent className="text-center py-12">
            <Clock className="w-12 h-12 mx-auto mb-4 text-bambu-gray/30" />
            <p className="text-bambu-gray">{t('common.noPrinters')}</p>
            <p className="text-sm text-bambu-gray/70 mt-1">
              {t('maintenance.addPrintersForMaintenance')}
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

type TabType = 'status' | 'settings';

export function MaintenancePage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const [activeTab, setActiveTab] = useState<TabType>('status');

  const { data: overview, isLoading } = useQuery({
    queryKey: ['maintenanceOverview'],
    queryFn: api.getMaintenanceOverview,
  });

  const { data: types } = useQuery({
    queryKey: ['maintenanceTypes'],
    queryFn: api.getMaintenanceTypes,
  });

  const performMutation = useMutation({
    mutationFn: ({ id, notes }: { id: number; notes?: string }) =>
      api.performMaintenance(id, notes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
      queryClient.invalidateQueries({ queryKey: ['maintenanceSummary'] });
      showToast(t('maintenance.markedComplete'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: { custom_interval_hours?: number | null; custom_interval_type?: 'hours' | 'days' | null; enabled?: boolean } }) =>
      api.updateMaintenanceItem(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // addTypeMutation removed - we now handle type creation with printer assignment
  // directly in onAddType callback

  const updateTypeMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<{ name: string; default_interval_hours: number; interval_type: 'hours' | 'days'; icon: string }> }) =>
      api.updateMaintenanceType(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceTypes'] });
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
      showToast(t('maintenance.typeUpdated'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const deleteTypeMutation = useMutation({
    mutationFn: api.deleteMaintenanceType,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceTypes'] });
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
      showToast(t('maintenance.typeDeleted'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const setHoursMutation = useMutation({
    mutationFn: ({ printerId, hours }: { printerId: number; hours: number }) =>
      api.setPrinterHours(printerId, hours),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
      queryClient.invalidateQueries({ queryKey: ['maintenanceSummary'] });
      showToast(t('maintenance.hoursUpdated'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const assignTypeMutation = useMutation({
    mutationFn: ({ printerId, typeId }: { printerId: number; typeId: number }) =>
      api.assignMaintenanceType(printerId, typeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
      showToast(t('maintenance.printerAssigned'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const removeItemMutation = useMutation({
    mutationFn: api.removeMaintenanceItem,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
      showToast(t('maintenance.printerRemoved'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const handlePerform = (id: number) => {
    performMutation.mutate({ id });
  };

  const handleToggle = (id: number, enabled: boolean) => {
    updateMutation.mutate({ id, data: { enabled } });
  };

  const handleSetHours = (printerId: number, hours: number) => {
    setHoursMutation.mutate({ printerId, hours });
  };

  if (isLoading) {
    return (
      <div className="p-4 md:p-8 flex justify-center">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  const totalDue = overview?.reduce((sum, p) => sum + p.due_count, 0) || 0;
  const totalWarning = overview?.reduce((sum, p) => sum + p.warning_count, 0) || 0;

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white">{t('maintenance.title')}</h1>
        <p className="text-bambu-gray text-sm mt-1">
          {activeTab === 'status' ? (
            <>
              {totalDue > 0 && <span className="text-red-400">{t('maintenance.tasksOverdue', { count: totalDue })}</span>}
              {totalDue > 0 && totalWarning > 0 && ' · '}
              {totalWarning > 0 && <span className="text-amber-400">{t('maintenance.countDueSoon', { count: totalWarning })}</span>}
              {totalDue === 0 && totalWarning === 0 && <span className="text-bambu-green">{t('maintenance.allOk')}</span>}
            </>
          ) : (
            t('maintenance.configureDescription')
          )}
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-bambu-dark-tertiary">
        <button
          onClick={() => setActiveTab('status')}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            activeTab === 'status'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray border-transparent hover:text-white'
          }`}
        >
          {t('common.status')}
        </button>
        <button
          onClick={() => setActiveTab('settings')}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            activeTab === 'settings'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray border-transparent hover:text-white'
          }`}
        >
          {t('settings.title')}
        </button>
      </div>

      {/* Tab content */}
      {activeTab === 'status' ? (
        <div className="space-y-6">
          {overview && overview.length > 0 ? (
            [...overview].sort((a, b) => {
              // Sort printers with issues first
              const aScore = a.due_count * 10 + a.warning_count;
              const bScore = b.due_count * 10 + b.warning_count;
              if (aScore !== bScore) return bScore - aScore;
              return a.printer_name.localeCompare(b.printer_name);
            }).map((printerOverview) => (
              <PrinterSection
                key={printerOverview.printer_id}
                overview={printerOverview}
                onPerform={handlePerform}
                onToggle={handleToggle}
                onSetHours={handleSetHours}
                hasPermission={hasPermission}
              />
            ))
          ) : (
            <Card>
              <CardContent className="text-center py-16">
                <Wrench className="w-16 h-16 mx-auto mb-4 text-bambu-gray/30" />
                <p className="text-lg font-medium text-white mb-2">{t('common.noPrinters')}</p>
                <p className="text-bambu-gray">{t('maintenance.addPrintersToTrack')}</p>
              </CardContent>
            </Card>
          )}
        </div>
      ) : (
        <SettingsSection
          overview={overview}
          types={types || []}
          onUpdateInterval={(id, data) =>
            updateMutation.mutate({ id, data })
          }
          onAddType={async (data, printerIds) => {
            // Create the type first, then assign to selected printers
            const newType = await api.createMaintenanceType(data);
            // Assign to each selected printer
            for (const printerId of printerIds) {
              await api.assignMaintenanceType(printerId, newType.id);
            }
            queryClient.invalidateQueries({ queryKey: ['maintenanceTypes'] });
            queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
            showToast(t('maintenance.typeAdded'));
          }}
          onUpdateType={(id, data) => updateTypeMutation.mutate({ id, data })}
          onDeleteType={(id) => deleteTypeMutation.mutate(id)}
          onAssignType={(printerId, typeId) => assignTypeMutation.mutate({ printerId, typeId })}
          onRemoveItem={(itemId) => removeItemMutation.mutate(itemId)}
          hasPermission={hasPermission}
        />
      )}
    </div>
  );
}
