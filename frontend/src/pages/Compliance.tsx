import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Download, FileArchive, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/app/Shell";
import { cn } from "@/design-system/cn";
import { PharmacyIcon } from "@/design-system/icons";
import { ConfirmDialog, Dialog, useToast } from "@/design-system/overlays";
import { Badge, Button, Card, ControlStatus, EmptyState, ErrorNote, Field, Input, Select, SkeletonRows, Tabs } from "@/design-system/primitives";
import { api, download } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { count, dateTime, longDate } from "@/lib/format";

type Tab = "controls" | "approvals" | "consent" | "access" | "audit" | "ai" | "vendors" | "alerts";
const TABS: { value: Tab; label: string }[] = [
  { value: "controls", label: "Controls" },
  { value: "approvals", label: "Approvals" },
  { value: "consent", label: "Consent" },
  { value: "access", label: "Access review" },
  { value: "audit", label: "Audit log" },
  { value: "ai", label: "AI governance" },
  { value: "vendors", label: "Vendors & BAAs" },
  { value: "alerts", label: "Alerts" },
];
const LINK_TAB: Record<string, Tab> = { vendors: "vendors", audit: "audit", "ai-log": "ai", approvals: "approvals", consent: "consent", alerts: "alerts" };

interface Control {
  id: string; title: string; requirement: string; status: "green" | "amber" | "red"; evidence: string; automated: boolean;
  failing: Record<string, unknown>[]; owner: string; last_check: string; link: string | null;
}

export default function Compliance() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "controls";
  const { can } = useAuth();
  const [packOpen, setPackOpen] = useState(false);
  return (
    <>
      <PageHeader
        title="Compliance Center"
        subtitle="Proof the controls are working — checked from live data wherever possible."
        actions={can("audit.export") && <Button variant="primary" icon={FileArchive} onClick={() => setPackOpen(true)}>Audit pack</Button>}
      />
      <Tabs label="Compliance sections" tabs={TABS} value={tab} onChange={(v) => setParams({ tab: v })} />
      <div className="mt-5">
        {tab === "controls" && <Controls onOpen={(t) => setParams({ tab: t })} />}
        {tab === "approvals" && <Approvals />}
        {tab === "consent" && <Consent />}
        {tab === "access" && <AccessReview />}
        {tab === "audit" && <AuditLog />}
        {tab === "ai" && <AiLog />}
        {tab === "vendors" && <Vendors />}
        {tab === "alerts" && <Alerts />}
      </div>
      <AuditPackDialog open={packOpen} onOpenChange={setPackOpen} />
    </>
  );
}

