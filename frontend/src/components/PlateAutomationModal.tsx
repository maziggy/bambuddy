import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  printerId: number;
  initial?: Automation | null;
  onSaved?: () => void;
}

export function PlateAutomationModal({ isOpen, onClose, printerId, initial = null, onSaved }: Props) {
  const { t } = useTranslation();
  const [startCode, setStartCode] = useState('');
  const [startDetect, setStartDetect] = useState('');
  const [startAfter, setStartAfter] = useState('');
  const [endCode, setEndCode] = useState('');
  const [endDetect, setEndDetect] = useState('');
  const [endAfter, setEndAfter] = useState('');

  useEffect(() => {
    if (!isOpen) return;
    // Populate from initial
    setStartCode(initial?.start_code ?? '');
    setStartDetect(initial?.start_code_detect ?? '');
    setStartAfter(initial?.start_code_after ?? '');
    setEndCode(initial?.end_code ?? '');
    setEndDetect(initial?.end_code_detect ?? '');
    setEndAfter(initial?.end_code_after ?? '');
  }, [isOpen, initial]);

  const createMut = useMutation((data: Partial<Automation>) => api.createAutomation(printerId, data));
  const updateMut = useMutation((args: { id: number; data: Partial<Automation> }) => api.updateAutomation(args.id, args.data));
  const deleteMut = useMutation((id: number) => api.deleteAutomation(id));

  const isAllBlank = () => {
    return [startCode, startDetect, startAfter, endCode, endDetect, endAfter].every(s => !s || s.trim() === '');
  };

  const handleSave = async () => {
    try {
      const payload: Partial<Automation> = {
        start_code: startCode || '',
        start_code_detect: startDetect || '',
        start_code_after: startAfter || '',
        end_code: endCode || '',
        end_code_detect: endDetect || '',
        end_code_after: endAfter || '',
      };

      if (isAllBlank()) {
        // If nothing and record exists -> delete, else just close
        if (initial && (initial as any).id) {
          await deleteMut.mutateAsync((initial as any).id);
        }
      } else {
        if (initial && (initial as any).id) {
          await updateMut.mutateAsync({ id: (initial as any).id, data: payload });
        } else {
          await createMut.mutateAsync(payload);
        }
      }

      onSaved?.();
      onClose();
    } catch (e) {
      // Let outer components show error toasts; keep modal open
      console.error('Failed to save automation', e);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-bambu-dark rounded-lg w-full max-w-2xl p-4" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-lg font-semibold">{t('printers.plateAutomation.manageCustomization')}</h3>
          <Button variant="ghost" onClick={onClose}><X className="w-4 h-4" /></Button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-sm text-bambu-gray mb-1">Start Code (multiline)</label>
            <textarea value={startCode} onChange={e => setStartCode(e.target.value)} className="w-full bg-bambu-dark-secondary p-2 rounded text-sm" rows={6} />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Start Detect (single-line)</label>
              <input value={startDetect} onChange={e => setStartDetect(e.target.value)} className="w-full bg-bambu-dark-secondary p-2 rounded text-sm" />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Start After (single-line)</label>
              <input value={startAfter} onChange={e => setStartAfter(e.target.value)} className="w-full bg-bambu-dark-secondary p-2 rounded text-sm" />
            </div>
          </div>

          <div>
            <label className="block text-sm text-bambu-gray mb-1">End Code (multiline)</label>
            <textarea value={endCode} onChange={e => setEndCode(e.target.value)} className="w-full bg-bambu-dark-secondary p-2 rounded text-sm" rows={6} />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm text-bambu-gray mb-1">End Detect (single-line)</label>
              <input value={endDetect} onChange={e => setEndDetect(e.target.value)} className="w-full bg-bambu-dark-secondary p-2 rounded text-sm" />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">End After (single-line)</label>
              <input value={endAfter} onChange={e => setEndAfter(e.target.value)} className="w-full bg-bambu-dark-secondary p-2 rounded text-sm" />
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-4">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSave}>
            {(createMut.isLoading || updateMut.isLoading || deleteMut.isLoading) ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Save'}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default PlateAutomationModal;
