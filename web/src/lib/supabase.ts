import { createClient } from "@supabase/supabase-js";

// Email-code auth only (spec 9.1): no OAuth, no URL detection, refresh-token
// sessions persisted in localStorage so a device signs in once.
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY,
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
    },
  },
);
