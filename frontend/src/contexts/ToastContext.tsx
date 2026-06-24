import { AlertCircle, CheckCircle, Info, Loader2, X, XCircle } from 'lucide-react';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

type ToastType = 'success' | 'error' | 'warning' | 'info' | 'loading';

interface ToastAction {
  label: string;
  href: string;
  onClick?: () => void;
}

type ShowPersistentToast = (
  id: string,
  message: string,
  type?: ToastType,
  options?: { action?: ToastAction },
) => void;

interface Toast {
  id: string;
  message: string;
  type: ToastType;
  persistent?: boolean;
  action?: ToastAction;
}

interface ToastContextType {
  showToast: (message: string, type?: ToastType) => void;
  showPersistentToast: ShowPersistentToast;
  dismissToast: (id: string) => void;
  /**
   * Suppress the visible toast viewport while keeping the state machine alive.
   * Used by the SpoolBuddy kiosk layout to keep the kiosk display free of
   * main-app notifications.
   */
  setViewportSuppressed: (suppressed: boolean) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}

const icons = {
  success: <CheckCircle className="w-5 h-5 text-green-400" />,
  error: <XCircle className="w-5 h-5 text-red-400" />,
  warning: <AlertCircle className="w-5 h-5 text-yellow-400" />,
  info: <Info className="w-5 h-5 text-blue-400" />,
  loading: <Loader2 className="w-5 h-5 text-bambu-green animate-spin" />,
};

const bgColors = {
  success: 'bg-green-500/10 border-green-500/30',
  error: 'bg-red-500/10 border-red-500/30',
  warning: 'bg-yellow-500/10 border-yellow-500/30',
  info: 'bg-blue-500/10 border-blue-500/30',
  loading: 'bg-bambu-green/10 border-bambu-green/30',
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [viewportSuppressed, setViewportSuppressed] = useState(false);
  const timeoutRefs = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  // Tracks whether the provider is still mounted. A toast can be triggered by
  // an async callback that resolves AFTER React has unmounted us (common in
  // tests: `cleanup()` runs while a login promise is still in flight, then
  // the error handler calls showToast). In that case, scheduling a setTimeout
  // that later calls setToasts produces "window is not defined" once the jsdom
  // environment is torn down. Guard every setToasts call behind this ref so a
  // post-unmount showToast is a no-op instead of crashing.
  const isMountedRef = useRef(true);

  // Clean up all timeouts on unmount
  useEffect(() => {
    isMountedRef.current = true;
    const timeouts = timeoutRefs.current;
    return () => {
      isMountedRef.current = false;
      timeouts.forEach((timeout) => clearTimeout(timeout));
      timeouts.clear();
    };
  }, []);

  const showToast = useCallback((message: string, type: ToastType = 'success') => {
    if (!isMountedRef.current) return;
    const id = Math.random().toString(36).substr(2, 9);
    setToasts((prev) => [...prev, { id, message, type }]);

    // Auto-dismiss after 3 seconds
    const timeout = setTimeout(() => {
      if (!isMountedRef.current) return;
      setToasts((prev) => prev.filter((t) => t.id !== id));
      timeoutRefs.current.delete(id);
    }, 3000);
    timeoutRefs.current.set(id, timeout);
  }, []);

  const showPersistentToast = useCallback(
    (id: string, message: string, type: ToastType = 'info', options?: { action?: ToastAction }) => {
      if (!isMountedRef.current) return;
      setToasts((prev) => {
        // Update existing toast if same id, otherwise add new one
        const exists = prev.find((t) => t.id === id);
        if (exists) {
          return prev.map((t) =>
            t.id === id ? { ...t, message, type, persistent: true, action: options?.action } : t,
          );
        }
        return [...prev, { id, message, type, persistent: true, action: options?.action }];
      });
    },
    [],
  );

  const dismissToast = useCallback((id: string) => {
    if (!isMountedRef.current) return;
    // Clear any pending auto-dismiss timeout
    const timeout = timeoutRefs.current.get(id);
    if (timeout) {
      clearTimeout(timeout);
      timeoutRefs.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ showToast, showPersistentToast, dismissToast, setViewportSuppressed }}>
      {children}

      {/* Toast Container — to the left of the bug-report bubble (bottom-4 right-4 w-12).
          The kiosk layout suppresses this entire viewport so SpoolBuddy displays stay
          free of main-app notifications. */}
      <div className={`fixed bottom-4 right-20 z-[60] flex flex-col items-end gap-2 ${viewportSuppressed ? 'hidden' : ''}`}>
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`rounded-lg border shadow-lg backdrop-blur-sm animate-slide-in ${bgColors[toast.type]} flex items-center gap-3 px-4 py-3`}
          >
            {icons[toast.type]}
            <span className="text-white text-sm">{toast.message}</span>
            {toast.action && (
              <a
                href={toast.action.href}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => {
                  toast.action?.onClick?.();
                  dismissToast(toast.id);
                }}
                className="ml-2 px-2 py-1 rounded text-xs font-medium bg-bambu-green/20 text-bambu-green hover:bg-bambu-green/30 whitespace-nowrap"
              >
                {toast.action.label}
              </a>
            )}
            <button
              onClick={() => dismissToast(toast.id)}
              className="ml-2 text-bambu-gray hover:text-white transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
