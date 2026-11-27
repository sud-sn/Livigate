import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Wordmark } from "@/design-system/Mark";
import { Button, Card, ErrorNote } from "@/design-system/primitives";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type DemoUser = { id: string; name: string; role: string; title: string; location: string };
type AuthConfig = { demo_login: boolean; sso: boolean; pharmacy_name: string };

const ROLE_NOTE: Record<string, string> = {
  billing: "Claims queue, approvals, follow-ups",
  outreach: "Refill, pickup and balance call lists",
  marketing: "Prescriber CRM and ranked lists",
  pharmacist: "Clinical rejects and DUR reviews",
  admin: "Users, connections, business catalog",
  auditor: "Compliance Center and audit pack",
};

export function SignIn() {
  const { signIn, notice } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api<AuthConfig>("/api/auth/config") });
  const users = useQuery({
    queryKey: ["demo-users"],
    queryFn: () => api<DemoUser[]>("/api/auth/demo-users"),
    enabled: Boolean(config.data?.demo_login),
  });
  const choose = async (id: string) => {
    setBusy(id);
    setError(null);
    try {
      await signIn(id);
    } catch (err) {
      setError(err);
      setBusy(null);
    }
  };
  return (
    <div className="flex min-h-screen items-center justify-center bg-mist px-4 py-10">
      <div className="w-full max-w-3xl">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <Wordmark size={30} />
          <p className="font-display text-xl font-semibold">Every script, smoothed to paid.</p>
          {config.data && <p className="text-sm text-muted">{config.data.pharmacy_name}</p>}
        </div>
        {notice && (
          <div className="mb-4 rounded-md border border-attention/30 bg-attention-soft px-4 py-3 text-sm text-ink" role="status">
            {notice}
          </div>
        )}
        {error ? <div className="mb-4"><ErrorNote error={error} /></div> : null}
        {config.data?.sso && (
          <Card className="flex flex-col items-center gap-4 p-8 text-center">
            <ShieldCheck className="h-8 w-8 text-teal" aria-hidden="true" />
            <p className="text-sm text-muted">Sign in with your organization account. Multi-factor authentication is required.</p>
            <Button variant="primary" size="lg" onClick={() => (window.location.href = "/api/auth/sso/start")}>
              Continue with single sign-on
            </Button>
          </Card>
        )}
        {config.data?.demo_login && (
          <Card className="p-6">
            <h1 className="text-lg">Choose a demo user</h1>
            <p className="mt-1 text-sm text-muted">
              This environment holds synthetic data only. Each user sees exactly what their role allows.
            </p>
            <ul className="mt-5 grid gap-3 sm:grid-cols-2">
              {users.data?.map((u) => (
                <li key={u.id}>
                  <button
                    onClick={() => void choose(u.id)}
                    disabled={busy !== null}
                    className="lv-press flex w-full flex-col items-start gap-0.5 rounded-md border border-line bg-surface px-4 py-3 text-left hover:border-teal/50 hover:bg-teal-soft disabled:opacity-60"
                  >
                    <span className="font-semibold">{u.name}</span>
                    <span className="text-[13px] text-muted">
                      {u.title}{u.location !== "US" ? ` · works from ${u.location} (PHI masked)` : ""}
                    </span>
                    <span className="text-xs text-subtle">{busy === u.id ? "Signing in…" : ROLE_NOTE[u.role]}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}
