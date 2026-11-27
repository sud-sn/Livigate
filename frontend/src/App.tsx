import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "@/app/Shell";
import { SignIn } from "@/app/SignIn";
import { LevigateLoader } from "@/design-system/Mark";
import { ToastProvider, TooltipProvider } from "@/design-system/overlays";
import { EmptyState, SkeletonRows } from "@/design-system/primitives";
import { ApiError } from "@/lib/api";
import { AuthProvider, useAuth } from "@/lib/auth";

const MyQueue = lazy(() => import("@/pages/MyQueue"));
const WorkItem = lazy(() => import("@/pages/WorkItem"));
const Claims = lazy(() => import("@/pages/Claims"));
const CallList = lazy(() => import("@/pages/CallList"));
const PrescriberList = lazy(() => import("@/pages/Prescribers").then((m) => ({ default: m.PrescriberList })));
const PrescriberProfile = lazy(() => import("@/pages/Prescribers").then((m) => ({ default: m.PrescriberProfile })));
const Results = lazy(() => import("@/pages/Results"));
const Compliance = lazy(() => import("@/pages/Compliance"));
const Admin = lazy(() => import("@/pages/Admin"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (n, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && n < 2,
    },
  },
});

function Guard({ perm, children }: { perm: string; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(perm)) {
    return <EmptyState title="Your role doesn't include this area." action={{ label: "Go to your home", to: "/" }} />;
  }
  return <>{children}</>;
}

function Home() {
  const { can } = useAuth();
  if (can("queue.view")) return <MyQueue />;
  if (can("compliance.view")) return <Navigate to="/compliance" replace />;
  return <Navigate to="/results" replace />;
}

function Routed() {
  const { me, loading } = useAuth();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <LevigateLoader />
      </div>
    );
  }
  if (!me) return <SignIn />;
  return (
    <Suspense fallback={<SkeletonRows rows={8} />}>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Home />} />
          <Route path="work/:id" element={<Guard perm="claims.view"><WorkItem /></Guard>} />
          <Route path="claims" element={<Guard perm="claims.view"><Claims /></Guard>} />
          <Route path="outreach" element={<Guard perm="outreach.view"><CallList /></Guard>} />
          <Route path="prescribers" element={<Guard perm="prescribers.view"><PrescriberList /></Guard>} />
          <Route path="prescribers/:id" element={<Guard perm="prescribers.view"><PrescriberProfile /></Guard>} />
          <Route path="results" element={<Guard perm="dashboard.view"><Results /></Guard>} />
          <Route path="compliance" element={<Guard perm="compliance.view"><Compliance /></Guard>} />
          <Route path="admin" element={<Guard perm="admin.users"><Admin /></Guard>} />
          <Route path="*" element={<EmptyState title="We couldn't find that page." action={{ label: "Back to your queue", to: "/" }} />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <ToastProvider>
          <BrowserRouter>
            <AuthProvider>
              <Routed />
            </AuthProvider>
          </BrowserRouter>
        </ToastProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
