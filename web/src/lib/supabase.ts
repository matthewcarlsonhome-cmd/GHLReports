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
//
// Security notes (docs/SECURITY-SPEC.md):
// - VITE_SUPABASE_ANON_KEY must be the anon key. If the service role key were
//   ever pasted into Netlify under this name, it would ship to every browser
//   and bypass RLS for anyone who looked (SEC-16 suggests a fail-fast check).
// - Being signed in is not what grants access: the RLS policies call
//   public.is_staff(), which checks explicit staff grants after migration 0016.
//   Client reports have separate account membership and publication controls.
// - The session (including the long-lived refresh token) sits in this
//   browser's localStorage until sign-out. Access therefore lasts until the
//   Supabase user is deleted or a session time limit set in Supabase Auth
//   runs out (SEC-08).
import { createClient } from "@supabase/supabase-js";

// Embedded browsers may deny localStorage. Keep that session in memory and
// offer the standalone login path instead of crashing or weakening access.
const memory = new Map<string, string>();
const storage = {
  getItem(key: string) { try { return window.localStorage.getItem(key); } catch { return memory.get(key) ?? null; } },
  setItem(key: string, value: string) { memory.set(key, value); try { window.localStorage.setItem(key, value); } catch { /* Memory-only session. */ } },
  removeItem(key: string) { memory.delete(key); try { window.localStorage.removeItem(key); } catch { /* Already inaccessible. */ } },
};
const publicKey = import.meta.env.VITE_SUPABASE_ANON_KEY;
if (publicKey && !publicKey.startsWith('sb_publishable_')) {
  let role = '';
  try { role = JSON.parse(atob(publicKey.split('.')[1].replace(/-/g,'+').replace(/_/g,'/'))).role; } catch { /* Invalid public key fails closed. */ }
  if (role !== 'anon') throw new Error('The dashboard requires a public Supabase key.');
}

// Email-code auth only (spec 9.1): no OAuth, no URL detection, refresh-token
// sessions persisted in localStorage so a device signs in once.
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  publicKey,
  {
    auth: {
      storage,
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
