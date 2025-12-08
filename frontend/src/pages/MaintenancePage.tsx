import { useState } from 'react';
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
} from 'lucide-react';
import { api } from '../api/client';
import type { MaintenanceStatus, PrinterMaintenanceOverview, MaintenanceType } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { Toggle } from '../components/Toggle';
import { useToast } from '../contexts/ToastContext';

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

function formatDuration(value: number, type: 'hours' | 'days'): string {
  if (type === 'days') {
    if (value < 1) return 'Today';
    if (value === 1) return '1 day';
    if (value < 7) return `${Math.round(value)} days`;
    if (value < 30) return `${Math.round(value / 7)} weeks`;
    return `${Math.round(value / 30)} months`;
  } else {
    if (value < 1) return `${Math.round(value * 60)}m`;
    if (value < 10) return `${value.toFixed(1)}h`;
    return `${Math.round(value)}h`;
  }
}

function formatIntervalLabel(value: number, type: 'hours' | 'days'): string {
  if (type === 'days') {
    if (value === 1) return '1 day';
    if (value === 7) return '1 week';
    if (value === 14) return '2 weeks';
    if (value === 30) return '1 month';
    if (value === 60) return '2 months';
    if (value === 90) return '3 months';
    if (value === 180) return '6 months';
    if (value === 365) return '1 year';
    return `${value} days`;
  }
  return `${value}h`;
}

