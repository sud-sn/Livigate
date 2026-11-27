import { useQuery } from "@tanstack/react-query";
import { BarChart3, Keyboard, LogOut, Moon, Search, Settings2, Sun } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { cn } from "@/design-system/cn";
import { PharmacyIcon, type PharmacyIconName } from "@/design-system/icons";
import { Wordmark } from "@/design-system/Mark";
import { Dialog } from "@/design-system/overlays";
import { Kbd } from "@/design-system/primitives";
import { api } from "@/lib/api";
import { useAuth, useMe } from "@/lib/auth";
import { shortDate } from "@/lib/format";
import { SHORTCUTS, useShortcuts } from "@/lib/keys";

type NavItem = { to: string; label: string; perm: string; icon: PharmacyIconName | "results" | "admin"; end?: boolean };
const NAV: NavItem[] = [
  { to: "/", label: "My queue", perm: "queue.view", icon: "approve", end: true },
  { to: "/claims", label: "Claims", perm: "claims.view", icon: "claim" },
  { to: "/outreach", label: "Call lists", perm: "outreach.view", icon: "call" },
  { to: "/prescribers", label: "Prescribers", perm: "prescribers.view", icon: "prescriber" },
  { to: "/results", label: "Results", perm: "dashboard.view", icon: "results" },
  { to: "/compliance", label: "Compliance", perm: "compliance.view", icon: "compliance" },
  { to: "/admin", label: "Admin", perm: "admin.users", icon: "admin" },
];

function NavIcon({ icon }: { icon: NavItem["icon"] }) {
  if (icon === "results") return <BarChart3 className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />;
  if (icon === "admin") return <Settings2 className="h-5 w-5" strokeWidth={1.75} aria-hidden="true" />;
  return <PharmacyIcon name={icon} size={20} />;
}

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    try {
      return (localStorage.getItem("lv-theme") as "light" | "dark") || "light";
    } catch {
      return "light";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("lv-theme", theme);
    } catch {
      /* storage unavailable: theme still applies for this visit */
    }
  }, [theme]);
  return [theme, setTheme] as const;
}

interface SearchResults {
  rx: { rx_label: string; fill_date: string; drug_name: string; work_item_id: number | null }[];
  patients: { patient: { patient_id: string; display: string; phone: string } }[];
  prescribers: { id: string; name: string; practice: string }[];
}

