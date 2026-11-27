/**
 * Single-series charts. Marks use the validated chart token (light #008E99, dark #14B3A1); text stays in text
 * tokens; grids are 1 px hairlines; bars are ≤24 px with a 4 px rounded data-end, square at the baseline; lines
 * are 2 px with an 8 px end dot ringed in the surface colour. Every chart has a hover/focus tooltip and a table view.
 */
import { BarChart3, Table2 } from "lucide-react";
import { useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "./cn";

export type Datum = { label: string; value: number | null; detail?: string };

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(600);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(200, Math.floor(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(Math.round(v * 1000) / 1000);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

function roundedTop(x: number, y: number, w: number, h: number, r = 4) {
  if (h <= 0) return "";
  const rr = Math.min(r, h, w / 2);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

function roundedRight(x: number, y: number, w: number, h: number, r = 4) {
  if (w <= 0) return "";
  const rr = Math.min(r, w, h / 2);
  return `M${x},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h - rr}Q${x + w},${y + h} ${x + w - rr},${y + h}H${x}Z`;
}

function TooltipBox({ x, y, children, width }: { x: number; y: number; children: ReactNode; width: number }) {
  const left = Math.min(Math.max(x, 70), width - 70);
  return (
    <div
      className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md bg-ink px-2.5 py-1.5 text-xs text-mist shadow-lg"
      style={{ left, top: y - 8 }}
      role="presentation"
    >
      {children}
    </div>
  );
}

/** Chart + table toggle wrapper. The table view is the accessible equivalent of every chart. */
export function ChartFrame({ title, data, format, children, caption }: {
  title: string;
  data: Datum[];
  format: (v: number) => string;
  children: ReactNode;
  caption?: string;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <figure className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <figcaption className="text-[13px] font-semibold text-muted">{title}</figcaption>
        <div className="flex rounded-md border border-line p-0.5" role="group" aria-label={`${title} view`}>
          {(["chart", "table"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              aria-pressed={view === v}
              aria-label={v === "chart" ? "Show chart" : "Show table"}
              className={cn("lv-press rounded p-1", view === v ? "bg-teal-soft text-teal" : "text-subtle hover:text-ink")}
            >
              {v === "chart" ? <BarChart3 className="h-4 w-4" aria-hidden="true" /> : <Table2 className="h-4 w-4" aria-hidden="true" />}
            </button>
          ))}
        </div>
      </div>
      {view === "chart" ? (
        children
      ) : (
        <div className="max-h-64 overflow-auto rounded-md border border-line">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-mist text-left text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-semibold">Period</th>
                <th className="px-3 py-2 text-right font-semibold">Value</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.map((d) => (
                <tr key={d.label}>
                  <td className="px-3 py-1.5">{d.label}</td>
                  <td className="tabular px-3 py-1.5 text-right">{d.value === null ? "—" : format(d.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {caption && <p className="text-xs text-subtle">{caption}</p>}
    </figure>
  );
}

const M = { top: 12, right: 12, bottom: 26, left: 48 };

function useKeyIndex(n: number, setIdx: (i: number | null) => void, idx: number | null) {
  return (e: KeyboardEvent) => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const cur = idx ?? (e.key === "ArrowRight" ? -1 : n);
      setIdx(Math.max(0, Math.min(n - 1, cur + (e.key === "ArrowRight" ? 1 : -1))));
    }
    if (e.key === "Escape") setIdx(null);
  };
}

export function ColumnChart({ data, format, axisFormat, height = 200, label }: {
  data: Datum[];
  format: (v: number) => string;
  axisFormat?: (v: number) => string;
  height?: number;
  label: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [idx, setIdx] = useState<number | null>(null);
  const max = Math.max(0, ...data.map((d) => d.value ?? 0));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] || 1;
  const innerW = width - M.left - M.right;
  const innerH = height - M.top - M.bottom;
  const band = innerW / Math.max(1, data.length);
  const barW = Math.min(24, band * 0.6);
  const y = (v: number) => M.top + innerH - (v / top) * innerH;
  const every = Math.max(1, Math.ceil((data.length * 48) / innerW));
  const onKey = useKeyIndex(data.length, setIdx, idx);
  return (
    <div ref={ref} className="relative" tabIndex={0} role="group" aria-label={`${label}. Use left and right arrows to read values.`}
      onKeyDown={onKey} onBlur={() => setIdx(null)}>
      <svg width={width} height={height} aria-hidden="true">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} stroke="rgb(var(--lv-chart-grid))" strokeWidth="1" />
            <text x={M.left - 8} y={y(t) + 4} textAnchor="end" className="fill-subtle text-[11px] tabular">
              {(axisFormat ?? format)(t)}
            </text>
          </g>
        ))}
        {data.map((d, i) => {
          const cx = M.left + band * i + band / 2;
          const v = d.value ?? 0;
          return (
            <g key={d.label} onMouseEnter={() => setIdx(i)} onMouseLeave={() => setIdx(null)}>
              <rect x={M.left + band * i} y={M.top} width={band} height={innerH} fill="transparent" />
              <path d={roundedTop(cx - barW / 2, y(v), barW, M.top + innerH - y(v))} fill="rgb(var(--lv-chart))"
                opacity={idx === null || idx === i ? 1 : 0.55} />
              {i % every === 0 && (
                <text x={cx} y={height - 8} textAnchor="middle" className="fill-subtle text-[11px]">
                  {d.label}
                </text>
              )}
            </g>
          );
        })}
        <line x1={M.left} x2={width - M.right} y1={M.top + innerH} y2={M.top + innerH} stroke="rgb(var(--lv-line))" strokeWidth="1" />
      </svg>
      {idx !== null && data[idx] && (
        <TooltipBox x={M.left + band * idx + band / 2} y={y(data[idx].value ?? 0)} width={width}>
          <span className="font-semibold">{data[idx].label}</span> · {data[idx].value === null ? "no data" : format(data[idx].value as number)}
          {data[idx].detail ? ` · ${data[idx].detail}` : ""}
        </TooltipBox>
      )}
    </div>
  );
}

export function LineChart({ data, format, axisFormat, height = 200, label, yMin }: {
  data: Datum[];
  format: (v: number) => string;
  axisFormat?: (v: number) => string;
  height?: number;
  label: string;
  yMin?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [idx, setIdx] = useState<number | null>(null);
  const values = data.map((d) => d.value).filter((v): v is number => v !== null);
  const max = Math.max(1, ...values);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const bottom = yMin ?? 0;
  const innerW = width - M.left - M.right;
  const innerH = height - M.top - M.bottom;
  const x = (i: number) => M.left + (data.length <= 1 ? innerW / 2 : (innerW * i) / (data.length - 1));
  const y = (v: number) => M.top + innerH - ((v - bottom) / (top - bottom)) * innerH;
  const segments: string[] = [];
  let cur = "";
  data.forEach((d, i) => {
    if (d.value === null) {
      if (cur) segments.push(cur);
      cur = "";
      return;
    }
    cur += `${cur ? "L" : "M"}${x(i)},${y(d.value)}`;
  });
  if (cur) segments.push(cur);
  const lastIdx = data.map((d) => d.value !== null).lastIndexOf(true);
  const every = Math.max(1, Math.ceil((data.length * 48) / innerW));
  const onKey = useKeyIndex(data.length, setIdx, idx);
  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const rel = e.clientX - rect.left;
    setIdx(Math.max(0, Math.min(data.length - 1, Math.round((rel / rect.width) * (data.length - 1)))));
  };
  return (
    <div ref={ref} className="relative" tabIndex={0} role="group" aria-label={`${label}. Use left and right arrows to read values.`}
      onKeyDown={onKey} onBlur={() => setIdx(null)}>
      <svg width={width} height={height} aria-hidden="true">
        {ticks.filter((t) => t >= bottom).map((t) => (
          <g key={t}>
            <line x1={M.left} x2={width - M.right} y1={y(t)} y2={y(t)} stroke="rgb(var(--lv-chart-grid))" strokeWidth="1" />
            <text x={M.left - 8} y={y(t) + 4} textAnchor="end" className="fill-subtle text-[11px] tabular">
              {(axisFormat ?? format)(t)}
            </text>
          </g>
        ))}
        {data.map((d, i) =>
          i % every === 0 ? (
            <text key={d.label} x={x(i)} y={height - 8} textAnchor="middle" className="fill-subtle text-[11px]">
              {d.label}
            </text>
          ) : null,
        )}
        {idx !== null && <line x1={x(idx)} x2={x(idx)} y1={M.top} y2={M.top + innerH} stroke="rgb(var(--lv-subtle))" strokeWidth="1" />}
        {segments.map((p) => (
          <path key={p} d={p} fill="none" stroke="rgb(var(--lv-chart))" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        ))}
        {lastIdx >= 0 && (
          <circle cx={x(lastIdx)} cy={y(data[lastIdx].value as number)} r="4" fill="rgb(var(--lv-chart))" stroke="rgb(var(--lv-surface))" strokeWidth="2" />
        )}
        {idx !== null && data[idx]?.value !== null && (
          <circle cx={x(idx)} cy={y(data[idx].value as number)} r="4.5" fill="rgb(var(--lv-chart))" stroke="rgb(var(--lv-surface))" strokeWidth="2" />
        )}
        <rect x={M.left} y={M.top} width={innerW} height={innerH} fill="transparent" onMouseMove={onMove} onMouseLeave={() => setIdx(null)} />
      </svg>
      {idx !== null && data[idx] && (
        <TooltipBox x={x(idx)} y={data[idx].value === null ? M.top + innerH / 2 : y(data[idx].value as number)} width={width}>
          <span className="font-semibold">{data[idx].label}</span> · {data[idx].value === null ? "not measurable yet" : format(data[idx].value as number)}
        </TooltipBox>
      )}
    </div>
  );
}

export function HBarChart({ data, format, label }: { data: Datum[]; format: (v: number) => string; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [idx, setIdx] = useState<number | null>(null);
  const labelW = Math.min(190, width * 0.38);
  const valueW = 56;
  const row = 30;
  const barH = 18;
  const max = Math.max(1, ...data.map((d) => d.value ?? 0));
  const innerW = width - labelW - valueW;
  return (
    <div ref={ref} className="relative" role="img" aria-label={`${label}: ${data.map((d) => `${d.label} ${d.value}`).join(", ")}`}>
      <svg width={width} height={data.length * row + 4} aria-hidden="true">
        {data.map((d, i) => {
          const w = ((d.value ?? 0) / max) * innerW;
          const yy = i * row + (row - barH) / 2;
          return (
            <g key={d.label} onMouseEnter={() => setIdx(i)} onMouseLeave={() => setIdx(null)}>
              <rect x={0} y={i * row} width={width} height={row} fill="transparent" />
              <text x={labelW - 10} y={yy + barH / 2 + 4} textAnchor="end" className="fill-ink text-[12px]">
                {d.label}
              </text>
              <path d={roundedRight(labelW, yy, Math.max(2, w), barH)} fill="rgb(var(--lv-chart))" opacity={idx === null || idx === i ? 1 : 0.55} />
              <text x={labelW + w + 6} y={yy + barH / 2 + 4} className="fill-muted text-[12px] tabular">
                {d.value === null ? "—" : format(d.value)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Sparkline: history in the de-emphasis hue, the current period in the accent. */
export function Sparkline({ values, width = 132, height = 36, label }: { values: number[]; width?: number; height?: number; label: string }) {
  if (values.length === 0) return null;
  const max = Math.max(1, ...values);
  const x = (i: number) => 4 + ((width - 8) * i) / Math.max(1, values.length - 1);
  const y = (v: number) => height - 4 - (v / max) * (height - 8);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
  const last = values.length - 1;
  return (
    <svg width={width} height={height} role="img" aria-label={label}>
      <path d={d} fill="none" stroke="rgb(var(--lv-subtle))" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.7" />
      <circle cx={x(last)} cy={y(values[last])} r="4" fill="rgb(var(--lv-chart))" stroke="rgb(var(--lv-surface))" strokeWidth="2" />
    </svg>
  );
}
