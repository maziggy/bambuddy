import { useState, useCallback } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';

interface UseCameraControlsOptions {
  printerId: number;
}

export function useCameraControls({ printerId }: UseCameraControlsOptions) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const [showSkipObjectsModal, setShowSkipObjectsModal] = useState(false);

  // Fetch printer status for light toggle and skip objects
  const { data: status } = useQuery({
    queryKey: ['printerStatus', printerId],
    queryFn: () => api.getPrinterStatus(printerId),
    refetchInterval: 30000,
    enabled: printerId > 0,
  });

  // Chamber light mutation with optimistic update
  const chamberLightMutation = useMutation({
    mutationFn: (on: boolean) => api.setChamberLight(printerId, on),
    onMutate: async (on) => {
      await queryClient.cancelQueries({ queryKey: ['printerStatus', printerId] });
      const previousStatus = queryClient.getQueryData(['printerStatus', printerId]);
      queryClient.setQueryData(['printerStatus', printerId], (old: typeof status) => ({
        ...old,
        chamber_light: on,
      }));
      return { previousStatus };
    },
    onSuccess: (_, on) => {
      showToast(t(on ? 'printers.chamberLightOn' : 'printers.chamberLightOff'));
    },
    onError: (error: Error, _, context) => {
      if (context?.previousStatus) {
        queryClient.setQueryData(['printerStatus', printerId], context.previousStatus);
      }
      showToast(error.message || t('printers.toast.failedToControlChamberLight'), 'error');
    },
  });

  const isPrintingWithObjects = (status?.state === 'RUNNING' || status?.state === 'PAUSE') && (status?.printable_objects_count ?? 0) >= 2;

  const checkStalled = useCallback(async () => {
    const s = await api.getCameraStatus(printerId);
    return !!(s.stalled || !s.active);
  }, [printerId]);

  return {
    status,
    chamberLightMutation,
    isPrintingWithObjects,
    showSkipObjectsModal,
    setShowSkipObjectsModal,
    checkStalled,
    hasControlPermission: hasPermission('printers:control'),
  };
}
