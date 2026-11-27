import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock3,
  FileWarning,
  Info,
  RotateCcw,
  Search,
  type LucideIcon,
} from "lucide-react";
import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { Link } from "react-router-dom";
import { cn } from "./cn";

/** True only once `active` has stayed true for `delay` ms — so fast actions never flash a spinner. */
export function useDelayedFlag(active: boolean, delay = 300) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!active) {
      setShown(false);
      return;
    }
    const t = window.setTimeout(() => setShown(true), delay);
    return () => window.clearTimeout(t);
  }, [active, delay]);
  return shown;
}

export function Spinner({ className, onDark = false }: { className?: string; onDark?: boolean }) {
  return (
    <svg className={cn("lv-spin h-[18px] w-[18px] shrink-0", className)} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke={onDark ? "#3F8F90" : "rgb(var(--lv-line))"} strokeWidth="3" />
      <path d="M12 3a9 9 0 0 1 9 9" fill="none" stroke={onDark ? "#FFFFFF" : "rgb(var(--lv-teal))"} strokeWidth="3"
        strokeLinecap="round" />
    </svg>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-teal text-[rgb(var(--lv-on-teal))] hover:bg-teal-hover active:bg-teal-press disabled:bg-teal/60",
  secondary: "border border-line bg-surface text-ink hover:border-teal/50 hover:bg-teal-soft",
  ghost: "text-teal hover:bg-teal-soft",
  danger: "border border-rejected/40 bg-surface text-rejected hover:bg-rejected-soft",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md" | "lg";
  icon?: LucideIcon;
  /** While true the button is busy; after 300 ms it shows a spinner and `busyLabel` (a verb). */
  busy?: boolean;
  busyLabel?: string;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon: Icon, busy = false, busyLabel, className, children, disabled, ...rest },
  ref,
) {
  const showSpinner = useDelayedFlag(busy);
  const sizes = { sm: "h-8 px-3 text-[13px] gap-1.5", md: "h-10 px-4 text-sm gap-2", lg: "h-11 px-5 text-[15px] gap-2.5" };
  return (
    <button
      ref={ref}
      className={cn(
        "lv-press inline-flex select-none items-center justify-center whitespace-nowrap rounded-md font-semibold",
        "disabled:cursor-not-allowed disabled:opacity-70",
        VARIANTS[variant],
        sizes[size],
        className,
      )}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {showSpinner ? (
        <>
          <Spinner onDark={variant === "primary"} />
          <span>{busyLabel ?? children}</span>
        </>
      ) : (
        <>
          {Icon && <Icon className="h-5 w-5 shrink-0" strokeWidth={1.75} aria-hidden="true" />}
          {children}
        </>
      )}
    </button>
  );
});

export function Card({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("rounded-lg border border-line bg-surface", className)} {...rest}>
      {children}
    </div>
  );
}

export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cn("text-xs font-semibold uppercase tracking-[0.1em] text-teal", className)}>{children}</span>
  );
}

/* ---------------------------------------------------------------------------------------------------------------
 * Status: never colour alone — every status pairs an icon, a colour and a word.
 * ------------------------------------------------------------------------------------------------------------- */

export type Tone = "success" | "attention" | "rejected" | "info" | "neutral";
const TONE: Record<Tone, string> = {
  success: "bg-success-soft text-success",
  attention: "bg-attention-soft text-attention",
  rejected: "bg-rejected-soft text-rejected",
  info: "bg-info-soft text-info",
  neutral: "bg-teal-soft text-muted",
};
const TONE_ICON: Record<Tone, LucideIcon> = {
  success: CheckCircle2,
  attention: Clock3,
  rejected: Ban,
  info: Info,
  neutral: Info,
};

export function Badge({ tone = "neutral", icon, children, className }: {
  tone?: Tone;
  icon?: LucideIcon | null;
  children: ReactNode;
  className?: string;
}) {
  const Icon = icon === null ? null : (icon ?? TONE_ICON[tone]);
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold", TONE[tone], className)}>
      {Icon && <Icon className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />}
      {children}
    </span>
  );
}

const CLAIM_STATUS: Record<string, { tone: Tone; label: string; icon: LucideIcon }> = {
  paid: { tone: "success", label: "Paid", icon: CheckCircle2 },
  pending: { tone: "attention", label: "Pending", icon: Clock3 },
  rejected: { tone: "rejected", label: "Rejected", icon: Ban },
  reversed: { tone: "neutral", label: "Reversed", icon: RotateCcw },
  unpaid_past_due: { tone: "attention", label: "Unpaid past due", icon: AlertTriangle },
  underpaid: { tone: "attention", label: "Underpaid", icon: FileWarning },
};

export function ClaimStatus({ status }: { status: string }) {
  const s = CLAIM_STATUS[status] ?? { tone: "neutral" as Tone, label: status, icon: Info };
  return <Badge tone={s.tone} icon={s.icon}>{s.label}</Badge>;
}

const CONTROL: Record<string, { tone: Tone; label: string; icon: LucideIcon }> = {
  green: { tone: "success", label: "Working", icon: CheckCircle2 },
  amber: { tone: "attention", label: "Attention", icon: AlertTriangle },
  red: { tone: "rejected", label: "Failing", icon: Ban },
};

export function ControlStatus({ status }: { status: string }) {
  const s = CONTROL[status] ?? CONTROL.amber;
  return <Badge tone={s.tone} icon={s.icon}>{s.label}</Badge>;
}

/* ---------------------------------------------------------------------------------------------------------------
 * Loading, empty and error states
 * ------------------------------------------------------------------------------------------------------------- */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("lv-skel h-2.5", className)} aria-hidden="true" />;
}

