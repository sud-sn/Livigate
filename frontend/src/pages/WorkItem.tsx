import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Eye, Pencil, PhoneForwarded } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { cn } from "@/design-system/cn";
import { PharmacyIcon } from "@/design-system/icons";
import { AiDraftPanel, ConfirmDialog, SuccessChip, Tooltip, useToast, type AiDraftData } from "@/design-system/overlays";
import { Button, Card, ErrorNote, Field, Input, Select, SkeletonRows, Textarea } from "@/design-system/primitives";
import { api, newIdempotencyKey } from "@/lib/api";
import { useMe } from "@/lib/auth";
import { dateTime, longDate, money, shortDate } from "@/lib/format";
import { useShortcuts } from "@/lib/keys";
import { TypeBadge, type QueueItem } from "./MyQueue";

interface Fix {
  action: string;
  summary: string;
  due_date: string;
  owner_role: string;
  approve_label: string;
  steps: string[];
  computed: Record<string, string | number>;
}
interface Detail extends QueueItem {
  fill_date: string;
  claim: { billed: number; expected: number; paid: number | null; days_supply: number; quantity: number; claim_id: string };
  prescriber: { id: string; npi: string; name: string; practice: string };
  rule: { kind: string; key: string; version: number; title: string; rule_text: string; is_current: boolean } | null;
  fix: Fix | null;
  ai_explanation: (AiDraftData & { interaction_id: number }) | null;
  timeline: { ts: string; kind: string; summary: string; actor: string | null; source: string }[];
  approvals: { id: number; approver: string; approved_at: string; rule: string | null; note: string | null; edited: boolean }[];
  attempts: number;
  next_followup_date: string | null;
  can_act: boolean;
  can_approve: boolean;
  next_item_id: number | null;
}
interface ApproveResult { message: string; audit_line: string; next_item_id: number | null }
interface Revealed { first_name: string; last_name: string; dob: string; phone: string; preferred_language: string }

const COMPUTED_LABELS: Record<string, string> = {
  last_fill_date: "Last dispensed fill",
  last_days_supply: "Days supply",
  refill_pct: "Plan refill threshold",
  eligible_date: "Next eligible date",
  days_until_eligible: "Days until eligible",
  followup_date: "Follow-up date",
  days_since_fill: "Days since fill",
  payment_window_days: "Payer payment window",
  expected: "Expected",
  paid: "Paid",
  shortfall: "Shortfall",
  days_supply: "Days supply billed",
  plan_max_days_supply: "Plan days-supply limit",
};

function fmtComputed(key: string, v: string | number) {
  if (key.endsWith("_date") && typeof v === "string") return longDate(v);
  if (key === "refill_pct" || key === "threshold_pct") return `${Math.round(Number(v) * 100)}%`;
  if (["expected", "paid", "shortfall", "threshold_min"].includes(key)) return money(Number(v));
  return String(v);
}

