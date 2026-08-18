// App.tsx — the app shell: top navigation, the freshness banner, and the route
// table that maps URLs to pages.
//
// Key ideas for reading this file:
// - A react-router <Route> pairs a URL path with the component to render there.
//   ":locationId" in "/account/:locationId" is a URL parameter — the Account
//   page reads it with useParams() to know which account to load.
// - The auth guard (RequireAuth) wraps every protected page. If there is no
//   signed-in session it renders <Navigate to="/login">, which is react-router's
//   declarative redirect: rendering it changes the URL instead of showing UI.
// - "session" is the Supabase auth session (a JWT — a signed token proving who
//   the user is). useSession (lib/useSession.ts) keeps it in React state.
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

// Thin strip under the nav showing how fresh the data is ("Data as of ...").
// useSnapshotAge returns null until it has loaded, so we render nothing then.
function SnapshotBanner() {
  const banner = useSnapshotAge();
  if (!banner) return null;
  return (
    <div className="border-b border-grid bg-plane px-4 py-1 text-xxs text-ink-2">{banner}</div>
  );
}

// Auth guard: only renders its children when a session exists.
// - While the session is still being looked up we show a loading stub instead
//   of redirecting — otherwise a signed-in user would flash to /login on every
//   hard refresh before the stored session was read back.
// - On redirect we stash the current location in navigation state so Login can
//   send the user back to the page they originally asked for.
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

// Top navigation bar. Two very different renders:
// - Embedded (inside a GoHighLevel iframe): the host app already has chrome,
//   so we show only a small "open full view" escape hatch.
// - Standalone: normal nav links plus the signed-in email and a sign-out
//   button. signOut() clears the stored session; onAuthStateChange (in
//   useSession) then fires and RequireAuth redirects to /login.
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

// The route table. Every page except /login sits inside RequireAuth, and the
// "*" catch-all sends unknown URLs back to the portfolio.
export default function App() {
  const { session } = useSession();
  return (
    <div className="min-h-screen">
      <Nav />
      {/* Only show the freshness banner once signed in — it queries the DB. */}
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
