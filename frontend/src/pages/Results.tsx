import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, HelpCircle, MessageSquareText } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/app/Shell";
import { ChartFrame, ColumnChart, HBarChart, LineChart } from "@/design-system/charts";
import { Tooltip } from "@/design-system/overlays";
import { Button, Card, ErrorNote, Input, SkeletonRows, StatTile } from "@/design-system/primitives";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { count, money0, moneyAxis, pct, shortDate } from "@/lib/format";

interface Metric {
  key: string;
  label: string;
  unit: "usd" | "days" | "pct" | "count";
  value: number | null;
  value_label: string;
  series: { week: string; value: number | null }[];
  source: string;
}
interface Dashboard {
  weeks: string[];
  metrics: Metric[];
  backlog: { value: number; label: string };
  ai_usefulness: { accepted: number; edited: number; discarded: number; rate: number | null };
}

const FMT: Record<Metric["unit"], (v: number) => string> = {
  usd: money0,
  days: (v) => `${v.toFixed(v < 10 ? 1 : 0)} days`,
  pct: pct,
  count: count,
};

function Source({ text }: { text: string }) {
  return (
    <Tooltip content={text}>
      <button className="text-subtle hover:text-ink" aria-label={`Where this number comes from: ${text}`}>
        <HelpCircle className="h-4 w-4" aria-hidden="true" />
      </button>
    </Tooltip>
  );
}

export default function Results() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/api/dashboard?weeks=12") });
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />;
  const d = q.data;
  const metric = (k: string) => d?.metrics.find((m) => m.key === k);
  const series = (m?: Metric) => (m?.series ?? []).map((s) => ({ label: shortDate(s.week), value: s.value }));
  const recovered = metric("dollars_recovered");
  const dtp = metric("days_to_payment");
  const capture = metric("refill_capture_rate");
  const reactivation = metric("prescriber_reactivation");
  return (
    <>
      <PageHeader title="Results" subtitle="Weekly results from logged work items and the warehouse. Every number names its source." />
      {!d ? (
        <Card><SkeletonRows rows={6} /></Card>
      ) : (
        <div className="flex flex-col gap-5">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {[recovered, dtp, capture, reactivation].map((m) =>
              m ? (
                <Card key={m.key} className="flex flex-col gap-1 p-4">
                  <span className="flex items-center justify-between text-[13px] text-muted">{m.label}<Source text={m.source} /></span>
                  <span className="tabular font-display text-2xl font-semibold">{m.value === null ? "—" : FMT[m.unit](m.value)}</span>
                  <span className="text-xs text-subtle">{m.value_label}</span>
                </Card>
              ) : null,
            )}
          </div>
          <div className="grid gap-5 xl:grid-cols-2">
            <Card className="p-5">
              {recovered && (
                <ChartFrame title="Dollars recovered per week" data={series(recovered)} format={money0} caption={recovered.source}>
                  <ColumnChart label="Dollars recovered per week" data={series(recovered)} format={money0} axisFormat={moneyAxis} />
                </ChartFrame>
              )}
            </Card>
            <Card className="p-5">
              {dtp && (
                <ChartFrame title="Median days to payment" data={series(dtp)} format={FMT.days} caption={dtp.source}>
                  <LineChart label="Median days to payment" data={series(dtp)} format={FMT.days} axisFormat={(v) => `${v}`} />
                </ChartFrame>
              )}
            </Card>
            <Card className="p-5">
              {capture && (
                <ChartFrame title="Refill capture rate" data={series(capture)} format={pct} caption={capture.source}>
                  <LineChart label="Refill capture rate" data={series(capture)} format={pct} />
                </ChartFrame>
              )}
            </Card>
            <div className="grid gap-4 sm:grid-cols-2">
              <StatTile label={d.backlog.label} value={count(d.backlog.value)} icon={AlertTriangle} tone="attention" sub="Rejection backlog — down vs baseline is the pilot target" />
              <StatTile
                label="AI drafts used with light or no edits"
                value={d.ai_usefulness.rate === null ? "—" : pct(d.ai_usefulness.rate)}
                sub={`${count(d.ai_usefulness.accepted)} accepted · ${count(d.ai_usefulness.edited)} edited · ${count(d.ai_usefulness.discarded)} discarded`}
              />
              {can("claims.view") && <Ask />}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

interface Answer {
  answer: string | null;
  chart?: { type: "bar"; data: { label: string; value: number }[] } | null;
  source?: string;
  supported_questions?: string[];
  message?: string;
}

function Ask() {
  const [question, setQuestion] = useState("Which payers rejected most this month?");
  const ask = useMutation({ mutationFn: (q: string) => api<Answer>("/api/ask", { method: "POST", body: { question: q } }) });
  const a = ask.data;
  return (
    <Card className="flex flex-col gap-3 p-4 sm:col-span-2">
      <h2 className="flex items-center gap-2 text-base"><MessageSquareText className="h-5 w-5 text-teal" aria-hidden="true" />Ask a question</h2>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (question.trim().length >= 3) ask.mutate(question); }}>
        <Input aria-label="Question" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={300} />
        <Button type="submit" variant="primary" busy={ask.isPending} busyLabel="Answering…">Ask</Button>
      </form>
      {ask.error && <ErrorNote error={ask.error} />}
      {a && (
        <div className="lv-in flex flex-col gap-3">
          {a.answer ? <p className="text-sm font-semibold">{a.answer}</p> : (
            <div className="text-sm text-muted">
              <p>{a.message}</p>
              <ul className="mt-1 list-disc pl-5">{a.supported_questions?.map((s) => (
                <li key={s}><button className="text-teal hover:underline" onClick={() => { setQuestion(s); ask.mutate(s); }}>{s}</button></li>
              ))}</ul>
            </div>
          )}
          {a.chart && a.chart.data.length > 0 && (
            <ChartFrame title="Rejected claims by payer" data={a.chart.data} format={count}>
              <HBarChart label="Rejected claims by payer" data={a.chart.data} format={count} />
            </ChartFrame>
          )}
          {a.source && <p className="text-xs text-subtle">{a.source}</p>}
        </div>
      )}
    </Card>
  );
}
