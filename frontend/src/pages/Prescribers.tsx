import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownRight, ArrowLeft, ArrowUpRight, Mail, Minus, NotebookPen } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { PageHeader } from "@/app/Shell";
import { ChartFrame, LineChart, Sparkline } from "@/design-system/charts";
import { cn } from "@/design-system/cn";
import { PharmacyIcon } from "@/design-system/icons";
import { AiDraftPanel, Tooltip, useToast, type AiDraftData } from "@/design-system/overlays";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Select, SkeletonRows, StatTile, Tabs, Textarea } from "@/design-system/primitives";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { count, dateTime, money0, plural, shortDate, signedPct } from "@/lib/format";

interface Row {
  id: string;
  name: string;
  specialty: string;
  practice: string;
  city: string;
  rx_90: number;
  rx_prior_90: number;
  revenue_90: number;
  trend: number;
  score: number;
  score_parts: { volume: number; revenue: number; trend: number };
  segments: string[];
  owner: string | null;
  last_contact: string | null;
  rejected_claims_90: number;
}
type Segment = "top" | "declining" | "new" | "dormant" | "all";
const SEGMENT_LABELS: Record<Segment, string> = { top: "Top", declining: "Declining", new: "New", dormant: "Dormant", all: "All" };

function Trend({ value }: { value: number }) {
  const Icon = value > 0.05 ? ArrowUpRight : value < -0.05 ? ArrowDownRight : Minus;
  const tone = value <= -0.3 ? "text-rejected" : value < -0.05 ? "text-attention" : value > 0.05 ? "text-success" : "text-muted";
  return (
    <span className={cn("inline-flex items-center gap-0.5 font-semibold", tone)}>
      <Icon className="h-4 w-4" aria-hidden="true" />
      {signedPct(value)}
    </span>
  );
}

