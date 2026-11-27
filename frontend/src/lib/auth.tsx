import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, setCsrfToken, SIGNED_OUT_EVENT } from "./api";

export interface Me {
  id: string;
  name: string;
  email: string;
  role: "billing" | "outreach" | "marketing" | "pharmacist" | "admin" | "auditor";
  role_title: string;
  location: string;
  pharmacy_name: string;
  permissions: string[];
  phi_visible: boolean;
  csrf_token: string;
  idle_timeout_minutes: number;
  env: string;
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  notice: string | null;
  can: (permission: string) => boolean;
  signIn: (userId: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const qc = useQueryClient();

  const accept = useCallback((data: Me | null) => {
    setMe(data);
    setCsrfToken(data?.csrf_token ?? "");
  }, []);

  useEffect(() => {
    // Boot from /session (always 200), so a signed-out visit is not treated as an error.
    api<{ me: Me | null; notice?: string }>("/api/auth/session")
      .then((s) => {
        accept(s.me);
        if (s.notice) setNotice(s.notice);
      })
      .catch(() => accept(null))
      .finally(() => setLoading(false));
  }, [accept]);

  useEffect(() => {
    const onSignedOut = (e: Event) => {
      setNotice((e as CustomEvent<string>).detail);
      accept(null);
      qc.clear();
    };
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
  }, [accept, qc]);

  const signIn = useCallback(
    async (userId: string) => {
      const data = await api<Me>("/api/auth/demo-login", { method: "POST", body: { user_id: userId } });
      qc.clear();
      setNotice(null);
      accept(data);
    },
    [accept, qc],
  );

  const signOut = useCallback(async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 401) throw err;
    }
    qc.clear();
    accept(null);
  }, [accept, qc]);

  const can = useCallback((p: string) => Boolean(me?.permissions.includes(p)), [me]);

  return <AuthCtx.Provider value={{ me, loading, notice, can, signIn, signOut }}>{children}</AuthCtx.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

export function useMe(): Me {
  const { me } = useAuth();
  if (!me) throw new Error("not signed in");
  return me;
}
