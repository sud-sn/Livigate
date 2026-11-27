import * as RDialog from "@radix-ui/react-dialog";
import * as RTooltip from "@radix-ui/react-tooltip";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "./cn";
import { PharmacyIcon } from "./icons";
import { Button, Spinner } from "./primitives";

/* ---------------------------------------------------------------------------------------------------------------
 * Tooltip — icon-only buttons always carry one (plus an accessible name).
 * ------------------------------------------------------------------------------------------------------------- */

export function Tooltip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <RTooltip.Root delayDuration={250}>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content
          side={side}
          sideOffset={6}
          className="lv-in z-50 max-w-xs rounded-md bg-ink px-3 py-2 text-xs leading-relaxed text-mist shadow-lg"
        >
          {content}
          <RTooltip.Arrow className="fill-ink" />
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  );
}

export const TooltipProvider = RTooltip.Provider;

/* ---------------------------------------------------------------------------------------------------------------
 * Dialogs — confirm what can't be undone (approvals, exports, bulk lists).
 * ------------------------------------------------------------------------------------------------------------- */

export function Dialog({ open, onOpenChange, title, description, children, wide = false }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-40 bg-ink/40" />
        <RDialog.Content
          className={cn(
            "lv-in fixed left-1/2 top-1/2 z-50 w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-line bg-surface-raised p-6 shadow-xl",
            wide ? "max-w-2xl" : "max-w-md",
          )}
        >
          <div className="flex items-start justify-between gap-4">
            <RDialog.Title className="font-display text-lg font-semibold">{title}</RDialog.Title>
            <RDialog.Close className="lv-press rounded-md p-1 text-muted hover:bg-teal-soft" aria-label="Close">
              <X className="h-5 w-5" aria-hidden="true" />
            </RDialog.Close>
          </div>
          {description ? (
            <RDialog.Description className="mt-2 text-sm text-muted">{description}</RDialog.Description>
          ) : (
            <RDialog.Description className="sr-only">{title}</RDialog.Description>
          )}
          <div className="mt-5">{children}</div>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}

export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel, busyLabel, onConfirm, busy, tone = "primary" }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  busyLabel?: string;
  onConfirm: () => void;
  busy?: boolean;
  tone?: "primary" | "danger";
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={title} description={description}>
      <div className="flex justify-end gap-3">
        <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={busy}>
          Cancel
        </Button>
        <Button variant={tone === "danger" ? "danger" : "primary"} onClick={onConfirm} busy={busy} busyLabel={busyLabel} autoFocus>
          {confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}

/* ---------------------------------------------------------------------------------------------------------------
 * Toasts — rise 8 px and fade in (250 ms). Reversible actions offer Undo for 10 seconds.
 * ------------------------------------------------------------------------------------------------------------- */

type ToastItem = { id: number; tone: "success" | "error" | "info"; text: string; undo?: () => void };
const ToastCtx = createContext<(t: Omit<ToastItem, "id">) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const push = useCallback((t: Omit<ToastItem, "id">) => {
    const id = ++seq.current;
    setItems((xs) => [...xs, { ...t, id }]);
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), t.undo ? 10_000 : 5_000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(420px,calc(100vw-32px))] flex-col gap-2" aria-live="polite">
        {items.map((t) => {
          const Icon = t.tone === "success" ? CheckCircle2 : t.tone === "error" ? AlertTriangle : Info;
          return (
            <div key={t.id} className="lv-in pointer-events-auto flex items-start gap-3 rounded-md border border-line bg-surface-raised px-4 py-3 text-sm shadow-lg">
              <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", t.tone === "success" ? "text-success" : t.tone === "error" ? "text-rejected" : "text-info")} aria-hidden="true" />
              <span className="flex-1">{t.text}</span>
              {t.undo && (
                <button
                  className="font-semibold text-teal hover:underline"
                  onClick={() => {
                    t.undo?.();
                    setItems((xs) => xs.filter((x) => x.id !== t.id));
                  }}
                >
                  Undo
                </button>
              )}
            </div>
          );
        })}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/* ---------------------------------------------------------------------------------------------------------------
 * Process steps — for jobs over 2 seconds: named steps with checks, never a bare spinner.
 * ------------------------------------------------------------------------------------------------------------- */

export type Step = { key: string; label: string; status: "pending" | "running" | "done" | "failed" };

