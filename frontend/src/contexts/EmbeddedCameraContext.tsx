import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useMemo, useState, type ComponentType, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { useAuth } from './AuthContext';
import { listenForCameraPopIn, registerCameraTab } from '../utils/camera';
import { recoverFromMissingChunk } from '../utils/lazyPage';
import type { EmbeddedCameraViewerProps } from '../components/EmbeddedCameraViewer';

/**
 * The floating camera overlays, open on every page of the app (#2737).
 *
 * They lived in the Printers page, so they vanished on any other page and
 * only came back on returning to it. Persisted under the same key as then,
 * so overlays left open before the update are still open after it.
 */
const STORAGE_KEY = 'openEmbeddedCameras';

// Loaded on first use: every page now carries the host, and most sessions
// never open an overlay (#3175 keeps the first load small).
// A file missing after an update reloads the page, as for a page (lazyPage).
// One that still fails leaves the overlay out: the host sits above the app's
// layout, so throwing here would replace every page with the error screen.
const EmbeddedCameraViewer = lazy(async (): Promise<{ default: ComponentType<EmbeddedCameraViewerProps> }> => {
  try {
    const m = await import('../components/EmbeddedCameraViewer');
    return { default: m.EmbeddedCameraViewer };
  } catch (error) {
    if (await recoverFromMissingChunk()) return new Promise<never>(() => {});
    console.error('Could not load the camera overlay:', error);
    return { default: () => null };
  }
});

export interface OpenCamera {
  id: number;
  name: string;
}

interface EmbeddedCameraContextType {
  openCameras: OpenCamera[];
  openEmbeddedCamera: (id: number, name: string) => void;
  closeEmbeddedCamera: (id: number) => void;
}

const EmbeddedCameraContext = createContext<EmbeddedCameraContextType | undefined>(undefined);

function readStored(): OpenCamera[] {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const cameras = JSON.parse(saved) as OpenCamera[];
      if (Array.isArray(cameras)) {
        return cameras
          .filter(c => c && Number.isInteger(c.id) && c.id > 0)
          .map(c => ({ id: c.id, name: String(c.name ?? '') }));
      }
    }
  } catch {
    // Unreadable storage or a corrupt entry: start with none open.
  }
  return [];
}

/**
 * Applies one tab's change to the stored list, read fresh each time: every
 * open tab writes it, and writing this tab's whole list would drop what the
 * others opened.
 */
function updateStored(change: (cameras: OpenCamera[]) => OpenCamera[]) {
  try {
    const next = change(readStored());
    if (next.length > 0) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Overlays stay open in this tab; only reopening after a reload is lost.
  }
}

export function EmbeddedCameraProvider({ children }: { children: ReactNode }) {
  const { hasPermission } = useAuth();
  // Which printers may be shown is only known from the printer list, which
  // takes printers:read; without it the Printers page, where a camera opened
  // from before, is out of reach too.
  const canView = hasPermission('camera:view') && hasPermission('printers:read');
  const [cameras, setCameras] = useState<Map<number, OpenCamera>>(
    () => new Map(readStored().map(c => [c.id, c])),
  );

  const openEmbeddedCamera = useCallback((id: number, name: string) => {
    setCameras(prev => (prev.has(id) ? prev : new Map(prev).set(id, { id, name })));
    updateStored(list => (list.some(c => c.id === id) ? list : [...list, { id, name }]));
  }, []);

  const closeEmbeddedCamera = useCallback((id: number) => {
    setCameras(prev => {
      if (!prev.has(id)) return prev;
      const next = new Map(prev);
      next.delete(id);
      return next;
    });
    updateStored(list => list.filter(c => c.id !== id));
  }, []);

  // The list is stored per browser, not per user: a printer that was deleted,
  // or one the user now signed in may not see, is dropped once the printers
  // they can see are known, rather than showing up as a broken overlay. Only
  // a list fetched since this mounted counts: the cache can still hold the
  // last user's, and a fresh one would not be fetched again by itself.
  const queryClient = useQueryClient();
  const [mountedAt] = useState(() => Date.now());
  const { data: printers, dataUpdatedAt, isSuccess, isError } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
    enabled: canView && cameras.size > 0,
    // Stale at once, so it is fetched on mount and whenever an overlay opens.
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
  const listFresh = isSuccess && dataUpdatedAt >= mountedAt;
  const listChecked = listFresh || isError;
  useEffect(() => {
    if (!listFresh || !printers) return;
    const visible = new Set(printers.map(p => p.id));
    // Also for an overlay added after the list came in.
    const dropped = Array.from(cameras.keys()).filter(id => !visible.has(id));
    dropped.forEach(closeEmbeddedCamera);
  }, [listFresh, printers, cameras, closeEmbeddedCamera]);

  useEffect(() => {
    if (!canView) return;
    // A camera window is told done only for a printer this tab's user can
    // see, checked against the list as it is now.
    const canShow = async (printerId: number) => {
      const list = await queryClient.fetchQuery({ queryKey: ['printers'], queryFn: api.getPrinters, staleTime: 0 });
      return list.some(p => p.id === printerId);
    };
    return listenForCameraPopIn(registerCameraTab(), openEmbeddedCamera, canShow);
  }, [canView, openEmbeddedCamera, queryClient]);

  const openCameras = useMemo(() => Array.from(cameras.values()), [cameras]);
  const value = useMemo(
    () => ({ openCameras, openEmbeddedCamera, closeEmbeddedCamera }),
    [openCameras, openEmbeddedCamera, closeEmbeddedCamera],
  );

  return (
    <EmbeddedCameraContext.Provider value={value}>
      {children}
      {/* Several can be open at once; each offsets itself by its index. */}
      {canView && listChecked && openCameras.length > 0 && (
        <Suspense fallback={null}>
          {openCameras.map((camera, index) => (
            <EmbeddedCameraViewer
              key={camera.id}
              printerId={camera.id}
              printerName={camera.name}
              viewerIndex={index}
              onClose={() => closeEmbeddedCamera(camera.id)}
            />
          ))}
        </Suspense>
      )}
    </EmbeddedCameraContext.Provider>
  );
}

export function useEmbeddedCameras(): EmbeddedCameraContextType {
  const context = useContext(EmbeddedCameraContext);
  if (!context) {
    throw new Error('useEmbeddedCameras must be used inside EmbeddedCameraProvider');
  }
  return context;
}
