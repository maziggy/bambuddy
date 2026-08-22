/**
 * G-code preview drawn the way the desktop slicer draws it.
 *
 * The renderer under this is OrcaSlicer's own `libvgcode`, vendored via
 * `three-slicer` (see `src/lib/vendor/toolpathRenderer.js`): each extrusion is a
 * diamond-section prism instanced once per segment, so a whole print is a
 * single indexed draw call and the toolpath occludes itself. The previous
 * viewer drew screen-space lines, which have no thickness in the scene and
 * therefore cannot hide the layer behind them -- the reason a sliced model
 * came out stringy and shimmering.
 *
 * The other half of the difference is colour. This colours by *feature* --
 * wall, infill, support, bridge -- from the `;TYPE:` annotations the slicer
 * writes, which is what makes a preview readable. Colouring by filament, as
 * the old viewer did, paints AMS slot colours across the whole print and tells
 * you nothing about what the printer is doing.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { Loader2, FileWarning } from 'lucide-react';

import { getAuthToken } from '../api/client';
import {
  parseGcodeToolpath,
  layersByFilament,
  filterLayersByType,
  ToolpathType,
  type ParsedToolpath,
} from '../lib/gcodeToolpath';
// Typed by the sibling toolpathRenderer.d.ts.
import {
  buildSegmentData,
  makeToolpath,
  computeColors,
  TYPE_COLOR,
  DEFAULT_RANGES_COLORS,
} from '../lib/vendor/toolpathRenderer.js';

interface GcodeToolpathViewerProps {
  gcodeUrl: string;
  buildVolume?: { x: number; y: number; z: number };
  /**
   * AMS slot colours, in tool order. When supplied the viewer opens on a
   * filament-coloured view, which is what a multi-material print is usually
   * being looked at for -- feature colouring answers a different question.
   */
  filamentColors?: string[];
  className?: string;
}

/**
 * The colour modes worth offering.
 *
 * Upstream also exposes speed, fan and temperature, but its own implementation
 * derives those from *settings* rather than the toolpath, because its slicing
 * kernel doesn't expose them per segment. Reading them out of the G-code would
 * give real values -- `F`, `M106` and `M104` are right there in the file -- so
 * they are left out until the parser carries them, rather than shipped as
 * plausible-looking guesses.
 */
const VIEW_MODES = ['filament', 'feature', 'height', 'width'] as const;
type ViewMode = (typeof VIEW_MODES)[number];

/** Feature rows for the legend, in the order the slicer lists them. */
const LEGEND_ENTRIES: Array<{ type: number; key: string; fallback: string }> = [
  { type: ToolpathType.wall, key: 'gcodeViewer.feature.wall', fallback: 'Walls' },
  { type: ToolpathType.sparseInfill, key: 'gcodeViewer.feature.sparseInfill', fallback: 'Sparse infill' },
  { type: ToolpathType.solidInfill, key: 'gcodeViewer.feature.solidInfill', fallback: 'Solid infill' },
  { type: ToolpathType.bridge, key: 'gcodeViewer.feature.bridge', fallback: 'Bridge / overhang' },
  { type: ToolpathType.support, key: 'gcodeViewer.feature.support', fallback: 'Support' },
  { type: ToolpathType.skirt, key: 'gcodeViewer.feature.skirt', fallback: 'Skirt / brim' },
  { type: ToolpathType.gapFill, key: 'gcodeViewer.feature.gapFill', fallback: 'Gap fill' },
  { type: ToolpathType.ironing, key: 'gcodeViewer.feature.ironing', fallback: 'Ironing' },
  { type: ToolpathType.primeTower, key: 'gcodeViewer.feature.primeTower', fallback: 'Prime tower' },
];

/**
 * Pack a CSS hex colour the way the renderer expects.
 *
 * It stores colour as a single float holding `r << 16 | g << 8 | b`, which its
 * shader unpacks. Matching that exactly is what lets filament colours be
 * applied through the same `setColors` path the built-in views use.
 */
function packColor(hex: string): number {
  const value = hex.replace('#', '');
  const full = value.length === 3 ? value.split('').map((c) => c + c).join('') : value;
  const n = Number.parseInt(full.slice(0, 6), 16);
  return Number.isFinite(n) ? n : 0x00ae42;
}

const cssColor = (rgb: number[] | undefined): string =>
  rgb ? `rgb(${rgb.map((c) => Math.round(c * 255)).join(',')})` : 'transparent';

/** The renderer's own blue-to-red ramp, as CSS gradient stops. */
function rampStops(): string {
  const colors = DEFAULT_RANGES_COLORS as number[][];
  return colors
    .map((rgb, i) => `${cssColor(rgb)} ${((i / (colors.length - 1)) * 100).toFixed(0)}%`)
    .join(', ');
}

