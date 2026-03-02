import { useState, useEffect, useCallback, useMemo } from 'react';
import { useOutletContext } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { SpoolBuddyOutletContext } from '../../components/spoolbuddy/SpoolBuddyLayout';
import { api, spoolbuddyApi, type InventorySpool } from '../../api/client';

type Tab = 'existing' | 'new' | 'replace';
type WriteStatus = 'idle' | 'selected' | 'writing' | 'success' | 'error';

const COMMON_MATERIALS = ['PLA', 'PETG', 'ABS', 'ASA', 'TPU', 'PA', 'PC', 'PVA', 'HIPS'];

export function SpoolBuddyWriteTagPage() {
  const { t } = useTranslation();
  const { sbState } = useOutletContext<SpoolBuddyOutletContext>();

  const [activeTab, setActiveTab] = useState<Tab>('existing');
  const [selectedSpool, setSelectedSpool] = useState<InventorySpool | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [writeStatus, setWriteStatus] = useState<WriteStatus>('idle');
  const [writeMessage, setWriteMessage] = useState('');
  const [tagOnReader, setTagOnReader] = useState(false);
  const [tagUid, setTagUid] = useState<string | null>(null);

  // New spool form state
  const [newMaterial, setNewMaterial] = useState('PLA');
  const [newColorName, setNewColorName] = useState('');
  const [newColorHex, setNewColorHex] = useState('#00AE42');
  const [newBrand, setNewBrand] = useState('');
  const [newWeight, setNewWeight] = useState(1000);
  const [creating, setCreating] = useState(false);

  const { data: spools = [], refetch: refetchSpools } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(false),
    refetchInterval: 10000,
  });

  const { data: devices = [] } = useQuery({
    queryKey: ['spoolbuddy-devices'],
    queryFn: () => spoolbuddyApi.getDevices(),
    refetchInterval: 5000,
  });

  const device = devices[0];
  const deviceOnline = sbState.deviceOnline;

  // Filter spools based on tab
  const filteredSpools = useMemo(() => {
    let list: InventorySpool[];
    if (activeTab === 'existing') {
      list = spools.filter(s => !s.tag_uid && !s.archived_at);
    } else if (activeTab === 'replace') {
      list = spools.filter(s => s.tag_uid && !s.archived_at);
    } else {
      return [];
    }

    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      list = list.filter(s =>
        (s.material?.toLowerCase().includes(q)) ||
        (s.color_name?.toLowerCase().includes(q)) ||
        (s.brand?.toLowerCase().includes(q)) ||
        (s.subtype?.toLowerCase().includes(q))
      );
    }

    return list;
  }, [spools, activeTab, searchQuery]);

  // Listen for tag events
  const handleUnknownTag = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    const sak = detail.sak ?? detail.data?.sak;
    if (sak === 0x00) {
      setTagOnReader(true);
      setTagUid(detail.tag_uid ?? detail.data?.tag_uid ?? null);
    }
  }, []);

  const handleTagMatched = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    // Tag is on the reader — could be used for replace flow
    setTagOnReader(true);
    setTagUid(detail.tag_uid ?? detail.data?.tag_uid ?? null);
  }, []);

  const handleTagRemoved = useCallback(() => {
    setTagOnReader(false);
    setTagUid(null);
  }, []);

  const handleTagWritten = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    if (detail.spool_id === selectedSpool?.id || detail.data?.spool_id === selectedSpool?.id) {
      setWriteStatus('success');
      setWriteMessage(t('spoolbuddy.writeTag.writeSuccess', 'Tag written successfully!'));
      refetchSpools();
      setTimeout(() => {
        setWriteStatus('idle');
        setSelectedSpool(null);
        setWriteMessage('');
      }, 5000);
    }
  }, [selectedSpool, t, refetchSpools]);

  const handleWriteFailed = useCallback((e: Event) => {
    const detail = (e as CustomEvent).detail;
    if (detail.spool_id === selectedSpool?.id || detail.data?.spool_id === selectedSpool?.id) {
      setWriteStatus('error');
      setWriteMessage(detail.message ?? detail.data?.message ?? t('spoolbuddy.writeTag.writeFailed', 'Write failed'));
    }
  }, [selectedSpool, t]);

  useEffect(() => {
    window.addEventListener('spoolbuddy-unknown-tag', handleUnknownTag);
    window.addEventListener('spoolbuddy-tag-matched', handleTagMatched);
    window.addEventListener('spoolbuddy-tag-removed', handleTagRemoved);
    window.addEventListener('spoolbuddy-tag-written', handleTagWritten);
    window.addEventListener('spoolbuddy-tag-write-failed', handleWriteFailed);
    return () => {
      window.removeEventListener('spoolbuddy-unknown-tag', handleUnknownTag);
      window.removeEventListener('spoolbuddy-tag-matched', handleTagMatched);
      window.removeEventListener('spoolbuddy-tag-removed', handleTagRemoved);
      window.removeEventListener('spoolbuddy-tag-written', handleTagWritten);
      window.removeEventListener('spoolbuddy-tag-write-failed', handleWriteFailed);
    };
  }, [handleUnknownTag, handleTagMatched, handleTagRemoved, handleTagWritten, handleWriteFailed]);

  // Clear selection when switching tabs
  useEffect(() => {
    setSelectedSpool(null);
    setWriteStatus('idle');
    setWriteMessage('');
    setSearchQuery('');
  }, [activeTab]);

  const handleWriteTag = async () => {
    if (!selectedSpool || !device) return;
    setWriteStatus('writing');
    setWriteMessage(t('spoolbuddy.writeTag.waiting', 'Waiting for SpoolBuddy...'));
    try {
      await spoolbuddyApi.writeTag(device.device_id, selectedSpool.id);
    } catch {
      setWriteStatus('error');
      setWriteMessage(t('spoolbuddy.writeTag.queueFailed', 'Failed to queue write command'));
    }
  };

  const handleCancelWrite = async () => {
    if (!device) return;
    try {
      await spoolbuddyApi.cancelWrite(device.device_id);
    } catch { /* ignore */ }
    setWriteStatus('idle');
    setWriteMessage('');
  };

  const handleCreateAndSelect = async () => {
    setCreating(true);
    try {
      const rgba = newColorHex.replace('#', '') + 'FF';
      const spool = await api.createSpool({
        material: newMaterial,
        subtype: null,
        color_name: newColorName || null,
        rgba,
        brand: newBrand || null,
        label_weight: newWeight,
        core_weight: 250,
        core_weight_catalog_id: null,
        weight_used: 0,
        slicer_filament: null,
        slicer_filament_name: null,
        nozzle_temp_min: null,
        nozzle_temp_max: null,
        note: null,
        added_full: true,
        last_used: null,
        encode_time: null,
        tag_uid: null,
        tray_uuid: null,
        data_origin: null,
        tag_type: null,
        cost_per_kg: null,
        last_scale_weight: null,
        last_weighed_at: null,
      });
      setSelectedSpool(spool);
      refetchSpools();
    } catch {
      setWriteMessage(t('spoolbuddy.writeTag.createFailed', 'Failed to create spool'));
      setWriteStatus('error');
    } finally {
      setCreating(false);
    }
  };

  const canWrite = selectedSpool && deviceOnline && writeStatus !== 'writing' && writeStatus !== 'success';

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex border-b border-bambu-dark-tertiary shrink-0">
        {([
          { key: 'existing' as Tab, label: t('spoolbuddy.writeTag.tabExisting', 'Existing Spool') },
          { key: 'new' as Tab, label: t('spoolbuddy.writeTag.tabNew', 'New Spool') },
          { key: 'replace' as Tab, label: t('spoolbuddy.writeTag.tabReplace', 'Replace Tag') },
        ]).map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex-1 py-3 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'text-bambu-green border-b-2 border-bambu-green bg-bambu-dark'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-bambu-dark-tertiary'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Main content: two columns */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left panel — spool list or form */}
        <div className="flex-1 flex flex-col overflow-hidden border-r border-bambu-dark-tertiary">
          {activeTab === 'new' ? (
            <NewSpoolForm
              material={newMaterial}
              setMaterial={setNewMaterial}
              colorName={newColorName}
              setColorName={setNewColorName}
              colorHex={newColorHex}
              setColorHex={setNewColorHex}
              brand={newBrand}
              setBrand={setNewBrand}
              weight={newWeight}
              setWeight={setNewWeight}
              creating={creating}
              onSubmit={handleCreateAndSelect}
              selectedSpool={selectedSpool}
              t={t}
            />
          ) : (
            <>
              {/* Search */}
              <div className="p-3 shrink-0">
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t('spoolbuddy.writeTag.searchPlaceholder', 'Search by material, color, brand...')}
                  className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-bambu-green"
                />
              </div>

              {/* Spool list */}
              <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-2">
                {filteredSpools.length === 0 ? (
                  <div className="text-center text-zinc-500 py-8 text-sm">
                    {activeTab === 'existing'
                      ? t('spoolbuddy.writeTag.noUntaggedSpools', 'No spools without tags')
                      : t('spoolbuddy.writeTag.noTaggedSpools', 'No spools with tags')}
                  </div>
                ) : (
                  filteredSpools.map(spool => (
                    <SpoolListItem
                      key={spool.id}
                      spool={spool}
                      selected={selectedSpool?.id === spool.id}
                      showTag={activeTab === 'replace'}
                      onClick={() => {
                        setSelectedSpool(spool);
                        setWriteStatus('idle');
                        setWriteMessage('');
                      }}
                    />
                  ))
                )}
              </div>
            </>
          )}
        </div>

        {/* Right panel — NFC status + write action */}
        <div className="w-[340px] flex flex-col items-center justify-center p-6 shrink-0">
          <NfcStatusPanel
            writeStatus={writeStatus}
            writeMessage={writeMessage}
            selectedSpool={selectedSpool}
            tagOnReader={tagOnReader}
            tagUid={tagUid}
            deviceOnline={deviceOnline}
            canWrite={!!canWrite}
            isReplace={activeTab === 'replace'}
            onWrite={handleWriteTag}
            onCancel={handleCancelWrite}
            onRetry={() => { setWriteStatus('idle'); setWriteMessage(''); }}
            t={t}
          />
        </div>
      </div>
    </div>
  );
}

