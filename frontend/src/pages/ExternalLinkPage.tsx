import { useCallback, useEffect, useRef, type RefObject } from 'react';
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
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const sendTheme = useThemeMessenger(iframeRef);

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
      ref={iframeRef}
      src={link.url}
      onLoad={sendTheme}
      className="h-full w-full border-0"
      style={{ colorScheme: mode }}
      title={link.name}
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
    />
  );
}

/**
 * Tell the framed page which theme Bambuddy is showing, so an app built to
 * sit in the sidebar (Bambuddy Orders) can match it. Sent when the page
 * loads, when the theme changes, and when the page asks for it. Only to the
 * link's own origin, and it carries nothing but the theme names.
 */
function useThemeMessenger(iframeRef: RefObject<HTMLIFrameElement | null>) {
  const { resolvedMode, darkStyle, darkBackground, darkAccent, lightStyle, lightBackground, lightAccent } = useTheme();
  const dark = resolvedMode === 'dark';
  const style = dark ? darkStyle : lightStyle;
  const background = dark ? darkBackground : lightBackground;
  const accent = dark ? darkAccent : lightAccent;

  const sendTheme = useCallback(() => {
    const frame = iframeRef.current;
    if (!frame?.contentWindow) return;
    let origin: string;
    try {
      origin = new URL(frame.src).origin;
    } catch {
      return;
    }
    frame.contentWindow.postMessage({ type: 'bambuddy:theme', mode: resolvedMode, style, background, accent }, origin);
  }, [iframeRef, resolvedMode, style, background, accent]);

  useEffect(() => sendTheme(), [sendTheme]);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source === iframeRef.current?.contentWindow && e.data?.type === 'bambuddy:theme-request') sendTheme();
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [iframeRef, sendTheme]);

  return sendTheme;
}
