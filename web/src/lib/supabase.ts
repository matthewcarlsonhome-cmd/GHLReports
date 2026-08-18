// supabase.ts — the single shared Supabase client the whole app imports.
//
// Why exposing these values in the browser is safe: the "anon key" is Supabase's
// *public* key — it is shipped to every visitor by design. It only identifies
// the project; it grants no data access by itself. Actual access control lives
// in Postgres row-level security (RLS): every table has policies that decide,
// per row, what an authenticated (or anonymous) user may read or write. So the
// security boundary is the database's RLS policies, not key secrecy.
//
// import.meta.env.VITE_* — Vite inlines environment variables prefixed with
// VITE_ into the client bundle at build time (from .env files or the host's
// environment). That prefix is a deliberate opt-in marker: only variables meant
// to be public get it.
import { createClient } from "@supabase/supabase-js";

// Email-code auth only (spec 9.1): no OAuth, no URL detection, refresh-token
// sessions persisted in localStorage so a device signs in once.
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY,
  {
    auth: {
      // persistSession: keep the session (JWT + refresh token) in localStorage
      // so a reload or new tab stays signed in.
      persistSession: true,
      // autoRefreshToken: silently swap the short-lived JWT for a new one
      // before it expires, using the long-lived refresh token.
      autoRefreshToken: true,
      // detectSessionInUrl is for magic-link/OAuth redirects that put tokens in
      // the URL. This app verifies a 6-digit code instead, so it's off.
      detectSessionInUrl: false,
    },
  },
);
