import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Download, ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { PageHeader } from "@/app/Shell";
import { cn } from "@/design-system/cn";
import { ConfirmDialog, useToast } from "@/design-system/overlays";
import { Button, Card, ClaimStatus, EmptyState, ErrorNote, Field, Input, Select, SkeletonRows } from "@/design-system/primitives";
import { api, download } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { count, money, shortDate } from "@/lib/format";

interface ClaimRow {
  fill_id: string;
  rx_label: string;
  fill_date: string;
  payer_name: string;
  drug_name: string;
  status: string;
  reject_code: string | null;
  reject_message: string | null;
  expected: number;
  paid: number | null;
  location: string;
  patient: { display: string; phone: string } | null;
  work_item_id: number | null;
}
interface ClaimsResponse {
  range: { start: string; end: string };
  counts: Record<string, number>;
  total: number;
  page: number;
  page_size: number;
  rows: ClaimRow[];
}
interface Filters { payers: { id: string; name: string }[]; locations: string[]; statuses: string[] }

const STATUS_LABEL: Record<string, string> = {
  paid: "Paid",
  pending: "Pending",
  rejected: "Rejected",
  reversed: "Reversed",
  unpaid_past_due: "Unpaid past due",
  underpaid: "Underpaid",
};

const iso = (d: Date) => d.toISOString().slice(0, 10);

