import { useState, useEffect, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { X, ExternalLink, Box, Code2, Loader2, Layers, Check, Maximize2, Minimize2 } from 'lucide-react';
import { ModelViewer } from './ModelViewer';
import { GcodeViewer } from './GcodeViewer';
import { Button } from './Button';
import { api } from '../api/client';
import { openInSlicer, type SlicerType } from '../utils/slicer';
import type { ArchivePlatesResponse, LibraryFilePlatesResponse, PlateMetadata } from '../types/plates';

type ViewTab = '3d' | 'gcode';

interface ModelViewerModalProps {
  archiveId?: number;
  libraryFileId?: number;
  title: string;
  fileType?: string;
  onClose: () => void;
}

interface Capabilities {
  has_model: boolean;
  has_gcode: boolean;
  has_source: boolean;
  build_volume: { x: number; y: number; z: number };
  filament_colors: string[];
}

export function ModelViewerModal({ archiveId, libraryFileId, title, fileType, onClose }: ModelViewerModalProps) {
  const { t } = useTranslation();
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings });
  const preferredSlicer: SlicerType = settings?.preferred_slicer || 'bambu_studio';
  const isLibrary = libraryFileId != null;
  const [activeTab, setActiveTab] = useState<ViewTab | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [platesData, setPlatesData] = useState<ArchivePlatesResponse | LibraryFilePlatesResponse | null>(null);
  const [platesLoading, setPlatesLoading] = useState(false);
  const [selectedPlateId, setSelectedPlateId] = useState<number | null>(null);
  const [platePage, setPlatePage] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [platePanelHeight, setPlatePanelHeight] = useState<number | null>(null);
  const [isDraggingDivider, setIsDraggingDivider] = useState(false);
  const [hasCustomSplit, setHasCustomSplit] = useState(false);
  const splitContainerRef = useRef<HTMLDivElement>(null);
  const platesPanelRef = useRef<HTMLDivElement>(null);
  const dividerHeight = 10;
  const minPlateHeight = 160;
  const minViewerPx = 240;
  const minViewerRatio = 0.35;

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    setLoading(true);

    if (isLibrary) {
      const normalizedType = (fileType || '').toLowerCase();
      const hasModel = normalizedType === '3mf' || normalizedType === 'stl';
      const hasGcode = normalizedType === 'gcode' || normalizedType === '3mf';
      setCapabilities({
        has_model: hasModel,
        has_gcode: hasGcode,
        has_source: false,
        build_volume: { x: 256, y: 256, z: 256 },
        filament_colors: [],
      });
      setActiveTab(hasModel ? '3d' : hasGcode ? 'gcode' : null);
      setLoading(false);
      return;
    }

    if (!archiveId) {
      setCapabilities(null);
      setActiveTab(null);
      setLoading(false);
      return;
    }

    api.getArchiveCapabilities(archiveId)
      .then(caps => {
        setCapabilities(caps);
        // Auto-select the first available tab
        if (caps.has_model) {
          setActiveTab('3d');
        } else if (caps.has_gcode) {
          setActiveTab('gcode');
        }
        setLoading(false);
      })
      .catch(() => {
        // Fallback to 3D model tab if capabilities check fails
        setCapabilities({ has_model: true, has_gcode: false, has_source: false, build_volume: { x: 256, y: 256, z: 256 }, filament_colors: [] });
        setActiveTab('3d');
        setLoading(false);
      });
  }, [archiveId, fileType, isLibrary]);

  useEffect(() => {
    setPlatesLoading(true);
    setSelectedPlateId(null);
    setPlatePage(0);

    if (isLibrary) {
      const normalizedType = (fileType || '').toLowerCase();
      if (!libraryFileId || normalizedType !== '3mf') {
        setPlatesData(null);
        setPlatesLoading(false);
        return;
      }
      api.getLibraryFilePlates(libraryFileId)
        .then((data) => setPlatesData(data))
        .catch(() => setPlatesData(null))
        .finally(() => setPlatesLoading(false));
      return;
    }

    if (!archiveId) {
      setPlatesData(null);
      setPlatesLoading(false);
      return;
    }

    api.getArchivePlates(archiveId)
      .then((data) => setPlatesData(data))
      .catch(() => setPlatesData(null))
      .finally(() => setPlatesLoading(false));
  }, [archiveId, fileType, isLibrary, libraryFileId]);

  const plates = useMemo(() => platesData?.plates ?? [], [platesData]);
  const hasMultiplePlates = (platesData?.is_multi_plate ?? false) && plates.length > 1;
  const splitFullscreen = isFullscreen && hasMultiplePlates;
  const selectedPlate: PlateMetadata | null = selectedPlateId == null
    ? null
    : plates.find((plate) => plate.index === selectedPlateId) ?? null;
  const getPlateObjectCount = (plate: PlateMetadata): number => plate.object_count ?? plate.objects?.length ?? 0;
  const totalObjectCount = plates.reduce((sum, plate) => sum + getPlateObjectCount(plate), 0);
  const selectedObjectCount = selectedPlate ? getPlateObjectCount(selectedPlate) : totalObjectCount;
  const objectCountLabel = selectedPlate ? t('modelViewer.plateNumber', { number: selectedPlate.index }) : t('modelViewer.allPlates');
  const hasObjectCount = plates.length > 0;
  const platesGridRef = useRef<HTMLDivElement>(null);
  const platesViewportRef = useRef<HTMLDivElement>(null);
  const [platesPerPage, setPlatesPerPage] = useState(10);
  const [plateColumns, setPlateColumns] = useState(3);
  const shouldPaginatePlates = plates.length > platesPerPage;
  const totalPlatePages = Math.max(1, Math.ceil(plates.length / platesPerPage));
  const pagedPlates = shouldPaginatePlates
    ? plates.slice(platePage * platesPerPage, (platePage + 1) * platesPerPage)
    : plates;

  useEffect(() => {
    if (!splitFullscreen) {
      setPlatesPerPage(10);
      setPlateColumns(3);
      return;
    }
    const grid = platesGridRef.current;
    const viewport = platesViewportRef.current;
    if (!grid || !viewport) return;
    let rafId = 0;
    const updateLayout = () => {
      const availableWidth = viewport.clientWidth;
      const minButtonWidth = 210;
      const computedCols = Math.floor(availableWidth / minButtonWidth);
      const nextCols = Math.max(3, Math.min(5, computedCols || 3));
      setPlateColumns((prev) => (prev === nextCols ? prev : nextCols));

      const computed = window.getComputedStyle(grid);
      const rowGap = Number.parseFloat(computed.rowGap || '0');
      const firstItem = grid.querySelector<HTMLElement>('button');
      const rowHeight = firstItem?.getBoundingClientRect().height ?? 44;
      const availableHeight = viewport.clientHeight;
      const rows = Math.max(1, Math.floor((availableHeight + rowGap) / (rowHeight + rowGap)));
      const maxSlots = rows * nextCols;
      const nextPerPage = Math.max(1, maxSlots - 1);
      setPlatesPerPage((prev) => (prev === nextPerPage ? prev : nextPerPage));
    };
    const scheduleUpdate = () => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(updateLayout);
    };
    scheduleUpdate();
    const resizeObserver = new ResizeObserver(scheduleUpdate);
    resizeObserver.observe(viewport);
    resizeObserver.observe(grid);
    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      resizeObserver.disconnect();
    };
  }, [splitFullscreen, plates.length]);

  useEffect(() => {
    if (!shouldPaginatePlates) {
      setPlatePage(0);
      return;
    }
    setPlatePage((prev) => Math.min(prev, totalPlatePages - 1));
  }, [plates.length, shouldPaginatePlates, totalPlatePages]);

  useEffect(() => {
    if (!shouldPaginatePlates || selectedPlateId == null) return;
    const selectedIndex = plates.findIndex((plate) => plate.index === selectedPlateId);
    if (selectedIndex < 0) return;
    const nextPage = Math.floor(selectedIndex / platesPerPage);
    setPlatePage((prev) => (prev === nextPage ? prev : nextPage));
  }, [plates, platesPerPage, selectedPlateId, shouldPaginatePlates]);

  useEffect(() => {
    if (!splitFullscreen) {
      setPlatePanelHeight(null);
      setHasCustomSplit(false);
      return;
    }
    if (hasCustomSplit) return;
    const container = splitContainerRef.current;
    const panel = platesPanelRef.current;
    if (!container || !panel) return;
    const containerHeight = container.clientHeight;
    if (!containerHeight) return;
    const minViewerHeight = Math.max(minViewerPx, containerHeight * minViewerRatio);
    const maxPlateHeight = Math.max(minPlateHeight, containerHeight - dividerHeight - minViewerHeight);
    const desiredHeight = Math.min(panel.scrollHeight, maxPlateHeight);
    setPlatePanelHeight(Math.max(minPlateHeight, desiredHeight));
  }, [splitFullscreen, hasCustomSplit, plates.length, platePage, dividerHeight, minPlateHeight, minViewerPx, minViewerRatio]);

  useEffect(() => {
    if (!isDraggingDivider) return;
    const handleMouseMove = (event: MouseEvent) => {
      const container = splitContainerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const containerHeight = rect.height;
      if (!containerHeight) return;
      const minViewerHeight = Math.max(minViewerPx, containerHeight * minViewerRatio);
      const maxPlateHeight = Math.max(minPlateHeight, containerHeight - dividerHeight - minViewerHeight);
      const nextHeight = Math.min(maxPlateHeight, Math.max(minPlateHeight, event.clientY - rect.top));
      setPlatePanelHeight(nextHeight);
    };
    const handleMouseUp = () => {
      setIsDraggingDivider(false);
      setHasCustomSplit(true);
    };
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isDraggingDivider, dividerHeight, minPlateHeight, minViewerPx, minViewerRatio]);

  const canOpenInSlicer = isLibrary ? (fileType || '').toLowerCase() === '3mf' : true;

  const handleOpenInSlicer = async () => {
    if (!canOpenInSlicer) return;
    const filename = title || 'model';
    try {
      if (isLibrary) {
        const { token } = await api.createLibrarySlicerToken(libraryFileId!);
        const path = api.getLibrarySlicerDownloadUrl(libraryFileId!, token, filename);
        openInSlicer(`${window.location.origin}${path}`, preferredSlicer);
      } else {
        const { token } = await api.createArchiveSlicerToken(archiveId!);
        const path = api.getArchiveSlicerDownloadUrl(archiveId!, token, filename);
        openInSlicer(`${window.location.origin}${path}`, preferredSlicer);
      }
    } catch {
      // Fallback to direct URL (works when auth is disabled)
      if (isLibrary) {
        const downloadUrl = `${window.location.origin}${api.getLibraryFileDownloadUrl(libraryFileId!)}`;
        openInSlicer(downloadUrl, preferredSlicer);
      } else {
        const downloadUrl = `${window.location.origin}${api.getArchiveForSlicer(archiveId!, filename)}`;
        openInSlicer(downloadUrl, preferredSlicer);
      }
    }
  };

  return (
    <div
      className={`fixed inset-0 bg-black/70 flex items-center justify-center z-50 ${isFullscreen ? 'p-0' : 'p-8'}`}
      onClick={onClose}
    >
      <div
        className={`bg-bambu-dark-secondary border border-bambu-dark-tertiary w-full flex flex-col ${
          isFullscreen ? 'h-full max-w-none rounded-none' : 'h-[80vh] max-w-4xl rounded-xl'
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-3 min-w-0 flex-1 mr-4">
            <h2 className="text-lg font-semibold text-white truncate">{title}</h2>
            {hasObjectCount && (
              <span className="text-xs text-bambu-gray bg-bambu-dark-tertiary/70 px-2 py-1 rounded whitespace-nowrap">
                {objectCountLabel}: {t('modelViewer.objectCount', { count: selectedObjectCount })}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={handleOpenInSlicer} disabled={!canOpenInSlicer}>
              <ExternalLink className="w-4 h-4" />
              {t('modelViewer.openInSlicer')}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setIsFullscreen((prev) => !prev)}
              title={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
            >
              {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="w-5 h-5" />
            </Button>
          </div>
        </div>

        {/* Tabs - only show if we have capabilities */}
        {capabilities && (
          <div className="flex border-b border-bambu-dark-tertiary">
            <button
              onClick={() => capabilities.has_model && setActiveTab('3d')}
              disabled={!capabilities.has_model}
              className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors ${
                activeTab === '3d'
                  ? 'text-bambu-green border-b-2 border-bambu-green'
                  : capabilities.has_model
                    ? 'text-bambu-gray hover:text-white'
                    : 'text-bambu-gray/30 cursor-not-allowed'
              }`}
            >
              <Box className="w-4 h-4" />
              {t('modelViewer.tabs.model')}
              {!capabilities.has_model && <span className="text-xs">({t('modelViewer.notAvailable')})</span>}
            </button>
            <button
              onClick={() => capabilities.has_gcode && setActiveTab('gcode')}
              disabled={!capabilities.has_gcode}
              className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors ${
                activeTab === 'gcode'
                  ? 'text-bambu-green border-b-2 border-bambu-green'
                  : capabilities.has_gcode
                    ? 'text-bambu-gray hover:text-white'
                    : 'text-bambu-gray/30 cursor-not-allowed'
              }`}
            >
              <Code2 className="w-4 h-4" />
              {t('modelViewer.tabs.gcode')}
              {!capabilities.has_gcode && <span className="text-xs">({t('modelViewer.notSliced')})</span>}
            </button>
          </div>
        )}

        {/* Viewer */}
        <div className="flex-1 overflow-hidden p-4">
          {loading ? (
            <div className="w-full h-full flex items-center justify-center">
              <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
            </div>
          ) : activeTab === '3d' && capabilities ? (
            <div
              ref={splitContainerRef}
              className={`w-full h-full flex flex-col ${splitFullscreen ? 'gap-0 min-h-0' : 'gap-3'}`}
            >
              {hasMultiplePlates && (
                <div
                  ref={platesPanelRef}
                  style={splitFullscreen && platePanelHeight != null ? { height: platePanelHeight } : undefined}
                  className={`rounded-lg border border-bambu-dark-tertiary bg-bambu-dark p-3 ${splitFullscreen ? 'flex flex-col shrink-0' : ''}`}
                >
                  <div className="flex items-center gap-2 text-sm text-bambu-gray mb-2">
                    <Layers className="w-4 h-4" />
                    {t('modelViewer.plates')}
                    {platesLoading && <Loader2 className="w-3 h-3 animate-spin" />}
                  </div>
                  <div className={splitFullscreen ? 'flex flex-col min-h-0 flex-1' : undefined}>
                      <div
                        ref={platesViewportRef}
                        className={splitFullscreen ? 'min-h-0 overflow-hidden pr-1 flex-1' : undefined}
                      >
                      <div
                        ref={platesGridRef}
                        className={splitFullscreen ? 'grid gap-2' : 'grid grid-cols-2 md:grid-cols-3 gap-2'}
                        style={splitFullscreen ? { gridTemplateColumns: `repeat(${plateColumns}, minmax(0, 1fr))` } : undefined}
                      >
                        <button
                          type="button"
                          onClick={() => setSelectedPlateId(null)}
                          className={`flex items-center rounded-lg border text-left transition-colors ${
                            splitFullscreen ? 'gap-1.5 p-1.5 w-full' : 'gap-2 p-2'
                          } ${
                            selectedPlateId == null
                              ? 'border-bambu-green bg-bambu-green/10'
                              : 'border-bambu-dark-tertiary bg-bambu-dark-secondary hover:border-bambu-gray'
                          }`}
                        >
                          <div className={`rounded bg-bambu-dark-tertiary flex items-center justify-center ${
                            splitFullscreen ? 'w-8 h-8' : 'w-10 h-10'
                          }`}>
                            <Layers className={`${splitFullscreen ? 'w-4 h-4' : 'w-5 h-5'} text-bambu-gray`} />
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className={`${splitFullscreen ? 'text-xs' : 'text-sm'} text-white font-medium truncate`}>{t('modelViewer.allPlates')}</p>
                            <p className={`${splitFullscreen ? 'text-[10px]' : 'text-xs'} text-bambu-gray truncate`}>
                              {t('modelViewer.plateCount', { count: plates.length })}
                            </p>
                          </div>
                          {selectedPlateId == null && (
                            <Check className={`${splitFullscreen ? 'w-3.5 h-3.5' : 'w-4 h-4'} text-bambu-green flex-shrink-0`} />
                          )}
                        </button>
                        {pagedPlates.map((plate) => (
                          <button
                            key={plate.index}
                            type="button"
                            onClick={() => setSelectedPlateId(plate.index)}
                            className={`flex items-center rounded-lg border text-left transition-colors ${
                              splitFullscreen ? 'gap-1.5 p-1.5 w-full' : 'gap-2 p-2'
                            } ${
                              selectedPlateId === plate.index
                                ? 'border-bambu-green bg-bambu-green/10'
                                : 'border-bambu-dark-tertiary bg-bambu-dark-secondary hover:border-bambu-gray'
                            }`}
                          >
                            {plate.has_thumbnail && plate.thumbnail_url ? (
                              <img
                                src={plate.thumbnail_url}
                                alt={`Plate ${plate.index}`}
                                className={`${splitFullscreen ? 'w-8 h-8' : 'w-10 h-10'} rounded object-cover bg-bambu-dark-tertiary`}
                              />
                            ) : (
                              <div className={`rounded bg-bambu-dark-tertiary flex items-center justify-center ${
                                splitFullscreen ? 'w-8 h-8' : 'w-10 h-10'
                              }`}>
                                <Layers className={`${splitFullscreen ? 'w-4 h-4' : 'w-5 h-5'} text-bambu-gray`} />
                              </div>
                            )}
                            <div className="min-w-0 flex-1">
                              <p className={`${splitFullscreen ? 'text-xs' : 'text-sm'} text-white font-medium truncate`}>
                                {plate.name || t('modelViewer.plateNumber', { number: plate.index })}
                              </p>
                              <p className={`${splitFullscreen ? 'text-[10px]' : 'text-xs'} text-bambu-gray truncate`}>
                                {t('modelViewer.objectCount', { count: plate.object_count ?? plate.objects?.length ?? 0 })}
                              </p>
                            </div>
                            {selectedPlateId === plate.index && (
                              <Check className={`${splitFullscreen ? 'w-3.5 h-3.5' : 'w-4 h-4'} text-bambu-green flex-shrink-0`} />
                            )}
                          </button>
                        ))}
                      </div>
                    </div>
                    {(selectedPlate || shouldPaginatePlates) && (
                      <div className="mt-auto pt-3 flex items-center gap-4 text-xs text-bambu-gray overflow-x-auto">
                        {selectedPlate && (
                          <div className="flex items-center gap-3 whitespace-nowrap">
                            <span>{t('modelViewer.plateNumber', { number: selectedPlate.index })}</span>
                            {selectedPlate.print_time_seconds != null && (
                              <span>{t('modelViewer.eta', { minutes: Math.round(selectedPlate.print_time_seconds / 60) })}</span>
                            )}
                            {selectedPlate.filament_used_grams != null && (
                              <span>{selectedPlate.filament_used_grams.toFixed(1)} g</span>
                            )}
                            {selectedPlate.filaments.length > 0 && (
                              <span>{t('modelViewer.filamentCount', { count: selectedPlate.filaments.length })}</span>
                            )}
                          </div>
                        )}
                        {shouldPaginatePlates && (
                          <div className={`flex items-center gap-2 whitespace-nowrap ${selectedPlate ? 'ml-auto' : ''}`}>
                            <span>{t('modelViewer.pagination.pageOf', { current: platePage + 1, total: totalPlatePages })}</span>
                            <div className="flex items-center gap-1">
                              <button
                                type="button"
                                onClick={() => setPlatePage((prev) => Math.max(prev - 1, 0))}
                                disabled={platePage === 0}
                                className={`px-2 py-1 rounded border text-xs ${
                                  platePage === 0
                                    ? 'border-bambu-dark-tertiary text-bambu-gray/40 cursor-not-allowed'
                                    : 'border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray'
                                }`}
                              >
                                {t('modelViewer.pagination.prev')}
                              </button>
                              {(() => {
                                const maxVisible = 5;
                                let start = Math.max(0, platePage - Math.floor(maxVisible / 2));
                                const end = Math.min(totalPlatePages, start + maxVisible);
                                if (end - start < maxVisible) {
                                  start = Math.max(0, end - maxVisible);
                                }
                                const pages = Array.from({ length: end - start }, (_, i) => start + i);

                                return (
                                  <>
                                    {start > 0 && (
                                      <button
                                        type="button"
                                        onClick={() => setPlatePage(0)}
                                        className={`px-2 py-1 rounded border text-xs ${
                                          platePage === 0
                                            ? 'border-bambu-green text-bambu-green'
                                            : 'border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray'
                                        }`}
                                      >
                                        1
                                      </button>
                                    )}
                                    {start > 1 && <span className="px-1">…</span>}
                                    {pages.map((pageNumber) => (
                                      <button
                                        key={pageNumber}
                                        type="button"
                                        onClick={() => setPlatePage(pageNumber)}
                                        className={`px-2 py-1 rounded border text-xs ${
                                          platePage === pageNumber
                                            ? 'border-bambu-green text-bambu-green'
                                            : 'border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray'
                                        }`}
                                      >
                                        {pageNumber + 1}
                                      </button>
                                    ))}
                                    {end < totalPlatePages - 1 && <span className="px-1">…</span>}
                                    {end < totalPlatePages && (
                                      <button
                                        type="button"
                                        onClick={() => setPlatePage(totalPlatePages - 1)}
                                        className={`px-2 py-1 rounded border text-xs ${
                                          platePage === totalPlatePages - 1
                                            ? 'border-bambu-green text-bambu-green'
                                            : 'border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray'
                                        }`}
                                      >
                                        {totalPlatePages}
                                      </button>
                                    )}
                                  </>
                                );
                              })()}
                              <button
                                type="button"
                                onClick={() => setPlatePage((prev) => Math.min(prev + 1, totalPlatePages - 1))}
                                disabled={platePage >= totalPlatePages - 1}
                                className={`px-2 py-1 rounded border text-xs ${
                                  platePage >= totalPlatePages - 1
                                    ? 'border-bambu-dark-tertiary text-bambu-gray/40 cursor-not-allowed'
                                    : 'border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray'
                                }`}
                              >
                                {t('modelViewer.pagination.next')}
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}
              {splitFullscreen && (
                <div
                  role="separator"
                  aria-orientation="horizontal"
                  onMouseDown={(event) => {
                    event.preventDefault();
                    setIsDraggingDivider(true);
                    setHasCustomSplit(true);
                  }}
                  className={`h-2 cursor-row-resize flex items-center justify-center ${
                    isDraggingDivider ? 'bg-bambu-dark-tertiary' : 'bg-bambu-dark-secondary/60 hover:bg-bambu-dark-tertiary'
                  }`}
                >
                  <div className="w-12 h-1 rounded-full bg-bambu-gray/50" />
                </div>
              )}
              <div className={`flex-1 ${splitFullscreen ? 'min-h-0' : ''}`}>
                  <ModelViewer
                    url={isLibrary
                      ? api.getLibraryFileDownloadUrl(libraryFileId!)
                      : (capabilities.has_source
                        ? api.getSource3mfDownloadUrl(archiveId!)
                        : api.getArchiveDownload(archiveId!))}
                    fileType={fileType}
                    buildVolume={capabilities.build_volume}
                    filamentColors={capabilities.filament_colors}
                    selectedPlateId={selectedPlateId}
                    className="w-full h-full"
                  />
              </div>
            </div>
          ) : activeTab === 'gcode' && capabilities ? (
            <GcodeViewer
              gcodeUrl={isLibrary ? api.getLibraryFileGcodeUrl(libraryFileId!) : api.getArchiveGcode(archiveId!)}
              filamentColors={capabilities.filament_colors}
              className="w-full h-full"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-bambu-gray">
              {t('modelViewer.noPreview')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
