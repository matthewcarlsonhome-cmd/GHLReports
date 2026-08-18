// useSession.ts — a tiny custom hook that answers "who is signed in right now?".
//
// A "custom hook" is just a function whose name starts with `use` and that
// calls other hooks (useState/useEffect). It lets many components share this
// logic while each gets its own live, re-rendering copy of the state.
//
// Two-part sync strategy:
// 1. getSession() reads the session Supabase persisted in localStorage — an
//    async one-time read, which is why `loading` starts true (we don't yet know
//    whether the user is signed in, and guessing "no" would cause a redirect
//    flash on refresh).
// 2. onAuthStateChange subscribes to every later change — sign-in, sign-out,
//    token refresh, even from another browser tab — and pushes the new session
//    into state, re-rendering every component using this hook.
import type { Session } from "@supabase/supabase-js";
import { useEffect, useState } from "react";

import { supabase } from "./supabase";

export function useSession(): { session: Session | null; loading: boolean } {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  // The empty dependency array [] means this effect runs once when the
  // component mounts; the returned cleanup runs when it unmounts.
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
      setLoading(false);
    });
    // Cleanup: drop the listener so unmounted components don't leak or try to
    // set state after they're gone.
    return () => sub.subscription.unsubscribe();
  }, []);

  return { session, loading };
}