export function ProcessSteps({ steps }: { steps: Step[] }) {
  const done = steps.filter((s) => s.status === "done").length;
  return (
    <div className="flex flex-col gap-3" role="status" aria-live="polite">
      <div className="h-1.5 overflow-hidden rounded-full bg-skeleton">
        <div className="h-1.5 rounded-full bg-teal transition-all duration-panel ease-out" style={{ width: `${(done / steps.length) * 100}%` }} />
      </div>
      <ol className="flex flex-col gap-2.5 text-sm">
        {steps.map((s) => (
          <li key={s.key} className={cn("lv-in flex items-center gap-2.5", s.status === "pending" && "text-subtle")}>
            {s.status === "done" ? (
              <CheckDot />
            ) : s.status === "running" ? (
              <Spinner />
            ) : s.status === "failed" ? (
              <AlertTriangle className="h-[18px] w-[18px] text-rejected" aria-hidden="true" />
            ) : (
              <span className="h-[18px] w-[18px] rounded-full border-2 border-line" aria-hidden="true" />
            )}
            <span>
              {s.label}
              {s.status === "running" && "…"}
            </span>
            <span className="sr-only">{s.status}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function CheckDot({ size = 18, fill = "rgb(var(--lv-teal))" }: { size?: number; fill?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="10" fill={fill} />
      <path className="lv-check" d="M7.5 12.5l3 3 6-6" fill="none" stroke="#FFFFFF" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Success: the check draws (350 ms), then the audit line rises in beneath it. Shown only after the server logged it. */
export function SuccessChip({ message, auditLine }: { message: string; auditLine?: string }) {
  const [showAudit, setShowAudit] = useState(false);
  useEffect(() => {
    const t = window.setTimeout(() => setShowAudit(true), 350);
    return () => window.clearTimeout(t);
  }, []);
  return (
    <div className="flex flex-col gap-2" role="status" aria-live="polite">
      <div className="lv-in inline-flex h-11 items-center gap-2.5 self-start rounded-md bg-success-soft px-4 text-[15px] font-semibold text-success">
        <CheckDot size={20} fill="rgb(var(--lv-success))" />
        {message}
      </div>
      {auditLine && showAudit && (
        <div className="lv-in flex items-center gap-2.5 rounded-md border border-line bg-mist px-3.5 py-2.5 text-[13px] text-muted">
          <PharmacyIcon name="audit-log" size={16} className="text-teal" />
          {auditLine}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------------------------------------------
 * AI draft — always labelled, shown next to the rule or data it came from, editable before use.
 * ------------------------------------------------------------------------------------------------------------- */

export interface AiDraftData {
  text: string;
  provider: string;
  model: string;
  fallback_reason?: string | null;
  fields_sent: string[];
  sources?: string[];
}

export function AiDraftPanel({ draft, loading, error, editable = false, value, onChange, title, onRetry }: {
  draft?: AiDraftData | null;
  loading?: boolean;
  error?: unknown;
  editable?: boolean;
  value?: string;
  onChange?: (v: string) => void;
  title?: string;
  onRetry?: () => void;
}) {
  const edited = editable && draft && value !== undefined && value !== draft.text;
  return (
    <section className="rounded-lg border border-line bg-surface" aria-label={title ?? "AI draft"}>
      <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <span className="flex items-center gap-2 text-sm font-semibold">
          <PharmacyIcon name="ai-draft" size={20} className="text-teal" />
          {title && <span>{title}</span>}
          <span className={cn("rounded-full px-2 py-0.5 text-xs font-semibold", edited ? "bg-teal-soft text-muted" : "bg-info-soft text-info")}>
            {edited ? "Edited by you" : "AI draft"}
          </span>
        </span>
        {draft && (
          <Tooltip
            content={
              <span>
                {draft.provider === "offline"
                  ? "Generated by the offline template engine (no data leaves the environment)."
                  : `Generated by ${draft.model} on a BAA-covered endpoint.`}
                {draft.fallback_reason ? ` Fallback: ${draft.fallback_reason}.` : ""}
                <br />
                Fields sent: {draft.fields_sent.join(", ")}
              </span>
            }
          >
            <button className="text-xs font-medium text-subtle underline-offset-4 hover:underline">What was sent?</button>
          </Tooltip>
        )}
      </header>
      <div className="px-4 py-3">
        {loading ? (
          <div className="flex flex-col gap-2 py-1" role="status" aria-label="Drafting">
            <div className="lv-skel h-3 w-11/12" />
            <div className="lv-skel h-3 w-4/5" />
            <div className="lv-skel h-3 w-2/3" />
          </div>
        ) : error ? (
          <div className="flex items-center justify-between gap-3 text-sm text-muted">
            <span>{error instanceof Error ? error.message : "The draft could not be generated."}</span>
            {onRetry && (
              <Button size="sm" variant="ghost" onClick={onRetry}>
                Try again
              </Button>
            )}
          </div>
        ) : draft ? (
          editable ? (
            <textarea
              aria-label="Edit draft"
              className="min-h-[160px] w-full resize-y rounded-md border border-line bg-surface p-3 text-sm leading-relaxed"
              value={value}
              onChange={(e) => onChange?.(e.target.value)}
            />
          ) : (
            // Rendered as text, never as HTML (LLM05: improper output handling).
            <p className="whitespace-pre-line text-sm leading-relaxed">{draft.text}</p>
          )
        ) : null}
        {draft?.sources && draft.sources.length > 0 && (
          <p className="mt-2 text-xs text-subtle">Based on: {draft.sources.join(" · ")}</p>
        )}
      </div>
    </section>
  );
}
