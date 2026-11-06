import { useEffect, useState } from 'react';
import { api } from '../api/client';

export function useOverlayLogo(enabled: boolean, token: string | null = null, revision = 0) {
  const [logo, setLogo] = useState<{ url: string; token: string | null; revision: number } | null>(null);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let url: string | undefined;
    void api.getOverlayLogo(token, controller.signal).then((blob) => {
      if (controller.signal.aborted) return;
      if (blob) {
        url = URL.createObjectURL(blob);
        setLogo({ url, token, revision });
      } else {
        setLogo(null);
      }
    }).catch(() => {
      if (!controller.signal.aborted) setLogo(null);
    });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [enabled, token, revision]);
  return enabled && logo?.token === token && logo.revision === revision ? logo.url : null;
}
