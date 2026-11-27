import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Phone, PhoneOff } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/app/Shell";
import { cn } from "@/design-system/cn";
import { PharmacyIcon } from "@/design-system/icons";
import { AiDraftPanel, Tooltip, useToast, type AiDraftData } from "@/design-system/overlays";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, SkeletonRows, Tabs, Textarea } from "@/design-system/primitives";
import { api } from "@/lib/api";
import { count, LIST_LABELS, money, relativeDays, shortDate } from "@/lib/format";
import { useShortcuts } from "@/lib/keys";

type ListType = "refill_due" | "adherence_gap" | "ready_for_pickup" | "balance_due";
interface Entry {
  patient_id: string;
  rx_number: string | null;
  drug_name: string | null;
  due_date: string;
  days_to_due: number;
  amount: number;
  last_contact: string | null;
  score?: number;
  score_parts?: Record<string, number>;
  reasons?: string[];
  patient: { display: string; phone: string };
}
interface ListsResponse {
  list_type: ListType;
  lists: { type: ListType; label: string; count: number; excluded: number }[];
  entries: Entry[];
  exclusions: Entry[];
  sort_explanation: string;
  pending_call: { id: number; patient_id: string; list_type: string } | null;
  can_reveal: boolean;
}

const OUTCOMES: { value: string; label: string }[] = [
  { value: "refill_confirmed", label: "Refill confirmed" },
  { value: "reached", label: "Reached" },
  { value: "voicemail", label: "Voicemail" },
  { value: "no_answer", label: "No answer" },
  { value: "wrong_number", label: "Wrong number" },
  { value: "opted_out", label: "Opted out" },
];

