/**
 * The Levigate mark: ℞ drawn as one smooth stroke, the R's leg sweeping into a level base, with the mint
 * cross-stroke. Geometry is taken verbatim from the brand sheet; small sizes use the heavier favicon strokes.
 */
import { cn } from "./cn";

const R_STEM = "M21 50V14H32A9 9 0 0 1 32 32H21";
const R_LEG = "M28 32L39.5 43.5Q45 49 52 49";
const SLASH = "M32 44L40 36";
const R_STEM_SMALL = "M20 51V13H32A9.5 9.5 0 0 1 32 32H20";
const R_LEG_SMALL = "M28 32L40 44Q45 49 53 49";
const SLASH_SMALL = "M31.5 45.5L41.5 35.5";

export function LevigateMark({ size = 32, onDark = false, title }: { size?: number; onDark?: boolean; title?: string }) {
  const small = size <= 32;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <rect width="64" height="64" rx="15" fill={onDark ? "#0E6B6E" : "#0B4F57"} />
      <path
        d={small ? R_STEM_SMALL : R_STEM}
        fill="none"
        stroke="#F4F9F8"
        strokeWidth={small ? 8 : 6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d={small ? R_LEG_SMALL : R_LEG}
        fill="none"
        stroke="#F4F9F8"
        strokeWidth={small ? 8 : 6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d={small ? SLASH_SMALL : SLASH} stroke="#5EEAD4" strokeWidth={small ? 7 : 5} strokeLinecap="round" />
    </svg>
  );
}

/** App loader: the Rx draws itself, then the mint stroke lands. Full screen on first load only. */
export function LevigateLoader({ size = 88, label = "Loading your queue…" }: { size?: number; label?: string }) {
  return (
    <div className="flex flex-col items-center gap-4" role="status" aria-live="polite">
      <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
        <rect width="64" height="64" rx="15" fill="#0B4F57" />
        <path className="lv-draw" pathLength={100} d={R_STEM} fill="none" stroke="#F4F9F8" strokeWidth="6"
          strokeLinecap="round" strokeLinejoin="round" />
        <path className="lv-draw" pathLength={100} d={R_LEG} fill="none" stroke="#F4F9F8" strokeWidth="6"
          strokeLinecap="round" strokeLinejoin="round" />
        <path className="lv-slash" d={SLASH} stroke="#5EEAD4" strokeWidth="5" strokeLinecap="round" />
      </svg>
      <span className="text-sm text-muted">{label}</span>
    </div>
  );
}

export function Wordmark({ className, size = 22 }: { className?: string; size?: number }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LevigateMark size={Math.round(size * 1.5)} />
      <span className="font-display font-semibold tracking-tight text-ink" style={{ fontSize: size, letterSpacing: "-0.02em" }}>
        levigate
      </span>
    </span>
  );
}