export default function WorkItem() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const me = useMe();
  const toast = useToast();
  const itemQ = useQuery({ queryKey: ["work-item", id], queryFn: () => api<Detail>(`/api/work-items/${id}`) });
  const item = itemQ.data;

  const explain = useMutation({
    mutationFn: () => api<AiDraftData & { interaction_id: number }>(`/api/work-items/${id}/explanation`, { method: "POST" }),
  });
  useEffect(() => {
    if (item?.fix && !item.ai_explanation && explain.status === "idle") explain.mutate();
  }, [item, explain]);
  const explanation = item?.ai_explanation ?? explain.data ?? null;

  const [editing, setEditing] = useState(false);
  const [summary, setSummary] = useState("");
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [key] = useState(newIdempotencyKey);
  const [result, setResult] = useState<ApproveResult | null>(null);
  const [stay, setStay] = useState(false);
  const [revealed, setRevealed] = useState<Revealed | null>(null);

  useEffect(() => {
    setResult(null);
    setStay(false);
    setEditing(false);
    setRevealed(null);
    setNote("");
  }, [id]);
  useEffect(() => {
    if (item?.fix) setSummary(item.fix.summary);
  }, [item?.fix]);

  const approve = useMutation({
    mutationFn: () =>
      api<ApproveResult>(`/api/work-items/${id}/approve`, {
        method: "POST",
        body: {
          idempotency_key: key,
          confirm: true,
          note: note.trim() || undefined,
          edited_summary: editing && item?.fix && summary !== item.fix.summary ? summary : undefined,
          ai_disposition: explanation ? (editing ? "edited" : "accepted") : undefined,
        },
      }),
    onSuccess: (data) => {
      setConfirm(false);
      setResult(data);
      void qc.invalidateQueries({ queryKey: ["queue"] });
    },
    onError: () => setConfirm(false),
  });

  // Finishing an item opens the next one automatically (unless the user chooses to stay).
  useEffect(() => {
    if (!result?.next_item_id || stay) return;
    const t = window.setTimeout(() => navigate(`/work/${result.next_item_id}`), 2600);
    return () => window.clearTimeout(t);
  }, [result, stay, navigate]);

  const reveal = useMutation({
    mutationFn: () => api<Revealed>(`/api/patients/${item?.patient?.patient_id}/reveal`, { method: "POST" }),
    onSuccess: setRevealed,
    onError: (e) => toast({ tone: "error", text: (e as Error).message }),
  });

  const isReview = item?.type === "reject_review";
  const canApprove = Boolean(item?.can_approve && !result && (!isReview || note.trim()));
  useShortcuts({
    a: () => item?.can_approve && !result && (isReview ? document.getElementById("review-note")?.focus() : setConfirm(true)),
    e: () => item?.fix && item.can_approve && setEditing(true),
    j: () => item?.next_item_id && navigate(`/work/${item.next_item_id}`),
    k: () => navigate(-1),
  });

  if (itemQ.error) return <ErrorNote error={itemQ.error} retry={() => void itemQ.refetch()} />;
  if (!item) return <Card><SkeletonRows rows={10} cols={3} /></Card>;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link to="/" className="inline-flex items-center gap-1.5 text-sm font-semibold text-teal hover:underline">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" /> My queue
        </Link>
        {item.next_item_id && (
          <Link to={`/work/${item.next_item_id}`} className="inline-flex items-center gap-1.5 text-sm font-semibold text-teal hover:underline">
            Next item <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        )}
      </div>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col items-start gap-2">
          <TypeBadge type={item.type} label={item.type_label} />
          <h1 className="text-[26px] leading-tight">
            {item.rx_label}
            {item.reject_code && <span className="text-muted"> · Reject {item.reject_code}, {item.reject_message?.toLowerCase()}</span>}
          </h1>
          <p className="text-sm text-muted">
            {item.drug_name} · {item.payer_name} · filled {longDate(item.fill_date)} · {item.claim.days_supply}-day supply
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs text-muted">At risk</p>
          <p className="tabular font-display text-3xl font-semibold">{money(item.amount_at_risk)}</p>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex flex-col gap-5">
          {isReview ? (
            <Card className="flex flex-col gap-3 border-info/40 p-5">
              <h2 className="flex items-center gap-2 text-lg"><PharmacyIcon name="alert" size={24} className="text-info" />This reject code is not in the playbook</h2>
              <p className="text-sm text-muted">
                Levigate does not guess: there is no automated or AI suggestion for code <b>{item.reject_code}</b>. Work it manually
                in the PMS, then close this review with a note on what you did. An admin can add a playbook entry for this code
                (Admin → Business catalog) so future rejections are routed automatically.
              </p>
            </Card>
          ) : item.fix ? (
            <Card className="flex flex-col gap-4 p-5">
              <div className="flex items-center justify-between gap-3">
                <h2 className="flex items-center gap-2 text-lg"><PharmacyIcon name="prescription" size={24} className="text-teal" />Rule-based fix</h2>
                {item.rule && (
                  <Tooltip content={<span><b>{item.rule.title}</b><br />{item.rule.rule_text}</span>}>
                    <button className="rounded-full bg-teal-soft px-2.5 py-0.5 text-xs font-semibold text-teal">
                      Rule {item.rule.key} v{item.rule.version}{item.rule.is_current ? "" : " (superseded)"}
                    </button>
                  </Tooltip>
                )}
              </div>
              {editing ? (
                <Field label="Fix summary (your edit is recorded with the approval)">
                  {(fid) => <Input id={fid} value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={300} autoFocus />}
                </Field>
              ) : (
                <p className="font-display text-xl font-semibold">{item.fix.summary}</p>
              )}
              <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
                {item.fix.steps.map((s) => <li key={s}>{s}</li>)}
              </ol>
              {Object.keys(item.fix.computed).length > 0 && (
                <dl className="grid grid-cols-2 gap-x-6 gap-y-2 rounded-md bg-mist p-3 text-sm sm:grid-cols-3">
                  {Object.entries(item.fix.computed).filter(([k]) => COMPUTED_LABELS[k]).map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-xs text-subtle">{COMPUTED_LABELS[k]}</dt>
                      <dd className="tabular font-semibold">{fmtComputed(k, v)}</dd>
                    </div>
                  ))}
                </dl>
              )}
              <p className="text-xs text-subtle">Computed by the rules engine from the catalog and the claim response — not by the AI.</p>
            </Card>
          ) : null}

          {item.fix && (
            <AiDraftPanel
              title="Why this fix"
              draft={explanation}
              loading={explain.isPending}
              error={explain.error}
              onRetry={() => explain.mutate()}
            />
          )}

          <Card className="flex flex-col gap-4 p-5">
            {result ? (
              <div className="flex flex-col gap-3">
                <SuccessChip message={result.message} auditLine={result.audit_line} />
                {result.next_item_id && !stay ? (
                  <p className="flex items-center gap-3 text-sm text-muted">
                    Opening the next item…
                    <button className="font-semibold text-teal hover:underline" onClick={() => setStay(true)}>Stay here</button>
                  </p>
                ) : (
                  <div className="flex gap-3">
                    {result.next_item_id && <Button variant="primary" icon={ArrowRight} onClick={() => navigate(`/work/${result.next_item_id}`)}>Next item</Button>}
                    <Button onClick={() => navigate("/")}>Back to my queue</Button>
                  </div>
                )}
              </div>
            ) : item.can_approve ? (
              <>
                {isReview && (
                  <Field label="How was this code handled? (required)">
                    {(fid) => <Textarea id={fid} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000}
                      placeholder="e.g. Called the plan; days-supply limit is 30 for this product. Rebilled at 30 days." />}
                  </Field>
                )}
                <div className="flex flex-wrap items-center gap-3">
                  <Button variant="primary" size="lg" onClick={() => setConfirm(true)} disabled={!canApprove}>
                    {isReview ? "Close review" : item.fix?.approve_label ?? "Approve"}
                  </Button>
                  {item.fix && !editing && (
                    <Button icon={Pencil} onClick={() => setEditing(true)}>Edit fix</Button>
                  )}
                  <span className="text-xs text-subtle">Shortcut: A to approve, E to edit</span>
                </div>
                {approve.error && <ErrorNote error={approve.error} />}
              </>
            ) : (
              <p className="text-sm text-muted">
                {item.status === "awaiting_followup"
                  ? `Approved — follow-up due ${item.next_followup_date ? shortDate(item.next_followup_date) : "soon"}. Log contact with the plan or prescriber below.`
                  : "This item is closed."}
              </p>
            )}
          </Card>

          {item.can_act && !isReview && <FollowUp id={item.id} attempts={item.attempts} />}
        </div>

        <aside className="flex flex-col gap-5">
          <Card className="flex flex-col gap-3 p-4 text-sm">
            <h2 className="text-base">Claim</h2>
            <dl className="grid grid-cols-2 gap-y-2">
              <dt className="text-muted">Billed</dt><dd className="tabular text-right">{money(item.claim.billed)}</dd>
              <dt className="text-muted">Expected</dt><dd className="tabular text-right">{money(item.claim.expected)}</dd>
              <dt className="text-muted">Paid</dt><dd className="tabular text-right">{item.claim.paid === null ? "—" : money(item.claim.paid)}</dd>
              <dt className="text-muted">Location</dt><dd className="text-right">{item.location}</dd>
            </dl>
            <div className="border-t border-line pt-3">
              <p className="flex items-center gap-2 font-semibold"><PharmacyIcon name="patient" size={16} className="text-teal" />Patient</p>
              {revealed ? (
                <p className="mt-1">{revealed.first_name} {revealed.last_name} · DOB {revealed.dob} · {revealed.phone}<br />
                  <span className="text-xs text-subtle">Viewing recorded on the audit trail.</span></p>
              ) : (
                <div className="mt-1 flex items-center justify-between gap-2">
                  <span>{item.patient?.display} <span className="text-subtle">{item.patient?.phone}</span></span>
                  {me.phi_visible ? (
                    <Button size="sm" variant="ghost" icon={Eye} busy={reveal.isPending} onClick={() => reveal.mutate()}>Reveal</Button>
                  ) : (
                    <span className="text-xs text-subtle">Masked for your location</span>
                  )}
                </div>
              )}
            </div>
            <div className="border-t border-line pt-3">
              <p className="flex items-center gap-2 font-semibold"><PharmacyIcon name="prescriber" size={16} className="text-teal" />Prescriber</p>
              <p className="mt-1">{item.prescriber.name} · NPI {item.prescriber.npi}</p>
              <p className="text-muted">{item.prescriber.practice}</p>
            </div>
          </Card>
          <Card className="p-4">
            <h2 className="mb-3 flex items-center gap-2 text-base"><PharmacyIcon name="audit-log" size={20} className="text-teal" />History</h2>
            <ol className="relative flex flex-col gap-4 border-l border-line pl-4">
              {item.timeline.map((t, i) => (
                <li key={`${t.ts}-${i}`} className="text-sm">
                  <span className={cn("absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full", t.source === "Levigate" ? "bg-teal" : "bg-line")} aria-hidden="true" />
                  <p>{t.summary}</p>
                  <p className="text-xs text-subtle">{dateTime(t.ts)} · {t.actor ? `${t.actor} · ` : ""}{t.source}</p>
                </li>
              ))}
            </ol>
          </Card>
        </aside>
      </div>

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={isReview ? "Close this review?" : `${item.fix?.approve_label ?? "Approve"}?`}
        description={
          <>
            {isReview ? "Your note and name" : <>“{editing ? summary : item.fix?.summary}” and your name</>} will be recorded on the audit trail
            {item.rule ? ` with rule ${item.rule.key} v${item.rule.version}` : ""}. This can’t be undone.
          </>
        }
        confirmLabel={isReview ? "Close review" : "Approve and log"}
        busyLabel="Saving and logging…"
        busy={approve.isPending}
        onConfirm={() => approve.mutate()}
      />
    </div>
  );
}

