import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent } from '../Card';
import { Button } from '../Button';
import { api } from '../../api/client';
import { useAuth } from '../../contexts/AuthContext';
import { useOnboarding } from '../../contexts/OnboardingContext';
import { TourSpotlight } from './TourSpotlight';
import { MascotIcon } from './MascotIcon';
import { TOUR_STEPS, statusForStep, stepIndexFromStatus } from './tourSteps';
import type { TourStepContext } from './tourSteps';

const ANCHOR_POLL_MS = 100;
const ANCHOR_TIMEOUT_MS = 3000;
const MODAL_MARGIN = 16;
const MODAL_WIDTH = 360;

interface ModalPosition {
  top: number;
  left: number;
}

/**
 * Decide where to place the step modal relative to the anchor. Prefers below
 * the anchor; if there isn't enough room, flips above; for sidebar anchors
 * (anchor on the far left), flips to the right.
 */
function computeModalPosition(anchorRect: DOMRect | null): ModalPosition {
  const viewportH = window.innerHeight;
  const viewportW = window.innerWidth;
  // No anchor → centre the modal
  if (!anchorRect) {
    return {
      top: Math.max(MODAL_MARGIN, viewportH / 2 - 160),
      left: Math.max(MODAL_MARGIN, viewportW / 2 - MODAL_WIDTH / 2),
    };
  }

  // Sidebar anchor heuristic — the live sidebar lives in the leftmost ~260px.
  // Put the modal to the right of the anchor in that case.
  if (anchorRect.right < 280) {
    return {
      top: Math.min(
        Math.max(MODAL_MARGIN, anchorRect.top),
        viewportH - 320,
      ),
      left: anchorRect.right + MODAL_MARGIN,
    };
  }

  const roomBelow = viewportH - anchorRect.bottom;
  const placeBelow = roomBelow > 280;
  const top = placeBelow
    ? anchorRect.bottom + MODAL_MARGIN
    : Math.max(MODAL_MARGIN, anchorRect.top - 280);
  const left = Math.min(
    Math.max(MODAL_MARGIN, anchorRect.left + anchorRect.width / 2 - MODAL_WIDTH / 2),
    viewportW - MODAL_WIDTH - MODAL_MARGIN,
  );
  return { top, left };
}

/**
 * The step-by-step tour overlay. Activated when `OnboardingContext.status`
 * starts with `tour_in_progress:`. Walks the user through the steps defined
 * in tourSteps.ts, navigating between routes as needed and persisting the
 * current step back to the backend after each Back/Next.
 *
 * The anchor lookup retries for up to ANCHOR_TIMEOUT_MS — pages need a beat
 * to render after navigation. If the anchor still doesn't exist after the
 * timeout, the step renders without a spotlight (modal centred) so the user
 * is never stuck.
 */
