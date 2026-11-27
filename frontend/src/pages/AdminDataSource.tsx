import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Code2, Database, GitCompareArrows, ListChecks, ShieldCheck, XCircle } from "lucide-react";
import { useState } from "react";
import { Dialog } from "@/design-system/overlays";
import { Badge, Button, Card, ErrorNote, SkeletonRows, StatTile, type Tone } from "@/design-system/primitives";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";

interface Relation { relation: string; meaning: string; ok: boolean; missing: string[]; extra: string[]; mapped: boolean; sql: string | null; source_tables: string[]; error?: string }
interface Summary { fail: number; warn: number; info: number; pass: number; error: number }
interface DataSource {
  profile: { name: string; title: string; description: string; notes: string[] };
  relations: Relation[];
  helpers: { name: string; sql: string }[];
  bi_views: string[];
  checks: number;
  last_sync: { finished_at: string | null; quality: Summary | null; findings: { key: string; status: string; count: number }[] };
}
interface CheckResult { key: string; title: string; severity: string; scope: string; count: number | null; status: string; explanation: string }
interface Group { label: string; explanation: string; explained: boolean; count: number; samples: string[] }
interface Section { key: string; title: string; compared: number; matched: number; mismatched: number; explained: number; unexplained: number; groups: Group[] }
type Parity = { available: false; reason: string } | { available: true; window: { start: string; end: string }; bi_views: string[]; sections: Section[]; unexplained: number };

const CHECK_TONE: Record<string, Tone> = { pass: "success", info: "info", warn: "attention", fail: "rejected", error: "rejected" };
const CHECK_LABEL: Record<string, string> = { pass: "Pass", info: "Note", warn: "Warning", fail: "Failing", error: "Could not run" };
const ORDER = ["error", "fail", "warn", "info", "pass"];