export default function CallList() {
  const [params, setParams] = useSearchParams();
  const listType = (params.get("list") as ListType) || "refill_due";
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({
    queryKey: ["outreach-lists", listType],
    queryFn: () => api<ListsResponse>(`/api/outreach/lists?list_type=${listType}`),
  });
  const [sel, setSel] = useState(0);
  const [callId, setCallId] = useState<number | null>(null);
  const [callPatient, setCallPatient] = useState<Entry | null>(null);
  const [notes, setNotes] = useState("");
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  useEffect(() => setSel(0), [listType]);
  useEffect(() => {
    if (q.data?.pending_call && !callId) setCallId(q.data.pending_call.id);
  }, [q.data, callId]);

  const entries = q.data?.entries ?? [];
  const current = callPatient ?? entries[sel] ?? null;

  const script = useMutation({
    mutationFn: (e: Entry) =>
      api<AiDraftData & { interaction_id: number }>("/api/outreach/script", {
        method: "POST",
        body: { patient_id: e.patient_id, list_type: listType, rx_number: e.rx_number ?? undefined },
      }),
  });
  const currentKey = current ? `${current.patient_id}-${current.rx_number}` : "";
  useEffect(() => {
    if (current && !callId) script.mutate(current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentKey]);

  const start = useMutation({
    mutationFn: (e: Entry) =>
      api<{ call_id: number }>("/api/outreach/calls", {
        method: "POST",
        body: { patient_id: e.patient_id, list_type: listType, rx_number: e.rx_number ?? undefined },
      }),
    onSuccess: (r, e) => {
      setCallId(r.call_id);
      setCallPatient(e);
    },
    onError: (err) => toast({ tone: "error", text: (err as Error).message }),
  });
  const outcome = useMutation({
    mutationFn: (value: string) =>
      api(`/api/outreach/calls/${callId}/outcome`, {
        method: "POST",
        body: {
          outcome: value,
          notes: notes.trim() || undefined,
          script_interaction_id: script.data?.interaction_id,
          script_disposition: script.data ? "accepted" : undefined,
        },
      }),
    onSuccess: (_, value) => {
      toast({ tone: "success", text: value === "opted_out" ? "Opt-out recorded for calls and texts. The patient won't appear on any list." : "Outcome logged." });
      setCallId(null);
      setCallPatient(null);
      setNotes("");
      void qc.invalidateQueries({ queryKey: ["outreach-lists"] });
      void qc.invalidateQueries({ queryKey: ["queue"] });
    },
    onError: (err) => toast({ tone: "error", text: (err as Error).message }),
  });
  const reveal = useMutation({
    mutationFn: (pid: string) => api<{ first_name: string; last_name: string; phone: string; dob: string }>(`/api/patients/${pid}/reveal`, { method: "POST" }),
    onSuccess: (r, pid) => setRevealed((m) => ({ ...m, [pid]: `${r.first_name} ${r.last_name} · ${r.phone} · DOB ${r.dob}` })),
    onError: (err) => toast({ tone: "error", text: (err as Error).message }),
  });

  useShortcuts({
    j: () => !callId && setSel((s) => Math.min(entries.length - 1, s + 1)),
    k: () => !callId && setSel((s) => Math.max(0, s - 1)),
  });

  const tabs = (q.data?.lists ?? (Object.keys(LIST_LABELS) as ListType[]).map((t) => ({ type: t, label: LIST_LABELS[t], count: 0, excluded: 0 })))
    .map((l) => ({ value: l.type, label: l.label, count: q.data ? l.count : undefined }));

  return (
    <>
      <PageHeader title="Call lists" subtitle={q.data?.sort_explanation ?? "Ranked daily from the eligibility rules."} />
      <Tabs label="Call lists" tabs={tabs} value={listType} onChange={(v) => setParams({ list: v })} />
      {q.error ? (
        <div className="mt-4"><ErrorNote error={q.error} retry={() => void q.refetch()} /></div>
      ) : (
        <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_400px]">
          <div className="flex flex-col gap-5">
            <Card className="overflow-hidden">
              {!q.data ? (
                <SkeletonRows rows={10} cols={5} />
              ) : entries.length === 0 ? (
                <EmptyState title={`No one on the ${LIST_LABELS[listType].toLowerCase()} list right now.`} action={{ label: "Check the adherence gap list", to: "/outreach?list=adherence_gap" }} />
              ) : (
                <div className="max-h-[62vh] overflow-auto">
                  <table className="w-full min-w-[640px] text-sm">
                    <thead className="sticky top-0 z-10 bg-mist text-left text-xs text-muted">
                      <tr>
                        <th className="px-4 py-2.5 font-semibold">#</th>
                        <th className="px-4 py-2.5 font-semibold">Patient</th>
                        <th className="px-4 py-2.5 font-semibold">{listType === "balance_due" ? "Last fill" : "Prescription"}</th>
                        <th className="px-4 py-2.5 font-semibold">{listType === "ready_for_pickup" ? "Ready" : "Due"}</th>
                        <th className="px-4 py-2.5 text-right font-semibold">{listType === "balance_due" ? "Balance" : "Value"}</th>
                        <th className="px-4 py-2.5 font-semibold">Last contact</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {entries.map((e, i) => (
                        <tr key={`${e.patient_id}-${e.rx_number}`} onClick={() => !callId && setSel(i)}
                          className={cn("cursor-pointer", i === sel && !callPatient ? "bg-teal-soft" : "hover:bg-mist")} aria-selected={i === sel}>
                          <td className="px-4 py-2.5 text-subtle">
                            <Tooltip content={<span>Score {e.score}: due date {e.score_parts?.urgency}, revenue {e.score_parts?.revenue}, contact gap {e.score_parts?.contact_gap}</span>}>
                              <span className="tabular cursor-help">{i + 1}</span>
                            </Tooltip>
                          </td>
                          <td className="whitespace-nowrap px-4 py-2.5 font-semibold">{revealed[e.patient_id] ?? <>{e.patient.display} <span className="font-normal text-subtle">{e.patient.phone}</span></>}</td>
                          <td className="max-w-[240px] truncate px-4 py-2.5" title={e.drug_name ?? ""}>{e.rx_number ? `Rx ${e.rx_number} · ${e.drug_name}` : "Account balance"}</td>
                          <td className={cn("whitespace-nowrap px-4 py-2.5", e.days_to_due < 0 && listType !== "balance_due" ? "font-semibold text-attention" : "text-muted")}>
                            {listType === "balance_due" ? shortDate(e.due_date) : relativeDays(e.days_to_due)}
                          </td>
                          <td className="tabular px-4 py-2.5 text-right">{money(e.amount)}</td>
                          <td className="px-4 py-2.5 text-muted">{e.last_contact ? shortDate(e.last_contact) : "Never"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            {q.data && q.data.exclusions.length > 0 && (
              <Card className="p-4">
                <h2 className="mb-1 flex items-center gap-2 text-base"><PharmacyIcon name="consent" size={20} className="text-teal" />Not on today's list</h2>
                <p className="mb-3 text-sm text-muted">These patients would qualify by date but are held back by the rules. Showing {count(q.data.exclusions.length)}.</p>
                <ul className="divide-y divide-line text-sm">
                  {q.data.exclusions.slice(0, 12).map((x) => (
                    <li key={`${x.patient_id}-${x.rx_number}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
                      <span>{x.patient.display} <span className="text-subtle">{x.patient.phone}</span>{x.rx_number ? ` · Rx ${x.rx_number}` : ""}</span>
                      <span className="flex flex-wrap gap-1.5">
                        {x.reasons?.map((r) => <Badge key={r} tone={r.includes("lapsed") ? "neutral" : "attention"} icon={null}><PhoneOff className="h-3.5 w-3.5" aria-hidden="true" />{r}</Badge>)}
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>

          <aside className="flex flex-col gap-4">
            {current ? (
              <Card className="flex flex-col gap-4 p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs text-subtle">{callId ? "On a call with" : "Next up"}</p>
                    <p className="font-display text-xl font-semibold">{revealed[current.patient_id] ?? `${current.patient.display} ${current.patient.phone}`}</p>
                    <p className="text-sm text-muted">{current.rx_number ? `Rx ${current.rx_number} · ${current.drug_name}` : "Account balance"}</p>
                  </div>
                  {q.data?.can_reveal && !revealed[current.patient_id] && (
                    <Button size="sm" variant="ghost" icon={Eye} busy={reveal.isPending} onClick={() => reveal.mutate(current.patient_id)}>Reveal</Button>
                  )}
                </div>
                {!callId ? (
                  <Button variant="primary" size="lg" icon={Phone} busy={start.isPending} busyLabel="Dialing…" onClick={() => start.mutate(current)}>
                    Call
                  </Button>
                ) : (
                  <div className="flex flex-col gap-3">
                    <p className="flex items-center gap-2 text-sm font-semibold text-success"><Phone className="h-4 w-4" aria-hidden="true" />Call in progress — log the outcome to continue.</p>
                    <Field label="Notes (optional)">{(id) => <Textarea id={id} value={notes} onChange={(e) => setNotes(e.target.value)} className="min-h-[72px]" />}</Field>
                    <div className="grid grid-cols-2 gap-2" role="group" aria-label="Call outcome">
                      {OUTCOMES.map((o) => (
                        <Button key={o.value} variant={o.value === "opted_out" ? "danger" : o.value === "refill_confirmed" ? "primary" : "secondary"}
                          size="sm" busy={outcome.isPending && outcome.variables === o.value} busyLabel="Logging…" onClick={() => outcome.mutate(o.value)}>
                          {o.label}
                        </Button>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            ) : null}
            {current && (
              <AiDraftPanel title="Call script" draft={script.data} loading={script.isPending} error={script.error} onRetry={() => current && script.mutate(current)} />
            )}
          </aside>
        </div>
      )}
    </>
  );
}
