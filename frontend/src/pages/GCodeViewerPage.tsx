import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { api } from '../api/client';
import { GcodeToolpathViewer } from '../components/GcodeToolpathViewer';

/**
 * Full-page G-code preview.
 *
 * Previously an iframe onto a vendored copy of PrettyGCode served from
 * `/gcode-viewer/`. That brought its own problems -- a second viewer to keep
 * packaged and updated, no way to theme or translate it, and a whole
 * frame-refusal probe to detect when a proxy blocked the embed -- and its
 * output was the thing this page exists to show.
 *
 * It now renders Bambuddy's own toolpath viewer, which draws with OrcaSlicer's
 * `libvgcode` and colours by feature. Same component as the file-manager
 * preview, so the two surfaces cannot drift apart.
 */
export function GCodeViewerPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { t } = useTranslation();

  const archiveId = searchParams.get('archive');
  const libraryFileId = searchParams.get('library_file');
  const plate = searchParams.get('plate');

  // Filament colours, so a multi-material print opens on its own colours.
  // The two sources differ: an archive reports them through its capabilities,
  // while a library file carries them in its plate metadata, read straight out
  // of the 3MF's slice info. Neither is worth blocking the preview over -- the
  // viewer falls back to feature colouring -- hence no retry and no error path.
  const archiveColorsQuery = useQuery({
    queryKey: ['gcode-viewer-archive-colors', archiveId],
    queryFn: () => api.getArchiveCapabilities(Number(archiveId)),
    enabled: Boolean(archiveId),
    staleTime: 5 * 60_000,
    retry: false,
  });

  const libraryPlatesQuery = useQuery({
    queryKey: ['gcode-viewer-library-colors', libraryFileId],
    queryFn: () => api.getLibraryFilePlates(Number(libraryFileId)),
    enabled: Boolean(libraryFileId),
    staleTime: 5 * 60_000,
    retry: false,
  });

  const archivePlatesQuery = useQuery({
    queryKey: ['gcode-viewer-archive-plates', archiveId],
    queryFn: () => api.getArchivePlates(Number(archiveId)),
    enabled: Boolean(archiveId),
    staleTime: 5 * 60_000,
    retry: false,
  });

  const plates = useMemo(
    () => (archiveId ? archivePlatesQuery.data?.plates : libraryPlatesQuery.data?.plates) ?? [],
    [archiveId, archivePlatesQuery.data, libraryPlatesQuery.data],
  );

  // Which plate the viewer is showing. Without a `plate` in the URL the backend
  // serves the lowest-numbered one, so that is what the switcher has to mark as
  // current — the URL stays clean until the user picks something else.
  const activePlate = useMemo(() => {
    if (plate) return Number(plate);
    if (plates.length === 0) return null;
    return Math.min(...plates.map((p) => p.index));
  }, [plate, plates]);

  const selectPlate = (index: number) => {
    // The G-code URL is derived from this parameter, so writing the one already
    // being shown would refetch the whole toolpath for no change.
    if (index === activePlate) return;
    const next = new URLSearchParams(searchParams);
    next.set('plate', String(index));
    setSearchParams(next, { replace: true });
  };

  const filamentColors = useMemo<string[] | undefined>(() => {
    if (archiveId) return archiveColorsQuery.data?.filament_colors;

    // Colours are per plate; use the one being previewed.
    const source = plates.find((p) => p.index === activePlate) || plates[0];
    if (!source?.filaments?.length) return undefined;

    // slot_id is 1-based and the G-code's tool numbers are 0-based, so index
    // by slot - 1 or every colour lands one filament out.
    const colors: string[] = [];
    for (const filament of source.filaments) {
      const slot = Math.max(0, (filament.slot_id ?? 1) - 1);
      if (filament.color) colors[slot] = filament.color;
    }
    return colors.length > 0 ? colors : undefined;
  }, [archiveId, archiveColorsQuery.data, plates, activePlate]);

  const gcodeUrl = useMemo(() => {
    // Multi-plate sources need the plate carried through, or the viewer shows
    // whichever plate the backend defaults to rather than the one picked.
    const withPlate = (base: string) => (plate ? `${base}?plate=${encodeURIComponent(plate)}` : base);
    if (archiveId) return withPlate(api.getArchiveGcode(Number(archiveId)));
    if (libraryFileId) return withPlate(api.getLibraryFileGcodeUrl(Number(libraryFileId)));
    return null;
  }, [archiveId, libraryFileId, plate]);

  const handleBack = () => {
    if (window.history.length > 1) navigate(-1);
    else navigate(archiveId ? '/archives' : '/files');
  };

  const backLabel = archiveId
    ? t('gcodeViewer.backToArchives', 'Back to Archives')
    : t('gcodeViewer.backToFiles', 'Back to File Manager');

  return (
    <div className="flex flex-col h-full">
      <div className="flex-shrink-0 px-4 py-2 border-b border-bambu-dark-tertiary flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="button"
          onClick={handleBack}
          className="inline-flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          {backLabel}
        </button>

        {/* A sliced multi-plate 3MF holds one toolpath per plate, and only one
            of them can be on screen. Without this the other plates were
            unreachable: nothing that opens this page from the File Manager
            passes a plate, so it showed whichever one the backend picked. */}
        {plates.length > 1 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-bambu-gray">{t('gcodeViewer.plates', 'Plates')}</span>
            {plates.map((p) => (
              <button
                key={p.index}
                type="button"
                onClick={() => selectPlate(p.index)}
                aria-pressed={p.index === activePlate}
                title={p.name ?? undefined}
                className={`px-2 py-0.5 rounded text-xs transition-colors ${
                  p.index === activePlate
                    ? 'bg-bambu-green text-white'
                    : 'bg-bambu-dark-tertiary text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white'
                }`}
              >
                {t('gcodeViewer.plateN', 'Plate {{n}}', { n: p.index })}
              </button>
            ))}
          </div>
        )}
      </div>

      {gcodeUrl ? (
        <GcodeToolpathViewer
          gcodeUrl={gcodeUrl}
          filamentColors={filamentColors}
          className="flex-1 min-h-0"
        />
      ) : (
        <div className="flex-1 flex items-center justify-center text-sm text-bambu-gray">
          {t('gcodeViewer.noSource', 'No file was given to preview.')}
        </div>
      )}
    </div>
  );
}
