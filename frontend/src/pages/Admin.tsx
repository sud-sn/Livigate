import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Database, History, PlugZap, RefreshCw, XCircle } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/app/Shell";
import { cn } from "@/design-system/cn";
import { Dialog, ProcessSteps, useToast, type Step } from "@/design-system/overlays";
import { Badge, Button, Card, ErrorNote, Field, Input, Select, SkeletonRows, StatTile, Tabs, Textarea } from "@/design-system/primitives";
import { api } from "@/lib/api";
import { useMe } from "@/lib/auth";
import { dateTime } from "@/lib/format";

type Tab = "users" | "connections" | "catalog" | "sync";
const ROLES = ["billing", "outreach", "marketing", "pharmacist", "admin", "auditor"];

export default function Admin() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "users";
  return (
    <>
      <PageHeader title="Admin" subtitle="Users and roles, data connections, the business catalog and sync." />
      <Tabs label="Admin sections" value={tab} onChange={(v) => setParams({ tab: v })}
        tabs={[{ value: "users", label: "Users and roles" }, { value: "connections", label: "Data connections" }, { value: "catalog", label: "Business catalog" }, { value: "sync", label: "Sync and health" }]} />
      <div className="mt-5">
        {tab === "users" && <Users />}
        {tab === "connections" && <Connections />}
        {tab === "catalog" && <Catalog />}
        {tab === "sync" && <Sync />}
      </div>
    </>
  );
}

interface UserRow { id: string; name: string; email: string; role: string; location: string; offshore_phi_approved: boolean; active: boolean; last_login_at: string | null }
function Users() {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["admin-users"], queryFn: () => api<UserRow[]>("/api/admin/users") });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<UserRow> }) => api(`/api/admin/users/${id}`, { method: "PATCH", body }),
    onSuccess: () => { toast({ tone: "success", text: "Saved. The change applies on the user's next request and is audited." }); void qc.invalidateQueries({ queryKey: ["admin-users"] }); },
    onError: (e) => toast({ tone: "error", text: (e as Error).message }),
  });
  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Card><SkeletonRows rows={8} /></Card>;
  return (
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-sm">
        <thead className="bg-mist text-left text-xs text-muted"><tr>
          <th className="px-4 py-2.5 font-semibold">User</th><th className="px-4 py-2.5 font-semibold">Role</th><th className="px-4 py-2.5 font-semibold">Location</th>
          <th className="px-4 py-2.5 font-semibold">Last sign-in</th><th className="px-4 py-2.5 font-semibold">Access</th>
        </tr></thead>
        <tbody className="divide-y divide-line">
          {q.data.map((u) => (
            <tr key={u.id} className={cn(!u.active && "text-subtle")}>
              <td className="px-4 py-2.5"><b>{u.name}</b><p className="text-xs text-muted">{u.email}</p></td>
              <td className="px-4 py-2.5">
                <Select aria-label={`Role for ${u.name}`} value={u.role} disabled={u.id === me.id || patch.isPending} className="h-9 w-40"
                  onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value } })}>
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </Select>
              </td>
              <td className="px-4 py-2.5">{u.location}{u.location !== "US" && <span className="block text-xs">{u.offshore_phi_approved ? "PHI approved by client" : "PHI masked (C-11)"}</span>}</td>
              <td className="px-4 py-2.5">{u.last_login_at ? dateTime(u.last_login_at) : "Never"}</td>
              <td className="px-4 py-2.5">
                {u.id === me.id ? <span className="text-xs">You</span> : u.active ? (
                  <Button size="sm" variant="danger" onClick={() => patch.mutate({ id: u.id, body: { active: false } })}>Remove access</Button>
                ) : (
                  <Button size="sm" onClick={() => patch.mutate({ id: u.id, body: { active: true } })}>Restore</Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

interface Conn { id: string; name: string; kind: string; host: string | null; database: string | null; schema: string | null; auth_method: string; secret_ref: string | null; mode: string; enabled: boolean; blocked_reason: string | null; vendor: string | null; last_test_at: string | null; last_test_ok: boolean | null; last_test_message: string | null }
function Connections() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-conns"], queryFn: () => api<Conn[]>("/api/admin/connections") });
  const test = useMutation({
    mutationFn: (id: string) => api<{ ok: boolean; message: string }>(`/api/admin/connections/${id}/test`, { method: "POST" }),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["admin-conns"] }),
  });
  if (q.error) return <ErrorNote error={q.error} />;
  if (!q.data) return <Card><SkeletonRows rows={3} /></Card>;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {q.data.map((c) => (
        <Card key={c.id} className="flex flex-col gap-3 p-4">
          <div className="flex items-start justify-between gap-2">
            <span className="flex items-center gap-2 font-semibold"><Database className="h-5 w-5 text-teal" aria-hidden="true" />{c.name}</span>
            <Badge tone={c.enabled ? "success" : "neutral"}>{c.enabled ? "Enabled" : "Disabled"}</Badge>
          </div>
          <dl className="grid grid-cols-[110px_1fr] gap-y-1 text-sm">
            <dt className="text-muted">Type</dt><dd>{c.kind} · {c.mode === "read" ? "read-only" : c.mode}</dd>
            {c.host && (<><dt className="text-muted">Host</dt><dd className="truncate">{c.host}</dd></>)}
            {c.database && (<><dt className="text-muted">Database</dt><dd>{c.database}{c.schema ? `.${c.schema}` : ""}</dd></>)}
            <dt className="text-muted">Auth</dt><dd>{c.auth_method}</dd>
            {c.secret_ref && (<><dt className="text-muted">Secret</dt><dd className="truncate font-mono text-xs">{c.secret_ref}</dd></>)}
          </dl>
          {c.blocked_reason && <p className="text-sm font-semibold text-rejected">{c.blocked_reason}</p>}
          {c.last_test_at && (
            <p className={cn("flex items-center gap-1.5 text-sm", c.last_test_ok ? "text-success" : "text-rejected")}>
              {c.last_test_ok ? <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> : <XCircle className="h-4 w-4" aria-hidden="true" />}
              {c.last_test_ok ? "Passed" : "Failed"}: {c.last_test_message} · {dateTime(c.last_test_at)}
            </p>
          )}
          <Button size="sm" icon={PlugZap} className="mt-auto self-start" busy={test.isPending && test.variables === c.id} busyLabel="Testing…" onClick={() => test.mutate(c.id)}>Test connection</Button>
        </Card>
      ))}
      <p className="text-xs text-subtle lg:col-span-3">Secrets live in the vault; connections store only a reference. Every client connection uses a read-only service account.</p>
    </div>
  );
}

