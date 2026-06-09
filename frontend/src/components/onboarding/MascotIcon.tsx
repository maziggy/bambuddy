/**
 * BB mascot poses, sliced from the character sheet at
 * screenshots/bb_bambuddy.webp (repo-private reference; not shipped in the
 * frontend bundle — only the per-pose crops are served). Mapped to tour
 * moments per Appendix E of docs/onboarding-tour-plan.md:
 *
 *   hero    — generic / default
 *   started — Phase 0.1 welcome (and the sidebar relauncher)
 *   walk    — Phase 0.2 about and the early "let me show you" steps
 *   almost  — load-bearing setup steps (Add Printer, Verify Connection)
 *   allset  — outro / tour-complete state
 *   help    — informational / need-help steps
 */
export type MascotPose = 'hero' | 'started' | 'walk' | 'almost' | 'allset' | 'help';

interface MascotIconProps {
  pose?: MascotPose;
  /** CSS class names. Use to control width/height (default w-12 h-12). */
  className?: string;
}

const POSE_SRC: Record<MascotPose, string> = {
  hero: '/img/bb_hero.webp',
  started: '/img/bb_started.webp',
  walk: '/img/bb_walk.webp',
  almost: '/img/bb_almost.webp',
  allset: '/img/bb_allset.webp',
  help: '/img/bb_help.webp',
};

export function MascotIcon({ pose = 'hero', className = 'w-12 h-12' }: MascotIconProps) {
  return (
    <img
      src={POSE_SRC[pose]}
      alt=""
      role="presentation"
      className={`${className} object-contain`}
      draggable={false}
    />
  );
}