/** Skeleton rows in the final table shape, so nothing jumps when data arrives. */
export function SkeletonRows({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="divide-y divide-line" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex items-center gap-4 px-4 py-3.5">
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={c} className={cn("h-3", c === 0 ? "w-24" : c === cols - 1 ? "w-16" : "flex-1")} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ icon: Icon = Search, title, children, action }: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: { label: string; to: string };
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-12 text-center">
      <Icon className="h-7 w-7 text-teal" strokeWidth={1.75} aria-hidden="true" />
      <p className="font-display text-lg font-semibold">{title}</p>
      {children && <p className="max-w-md text-sm text-muted">{children}</p>}
      {action && (
        <Link className="text-sm font-semibold text-teal underline-offset-4 hover:underline" to={action.to}>
          {action.label} →
        </Link>
      )}
    </div>
  );
}

/** Errors say what happened and what to do next — never a raw stack trace. */
export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="flex items-start gap-3 rounded-md border border-rejected/30 bg-rejected-soft px-4 py-3 text-sm">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rejected" aria-hidden="true" />
      <div className="flex-1">
        <p className="font-semibold text-ink">{message}</p>
        {retry && (
          <button className="mt-1 font-semibold text-teal underline-offset-4 hover:underline" onClick={retry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------------------------------------------
 * Form fields — always with a visible label
 * ------------------------------------------------------------------------------------------------------------- */

const fieldBase =
  "w-full rounded-md border border-line bg-surface px-3 text-sm text-ink placeholder:text-subtle " +
  "transition-colors duration-hover hover:border-teal/50 focus-visible:border-teal";

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: (id: string) => ReactNode; className?: string }) {
  const id = useId();
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-[13px] font-medium text-muted">
        {label}
      </label>
      {children(id)}
      {hint && <span className="text-xs text-subtle">{hint}</span>}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cn(fieldBase, "h-10", className)} {...rest} />;
});

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(fieldBase, "h-10 pr-8", className)} {...rest}>
      {children}
    </select>
  );
}

export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(fieldBase, "min-h-[96px] py-2 leading-relaxed", className)} {...rest} />;
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-line bg-mist px-1.5 py-0.5 font-sans text-[11px] font-semibold text-muted">
      {children}
    </kbd>
  );
}

/* ---------------------------------------------------------------------------------------------------------------
 * Tabs (WAI-ARIA tabs pattern: arrow keys move between tabs)
 * ------------------------------------------------------------------------------------------------------------- */

export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: { value: T; label: string; count?: number }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent, i: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    refs.current[next]?.focus();
    onChange(tabs[next].value);
  };
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 border-b border-line">
      {tabs.map((t, i) => {
        const active = t.value === value;
        return (
          <button
            key={t.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onKeyDown={(e) => onKey(e, i)}
            onClick={() => onChange(t.value)}
            className={cn(
              "lv-press -mb-px flex items-center gap-2 border-b-2 px-3 py-2.5 text-sm font-semibold",
              active ? "border-teal text-ink" : "border-transparent text-muted hover:text-ink",
            )}
          >
            {t.label}
            {t.count !== undefined && (
              <span className={cn("tabular rounded-full px-2 text-xs", active ? "bg-teal text-[rgb(var(--lv-on-teal))]" : "bg-teal-soft text-muted")}>
                {t.count.toLocaleString()}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------------------------------------------------------------
 * Goal progress ring and stat tiles
 * ------------------------------------------------------------------------------------------------------------- */

export function ProgressRing({ value, target, label, format }: {
  value: number;
  target: number;
  label: string;
  format: (n: number) => string;
}) {
  const pct = target > 0 ? Math.min(1, value / target) : 0;
  const r = 30;
  const c = 2 * Math.PI * r;
  return (
    <div className="flex items-center gap-4">
      <svg width="76" height="76" viewBox="0 0 76 76" role="img" aria-label={`${label}: ${format(value)} of ${format(target)}`}>
        <circle cx="38" cy="38" r={r} fill="none" stroke="rgb(var(--lv-skeleton))" strokeWidth="8" />
        <circle
          cx="38" cy="38" r={r} fill="none" stroke="rgb(var(--lv-mint-stroke))" strokeWidth="8" strokeLinecap="round"
          strokeDasharray={`${c * pct} ${c}`} transform="rotate(-90 38 38)"
          style={{ transition: "stroke-dasharray 600ms var(--lv-ease-out)" }}
        />
        <text x="38" y="43" textAnchor="middle" className="fill-ink font-display text-[15px] font-semibold">
          {Math.round(pct * 100)}%
        </text>
      </svg>
      <div className="flex flex-col">
        <span className="text-[13px] text-muted">{label}</span>
        <span className="tabular font-display text-lg font-semibold">
          {format(value)} <span className="text-sm font-normal text-subtle">of {format(target)}</span>
        </span>
      </div>
    </div>
  );
}

export function StatTile({ label, value, sub, tone, icon: Icon }: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
  icon?: LucideIcon;
}) {
  return (
    <Card className="flex flex-col gap-1 p-4">
      <span className="flex items-center gap-2 text-[13px] text-muted">
        {Icon && <Icon className={cn("h-4 w-4", tone ? TONE[tone].split(" ")[1] : "text-teal")} aria-hidden="true" />}
        {label}
      </span>
      <span className="tabular font-display text-2xl font-semibold tracking-tight">{value}</span>
      {sub && <span className="text-xs text-subtle">{sub}</span>}
    </Card>
  );
}
