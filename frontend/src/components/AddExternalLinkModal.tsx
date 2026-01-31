import { useState, useEffect, useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { X, Save, Loader2, Upload, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import type { ExternalLink, ExternalLinkCreate, ExternalLinkUpdate } from '../api/client';
import { Button } from './Button';
import { IconPicker, getIconByName } from './IconPicker';
import { useTheme } from '../contexts/ThemeContext';

interface AddExternalLinkModalProps {
  link?: ExternalLink | null;
  onClose: () => void;
}

export function AddExternalLinkModal({ link, onClose }: AddExternalLinkModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { mode } = useTheme();
  const isEditing = !!link;
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [name, setName] = useState(link?.name || '');
  const [url, setUrl] = useState(link?.url || '');
  const [icon, setIcon] = useState(link?.icon || 'link');
  const [useCustomIcon, setUseCustomIcon] = useState(!!link?.custom_icon);
  const [customIconPreview, setCustomIconPreview] = useState<string | null>(
    link?.custom_icon ? api.getExternalLinkIconUrl(link.id) : null
  );
  const [pendingIconFile, setPendingIconFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Create mutation
  const createMutation = useMutation({
    mutationFn: async (data: ExternalLinkCreate) => {
      const created = await api.createExternalLink(data);
      // If there's a pending icon file, upload it
      if (pendingIconFile) {
        return await api.uploadExternalLinkIcon(created.id, pendingIconFile);
      }
      return created;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['external-links'] });
      onClose();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: async (data: ExternalLinkUpdate) => {
      let updated = await api.updateExternalLink(link!.id, data);
      // Handle icon changes
      if (pendingIconFile) {
        // Upload new icon
        updated = await api.uploadExternalLinkIcon(link!.id, pendingIconFile);
      } else if (!useCustomIcon && link?.custom_icon) {
        // Remove custom icon if switching to preset
        updated = await api.deleteExternalLinkIcon(link!.id);
      }
      return updated;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['external-links'] });
      onClose();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      // Validate file type
      const validTypes = ['image/png', 'image/jpeg', 'image/gif', 'image/svg+xml', 'image/webp', 'image/x-icon'];
      if (!validTypes.includes(file.type)) {
        setError(t('settings.linkInvalidImageType'));
        return;
      }

      // Validate file size (max 1MB)
      if (file.size > 1024 * 1024) {
        setError(t('settings.linkImageTooLarge'));
        return;
      }

      setPendingIconFile(file);
      setUseCustomIcon(true);

      // Create preview
      const reader = new FileReader();
      reader.onload = (e) => {
        setCustomIconPreview(e.target?.result as string);
      };
      reader.readAsDataURL(file);
    }
  };

  const handleRemoveCustomIcon = () => {
    setPendingIconFile(null);
    setCustomIconPreview(null);
    setUseCustomIcon(false);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError(t('settings.linkNameRequired'));
      return;
    }

    if (!url.trim()) {
      setError(t('settings.linkUrlRequired'));
      return;
    }

    // Validate URL
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
      setError(t('settings.linkUrlInvalid'));
      return;
    }

    const data = {
      name: name.trim(),
      url: url.trim(),
      icon: useCustomIcon ? icon : icon, // Keep preset icon as fallback
    };

    if (isEditing) {
      updateMutation.mutate(data);
    } else {
      createMutation.mutate(data);
    }
  };

  const isPending = createMutation.isPending || updateMutation.isPending;
  const PresetIcon = getIconByName(icon);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-full bg-bambu-green/20 text-bambu-green">
              {useCustomIcon && customIconPreview ? (
                <img src={customIconPreview} alt="" className={`w-5 h-5 rounded ${mode === 'dark' ? 'invert opacity-[0.65]' : 'opacity-60'}`} />
              ) : (
                <PresetIcon className="w-5 h-5" />
              )}
            </div>
            <h2 className="text-lg font-semibold text-white">
              {isEditing ? t('settings.editLink') : t('settings.addExternalLink')}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && (
            <div className="p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-400">
              {error}
            </div>
          )}

          {/* Name */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('settings.linkNameLabel')} *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('settings.linkNamePlaceholder')}
              maxLength={50}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            />
          </div>

          {/* URL */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('settings.linkUrlLabel')} *</label>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com"
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            />
          </div>

          {/* Icon Section */}
          <div className="space-y-3">
            <label className="block text-sm text-bambu-gray">{t('settings.linkIconLabel')}</label>

            {/* Custom Icon Upload */}
            <div className="p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm text-white">{t('settings.linkCustomIcon')}</span>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/gif,image/svg+xml,image/webp,image/x-icon"
                  className="hidden"
                  onChange={handleFileSelect}
                />
                {useCustomIcon && customIconPreview ? (
                  <div className="flex items-center gap-2">
                    <img src={customIconPreview} alt="Custom icon" className={`w-8 h-8 rounded border border-bambu-dark-tertiary ${mode === 'dark' ? 'invert opacity-[0.65]' : 'opacity-60'}`} />
                    <button
                      type="button"
                      onClick={handleRemoveCustomIcon}
                      className="p-1 text-red-400 hover:text-red-300 transition-colors"
                      title={t('settings.removeCustomIcon')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <Upload className="w-4 h-4" />
                    {t('common.upload')}
                  </Button>
                )}
              </div>
              <p className="text-xs text-bambu-gray">
                {t('settings.linkCustomIconHint')}
              </p>
            </div>

            {/* Preset Icon Picker */}
            {!useCustomIcon && (
              <div>
                <span className="text-sm text-bambu-gray block mb-2">{t('settings.linkChoosePresetIcon')}</span>
                <IconPicker value={icon} onChange={setIcon} />
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <Button
              type="button"
              variant="secondary"
              onClick={onClose}
              className="flex-1"
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="submit"
              disabled={isPending}
              className="flex-1"
            >
              {isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {isEditing ? t('common.save') : t('common.add')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