// Maintenance item card - cleaner, more visual design
function MaintenanceCard({
  item,
  onPerform,
  onToggle,
}: {
  item: MaintenanceStatus;
  onPerform: (id: number) => void;
  onToggle: (id: number, enabled: boolean) => void;
}) {
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
    if (!item.enabled) return 'Disabled';

    if (intervalType === 'days') {
      const daysUntil = item.days_until_due ?? 0;
      if (item.is_due) return `Overdue by ${formatDuration(Math.abs(daysUntil), 'days')}`;
      if (item.is_warning) return `Due in ${formatDuration(daysUntil, 'days')}`;
      return `${formatDuration(daysUntil, 'days')} left`;
    } else {
      if (item.is_due) return `Overdue by ${formatDuration(Math.abs(item.hours_until_due), 'hours')}`;
      if (item.is_warning) return `Due in ${formatDuration(item.hours_until_due, 'hours')}`;
      return `${formatDuration(item.hours_until_due, 'hours')} left`;
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
              {item.maintenance_type_name}
            </h3>
            {intervalType === 'days' && (
              <span title="Time-based interval">
                <Calendar className="w-3.5 h-3.5 text-bambu-gray shrink-0" />
              </span>
            )}
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
          <Toggle
            checked={item.enabled}
            onChange={(checked) => onToggle(item.id, checked)}
          />
          <Button
            size="sm"
            variant={item.is_due ? 'primary' : 'secondary'}
            onClick={() => onPerform(item.id)}
            disabled={!item.enabled}
            className="!px-3"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reset
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
}: {
  overview: PrinterMaintenanceOverview;
  onPerform: (id: number) => void;
  onToggle: (id: number, enabled: boolean) => void;
  onSetHours: (printerId: number, hours: number) => void;
}) {
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
                  {overview.due_count} overdue
                </span>
              )}
              {overview.warning_count > 0 && (
                <span className="px-2.5 py-1 bg-amber-500/20 text-amber-400 text-xs font-medium rounded-full flex items-center gap-1.5">
                  <Clock className="w-3 h-3" />
                  {overview.warning_count} due soon
                </span>
              )}
              {overview.due_count === 0 && overview.warning_count === 0 && (
                <span className="px-2.5 py-1 bg-bambu-green/20 text-bambu-green text-xs font-medium rounded-full flex items-center gap-1.5">
                  <Check className="w-3 h-3" />
                  All good
                </span>
              )}
            </div>
          </div>
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-bambu-gray hover:text-white hover:bg-bambu-dark rounded-lg transition-colors"
          >
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            {expanded ? 'Collapse' : 'Expand'}
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
                <span className="text-xs text-bambu-gray">hours</span>
                <Button size="sm" onClick={handleSaveHours}>Save</Button>
                <Button size="sm" variant="secondary" onClick={() => setEditingHours(false)}>Cancel</Button>
              </div>
            ) : (
              <button
                onClick={() => {
                  setHoursInput(Math.round(overview.total_print_hours).toString());
                  setEditingHours(true);
                }}
                className="group"
              >
                <div className="text-sm font-medium text-white group-hover:text-bambu-green transition-colors flex items-center gap-1">
                  {Math.floor(overview.total_print_hours)} hours
                  <Edit3 className="w-3 h-3 text-bambu-gray group-hover:text-bambu-green" />
                </div>
                <div className="text-xs text-bambu-gray">Total print time</div>
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
                  {nextTask.maintenance_type_name}
                </div>
                <div className={`text-xs ${nextTask.is_due ? 'text-red-400/70' : 'text-amber-400/70'}`}>
                  {nextTask.is_due ? 'Overdue' : 'Due soon'}
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
}: {
  overview: PrinterMaintenanceOverview[] | undefined;
  types: MaintenanceType[];
  onUpdateInterval: (id: number, data: { custom_interval_hours?: number | null; custom_interval_type?: 'hours' | 'days' | null }) => void;
  onAddType: (data: { name: string; description?: string; default_interval_hours: number; interval_type: 'hours' | 'days'; icon?: string }) => void;
  onUpdateType: (id: number, data: { name?: string; default_interval_hours?: number; interval_type?: 'hours' | 'days'; icon?: string }) => void;
  onDeleteType: (id: number) => void;
}) {
  const [editingInterval, setEditingInterval] = useState<number | null>(null);
  const [intervalInput, setIntervalInput] = useState('');
  const [intervalTypeInput, setIntervalTypeInput] = useState<'hours' | 'days'>('hours');
  const [showAddType, setShowAddType] = useState(false);
  const [newTypeName, setNewTypeName] = useState('');
  const [newTypeInterval, setNewTypeInterval] = useState('100');
  const [newTypeIntervalType, setNewTypeIntervalType] = useState<'hours' | 'days'>('hours');
  const [newTypeIcon, setNewTypeIcon] = useState('Wrench');

  // Edit type state
  const [editingType, setEditingType] = useState<MaintenanceType | null>(null);
  const [editTypeName, setEditTypeName] = useState('');
  const [editTypeInterval, setEditTypeInterval] = useState('');
  const [editTypeIntervalType, setEditTypeIntervalType] = useState<'hours' | 'days'>('hours');
  const [editTypeIcon, setEditTypeIcon] = useState('Wrench');

  const startEditType = (type: MaintenanceType) => {
    setEditingType(type);
    setEditTypeName(type.name);
    setEditTypeInterval(type.default_interval_hours.toString());
    setEditTypeIntervalType(type.interval_type || 'hours');
    setEditTypeIcon(type.icon || 'Wrench');
  };

  const handleSaveEditType = () => {
    if (editingType && editTypeName.trim() && parseFloat(editTypeInterval) > 0) {
      onUpdateType(editingType.id, {
        name: editTypeName.trim(),
        default_interval_hours: parseFloat(editTypeInterval),
        interval_type: editTypeIntervalType,
        icon: editTypeIcon,
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
    if (newTypeName.trim() && parseFloat(newTypeInterval) > 0) {
      onAddType({
        name: newTypeName.trim(),
        default_interval_hours: parseFloat(newTypeInterval),
        interval_type: newTypeIntervalType,
        icon: newTypeIcon,
      });
      setNewTypeName('');
      setNewTypeInterval('100');
      setNewTypeIntervalType('hours');
      setShowAddType(false);
    }
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
            <h2 className="text-lg font-semibold text-white">Maintenance Types</h2>
            <p className="text-sm text-bambu-gray mt-1">System types and your custom maintenance tasks</p>
          </div>
          <Button onClick={() => setShowAddType(!showAddType)}>
            <Plus className="w-4 h-4" />
            Add Custom Type
          </Button>
        </div>

        {/* Add custom type form */}
        {showAddType && (
          <Card className="mb-6">
            <CardContent className="py-4">
              <form onSubmit={handleAddType}>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                  <div className="lg:col-span-2">
                    <label className="block text-xs text-bambu-gray mb-1.5">Name</label>
                    <input
                      type="text"
                      value={newTypeName}
                      onChange={(e) => setNewTypeName(e.target.value)}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                      placeholder="e.g., Replace HEPA Filter"
                      autoFocus
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1.5">Interval Type</label>
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
                      <option value="hours">Print Hours</option>
                      <option value="days">Calendar Days</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1.5">
                      Interval ({newTypeIntervalType === 'days' ? 'days' : 'hours'})
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
                    <label className="block text-xs text-bambu-gray mb-1.5">Icon</label>
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
                  <div className="flex gap-2">
                    <Button type="button" variant="secondary" onClick={() => setShowAddType(false)}>
                      Cancel
                    </Button>
                    <Button type="submit" disabled={!newTypeName.trim()}>
                      Add Type
                    </Button>
                  </div>
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
                    <div className="text-sm font-medium text-white truncate">{type.name}</div>
                    <div className="text-xs text-bambu-gray mt-0.5 flex items-center gap-1">
                      {intervalType === 'days' ? <Calendar className="w-3 h-3" /> : <Timer className="w-3 h-3" />}
                      {formatIntervalLabel(type.default_interval_hours, intervalType)}
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
                      placeholder="Name"
                      autoFocus
                    />
                    <div className="flex gap-2">
                      <select
                        value={editTypeIntervalType}
                        onChange={(e) => setEditTypeIntervalType(e.target.value as 'hours' | 'days')}
                        className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                      >
                        <option value="hours">Print Hours</option>
                        <option value="days">Calendar Days</option>
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
                    <div className="flex gap-2">
                      <Button size="sm" onClick={handleSaveEditType} disabled={!editTypeName.trim()}>
                        Save
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => setEditingType(null)}>
                        Cancel
                      </Button>
                    </div>
                  </div>
                </div>
              );
            }

            return (
              <div key={type.id} className="bg-bambu-dark-secondary rounded-xl p-4 border border-bambu-green/30">
                <div className="flex items-center gap-3">
                  <div className="p-2.5 bg-bambu-green/20 rounded-lg">
                    <Icon className="w-5 h-5 text-bambu-green" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-white truncate">{type.name}</span>
                      <span className="px-1.5 py-0.5 bg-bambu-green/20 text-bambu-green text-[10px] font-medium rounded">
                        Custom
                      </span>
                    </div>
                    <div className="text-xs text-bambu-gray mt-0.5 flex items-center gap-1">
                      {intervalType === 'days' ? <Calendar className="w-3 h-3" /> : <Timer className="w-3 h-3" />}
                      {formatIntervalLabel(type.default_interval_hours, intervalType)}
                    </div>
                  </div>
                  <button
                    onClick={() => startEditType(type)}
                    className="p-2 rounded-lg hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
                  >
                    <Edit3 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Delete "${type.name}"?`)) {
                        onDeleteType(type.id);
                      }
                    }}
                    className="p-2 rounded-lg hover:bg-bambu-dark text-bambu-gray hover:text-red-400 transition-colors"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Per-printer interval overrides */}
      {printerItems.length > 0 && (
        <div>
          <div className="mb-4">
            <h2 className="text-lg font-semibold text-white">Interval Overrides</h2>
            <p className="text-sm text-bambu-gray mt-1">Customize intervals for specific printers</p>
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
                          <span className="text-xs text-bambu-gray flex-1 truncate">{item.maintenance_type_name}</span>

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
                                <option value="hours">Print Hours</option>
                                <option value="days">Calendar Days</option>
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
                              <Button size="sm" onClick={() => handleSaveInterval(item.id, defaultInterval, defaultIntervalType)}>OK</Button>
                            </div>
                          ) : (
                            <button
                              onClick={() => {
                                setEditingInterval(item.id);
                                setIntervalInput(item.interval_hours.toString());
                                setIntervalTypeInput(intervalType);
                              }}
                              className="px-2 py-1 bg-bambu-dark-tertiary hover:bg-bambu-dark-secondary border border-bambu-dark-tertiary hover:border-bambu-green rounded text-xs font-medium text-white transition-colors flex items-center gap-1"
                            >
                              {intervalType === 'days' ? <Calendar className="w-3 h-3" /> : <Timer className="w-3 h-3" />}
                              {formatIntervalLabel(item.interval_hours, intervalType)}
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
            <p className="text-bambu-gray">No printers configured</p>
            <p className="text-sm text-bambu-gray/70 mt-1">
              Add printers to configure maintenance intervals
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

type TabType = 'status' | 'settings';

export function MaintenancePage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
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
      showToast('Maintenance marked as complete');
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

  const addTypeMutation = useMutation({
    mutationFn: api.createMaintenanceType,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceTypes'] });
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
      showToast('Maintenance type added');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const updateTypeMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<{ name: string; default_interval_hours: number; interval_type: 'hours' | 'days'; icon: string }> }) =>
      api.updateMaintenanceType(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceTypes'] });
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
      showToast('Maintenance type updated');
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
      showToast('Maintenance type deleted');
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
      showToast('Print hours updated');
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
      <div className="p-8 flex justify-center">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  const totalDue = overview?.reduce((sum, p) => sum + p.due_count, 0) || 0;
  const totalWarning = overview?.reduce((sum, p) => sum + p.warning_count, 0) || 0;

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white">Maintenance</h1>
        <p className="text-bambu-gray text-sm mt-1">
          {activeTab === 'status' ? (
            <>
              {totalDue > 0 && <span className="text-red-400">{totalDue} task{totalDue !== 1 ? 's' : ''} overdue</span>}
              {totalDue > 0 && totalWarning > 0 && ' · '}
              {totalWarning > 0 && <span className="text-amber-400">{totalWarning} due soon</span>}
              {totalDue === 0 && totalWarning === 0 && <span className="text-bambu-green">All maintenance up to date</span>}
            </>
          ) : (
            'Configure maintenance types and intervals'
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
          Status
        </button>
        <button
          onClick={() => setActiveTab('settings')}
          className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            activeTab === 'settings'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray border-transparent hover:text-white'
          }`}
        >
          Settings
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
              />
            ))
          ) : (
            <Card>
              <CardContent className="text-center py-16">
                <Wrench className="w-16 h-16 mx-auto mb-4 text-bambu-gray/30" />
                <p className="text-lg font-medium text-white mb-2">No printers configured</p>
                <p className="text-bambu-gray">Add printers to start tracking maintenance</p>
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
          onAddType={(data) => addTypeMutation.mutate(data)}
          onUpdateType={(id, data) => updateTypeMutation.mutate({ id, data })}
          onDeleteType={(id) => deleteTypeMutation.mutate(id)}
        />
      )}
    </div>
  );
}
