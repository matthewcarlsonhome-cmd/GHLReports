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
import { type ReactNode, useEffect } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { fullViewUrl, isEmbedded } from "./lib/embed";
import { supabase } from "./lib/supabase";
import { useSession } from "./lib/useSession";
import { useSnapshotAge } from "./lib/useSnapshotAge";
import Account from "./pages/Account";
import Forms from "./pages/Forms";
import Login from "./pages/Login";
import Portfolio from "./pages/Portfolio";
import Runs from "./pages/Runs";
import ClientDashboard from "./pages/ClientDashboard";
import AccessSettings from "./pages/AccessSettings";
import { AccessProvider, useAccess } from "./lib/useAccess";
import { safeDestination } from "./lib/dashboard";

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
// - This is a convenience, not the security boundary: the database's RLS
//   policies decide what any request can read from explicit account grants, so a page
//   rendered without the right login simply gets no rows back.
// - While the session is still being looked up we show a loading stub instead
//   of redirecting — otherwise a signed-in user would flash to /login on every
//   hard refresh before the stored session was read back.
// - On redirect we stash the current location in navigation state so Login can
//   send the user back to the page they originally asked for.
function RequireAuth({ children, client = false, admin = false }: { children: ReactNode; client?: boolean; admin?: boolean }) {
  const { session, loading } = useSession();
  const access = useAccess();
  const location = useLocation();
  if (loading || access.loading) {
    return <div className="p-6 text-sm text-muted">Loading…</div>;
  }
  if (!session) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  if (admin && !access.admin) return <Navigate to="/client" replace />;
  if (!client && !access.staff) return <Navigate to="/client" replace />;
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
  const { staff, admin } = useAccess();
  const { pathname, state } = useLocation();
  const embedded = isEmbedded();
  const clientView = pathname.startsWith('/client') || !staff;
  const returnTo = state?.from;
  const standalone = new URL(pathname === '/login' && returnTo
    ? safeDestination(`${returnTo.pathname}${returnTo.search ?? ''}`) : fullViewUrl(), window.location.origin);
  standalone.searchParams.delete('embed');

  if (embedded || clientView) {
    return (
      <div className="flex items-center justify-end border-b border-grid bg-surface px-3 py-1">
        {session && <button className="mr-4 text-xs underline" onClick={() => void supabase.auth.signOut()}>Sign out</button>}
        <a
          href={standalone.toString()}
          target="_blank"
          rel="noreferrer"
          className="text-xxs text-series underline underline-offset-2"
        >
          Open secure report in a new tab ↗
        </a>
      </div>
    );
  }
  return (
    <nav className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-grid bg-surface px-4 py-2">
      <Link to="/" className="text-sm font-semibold text-ink">
        Account Health
      </Link>
      <Link to="/" className="text-xs text-ink-2 hover:text-ink">
        Accounts
      </Link>
      <Link to="/forms" className="text-xs text-ink-2 hover:text-ink">
        Forms
      </Link>
      <Link to="/runs" className="text-xs text-ink-2 hover:text-ink">
        Runs
      </Link>
      {admin && <Link to="/access" className="text-xs text-ink-2">Client access</Link>}
      <div className="ml-auto flex max-w-full items-center gap-3">
        {session?.user?.email ? <span className="break-all text-xxs text-muted">{session.user.email}</span> : null}
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
  return <AccessProvider><AppContent /></AccessProvider>;
}
function AppContent() {
  const { session } = useSession();
  const access = useAccess();
  const { pathname } = useLocation();
  // Opening an account should start at its priorities, even from a long list.
  useEffect(() => { window.scrollTo(0, 0); }, [pathname]);
  return (
    <div className="min-h-screen">
      <Nav />
      {/* Only show the freshness banner once signed in — it queries the DB. */}
      {session && access.staff && !pathname.startsWith('/client') ? <SnapshotBanner /> : null}
      <Routes key={session?.user.id ?? 'signed-out'}>
        <Route path="/login" element={<Login />} />
        <Route path="/client" element={<RequireAuth client><ClientDashboard /></RequireAuth>} />
        <Route path="/client/accounts/:locationId" element={<RequireAuth client><ClientDashboard /></RequireAuth>} />
        <Route path="/access" element={<RequireAuth admin><AccessSettings /></RequireAuth>} />
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
          path="/forms"
          element={
            <RequireAuth>
              <Forms />
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