export function PrescriberList() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const segment = (params.get("segment") as Segment) || "top";
  const [search, setSearch] = useState("");
  const q = useQuery({
    queryKey: ["prescribers", segment],
    queryFn: () => api<{ counts: Record<Segment, number>; formula: string; rows: Row[] }>(`/api/prescribers?segment=${segment}`),
  });
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (q.data?.rows ?? []).filter((r) => !needle || r.name.toLowerCase().includes(needle) || r.practice.toLowerCase().includes(needle));
  }, [q.data, search]);
  return (
    <>
      <PageHeader title="Prescribers" subtitle={q.data?.formula ?? "Ranked from Rx volume, revenue and trend."} />
      <div className="flex flex-wrap items-end justify-between gap-4">
        <Tabs label="Prescriber lists" value={segment} onChange={(v) => setParams({ segment: v })}
          tabs={(Object.keys(SEGMENT_LABELS) as Segment[]).map((s) => ({ value: s, label: SEGMENT_LABELS[s], count: q.data?.counts[s] }))} />
        <Input aria-label="Filter by name or practice" placeholder="Filter by name or practice" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-xs" />
      </div>
      <Card className="mt-4 overflow-hidden">
        {q.error ? (
          <div className="p-4"><ErrorNote error={q.error} retry={() => void q.refetch()} /></div>
        ) : !q.data ? (
          <SkeletonRows rows={10} cols={7} />
        ) : rows.length === 0 ? (
          <EmptyState title="No prescribers in this list." action={{ label: "See all prescribers", to: "/prescribers?segment=all" }} />
        ) : (
          <div className="max-h-[68vh] overflow-auto">
            <table className="w-full min-w-[960px] text-sm">
              <thead className="sticky top-0 z-10 bg-mist text-left text-xs text-muted">
                <tr>
                  <th className="px-4 py-2.5 font-semibold">Prescriber</th>
                  <th className="px-4 py-2.5 text-right font-semibold">Rx (90 days)</th>
                  <th className="px-4 py-2.5 font-semibold">Trend</th>
                  <th className="px-4 py-2.5 text-right font-semibold">Revenue</th>
                  <th className="px-4 py-2.5 text-right font-semibold">Score</th>
                  <th className="px-4 py-2.5 text-right font-semibold">Rejections (90d)</th>
                  <th className="px-4 py-2.5 font-semibold">Last contact</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((r) => (
                  <tr key={r.id} className="cursor-pointer hover:bg-mist" onClick={() => navigate(`/prescribers/${r.id}`)}>
                    <td className="px-4 py-2.5">
                      <Link to={`/prescribers/${r.id}`} className="font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>{r.name}</Link>
                      <p className="text-xs text-muted">{r.specialty} · {r.practice}</p>
                    </td>
                    <td className="tabular px-4 py-2.5 text-right">{count(r.rx_90)} <span className="text-xs text-subtle">vs {count(r.rx_prior_90)}</span></td>
                    <td className="px-4 py-2.5"><Trend value={r.trend} /></td>
                    <td className="tabular px-4 py-2.5 text-right">{money0(r.revenue_90)}</td>
                    <td className="tabular px-4 py-2.5 text-right">
                      <Tooltip content={`Volume ${r.score_parts.volume} + revenue ${r.score_parts.revenue} + trend ${r.score_parts.trend}`}>
                        <span className="cursor-help font-semibold underline decoration-dotted underline-offset-4">{r.score.toFixed(0)}</span>
                      </Tooltip>
                    </td>
                    <td className="tabular px-4 py-2.5 text-right">{count(r.rejected_claims_90)}</td>
                    <td className={cn("px-4 py-2.5", !r.last_contact && "text-attention")}>{r.last_contact ? shortDate(r.last_contact) : "No contact"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

interface Activity { id: number; kind: string; outcome: string | null; notes: string | null; owner: string | null; created_at: string; next_action: string | null; next_action_due: string | null; next_action_done: boolean }
interface Profile {
  id: string; npi: string; name: string; specialty: string; practice: string; phone: string | null; fax: string | null; email: string | null;
  city: string; state: string; stats: Row; monthly: { month: string; rx: number; revenue: number }[];
  reject_reasons: { code: string; reason: string; count: number; amount: number }[];
  activities: Activity[]; next_action: Activity | null; formula: string;
}

const monthLabel = (m: string) => new Date(`${m}-15T12:00:00`).toLocaleDateString("en-US", { month: "short" });

export function PrescriberProfile() {
  const { id } = useParams();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["prescriber", id], queryFn: () => api<Profile>(`/api/prescribers/${id}`) });
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />;
  const p = q.data;
  if (!p) return <Card><SkeletonRows rows={8} cols={4} /></Card>;
  const s = p.stats;
  return (
    <div className="flex flex-col gap-5">
      <Link to="/prescribers" className="inline-flex items-center gap-1.5 text-sm font-semibold text-teal hover:underline">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Prescribers
      </Link>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="mb-2 flex flex-wrap gap-1.5">
            {s.segments.map((seg) => (
              <Badge key={seg} tone={seg === "declining" || seg === "dormant" ? "attention" : seg === "top" ? "success" : "info"}>{SEGMENT_LABELS[seg as Segment]}</Badge>
            ))}
          </div>
          <h1 className="text-[26px] leading-tight">{p.name}</h1>
          <p className="text-sm text-muted">{p.specialty} · {p.practice} · {p.city}, {p.state} · NPI {p.npi}</p>
          <p className="mt-1 text-sm text-muted">{p.phone} · fax {p.fax} · {p.email}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <span className="text-xs text-subtle">Rx per month</span>
          <Sparkline values={p.monthly.map((m) => m.rx)} label={`Rx per month: ${p.monthly.map((m) => `${monthLabel(m.month)} ${m.rx}`).join(", ")}`} />
        </div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Rx, last 90 days" value={count(s.rx_90)} sub={<>vs {count(s.rx_prior_90)} the 90 days before · <Trend value={s.trend} /></>} />
        <StatTile label="Revenue, last 90 days" value={money0(s.revenue_90)} />
        <StatTile label="Score" value={s.score.toFixed(0)} sub={`Volume ${s.score_parts.volume} · revenue ${s.score_parts.revenue} · trend ${s.score_parts.trend}`} />
        <StatTile label="Rejections (90 days)" value={count(s.rejected_claims_90)} sub="Among this prescriber's fills — aggregate only" />
      </div>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_400px]">
        <div className="flex flex-col gap-5">
          <Card className="p-5">
            <ChartFrame title="Rx filled per month" data={p.monthly.map((m) => ({ label: monthLabel(m.month), value: m.rx }))} format={count}>
              <LineChart label="Rx filled per month" data={p.monthly.map((m) => ({ label: monthLabel(m.month), value: m.rx }))} format={count} height={180} />
            </ChartFrame>
          </Card>
          <Card className="p-5">
            <h2 className="mb-1 flex items-center gap-2 text-base"><PharmacyIcon name="rejected-claim" size={20} className="text-teal" />Claim friction (90 days, aggregate)</h2>
            <p className="mb-3 text-xs text-subtle">Every rejection among this prescriber's fills, including ones since fixed. Counts only — no patient details.</p>
            {p.reject_reasons.length === 0 ? (
              <p className="text-sm text-muted">No open rejections among this prescriber's fills.</p>
            ) : (
              <ul className="divide-y divide-line text-sm">
                {p.reject_reasons.map((r) => (
                  <li key={r.code} className="flex items-center justify-between gap-3 py-2">
                    <span><span className="font-semibold">{r.code}</span> · {r.reason}</span>
                    <span className="tabular text-muted">{plural(r.count, "rejection")} · {money0(r.amount)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card className="p-5">
            <h2 className="mb-3 text-base">Contact history</h2>
            {p.activities.length === 0 ? (
              <p className="text-sm text-muted">No calls, visits or emails logged yet.</p>
            ) : (
              <ol className="flex flex-col gap-3 text-sm">
                {p.activities.map((a) => (
                  <li key={a.id} className="border-l-2 border-line pl-3">
                    <p><span className="font-semibold capitalize">{a.kind}</span>{a.outcome ? ` · ${a.outcome}` : ""}</p>
                    {a.notes && <p className="whitespace-pre-line text-muted">{a.notes}</p>}
                    {a.next_action && <p className="text-xs text-subtle">Next: {a.next_action}{a.next_action_due ? ` by ${shortDate(a.next_action_due)}` : ""}{a.next_action_done ? " · done" : ""}</p>}
                    <p className="text-xs text-subtle">{dateTime(a.created_at)} · {a.owner}</p>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>
        <aside className="flex flex-col gap-4">
          {p.next_action && (
            <Card className="border-teal/40 p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.1em] text-teal">Next action</p>
              <p className="mt-1 font-semibold">{p.next_action.next_action}</p>
              {p.next_action.next_action_due && <p className="text-sm text-muted">Due {shortDate(p.next_action.next_action_due)}</p>}
            </Card>
          )}
          {can("crm.write") ? <CrmActions prescriberId={p.id} /> : (
            <Card className="p-4 text-sm text-muted">Prescriber outreach is logged by the marketing team.</Card>
          )}
        </aside>
      </div>
    </div>
  );
}

function CrmActions({ prescriberId }: { prescriberId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [kind, setKind] = useState<"call" | "visit" | "email" | "note">("call");
  const [outcome, setOutcome] = useState("");
  const [notes, setNotes] = useState("");
  const [next, setNext] = useState("");
  const [due, setDue] = useState("");
  const [draftKind, setDraftKind] = useState<"prescriber_email" | "visit_brief" | null>(null);
  const [text, setText] = useState("");
  const draft = useMutation({
    mutationFn: (k: "prescriber_email" | "visit_brief") =>
      api<AiDraftData & { interaction_id: number }>(`/api/prescribers/${prescriberId}/draft`, { method: "POST", body: { kind: k } }),
    onSuccess: (d) => setText(d.text),
  });
  const log = useMutation({
    mutationFn: () =>
      api(`/api/prescribers/${prescriberId}/activities`, {
        method: "POST",
        body: {
          kind, outcome: outcome || undefined, notes: notes || (draftKind === "prescriber_email" && kind === "email" ? text : undefined) || undefined,
          next_action: next || undefined, next_action_due: due || undefined,
          draft_interaction_id: draft.data?.interaction_id, draft_disposition: draft.data ? (text === draft.data.text ? "accepted" : "edited") : undefined,
        },
      }),
    onSuccess: () => {
      toast({ tone: "success", text: "Logged. Next actions appear on your queue." });
      setOutcome(""); setNotes(""); setNext(""); setDue("");
      void qc.invalidateQueries({ queryKey: ["prescriber", prescriberId] });
      void qc.invalidateQueries({ queryKey: ["queue"] });
    },
    onError: (e) => toast({ tone: "error", text: (e as Error).message }),
  });
  return (
    <>
      <Card className="flex flex-col gap-3 p-4">
        <h2 className="flex items-center gap-2 text-base"><NotebookPen className="h-5 w-5 text-teal" aria-hidden="true" />Log a contact</h2>
        <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); log.mutate(); }}>
          <Field label="Type">
            {(fid) => (
              <Select id={fid} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
                <option value="call">Call</option><option value="visit">Visit</option><option value="email">Email</option><option value="note">Note</option>
              </Select>
            )}
          </Field>
          <Field label="Outcome">{(fid) => <Input id={fid} value={outcome} onChange={(e) => setOutcome(e.target.value)} placeholder="e.g. Spoke with office manager" />}</Field>
          <Field label="Notes">{(fid) => <Textarea id={fid} value={notes} onChange={(e) => setNotes(e.target.value)} className="min-h-[72px]" />}</Field>
          <div className="grid grid-cols-[1fr_150px] gap-2">
            <Field label="Next action">{(fid) => <Input id={fid} value={next} onChange={(e) => setNext(e.target.value)} placeholder="e.g. Drop off PA checklist" />}</Field>
            <Field label="Due">{(fid) => <Input id={fid} type="date" value={due} onChange={(e) => setDue(e.target.value)} />}</Field>
          </div>
          <Button type="submit" variant="primary" busy={log.isPending} busyLabel="Logging…">Log contact</Button>
        </form>
      </Card>
      <Card className="flex flex-col gap-3 p-4">
        <h2 className="flex items-center gap-2 text-base"><Mail className="h-5 w-5 text-teal" aria-hidden="true" />Draft from this prescriber's stats</h2>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => { setDraftKind("prescriber_email"); draft.mutate("prescriber_email"); }}>Outreach email</Button>
          <Button size="sm" onClick={() => { setDraftKind("visit_brief"); draft.mutate("visit_brief"); }}>Visit brief</Button>
        </div>
        {draftKind && (
          <AiDraftPanel title={draftKind === "visit_brief" ? "Visit brief" : "Outreach email"} draft={draft.data} loading={draft.isPending}
            error={draft.error} editable value={text} onChange={setText} onRetry={() => draft.mutate(draftKind)} />
        )}
        <p className="text-xs text-subtle">Drafts use aggregate numbers only — no patient information. Emails are sent from your mail client in the pilot.</p>
      </Card>
    </>
  );
}
