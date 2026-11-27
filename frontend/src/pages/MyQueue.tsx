import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Clock3, PhoneCall } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageHeader } from "@/app/Shell";
import { cn } from "@/design-system/cn";
import { PharmacyIcon } from "@/design-system/icons";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  ProgressRing,
  SkeletonRows,
  StatTile,
  type Tone,
} from "@/design-system/primitives";
import { api } from "@/lib/api";
import { useMe } from "@/lib/auth";
import { count, LIST_LABELS, money, money0, relativeDays, shortDate } from "@/lib/format";
import { useShortcuts } from "@/lib/keys";

export interface QueueItem {
  id: number;
  type: string;
  type_label: string;
  status: string;
  rx_label: string;
  reject_code: string | null;
  reject_message: string | null;
  payer_name: string;
  drug_name: string | null;
  amount_at_risk: number;
  due_date: string;
  days_overdue: number;
  headline: string;
  patient: { display: string; phone: string; patient_id: string } | null;
  location: string;
}

interface Goal { label: string; unit: "dollars" | "count"; progress: number; target: number }
interface ClaimsQueue {
  kind: "claims";
  total: number;
  dollars_at_risk: number;
  overdue: number;
  counts: { type: string; label: string; count: number }[];
  items: QueueItem[];
  filtered_total: number;
  page: number;
  page_size: number;
  wins: { approvals_today: number; recovered_today: number };
  goal: Goal;
}
interface OutreachQueue { kind: "outreach"; pending_call_id: number | null; wins: { calls_today: number; refills_confirmed_today: number }; goal: Goal }
interface CrmQueue {
  kind: "crm";
  tasks: { id: number; prescriber_id: string; next_action: string; due_date: string | null; overdue: boolean }[];
  wins: { contacts_today: number };
  goal: Goal;
}
type Queue = ClaimsQueue | OutreachQueue | CrmQueue;

export const TYPE_STYLE: Record<string, { tone: Tone; icon: "rejected-claim" | "claim" | "alert" }> = {
  claim_rejected: { tone: "rejected", icon: "rejected-claim" },
  claim_unpaid: { tone: "attention", icon: "claim" },
  claim_underpaid: { tone: "attention", icon: "claim" },
  reject_review: { tone: "info", icon: "alert" },
};

export function TypeBadge({ type, label }: { type: string; label: string }) {
  const s = TYPE_STYLE[type] ?? { tone: "neutral" as Tone, icon: "claim" as const };
  return (
    <Badge tone={s.tone} icon={null} className="gap-1">
      <PharmacyIcon name={s.icon} size={16} />
      {label}
    </Badge>
  );
}

function greeting(name: string) {
  const h = new Date().getHours();
  const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  return `${part}, ${name.split(" ")[0].replace(",", "")}`;
}

export default function MyQueue() {
  const me = useMe();
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ page: String(page), limit: "50" });
  if (filter !== "all") params.set("type", filter);
  const q = useQuery({
    queryKey: ["queue", filter, page],
    queryFn: () => api<Queue>(`/api/queue?${params}`),
    placeholderData: keepPreviousData,
  });
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />;
  const data = q.data;
  return (
    <>
      <PageHeader
        title={greeting(me.name)}
        subtitle={
          data?.kind === "claims"
            ? `${count(data.total)} items in your queue · ${count(data.overdue)} overdue · ${money0(data.dollars_at_risk)} at risk`
            : data?.kind === "outreach"
              ? "Your call lists are ready. Consent and eligibility rules are already applied."
              : data?.kind === "crm"
                ? "Your prescriber follow-ups and the relationships that need attention."
                : "Loading your work…"
        }
      />
      {!data ? (
        <Card><SkeletonRows rows={8} /></Card>
      ) : data.kind === "claims" ? (
        <ClaimsHome data={data} filter={filter} onFilter={(f) => { setFilter(f); setPage(1); }} page={page} onPage={setPage} loading={q.isFetching} />
      ) : data.kind === "outreach" ? (
        <OutreachHome data={data} />
      ) : (
        <CrmHome data={data} />
      )}
    </>
  );
}

function Wins({ text, goal }: { text: string; goal: Goal }) {
  const fmt = goal.unit === "dollars" ? money0 : count;
  return (
    <>
      <StatTile label="Today so far" value={<span className="text-lg">{text}</span>} icon={Clock3} />
      <Card className="flex items-center p-4">
        <ProgressRing value={goal.progress} target={goal.target} label={goal.label} format={fmt} />
      </Card>
    </>
  );
}

