import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import type { AMSUnit } from '../api/client';

export function AMSNameHoverCard({
  ams,
  printerId,
  label,
  amsLabels,
  canEdit,
  onSaved,
  children,
}: {
  ams: AMSUnit;
  printerId: number;
  label: string;           // auto-generated label, e.g. "AMS-A"
  amsLabels?: Record<number, string>;
  canEdit: boolean;
  onSaved: () => void;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [isVisible, setIsVisible] = useState(false);
  const [position, setPosition] = useState<'top' | 'bottom'>('top');
  const [editValue, setEditValue] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isInputFocused, setIsInputFocused] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (isVisible) {
      setEditValue(amsLabels?.[ams.id] ?? '');
      setSaveError(null);
      requestAnimationFrame(() => {
        if (triggerRef.current && cardRef.current) {
          const rect = triggerRef.current.getBoundingClientRect();
          const spaceAbove = rect.top - 56;
          const spaceBelow = window.innerHeight - rect.bottom;
          setPosition(spaceAbove < cardRef.current.offsetHeight + 12 && spaceBelow > spaceAbove ? 'bottom' : 'top');
        }
      });
    }
  }, [isVisible, amsLabels, ams.id]);

  const handleMouseEnter = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(true), 80);
  };
  const handleMouseLeave = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    if (!isInputFocused) {
      timeoutRef.current = setTimeout(() => setIsVisible(false), 200);
    }
  };
  useEffect(() => () => { if (timeoutRef.current) clearTimeout(timeoutRef.current); }, []);

  const handleSave = async () => {
    if (!canEdit) return;
    if (!printerId) { setSaveError(t('printers.amsPopup.printerIdUnavailable')); return; }
    setIsSaving(true);
    setSaveError(null);
    try {
      const trimmed = editValue.trim();
      if (trimmed) {
        await api.saveAmsLabel(printerId, ams.id, trimmed, ams.serial_number);
      } else {
        await api.deleteAmsLabel(printerId, ams.id, ams.serial_number);
      }
      onSaved();
      setIsVisible(false);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSaving(false);
    }
  };

  const handleClear = async () => {
    if (!canEdit) return;
    if (!printerId) { setSaveError(t('printers.amsPopup.printerIdUnavailable')); return; }
    setIsSaving(true);
    setSaveError(null);
    try {
      await api.deleteAmsLabel(printerId, ams.id, ams.serial_number);
      setEditValue('');
      onSaved();
      setIsVisible(false);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div
      ref={triggerRef}
      className="relative inline-block"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {children}

      {isVisible && (
        <div
          ref={cardRef}
          className={`
            absolute left-0 z-50
            ${position === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'}
            animate-in fade-in-0 zoom-in-95 duration-150
          `}
          style={{ maxWidth: 'calc(100vw - 24px)' }}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          <div className="w-52 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl overflow-hidden backdrop-blur-sm p-2.5 space-y-2">
            {/* AMS auto-label */}
            <div className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">{label}</div>

            {/* Serial number */}
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] tracking-wide text-bambu-gray font-medium shrink-0">
                {t('printers.amsPopup.serialNumber')}
              </span>
              <span className="text-[10px] text-white font-mono truncate">{ams.serial_number || '—'}</span>
            </div>

            {/* Firmware version */}
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] tracking-wide text-bambu-gray font-medium shrink-0">
                {t('printers.amsPopup.firmwareVersion')}
              </span>
              <span className="text-[10px] text-white font-mono truncate">{ams.sw_ver || '—'}</span>
            </div>

            {/* Divider */}
            <div className="h-px bg-bambu-dark-tertiary/50" />

            {/* Friendly name editor */}
            <div className="space-y-1">
              <span className="text-[10px] text-bambu-gray font-medium block">
                {t('printers.amsPopup.friendlyName')}
              </span>
              <input
                type="text"
                value={editValue}
                onChange={(e) => canEdit && setEditValue(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSave()}
                onFocus={() => setIsInputFocused(true)}
                onBlur={(e) => {
                  setIsInputFocused(false);
                  // Don't close if focus moved to another element inside the card (e.g. Save/Clear button)
                  if (cardRef.current?.contains(e.relatedTarget as Node)) return;
                  if (timeoutRef.current) clearTimeout(timeoutRef.current);
                  timeoutRef.current = setTimeout(() => setIsVisible(false), 200);
                }}
                placeholder={canEdit ? t('printers.amsPopup.friendlyNamePlaceholder') : (amsLabels?.[ams.id] || '—')}
                disabled={!canEdit}
                title={!canEdit ? t('printers.amsPopup.noEditPermission') : undefined}
                className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1 text-xs text-white placeholder-bambu-gray/60 focus:outline-none focus:border-bambu-green disabled:opacity-50 disabled:cursor-not-allowed"
                maxLength={100}
              />
              {canEdit && (
                <div className="space-y-1">
                  {saveError && (
                    <p className="text-[10px] text-red-400 break-words">{saveError}</p>
                  )}
                  <div className="flex gap-1 justify-end">
                    <button
                      onClick={handleSave}
                      disabled={isSaving}
                      className="px-2 py-0.5 text-[10px] bg-bambu-green text-white rounded hover:bg-bambu-green/80 disabled:opacity-50"
                    >
                      {t('printers.amsPopup.save')}
                    </button>
                    {amsLabels?.[ams.id] && (
                      <button
                        onClick={handleClear}
                        disabled={isSaving}
                        className="px-2 py-0.5 text-[10px] bg-bambu-dark-tertiary text-bambu-gray rounded hover:bg-bambu-dark-tertiary/70 disabled:opacity-50"
                      >
                        {t('printers.amsPopup.clear')}
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