interface Entry { id: number; kind: string; key: string; version: number; title: string; body: Record<string, unknown>; status: string; is_current: boolean; change_note: string | null; created_by: string; created_at: string; approved_by: string | null; approved_at: string | null }
function Catalog() {
  const [kind, setKind] = useState("reject_playbook");
  const [open, setOpen] = useState<Entry | null>(null);
  const q = useQuery({ queryKey: ["catalog", kind], queryFn: () => api<{ kinds: string[]; entries: Entry[] }>(`/api/admin/catalog?kind=${kind}`) });
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Catalog part">
          {(id) => <Select id={id} value={kind} onChange={(e) => setKind(e.target.value)} className="w-64">{(q.data?.kinds ?? [kind]).map((k) => <option key={k} value={k}>{k.replaceAll("_", " ")}</option>)}</Select>}
        </Field>
        <p className="pb-2 text-sm text-muted">The rules engine executes these entries; the AI may only read them. Every change is versioned and needs a second admin.</p>
      </div>
      <Card className="overflow-x-auto">
        {q.error ? <div className="p-4"><ErrorNote error={q.error} /></div> : !q.data ? <SkeletonRows rows={8} /> : (
          <table className="w-full min-w-[760px] text-sm">
            <thead className="bg-mist text-left text-xs text-muted"><tr>
              <th className="px-4 py-2.5 font-semibold">Key</th><th className="px-4 py-2.5 font-semibold">Title</th><th className="px-4 py-2.5 font-semibold">Version</th>
              <th className="px-4 py-2.5 font-semibold">Status</th><th className="px-4 py-2.5 font-semibold">Approved</th><th className="px-4 py-2.5" />
            </tr></thead>
            <tbody className="divide-y divide-line">
              {q.data.entries.map((e) => (
                <tr key={e.id}>
                  <td className="px-4 py-2.5 font-mono text-xs">{e.key}</td><td className="px-4 py-2.5">{e.title}</td><td className="px-4 py-2.5">v{e.version}</td>
                  <td className="px-4 py-2.5">{e.status === "proposed" ? <Badge tone="attention">Waiting for approval</Badge> : <Badge tone="success">Current</Badge>}</td>
                  <td className="px-4 py-2.5 text-muted">{e.approved_by ? `${e.approved_by} · ${dateTime(e.approved_at!)}` : "—"}</td>
                  <td className="px-4 py-2.5 text-right"><Button size="sm" variant="ghost" icon={History} onClick={() => setOpen(e)}>Open</Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {open && <EntryDialog entry={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function EntryDialog({ entry, onClose }: { entry: Entry; onClose: () => void }) {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const [body, setBody] = useState(JSON.stringify(entry.body, null, 2));
  const [note, setNote] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const history = useQuery({ queryKey: ["catalog-history", entry.kind, entry.key], queryFn: () => api<Entry[]>(`/api/admin/catalog/${entry.kind}/${encodeURIComponent(entry.key)}/history`) });
  const pending = history.data?.find((h) => h.status === "proposed");
  const done = () => { void qc.invalidateQueries({ queryKey: ["catalog"] }); void qc.invalidateQueries({ queryKey: ["catalog-history"] }); };
  const propose = useMutation({
    mutationFn: () => api("/api/admin/catalog/proposals", { method: "POST", body: { kind: entry.kind, key: entry.key, title: entry.title, body: JSON.parse(body), change_note: note } }),
    onSuccess: () => { toast({ tone: "success", text: "Change proposed. A different admin must approve it." }); done(); },
    onError: (e) => toast({ tone: "error", text: (e as Error).message }),
  });
  const approve = useMutation({
    mutationFn: (id: number) => api(`/api/admin/catalog/proposals/${id}/approve`, { method: "POST" }),
    onSuccess: () => { toast({ tone: "success", text: "Approved. The new version is current and audited." }); done(); },
    onError: (e) => toast({ tone: "error", text: (e as Error).message }),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`${entry.title} (${entry.key})`} wide description={`Catalog part: ${entry.kind.replaceAll("_", " ")}`}>
      <div className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto pr-1">
        {pending ? (
          <div className="rounded-md border border-attention/30 bg-attention-soft p-3 text-sm">
            <p className="font-semibold">v{pending.version} is waiting for approval — proposed by {pending.created_by}: “{pending.change_note}”</p>
            <pre className="mt-2 max-h-40 overflow-auto rounded bg-surface p-2 text-xs">{JSON.stringify(pending.body, null, 2)}</pre>
            {pending.created_by !== me.name && <Button className="mt-2" variant="primary" size="sm" busy={approve.isPending} busyLabel="Approving…" onClick={() => approve.mutate(pending.id)}>Approve v{pending.version}</Button>}
          </div>
        ) : (
          <form className="flex flex-col gap-3" onSubmit={(e) => {
            e.preventDefault();
            try { JSON.parse(body); setJsonError(null); propose.mutate(); } catch { setJsonError("This isn't valid JSON yet — check commas and quotes."); }
          }}>
            <Field label="Rule body (JSON)" hint={jsonError ?? "Edit parameters and rule text. Handlers are fixed in code; only supported handlers are accepted."}>
              {(id) => <Textarea id={id} value={body} onChange={(e) => setBody(e.target.value)} className="min-h-[180px] font-mono text-xs" />}
            </Field>
            <Field label="Why are you changing it?">{(id) => <Input id={id} value={note} onChange={(e) => setNote(e.target.value)} minLength={3} required />}</Field>
            <Button type="submit" variant="primary" className="self-start" busy={propose.isPending} busyLabel="Proposing…" disabled={note.trim().length < 3}>Propose change</Button>
          </form>
        )}
        <div>
          <h3 className="mb-2 text-sm">Version history</h3>
          <ol className="flex flex-col gap-2 text-sm">
            {history.data?.map((h) => (
              <li key={h.id} className="rounded-md border border-line p-3">
                <p className="font-semibold">v{h.version} · {h.status}{h.is_current ? " (current)" : ""}</p>
                <p className="text-muted">{h.change_note}</p>
                <p className="text-xs text-subtle">Proposed by {h.created_by} · {dateTime(h.created_at)}{h.approved_by ? ` · approved by ${h.approved_by}` : ""}</p>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </Dialog>
  );
}

interface Run { id: number; kind: string; status: string; steps: Step[]; stats: Record<string, number>; error: string | null; started_at: string; finished_at: string | null }
interface Health { warehouse: { ok: boolean; message: string }; last_sync: Run | null; audit_chain: { ok: boolean; events: number }; ai_provider: string; env: string; sync_interval_minutes: number }
function Sync() {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["admin-health"], queryFn: () => api<Health>("/api/admin/health") });
  const runs = useQuery({ queryKey: ["admin-runs"], queryFn: () => api<Run[]>("/api/admin/sync/runs") });
  const run = useMutation({
    mutationFn: () => api<Run>("/api/admin/sync", { method: "POST" }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: ["admin-runs"] }); void qc.invalidateQueries({ queryKey: ["admin-health"] }); void qc.invalidateQueries({ queryKey: ["queue"] }); },
  });
  const pendingSteps: Step[] = [
    { key: "connect", label: "Connecting to warehouse", status: "running" },
    { key: "rules", label: "Reject-code rules applied", status: "pending" },
    { key: "items", label: "Building work items", status: "pending" },
    { key: "reconcile", label: "Reconciled with source", status: "pending" },
  ];
  const h = health.data;
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
      <div className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <StatTile label="Warehouse" value={h ? (h.warehouse.ok ? "Connected" : "Down") : "…"} sub={h?.warehouse.message} tone={h?.warehouse.ok ? "success" : "rejected"} icon={Database} />
          <StatTile label="Audit chain" value={h ? (h.audit_chain.ok ? "Intact" : "Broken") : "…"} sub={h ? `${h.audit_chain.events.toLocaleString()} events verified` : undefined} tone={h?.audit_chain.ok ? "success" : "rejected"} icon={CheckCircle2} />
          <StatTile label="AI provider" value={h?.ai_provider ?? "…"} sub={h ? `Sync every ${h.sync_interval_minutes} min · ${h.env}` : undefined} />
        </div>
        <Card className="overflow-x-auto">
          <h2 className="border-b border-line px-4 py-3 text-base">Recent sync runs</h2>
          {!runs.data ? <SkeletonRows rows={4} /> : (
            <table className="w-full min-w-[640px] text-sm">
              <thead className="bg-mist text-left text-xs text-muted"><tr>
                <th className="px-4 py-2 font-semibold">Started</th><th className="px-4 py-2 font-semibold">Kind</th><th className="px-4 py-2 font-semibold">Status</th>
                <th className="px-4 py-2 text-right font-semibold">Rows</th><th className="px-4 py-2 text-right font-semibold">Created</th><th className="px-4 py-2 text-right font-semibold">Closed</th>
              </tr></thead>
              <tbody className="divide-y divide-line">
                {runs.data.map((r) => (
                  <tr key={r.id}>
                    <td className="px-4 py-2">{dateTime(r.started_at)}</td><td className="px-4 py-2 capitalize">{r.kind}</td>
                    <td className="px-4 py-2"><Badge tone={r.status === "ok" ? "success" : r.status === "failed" ? "rejected" : "info"}>{r.status}</Badge></td>
                    <td className="tabular px-4 py-2 text-right">{r.stats.rows ?? "—"}</td><td className="tabular px-4 py-2 text-right">{r.stats.created ?? "—"}</td>
                    <td className="tabular px-4 py-2 text-right">{(r.stats.resolved ?? 0) + (r.stats.closed_upstream ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
      <Card className="flex flex-col gap-4 p-5">
        <h2 className="text-base">Run a sync now</h2>
        <p className="text-sm text-muted">Pulls changed claims, applies the playbook, builds work items and reconciles open items with the source. Scheduled runs happen automatically.</p>
        {run.isPending ? <ProcessSteps steps={pendingSteps} /> : run.data ? <ProcessSteps steps={run.data.steps} /> : null}
        {run.data && <p className="text-sm">Created {run.data.stats.created}, re-routed {run.data.stats.updated}, resolved {run.data.stats.resolved}, closed upstream {run.data.stats.closed_upstream}.</p>}
        {run.error && <ErrorNote error={run.error} />}
        <Button variant="primary" icon={RefreshCw} busy={run.isPending} busyLabel="Syncing…" onClick={() => run.mutate()}>Run sync</Button>
      </Card>
    </div>
  );
}