function ClaimsHome({ data, filter, onFilter, page, onPage, loading }: {
  data: ClaimsQueue;
  filter: string;
  onFilter: (f: string) => void;
  page: number;
  onPage: (p: number) => void;
  loading: boolean;
}) {
  const navigate = useNavigate();
  const [sel, setSel] = useState(0);
  const listRef = useRef<HTMLTableSectionElement>(null);
  const items = data.items;
  const pages = Math.max(1, Math.ceil(data.filtered_total / data.page_size));
  useEffect(() => setSel(0), [filter, page]);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-row="${sel}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);
  useShortcuts({
    j: () => setSel((s) => Math.min(items.length - 1, s + 1)),
    k: () => setSel((s) => Math.max(0, s - 1)),
    Enter: () => items[sel] && navigate(`/work/${items[sel].id}`),
  });
  const winsText = data.wins.approvals_today
    ? `${count(data.wins.approvals_today)} approved${data.wins.recovered_today ? `, ${money0(data.wins.recovered_today)} recovered` : ""}`
    : "No approvals yet today";
  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Dollars at risk" value={money0(data.dollars_at_risk)} sub="Rejected, unpaid and underpaid claims" icon={AlertTriangle} tone="rejected" />
        <StatTile label="Overdue" value={count(data.overdue)} sub="Past their due or follow-up date" icon={Clock3} tone="attention" />
        <Wins text={winsText} goal={data.goal} />
      </div>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by type">
        {[{ type: "all", label: "All", count: data.total }, ...data.counts].map((c) => (
          <button
            key={c.type}
            onClick={() => onFilter(c.type)}
            aria-pressed={filter === c.type}
            className={cn(
              "lv-press flex items-center gap-2 rounded-full border px-3 py-1.5 text-[13px] font-semibold",
              filter === c.type ? "border-teal bg-teal text-[rgb(var(--lv-on-teal))]" : "border-line bg-surface text-muted hover:border-teal/50",
            )}
          >
            {c.label}
            <span className="tabular">{count(c.count)}</span>
          </button>
        ))}
      </div>
      <Card className={cn("overflow-hidden transition-opacity", loading && "opacity-70")}>
        {items.length === 0 ? (
          filter === "claim_rejected" || filter === "all" ? (
            <EmptyState title="No rejected claims today." action={{ label: "Review unpaid claims", to: "/claims?status=unpaid_past_due" }}>
              New rejections appear here within one sync cycle.
            </EmptyState>
          ) : (
            <EmptyState title="Nothing of this type in your queue." action={{ label: "See all items", to: "/" }} />
          )
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-sm">
              <thead className="sticky top-0 z-10 bg-mist text-left text-xs text-muted">
                <tr>
                  <th className="px-4 py-2.5 font-semibold">Type</th>
                  <th className="px-4 py-2.5 font-semibold">Claim and next step</th>
                  <th className="px-4 py-2.5 font-semibold">Patient</th>
                  <th className="px-4 py-2.5 font-semibold">Payer</th>
                  <th className="px-4 py-2.5 text-right font-semibold">At risk</th>
                  <th className="px-4 py-2.5 font-semibold">Due</th>
                </tr>
              </thead>
              <tbody ref={listRef} className="divide-y divide-line">
                {items.map((i, idx) => (
                  <tr
                    key={i.id}
                    data-row={idx}
                    onClick={() => navigate(`/work/${i.id}`)}
                    onMouseEnter={() => setSel(idx)}
                    className={cn("cursor-pointer transition-colors duration-hover", idx === sel ? "bg-teal-soft" : "hover:bg-mist")}
                    aria-selected={idx === sel}
                  >
                    <td className="px-4 py-3 align-top"><TypeBadge type={i.type} label={i.type_label} /></td>
                    <td className="px-4 py-3 align-top">
                      <Link to={`/work/${i.id}`} className="font-semibold text-ink hover:underline" onClick={(e) => e.stopPropagation()}>
                        {i.rx_label}
                        {i.reject_code && <span className="font-normal text-muted"> · Reject {i.reject_code}{i.reject_message ? `, ${i.reject_message.toLowerCase()}` : ""}</span>}
                      </Link>
                      <p className="text-[13px] text-muted">{i.headline}</p>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 align-top">
                      {i.patient ? <span title="Masked — open the item to reveal">{i.patient.display} <span className="text-subtle">{i.patient.phone}</span></span> : "—"}
                    </td>
                    <td className="max-w-[180px] truncate px-4 py-3 align-top text-muted" title={i.payer_name}>{i.payer_name}</td>
                    <td className="tabular px-4 py-3 text-right align-top font-semibold">{money(i.amount_at_risk)}</td>
                    <td className="whitespace-nowrap px-4 py-3 align-top">
                      {i.days_overdue > 0 ? (
                        <span className="inline-flex items-center gap-1 font-semibold text-rejected">
                          <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {i.days_overdue}d overdue
                        </span>
                      ) : (
                        <span className="text-muted">{shortDate(i.due_date)}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
        <span>Tip: J and K move through the list, Enter opens the selected item.</span>
        {pages > 1 && (
          <span className="flex items-center gap-2">
            Page {page} of {pages}
            <Button size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
            <Button size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
          </span>
        )}
      </div>
    </div>
  );
}

function OutreachHome({ data }: { data: OutreachQueue }) {
  const lists = useQuery({
    queryKey: ["outreach-lists", "refill_due"],
    queryFn: () => api<{ lists: { type: string; label: string; count: number; excluded: number }[] }>("/api/outreach/lists?list_type=refill_due"),
  });
  const winsText = `${count(data.wins.calls_today)} calls, ${count(data.wins.refills_confirmed_today)} refills confirmed`;
  return (
    <div className="flex flex-col gap-5">
      {data.pending_call_id && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-attention/30 bg-attention-soft px-4 py-3 text-sm">
          <span className="flex items-center gap-2 font-semibold"><PhoneCall className="h-4 w-4 text-attention" aria-hidden="true" /> A call is waiting for its outcome.</span>
          <Link to="/outreach" className="font-semibold text-teal hover:underline">Log the outcome →</Link>
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Wins text={winsText} goal={data.goal} />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {(lists.data?.lists ?? []).map((l) => (
          <Link key={l.type} to={`/outreach?list=${l.type}`} className="group">
            <Card className="flex h-full flex-col gap-2 p-4 transition-colors duration-hover group-hover:border-teal/50">
              <span className="flex items-center gap-2 text-[13px] text-muted">
                <PharmacyIcon name={l.type === "balance_due" ? "claim" : l.type === "ready_for_pickup" ? "medication" : "refill"} size={20} className="text-teal" />
                {LIST_LABELS[l.type]}
              </span>
              <span className="tabular font-display text-3xl font-semibold">{count(l.count)}</span>
              <span className="text-xs text-subtle">{count(l.excluded)} excluded by consent or lapse rules</span>
              <span className="mt-auto flex items-center gap-1 text-sm font-semibold text-teal">Open list <ArrowRight className="h-4 w-4" aria-hidden="true" /></span>
            </Card>
          </Link>
        ))}
        {!lists.data && Array.from({ length: 4 }, (_, i) => <Card key={i} className="h-36 p-4"><div className="lv-skel h-full" /></Card>)}
      </div>
    </div>
  );
}

function CrmHome({ data }: { data: CrmQueue }) {
  const winsText = `${count(data.wins.contacts_today)} prescriber contacts`;
  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Wins text={winsText} goal={data.goal} />
        <Link to="/prescribers?segment=declining" className="group xl:col-span-2">
          <Card className="flex h-full items-center justify-between gap-4 p-4 group-hover:border-teal/50">
            <div>
              <p className="font-semibold">Declining prescribers</p>
              <p className="text-sm text-muted">Volume down 30% or more vs the prior 90 days — start here.</p>
            </div>
            <ArrowRight className="h-5 w-5 text-teal" aria-hidden="true" />
          </Card>
        </Link>
      </div>
      <Card>
        <h2 className="border-b border-line px-4 py-3 text-base">Next actions (7 days)</h2>
        {data.tasks.length === 0 ? (
          <EmptyState title="No follow-ups due this week." action={{ label: "Review ranked prescribers", to: "/prescribers" }} />
        ) : (
          <ul className="divide-y divide-line">
            {data.tasks.map((t) => (
              <li key={t.id}>
                <Link to={`/prescribers/${t.prescriber_id}`} className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-mist">
                  <span className="font-medium">{t.next_action}</span>
                  <span className={cn("text-sm", t.overdue ? "font-semibold text-rejected" : "text-muted")}>
                    {t.due_date ? (t.overdue ? `Overdue · ${shortDate(t.due_date)}` : relativeDays(Math.round((new Date(`${t.due_date}T12:00`).getTime() - Date.now()) / 86_400_000))) : ""}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
