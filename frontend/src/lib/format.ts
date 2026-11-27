const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export const money = (v: number) => usd.format(v);
export const money0 = (v: number) => usd0.format(v);
export const moneyAxis = (v: number) => (Math.abs(v) >= 10_000 ? `$${compact.format(v)}` : usd0.format(v));
export const count = (v: number) => v.toLocaleString("en-US");
export const pct = (v: number) => `${Math.round(v)}%`;
export const signedPct = (v: number) => `${v > 0 ? "+" : ""}${Math.round(v * 100)}%`;

function parse(value: string): Date {
  // Bare dates are business dates: render them without shifting through UTC.
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date(value);
}

export const shortDate = (value: string) => parse(value).toLocaleDateString("en-US", { month: "short", day: "numeric" });
export const longDate = (value: string) =>
  parse(value).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
export const dateTime = (value: string) =>
  parse(value).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export function relativeDays(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

export const LIST_LABELS: Record<string, string> = {
  refill_due: "Refill due",
  adherence_gap: "Adherence gap",
  ready_for_pickup: "Ready for pickup",
  balance_due: "Balance due",
};

/** US phone for display: "7085550124" → "(708) 555-0124". Anything else is shown as stored. */
export function phone(value: string | null | undefined): string {
  const digits = (value ?? "").replace(/\D/g, "");
  if (digits.length !== 10) return value ?? "No number on file";
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
}
export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