export function TourEngine() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { status, setStatus } = useOnboarding();
  const { authEnabled, hasPermission } = useAuth();
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
    staleTime: 30_000,
  });
  const [anchorEl, setAnchorEl] = useState<Element | null>(null);
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null);
  const stepIndex = useMemo(() => stepIndexFromStatus(status), [status]);
  const step = stepIndex >= 0 ? TOUR_STEPS[stepIndex] : null;
  const stepStartedAtRef = useRef<number>(0);

  const skipContext = useMemo<TourStepContext>(
    () => ({
      authEnabled,
      printerCount: printers?.length ?? 0,
      // Cast through any: useAuth.hasPermission is typed as
      // (p: Permission) => boolean; the tour calls it with raw strings since
      // it doesn't import the Permission enum. The runtime check is the same.
      hasPermission: (perm: string) => hasPermission(perm as unknown as never),
    }),
    [authEnabled, printers, hasPermission],
  );

  // Auto-advance past any step whose `skipIf` evaluates true under the
  // current app state — eg. "Lock the front door" is silly when auth is
  // already on, and "Add your first printer" is silly when one exists. If
  // we land on the last step and it skips, we mark the tour completed so
  // we don't loop. Guarded against re-entrancy by gating on `step` itself.
  useEffect(() => {
    if (!step) return;
    if (!step.skipIf?.(skipContext)) return;
    if (stepIndex >= TOUR_STEPS.length - 1) {
      setStatus('completed_tour');
    } else {
      setStatus(statusForStep(stepIndex + 1));
    }
  }, [step, stepIndex, skipContext, setStatus]);

  // Navigate to the step's route before we try to find its anchor. We compare
  // pathname + search separately because the existing app uses `?tab=users`
  // and similar query-string deep-links; navigate() updates both.
  useEffect(() => {
    if (!step) return;
    if (!step.route) return;
    const [pathname, search = ''] = step.route.split('?');
    const currentSearch = location.search.replace(/^\?/, '');
    if (location.pathname !== pathname || currentSearch !== search) {
      navigate(step.route);
    }
  }, [step, location.pathname, location.search, navigate]);

  // Anchor lookup — poll until the element exists or we give up.
  useEffect(() => {
    if (!step) {
      setAnchorEl(null);
      setAnchorRect(null);
      return;
    }
    if (!step.anchor) {
      // Outro-style step — no anchor, centre the modal.
      setAnchorEl(null);
      setAnchorRect(null);
      return;
    }
    stepStartedAtRef.current = Date.now();
    let cancelled = false;

    const tryFind = () => {
      if (cancelled) return;
      const el = document.querySelector(step.anchor!);
      if (el) {
        setAnchorEl(el);
        setAnchorRect(el.getBoundingClientRect());
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
      }
      // The use of Date.now to bound polling is deliberate — without a hard
      // cap we'd spin forever if the anchor selector is wrong or the page
      // doesn't render the element under the current state.
      if (Date.now() - stepStartedAtRef.current < ANCHOR_TIMEOUT_MS) {
        setTimeout(tryFind, ANCHOR_POLL_MS);
      } else {
        setAnchorEl(null);
        setAnchorRect(null);
      }
    };
    tryFind();

    return () => {
      cancelled = true;
    };
  }, [step, location.pathname, location.search]);

  // Recompute anchor rect on resize/scroll so the modal follows the anchor.
  useEffect(() => {
    if (!anchorEl) return;
    const update = () => setAnchorRect(anchorEl.getBoundingClientRect());
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [anchorEl]);

  const handleBack = useCallback(() => {
    if (stepIndex <= 0) return;
    setStatus(statusForStep(stepIndex - 1));
  }, [stepIndex, setStatus]);

  const handleNext = useCallback(() => {
    if (stepIndex < 0) return;
    if (stepIndex >= TOUR_STEPS.length - 1) {
      setStatus('completed_tour');
      return;
    }
    setStatus(statusForStep(stepIndex + 1));
  }, [stepIndex, setStatus]);

  const handleSkip = useCallback(() => {
    setStatus('dismissed');
  }, [setStatus]);

  // Escape key skips the tour.
  useEffect(() => {
    if (!step) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleSkip();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [step, handleSkip]);

  if (!step) return null;
  // Hide the modal entirely while the skip-effect catches up — without this
  // gate the user briefly sees the skipped step's content before the effect
  // setStatuses past it.
  if (step.skipIf?.(skipContext)) return null;

  const modalPos = computeModalPosition(anchorRect);
  const isLastStep = stepIndex === TOUR_STEPS.length - 1;
  const isFirstStep = stepIndex === 0;
  // Step counter shows visible position, not raw index — when add-printer
  // auto-skips because the user already has printers, the next step is the
  // user's first visible one and should render as "1 / N", not "2 / N".
  const visibleTotal = TOUR_STEPS.filter((s) => !s.skipIf?.(skipContext)).length;
  const visiblePosition = TOUR_STEPS.slice(0, stepIndex + 1).filter(
    (s) => !s.skipIf?.(skipContext),
  ).length;

  return (
    <>
      <TourSpotlight anchor={anchorEl} />
      <Card
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-step-title"
        className="fixed z-[110] shadow-2xl"
        style={{
          top: modalPos.top,
          left: modalPos.left,
          width: MODAL_WIDTH,
        }}
      >
        <CardContent className="p-5">
          <div className="flex items-center gap-3 mb-3">
            <MascotIcon pose={step.pose ?? 'hero'} className="w-12 h-12 flex-shrink-0" />
            <div className="text-xs text-bambu-gray">
              {visiblePosition} / {visibleTotal}
            </div>
          </div>
          <h3 id="tour-step-title" className="text-lg font-semibold text-white mb-2">
            {t(step.titleKey)}
          </h3>
          <p className="text-sm text-bambu-gray mb-5">{t(step.bodyKey)}</p>
          <div className="flex items-center justify-between gap-2">
            <Button variant="ghost" onClick={handleSkip} className="text-xs">
              {t('onboarding.button.skipTour')}
            </Button>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                onClick={handleBack}
                disabled={isFirstStep}
              >
                {t('onboarding.button.back')}
              </Button>
              <Button onClick={handleNext}>
                {isLastStep ? t('onboarding.button.done') : t('onboarding.button.next')}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </>
  );
}