// --- Spool list item ---
function SpoolListItem({ spool, selected, showTag, onClick }: {
  spool: InventorySpool;
  selected: boolean;
  showTag: boolean;
  onClick: () => void;
}) {
  const color = spool.rgba ? `#${spool.rgba.slice(0, 6)}` : '#666';
  const remaining = Math.max(0, spool.label_weight - spool.weight_used);
  const pct = spool.label_weight > 0 ? Math.round((remaining / spool.label_weight) * 100) : 0;

  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 p-3 rounded-lg text-left transition-colors ${
        selected
          ? 'bg-bambu-green/15 border border-bambu-green/50'
          : 'bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary border border-transparent'
      }`}
    >
      {/* Color dot */}
      <div
        className="w-8 h-8 rounded-full shrink-0 border border-white/10"
        style={{ backgroundColor: color }}
      />

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-white truncate">
            {spool.brand ? `${spool.brand} ` : ''}{spool.material}{spool.subtype ? ` ${spool.subtype}` : ''}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          {spool.color_name && <span>{spool.color_name}</span>}
          <span>{remaining}g / {spool.label_weight}g ({pct}%)</span>
        </div>
        {showTag && spool.tag_uid && (
          <div className="text-xs text-zinc-500 mt-0.5 font-mono">{spool.tag_uid}</div>
        )}
      </div>

      {/* Check mark when selected */}
      {selected && (
        <svg className="w-5 h-5 text-bambu-green shrink-0" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
        </svg>
      )}
    </button>
  );
}

// --- New spool form ---
function NewSpoolForm({ material, setMaterial, colorName, setColorName, colorHex, setColorHex, brand, setBrand, weight, setWeight, creating, onSubmit, selectedSpool, t }: {
  material: string;
  setMaterial: (v: string) => void;
  colorName: string;
  setColorName: (v: string) => void;
  colorHex: string;
  setColorHex: (v: string) => void;
  brand: string;
  setBrand: (v: string) => void;
  weight: number;
  setWeight: (v: number) => void;
  creating: boolean;
  onSubmit: () => void;
  selectedSpool: InventorySpool | null;
  t: (key: string, fallback: string) => string;
}) {
  if (selectedSpool) {
    return (
      <div className="flex flex-col items-center justify-center h-full p-6 text-center">
        <div
          className="w-12 h-12 rounded-full mb-4 border border-white/10"
          style={{ backgroundColor: selectedSpool.rgba ? `#${selectedSpool.rgba.slice(0, 6)}` : '#666' }}
        />
        <p className="text-white font-medium">
          {selectedSpool.brand ? `${selectedSpool.brand} ` : ''}{selectedSpool.material}
        </p>
        {selectedSpool.color_name && <p className="text-zinc-400 text-sm">{selectedSpool.color_name}</p>}
        <p className="text-zinc-500 text-xs mt-1">{selectedSpool.label_weight}g</p>
        <p className="text-bambu-green text-sm mt-4">{t('spoolbuddy.writeTag.spoolCreated', 'Spool created! Ready to write.')}</p>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4 overflow-y-auto">
      {/* Material */}
      <div>
        <label className="block text-xs text-zinc-400 mb-1">{t('spoolbuddy.writeTag.material', 'Material')}</label>
        <select
          value={material}
          onChange={(e) => setMaterial(e.target.value)}
          className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-sm text-white focus:outline-none focus:border-bambu-green"
        >
          {COMMON_MATERIALS.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>

      {/* Color name + picker */}
      <div className="flex gap-3">
        <div className="flex-1">
          <label className="block text-xs text-zinc-400 mb-1">{t('spoolbuddy.writeTag.colorName', 'Color Name')}</label>
          <input
            type="text"
            value={colorName}
            onChange={(e) => setColorName(e.target.value)}
            placeholder="Jade White"
            className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-bambu-green"
          />
        </div>
        <div>
          <label className="block text-xs text-zinc-400 mb-1">{t('spoolbuddy.writeTag.color', 'Color')}</label>
          <input
            type="color"
            value={colorHex}
            onChange={(e) => setColorHex(e.target.value)}
            className="w-10 h-9 bg-transparent border border-bambu-dark-tertiary rounded cursor-pointer"
          />
        </div>
      </div>

      {/* Brand */}
      <div>
        <label className="block text-xs text-zinc-400 mb-1">{t('spoolbuddy.writeTag.brand', 'Brand')}</label>
        <input
          type="text"
          value={brand}
          onChange={(e) => setBrand(e.target.value)}
          placeholder="Polymaker"
          className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-bambu-green"
        />
      </div>

      {/* Weight */}
      <div>
        <label className="block text-xs text-zinc-400 mb-1">{t('spoolbuddy.writeTag.weight', 'Weight (g)')}</label>
        <input
          type="number"
          value={weight}
          onChange={(e) => setWeight(parseInt(e.target.value) || 0)}
          min={0}
          max={10000}
          className="w-full px-3 py-2 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-sm text-white focus:outline-none focus:border-bambu-green"
        />
      </div>

      {/* Create button */}
      <button
        onClick={onSubmit}
        disabled={creating || !material}
        className="w-full py-2.5 bg-bambu-green hover:bg-bambu-green/80 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded transition-colors"
      >
        {creating
          ? t('spoolbuddy.writeTag.creating', 'Creating...')
          : t('spoolbuddy.writeTag.createSpool', 'Create Spool')}
      </button>
    </div>
  );
}