function TopSearch({ inputRef }: { inputRef: React.RefObject<HTMLInputElement> }) {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const term = q.trim();
  const { data, isFetching } = useQuery({
    queryKey: ["search", term],
    queryFn: ({ signal }) => api<SearchResults>(`/api/search?q=${encodeURIComponent(term)}`, { signal }),
    enabled: term.length >= 2 && can("search"),
    staleTime: 30_000,
  });
  const go = (to: string) => {
    setOpen(false);
    setQ("");
    navigate(to);
  };
  const empty = data && !data.rx.length && !data.patients.length && !data.prescribers.length;
  if (!can("search")) return <div />;
  return (
    <div className="relative w-full max-w-md">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden="true" />
      <input
        ref={inputRef}
        type="search"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => e.key === "Escape" && (e.currentTarget.blur(), setOpen(false))}
        placeholder="Search Rx number, patient ID or prescriber"
        aria-label="Search Rx number, patient ID or prescriber"
        className="h-10 w-full rounded-md border border-line bg-surface pl-9 pr-12 text-sm placeholder:text-subtle hover:border-teal/50"
      />
      <span className="absolute right-3 top-1/2 -translate-y-1/2"><Kbd>/</Kbd></span>
      {open && term.length >= 2 && (
        <div className="lv-in absolute left-0 right-0 top-12 z-30 overflow-hidden rounded-md border border-line bg-surface-raised shadow-xl">
          {isFetching && !data && <p className="px-4 py-3 text-sm text-muted">Searching…</p>}
          {empty && <p className="px-4 py-3 text-sm text-muted">No matches for “{term}”. Try an Rx number like 4471, a patient ID like PT00042, or a prescriber name.</p>}
          {data?.rx.map((r) => (
            <button key={r.rx_label} onMouseDown={(e) => e.preventDefault()} onClick={() => go(r.work_item_id ? `/work/${r.work_item_id}` : `/claims?rx=${r.rx_label.split(" ")[1].split("-")[0]}`)}
              className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-teal-soft">
              <PharmacyIcon name="prescription" size={16} className="text-teal" />
              <span className="font-semibold">{r.rx_label}</span>
              <span className="truncate text-muted">{r.drug_name} · {shortDate(r.fill_date)}</span>
              {r.work_item_id && <span className="ml-auto text-xs font-semibold text-teal">Open work item</span>}
            </button>
          ))}
          {data?.patients.map((p) => (
            <div key={p.patient.patient_id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
              <PharmacyIcon name="patient" size={16} className="text-teal" />
              <span className="font-semibold">{p.patient.patient_id}</span>
              <span className="text-muted">{p.patient.display} · {p.patient.phone}</span>
            </div>
          ))}
          {data?.prescribers.map((p) => (
            <button key={p.id} onMouseDown={(e) => e.preventDefault()} onClick={() => go(`/prescribers/${p.id}`)}
              className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-teal-soft">
              <PharmacyIcon name="prescriber" size={16} className="text-teal" />
              <span className="font-semibold">{p.name}</span>
              <span className="truncate text-muted">{p.practice}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Shell() {
  const me = useMe();
  const { can, signOut } = useAuth();
  const [theme, setTheme] = useTheme();
  const [help, setHelp] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  useShortcuts({ "/": () => searchRef.current?.focus(), "?": () => setHelp(true) });
  const items = NAV.filter((n) => can(n.perm));
  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-surface focus:px-4 focus:py-2">
        Skip to content
      </a>
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-surface md:flex">
        <div className="px-5 py-5">
          <Wordmark size={20} />
        </div>
        <nav aria-label="Modules" className="flex flex-1 flex-col gap-0.5 px-3">
          {items.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                cn(
                  "lv-press flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium",
                  isActive ? "bg-teal-soft text-teal" : "text-muted hover:bg-mist hover:text-ink",
                )
              }
            >
              <NavIcon icon={n.icon} />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-line px-5 py-4 text-xs text-subtle">
          <p className="font-semibold text-muted">{me.pharmacy_name}</p>
          <p>Every script, smoothed to paid.</p>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex items-center gap-4 border-b border-line bg-surface/95 px-4 py-3 backdrop-blur md:px-8">
          <div className="md:hidden"><Wordmark size={18} /></div>
          <TopSearch inputRef={searchRef} />
          <div className="ml-auto flex items-center gap-1">
            {me.env !== "prod" && (
              <span className="mr-2 hidden rounded-full bg-attention-soft px-2.5 py-0.5 text-xs font-semibold text-attention lg:inline">
                Demo · synthetic data
              </span>
            )}
            <IconButton label="Keyboard shortcuts" onClick={() => setHelp(true)}><Keyboard className="h-5 w-5" aria-hidden="true" /></IconButton>
            <IconButton label={theme === "light" ? "Use dark theme" : "Use light theme"} onClick={() => setTheme(theme === "light" ? "dark" : "light")}>
              {theme === "light" ? <Moon className="h-5 w-5" aria-hidden="true" /> : <Sun className="h-5 w-5" aria-hidden="true" />}
            </IconButton>
            <div className="ml-2 hidden text-right leading-tight sm:block">
              <p className="text-sm font-semibold">{me.name}</p>
              <p className="text-xs text-subtle">{me.role_title}{me.location !== "US" ? ` · ${me.location}` : ""}</p>
            </div>
            <IconButton label="Sign out" onClick={() => void signOut()}><LogOut className="h-5 w-5" aria-hidden="true" /></IconButton>
          </div>
        </header>
        <nav aria-label="Modules" className="flex gap-1 overflow-x-auto border-b border-line bg-surface px-3 py-2 md:hidden">
          {items.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => cn("whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium", isActive ? "bg-teal-soft text-teal" : "text-muted")}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <main id="main" className="mx-auto w-full max-w-page flex-1 px-4 py-6 md:px-8">
          <Outlet />
        </main>
      </div>
      <Dialog open={help} onOpenChange={setHelp} title="Keyboard shortcuts">
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2.5 text-sm">
          {SHORTCUTS.map(([k, v]) => (
            <div key={k} className="contents">
              <dt><Kbd>{k}</Kbd></dt>
              <dd className="text-muted">{v}</dd>
            </div>
          ))}
        </dl>
      </Dialog>
    </div>
  );
}

function IconButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button aria-label={label} title={label} onClick={onClick} className="lv-press rounded-md p-2 text-muted hover:bg-teal-soft hover:text-ink">
      {children}
    </button>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[28px] leading-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
