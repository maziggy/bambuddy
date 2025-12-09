import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Cloud,
  LogIn,
  LogOut,
  Loader2,
  Settings2,
  Printer as PrinterIcon,
  Droplet,
  X,
  Key,
  RefreshCw,
  Gauge,
  Pencil,
  Trash2,
  Save,
  AlertTriangle,
  Search,
  Plus,
  Copy,
  Clock,
  Layers,
  Filter,
  ChevronDown,
  ArrowUp,
  Upload,
  Download,
  Sparkles,
  Check,
  AlertCircle,
  Code,
  Sliders,
  List,
  Eye,
  EyeOff,
  GitCompare,
  ArrowRight,
  Equal,
  Minus as MinusIcon,
  Plus as PlusIcon,
} from 'lucide-react';
import { api } from '../api/client';
import type { SlicerSetting, SlicerSettingsResponse, SlicerSettingDetail, SlicerSettingCreate, Printer, FieldDefinition } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { KProfilesView } from '../components/KProfilesView';

type ProfileTab = 'cloud' | 'kprofiles';
type LoginStep = 'email' | 'code' | 'token';
type PresetType = 'all' | 'filament' | 'printer' | 'process';

// Extract metadata from preset name or inherits field
function extractMetadata(name: string, inherits?: string): {
  printer: string | null;
  nozzle: string | null;
  layerHeight: string | null;
  filamentType: string | null;
} {
  const searchIn = `${name} ${inherits || ''}`;

  // Extract printer (e.g., "X1C", "P1S", "A1", "H2D")
  const printerMatch = searchIn.match(/@?\s*(?:BBL\s+)?(?:Bambu\s+Lab\s+)?([XPAH][1-9][A-Z]?(?:\s*(?:Carbon|mini))?|H2D)/i);
  const printer = printerMatch ? printerMatch[1].trim() : null;

  // Extract nozzle size (e.g., "0.4 nozzle", "0.6mm")
  const nozzleMatch = searchIn.match(/(\d+\.?\d*)\s*(?:mm\s*)?nozzle|nozzle\s*(\d+\.?\d*)/i);
  const nozzle = nozzleMatch ? (nozzleMatch[1] || nozzleMatch[2]) + 'mm' : null;

  // Extract layer height (e.g., "0.20mm", "0.08mm Extra Fine")
  const layerMatch = searchIn.match(/(\d+\.?\d*)mm\s*(?:Standard|Fine|Extra Fine|Draft|Quality)?/i);
  const layerHeight = layerMatch ? layerMatch[1] + 'mm' : null;

  // Extract filament type (e.g., "PLA", "PETG", "ABS", "TPU")
  const filamentMatch = searchIn.match(/\b(PLA|PETG|ABS|ASA|TPU|PC|PA|PVA|HIPS|PP|PET(?:-?CF)?|PA(?:-?CF)?|PLA(?:-?CF)?)\b/i);
  const filamentType = filamentMatch ? filamentMatch[1].toUpperCase() : null;

  return { printer, nozzle, layerHeight, filamentType };
}

// Check if preset is user-created (editable)
function isUserPreset(settingId: string): boolean {
  return /^(P[FPM]US|PF\d|PP\d)/.test(settingId);
}

// Format relative time
function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
}

// ============================================================================
// LOGIN FORM
// ============================================================================

