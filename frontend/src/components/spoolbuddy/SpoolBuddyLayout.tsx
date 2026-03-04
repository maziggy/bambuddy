import { useState, useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import { SpoolBuddyTopBar } from './SpoolBuddyTopBar';
import { SpoolBuddyBottomNav } from './SpoolBuddyBottomNav';
import { SpoolBuddyStatusBar } from './SpoolBuddyStatusBar';
import { useSpoolBuddyState } from '../../hooks/useSpoolBuddyState';

export function SpoolBuddyLayout() {
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);
  const [alert, setAlert] = useState<{ type: 'warning' | 'error' | 'info'; message: string } | null>(null);
  const sbState = useSpoolBuddyState();

  // Force dark theme on mount, restore on unmount
  useEffect(() => {
    const root = document.documentElement;
    const hadDark = root.classList.contains('dark');
    root.classList.add('dark');
    return () => {
      if (!hadDark) root.classList.remove('dark');
    };
  }, []);

  // Update alert based on device state
  useEffect(() => {
    if (!sbState.deviceOnline) {
      setAlert({ type: 'warning', message: 'SpoolBuddy device disconnected' });
    } else {
      setAlert(null);
    }
  }, [sbState.deviceOnline]);

  return (
    <div className="w-screen h-screen bg-bambu-dark text-white flex flex-col overflow-hidden">
      <SpoolBuddyTopBar
        selectedPrinterId={selectedPrinterId}
        onPrinterChange={setSelectedPrinterId}
        deviceOnline={sbState.deviceOnline}
      />

      <main className="flex-1 overflow-y-auto">
        <Outlet context={{ selectedPrinterId, setSelectedPrinterId, sbState, setAlert }} />
      </main>

      <SpoolBuddyStatusBar alert={alert} />
      <SpoolBuddyBottomNav />
    </div>
  );
}

// Hook for child pages to access shared context
export interface SpoolBuddyOutletContext {
  selectedPrinterId: number | null;
  setSelectedPrinterId: (id: number) => void;
  sbState: ReturnType<typeof useSpoolBuddyState>;
  setAlert: (alert: { type: 'warning' | 'error' | 'info'; message: string } | null) => void;
}
