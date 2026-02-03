import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Loader2, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { useTheme } from '../contexts/ThemeContext';

export function ExternalLinkPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const { mode } = useTheme();

  const { data: link, isLoading, error } = useQuery({
    queryKey: ['external-link', id],
    queryFn: () => api.getExternalLink(Number(id)),
    enabled: !!id,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  if (error || !link) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-bambu-gray">
        <AlertTriangle className="w-12 h-12" />
        <p>{t('common.linkNotFound')}</p>
      </div>
    );
  }

  return (
    <iframe
      src={link.url}
      className="h-full w-full border-0"
      style={{ colorScheme: mode }}
      title={link.name}
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
    />
  );
}
