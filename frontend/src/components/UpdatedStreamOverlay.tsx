import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type Ref, type SyntheticEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, Layers, Printer, Timer } from 'lucide-react';
import './UpdatedStreamOverlay.css';
import { overlayProgressTextStyle } from '../utils/overlayBranding';
import { useSidewaysBox } from '../hooks/useSidewaysBox';

import type { OverlayLayout } from '../utils/overlayLayout';

interface UpdatedStreamOverlayProps {
  layout?: OverlayLayout;
  backgroundTransparency?: number;
  customLogo?: ReactNode;
  progressBackground?: string;
  size: 'small' | 'medium' | 'large';
  // url is undefined while the stream is closed for a renewal (useOverlayCameraRecovery).
  camera: {
    url: string | undefined;
    rotation: number;
    onError: (event: SyntheticEvent<HTMLImageElement>) => void;
    imgRef?: Ref<HTMLImageElement>;
  } | null;
  name: string | null;
  model: string | null;
  filename: string | null;
  status: string | null;
  state: string | null;
  progress: number | null;
  layers: string | null;
  remaining: string | null;
  eta: string | null;
  temperatures: { key: string; icon: ReactNode; label: string; current: number; target: number | null }[];
}

// Explicit sources keep their orientation; legacy URLs follow the viewport.
function useOverlayOrientation(layout?: OverlayLayout): OverlayLayout {
  const [portrait, setPortrait] = useState(() => window.matchMedia('(orientation: portrait)').matches);
  useEffect(() => {
    if (layout) return;
    const media = window.matchMedia('(orientation: portrait)');
    const update = (event: MediaQueryListEvent) => setPortrait(event.matches);
    setPortrait(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [layout]);
  return layout ?? (portrait ? 'portrait' : 'landscape');
}

export function UpdatedStreamOverlay(props: UpdatedStreamOverlayProps) {
  const { t } = useTranslation();
  const backgroundAlpha = 1 - (props.backgroundTransparency ?? 0) / 100;
  const backgroundStyle: CSSProperties & {
    '--overlay-background-alpha': number;
    '--overlay-identity-alpha': number;
    '--overlay-panel-alpha': number;
  } = {
    '--overlay-background-alpha': backgroundAlpha,
    '--overlay-identity-alpha': backgroundAlpha * 0.9,
    '--overlay-panel-alpha': backgroundAlpha * 0.88,
  };
  useLayoutEffect(() => {
    if (backgroundAlpha === 1) return;
    // The app's body background would otherwise fill every transparent pixel
    // in OBS. Restore it when leaving the overlay or resetting transparency.
    const previous = document.body.style.backgroundColor;
    document.body.style.backgroundColor = 'transparent';
    return () => { document.body.style.backgroundColor = previous; };
  }, [backgroundAlpha]);
  const layout = useOverlayOrientation(props.layout);
  const { camera, name, model, filename, status, state, progress, layers, remaining, eta, temperatures } =
    props;
  const progressTextStyle = overlayProgressTextStyle(props.progressBackground);
  const hasPanel =
    filename || status || progress != null || layers || remaining || eta || temperatures.length > 0;
  const rotation = camera?.rotation ?? 0;
  const sideways = Math.abs(rotation % 180) === 90;
  const cameraRef = useRef<HTMLDivElement>(null);
  const cameraBox = useSidewaysBox(cameraRef, sideways && camera != null);
  const stats = [
    { key: 'layers', icon: <Layers />, label: t('streamOverlay.layer'), value: layers },
    { key: 'remaining', icon: <Timer />, label: t('streamOverlay.remaining'), value: remaining },
    { key: 'eta', icon: <Clock />, label: t('streamOverlay.eta'), value: eta },
  ].filter((stat) => stat.value != null);

  return (
    <div className="updated-overlay" data-size={props.size} data-layout={layout} data-state={state} style={backgroundStyle}>
      <header className="updated-overlay__header">
        {(name || model) && (
          <div className="updated-overlay__identity">
            <Printer aria-hidden="true" />
            <div>
              {name && <strong>{name}</strong>}
              {model && <span>{model}</span>}
            </div>
          </div>
        )}
        <div className="updated-overlay__logo flex flex-col items-end">
          {props.customLogo}
          <a
            href="https://github.com/maziggy/bambuddy"
            target="_blank"
            rel="noopener noreferrer"
          >
            <img src="/img/bambuddy_logo_powered_by.png" alt="Bambuddy" />
          </a>
        </div>
      </header>
      {camera && (
        <div className="updated-overlay__camera" ref={cameraRef}>
          <img
            ref={camera.imgRef}
            src={camera.url}
            alt={t('streamOverlay.cameraStream')}
            style={{
              transform: `translate(-50%, -50%) rotate(${rotation}deg)`,
              // A quarter turn swaps the image's axes, so give it the slot's
              // height as width and vice versa; cover then still fills the slot.
              ...(sideways && cameraBox ? { width: cameraBox.height, height: cameraBox.width } : {}),
            }}
            onError={camera.onError}
          />
        </div>
      )}
      {hasPanel && (
        <section className="updated-overlay__panel">
          {(filename || status) && (
            <div className="updated-overlay__summary">
              {filename && <h1>{filename}</h1>}
              {status && (
                <div className="updated-overlay__status">
                  <span aria-hidden="true" />
                  {status}
                </div>
              )}
            </div>
          )}
          {progress != null && (
            <div className="updated-overlay__progress">
              <span style={progressTextStyle}>{t('streamOverlay.progress')}</span>
              <div
                role="progressbar"
                aria-label={t('streamOverlay.progress')}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={progress}
              >
                <div style={{ width: `${progress}%`, background: props.progressBackground }} />
              </div>
              <strong style={progressTextStyle}>{Math.round(progress)}%</strong>
            </div>
          )}
          {(stats.length > 0 || temperatures.length > 0) && (
            <div className="updated-overlay__readings">
              {stats.length > 0 && (
                <div className="updated-overlay__stats">
                  {stats.map((stat) => (
                    <div className="updated-overlay__reading" key={stat.key}>
                      {stat.icon}
                      <div>
                        <span>{stat.label}</span>
                        <strong>{stat.value}</strong>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {temperatures.length > 0 && (
                <div className="updated-overlay__temperatures">
                  {temperatures.map((reading) => (
                    <div className="updated-overlay__reading" key={reading.key}>
                      {reading.icon}
                      <div>
                        <span>{reading.label}</span>
                        <strong>
                          {Math.round(reading.current)}°C
                          {reading.target != null &&
                            reading.target > 0 &&
                            Math.round(reading.target) !== Math.round(reading.current) && (
                              <small> / {Math.round(reading.target)}°C</small>
                            )}
                        </strong>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