// --- NFC status panel ---
function NfcStatusPanel({ writeStatus, writeMessage, selectedSpool, tagOnReader, tagUid, deviceOnline, canWrite, isReplace, onWrite, onCancel, onRetry, t }: {
  writeStatus: WriteStatus;
  writeMessage: string;
  selectedSpool: InventorySpool | null;
  tagOnReader: boolean;
  tagUid: string | null;
  deviceOnline: boolean;
  canWrite: boolean;
  isReplace: boolean;
  onWrite: () => void;
  onCancel: () => void;
  onRetry: () => void;
  t: (key: string, fallback: string) => string;
}) {
  // Success state
  if (writeStatus === 'success') {
    return (
      <div className="flex flex-col items-center text-center space-y-4">
        <div className="w-16 h-16 rounded-full bg-green-500/20 flex items-center justify-center">
          <svg className="w-8 h-8 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <p className="text-green-400 font-medium">{writeMessage}</p>
        {selectedSpool && (
          <p className="text-zinc-400 text-sm">
            {selectedSpool.brand ? `${selectedSpool.brand} ` : ''}{selectedSpool.material}
            {selectedSpool.color_name ? ` - ${selectedSpool.color_name}` : ''}
          </p>
        )}
      </div>
    );
  }

  // Error state
  if (writeStatus === 'error') {
    return (
      <div className="flex flex-col items-center text-center space-y-4">
        <div className="w-16 h-16 rounded-full bg-red-500/20 flex items-center justify-center">
          <svg className="w-8 h-8 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </div>
        <p className="text-red-400 font-medium">{writeMessage}</p>
        <button
          onClick={onRetry}
          className="px-4 py-2 bg-bambu-dark-tertiary hover:bg-bambu-dark-secondary text-white text-sm rounded transition-colors"
        >
          {t('spoolbuddy.writeTag.tryAgain', 'Try Again')}
        </button>
      </div>
    );
  }

  // Writing state
  if (writeStatus === 'writing') {
    return (
      <div className="flex flex-col items-center text-center space-y-4">
        <div className="relative w-16 h-16">
          <div className="absolute inset-0 rounded-full border-2 border-bambu-green/30 animate-ping" />
          <div className="absolute inset-2 rounded-full border-2 border-bambu-green/50 animate-pulse" />
          <div className="absolute inset-0 flex items-center justify-center">
            <NfcIcon className="w-8 h-8 text-bambu-green" />
          </div>
        </div>
        <p className="text-bambu-green font-medium">{t('spoolbuddy.writeTag.writing', 'Writing tag...')}</p>
        <p className="text-zinc-500 text-xs">{writeMessage}</p>
        <button
          onClick={onCancel}
          className="px-4 py-2 bg-bambu-dark-tertiary hover:bg-bambu-dark-secondary text-zinc-400 text-sm rounded transition-colors"
        >
          {t('spoolbuddy.writeTag.cancel', 'Cancel')}
        </button>
      </div>
    );
  }

  // Device offline
  if (!deviceOnline) {
    return (
      <div className="flex flex-col items-center text-center space-y-3">
        <NfcIcon className="w-12 h-12 text-zinc-600" />
        <p className="text-zinc-500 text-sm">{t('spoolbuddy.writeTag.deviceOffline', 'SpoolBuddy is offline')}</p>
      </div>
    );
  }

  // No spool selected
  if (!selectedSpool) {
    return (
      <div className="flex flex-col items-center text-center space-y-3">
        <NfcIcon className="w-12 h-12 text-zinc-600" />
        <p className="text-zinc-400 text-sm">{t('spoolbuddy.writeTag.selectSpool', 'Select a spool, then place a blank NTAG on the reader')}</p>
      </div>
    );
  }

  // Spool selected — show summary + write button
  const spoolColor = selectedSpool.rgba ? `#${selectedSpool.rgba.slice(0, 6)}` : '#666';

  return (
    <div className="flex flex-col items-center text-center space-y-4 w-full">
      {/* NFC indicator */}
      <div className="relative w-16 h-16">
        {tagOnReader ? (
          <>
            <div className="absolute inset-0 rounded-full bg-bambu-green/10" />
            <div className="absolute inset-0 flex items-center justify-center">
              <NfcIcon className="w-8 h-8 text-bambu-green" />
            </div>
          </>
        ) : (
          <>
            <div className="absolute inset-0 rounded-full border-2 border-zinc-600 animate-pulse" />
            <div className="absolute inset-0 flex items-center justify-center">
              <NfcIcon className="w-8 h-8 text-zinc-500" />
            </div>
          </>
        )}
      </div>

      {tagOnReader ? (
        <div className="space-y-1">
          <p className="text-bambu-green text-sm font-medium">{t('spoolbuddy.writeTag.tagReady', 'Tag detected — ready to write')}</p>
          {tagUid && <p className="text-zinc-500 text-xs font-mono">{tagUid}</p>}
        </div>
      ) : (
        <p className="text-zinc-400 text-sm">{t('spoolbuddy.writeTag.placeTag', 'Place an NTAG on the reader')}</p>
      )}

      {/* Selected spool summary */}
      <div className="w-full bg-bambu-dark-secondary rounded-lg p-3 space-y-2">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full border border-white/10 shrink-0" style={{ backgroundColor: spoolColor }} />
          <div className="text-left min-w-0">
            <p className="text-white text-sm font-medium truncate">
              {selectedSpool.brand ? `${selectedSpool.brand} ` : ''}{selectedSpool.material}
            </p>
            {selectedSpool.color_name && <p className="text-zinc-400 text-xs">{selectedSpool.color_name}</p>}
          </div>
        </div>
        <div className="text-xs text-zinc-500">{selectedSpool.label_weight}g</div>
      </div>

      {/* Replace warning */}
      {isReplace && selectedSpool.tag_uid && (
        <p className="text-yellow-500/80 text-xs">
          {t('spoolbuddy.writeTag.replaceWarning', 'Old tag will be unlinked. New tag will replace it.')}
        </p>
      )}

      {/* Write button */}
      <button
        onClick={onWrite}
        disabled={!canWrite}
        className="w-full py-3 bg-bambu-green hover:bg-bambu-green/80 disabled:opacity-40 disabled:cursor-not-allowed text-white font-medium rounded-lg transition-colors text-sm"
      >
        {isReplace
          ? t('spoolbuddy.writeTag.replaceTag', 'Replace Tag')
          : t('spoolbuddy.writeTag.writeTag', 'Write Tag')}
      </button>
    </div>
  );
}

// --- NFC icon ---
function NfcIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.288 15.038a5.25 5.25 0 017.424 0M5.106 11.856c3.807-3.808 9.98-3.808 13.788 0M1.924 8.674c5.565-5.565 14.587-5.565 20.152 0" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M12.53 18.22l-.53.53-.53-.53a.75.75 0 011.06 0z" />
    </svg>
  );
}