/** Two decimals for a layer height, none for a large speed-like value. */
function formatScale(value: number): string {
  if (!Number.isFinite(value)) return '-';
  return Math.abs(value) < 10 ? value.toFixed(2) : value.toFixed(0);
}

export function GcodeToolpathViewer({
  gcodeUrl,
  buildVolume = { x: 256, y: 256, z: 256 },
  filamentColors,
  className = '',
}: GcodeToolpathViewerProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notSliced, setNotSliced] = useState(false);
  const [parsed, setParsed] = useState<ParsedToolpath | null>(null);

  const hasFilamentColors = (filamentColors?.length ?? 0) > 0;
  const [viewMode, setViewMode] = useState<ViewMode>(hasFilamentColors ? 'filament' : 'feature');
  // Colours are fetched, so they usually arrive after the first render and the
  // initial state above lands on 'feature'. Adopt filament when they turn up,
  // unless the user has already picked a mode for themselves.
  const modeChosenRef = useRef(false);
  useEffect(() => {
    if (hasFilamentColors && !modeChosenRef.current) setViewMode('filament');
  }, [hasFilamentColors]);
  // Filament and feature colouring merge vertices differently, so they cannot
  // share one built mesh; the toolpath is rebuilt when crossing between them.
  const [layerRange, setLayerRange] = useState<[number, number]>([0, 0]);
  // Hidden types, tracked separately per colour space: in filament view a
  // "type" is a filament slot, in every other view it is a feature.
  const [hiddenFeatures, setHiddenFeatures] = useState<ReadonlySet<number>>(new Set());
  const [hiddenFilaments, setHiddenFilaments] = useState<ReadonlySet<number>>(new Set());

  const filamentView = viewMode === 'filament';
  const hidden = filamentView ? hiddenFilaments : hiddenFeatures;
  // A stable key so the toolpath effect re-runs on a change of contents rather
  // than on every new Set identity.
  const hiddenKey = [...hidden].sort((a, b) => a - b).join(',');

  const toggleHidden = (type: number) => {
    const update = (prev: ReadonlySet<number>) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    };
    if (filamentView) setHiddenFilaments(update);
    else setHiddenFeatures(update);
  };
  const [showTravel, setShowTravel] = useState(false);

  // Kept out of state: these are three.js objects, and re-rendering React on
  // every camera nudge would be pointless work.
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const handleRef = useRef<ReturnType<typeof makeToolpath> | null>(null);
  const segmentDataRef = useRef<ReturnType<typeof buildSegmentData> | null>(null);
  // Bumped when a new toolpath is built, so the colour effect re-runs against
  // it -- the data it colours lives in a ref rather than in state.
  const [toolpathGeneration, setToolpathGeneration] = useState(0);
  // Read inside the toolpath effect without making it a dependency: a rebuild
  // should honour the current controls, not reset them, and re-running on
  // every slider nudge would rebuild the whole mesh.
  const showTravelRef = useRef(showTravel);
  const layerRangeRef = useRef(layerRange);
  showTravelRef.current = showTravel;
  layerRangeRef.current = layerRange;
  // The camera is framed once. Re-framing on a colour-mode switch would yank
  // the view back from wherever the user had put it.
  const framedRef = useRef(false);

  // `buildVolume` defaults to an object literal, so without this every render
  // produced a new identity. That identity was a dependency of the scene
  // effect, which therefore tore down and rebuilt the WebGL renderer on every
  // render -- and browsers cap live WebGL contexts at around sixteen, dropping
  // the oldest, which is why the canvas went blank after a few interactions.
  const volumeKey = `${buildVolume.x}x${buildVolume.y}x${buildVolume.z}`;
  const volume = useMemo(
    () => ({ x: buildVolume.x, y: buildVolume.y, z: buildVolume.z }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [volumeKey],
  );

  // --- Fetch and parse -----------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNotSliced(false);
    setParsed(null);

    const headers: HeadersInit = {};
    const token = getAuthToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    fetch(gcodeUrl, { headers })
      .then(async (response) => {
        if (!response.ok) {
          if (response.status === 404) {
            const data = await response.json().catch(() => ({}));
            if (typeof data.detail === 'string' && data.detail.includes('sliced')) {
              setNotSliced(true);
              throw new Error('not_sliced');
            }
          }
          throw new Error('Failed to load G-code');
        }
        return response.text();
      })
      .then((gcode) => {
        if (cancelled) return;
        framedRef.current = false;
        const result = parseGcodeToolpath(gcode);
        setParsed(result);
        setLayerRange([0, Math.max(0, result.layers.length - 1)]);
        setLoading(false);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        if (err.message !== 'not_sliced') setError(err.message);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [gcodeUrl]);

  // --- Scene: created once, never rebuilt ----------------------------------
  // Deliberately independent of the toolpath. Tearing the renderer down to
  // recolour would leak WebGL contexts and throw away the camera the user had
  // positioned.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const width = container.clientWidth || 1;
    const height = container.clientHeight || 1;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a1a1a);
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 10000);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    // Take the canvas out of flow before it is ever in the document (#2887).
    // `setSize` writes the size onto the canvas as inline width/height, and the
    // canvas lives inside the very element we measure and observe — so on a page
    // where that element's height comes from its content, each resize grew the
    // container, which fired the observer, which resized again. three.js leaves
    // the canvas `display: inline`, so the line box added its descender space
    // (~33px) every round and the page climbed without limit. Out of flow it
    // cannot contribute to the container's height at all; `display: block` is
    // belt and braces for the same descender, and matters if this is ever
    // rendered somewhere the absolute positioning is overridden.
    renderer.domElement.style.display = 'block';
    renderer.domElement.style.position = 'absolute';
    renderer.domElement.style.inset = '0';
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controlsRef.current = controls;

    // The bed. The toolpath shader lights itself (libvgcode carries its own
    // light directions), so the scene needs no lights at all.
    const grid = new THREE.GridHelper(
      Math.max(volume.x, volume.y),
      Math.ceil(Math.max(volume.x, volume.y) / 16),
      0x444444,
      0x333333,
    );
    // The toolpath group is rotated -90 degrees about X to take the slicer's
    // Z-up space into three's Y-up, which maps (x, y, z) to (x, z, -y) -- so
    // the bed's +Y runs along world -Z. Placing the grid at +Z left the print
    // sitting beside its own plate rather than on it.
    grid.position.set(volume.x / 2, 0, -volume.y / 2);
    scene.add(grid);

    let frame = 0;
    const animate = () => {
      frame = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const handleResize = () => {
      const w = container.clientWidth || 1;
      const h = container.clientHeight || 1;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    const observer = new ResizeObserver(handleResize);
    observer.observe(container);
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      observer.disconnect();
      cancelAnimationFrame(frame);
      controls.dispose();
      grid.geometry.dispose();
      (grid.material as THREE.Material).dispose();
      renderer.dispose();
      container.removeChild(renderer.domElement);
      sceneRef.current = null;
      cameraRef.current = null;
      controlsRef.current = null;
    };
  }, [volume]);

  // --- Toolpath: rebuilt when the colouring changes its vertex layout ------
  useEffect(() => {
    const scene = sceneRef.current;
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!scene || !camera || !controls || !parsed || parsed.layers.length === 0) return;

    // Filament and feature colouring merge adjacent vertices differently, so
    // they genuinely produce different vertex streams and cannot share a mesh.
    const keyed = filamentView ? layersByFilament(parsed.layers) : parsed.layers;
    const sourceLayers = filterLayersByType(keyed, hidden);
    const data = buildSegmentData(sourceLayers, parsed.defaultWidth);
    const handle = makeToolpath(THREE, data);
    segmentDataRef.current = data;
    handleRef.current = handle;

    const group = new THREE.Group();
    group.rotation.x = -Math.PI / 2;
    group.add(handle.mesh);
    group.add(handle.travLines);
    scene.add(group);

    handle.setTravelVisible(showTravelRef.current);
    handle.setLayerRange(layerRangeRef.current[0], layerRangeRef.current[1]);
    setToolpathGeneration((n) => n + 1);

    // Frame only on first build, so switching colour mode does not yank the
    // camera back from wherever the user put it.
    if (!framedRef.current) {
      framedRef.current = true;
      // Not Box3.setFromObject: this renderer keeps segment positions in a
      // data texture, and the geometry attribute is only the 8-vertex diamond
      // template -- measuring the object reports a few millimetres, so the
      // camera parked itself far away and the print came out tiny.
      const b = parsed.bounds;
      const box = b
        ? new THREE.Box3(
            new THREE.Vector3(b.min[0], b.min[2], -b.max[1]),
            new THREE.Vector3(b.max[0], b.max[2], -b.min[1]),
          )
        : new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(volume.x, 1, -volume.y));
      const center = box.getCenter(new THREE.Vector3());
      const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.001);
      const vFov = THREE.MathUtils.degToRad(camera.fov);
      const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect);
      const distance = 1.15 * Math.max(radius / Math.sin(vFov / 2), radius / Math.sin(hFov / 2));
      camera.position.copy(center).addScaledVector(new THREE.Vector3(0.7, 0.5, 0.7).normalize(), distance);
      camera.near = Math.max(distance / 1000, 0.01);
      camera.far = distance + radius * 4;
      camera.updateProjectionMatrix();
      controls.target.copy(center);
      controls.update();
    }

    return () => {
      scene.remove(group);
      // The handle owns instanced buffers and a data texture per segment; on a
      // large print that is a lot of GPU memory to leave behind.
      handle.dispose();
      handleRef.current = null;
      segmentDataRef.current = null;
    };
    // hiddenKey rather than the Set, whose identity changes on every toggle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parsed, filamentView, volume, hiddenKey]);

  // --- Controls drive the existing handle rather than rebuilding it ---------
  useEffect(() => {
    handleRef.current?.setLayerRange(layerRange[0], layerRange[1]);
  }, [layerRange]);

  useEffect(() => {
    handleRef.current?.setTravelVisible(showTravel);
  }, [showTravel]);

  const colorResult = useMemo(() => {
    const data = segmentDataRef.current;
    if (!data || !parsed) return null;

    if (filamentView) {
      // In this mode each vertex's "type" is its filament index + 1, so the
      // AMS colours can be applied straight from the per-vertex metadata.
      const colors = new Float32Array(data.nV * 4);
      for (let v = 0; v < data.nV; v += 1) {
        const slot = Math.max(0, data.meta.vType[v] - 1);
        const hex = filamentColors?.[slot] ?? filamentColors?.[0] ?? '#00ae42';
        colors[v * 4] = packColor(hex);
      }
      return { color: colors, min: 0, max: 0, unit: '', cont: false };
    }

    // feature / height / width never consult the settings context, so an empty
    // one is honest here; speed / fan / temp would not be, which is why they
    // are not offered.
    return computeColors(data, viewMode, {});
    // toolpathGeneration is a dependency so a rebuilt mesh gets recoloured.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewMode, parsed, filamentView, filamentColors, toolpathGeneration]);

  useEffect(() => {
    if (colorResult) handleRef.current?.setColors(colorResult.color);
  }, [colorResult]);

  const layerCount = parsed?.layers.length ?? 0;

  if (notSliced) {
    return (
      <div className={`flex flex-col items-center justify-center gap-2 text-bambu-gray ${className}`}>
        <FileWarning className="w-8 h-8" />
        {t('gcodeViewer.notSliced', 'This file has not been sliced yet.')}
      </div>
    );
  }

  if (error) {
    return (
      <div className={`flex flex-col items-center justify-center gap-2 text-bambu-gray ${className}`}>
        <FileWarning className="w-8 h-8" />
        {t('gcodeViewer.loadFailed', 'Could not load the G-code for this file.')}
      </div>
    );
  }

  return (
    <div className={`relative ${className}`}>
      {/*
        Absolute, not `w-full h-full` (#2887). The canvas is appended here and
        this element is what the ResizeObserver watches, so its height must come
        from the pane above it and never from what it contains. `h-full` is a
        percentage, which resolves to `auto` unless every ancestor has a definite
        height — on the full-page route none does, so the height fell through to
        the content and the canvas ended up sizing the box that sizes the canvas.
        `inset-0` against the `relative` parent is a definite height whatever the
        page does, which also keeps this working if a future caller forgets to
        give the pane a height of its own.
      */}
      <div ref={containerRef} className="absolute inset-0" />

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center gap-2 bg-bambu-dark/60 text-sm text-bambu-gray">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t('gcodeViewer.loading', 'Reading toolpath...')}
        </div>
      )}

      {!loading && layerCount > 0 && (
        <>
          {/* Colour mode + travel toggle */}
          <div className="absolute left-3 top-3 flex flex-col gap-2 rounded border border-bambu-dark-tertiary bg-bambu-dark/85 p-2">
            <div className="flex overflow-hidden rounded border border-bambu-dark-tertiary">
              {VIEW_MODES.filter((mode) => mode !== 'filament' || hasFilamentColors).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => {
                    modeChosenRef.current = true;
                    setViewMode(mode);
                  }}
                  className={`px-2 py-1 text-xs transition-colors ${
                    viewMode === mode ? 'bg-bambu-green text-white' : 'text-bambu-gray hover:text-white'
                  }`}
                >
                  {t(`gcodeViewer.view.${mode}`, mode)}
                </button>
              ))}
            </div>

            <label className="flex cursor-pointer items-center gap-2 text-xs text-bambu-gray">
              <input
                type="checkbox"
                checked={showTravel}
                onChange={(e) => setShowTravel(e.target.checked)}
                className="cursor-pointer"
              />
              {t('gcodeViewer.showTravel', 'Travel moves')}
            </label>

            {filamentView ? (
              <ul className="flex flex-col gap-0.5">
                {(filamentColors ?? []).map((color, slot) => {
                  // Filament view keys types as slot + 1; see layersByFilament.
                  const type = slot + 1;
                  const isHidden = hidden.has(type);
                  return (
                    <li key={slot}>
                      <button
                        type="button"
                        onClick={() => toggleHidden(type)}
                        aria-pressed={!isHidden}
                        className={`flex w-full items-center gap-1.5 text-left text-[0.7rem] transition-opacity hover:text-white ${
                          isHidden ? 'text-bambu-gray/40' : 'text-bambu-gray'
                        }`}
                      >
                        <span
                          className={`inline-block h-2.5 w-2.5 shrink-0 rounded-sm border border-white/20 ${isHidden ? 'opacity-25' : ''}`}
                          style={{ backgroundColor: color }}
                          aria-hidden
                        />
                        <span className={isHidden ? 'line-through' : ''}>
                          {t('gcodeViewer.filamentSlot', 'Filament {{n}}', { n: slot + 1 })}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : viewMode === 'feature' ? (
              <ul className="flex flex-col gap-0.5">
                {LEGEND_ENTRIES.map((entry) => {
                  const isHidden = hidden.has(entry.type);
                  return (
                    <li key={entry.type}>
                      <button
                        type="button"
                        onClick={() => toggleHidden(entry.type)}
                        aria-pressed={!isHidden}
                        className={`flex w-full items-center gap-1.5 text-left text-[0.7rem] transition-opacity hover:text-white ${
                          isHidden ? 'text-bambu-gray/40' : 'text-bambu-gray'
                        }`}
                      >
                        <span
                          className={`inline-block h-2.5 w-2.5 shrink-0 rounded-sm ${isHidden ? 'opacity-25' : ''}`}
                          style={{ backgroundColor: cssColor(TYPE_COLOR[entry.type]) }}
                          aria-hidden
                        />
                        <span className={isHidden ? 'line-through' : ''}>{t(entry.key, entry.fallback)}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              colorResult && (
                // Continuous scale. The ramp is drawn from the renderer's own
                // stops rather than an approximation, and the ends are labelled
                // -- a bare gradient says nothing about what the colours mean.
                <div className="flex flex-col gap-1">
                  <div
                    className="h-2.5 w-full rounded-sm border border-white/10"
                    style={{ background: `linear-gradient(to right, ${rampStops()})` }}
                    aria-hidden
                  />
                  <div className="flex items-center justify-between text-[0.7rem] tabular-nums text-bambu-gray">
                    <span>{formatScale(colorResult.min)}</span>
                    <span className="text-bambu-gray/70">{colorResult.unit}</span>
                    <span>{formatScale(colorResult.max)}</span>
                  </div>
                </div>
              )
            )}
          </div>

          {/* Layer range. Two ends, because inspecting a print means isolating
              a band of layers, not just capping the top. */}
          <div className="absolute right-3 top-3 flex flex-col items-center gap-1 rounded border border-bambu-dark-tertiary bg-bambu-dark/85 p-2">
            <span className="text-[0.65rem] tabular-nums text-bambu-gray">{layerRange[1] + 1}</span>
            <input
              type="range"
              min={0}
              max={Math.max(0, layerCount - 1)}
              value={layerRange[1]}
              onChange={(e) => {
                const top = Number(e.target.value);
                setLayerRange(([bottom]) => [Math.min(bottom, top), top]);
              }}
              aria-label={t('gcodeViewer.topLayer', 'Top layer')}
              className="h-40 w-4 cursor-pointer"
              style={{ writingMode: 'vertical-lr', direction: 'rtl' }}
            />
            <input
              type="range"
              min={0}
              max={Math.max(0, layerCount - 1)}
              value={layerRange[0]}
              onChange={(e) => {
                const bottom = Number(e.target.value);
                setLayerRange(([, top]) => [bottom, Math.max(bottom, top)]);
              }}
              aria-label={t('gcodeViewer.bottomLayer', 'Bottom layer')}
              className="h-40 w-4 cursor-pointer"
              style={{ writingMode: 'vertical-lr', direction: 'rtl' }}
            />
            <span className="text-[0.65rem] tabular-nums text-bambu-gray">{layerRange[0] + 1}</span>
          </div>
        </>
      )}
    </div>
  );
}