export default function Claims() {
  const { can } = useAuth();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const today = new Date();
  const start = params.get("start") ?? iso(new Date(today.getTime() - 30 * 86_400_000));
  const end = params.get("end") ?? iso(today);
  const status = params.get("status") ?? "";
  const payer = params.get("payer") ?? "";
  const code = params.get("code") ?? "";
  const location = params.get("location") ?? "";
  const minAmount = params.get("min") ?? "";
  const rx = params.get("rx") ?? "";
  const page = Number(params.get("page") ?? 1);
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    if (k !== "page") next.delete("page");
    setParams(next, { replace: true });
  };

  const query = useMemo(() => {
    const q = new URLSearchParams({ start, end, page: String(page), limit: "50" });
    if (status) q.set("status", status);
    if (payer) q.set("payer_id", payer);
    if (code) q.set("reject_code", code.toUpperCase());
    if (location) q.set("location", location);
    if (minAmount) q.set("min_amount", minAmount);
    if (rx) q.set("rx_number", rx);
    return q.toString();
  }, [start, end, page, status, payer, code, location, minAmount, rx]);

  const filters = useQuery({ queryKey: ["claim-filters"], queryFn: () => api<Filters>("/api/claims/filters"), staleTime: 600_000 });
  const data = useQuery({ queryKey: ["claims", query], queryFn: () => api<ClaimsResponse>(`/api/claims?${query}`), placeholderData: keepPreviousData });
  const [confirmExport, setConfirmExport] = useState(false);
  const [exporting, setExporting] = useState(false);

  const doExport = async () => {
    setExporting(true);
    try {
      const name = await download("/api/exports/claims", { confirm: true, start, end, status: status || undefined, payer_id: payer || undefined }, "levigate-claims.csv");
      toast({ tone: "success", text: `Exported ${name}. The export is watermarked and logged.` });
    } catch (err) {
      toast({ tone: "error", text: (err as Error).message });
    } finally {
      setExporting(false);
      setConfirmExport(false);
    }
  };

  const d = data.data;
  const pages = d ? Math.max(1, Math.ceil(d.total / d.page_size)) : 1;
  return (
    <>
      <PageHeader
        title="Claims"
        subtitle="Every filled Rx from fill to paid. Status follows each payer's payment window in the business catalog."
        actions={can("claims.export") && <Button icon={Download} onClick={() => setConfirmExport(true)}>Export CSV</Button>}
      />
      <Card className="mb-4 grid gap-3 p-4 sm:grid-cols-3 lg:grid-cols-7">
        <Field label="From">{(id) => <Input id={id} type="date" value={start} onChange={(e) => set("start", e.target.value)} />}</Field>
        <Field label="To">{(id) => <Input id={id} type="date" value={end} onChange={(e) => set("end", e.target.value)} />}</Field>
        <Field label="Payer">
          {(id) => (
            <Select id={id} value={payer} onChange={(e) => set("payer", e.target.value)}>
              <option value="">All payers</option>
              {filters.data?.payers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Reject code">{(id) => <Input id={id} value={code} maxLength={4} placeholder="e.g. 79" onChange={(e) => set("code", e.target.value)} />}</Field>
        <Field label="Location">
          {(id) => (
            <Select id={id} value={location} onChange={(e) => set("location", e.target.value)}>
              <option value="">All locations</option>
              {filters.data?.locations.map((l) => <option key={l} value={l}>{l}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Min amount">{(id) => <Input id={id} type="number" min={0} value={minAmount} onChange={(e) => set("min", e.target.value)} />}</Field>
        <Field label="Rx number">{(id) => <Input id={id} value={rx} inputMode="numeric" onChange={(e) => set("rx", e.target.value.replace(/\D/g, ""))} />}</Field>
      </Card>

      <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Filter by status">
        <StatusChip label="All" n={d ? Object.values(d.counts).reduce((a, b) => a + b, 0) : undefined} active={!status} onClick={() => set("status", "")} />
        {Object.entries(STATUS_LABEL).map(([k, label]) => (
          <StatusChip key={k} label={label} n={d?.counts[k]} active={status === k} onClick={() => set("status", k)} />
        ))}
      </div>

      {data.error ? (
        <ErrorNote error={data.error} retry={() => void data.refetch()} />
      ) : (
        <Card className="overflow-hidden">
          {!d ? (
            <SkeletonRows rows={10} cols={7} />
          ) : d.rows.length === 0 ? (
            <EmptyState title="No claims match these filters." action={{ label: "Clear filters", to: "/claims" }} />
          ) : (
            <div role="region" aria-label="Claims" tabIndex={0} className={cn("max-h-[65vh] overflow-auto transition-opacity focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-teal", data.isFetching && "opacity-70")}>
              <table className="w-full min-w-[980px] text-sm">
                <thead className="sticky top-0 z-10 bg-mist text-left text-xs text-muted">
                  <tr>
                    <th className="px-4 py-2.5 font-semibold">Rx</th>
                    <th className="px-4 py-2.5 font-semibold">Filled</th>
                    <th className="px-4 py-2.5 font-semibold">Status</th>
                    <th className="px-4 py-2.5 font-semibold">Payer</th>
                    <th className="px-4 py-2.5 font-semibold">Drug</th>
                    <th className="px-4 py-2.5 font-semibold">Patient</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Expected</th>
                    <th className="px-4 py-2.5 text-right font-semibold">Paid</th>
                    <th className="px-4 py-2.5 font-semibold"><span className="sr-only">Work item</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {d.rows.map((r) => (
                    <tr key={r.fill_id} className="hover:bg-mist">
                      <td className="whitespace-nowrap px-4 py-2.5 font-semibold">{r.rx_label}</td>
                      <td className="whitespace-nowrap px-4 py-2.5 text-muted">{shortDate(r.fill_date)}</td>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          <ClaimStatus status={r.status} />
                          {r.reject_code && <span className="text-xs text-muted">{r.reject_code}</span>}
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-muted">{r.payer_name}</td>
                      <td className="max-w-[220px] truncate px-4 py-2.5" title={r.drug_name}>{r.drug_name}</td>
                      <td className="whitespace-nowrap px-4 py-2.5">{r.patient?.display} <span className="text-subtle">{r.patient?.phone}</span></td>
                      <td className="tabular px-4 py-2.5 text-right">{money(r.expected)}</td>
                      <td className="tabular px-4 py-2.5 text-right">{r.paid === null ? "—" : money(r.paid)}</td>
                      <td className="px-4 py-2.5 text-right">
                        {r.work_item_id && (
                          <Link to={`/work/${r.work_item_id}`} className="inline-flex items-center gap-1 text-xs font-semibold text-teal hover:underline">
                            Work item <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                          </Link>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {d && d.total > 0 && (
            <div className="flex items-center justify-between border-t border-line px-4 py-3 text-sm text-muted">
              <span>{count(d.total)} claims · page {d.page} of {pages}</span>
              <div className="flex gap-2">
                <Button size="sm" disabled={page <= 1} onClick={() => set("page", String(page - 1))}>Previous</Button>
                <Button size="sm" disabled={page >= pages} onClick={() => set("page", String(page + 1))}>Next</Button>
              </div>
            </div>
          )}
        </Card>
      )}
      <ConfirmDialog
        open={confirmExport}
        onOpenChange={setConfirmExport}
        title="Export claims to CSV?"
        description={`The file contains claim identifiers for ${shortDate(start)}–${shortDate(end)}. It is watermarked with your name and the time, and the export is recorded on the audit trail.`}
        confirmLabel="Export"
        busyLabel="Preparing export…"
        busy={exporting}
        onConfirm={() => void doExport()}
      />
    </>
  );
}

function StatusChip({ label, n, active, onClick }: { label: string; n?: number; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "lv-press flex items-center gap-2 rounded-full border px-3 py-1.5 text-[13px] font-semibold",
        active ? "border-teal bg-teal text-[rgb(var(--lv-on-teal))]" : "border-line bg-surface text-muted hover:border-teal/50",
      )}
    >
      {label}
      {n !== undefined && <span className="tabular">{count(n)}</span>}
    </button>
  );
}