function FollowUp({ id, attempts }: { id: number; attempts: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [party, setParty] = useState<"plan" | "prescriber">("plan");
  const [outcome, setOutcome] = useState("");
  const [next, setNext] = useState("");
  const save = useMutation({
    mutationFn: () => api(`/api/work-items/${id}/followups`, {
      method: "POST",
      body: { party, outcome, next_followup_date: next || undefined },
    }),
    onSuccess: () => {
      toast({ tone: "success", text: "Follow-up logged." });
      setOutcome("");
      void qc.invalidateQueries({ queryKey: ["work-item", String(id)] });
    },
  });
  return (
    <Card className="p-5">
      <h2 className="mb-3 flex items-center gap-2 text-base"><PhoneForwarded className="h-5 w-5 text-teal" aria-hidden="true" />Log a follow-up <span className="text-sm font-normal text-subtle">({attempts} so far)</span></h2>
      <form
        className="grid gap-3 sm:grid-cols-[160px_1fr_170px_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          if (outcome.trim().length >= 2) save.mutate();
        }}
      >
        <Field label="With">
          {(fid) => (
            <Select id={fid} value={party} onChange={(e) => setParty(e.target.value as "plan" | "prescriber")}>
              <option value="plan">Plan / payer</option>
              <option value="prescriber">Prescriber</option>
            </Select>
          )}
        </Field>
        <Field label="Outcome">{(fid) => <Input id={fid} value={outcome} onChange={(e) => setOutcome(e.target.value)} placeholder="e.g. PA submitted, decision in 48h" />}</Field>
        <Field label="Next follow-up">{(fid) => <Input id={fid} type="date" value={next} onChange={(e) => setNext(e.target.value)} />}</Field>
        <Button type="submit" busy={save.isPending} busyLabel="Saving…" disabled={outcome.trim().length < 2}>Log</Button>
      </form>
      {save.error && <div className="mt-3"><ErrorNote error={save.error} /></div>}
    </Card>
  );
}
