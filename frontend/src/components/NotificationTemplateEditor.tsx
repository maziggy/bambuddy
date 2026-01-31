import { useState, useEffect, useRef } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { X, Save, Loader2, RotateCcw, Plus, Eye } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import type { NotificationTemplate, NotificationTemplateUpdate } from '../api/client';
import { Button } from './Button';

interface NotificationTemplateEditorProps {
  template: NotificationTemplate;
  onClose: () => void;
}

export function NotificationTemplateEditor({ template, onClose }: NotificationTemplateEditorProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  const [titleTemplate, setTitleTemplate] = useState(template.title_template);
  const [bodyTemplate, setBodyTemplate] = useState(template.body_template);
  const [error, setError] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(true);

  // Fetch variables for this event type
  const { data: variablesData } = useQuery({
    queryKey: ['template-variables'],
    queryFn: api.getTemplateVariables,
  });

  // Get variables for this template's event type
  const eventVariables = variablesData?.find(v => v.event_type === template.event_type);

  // Live preview
  const { data: preview, isLoading: previewLoading } = useQuery({
    queryKey: ['template-preview', template.event_type, titleTemplate, bodyTemplate],
    queryFn: () => api.previewTemplate({
      event_type: template.event_type,
      title_template: titleTemplate,
      body_template: bodyTemplate,
    }),
    enabled: showPreview && titleTemplate.length > 0 && bodyTemplate.length > 0,
  });

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: (data: NotificationTemplateUpdate) => api.updateNotificationTemplate(template.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-templates'] });
      onClose();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  // Reset mutation
  const resetMutation = useMutation({
    mutationFn: () => api.resetNotificationTemplate(template.id),
    onSuccess: (resetTemplate) => {
      setTitleTemplate(resetTemplate.title_template);
      setBodyTemplate(resetTemplate.body_template);
      queryClient.invalidateQueries({ queryKey: ['notification-templates'] });
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!titleTemplate.trim()) {
      setError(t('settings.templateTitleRequired'));
      return;
    }
    if (!bodyTemplate.trim()) {
      setError(t('settings.templateBodyRequired'));
      return;
    }

    updateMutation.mutate({
      title_template: titleTemplate,
      body_template: bodyTemplate,
    });
  };

  const insertVariable = (variable: string) => {
    const textarea = bodyRef.current;
    if (!textarea) return;

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const text = bodyTemplate;
    const before = text.substring(0, start);
    const after = text.substring(end);
    const newValue = before + `{${variable}}` + after;

    setBodyTemplate(newValue);

    // Restore focus and cursor position
    setTimeout(() => {
      textarea.focus();
      const newCursor = start + variable.length + 2;
      textarea.setSelectionRange(newCursor, newCursor);
    }, 0);
  };

  const hasChanges = titleTemplate !== template.title_template || bodyTemplate !== template.body_template;

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary shrink-0">
          <h2 className="text-lg font-semibold text-white">
            {t('settings.editTemplate', { name: t(`notificationLog.events.${template.event_type}`, { defaultValue: template.name }) })}
          </h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-bambu-dark-tertiary rounded transition-colors"
          >
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        {/* Content */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-4 space-y-4">
          {error && (
            <div className="p-3 bg-red-500/20 border border-red-500/50 rounded text-red-400 text-sm">
              {error}
            </div>
          )}

          {/* Title */}
          <div>
            <label className="block text-sm font-medium text-bambu-gray mb-1">
              {t('settings.templateTitle')}
            </label>
            <input
              type="text"
              value={titleTemplate}
              onChange={(e) => setTitleTemplate(e.target.value)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white focus:outline-none focus:ring-1 focus:ring-bambu-green"
              placeholder={t('settings.notificationTitlePlaceholder')}
            />
          </div>

          {/* Body */}
          <div>
            <label className="block text-sm font-medium text-bambu-gray mb-1">
              {t('settings.templateBody')}
            </label>
            <textarea
              ref={bodyRef}
              value={bodyTemplate}
              onChange={(e) => setBodyTemplate(e.target.value)}
              rows={4}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white focus:outline-none focus:ring-1 focus:ring-bambu-green font-mono text-sm resize-none"
              placeholder={t('settings.notificationBodyPlaceholder')}
            />
          </div>

          {/* Available Variables */}
          {eventVariables && (
            <div>
              <label className="block text-sm font-medium text-bambu-gray mb-2">
                {t('settings.availableVariables')}
              </label>
              <div className="flex flex-wrap gap-2">
                {eventVariables.variables.map((variable) => (
                  <button
                    key={variable}
                    type="button"
                    onClick={() => insertVariable(variable)}
                    className="inline-flex items-center gap-1 px-2 py-1 bg-bambu-dark hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-xs text-bambu-gray hover:text-white transition-colors"
                  >
                    <Plus className="w-3 h-3" />
                    {variable}
                  </button>
                ))}
              </div>
              <p className="text-xs text-bambu-gray/60 mt-1">
                {t('settings.insertVariableHint')}
              </p>
            </div>
          )}

          {/* Preview */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm font-medium text-bambu-gray flex items-center gap-2">
                <Eye className="w-4 h-4" />
                {t('settings.livePreview')}
              </label>
              <button
                type="button"
                onClick={() => setShowPreview(!showPreview)}
                className="text-xs text-bambu-green hover:text-bambu-green-light"
              >
                {showPreview ? t('common.hide') : t('common.show')}
              </button>
            </div>
            {showPreview && (
              <div className="bg-bambu-dark border border-bambu-dark-tertiary rounded p-3 space-y-2">
                {previewLoading ? (
                  <div className="flex items-center gap-2 text-bambu-gray text-sm">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    {t('settings.loadingPreview')}
                  </div>
                ) : preview ? (
                  <>
                    <div>
                      <span className="text-xs text-bambu-gray">{t('settings.previewTitle')}</span>
                      <div className="text-white font-medium">{preview.title}</div>
                    </div>
                    <div>
                      <span className="text-xs text-bambu-gray">{t('settings.previewBody')}</span>
                      <div className="text-white whitespace-pre-wrap text-sm">{preview.body}</div>
                    </div>
                  </>
                ) : (
                  <div className="text-bambu-gray text-sm">
                    {t('settings.enterTemplateToPreview')}
                  </div>
                )}
              </div>
            )}
          </div>
        </form>

        {/* Footer */}
        <div className="flex items-center justify-between p-4 border-t border-bambu-dark-tertiary shrink-0">
          <Button
            type="button"
            variant="ghost"
            onClick={() => resetMutation.mutate()}
            disabled={resetMutation.isPending}
            className="text-orange-400 hover:text-orange-300"
          >
            {resetMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
            ) : (
              <RotateCcw className="w-4 h-4 mr-2" />
            )}
            {t('settings.resetToDefault')}
          </Button>

          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={updateMutation.isPending || !hasChanges}
            >
              {updateMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
              ) : (
                <Save className="w-4 h-4 mr-2" />
              )}
              {t('common.save')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