export function DataSourceTab() {
  const q = useQuery({ queryKey: ["admin-data-source"], queryFn: () => api<DataSource>("/api/admin/data-source") });
  const [sql, setSql] = useState<{ title: string; sql: string } | null>(null);
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />;
  if (!q.data) return <Card><SkeletonRows rows={7} /></Card>;
  const d = q.data;
  const okCount = d.relations.filter((r) => r.ok).length;
  const s = d.last_sync.quality;
  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile label="Source mapping" value={d.profile.name} sub={d.profile.title} icon={Database} />
        <StatTile label="Column contract" value={`${okCount} of ${d.relations.length}`} sub={okCount === d.relations.length ? "Every relation returns exactly the columns Levigate expects" : "Some relations do not match — see below"} tone={okCount === d.relations.length ? "success" : "rejected"} icon={ShieldCheck} />
        <StatTile label="Data checks at last sync" value={s ? (s.fail ? `${s.fail} failing` : s.warn ? `${s.warn} warning${s.warn === 1 ? "" : "s"}` : "All clear") : "—"}
          sub={s ? `${s.pass} passed · ${s.info} notes · ${d.checks} checks${d.last_sync.finished_at ? ` · ${dateTime(d.last_sync.finished_at)}` : ""}` : "No sync yet"}
          tone={s ? (s.fail ? "rejected" : s.warn ? "attention" : "success") : undefined} icon={ListChecks} />
      </div>

      <Card className="p-5">
        <h2 className="text-base">{d.profile.title}</h2>
        <p className="mt-1 max-w-3xl text-sm text-muted">{d.profile.description}</p>
        {d.profile.notes.length > 0 && (
          <ul className="mt-3 grid gap-1.5 text-sm sm:grid-cols-2">
            {d.profile.notes.map((n) => <li key={n} className="flex gap-2"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-teal" aria-hidden="true" />{n}</li>)}
          </ul>
        )}
        <p className="mt-3 text-xs text-subtle">Mappings are code, reviewed and versioned like the rest of Levigate. They are read-only here and never run anything but SELECT statements against the client's warehouse.</p>
      </Card>

      <Card className="overflow-x-auto">
        <h2 className="border-b border-line px-4 py-3 text-base">How the client's tables map to Levigate</h2>
        <table className="w-full min-w-[820px] text-sm">
          <thead className="bg-mist text-left text-xs text-muted"><tr>
            <th className="px-4 py-2 font-semibold">Levigate relation</th><th className="px-4 py-2 font-semibold">Client tables and views</th>
            <th className="px-4 py-2 font-semibold">Contract</th><th className="px-4 py-2 font-semibold"><span className="sr-only">Mapping SQL</span></th>
          </tr></thead>
          <tbody className="divide-y divide-line">
            {d.relations.map((r) => (
              <tr key={r.relation} className="align-top">
                <td className="px-4 py-3"><div className="font-mono text-[13px] font-semibold">{r.relation}</div><div className="text-xs text-muted">{r.meaning}</div></td>
                <td className="px-4 py-3"><div className="flex flex-wrap gap-1">{r.source_tables.map((t) => <code key={t} className="rounded bg-mist px-1.5 py-0.5 text-xs">{t}</code>)}</div></td>
                <td className="px-4 py-3">
                  {r.ok ? <Badge tone="success">Matches</Badge> : <Badge tone="rejected" icon={XCircle}>{r.error ? "Does not run" : `Missing ${r.missing.join(", ")}`}</Badge>}
                </td>
                <td className="px-4 py-3 text-right">
                  {r.sql ? <Button size="sm" variant="ghost" icon={Code2} onClick={() => setSql({ title: r.relation, sql: r.sql ?? "" })}>View mapping</Button> : <span className="text-xs text-subtle">Read directly</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {d.helpers.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3 text-xs text-muted">
            Shared steps:
            {d.helpers.map((h) => <button key={h.name} type="button" className="rounded bg-mist px-1.5 py-0.5 font-mono text-ink hover:bg-teal-soft focus-visible:outline-2" onClick={() => setSql({ title: h.name, sql: h.sql })}>{h.name}</button>)}
          </div>
        )}
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Checks findings={d.last_sync.findings} />
        <ParityCard hasBi={d.bi_views.length > 0} />
      </div>

      <Dialog open={sql !== null} onOpenChange={(o) => !o && setSql(null)} title={sql?.title ?? ""} description="Composed in front of Levigate's named queries as a WITH clause; nothing is created in the warehouse." wide>
        <pre tabIndex={0} aria-label="Mapping SQL" className="max-h-[60vh] overflow-auto rounded-lg bg-mist p-4 text-xs leading-relaxed focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-teal"><code>{sql?.sql}</code></pre>
      </Dialog>
    </div>
  );
}

function Checks({ findings }: { findings: { key: string; status: string; count: number }[] }) {
  const run = useMutation({ mutationFn: () => api<{ summary: Summary; results: CheckResult[] }>("/api/admin/data-source/checks", { method: "POST" }) });
  const results = run.data?.results.slice().sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));
  return (
    <Card className="flex flex-col">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <h2 className="text-base">Data checks</h2>
        <Button size="sm" icon={ListChecks} busy={run.isPending} busyLabel="Checking…" onClick={() => run.mutate()}>Run checks now</Button>
      </div>
      {run.error && <div className="p-4"><ErrorNote error={run.error} /></div>}
      {!results ? (
        <div className="p-4 text-sm text-muted">
          <p>Every sync runs these checks against the client's data. Findings from the last sync:</p>
          {findings.length === 0 ? <p className="mt-2 font-semibold text-success">Nothing to report.</p> : (
            <ul className="mt-2 flex flex-col gap-1.5">
              {findings.map((f) => <li key={f.key} className="flex items-center justify-between gap-3"><span className="font-mono text-xs">{f.key}</span><Badge tone={CHECK_TONE[f.status]}>{f.count.toLocaleString()} · {CHECK_LABEL[f.status]}</Badge></li>)}
            </ul>
          )}
        </div>
      ) : (
        <ul className="divide-y divide-line" aria-label="Data check results">
          {results.map((r) => (
            <li key={r.key} className="flex flex-col gap-1 px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <span className="text-sm font-semibold">{r.title}</span>
                <Badge tone={CHECK_TONE[r.status]}>{r.count === null ? CHECK_LABEL[r.status] : `${r.count.toLocaleString()} · ${CHECK_LABEL[r.status]}`}</Badge>
              </div>
              {r.status !== "pass" && <p className="text-xs text-muted">{r.explanation}</p>}
              <span className="text-[11px] text-subtle">{r.scope === "source" ? "Client tables" : "Levigate relations"} · {r.severity}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ParityCard({ hasBi }: { hasBi: boolean }) {
  const [asked, setAsked] = useState(false);
  const q = useQuery({ queryKey: ["admin-parity"], queryFn: () => api<Parity>("/api/admin/data-source/parity"), enabled: asked });
  return (
    <Card className="flex flex-col">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <h2 className="text-base">Compare with the client's BI</h2>
        <Button size="sm" icon={GitCompareArrows} disabled={!hasBi} busy={q.isFetching} busyLabel="Comparing…" onClick={() => (asked ? void q.refetch() : setAsked(true))}>Compare</Button>
      </div>
      {!hasBi && <p className="p-4 text-sm text-muted">This mapping names no BI views. Add the client's BI views to the mapping to compare Levigate's numbers with theirs.</p>}
      {hasBi && !asked && <p className="p-4 text-sm text-muted">Checks Levigate's expected reimbursement, claim status, days to payment and prescriber volume against the client's own BI views, fill by fill, and explains every difference it can.</p>}
      {q.error && <div className="p-4"><ErrorNote error={q.error} retry={() => void q.refetch()} /></div>}
      {q.data && !q.data.available && <p className="p-4 text-sm text-muted">{q.data.reason}</p>}
      {q.data?.available && (
        <div className="flex flex-col gap-3 p-4">
          <p className="text-sm">
            Fills from {q.data.window.start} to {q.data.window.end}.{" "}
            {q.data.unexplained === 0 ? <strong className="text-success">Every difference is explained.</strong> : <strong className="text-rejected">{q.data.unexplained.toLocaleString()} differences need a look.</strong>}
          </p>
          {q.data.sections.map((s) => (
            <section key={s.key} aria-label={s.title} className="rounded-lg border border-line p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">{s.title}</h3>
                <span className="tabular text-xs text-muted">{s.matched.toLocaleString()} of {s.compared.toLocaleString()} match</span>
              </div>
              {s.mismatched === 0 ? <p className="mt-1 text-xs text-success">Identical.</p> : (
                <ul className="mt-2 flex flex-col gap-2">
                  {s.groups.map((g) => (
                    <li key={g.label} className="text-xs">
                      <div className="flex items-start justify-between gap-2">
                        <span className="font-semibold">{g.label}</span>
                        <Badge tone={g.explained ? "info" : "rejected"}>{g.count.toLocaleString()} · {g.explained ? "Explained" : "Unexplained"}</Badge>
                      </div>
                      <p className="mt-0.5 text-muted">{g.explanation}</p>
                      {g.samples.length > 0 && <p className="mt-0.5 text-subtle">e.g. {g.samples.slice(0, 3).join("; ")}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}
        </div>
      )}
    </Card>
  );
}