function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const { showToast } = useToast();
  const [step, setStep] = useState<LoginStep>('email');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [token, setToken] = useState('');
  const [region, setRegion] = useState('global');

  const loginMutation = useMutation({
    mutationFn: () => api.cloudLogin(email, password, region),
    onSuccess: (result) => {
      if (result.success) {
        showToast('Logged in successfully');
        onSuccess();
      } else if (result.needs_verification) {
        showToast('Verification code sent to your email');
        setStep('code');
      } else {
        showToast(result.message, 'error');
      }
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const verifyMutation = useMutation({
    mutationFn: () => api.cloudVerify(email, code),
    onSuccess: (result) => {
      if (result.success) {
        showToast('Logged in successfully');
        onSuccess();
      } else {
        showToast(result.message, 'error');
      }
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const tokenMutation = useMutation({
    mutationFn: () => api.cloudSetToken(token),
    onSuccess: () => {
      showToast('Token set successfully');
      onSuccess();
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (step === 'email') loginMutation.mutate();
    else if (step === 'code') verifyMutation.mutate();
    else if (step === 'token') tokenMutation.mutate();
  };

  const isPending = loginMutation.isPending || verifyMutation.isPending || tokenMutation.isPending;

  return (
    <Card className="max-w-md mx-auto">
      <CardContent>
        <div className="text-center mb-6">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-bambu-green/20 mb-3">
            <Cloud className="w-6 h-6 text-bambu-green" />
          </div>
          <h2 className="text-xl font-semibold text-white">Connect to Bambu Cloud</h2>
          <p className="text-sm text-bambu-gray mt-1">Sync your slicer presets across devices</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {step === 'email' && (
            <>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray-dark focus:border-bambu-green focus:outline-none"
                  placeholder="your@email.com"
                  required
                />
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray-dark focus:border-bambu-green focus:outline-none"
                  placeholder="••••••••"
                  required
                />
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Region</label>
                <select
                  value={region}
                  onChange={(e) => setRegion(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="global">Global</option>
                  <option value="china">China</option>
                </select>
              </div>
            </>
          )}

          {step === 'code' && (
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Verification Code</label>
              <p className="text-xs text-bambu-gray mb-2">Check your email ({email}) for a 6-digit code</p>
              <input
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                className="w-full px-3 py-3 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-center text-2xl tracking-widest font-mono focus:border-bambu-green focus:outline-none"
                placeholder="000000"
                maxLength={6}
                required
              />
            </div>
          )}

          {step === 'token' && (
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Access Token</label>
              <p className="text-xs text-bambu-gray mb-2">Paste your Bambu Lab access token (from Bambu Studio)</p>
              <textarea
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-xs font-mono placeholder-bambu-gray-dark focus:border-bambu-green focus:outline-none resize-none"
                placeholder="eyJ..."
                rows={4}
                required
              />
            </div>
          )}

          <div className="flex gap-2">
            {step === 'code' && (
              <Button type="button" variant="secondary" onClick={() => setStep('email')} className="flex-1">
                Back
              </Button>
            )}
            <Button type="submit" disabled={isPending} className="flex-1">
              {isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />}
              {step === 'email' ? 'Login' : step === 'code' ? 'Verify' : 'Set Token'}
            </Button>
          </div>

          {step === 'email' && (
            <div className="pt-4 border-t border-bambu-dark-tertiary">
              <button
                type="button"
                onClick={() => setStep('token')}
                className="text-sm text-bambu-gray hover:text-white flex items-center gap-2 transition-colors"
              >
                <Key className="w-4 h-4" />
                Use access token instead
              </button>
            </div>
          )}

          {step === 'token' && (
            <div className="pt-4 border-t border-bambu-dark-tertiary">
              <button
                type="button"
                onClick={() => setStep('email')}
                className="text-sm text-bambu-gray hover:text-white flex items-center gap-2 transition-colors"
              >
                <LogIn className="w-4 h-4" />
                Login with email instead
              </button>
            </div>
          )}
        </form>
      </CardContent>
    </Card>
  );
}

// ============================================================================
// FILTER DROPDOWN
// ============================================================================

function FilterDropdown({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string; count?: number }[];
  onChange: (value: string) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const selectedOption = options.find(o => o.value === value);

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-white hover:border-bambu-gray-dark transition-colors"
      >
        <span className="text-bambu-gray">{label}:</span>
        <span>{selectedOption?.label || 'All'}</span>
        <ChevronDown className={`w-4 h-4 text-bambu-gray transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setIsOpen(false)} />
          <div className="absolute top-full left-0 mt-1 min-w-[160px] bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl z-20 py-1 max-h-60 overflow-y-auto">
            {options.map((option) => (
              <button
                key={option.value}
                onClick={() => { onChange(option.value); setIsOpen(false); }}
                className={`w-full px-3 py-2 text-left text-sm flex items-center justify-between hover:bg-bambu-dark-tertiary transition-colors ${
                  value === option.value ? 'text-bambu-green' : 'text-white'
                }`}
              >
                <span>{option.label}</span>
                {option.count !== undefined && (
                  <span className="text-bambu-gray text-xs">{option.count}</span>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ============================================================================
// SCROLL TO TOP BUTTON
// ============================================================================

function ScrollToTop() {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const toggleVisibility = () => {
      setIsVisible(window.scrollY > 300);
    };

    window.addEventListener('scroll', toggleVisibility);
    return () => window.removeEventListener('scroll', toggleVisibility);
  }, []);

  const scrollToTop = () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  if (!isVisible) return null;

  return (
    <button
      onClick={scrollToTop}
      className="fixed bottom-6 right-6 p-3 bg-bambu-green hover:bg-bambu-green-light text-white rounded-full shadow-lg shadow-bambu-green/25 transition-all z-40"
      aria-label="Scroll to top"
    >
      <ArrowUp className="w-5 h-5" />
    </button>
  );
}

// ============================================================================
// PRESET LIST ITEM (compact row style like K-Profiles)
// ============================================================================

function PresetListItem({
  setting,
  onClick,
  onDuplicate,
  compareMode,
  isCompareSelected,
  compareIndex,
  compareDisabled,
}: {
  setting: SlicerSetting;
  onClick: () => void;
  onDuplicate: () => void;
  compareMode?: boolean;
  isCompareSelected?: boolean;
  compareIndex?: number;
  compareDisabled?: boolean;
}) {
  const metadata = extractMetadata(setting.name);
  const isEditable = isUserPreset(setting.setting_id);

  return (
    <div className="flex items-center gap-2 group">
      <button
        onClick={onClick}
        disabled={compareDisabled}
        className={`flex-1 text-left px-3 py-2 rounded transition-colors ${
          isCompareSelected
            ? 'bg-blue-500/20 border border-blue-500/50'
            : compareDisabled
              ? 'bg-bambu-dark/50 opacity-40 cursor-not-allowed'
              : 'bg-bambu-dark hover:bg-bambu-dark-tertiary'
        } ${compareMode && !compareDisabled ? 'cursor-pointer' : ''}`}
      >
        <div className="flex items-center gap-2">
          {isCompareSelected && compareIndex !== undefined && (
            <span className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-500 text-white text-xs flex items-center justify-center font-medium">
              {compareIndex + 1}
            </span>
          )}
          {!isCompareSelected && isEditable && (
            <span className="flex-shrink-0 w-1.5 h-1.5 rounded-full bg-bambu-green" title="My preset (editable)" />
          )}
          <span className="text-white text-sm truncate flex-1" title={setting.name}>
            {setting.name}
          </span>
          {/* Show relevant metadata tag */}
          {metadata.filamentType && setting.type === 'filament' && (
            <span className="text-xs text-bambu-gray whitespace-nowrap">
              {metadata.filamentType}
            </span>
          )}
          {metadata.layerHeight && setting.type === 'process' && (
            <span className="text-xs text-bambu-gray whitespace-nowrap">
              {metadata.layerHeight}
            </span>
          )}
          {metadata.printer && (
            <span className="text-xs text-bambu-gray whitespace-nowrap">
              {metadata.printer}
            </span>
          )}
        </div>
      </button>
      <button
        onClick={(e) => { e.stopPropagation(); onDuplicate(); }}
        className="opacity-0 group-hover:opacity-100 text-bambu-gray hover:text-white transition-all p-1"
        title="Duplicate"
      >
        <Copy className="w-4 h-4" />
      </button>
    </div>
  );
}

// ============================================================================
// PRESET DETAIL MODAL
// ============================================================================

// Format JSON for display, converting escaped newlines to real newlines in string values
function formatJsonForDisplay(obj: unknown, indent = 0): string {
  const spaces = '  '.repeat(indent);

  if (obj === null) return 'null';
  if (obj === undefined) return 'undefined';

  if (typeof obj === 'string') {
    // Convert escaped newlines to actual newlines for readability
    if (obj.includes('\\n') || obj.includes('\n')) {
      const formatted = obj
        .replace(/\\n/g, '\n')
        .replace(/\\"/g, '"')
        .replace(/\\t/g, '\t');
      // For multi-line strings, show them nicely indented
      const lines = formatted.split('\n');
      if (lines.length > 1) {
        return '"""\n' + lines.map(l => spaces + '  ' + l).join('\n') + '\n' + spaces + '"""';
      }
    }
    return JSON.stringify(obj);
  }

  if (typeof obj === 'number' || typeof obj === 'boolean') {
    return String(obj);
  }

  if (Array.isArray(obj)) {
    if (obj.length === 0) return '[]';
    const items = obj.map(item => spaces + '  ' + formatJsonForDisplay(item, indent + 1));
    return '[\n' + items.join(',\n') + '\n' + spaces + ']';
  }

  if (typeof obj === 'object') {
    const entries = Object.entries(obj);
    if (entries.length === 0) return '{}';
    const items = entries.map(([key, val]) =>
      spaces + '  ' + JSON.stringify(key) + ': ' + formatJsonForDisplay(val, indent + 1)
    );
    return '{\n' + items.join(',\n') + '\n' + spaces + '}';
  }

  return String(obj);
}

function PresetDetailModal({
  setting,
  onClose,
  onDeleted,
  onDuplicate,
  onEdit,
}: {
  setting: SlicerSetting;
  onClose: () => void;
  onDeleted: () => void;
  onDuplicate: () => void;
  onEdit: () => void;
}) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const { data: detail, isLoading } = useQuery<SlicerSettingDetail>({
    queryKey: ['cloudSettingDetail', setting.setting_id],
    queryFn: () => api.getCloudSettingDetail(setting.setting_id),
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteCloudSetting(setting.setting_id),
    onSuccess: () => {
      showToast('Preset deleted');
      queryClient.invalidateQueries({ queryKey: ['cloudSettings'] });
      onDeleted();
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const isEditable = isUserPreset(setting.setting_id);
  const metadata = extractMetadata(setting.name, detail?.setting?.inherits as string);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden">
        <CardContent className="p-0 flex flex-col min-h-0 flex-1">
          {/* Header */}
          <div className="flex-shrink-0 flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <h2 className="text-xl font-semibold text-white truncate">{setting.name}</h2>
                {isEditable && (
                  <span className="px-2 py-0.5 text-xs font-medium bg-bambu-green/20 text-bambu-green rounded-full">
                    Editable
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-1 text-sm text-bambu-gray">
                <span className="capitalize">{setting.type} preset</span>
                {metadata.printer && <><span>•</span><span>{metadata.printer}</span></>}
              </div>
            </div>
            <button onClick={onClose} className="p-2 text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary rounded-lg transition-colors">
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Content */}
          <div className="flex-1 min-h-0 overflow-y-auto p-4">
            {isLoading ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
              </div>
            ) : detail ? (
              <pre className="text-xs text-bambu-gray font-mono whitespace-pre-wrap break-all bg-bambu-dark p-4 rounded-lg border border-bambu-dark-tertiary overflow-x-auto max-w-full">
                {formatJsonForDisplay(detail)}
              </pre>
            ) : (
              <div className="text-center py-16 text-bambu-gray">Failed to load preset details</div>
            )}
          </div>

          {/* Footer */}
          {showDeleteConfirm ? (
            <div className="flex-shrink-0 p-4 border-t border-bambu-dark-tertiary bg-red-500/5">
              <div className="flex items-center gap-2 mb-3 text-red-400">
                <AlertTriangle className="w-5 h-5" />
                <span className="font-medium">Delete this preset?</span>
              </div>
              <p className="text-sm text-bambu-gray mb-4">
                This will permanently delete "{setting.name}" from Bambu Cloud. This cannot be undone.
              </p>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => setShowDeleteConfirm(false)} disabled={deleteMutation.isPending} className="flex-1">
                  Cancel
                </Button>
                <Button variant="danger" onClick={() => deleteMutation.mutate()} disabled={deleteMutation.isPending} className="flex-1">
                  {deleteMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                  Delete
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex-shrink-0 p-4 border-t border-bambu-dark-tertiary">
              <div className="flex gap-2">
                <Button variant="secondary" onClick={onClose} className="flex-1">Close</Button>
                <Button variant="secondary" onClick={onDuplicate}>
                  <Copy className="w-4 h-4" />
                  Duplicate
                </Button>
                {isEditable && (
                  <>
                    <Button variant="secondary" onClick={onEdit} disabled={isLoading || !detail}>
                      <Pencil className="w-4 h-4" />
                      Edit
                    </Button>
                    <Button variant="danger" onClick={() => setShowDeleteConfirm(true)}>
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// ============================================================================
// TEMPLATES
// ============================================================================

type EditorTab = 'common' | 'fields' | 'json';

interface CustomTemplate {
  id: string;
  name: string;
  description: string;
  type: 'filament' | 'print' | 'printer';
  settings: Record<string, unknown>;
  showInModal?: boolean; // If true, show in add/edit preset modals
}

// Load custom templates from localStorage
function loadCustomTemplates(): CustomTemplate[] {
  try {
    const stored = localStorage.getItem('bambusy_preset_templates');
    return stored ? JSON.parse(stored) : [];
  } catch {
    return [];
  }
}

// Save custom templates to localStorage
function saveCustomTemplates(templates: CustomTemplate[]) {
  localStorage.setItem('bambusy_preset_templates', JSON.stringify(templates));
}

// ============================================================================
// TEMPLATES MODAL (manage templates from main page)
// ============================================================================

function TemplatesModal({
  onClose,
  onApply,
}: {
  onClose: () => void;
  onApply: (template: CustomTemplate) => void;
}) {
  const { showToast } = useToast();
  const [templates, setTemplates] = useState<CustomTemplate[]>(loadCustomTemplates);
  const [filterType, setFilterType] = useState<'all' | 'filament' | 'print' | 'printer'>('all');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [editSettings, setEditSettings] = useState('{}');
  const [editSettingsError, setEditSettingsError] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);

  const filteredTemplates = filterType === 'all'
    ? templates
    : templates.filter(t => t.type === filterType);

  const saveTemplates = (updated: CustomTemplate[]) => {
    setTemplates(updated);
    saveCustomTemplates(updated);
  };

  const handleDelete = (id: string) => {
    const updated = templates.filter(t => t.id !== id);
    saveTemplates(updated);
    setDeleteConfirmId(null);
    showToast('Template deleted');
  };

  const handleEdit = (template: CustomTemplate) => {
    setEditingId(template.id);
    setEditName(template.name);
    setEditDesc(template.description);
    setEditSettings(JSON.stringify(template.settings, null, 2));
    setEditSettingsError(null);
  };

  const handleSaveEdit = () => {
    if (!editingId || !editName.trim()) return;
    try {
      const settings = JSON.parse(editSettings);
      const updated = templates.map(t =>
        t.id === editingId
          ? { ...t, name: editName.trim(), description: editDesc.trim(), settings }
          : t
      );
      saveTemplates(updated);
      setEditingId(null);
      showToast('Template updated');
    } catch (e) {
      setEditSettingsError((e as Error).message);
    }
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setEditName('');
    setEditDesc('');
    setEditSettings('{}');
    setEditSettingsError(null);
  };

  const toggleShowInModal = (id: string) => {
    const updated = templates.map(t =>
      t.id === id ? { ...t, showInModal: !t.showInModal } : t
    );
    saveTemplates(updated);
  };

  const typeLabels = {
    filament: { label: 'Filament', icon: Droplet, color: 'text-amber-400' },
    print: { label: 'Process', icon: Settings2, color: 'text-blue-400' },
    printer: { label: 'Printer', icon: PrinterIcon, color: 'text-purple-400' },
  };

  const templateToDelete = deleteConfirmId ? templates.find(t => t.id === deleteConfirmId) : null;

  // Handle Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (deleteConfirmId) {
          setDeleteConfirmId(null);
        } else if (editingId) {
          handleCancelEdit();
        } else {
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [deleteConfirmId, editingId, onClose]);

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      {/* Delete confirmation modal */}
      {templateToDelete && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-60">
          <Card className="w-full max-w-md">
            <CardContent className="p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="p-2 bg-red-500/20 rounded-lg">
                  <AlertTriangle className="w-6 h-6 text-red-400" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white">Delete Template</h3>
                  <p className="text-sm text-bambu-gray">This action cannot be undone</p>
                </div>
              </div>
              <p className="text-white mb-6">
                Are you sure you want to delete "<span className="font-medium">{templateToDelete.name}</span>"?
              </p>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => setDeleteConfirmId(null)} className="flex-1">
                  Cancel
                </Button>
                <Button onClick={() => handleDelete(deleteConfirmId!)} className="flex-1 bg-red-500 hover:bg-red-600">
                  <Trash2 className="w-4 h-4" />
                  Delete
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      <Card className="w-full max-w-2xl max-h-[80vh] flex flex-col">
        <CardContent className="p-0 flex flex-col h-full">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-amber-400" />
              Quick Templates
            </h2>
            <button onClick={onClose} className="text-bambu-gray hover:text-white">
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Filter row */}
          <div className="flex items-center gap-2 p-4 border-b border-bambu-dark-tertiary">
            <span className="text-sm text-bambu-gray">Type:</span>
            {(['all', 'filament', 'print', 'printer'] as const).map((type) => (
              <button
                key={type}
                onClick={() => setFilterType(type)}
                className={`px-3 py-1 text-sm rounded-lg transition-colors ${
                  filterType === type
                    ? 'bg-bambu-green text-white'
                    : 'bg-bambu-dark text-bambu-gray hover:text-white'
                }`}
              >
                {type === 'all' ? 'All' : typeLabels[type].label}
              </button>
            ))}
          </div>

          {/* Templates list */}
          <div className="flex-1 overflow-y-auto p-4">
            {filteredTemplates.length === 0 ? (
              <div className="text-center py-12 text-bambu-gray">
                <Sparkles className="w-12 h-12 mx-auto mb-4 opacity-30" />
                <p>No templates yet</p>
                <p className="text-sm mt-1">Create templates from the preset editor</p>
              </div>
            ) : (
              <div className="space-y-2">
                {filteredTemplates.map((template) => {
                  const typeInfo = typeLabels[template.type];
                  const TypeIcon = typeInfo.icon;

                  if (editingId === template.id) {
                    return (
                      <div
                        key={template.id}
                        className="p-4 bg-bambu-dark rounded-lg border border-bambu-green"
                      >
                        <div className="grid grid-cols-2 gap-3 mb-3">
                          <input
                            type="text"
                            value={editName}
                            onChange={(e) => setEditName(e.target.value)}
                            placeholder="Template name"
                            className="px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
                            autoFocus
                          />
                          <input
                            type="text"
                            value={editDesc}
                            onChange={(e) => setEditDesc(e.target.value)}
                            placeholder="Description"
                            className="px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
                          />
                        </div>
                        <div className="mb-3">
                          <label className="text-xs text-bambu-gray mb-1 block">Settings (JSON)</label>
                          <textarea
                            value={editSettings}
                            onChange={(e) => {
                              setEditSettings(e.target.value);
                              setEditSettingsError(null);
                            }}
                            rows={6}
                            className={`w-full px-3 py-2 bg-bambu-dark-secondary border rounded text-white text-sm font-mono focus:outline-none ${
                              editSettingsError ? 'border-red-500' : 'border-bambu-dark-tertiary focus:border-bambu-green'
                            }`}
                          />
                          {editSettingsError && (
                            <p className="text-xs text-red-400 mt-1">{editSettingsError}</p>
                          )}
                        </div>
                        <div className="flex gap-2">
                          <Button size="sm" onClick={handleSaveEdit} disabled={!editName.trim()}>
                            <Save className="w-4 h-4" />
                            Save
                          </Button>
                          <Button size="sm" variant="secondary" onClick={handleCancelEdit}>
                            Cancel
                          </Button>
                        </div>
                      </div>
                    );
                  }

                  return (
                    <div
                      key={template.id}
                      className="flex items-center gap-3 p-3 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary hover:border-bambu-gray-dark transition-colors"
                    >
                      <TypeIcon className={`w-5 h-5 ${typeInfo.color} flex-shrink-0`} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-white">{template.name}</p>
                        <p className="text-xs text-bambu-gray truncate">{template.description}</p>
                      </div>
                      <span className="text-xs text-bambu-gray-dark px-2 py-1 bg-bambu-dark-secondary rounded">
                        {Object.keys(template.settings).length} fields
                      </span>
                      <button
                        onClick={() => toggleShowInModal(template.id)}
                        className={`p-1 transition-colors ${
                          template.showInModal
                            ? 'text-bambu-green hover:text-bambu-green/70'
                            : 'text-bambu-gray hover:text-white'
                        }`}
                        title={template.showInModal ? 'Shown in modals' : 'Hidden in modals'}
                      >
                        {template.showInModal ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
                      </button>
                      <button
                        onClick={() => onApply(template)}
                        className="px-3 py-1 text-xs bg-bambu-green/20 text-bambu-green rounded hover:bg-bambu-green/30 transition-colors"
                      >
                        Apply
                      </button>
                      <button
                        onClick={() => handleEdit(template)}
                        className="p-1 text-bambu-gray hover:text-white"
                        title="Edit"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => setDeleteConfirmId(template.id)}
                        className="p-1 text-bambu-gray hover:text-red-400"
                        title="Delete"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ============================================================================
// DIFF MODAL - Compare two presets or preset vs base
// ============================================================================

type DiffEntry = {
  key: string;
  left: unknown;
  right: unknown;
  status: 'added' | 'removed' | 'changed' | 'same';
};

function DiffModal({
  onClose,
  leftPreset,
  rightPreset,
  leftLabel,
  rightLabel,
}: {
  onClose: () => void;
  leftPreset: Record<string, unknown>;
  rightPreset: Record<string, unknown>;
  leftLabel: string;
  rightLabel: string;
}) {
  const [filterMode, setFilterMode] = useState<'changes' | 'all'>('changes');
  const [searchQuery, setSearchQuery] = useState('');

  // Calculate diff
  const diffEntries = useMemo(() => {
    const allKeys = new Set([...Object.keys(leftPreset), ...Object.keys(rightPreset)]);
    const entries: DiffEntry[] = [];

    for (const key of allKeys) {
      // Skip internal fields
      if (key === 'inherits' || key === 'version') continue;

      const leftVal = leftPreset[key];
      const rightVal = rightPreset[key];
      const leftExists = key in leftPreset;
      const rightExists = key in rightPreset;

      let status: DiffEntry['status'];
      if (!leftExists && rightExists) {
        status = 'added';
      } else if (leftExists && !rightExists) {
        status = 'removed';
      } else if (JSON.stringify(leftVal) !== JSON.stringify(rightVal)) {
        status = 'changed';
      } else {
        status = 'same';
      }

      entries.push({ key, left: leftVal, right: rightVal, status });
    }

    return entries.sort((a, b) => {
      // Sort by status (changed first, then added, removed, same)
      const statusOrder = { changed: 0, added: 1, removed: 2, same: 3 };
      if (statusOrder[a.status] !== statusOrder[b.status]) {
        return statusOrder[a.status] - statusOrder[b.status];
      }
      return a.key.localeCompare(b.key);
    });
  }, [leftPreset, rightPreset]);

  // Filter entries
  const filteredEntries = useMemo(() => {
    let entries = [...diffEntries];
    if (filterMode === 'changes') {
      entries = entries.filter(e => e.status !== 'same');
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      entries = entries.filter(e =>
        e.key.toLowerCase().includes(q) ||
        String(e.left).toLowerCase().includes(q) ||
        String(e.right).toLowerCase().includes(q)
      );
    }
    return entries;
  }, [diffEntries, filterMode, searchQuery]);

  // Stats
  const stats = useMemo(() => {
    return {
      added: diffEntries.filter(e => e.status === 'added').length,
      removed: diffEntries.filter(e => e.status === 'removed').length,
      changed: diffEntries.filter(e => e.status === 'changed').length,
      same: diffEntries.filter(e => e.status === 'same').length,
    };
  }, [diffEntries]);

  const formatValue = (val: unknown): string => {
    if (val === undefined) return '—';
    if (val === null) return 'null';
    if (Array.isArray(val)) {
      // Show arrays more cleanly
      if (val.length === 0) return '[]';
      if (val.length === 1) return String(val[0]);
      return val.join(', ');
    }
    if (typeof val === 'object') return JSON.stringify(val);
    // Handle strings - truncate long ones and clean up escaped chars
    const str = String(val);
    // Check if it looks like G-code or multi-line content
    if (str.includes('\\n') || str.length > 100) {
      // Count lines and show summary
      const lines = str.split('\\n').length;
      if (lines > 1) {
        return `[${lines} lines of G-code/script]`;
      }
      if (str.length > 100) {
        return str.substring(0, 100) + '…';
      }
    }
    return str;
  };

  // Handle Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-4xl max-h-[85vh] flex flex-col overflow-hidden">
        <CardContent className="p-0 flex flex-col min-h-0 flex-1">
          {/* Header */}
          <div className="flex-shrink-0 flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <GitCompare className="w-5 h-5 text-blue-400" />
              Compare Presets
            </h2>
            <button onClick={onClose} className="text-bambu-gray hover:text-white">
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Preset labels */}
          <div className="flex-shrink-0 grid grid-cols-2 gap-4 p-4 border-b border-bambu-dark-tertiary bg-bambu-dark">
            <div className="text-center">
              <span className="text-sm text-bambu-gray">Left:</span>
              <p className="text-white font-medium truncate">{leftLabel}</p>
            </div>
            <div className="text-center">
              <span className="text-sm text-bambu-gray">Right:</span>
              <p className="text-white font-medium truncate">{rightLabel}</p>
            </div>
          </div>

          {/* Stats and filters */}
          <div className="flex-shrink-0 flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <div className="flex items-center gap-4 text-sm">
              <span className="flex items-center gap-1 text-green-400">
                <PlusIcon className="w-3.5 h-3.5" />
                {stats.added} added
              </span>
              <span className="flex items-center gap-1 text-red-400">
                <MinusIcon className="w-3.5 h-3.5" />
                {stats.removed} removed
              </span>
              <span className="flex items-center gap-1 text-amber-400">
                <ArrowRight className="w-3.5 h-3.5" />
                {stats.changed} changed
              </span>
              <span className="flex items-center gap-1 text-bambu-gray">
                <Equal className="w-3.5 h-3.5" />
                {stats.same} same
              </span>
            </div>
            <div className="flex items-center gap-3">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search fields..."
                  className="pl-8 pr-3 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none w-48"
                />
              </div>
              {stats.same > 0 && (
                <div className="flex rounded overflow-hidden border border-bambu-dark-tertiary">
                  <button
                    onClick={() => setFilterMode('changes')}
                    className={`px-3 py-1.5 text-sm transition-colors ${
                      filterMode === 'changes'
                        ? 'bg-bambu-green text-white'
                        : 'bg-bambu-dark text-bambu-gray hover:text-white'
                    }`}
                  >
                    Changes
                  </button>
                  <button
                    onClick={() => setFilterMode('all')}
                    className={`px-3 py-1.5 text-sm transition-colors ${
                      filterMode === 'all'
                        ? 'bg-bambu-green text-white'
                        : 'bg-bambu-dark text-bambu-gray hover:text-white'
                    }`}
                  >
                    All
                  </button>
                </div>
              )}
            </div>
          </div>

          {/* Diff table */}
          <div className="flex-1 min-h-0 overflow-y-auto">
            {filteredEntries.length === 0 ? (
              <div className="text-center py-12 text-bambu-gray">
                <Equal className="w-12 h-12 mx-auto mb-4 opacity-30" />
                <p>{filterMode === 'changes' ? 'No differences found' : 'No fields match the search'}</p>
              </div>
            ) : (
              <table className="w-full">
                <thead className="sticky top-0 bg-bambu-dark-secondary">
                  <tr className="text-sm text-bambu-gray border-b border-bambu-dark-tertiary">
                    <th className="text-left p-3 w-1/3">Field</th>
                    <th className="text-left p-3 w-1/3">{leftLabel}</th>
                    <th className="text-left p-3 w-1/3">{rightLabel}</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredEntries.map((entry) => {
                    const bgClass = {
                      added: 'bg-green-500/10',
                      removed: 'bg-red-500/10',
                      changed: 'bg-amber-500/10',
                      same: '',
                    }[entry.status];

                    const statusIcon = {
                      added: <PlusIcon className="w-3.5 h-3.5 text-green-400" />,
                      removed: <MinusIcon className="w-3.5 h-3.5 text-red-400" />,
                      changed: <ArrowRight className="w-3.5 h-3.5 text-amber-400" />,
                      same: <Equal className="w-3.5 h-3.5 text-bambu-gray-dark" />,
                    }[entry.status];

                    return (
                      <tr key={entry.key} className={`border-b border-bambu-dark-tertiary ${bgClass}`}>
                        <td className="p-3">
                          <div className="flex items-center gap-2">
                            {statusIcon}
                            <span className="text-sm text-white font-mono">{entry.key}</span>
                          </div>
                        </td>
                        <td className="p-3">
                          <span className={`text-sm font-mono break-all ${
                            entry.status === 'removed' ? 'text-red-300' :
                            entry.status === 'changed' ? 'text-white' : 'text-bambu-gray'
                          }`}>
                            {formatValue(entry.left)}
                          </span>
                        </td>
                        <td className="p-3">
                          <span className={`text-sm font-mono break-all ${
                            entry.status === 'added' ? 'text-green-300' :
                            entry.status === 'changed' ? 'text-white' : 'text-bambu-gray'
                          }`}>
                            {formatValue(entry.right)}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ============================================================================
// CREATE PRESET MODAL
// ============================================================================

function CreatePresetModal({
  onClose,
  initialData,
  allPresets,
}: {
  onClose: () => void;
  initialData?: { type: string; name: string; base_id: string; setting: Record<string, unknown>; setting_id?: string };
  allPresets: SlicerSettingsResponse;
}) {
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  // Editing mode if initialData has setting_id
  const isEditMode = !!initialData?.setting_id;

  const [activeTab, setActiveTab] = useState<EditorTab>('common');
  const [presetType, setPresetType] = useState<'filament' | 'print' | 'printer'>(
    (initialData?.type as 'filament' | 'print' | 'printer') || 'filament'
  );
  const [name, setName] = useState(
    initialData?.name
      ? (isEditMode ? initialData.name : `${initialData.name} (Copy)`)
      : ''
  );
  const [baseId, setBaseId] = useState(initialData?.base_id || '');
  const [baseName, setBaseName] = useState('');
  const [settingsObj, setSettingsObj] = useState<Record<string, unknown>>(
    initialData?.setting || { inherits: '' }
  );
  const [jsonText, setJsonText] = useState(JSON.stringify(initialData?.setting || { inherits: '' }, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [fieldSearch, setFieldSearch] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [customFieldKey, setCustomFieldKey] = useState('');
  const [showCustomFieldInput, setShowCustomFieldInput] = useState(false);
  const [customTemplates, setCustomTemplates] = useState<CustomTemplate[]>(loadCustomTemplates);
  const [showSaveTemplate, setShowSaveTemplate] = useState(false);
  const [newTemplateName, setNewTemplateName] = useState('');
  const [newTemplateDesc, setNewTemplateDesc] = useState('');
  const [newTemplateShowInModal, setNewTemplateShowInModal] = useState(true);
  const [appliedTemplateName, setAppliedTemplateName] = useState<string | null>(null);
  const [showDiffModal, setShowDiffModal] = useState(false);

  // Fetch ALL preset details for the current type to discover all available fields
  const presetsOfType = useMemo(() => {
    const typeMap: Record<string, SlicerSetting[]> = {
      filament: allPresets.filament,
      print: allPresets.process,
      printer: allPresets.printer,
    };
    return typeMap[presetType] || [];
  }, [allPresets, presetType]);

  // Only fetch details for USER presets (not Bambu's built-in ones which return 500)
  const userPresetsOfType = useMemo(() => {
    return presetsOfType.filter(p => isUserPreset(p.setting_id));
  }, [presetsOfType]);

  // Fetch field definitions from API (cached, only loaded once per type)
  const { data: fieldDefinitions } = useQuery({
    queryKey: ['cloudFields', presetType],
    queryFn: () => api.getCloudFields(presetType === 'print' ? 'process' : presetType),
    staleTime: 1000 * 60 * 60, // Cache for 1 hour
  });

  // Fetch details for user presets of this type (for field discovery)
  const { data: allPresetDetails } = useQuery({
    queryKey: ['allPresetDetails', presetType, userPresetsOfType.map(p => p.setting_id).join(',')],
    queryFn: async () => {
      // Fetch all preset details in parallel (limit concurrency to avoid overwhelming API)
      const results: Record<string, SlicerSettingDetail> = {};
      const batchSize = 5;
      for (let i = 0; i < userPresetsOfType.length; i += batchSize) {
        const batch = userPresetsOfType.slice(i, i + batchSize);
        const batchResults = await Promise.all(
          batch.map(async (preset) => {
            try {
              const detail = await api.getCloudSettingDetail(preset.setting_id);
              return { id: preset.setting_id, detail };
            } catch {
              return null;
            }
          })
        );
        batchResults.forEach(r => {
          if (r) results[r.id] = r.detail;
        });
      }
      return results;
    },
    enabled: userPresetsOfType.length > 0,
    staleTime: 1000 * 60 * 10, // Cache for 10 minutes
  });

  // Fetch base preset details (works for both user presets and built-in presets with new API version)
  const { data: basePresetDetail, isLoading: isLoadingBasePreset } = useQuery<SlicerSettingDetail>({
    queryKey: ['cloudSettingDetail', baseId],
    queryFn: () => api.getCloudSettingDetail(baseId),
    enabled: !!baseId,
  });

  // Sync JSON text with settings object
  useEffect(() => {
    if (activeTab !== 'json') {
      setJsonText(JSON.stringify(settingsObj, null, 2));
    }
  }, [settingsObj, activeTab]);

  // Get presets filtered by selected type - only built-in presets allowed as base
  // (Bambu Cloud only allows custom presets to inherit from built-in presets)
  const availableBasePresets = useMemo(() => {
    const typeMap: Record<string, SlicerSetting[]> = {
      filament: allPresets.filament,
      print: allPresets.process,
      printer: allPresets.printer,
    };
    return (typeMap[presetType] || [])
      .filter(p => !isUserPreset(p.setting_id)) // Only built-in presets
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [allPresets, presetType]);

  // Set inherits field when base preset changes (don't pre-fill all values - they show as placeholders)
  // In edit mode, don't reset settingsObj - keep the saved values
  useEffect(() => {
    if (!baseId) return;

    const preset = availableBasePresets.find(p => p.setting_id === baseId);
    if (preset) {
      setBaseName(preset.name);
      // Don't reset settings in edit mode - keep saved values
      if (!isEditMode) {
        setSettingsObj({ inherits: preset.name });
        setJsonText(JSON.stringify({ inherits: preset.name }, null, 2));
      }
    }
  }, [baseId, availableBasePresets, isEditMode]);

  // Build dynamic fields list: merge API definitions with discovered fields from user presets
  const dynamicFields = useMemo(() => {
    // Use API field definitions if available
    const knownFields: FieldDefinition[] = fieldDefinitions?.fields || [];
    const knownKeySet = new Set(knownFields.map(f => f.key));

    // Collect all unique field keys from ALL user presets of this type
    const discoveredKeys = new Set<string>();
    const excludeKeys = new Set(['inherits', 'updated_time', 'compatible_printers', 'compatible_prints']);

    // From all preset details
    if (allPresetDetails) {
      Object.values(allPresetDetails).forEach(detail => {
        if (detail?.setting) {
          Object.keys(detail.setting).forEach(key => {
            if (!knownKeySet.has(key) && !excludeKeys.has(key)) {
              discoveredKeys.add(key);
            }
          });
        }
      });
    }

    // From current settings (in case user added custom fields)
    Object.keys(settingsObj).forEach(key => {
      if (!knownKeySet.has(key) && !excludeKeys.has(key)) {
        discoveredKeys.add(key);
      }
    });

    // Create field definitions for discovered keys (generic text inputs)
    const discoveredFields: FieldDefinition[] = Array.from(discoveredKeys)
      .sort()
      .map(key => ({
        key,
        label: key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
        type: 'text' as const,
        category: 'discovered',
        description: 'Discovered from presets',
      }));

    return [...knownFields, ...discoveredFields];
  }, [fieldDefinitions, allPresetDetails, settingsObj]);

  // Filter fields for search
  const filteredFields = dynamicFields.filter(f =>
    f.label.toLowerCase().includes(fieldSearch.toLowerCase()) ||
    f.key.toLowerCase().includes(fieldSearch.toLowerCase())
  );

  // Add a custom field
  const addCustomField = () => {
    if (customFieldKey.trim()) {
      const key = customFieldKey.trim().toLowerCase().replace(/\s+/g, '_');
      updateField(key, '');
      setCustomFieldKey('');
      setShowCustomFieldInput(false);
      showToast(`Field "${key}" added`);
    }
  };

  // Update a single field
  const updateField = (key: string, value: unknown) => {
    setSettingsObj(prev => {
      const newObj = { ...prev };
      if (value === '' || value === undefined) {
        delete newObj[key];
      } else {
        newObj[key] = value;
      }
      return newObj;
    });
  };

  // Apply a template
  const applyTemplate = (template: { name: string; settings: Record<string, unknown> }) => {
    setSettingsObj(prev => ({ ...prev, ...template.settings }));
    setAppliedTemplateName(template.name);
    showToast('Template applied');
  };

  // Save current settings as a template
  const saveAsTemplate = () => {
    if (!newTemplateName.trim()) return;
    const overrides = { ...settingsObj };
    delete overrides.inherits;
    if (Object.keys(overrides).length === 0) {
      showToast('No overrides to save', 'error');
      return;
    }
    const newTemplate: CustomTemplate = {
      id: Date.now().toString(),
      name: newTemplateName.trim(),
      description: newTemplateDesc.trim() || 'Custom template',
      type: presetType,
      settings: overrides,
      showInModal: newTemplateShowInModal,
    };
    const updated = [...customTemplates, newTemplate];
    setCustomTemplates(updated);
    saveCustomTemplates(updated);
    setShowSaveTemplate(false);
    setNewTemplateName('');
    setNewTemplateDesc('');
    setNewTemplateShowInModal(true);
    showToast('Template saved');
  };

  // Get templates for current type (only those marked to show in modals)
  const templatesForType = useMemo(() => {
    return customTemplates.filter(t => t.type === presetType && t.showInModal);
  }, [presetType, customTemplates]);

  // Handle JSON edit
  const handleJsonChange = (text: string) => {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text);
      setSettingsObj(parsed);
      setJsonError(null);
    } catch (e) {
      setJsonError((e as Error).message);
    }
  };

  // Handle file drop
  const handleFileDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith('.json')) {
      const reader = new FileReader();
      reader.onload = (event) => {
        try {
          const content = event.target?.result as string;
          const parsed = JSON.parse(content);
          // Handle both full preset format and settings-only format
          const settings = parsed.setting || parsed;
          setSettingsObj(prev => ({ ...prev, ...settings }));
          setJsonText(JSON.stringify({ ...settingsObj, ...settings }, null, 2));
          showToast('File imported successfully');
        } catch {
          showToast('Invalid JSON file', 'error');
        }
      };
      reader.readAsText(file);
    }
  };

  const createMutation = useMutation({
    mutationFn: () => {
      const finalSettings = { ...settingsObj };
      const settingsIdKey = presetType === 'filament' ? 'filament_settings_id'
        : presetType === 'print' ? 'print_settings_id' : 'printer_settings_id';
      finalSettings[settingsIdKey] = `"${name}"`;

      const data: SlicerSettingCreate = { type: presetType, name, base_id: baseId, setting: finalSettings };
      return api.createCloudSetting(data);
    },
    onSuccess: async () => {
      showToast('Preset created successfully');
      // Force immediate refetch of the settings list
      await queryClient.refetchQueries({ queryKey: ['cloudSettings'] });
      onClose();
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!initialData?.setting_id) throw new Error('No setting ID for update');
      return api.updateCloudSetting(initialData.setting_id, { name, setting: settingsObj });
    },
    onSuccess: async () => {
      showToast('Preset updated successfully');
      // Clear all detail caches to ensure fresh data
      queryClient.removeQueries({ queryKey: ['cloudSettingDetail'] });
      // Force immediate refetch of the settings list
      await queryClient.refetchQueries({ queryKey: ['cloudSettings'] });
      onClose();
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const saveMutation = isEditMode ? updateMutation : createMutation;

  // Check if base preset inherits from another preset (for user presets that only store overrides)
  const inheritedPresetName = basePresetDetail?.setting?.inherits as string | undefined;
  const inheritedPreset = inheritedPresetName
    ? availableBasePresets.find(p => p.name === inheritedPresetName)
    : undefined;

  // Fetch the inherited preset's full values (if applicable)
  const { data: inheritedPresetDetail } = useQuery<SlicerSettingDetail>({
    queryKey: ['cloudSettingDetail', inheritedPreset?.setting_id],
    queryFn: () => api.getCloudSettingDetail(inheritedPreset!.setting_id),
    enabled: !!inheritedPreset?.setting_id,
  });

  // Get base preset values - merge inherited values with overrides
  const basePresetValues = useMemo(() => {
    // Start with inherited preset's values (full base)
    const inheritedValues = inheritedPresetDetail?.setting as Record<string, unknown> || {};

    // Get the selected preset's values (could be overrides only)
    const selectedValues = basePresetDetail?.setting as Record<string, unknown> || {};

    // Fallback to allPresetDetails if no dedicated query result
    const fallbackValues = baseId && allPresetDetails?.[baseId]?.setting
      ? allPresetDetails[baseId].setting as Record<string, unknown>
      : {};

    // Merge: inherited base values + selected preset overrides
    // Selected values take precedence
    return {
      ...inheritedValues,
      ...selectedValues,
      ...fallbackValues,
    };
  }, [baseId, basePresetDetail, inheritedPresetDetail, allPresetDetails]);

  // Format a value for display (handles arrays, objects, etc.)
  const formatValue = (val: unknown): string => {
    if (val === undefined || val === null) return '';
    if (Array.isArray(val)) {
      // For arrays, join with comma or take first value if all same
      const unique = [...new Set(val.map(v => String(v)))];
      return unique.length === 1 ? unique[0] : val.join(', ');
    }
    return String(val);
  };

  // Render a field input
  const renderFieldInput = (field: FieldDefinition) => {
    const value = settingsObj[field.key] as string | number | boolean | undefined;
    const baseValue = basePresetValues[field.key];
    const formattedBaseValue = formatValue(baseValue);
    // Always show base value as placeholder when available
    const placeholder = isLoadingBasePreset
      ? 'Loading...'
      : (formattedBaseValue || '');
    const baseClass = "w-full px-3 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none";

    if (field.type === 'boolean') {
      const isOn = value === '1' || (value === undefined && baseValue === '1');
      return (
        <button
          type="button"
          onClick={() => updateField(field.key, value === '1' ? '0' : '1')}
          className={`w-8 h-5 rounded-full transition-colors ${isOn ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'}`}
        >
          <div className={`w-4 h-4 rounded-full bg-white shadow transition-transform ${isOn ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
        </button>
      );
    }

    if (field.type === 'select') {
      return (
        <select
          value={(value as string) || ''}
          onChange={(e) => updateField(field.key, e.target.value)}
          className={baseClass}
        >
          <option value="">{placeholder}</option>
          {field.options?.map(opt => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      );
    }

    return (
      <div className="flex items-center gap-2">
        <input
          type={field.type === 'number' ? 'number' : 'text'}
          value={value !== undefined ? String(value) : ''}
          onChange={(e) => updateField(field.key, e.target.value)}
          step={field.step}
          min={field.min}
          max={field.max}
          placeholder={placeholder}
          className={baseClass}
        />
        {field.unit && <span className="text-xs text-bambu-gray whitespace-nowrap">{field.unit}</span>}
      </div>
    );
  };

  // Get base preset settings for diff comparison
  const basePresetSettings = useMemo(() => {
    if (!basePresetDetail?.setting) return {};
    return basePresetDetail.setting as Record<string, unknown>;
  }, [basePresetDetail]);

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleFileDrop}
    >
      {/* Diff Modal */}
      {showDiffModal && baseId && (
        <DiffModal
          onClose={() => setShowDiffModal(false)}
          leftPreset={basePresetSettings}
          rightPreset={settingsObj}
          leftLabel={`Base: ${baseName || baseId}`}
          rightLabel={`Current: ${name || 'New Preset'}`}
        />
      )}

      <Card className="w-full max-w-6xl max-h-[90vh] flex flex-col">
        <CardContent className="p-0 flex flex-col h-full">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <div>
              <h2 className="text-xl font-semibold text-white">
                {isEditMode ? 'Edit Preset' : (initialData ? 'Duplicate Preset' : 'Create New Preset')}
              </h2>
              <p className="text-sm text-bambu-gray mt-1">
                Customize settings for your new preset
              </p>
            </div>
            <div className="flex items-center gap-2">
              {baseId && (
                <button
                  onClick={() => setShowDiffModal(true)}
                  className="flex items-center gap-2 px-3 py-2 text-sm text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
                  title="Compare with base preset"
                >
                  <GitCompare className="w-4 h-4" />
                  Compare
                </button>
              )}
              <button onClick={onClose} className="p-2 text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary rounded-lg transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* Drag overlay */}
          {isDragging && (
            <div className="absolute inset-0 bg-bambu-green/10 border-2 border-dashed border-bambu-green rounded-lg flex items-center justify-center z-10">
              <div className="text-center">
                <Upload className="w-12 h-12 text-bambu-green mx-auto mb-2" />
                <p className="text-bambu-green font-medium">Drop JSON file to import</p>
              </div>
            </div>
          )}

          {/* Basic Info */}
          <div className="p-4 border-b border-bambu-dark-tertiary space-y-3">
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Type</label>
                <select
                  value={presetType}
                  onChange={(e) => { setPresetType(e.target.value as 'filament' | 'print' | 'printer'); setBaseId(''); }}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="filament">Filament</option>
                  <option value="print">Process</option>
                  <option value="printer">Printer</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Base Preset</label>
                <select
                  value={baseId}
                  onChange={(e) => setBaseId(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                >
                  <option value="">Select base preset...</option>
                  {availableBasePresets.map((preset) => (
                    <option key={preset.setting_id} value={preset.setting_id}>{preset.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Preset Name</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  placeholder="My Custom Preset"
                />
              </div>
            </div>
            {baseName && (
              <div className="text-xs text-bambu-gray">
                <p className="flex items-center gap-1">
                  <Check className="w-3 h-3 text-bambu-green" />
                  Inherits from: <span className="text-white">{baseName}</span>
                  {isLoadingBasePreset && (
                    <Loader2 className="w-3 h-3 animate-spin ml-1" />
                  )}
                </p>
              </div>
            )}
          </div>

          {/* Tabs */}
          <div className="flex border-b border-bambu-dark-tertiary">
            <button
              onClick={() => setActiveTab('common')}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 -mb-px ${
                activeTab === 'common' ? 'text-bambu-green border-bambu-green' : 'text-bambu-gray hover:text-white border-transparent'
              }`}
            >
              <Sliders className="w-4 h-4" />
              Common
            </button>
            <button
              onClick={() => setActiveTab('fields')}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 -mb-px ${
                activeTab === 'fields' ? 'text-bambu-green border-bambu-green' : 'text-bambu-gray hover:text-white border-transparent'
              }`}
            >
              <List className="w-4 h-4" />
              All Fields
            </button>
            <button
              onClick={() => setActiveTab('json')}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 -mb-px ${
                activeTab === 'json' ? 'text-bambu-green border-bambu-green' : 'text-bambu-gray hover:text-white border-transparent'
              }`}
            >
              <Code className="w-4 h-4" />
              JSON
              {jsonError && <AlertCircle className="w-3 h-3 text-red-400" />}
            </button>
            <div className="flex-1" />
            <button
              onClick={() => {
                const exportData = {
                  name,
                  type: presetType,
                  base_id: baseId,
                  setting: settingsObj,
                };
                const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${name || 'preset'}.json`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                showToast('Preset exported');
              }}
              className="flex items-center gap-2 px-4 py-3 text-sm text-bambu-gray hover:text-white transition-colors"
              title="Export current settings to JSON file"
            >
              <Download className="w-4 h-4" />
              Export
            </button>
            <button
              onClick={() => document.getElementById('file-import')?.click()}
              className="flex items-center gap-2 px-4 py-3 text-sm text-bambu-gray hover:text-white transition-colors"
              title="Import settings from JSON file"
            >
              <Upload className="w-4 h-4" />
              Import
            </button>
            <input
              id="file-import"
              type="file"
              accept=".json"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) {
                  const reader = new FileReader();
                  reader.onload = (event) => {
                    try {
                      const parsed = JSON.parse(event.target?.result as string);
                      const settings = parsed.setting || parsed;
                      setSettingsObj(prev => ({ ...prev, ...settings }));
                      showToast('File imported');
                    } catch {
                      showToast('Invalid JSON', 'error');
                    }
                  };
                  reader.readAsText(file);
                }
              }}
            />
          </div>

          {/* Tab Content */}
          <div className="flex-1 overflow-y-auto p-4">
            {activeTab === 'common' && (
              <div className="space-y-6">
                {/* Templates */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-sm font-medium text-white flex items-center gap-2">
                      <Sparkles className="w-4 h-4 text-amber-400" />
                      Quick Templates
                    </h3>
                    {Object.keys(settingsObj).filter(k => k !== 'inherits').length > 0 && (
                      <button
                        onClick={() => setShowSaveTemplate(!showSaveTemplate)}
                        className="text-xs text-bambu-gray hover:text-white flex items-center gap-1 transition-colors"
                      >
                        <Save className="w-3 h-3" />
                        Save as template
                      </button>
                    )}
                  </div>

                  {showSaveTemplate && (
                    <div className="mb-3 p-3 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
                      <div className="grid grid-cols-2 gap-2 mb-2">
                        <input
                          type="text"
                          value={newTemplateName}
                          onChange={(e) => setNewTemplateName(e.target.value)}
                          placeholder="Template name"
                          className="px-3 py-1.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
                          autoFocus
                        />
                        <input
                          type="text"
                          value={newTemplateDesc}
                          onChange={(e) => setNewTemplateDesc(e.target.value)}
                          placeholder="Description (optional)"
                          className="px-3 py-1.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
                        />
                      </div>
                      <div className="flex items-center justify-between">
                        <div className="flex gap-2">
                          <Button size="sm" onClick={saveAsTemplate} disabled={!newTemplateName.trim()}>
                            <Save className="w-3 h-3" />
                            Save
                          </Button>
                          <Button size="sm" variant="secondary" onClick={() => setShowSaveTemplate(false)}>
                            Cancel
                          </Button>
                        </div>
                        <button
                          onClick={() => setNewTemplateShowInModal(!newTemplateShowInModal)}
                          className={`flex items-center gap-1.5 text-xs transition-colors ${
                            newTemplateShowInModal ? 'text-bambu-green' : 'text-bambu-gray hover:text-white'
                          }`}
                        >
                          {newTemplateShowInModal ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
                          {newTemplateShowInModal ? 'Show in modals' : 'Hidden in modals'}
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Applied template indicator */}
                  {appliedTemplateName && (
                    <div className="mb-3 px-3 py-2 bg-bambu-green/10 border border-bambu-green/30 rounded-lg flex items-center gap-2">
                      <Check className="w-4 h-4 text-bambu-green" />
                      <span className="text-sm text-bambu-green">Template applied: <span className="font-medium">{appliedTemplateName}</span></span>
                      <button
                        onClick={() => setAppliedTemplateName(null)}
                        className="ml-auto text-bambu-green/70 hover:text-bambu-green"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  )}

                  <div className="grid grid-cols-3 gap-2">
                    {templatesForType.map((template) => (
                      <button
                        key={template.id}
                        onClick={() => applyTemplate(template)}
                        className="p-3 text-left bg-bambu-dark border border-bambu-dark-tertiary rounded-lg hover:border-bambu-gray-dark transition-colors"
                      >
                        <p className="text-sm font-medium text-white">{template.name}</p>
                        <p className="text-xs text-bambu-gray mt-1">{template.description}</p>
                      </button>
                    ))}
                    {templatesForType.length === 0 && (
                      <p className="col-span-3 text-center text-bambu-gray text-sm py-4">
                        No templates selected. Use the Templates button to enable templates for quick access.
                      </p>
                    )}
                  </div>

                  {/* Note about template management */}
                  <p className="text-xs text-bambu-gray-dark mt-2 text-center">
                    Manage templates via the Templates button on the main page
                  </p>
                </div>

                {/* Common Fields */}
                <div>
                  <h3 className="text-sm font-medium text-white mb-3">Common Settings</h3>
                  <div className="grid grid-cols-2 gap-x-6 gap-y-3">
                    {dynamicFields.slice(0, 10).map(field => (
                      <div key={field.key} className="flex items-center justify-between gap-4">
                        <label className="text-sm text-bambu-gray flex-shrink-0">{field.label}</label>
                        <div className="w-48">{renderFieldInput(field)}</div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Current overrides */}
                {Object.keys(settingsObj).length > 1 && (
                  <div>
                    <h3 className="text-sm font-medium text-white mb-3">Current Overrides</h3>
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(settingsObj)
                        .filter(([k]) => k !== 'inherits')
                        .map(([key, value]) => (
                          <span key={key} className="inline-flex items-center gap-1 px-2 py-1 bg-bambu-green/10 text-bambu-green text-xs rounded">
                            {key}: {String(value).slice(0, 20)}
                            <button onClick={() => updateField(key, undefined)} className="hover:text-white">
                              <X className="w-3 h-3" />
                            </button>
                          </span>
                        ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'fields' && (
              <div className="grid grid-cols-2 gap-6" style={{ height: '400px' }}>
                {/* Left: Available Fields */}
                <div className="flex flex-col h-full overflow-hidden">
                  <div className="flex items-center justify-between mb-3 flex-shrink-0">
                    <h3 className="text-sm font-medium text-white">Available Fields</h3>
                    <span className="text-xs text-bambu-gray">
                      {allPresetDetails
                        ? `${dynamicFields.length} fields`
                        : 'Loading...'}
                    </span>
                  </div>

                  <div className="relative mb-3 flex-shrink-0">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
                    <input
                      type="text"
                      value={fieldSearch}
                      onChange={(e) => setFieldSearch(e.target.value)}
                      placeholder="Search fields..."
                      className="w-full pl-10 pr-4 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder-bambu-gray-dark focus:border-bambu-green focus:outline-none"
                    />
                  </div>

                  <div className="flex-1 overflow-y-auto space-y-1 pr-2 min-h-0">
                    {filteredFields
                      .filter(f => !(f.key in settingsObj))
                      .map(field => {
                        const baseVal = basePresetValues[field.key];
                        const formattedVal = formatValue(baseVal);
                        return (
                          <div
                            key={field.key}
                            onClick={() => {
                              // Add field directly (don't use updateField which deletes on empty)
                              setSettingsObj(prev => ({ ...prev, [field.key]: formattedVal || '' }));
                            }}
                            className="flex items-center justify-between gap-2 p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors cursor-pointer group"
                          >
                            <div className="min-w-0 flex-1">
                              <p className="text-sm text-white truncate">{field.label}</p>
                              <p className="text-xs text-bambu-gray-dark truncate">{field.key}</p>
                            </div>
                            <div className="flex items-center gap-2 flex-shrink-0">
                              {formattedVal && (
                                <span className="text-xs text-bambu-gray bg-bambu-dark px-2 py-0.5 rounded max-w-32 truncate" title={formattedVal}>
                                  {formattedVal.slice(0, 20)}{formattedVal.length > 20 ? '...' : ''}
                                </span>
                              )}
                              <div className="w-6 h-6 flex items-center justify-center rounded bg-bambu-dark-tertiary group-hover:bg-bambu-green/20 transition-colors">
                                <Plus className="w-4 h-4 text-bambu-gray group-hover:text-bambu-green transition-colors" />
                              </div>
                            </div>
                          </div>
                        );
                      })}

                    {filteredFields.filter(f => !(f.key in settingsObj)).length === 0 && (
                      <p className="text-center text-bambu-gray py-4 text-sm">
                        {fieldSearch ? 'No matching fields' : 'All fields added'}
                      </p>
                    )}
                  </div>

                  {/* Custom field input */}
                  <div className="pt-3 mt-3 border-t border-bambu-dark-tertiary flex-shrink-0">
                    {showCustomFieldInput ? (
                      <div className="flex gap-2">
                        <input
                          type="text"
                          value={customFieldKey}
                          onChange={(e) => setCustomFieldKey(e.target.value)}
                          onKeyDown={(e) => e.key === 'Enter' && addCustomField()}
                          placeholder="custom_field_name"
                          className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white font-mono text-sm placeholder-bambu-gray-dark focus:border-bambu-green focus:outline-none"
                          autoFocus
                        />
                        <Button size="sm" onClick={addCustomField} disabled={!customFieldKey.trim()}>
                          <Plus className="w-4 h-4" />
                        </Button>
                        <Button size="sm" variant="secondary" onClick={() => { setShowCustomFieldInput(false); setCustomFieldKey(''); }}>
                          <X className="w-4 h-4" />
                        </Button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setShowCustomFieldInput(true)}
                        className="w-full flex items-center justify-center gap-2 p-2 text-sm text-bambu-gray hover:text-white border border-dashed border-bambu-dark-tertiary hover:border-bambu-gray-dark rounded-lg transition-colors"
                      >
                        <Plus className="w-4 h-4" />
                        Add custom field
                      </button>
                    )}
                  </div>
                </div>

                {/* Right: Added Fields */}
                <div className="flex flex-col h-full overflow-hidden">
                  <div className="flex items-center justify-between mb-3 flex-shrink-0">
                    <h3 className="text-sm font-medium text-white">Your Overrides</h3>
                    <span className="text-xs text-bambu-gray">
                      {Object.keys(settingsObj).filter(k => k !== 'inherits').length} fields
                    </span>
                  </div>

                  <div className="flex-1 overflow-y-auto space-y-2 pr-2 min-h-0">
                    {Object.entries(settingsObj)
                      .filter(([key]) => key !== 'inherits')
                      .map(([key, value]) => {
                        const fieldDef = dynamicFields.find(f => f.key === key);
                        return (
                          <div key={key} className="p-3 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
                            <div className="flex items-center justify-between mb-2">
                              <div>
                                <p className="text-sm font-medium text-white">{fieldDef?.label || key}</p>
                                <p className="text-xs text-bambu-gray-dark">{key}</p>
                              </div>
                              <button
                                onClick={() => updateField(key, undefined)}
                                className="p-1 text-bambu-gray hover:text-red-400 transition-colors"
                              >
                                <X className="w-4 h-4" />
                              </button>
                            </div>
                            {fieldDef ? (
                              renderFieldInput(fieldDef)
                            ) : (
                              <input
                                type="text"
                                value={String(value)}
                                onChange={(e) => updateField(key, e.target.value)}
                                className="w-full px-3 py-1.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
                              />
                            )}
                          </div>
                        );
                      })}

                    {Object.keys(settingsObj).filter(k => k !== 'inherits').length === 0 && (
                      <div className="text-center py-8 text-bambu-gray">
                        <Sliders className="w-8 h-8 mx-auto mb-2 opacity-50" />
                        <p className="text-sm">No overrides yet</p>
                        <p className="text-xs mt-1">Click fields on the left to add them</p>
                      </div>
                    )}
                  </div>

                  {/* Save as template button */}
                  {Object.keys(settingsObj).filter(k => k !== 'inherits').length > 0 && (
                    <div className="pt-3 mt-3 border-t border-bambu-dark-tertiary flex-shrink-0">
                      <button
                        onClick={() => { setShowSaveTemplate(true); setActiveTab('common'); }}
                        className="w-full flex items-center justify-center gap-2 p-2 text-sm text-bambu-gray hover:text-white border border-dashed border-bambu-dark-tertiary hover:border-bambu-gray-dark rounded-lg transition-colors"
                      >
                        <Save className="w-4 h-4" />
                        Save as template
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}

            {activeTab === 'json' && (
              <div className="space-y-2">
                {jsonError && (
                  <div className="flex items-center gap-2 text-red-400 text-sm">
                    <AlertCircle className="w-4 h-4" />
                    {jsonError}
                  </div>
                )}
                <textarea
                  value={jsonText}
                  onChange={(e) => handleJsonChange(e.target.value)}
                  className={`w-full h-80 px-3 py-2 bg-bambu-dark border rounded-lg text-white text-xs font-mono focus:outline-none resize-none ${
                    jsonError ? 'border-red-500 focus:border-red-500' : 'border-bambu-dark-tertiary focus:border-bambu-green'
                  }`}
                  spellCheck={false}
                />
                <p className="text-xs text-bambu-gray">
                  Tip: Drag & drop a .json file anywhere on this modal to import settings
                </p>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="p-4 border-t border-bambu-dark-tertiary flex gap-2">
            <Button variant="secondary" onClick={onClose} className="flex-1">Cancel</Button>
            <Button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || !name.trim() || (!isEditMode && !baseId) || !!jsonError}
              className="flex-1"
            >
              {saveMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : (isEditMode ? <Save className="w-4 h-4" /> : <Plus className="w-4 h-4" />)}
              {isEditMode ? 'Save' : (initialData ? 'Duplicate' : 'Create')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ============================================================================
// CLOUD PROFILES VIEW
// ============================================================================

function CloudProfilesView({
  settings,
  lastSyncTime,
  onRefresh,
  isRefreshing,
  printers,
}: {
  settings: SlicerSettingsResponse;
  lastSyncTime?: Date;
  onRefresh: () => void;
  isRefreshing: boolean;
  printers: Printer[];
}) {
  const [searchQuery, setSearchQuery] = useState('');
  const [filterType, setFilterType] = useState<PresetType>('all');
  const [filterOwner, setFilterOwner] = useState<'all' | 'custom' | 'builtin'>('all');
  const [filterPrinter, setFilterPrinter] = useState('all');
  const [filterNozzle, setFilterNozzle] = useState('all');
  const [filterFilament, setFilterFilament] = useState('all');
  const [filterLayerHeight, setFilterLayerHeight] = useState('all');
  const [selectedSetting, setSelectedSetting] = useState<SlicerSetting | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showTemplatesModal, setShowTemplatesModal] = useState(false);
  const [duplicateData, setDuplicateData] = useState<{ type: string; name: string; base_id: string; setting: Record<string, unknown> } | null>(null);
  const [editData, setEditData] = useState<{ type: string; name: string; base_id: string; setting: Record<string, unknown>; setting_id: string } | null>(null);
  const [templateData, setTemplateData] = useState<{ type: string; setting: Record<string, unknown> } | null>(null);
  // Compare mode state
  const [compareMode, setCompareMode] = useState(false);
  const [compareSelection, setCompareSelection] = useState<[SlicerSetting | null, SlicerSetting | null]>([null, null]);
  const [showCompareModal, setShowCompareModal] = useState(false);
  const [comparePresets, setComparePresets] = useState<[Record<string, unknown>, Record<string, unknown>] | null>(null);

  const queryClient = useQueryClient();

  // Combine all presets with metadata
  const allPresetsWithMeta = useMemo(() => {
    const combined = [
      ...settings.filament.map(s => ({ ...s, type: 'filament' as const })),
      ...settings.printer.map(s => ({ ...s, type: 'printer' as const })),
      ...settings.process.map(s => ({ ...s, type: 'process' as const })),
    ];
    return combined.map(s => ({ ...s, meta: extractMetadata(s.name) }));
  }, [settings]);

  // Extract unique filter values (use configured printers from API)
  const filterOptions = useMemo(() => {
    const nozzles = new Set<string>();
    const filaments = new Set<string>();
    const layerHeights = new Set<string>();

    allPresetsWithMeta.forEach(p => {
      if (p.meta.nozzle) nozzles.add(p.meta.nozzle);
      if (p.meta.filamentType) filaments.add(p.meta.filamentType);
      if (p.meta.layerHeight) layerHeights.add(p.meta.layerHeight);
    });

    return {
      printers: printers.map(p => ({ id: p.id.toString(), name: p.name })),
      nozzles: Array.from(nozzles).sort((a, b) => parseFloat(a) - parseFloat(b)),
      filaments: Array.from(filaments).sort(),
      layerHeights: Array.from(layerHeights).sort((a, b) => parseFloat(a) - parseFloat(b)),
    };
  }, [allPresetsWithMeta, printers]);

  // Get selected printer's model for filtering
  const selectedPrinterModel = useMemo(() => {
    if (filterPrinter === 'all') return null;
    const printer = printers.find(p => p.id.toString() === filterPrinter);
    return printer?.model || null;
  }, [filterPrinter, printers]);

  // Apply filters
  const filteredPresets = useMemo(() => {
    return allPresetsWithMeta
      .filter(s => filterType === 'all' || s.type === filterType)
      .filter(s => {
        if (filterOwner === 'all') return true;
        const isCustom = isUserPreset(s.setting_id);
        return filterOwner === 'custom' ? isCustom : !isCustom;
      })
      .filter(s => {
        if (filterPrinter === 'all' || !selectedPrinterModel) return true;
        // Match preset's printer model to configured printer's model
        const presetPrinter = s.meta.printer?.toLowerCase() || '';
        const configuredModel = selectedPrinterModel.toLowerCase();
        return presetPrinter.includes(configuredModel) || configuredModel.includes(presetPrinter);
      })
      .filter(s => filterNozzle === 'all' || s.meta.nozzle === filterNozzle)
      .filter(s => filterFilament === 'all' || s.meta.filamentType === filterFilament)
      .filter(s => filterLayerHeight === 'all' || s.meta.layerHeight === filterLayerHeight)
      .filter(s => searchQuery === '' || s.name.toLowerCase().includes(searchQuery.toLowerCase()))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [allPresetsWithMeta, filterType, filterOwner, filterPrinter, selectedPrinterModel, filterNozzle, filterFilament, filterLayerHeight, searchQuery]);

  // Handle click on preset in compare mode
  const handlePresetClick = (preset: SlicerSetting) => {
    if (compareMode) {
      // In compare mode, toggle selection
      const isFirst = compareSelection[0]?.setting_id === preset.setting_id;
      const isSecond = compareSelection[1]?.setting_id === preset.setting_id;

      if (isFirst) {
        // Deselect first
        setCompareSelection([compareSelection[1], null]);
      } else if (isSecond) {
        // Deselect second
        setCompareSelection([compareSelection[0], null]);
      } else if (!compareSelection[0]) {
        // Select as first
        setCompareSelection([preset, null]);
      } else if (!compareSelection[1]) {
        // Check type match - only allow same type
        if (compareSelection[0].type !== preset.type) {
          return; // Don't allow selecting different types
        }
        // Select as second
        setCompareSelection([compareSelection[0], preset]);
      } else {
        // Both selected, replace second (must match first's type)
        if (compareSelection[0].type !== preset.type) {
          return;
        }
        setCompareSelection([compareSelection[0], preset]);
      }
    } else {
      // Normal mode, open detail
      setSelectedSetting(preset);
    }
  };

  // Check if preset is selected for comparison
  const getCompareIndex = (preset: SlicerSetting): number | undefined => {
    if (compareSelection[0]?.setting_id === preset.setting_id) return 0;
    if (compareSelection[1]?.setting_id === preset.setting_id) return 1;
    return undefined;
  };

  const handleDuplicate = async (setting: SlicerSetting) => {
    try {
      // Always fetch fresh data (bypass cache)
      const detail = await api.getCloudSettingDetail(setting.setting_id);

      const apiType = setting.type === 'process' ? 'print' : setting.type;
      setDuplicateData({
        type: apiType,
        name: setting.name,
        base_id: detail.base_id || 'GFSA00',
        setting: detail.setting || {},
      });
      setSelectedSetting(null);
    } catch (error) {
      console.error('Failed to fetch preset details for duplication:', error);
    }
  };

  const handleEdit = async (setting: SlicerSetting) => {
    try {
      // Clear any cached data first
      queryClient.removeQueries({ queryKey: ['cloudSettingDetail', setting.setting_id] });

      // Always fetch fresh data (bypass cache)
      const detail = await api.getCloudSettingDetail(setting.setting_id);

      const apiType = setting.type === 'process' ? 'print' : setting.type;
      setEditData({
        type: apiType,
        name: setting.name,
        base_id: detail.base_id || 'GFSA00',
        setting: detail.setting || {},
        setting_id: setting.setting_id,
      });
      setSelectedSetting(null);
    } catch (error) {
      console.error('Failed to fetch preset details for editing:', error);
    }
  };

  const clearFilters = () => {
    setFilterType('all');
    setFilterOwner('all');
    setFilterPrinter('all');
    setFilterNozzle('all');
    setFilterFilament('all');
    setFilterLayerHeight('all');
    setSearchQuery('');
  };

  const hasActiveFilters = filterType !== 'all' || filterOwner !== 'all' || filterPrinter !== 'all' || filterNozzle !== 'all' ||
    filterFilament !== 'all' || filterLayerHeight !== 'all' || searchQuery !== '';

  const totalCount = settings.filament.length + settings.printer.length + settings.process.length;

  return (
    <>
      {/* Search and Filters */}
      <div className="space-y-4 mb-6">
        {/* Search row */}
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search presets..."
              className="w-full pl-10 pr-4 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray-dark focus:border-bambu-green focus:outline-none"
            />
          </div>

          <div className="flex gap-2">
            <Button
              variant={compareMode ? 'primary' : 'secondary'}
              onClick={() => {
                if (compareMode) {
                  setCompareMode(false);
                  setCompareSelection([null, null]);
                } else {
                  setCompareMode(true);
                }
              }}
            >
              <GitCompare className="w-4 h-4" />
              {compareMode ? 'Cancel' : 'Compare'}
            </Button>
            <Button variant="secondary" onClick={() => setShowTemplatesModal(true)}>
              <Sparkles className="w-4 h-4" />
              Templates
            </Button>
            <Button variant="secondary" onClick={onRefresh} disabled={isRefreshing}>
              <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
            <Button onClick={() => setShowCreateModal(true)}>
              <Plus className="w-4 h-4" />
              New Preset
            </Button>
          </div>
        </div>

        {/* Filter row */}
        <div className="flex flex-wrap items-center gap-2">
          <Filter className="w-4 h-4 text-bambu-gray" />

          <FilterDropdown
            label="Type"
            value={filterType}
            options={[
              { value: 'all', label: 'All', count: totalCount },
              { value: 'filament', label: 'Filament', count: settings.filament.length },
              { value: 'printer', label: 'Printer', count: settings.printer.length },
              { value: 'process', label: 'Process', count: settings.process.length },
            ]}
            onChange={(v) => setFilterType(v as PresetType)}
          />

          <FilterDropdown
            label="Owner"
            value={filterOwner}
            options={[
              { value: 'all', label: 'All' },
              { value: 'custom', label: 'My Presets' },
              { value: 'builtin', label: 'Built-in' },
            ]}
            onChange={(v) => setFilterOwner(v as 'all' | 'custom' | 'builtin')}
          />

          {filterOptions.printers.length > 0 && (
            <FilterDropdown
              label="Printer"
              value={filterPrinter}
              options={[
                { value: 'all', label: 'All' },
                ...filterOptions.printers.map(p => ({ value: p.id, label: p.name })),
              ]}
              onChange={setFilterPrinter}
            />
          )}

          {filterOptions.nozzles.length > 0 && (
            <FilterDropdown
              label="Nozzle"
              value={filterNozzle}
              options={[
                { value: 'all', label: 'All' },
                ...filterOptions.nozzles.map(n => ({ value: n, label: n })),
              ]}
              onChange={setFilterNozzle}
            />
          )}

          {filterOptions.filaments.length > 0 && (filterType === 'all' || filterType === 'filament') && (
            <FilterDropdown
              label="Filament"
              value={filterFilament}
              options={[
                { value: 'all', label: 'All' },
                ...filterOptions.filaments.map(f => ({ value: f, label: f })),
              ]}
              onChange={setFilterFilament}
            />
          )}

          {filterOptions.layerHeights.length > 0 && (filterType === 'all' || filterType === 'process') && (
            <FilterDropdown
              label="Layer"
              value={filterLayerHeight}
              options={[
                { value: 'all', label: 'All' },
                ...filterOptions.layerHeights.map(l => ({ value: l, label: l })),
              ]}
              onChange={setFilterLayerHeight}
            />
          )}

          {hasActiveFilters && (
            <button
              onClick={clearFilters}
              className="px-3 py-2 text-sm text-bambu-gray hover:text-white transition-colors"
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* Compare mode bar */}
      {compareMode && (
        <div className="mb-4 p-3 bg-blue-500/10 border border-blue-500/30 rounded-lg">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <GitCompare className="w-5 h-5 text-blue-400" />
              <span className="text-white font-medium">Compare Mode</span>
              <span className="text-bambu-gray">
                {compareSelection[0]
                  ? `Select another ${compareSelection[0].type} preset`
                  : 'Click two presets of the same type to compare'}
              </span>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <span className={`px-2 py-1 text-sm rounded truncate max-w-[200px] ${compareSelection[0] ? 'bg-blue-500/30 text-blue-700 dark:text-blue-300' : 'bg-bambu-dark text-bambu-gray'}`}>
                  {compareSelection[0] ? compareSelection[0].name : '1. Select first'}
                </span>
                <ArrowRight className="w-4 h-4 text-bambu-gray" />
                <span className={`px-2 py-1 text-sm rounded truncate max-w-[200px] ${compareSelection[1] ? 'bg-blue-500/30 text-blue-700 dark:text-blue-300' : 'bg-bambu-dark text-bambu-gray'}`}>
                  {compareSelection[1] ? compareSelection[1].name : '2. Select second'}
                </span>
              </div>
              {compareSelection[0] && compareSelection[1] && (
                <Button
                  size="sm"
                  onClick={async () => {
                    try {
                      const [left, right] = await Promise.all([
                        api.getCloudSettingDetail(compareSelection[0]!.setting_id),
                        api.getCloudSettingDetail(compareSelection[1]!.setting_id),
                      ]);
                      setComparePresets([
                        (left.setting || {}) as Record<string, unknown>,
                        (right.setting || {}) as Record<string, unknown>,
                      ]);
                      setShowCompareModal(true);
                    } catch {
                      // Handle error silently
                    }
                  }}
                >
                  <GitCompare className="w-4 h-4" />
                  Compare Now
                </Button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Status row: sync time, count, and legend */}
      <div className="flex flex-wrap items-center gap-4 mb-4 text-sm text-bambu-gray">
        {lastSyncTime && (
          <div className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            Last synced: {formatRelativeTime(lastSyncTime.toISOString())}
          </div>
        )}
        <span>Showing {filteredPresets.length} of {totalCount} presets</span>
        <div className="flex items-center gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-bambu-green" />
          <span>= My preset (editable)</span>
        </div>
      </div>

      {/* 3-Column Presets List */}
      {filteredPresets.length === 0 ? (
        <div className="text-center py-16">
          <Layers className="w-12 h-12 text-bambu-gray-dark mx-auto mb-4" />
          <p className="text-bambu-gray">No presets found</p>
          {hasActiveFilters && (
            <button onClick={clearFilters} className="mt-2 text-sm text-bambu-green hover:text-bambu-green-light">
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Filament Column */}
          <div>
            <div className="flex items-center gap-2 mb-3 px-1">
              <Droplet className="w-4 h-4 text-amber-400" />
              <h3 className="text-sm font-medium text-bambu-gray">Filament</h3>
              <span className="text-xs text-bambu-gray-dark">
                ({filteredPresets.filter(p => p.type === 'filament').length})
              </span>
            </div>
            <div className="space-y-1 max-h-[calc(100vh-320px)] overflow-y-auto pr-1">
              {filteredPresets
                .filter(p => p.type === 'filament')
                .map((preset) => (
                  <PresetListItem
                    key={preset.setting_id}
                    setting={preset}
                    onClick={() => handlePresetClick(preset)}
                    onDuplicate={() => handleDuplicate(preset)}
                    compareMode={compareMode}
                    isCompareSelected={getCompareIndex(preset) !== undefined}
                    compareIndex={getCompareIndex(preset)}
                    compareDisabled={compareMode && !!compareSelection[0] && compareSelection[0].type !== preset.type}
                  />
                ))}
              {filteredPresets.filter(p => p.type === 'filament').length === 0 && (
                <p className="text-xs text-bambu-gray-dark px-3 py-2">No filament presets</p>
              )}
            </div>
          </div>

          {/* Process Column */}
          <div>
            <div className="flex items-center gap-2 mb-3 px-1">
              <Settings2 className="w-4 h-4 text-blue-400" />
              <h3 className="text-sm font-medium text-bambu-gray">Process</h3>
              <span className="text-xs text-bambu-gray-dark">
                ({filteredPresets.filter(p => p.type === 'process').length})
              </span>
            </div>
            <div className="space-y-1 max-h-[calc(100vh-320px)] overflow-y-auto pr-1">
              {filteredPresets
                .filter(p => p.type === 'process')
                .map((preset) => (
                  <PresetListItem
                    key={preset.setting_id}
                    setting={preset}
                    onClick={() => handlePresetClick(preset)}
                    onDuplicate={() => handleDuplicate(preset)}
                    compareMode={compareMode}
                    isCompareSelected={getCompareIndex(preset) !== undefined}
                    compareIndex={getCompareIndex(preset)}
                    compareDisabled={compareMode && !!compareSelection[0] && compareSelection[0].type !== preset.type}
                  />
                ))}
              {filteredPresets.filter(p => p.type === 'process').length === 0 && (
                <p className="text-xs text-bambu-gray-dark px-3 py-2">No process presets</p>
              )}
            </div>
          </div>

          {/* Printer Column */}
          <div>
            <div className="flex items-center gap-2 mb-3 px-1">
              <PrinterIcon className="w-4 h-4 text-purple-400" />
              <h3 className="text-sm font-medium text-bambu-gray">Printer</h3>
              <span className="text-xs text-bambu-gray-dark">
                ({filteredPresets.filter(p => p.type === 'printer').length})
              </span>
            </div>
            <div className="space-y-1 max-h-[calc(100vh-320px)] overflow-y-auto pr-1">
              {filteredPresets
                .filter(p => p.type === 'printer')
                .map((preset) => (
                  <PresetListItem
                    key={preset.setting_id}
                    setting={preset}
                    onClick={() => handlePresetClick(preset)}
                    onDuplicate={() => handleDuplicate(preset)}
                    compareMode={compareMode}
                    isCompareSelected={getCompareIndex(preset) !== undefined}
                    compareIndex={getCompareIndex(preset)}
                    compareDisabled={compareMode && !!compareSelection[0] && compareSelection[0].type !== preset.type}
                  />
                ))}
              {filteredPresets.filter(p => p.type === 'printer').length === 0 && (
                <p className="text-xs text-bambu-gray-dark px-3 py-2">No printer presets</p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Modals */}
      {selectedSetting && (
        <PresetDetailModal
          setting={selectedSetting}
          onClose={() => setSelectedSetting(null)}
          onDeleted={() => setSelectedSetting(null)}
          onDuplicate={() => handleDuplicate(selectedSetting)}
          onEdit={() => handleEdit(selectedSetting)}
        />
      )}

      {(showCreateModal || duplicateData || editData || templateData) && (
        <CreatePresetModal
          onClose={() => { setShowCreateModal(false); setDuplicateData(null); setEditData(null); setTemplateData(null); }}
          initialData={editData || duplicateData || (templateData ? { type: templateData.type, name: '', base_id: '', setting: templateData.setting } : undefined)}
          allPresets={settings}
        />
      )}

      {showTemplatesModal && (
        <TemplatesModal
          onClose={() => setShowTemplatesModal(false)}
          onApply={(template) => {
            setTemplateData({ type: template.type, setting: template.settings });
            setShowTemplatesModal(false);
          }}
        />
      )}

      {showCompareModal && comparePresets && compareSelection[0] && compareSelection[1] && (
        <DiffModal
          onClose={() => {
            setShowCompareModal(false);
            setComparePresets(null);
          }}
          leftPreset={comparePresets[0]}
          rightPreset={comparePresets[1]}
          leftLabel={compareSelection[0].name}
          rightLabel={compareSelection[1].name}
        />
      )}
    </>
  );
}

// ============================================================================
// MAIN PAGE
// ============================================================================

export function ProfilesPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [activeTab, setActiveTab] = useState<ProfileTab>('cloud');
  const [lastSyncTime, setLastSyncTime] = useState<Date>();

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['cloudStatus'],
    queryFn: api.getCloudStatus,
  });

  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: settings, isLoading: settingsLoading, refetch: refetchSettings, dataUpdatedAt } = useQuery({
    queryKey: ['cloudSettings'],
    queryFn: () => api.getCloudSettings(),
    enabled: !!status?.is_authenticated,
    retry: false,
    staleTime: 1000 * 60 * 5,
  });

  useEffect(() => {
    if (dataUpdatedAt) {
      setLastSyncTime(new Date(dataUpdatedAt));
    }
  }, [dataUpdatedAt]);

  const logoutMutation = useMutation({
    mutationFn: api.cloudLogout,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cloudStatus'] });
      queryClient.removeQueries({ queryKey: ['cloudSettings'] });
      showToast('Logged out');
    },
  });

  const handleLoginSuccess = () => {
    queryClient.invalidateQueries({ queryKey: ['cloudStatus'] });
  };

  if (statusLoading) {
    return (
      <div className="p-4 md:p-8 flex items-center justify-center min-h-[400px]">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  return (
    <div className="p-6 lg:p-8">
      {/* Page Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white">Profiles</h1>
        <p className="text-bambu-gray">Manage your slicer presets and pressure advance calibrations</p>
      </div>

      {/* Tab Navigation */}
      <div className="flex border-b border-bambu-dark-tertiary mb-6">
        <button
          onClick={() => setActiveTab('cloud')}
          className={`flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 -mb-px ${
            activeTab === 'cloud'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-white border-transparent'
          }`}
        >
          <Cloud className="w-4 h-4" />
          Cloud Profiles
        </button>
        <button
          onClick={() => setActiveTab('kprofiles')}
          className={`flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 -mb-px ${
            activeTab === 'kprofiles'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-white border-transparent'
          }`}
        >
          <Gauge className="w-4 h-4" />
          K-Profiles
        </button>
      </div>

      {/* Cloud Profiles Tab */}
      {activeTab === 'cloud' && (
        <>
          {/* Connection Status Bar */}
          {status?.is_authenticated && (
            <div className="flex items-center justify-between p-3 mb-6 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
              <div className="flex items-center gap-3">
                <div className="w-2 h-2 rounded-full bg-bambu-green animate-pulse" />
                <span className="text-sm text-bambu-gray">
                  Connected as <span className="text-white">{status.email}</span>
                </span>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => logoutMutation.mutate()}
                disabled={logoutMutation.isPending}
              >
                <LogOut className="w-4 h-4" />
                Logout
              </Button>
            </div>
          )}

          {!status?.is_authenticated ? (
            <LoginForm onSuccess={handleLoginSuccess} />
          ) : settingsLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
            </div>
          ) : settings ? (
            <CloudProfilesView
              settings={settings}
              lastSyncTime={lastSyncTime}
              onRefresh={() => refetchSettings()}
              isRefreshing={settingsLoading}
              printers={printers}
            />
          ) : (
            <div className="text-center py-16">
              <p className="text-bambu-gray mb-4">Failed to load profiles</p>
              <Button onClick={() => refetchSettings()}>Retry</Button>
            </div>
          )}
        </>
      )}

      {/* K-Profiles Tab */}
      {activeTab === 'kprofiles' && <KProfilesView />}

      {/* Scroll to Top Button */}
      <ScrollToTop />
    </div>
  );
}