function Controls({ onOpen }: { onOpen: (t: Tab) => void }) {
  const q = useQuery({
    queryKey: ["controls"],
    queryFn: () => api<{ controls: Control[]; summary: Record<string, number>; open_alerts: { id: number; summary: string }[] }>("/api/compliance/controls"),
  });
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />;
  if (!q.data) return <Card><SkeletonRows rows={6} cols={4} /></Card>;
  const { controls, summary } = q.data;
  const exceptions = controls.filter((c) => c.status !== "green");
  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-3">
        {(["green", "amber", "red"] as const).map((k) => (
          <Card key={k} className="flex items-center justify-between p-4">
            <ControlStatus status={k} />
            <span className="text-sm text-muted">
              <span className="tabular mr-1.5 font-display text-2xl font-semibold text-ink">{summary[k] ?? 0}</span>
              of {controls.length} controls
            </span>
          </Card>
        ))}
      </div>
      {exceptions.length > 0 && (
        <Card className="p-4">
          <h2 className="mb-2 text-base">Exceptions to fix</h2>
          <ul className="divide-y divide-line text-sm">
            {exceptions.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span><b>{c.id} {c.title}</b> — {c.evidence}</span>
                {c.link && LINK_TAB[c.link] && <button className="font-semibold text-teal hover:underline" onClick={() => onOpen(LINK_TAB[c.link!])}>Open evidence →</button>}
              </li>
            ))}
          </ul>
        </Card>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {controls.map((c) => (
          <Card key={c.id} className={cn("flex flex-col gap-2 p-4", c.status === "red" && "border-rejected/40", c.status === "amber" && "border-attention/40")}>
            <div className="flex items-start justify-between gap-2">
              <span className="text-xs font-semibold text-subtle">{c.id}</span>
              <ControlStatus status={c.status} />
            </div>
            <h3 className="text-base">{c.title}</h3>
            <p className="text-xs text-subtle">{c.requirement}</p>
            <p className="text-sm">{c.evidence}</p>
            <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-2 text-xs text-subtle">
              <span>{c.automated ? "Checked automatically" : "Attested"} · {dateTime(c.last_check)} · {c.owner}</span>
              {c.link && LINK_TAB[c.link] && <button className="font-semibold text-teal hover:underline" onClick={() => onOpen(LINK_TAB[c.link!])}>Evidence</button>}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

function Approvals() {
  const q = useQuery({ queryKey: ["cc-approvals"], queryFn: () => api<{ total: number; with_approver: number; pct: number; exceptions: { work_item_id: number; rx_label: string; status: string }[] }>("/api/compliance/approvals") });
  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Card><SkeletonRows rows={3} /></Card>;
  const d = q.data;
  return (
    <Card className="p-5">
      <p className="font-display text-3xl font-semibold">{d.pct}%</p>
      <p className="text-sm text-muted">{count(d.with_approver)} of {count(d.total)} claim actions carry a named approver (C-08, CC-02).</p>
      {d.exceptions.length === 0 ? (
        <p className="mt-3 flex items-center gap-2 text-sm font-semibold text-success"><BadgeCheck className="h-5 w-5" aria-hidden="true" />No exceptions.</p>
      ) : (
        <ul className="mt-3 divide-y divide-line text-sm">{d.exceptions.map((e) => <li key={e.work_item_id} className="py-2">{e.rx_label} · work item {e.work_item_id} · {e.status}</li>)}</ul>
      )}
    </Card>
  );
}

function Consent() {
  const q = useQuery({ queryKey: ["cc-consent"], queryFn: () => api<{ by_channel: Record<string, Record<string, number>>; contacts_total: number; contacts_without_consent: number; opt_outs: number; opt_out_max_hours: number | null; opt_outs_late: unknown[] }>("/api/compliance/consent") });
  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Card><SkeletonRows rows={3} /></Card>;
  const d = q.data;
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Card className={cn("p-5", d.contacts_without_consent > 0 && "border-rejected/40")}>
        <p className="text-sm text-muted">Contacts without consent</p>
        <p className="font-display text-3xl font-semibold">{count(d.contacts_without_consent)}</p>
        <p className="text-xs text-subtle">Must read zero. Checked against the consent on file at the moment of each of {count(d.contacts_total)} calls.</p>
      </Card>
      <Card className="p-5">
        <p className="text-sm text-muted">Opt-outs honoured</p>
        <p className="font-display text-3xl font-semibold">{count(d.opt_outs)}</p>
        <p className="text-xs text-subtle">Slowest sync back: {d.opt_out_max_hours ?? 0} hours (limit: 1 business day). Late: {d.opt_outs_late.length}.</p>
      </Card>
      <Card className="p-5">
        <p className="mb-2 text-sm text-muted">Consent on file by channel</p>
        <dl className="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-1 text-sm">
          <dt className="text-xs text-subtle">Channel</dt><dd className="text-xs text-subtle">Granted</dd><dd className="text-xs text-subtle">Revoked</dd>
          {Object.entries(d.by_channel).map(([ch, c]) => (
            <div key={ch} className="contents"><dt className="capitalize">{ch}</dt><dd className="tabular text-right">{count(c.granted ?? 0)}</dd><dd className="tabular text-right">{count(c.revoked ?? 0)}</dd></div>
          ))}
        </dl>
      </Card>
    </div>
  );
}

interface AccessUser { id: string; name: string; role: string; location: string; active: boolean; manager: string | null; last_login_at: string | null; phi_records_viewed_90d: number }
function AccessReview() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const q = useQuery({ queryKey: ["cc-access"], queryFn: () => api<{ period: string; due: boolean; last_review: { period: string; reviewer: string; signed_off_at: string } | null; users: AccessUser[] }>("/api/compliance/access-review") });
  const sign = useMutation({
    mutationFn: () => api("/api/compliance/access-review/sign-off", { method: "POST", body: { confirm: true } }),
    onSuccess: () => { setConfirm(false); toast({ tone: "success", text: "Access review signed off and logged." }); void qc.invalidateQueries({ queryKey: ["cc-access"] }); },
    onError: (e) => { setConfirm(false); toast({ tone: "error", text: (e as Error).message }); },
  });
  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Card><SkeletonRows rows={6} /></Card>;
  const d = q.data;
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line p-4">
        <p className="text-sm">
          {d.last_review ? <>Last review <b>{d.last_review.period}</b>, signed by {d.last_review.reviewer} on {longDate(d.last_review.signed_off_at)}.</> : "No review recorded yet."}
          {d.due && <Badge tone="attention" className="ml-2">{d.period} review due</Badge>}
        </p>
        {can("compliance.manage") && d.due && <Button variant="primary" onClick={() => setConfirm(true)}>Sign off {d.period}</Button>}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-mist text-left text-xs text-muted"><tr>
            <th className="px-4 py-2.5 font-semibold">User</th><th className="px-4 py-2.5 font-semibold">Role</th><th className="px-4 py-2.5 font-semibold">Location</th>
            <th className="px-4 py-2.5 font-semibold">Manager</th><th className="px-4 py-2.5 font-semibold">Last sign-in</th><th className="px-4 py-2.5 text-right font-semibold">Patients viewed (90d)</th>
          </tr></thead>
          <tbody className="divide-y divide-line">
            {d.users.map((u) => (
              <tr key={u.id} className={cn(!u.active && "text-subtle")}>
                <td className="px-4 py-2.5 font-semibold">{u.name}{!u.active && " (removed)"}</td><td className="px-4 py-2.5 capitalize">{u.role}</td>
                <td className="px-4 py-2.5">{u.location}</td><td className="px-4 py-2.5">{u.manager ?? "—"}</td>
                <td className="px-4 py-2.5">{u.last_login_at ? dateTime(u.last_login_at) : "Never"}</td>
                <td className="tabular px-4 py-2.5 text-right">{count(u.phi_records_viewed_90d)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title={`Sign off the ${d.period} access review?`}
        description="You confirm each user's role and access is still appropriate. Your name and the time are recorded." confirmLabel="Sign off"
        busyLabel="Recording…" busy={sign.isPending} onConfirm={() => sign.mutate()} />
    </Card>
  );
}

interface AuditRow { seq: number; ts: string; actor: string; actor_role: string; action: string; entity: string; patient_id: string | null; detail: Record<string, unknown>; hash: string }
function AuditLog() {
  const [user, setUser] = useState("");
  const [action, setAction] = useState("");
  const [patient, setPatient] = useState("");
  const [page, setPage] = useState(1);
  const qs = new URLSearchParams({ page: String(page), limit: "50" });
  if (user) qs.set("user_id", user);
  if (action) qs.set("action", action);
  if (patient) qs.set("patient_id", patient.toUpperCase());
  const q = useQuery({ queryKey: ["audit", qs.toString()], queryFn: () => api<{ total: number; page_size: number; actions: string[]; users: { id: string; name: string }[]; rows: AuditRow[] }>(`/api/compliance/audit-log?${qs}`) });
  const verify = useMutation({ mutationFn: () => api<{ ok: boolean; checked: number; first_bad_seq: number | null }>("/api/compliance/audit-log/verify") });
  const pages = q.data ? Math.max(1, Math.ceil(q.data.total / q.data.page_size)) : 1;
  return (
    <div className="flex flex-col gap-4">
      <Card className="grid items-end gap-3 p-4 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_auto]">
        <Field label="User">{(id) => <Select id={id} value={user} onChange={(e) => { setUser(e.target.value); setPage(1); }}><option value="">Everyone</option>{q.data?.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</Select>}</Field>
        <Field label="Action">{(id) => <Select id={id} value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }}><option value="">All actions</option>{q.data?.actions.map((a) => <option key={a} value={a}>{a}</option>)}</Select>}</Field>
        <Field label="Patient ID">{(id) => <Input id={id} value={patient} onChange={(e) => { setPatient(e.target.value); setPage(1); }} placeholder="e.g. PT00042" />}</Field>
        <Button icon={ShieldAlert} busy={verify.isPending} busyLabel="Verifying chain…" onClick={() => verify.mutate()}>Verify hash chain</Button>
      </Card>
      {verify.data && (
        <div role="status" className={cn("lv-in rounded-md px-4 py-3 text-sm font-semibold", verify.data.ok ? "bg-success-soft text-success" : "bg-rejected-soft text-rejected")}>
          {verify.data.ok ? `Chain intact: ${count(verify.data.checked)} events verified end to end.` : `Chain broken at event #${verify.data.first_bad_seq}. Open an incident.`}
        </div>
      )}
      <Card className="overflow-hidden">
        {q.error ? <div className="p-4"><ErrorNote error={q.error} /></div> : !q.data ? <SkeletonRows rows={10} cols={6} /> : q.data.rows.length === 0 ? (
          <EmptyState title="No audit events match these filters." />
        ) : (
          <div className="max-h-[60vh] overflow-auto">
            <table className="w-full min-w-[980px] text-sm">
              <thead className="sticky top-0 bg-mist text-left text-xs text-muted"><tr>
                <th className="px-4 py-2.5 font-semibold">#</th><th className="px-4 py-2.5 font-semibold">When</th><th className="px-4 py-2.5 font-semibold">Who</th>
                <th className="px-4 py-2.5 font-semibold">Action</th><th className="px-4 py-2.5 font-semibold">Record</th><th className="px-4 py-2.5 font-semibold">Patient</th>
                <th className="px-4 py-2.5 font-semibold">Detail</th><th className="px-4 py-2.5 font-semibold">Hash</th>
              </tr></thead>
              <tbody className="divide-y divide-line">
                {q.data.rows.map((r) => (
                  <tr key={r.seq}>
                    <td className="tabular px-4 py-2 text-subtle">{r.seq}</td><td className="whitespace-nowrap px-4 py-2">{dateTime(r.ts)}</td>
                    <td className="px-4 py-2">{r.actor} <span className="text-xs text-subtle">{r.actor_role}</span></td>
                    <td className="px-4 py-2 font-mono text-xs">{r.action}</td><td className="px-4 py-2 text-muted">{r.entity}</td>
                    <td className="px-4 py-2">{r.patient_id ?? "—"}</td>
                    <td className="max-w-[260px] truncate px-4 py-2 text-xs text-muted" title={JSON.stringify(r.detail)}>{Object.entries(r.detail).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}</td>
                    <td className="px-4 py-2 font-mono text-xs text-subtle">{r.hash}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {q.data && q.data.total > 0 && (
          <div className="flex items-center justify-between border-t border-line px-4 py-3 text-sm text-muted">
            <span>{count(q.data.total)} events · page {page} of {pages}</span>
            <div className="flex gap-2"><Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><Button size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button></div>
          </div>
        )}
      </Card>
    </div>
  );
}

interface AiRow { id: number; created_at: string; task: string; purpose: string; provider: string; model: string; fields_sent: string[]; fields_outside_allow_list: string[]; user: string; entity: string; disposition: string; fallback_reason: string | null; tokens: number }
function AiLog() {
  const q = useQuery({ queryKey: ["cc-ai"], queryFn: () => api<{ total: number; violations: number; rows: AiRow[] }>("/api/compliance/ai-log") });
  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Card><SkeletonRows rows={6} /></Card>;
  return (
    <Card className="overflow-hidden">
      <p className="border-b border-line p-4 text-sm">{count(q.data.total)} AI calls logged · <b className={q.data.violations ? "text-rejected" : "text-success"}>{count(q.data.violations)} outside a task allow-list</b> · prompts and responses kept as SHA-256 only.</p>
      {q.data.rows.length === 0 ? <EmptyState title="No AI calls yet." /> : (
        <div className="max-h-[60vh] overflow-auto">
          <table className="w-full min-w-[980px] text-sm">
            <thead className="sticky top-0 bg-mist text-left text-xs text-muted"><tr>
              <th className="px-4 py-2.5 font-semibold">When</th><th className="px-4 py-2.5 font-semibold">Purpose</th><th className="px-4 py-2.5 font-semibold">Model</th>
              <th className="px-4 py-2.5 font-semibold">Fields sent</th><th className="px-4 py-2.5 font-semibold">User</th><th className="px-4 py-2.5 font-semibold">Draft was</th>
            </tr></thead>
            <tbody className="divide-y divide-line">
              {q.data.rows.map((r) => (
                <tr key={r.id}>
                  <td className="whitespace-nowrap px-4 py-2">{dateTime(r.created_at)}</td>
                  <td className="px-4 py-2"><span className="flex items-center gap-1.5"><PharmacyIcon name="ai-draft" size={16} className="text-teal" />{r.purpose}</span><span className="text-xs text-subtle">{r.entity}</span></td>
                  <td className="px-4 py-2 text-xs">{r.provider} · {r.model}{r.fallback_reason ? <span className="block text-attention">Fallback: {r.fallback_reason}</span> : null}</td>
                  <td className="max-w-[280px] px-4 py-2 text-xs text-muted">{r.fields_sent.join(", ")}{r.fields_outside_allow_list.length > 0 && <span className="block font-semibold text-rejected">Outside allow-list: {r.fields_outside_allow_list.join(", ")}</span>}</td>
                  <td className="px-4 py-2">{r.user}</td>
                  <td className="px-4 py-2 capitalize">{r.disposition}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

interface VendorRow { id: string; name: string; service: string; touches_phi: boolean; baa_status: string; baa_expires_on: string | null; days_to_expiry: number | null; status: "green" | "amber" | "red"; note: string; owner: string | null }
function Vendors() {
  const q = useQuery({ queryKey: ["cc-vendors"], queryFn: () => api<VendorRow[]>("/api/compliance/vendors") });
  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Card><SkeletonRows rows={5} /></Card>;
  return (
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-sm">
        <thead className="bg-mist text-left text-xs text-muted"><tr>
          <th className="px-4 py-2.5 font-semibold">Vendor</th><th className="px-4 py-2.5 font-semibold">Touches PHI</th><th className="px-4 py-2.5 font-semibold">BAA</th>
          <th className="px-4 py-2.5 font-semibold">Expires</th><th className="px-4 py-2.5 font-semibold">Status</th><th className="px-4 py-2.5 font-semibold">Owner</th>
        </tr></thead>
        <tbody className="divide-y divide-line">
          {q.data.map((v) => (
            <tr key={v.id}>
              <td className="px-4 py-2.5"><b>{v.name}</b><p className="text-xs text-muted">{v.service}</p></td>
              <td className="px-4 py-2.5">{v.touches_phi ? "Yes" : "No"}</td>
              <td className="px-4 py-2.5 capitalize">{v.baa_status.replace("_", " ")}</td>
              <td className="px-4 py-2.5">{v.baa_expires_on ? longDate(v.baa_expires_on) : "—"}</td>
              <td className="px-4 py-2.5"><div className="flex flex-col gap-1"><ControlStatus status={v.status} /><span className="text-xs text-muted">{v.note}</span></div></td>
              <td className="px-4 py-2.5">{v.owner ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

interface AlertRow { id: number; kind: string; severity: string; summary: string; user: string | null; created_at: string; acknowledged_by: string | null; acknowledged_at: string | null }
function Alerts() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["cc-alerts"], queryFn: () => api<AlertRow[]>("/api/compliance/alerts") });
  const ack = useMutation({ mutationFn: (id: number) => api(`/api/compliance/alerts/${id}/acknowledge`, { method: "POST" }), onSuccess: () => void qc.invalidateQueries({ queryKey: ["cc-alerts"] }) });
  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Card><SkeletonRows rows={4} /></Card>;
  if (q.data.length === 0) return <Card><EmptyState title="No unusual access detected." icon={BadgeCheck}>Bulk views, off-hours exports and offshore PHI access raise alerts here within 15 minutes.</EmptyState></Card>;
  return (
    <Card className="divide-y divide-line">
      {q.data.map((a) => (
        <div key={a.id} className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
          <div>
            <p className="flex items-center gap-2 font-semibold"><Badge tone={a.severity === "high" ? "rejected" : "attention"}>{a.severity}</Badge>{a.kind.replaceAll("_", " ")}</p>
            <p className="text-muted">{a.summary}{a.user ? ` · ${a.user}` : ""} · {dateTime(a.created_at)}</p>
          </div>
          {a.acknowledged_at ? <span className="text-xs text-subtle">Acknowledged by {a.acknowledged_by}</span> : can("compliance.manage") && (
            <Button size="sm" busy={ack.isPending && ack.variables === a.id} onClick={() => ack.mutate(a.id)}>Acknowledge</Button>
          )}
        </div>
      ))}
    </Card>
  );
}

function AuditPackDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const toast = useToast();
  const today = new Date().toISOString().slice(0, 10);
  const [start, setStart] = useState(new Date(Date.now() - 90 * 86_400_000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(today);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const name = await download("/api/compliance/audit-pack", { confirm: true, start, end }, "levigate-audit-pack.zip");
      toast({ tone: "success", text: `Downloaded ${name}. The export is recorded on the audit trail.` });
      onOpenChange(false);
    } catch (err) {
      toast({ tone: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Download the audit pack"
      description="PDF summary plus CSVs: control status, approvals, consent, access reviews, vendors, alerts and audit events for the date range.">
      <div className="grid grid-cols-2 gap-3">
        <Field label="From">{(id) => <Input id={id} type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />}</Field>
        <Field label="To">{(id) => <Input id={id} type="date" value={end} min={start} max={today} onChange={(e) => setEnd(e.target.value)} />}</Field>
      </div>
      <div className="mt-5 flex justify-end gap-3">
        <Button onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
        <Button variant="primary" icon={Download} busy={busy} busyLabel="Building audit pack…" onClick={() => void run()}>Download</Button>
      </div>
    </Dialog>
  );
}
