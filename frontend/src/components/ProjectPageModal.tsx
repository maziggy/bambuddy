import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  X,
  User,
  Calendar,
  FileText,
  Image,
  Edit3,
  Save,
  ExternalLink,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';
import { RichTextEditor } from './RichTextEditor';

interface ProjectPageModalProps {
  archiveId: number;
  archiveName?: string;
  onClose: () => void;
}

export function ProjectPageModal({ archiveId, archiveName, onClose }: ProjectPageModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [isEditing, setIsEditing] = useState(false);
  const [selectedImageIndex, setSelectedImageIndex] = useState<number | null>(null);
  const [editData, setEditData] = useState<{
    title?: string;
    description?: string;
    designer?: string;
    license?: string;
    profile_title?: string;
    profile_description?: string;
  }>({});

  const { data: projectPage, isLoading, error } = useQuery({
    queryKey: ['archive-project-page', archiveId],
    queryFn: () => api.getArchiveProjectPage(archiveId),
  });

  const updateMutation = useMutation({
    mutationFn: (data: typeof editData) => api.updateArchiveProjectPage(archiveId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archive-project-page', archiveId] });
      setIsEditing(false);
      setEditData({});
    },
  });

  // Handle escape key to close modal
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (selectedImageIndex !== null) {
          setSelectedImageIndex(null);
        } else if (isEditing) {
          handleCancelEdit();
        } else {
          onClose();
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [selectedImageIndex, isEditing, onClose]);

  // Combine all images for gallery
  const allImages = [
    ...(projectPage?.model_pictures || []),
    ...(projectPage?.profile_pictures || []),
  ];

  const handleStartEdit = () => {
    setEditData({
      title: projectPage?.title || '',
      description: projectPage?.description || '',
      designer: projectPage?.designer || '',
      license: projectPage?.license || '',
      profile_title: projectPage?.profile_title || '',
      profile_description: projectPage?.profile_description || '',
    });
    setIsEditing(true);
  };

  const handleSave = () => {
    updateMutation.mutate(editData);
  };

  const handleCancelEdit = () => {
    setIsEditing(false);
    setEditData({});
  };

  // Sanitize HTML content (basic XSS prevention)
  const sanitizeHtml = (html: string) => {
    // Allow basic formatting tags only
    const allowed = ['p', 'br', 'b', 'strong', 'i', 'em', 'u', 'a', 'ul', 'ol', 'li', 'figure', 'img'];
    const doc = new DOMParser().parseFromString(html, 'text/html');

    const clean = (node: Node): string => {
      if (node.nodeType === Node.TEXT_NODE) {
        return node.textContent || '';
      }
      if (node.nodeType === Node.ELEMENT_NODE) {
        const el = node as Element;
        const tag = el.tagName.toLowerCase();

        if (!allowed.includes(tag)) {
          // Return children content without the tag
          return Array.from(el.childNodes).map(clean).join('');
        }

        // Build allowed attributes
        let attrs = '';
        if (tag === 'a' && el.getAttribute('href')) {
          const href = el.getAttribute('href');
          if (href?.toLowerCase().startsWith('http')) {
            attrs = ` href="${href}" target="_blank" rel="noopener noreferrer"`;
          }
        }
        if (tag === 'img') {
          const src = el.getAttribute('src');
          // Only render img if it has a valid http(s) URL, otherwise skip entirely
          if (!src?.toLowerCase().startsWith('http')) {
            return ''; // Skip images without valid URLs
          }
          attrs = ` src="${src}" style="max-width: 100%; height: auto;"`;
        }

        const children = Array.from(el.childNodes).map(clean).join('');

        if (['br', 'img'].includes(tag)) {
          return `<${tag}${attrs} />`;
        }
        return `<${tag}${attrs}>${children}</${tag}>`;
      }
      return '';
    };

    return Array.from(doc.body.childNodes).map(clean).join('');
  };

  const hasContent = projectPage && (
    projectPage.title ||
    projectPage.description ||
    projectPage.designer ||
    projectPage.profile_title ||
    allImages.length > 0
  );

  // Handle backdrop click to close modal
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={handleBackdropClick}
    >
      <div className="bg-bambu-dark-secondary rounded-xl max-w-4xl w-full max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-3">
            <FileText className="w-5 h-5 text-bambu-green" />
            <h2 className="text-lg font-semibold text-white">
              {t('projectPage.title')}
              {archiveName && <span className="text-bambu-gray ml-2">- {archiveName}</span>}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            {!isEditing && hasContent && (
              <Button variant="ghost" size="sm" onClick={handleStartEdit}>
                <Edit3 className="w-4 h-4 mr-1" />
                {t('common.edit')}
              </Button>
            )}
            {isEditing && (
              <>
                <Button variant="ghost" size="sm" onClick={handleCancelEdit}>
                  {t('common.cancel')}
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleSave}
                  disabled={updateMutation.isPending}
                >
                  <Save className="w-4 h-4 mr-1" />
                  {t('common.save')}
                </Button>
              </>
            )}
            <button
              onClick={onClose}
              className="p-2 hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
            >
              <X className="w-5 h-5 text-bambu-gray" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {isLoading && (
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-2 border-bambu-green border-t-transparent" />
            </div>
          )}

          {error && (
            <div className="text-red-400 text-center py-12">
              {t('projectPage.loadFailed')}
            </div>
          )}

          {projectPage && !hasContent && (
            <div className="text-bambu-gray text-center py-12">
              <FileText className="w-12 h-12 mx-auto mb-4 opacity-50" />
              <p>{t('projectPage.noData')}</p>
              <p className="text-sm mt-2">
                {t('projectPage.noDataHint')}
              </p>
            </div>
          )}

          {projectPage && hasContent && (
            <div className="space-y-6">
              {/* Title & Designer */}
              <div className="space-y-4">
                {isEditing ? (
                  <input
                    type="text"
                    value={editData.title || ''}
                    onChange={(e) => setEditData({ ...editData, title: e.target.value })}
                    placeholder={t('projectPage.titleField')}
                    className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded-lg px-4 py-2 text-white text-xl font-semibold"
                  />
                ) : (
                  projectPage.title && (
                    <h3 className="text-xl font-semibold text-white">{projectPage.title}</h3>
                  )
                )}

                <div className="flex flex-wrap gap-4 text-sm">
                  {isEditing ? (
                    <div className="flex items-center gap-2">
                      <User className="w-4 h-4 text-bambu-gray" />
                      <input
                        type="text"
                        value={editData.designer || ''}
                        onChange={(e) => setEditData({ ...editData, designer: e.target.value })}
                        placeholder={t('projectPage.designer')}
                        className="bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1 text-white"
                      />
                    </div>
                  ) : (
                    projectPage.designer && (
                      <div className="flex items-center gap-2 text-bambu-gray">
                        <User className="w-4 h-4" />
                        <span>{projectPage.designer}</span>
                        {projectPage.designer_user_id && (
                          <a
                            href={`https://makerworld.com/en/@${projectPage.designer_user_id}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-bambu-green hover:underline"
                          >
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        )}
                      </div>
                    )
                  )}

                  {projectPage.creation_date && (
                    <div className="flex items-center gap-2 text-bambu-gray">
                      <Calendar className="w-4 h-4" />
                      <span>{projectPage.creation_date}</span>
                    </div>
                  )}

                  {isEditing ? (
                    <div className="flex items-center gap-2">
                      <FileText className="w-4 h-4 text-bambu-gray" />
                      <input
                        type="text"
                        value={editData.license || ''}
                        onChange={(e) => setEditData({ ...editData, license: e.target.value })}
                        placeholder={t('projectPage.license')}
                        className="bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1 text-white"
                      />
                    </div>
                  ) : (
                    projectPage.license && (
                      <div className="flex items-center gap-2 text-bambu-gray">
                        <FileText className="w-4 h-4" />
                        <span>{projectPage.license}</span>
                      </div>
                    )
                  )}

                  {projectPage.origin && (
                    <span className="px-2 py-0.5 bg-bambu-dark rounded text-bambu-gray">
                      {projectPage.origin}
                    </span>
                  )}
                </div>
              </div>

              {/* Description */}
              {(projectPage.description || isEditing) && (
                <div className="space-y-2">
                  <h4 className="text-sm font-medium text-bambu-gray uppercase tracking-wide">
                    {t('projectPage.description')}
                  </h4>
                  {isEditing ? (
                    <RichTextEditor
                      content={editData.description || ''}
                      onChange={(html) => setEditData({ ...editData, description: html })}
                      placeholder={t('projectPage.descriptionPlaceholder')}
                    />
                  ) : (
                    <div
                      className="prose prose-invert prose-sm max-w-none text-bambu-gray-light"
                      dangerouslySetInnerHTML={{
                        __html: sanitizeHtml(projectPage.description || ''),
                      }}
                    />
                  )}
                </div>
              )}

              {/* Profile Info */}
              {(projectPage.profile_title || projectPage.profile_description || isEditing) && (
                <div className="space-y-2 p-4 bg-bambu-dark rounded-lg">
                  <h4 className="text-sm font-medium text-bambu-gray uppercase tracking-wide">
                    {t('projectPage.printProfile')}
                  </h4>
                  {isEditing ? (
                    <div className="space-y-2">
                      <input
                        type="text"
                        value={editData.profile_title || ''}
                        onChange={(e) => setEditData({ ...editData, profile_title: e.target.value })}
                        placeholder={t('projectPage.profileTitle')}
                        className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-3 py-2 text-white"
                      />
                      <RichTextEditor
                        content={editData.profile_description || ''}
                        onChange={(html) => setEditData({ ...editData, profile_description: html })}
                        placeholder={t('projectPage.profileDescriptionPlaceholder')}
                      />
                    </div>
                  ) : (
                    <>
                      {projectPage.profile_title && (
                        <p className="text-white font-medium">{projectPage.profile_title}</p>
                      )}
                      {projectPage.profile_description && (
                        <div
                          className="prose prose-invert prose-sm max-w-none text-bambu-gray-light"
                          dangerouslySetInnerHTML={{
                            __html: sanitizeHtml(projectPage.profile_description),
                          }}
                        />
                      )}
                      {projectPage.profile_user_name && (
                        <p className="text-sm text-bambu-gray">
                          {t('projectPage.byUser', { name: projectPage.profile_user_name })}
                        </p>
                      )}
                    </>
                  )}
                </div>
              )}

              {/* Image Gallery */}
              {allImages.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-sm font-medium text-bambu-gray uppercase tracking-wide flex items-center gap-2">
                    <Image className="w-4 h-4" />
                    {t('projectPage.images', { count: allImages.length })}
                  </h4>
                  <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 gap-2">
                    {allImages.map((img, index) => (
                      <button
                        key={img.path}
                        onClick={() => setSelectedImageIndex(index)}
                        className="aspect-square rounded-lg overflow-hidden border border-bambu-dark-tertiary hover:border-bambu-green transition-colors"
                      >
                        <img
                          src={img.url}
                          alt={img.name}
                          className="w-full h-full object-cover"
                        />
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* MakerWorld Link */}
              {projectPage.design_model_id && (
                <div className="pt-4 border-t border-bambu-dark-tertiary">
                  <a
                    href={`https://makerworld.com/en/models/${projectPage.design_model_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 text-bambu-green hover:underline"
                  >
                    <ExternalLink className="w-4 h-4" />
                    {t('projectPage.viewOnMakerWorld')}
                  </a>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Image Lightbox */}
      {selectedImageIndex !== null && allImages[selectedImageIndex] && (
        <div
          className="fixed inset-0 bg-black/90 flex items-center justify-center z-60"
          onClick={() => setSelectedImageIndex(null)}
        >
          <button
            onClick={(e) => {
              e.stopPropagation();
              setSelectedImageIndex(Math.max(0, selectedImageIndex - 1));
            }}
            disabled={selectedImageIndex === 0}
            className="absolute left-4 p-2 bg-bambu-dark-secondary rounded-full hover:bg-bambu-dark-tertiary disabled:opacity-30"
          >
            <ChevronLeft className="w-6 h-6 text-white" />
          </button>

          <img
            src={allImages[selectedImageIndex].url}
            alt={allImages[selectedImageIndex].name}
            className="max-w-[90vw] max-h-[90vh] object-contain"
            onClick={(e) => e.stopPropagation()}
          />

          <button
            onClick={(e) => {
              e.stopPropagation();
              setSelectedImageIndex(Math.min(allImages.length - 1, selectedImageIndex + 1));
            }}
            disabled={selectedImageIndex === allImages.length - 1}
            className="absolute right-4 p-2 bg-bambu-dark-secondary rounded-full hover:bg-bambu-dark-tertiary disabled:opacity-30"
          >
            <ChevronRight className="w-6 h-6 text-white" />
          </button>

          <button
            onClick={() => setSelectedImageIndex(null)}
            className="absolute top-4 right-4 p-2 bg-bambu-dark-secondary rounded-full hover:bg-bambu-dark-tertiary"
          >
            <X className="w-6 h-6 text-white" />
          </button>

          <div className="absolute bottom-4 text-white text-sm">
            {selectedImageIndex + 1} / {allImages.length}
          </div>
        </div>
      )}
    </div>
  );
}
