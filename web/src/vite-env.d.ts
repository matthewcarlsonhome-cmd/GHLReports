/// <reference types="vite/client" />

// Types for the build-time settings the app reads through import.meta.env
// (see lib/supabase.ts). Vite copies every VITE_* value into the public
// JavaScript bundle, so only values that are safe for anyone to read may be
// listed here: the project URL and the anon (public) key. Never add the
// service role key or any other secret as a VITE_* variable.
interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL: string;
  readonly VITE_SUPABASE_ANON_KEY: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
