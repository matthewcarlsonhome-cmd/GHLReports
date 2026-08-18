import type { ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { fullViewUrl, isEmbedded } from "./lib/embed";
import { supabase } from "./lib/supabase";
import { useSession } from "./lib/useSession";
import { useSnapshotAge } from "./lib/useSnapshotAge";
import Account from "./pages/Account";
import Login from "./pages/Login";
import Portfolio from "./pages/Portfolio";
import Runs from "./pages/Runs";

function SnapshotBanner() {
  const banner = useSnapshotAge();
  if (!banner) return null;
  return (
    <div className="border-b border-grid bg-plane px-4 py-1 text-xxs text-ink-2">{banner}</div>
  );
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { session, loading } = useSession();
  const location = useLocation();
  if (loading) {
    return <div className="p-6 text-sm text-muted">Loading…</div>;
  }
  if (!session) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return <>{children}</>;
}

function Nav() {
  const { session } = useSession();
  const embedded = isEmbedded();

  if (embedded) {
    return (
      <div className="flex items-center justify-end border-b border-grid bg-surface px-3 py-1">
        <a
          href={fullViewUrl()}
          target="_blank"
          rel="noreferrer"
          className="text-xxs text-series underline underline-offset-2"
        >
          Open full view ↗
        </a>
      </div>
    );
  }
  return (
    <nav className="flex items-center gap-4 border-b border-grid bg-surface px-4 py-2">
      <Link to="/" className="text-sm font-semibold text-ink">
        Account Health
      </Link>
      <Link to="/" className="text-xs text-ink-2 hover:text-ink">
        Portfolio
      </Link>
      <Link to="/runs" className="text-xs text-ink-2 hover:text-ink">
        Runs
      </Link>
      <div className="ml-auto flex items-center gap-3">
        {session?.user?.email ? <span className="text-xxs text-muted">{session.user.email}</span> : null}
        {session ? (
          <button
            onClick={() => void supabase.auth.signOut()}
            className="text-xxs text-ink-2 underline underline-offset-2 hover:text-ink"
          >
            Sign out
          </button>
        ) : null}
      </div>
    </nav>
  );
}

export default function App() {
  const { session } = useSession();
  return (
    <div className="min-h-screen">
      <Nav />
      {session ? <SnapshotBanner /> : null}
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <Portfolio />
            </RequireAuth>
          }
        />
        <Route
          path="/account/:locationId"
          element={
            <RequireAuth>
              <Account />
            </RequireAuth>
          }
        />
        <Route
          path="/runs"
          element={
            <RequireAuth>
              <Runs />
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
