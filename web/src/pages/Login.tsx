// Login.tsx — the sign-in page: a two-stage email OTP (one-time password) flow.
// Stage 1 asks for a work email and sends a 6-digit code; stage 2 verifies the
// code. On success Supabase stores a session (JWT) and we navigate back to
// wherever RequireAuth (App.tsx) originally bounced the user from.
import { FormEvent, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { emailHint } from "../lib/embed";
import { supabase } from "../lib/supabase";

// Seconds before "Resend code" becomes clickable again — stops accidental
// double-sends and spamming the email provider.
const RESEND_COOLDOWN_S = 30;

// Email-code sign-in only, for pre-provisioned staff accounts (spec v3 9.1):
// public sign-up is disabled in Supabase Auth, the domain trigger is the
// backstop, and this flow works identically standalone and in any iframe.
export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  // RequireAuth stashed the page the user originally wanted in router state;
  // fall back to the portfolio ("/") when they came straight to /login.
  const from = (location.state as { from?: { pathname: string; search: string } } | null)?.from;
  const destination = from ? `${from.pathname}${from.search ?? ""}` : "/";

  // Form state. `stage` drives which of the two forms renders; `busy` disables
  // buttons while a request is in flight; notice/error are user feedback.
  const [email, setEmail] = useState(emailHint());
  const [code, setCode] = useState("");
  const [stage, setStage] = useState<"email" | "code">("email");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  // useRef gives direct access to the code <input> DOM node so we can .focus()
  // it — something declarative JSX can't express.
  const codeInput = useRef<HTMLInputElement>(null);

  // Resend-cooldown countdown. Instead of one setInterval, each render whose
  // cooldown > 0 schedules a single 1s timeout that decrements it; the effect
  // then re-runs (cooldown is in its dependency array) and schedules the next
  // tick. The cleanup cancels the pending timeout if the component unmounts.
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  // When the code form appears, put the cursor straight into the code box.
  useEffect(() => {
    if (stage === "code") codeInput.current?.focus();
  }, [stage]);

  // Ask Supabase to email a 6-digit code. shouldCreateUser: false is the
  // client-side half of "no public sign-up" — unknown emails are rejected
  // instead of silently creating an account.
  async function sendCode() {
    setBusy(true);
    setError(null);
    setNotice(null);
    const { error: err } = await supabase.auth.signInWithOtp({
      email: email.trim(),
      options: { shouldCreateUser: false },
    });
    setBusy(false);
    if (err) {
      // Translate Supabase's generic "signups not allowed" wording into a
      // message that tells staff what actually happened and who to ask.
      const message = err.message ?? "";
      const lower = message.toLowerCase();
      if (lower.includes("signups not allowed") || lower.includes("restricted")) {
        setError("Not a registered staff account. Ask Matthew to add you.");
      } else {
        setError(message || "Could not send the code. Try again.");
      }
      return;
    }
    setStage("code");
    setCooldown(RESEND_COOLDOWN_S);
    setNotice("Code sent. Check your email — it expires in 10 minutes.");
  }

  // preventDefault stops the browser's default full-page form submission —
  // the SPA handles the submit itself.
  async function onSubmitEmail(event: FormEvent) {
    event.preventDefault();
    await sendCode();
  }

  // Stage 2: exchange email + code for a session. On success Supabase fires
  // onAuthStateChange (useSession picks it up) and we return to `destination`.
  // `replace: true` swaps /login out of history so Back doesn't revisit it.
  async function onSubmitCode(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const { error: err } = await supabase.auth.verifyOtp({
      email: email.trim(),
      token: code.trim(),
      type: "email",
    });
    setBusy(false);
    if (err) {
      setError(err.message || "That code didn't work. Check it or resend.");
      return;
    }
    navigate(destination, { replace: true });
  }

  return (
    <div className="mx-auto mt-16 max-w-sm px-4">
      <div className="rounded border border-grid bg-surface p-5">
        <h1 className="mb-1 text-base font-semibold">Account Health</h1>
        <p className="mb-4 text-xs text-ink-2">
          Sign in with your work email. smallscreenproducer.com staff accounts only.
        </p>

        {/* stage switch: email form first, code form after a code was sent */}
        {stage === "email" ? (
          <form onSubmit={onSubmitEmail}>
            <label className="mb-1 block text-xxs uppercase tracking-wide text-muted" htmlFor="email">
              Work email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mb-3 w-full rounded border border-hairline bg-white px-2 py-1.5 text-sm"
              placeholder="you@smallscreenproducer.com"
            />
            <button
              type="submit"
              disabled={busy || !email.trim()}
              className="w-full rounded bg-ink px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {busy ? "Sending…" : "Email me a code"}
            </button>
          </form>
        ) : (
          <form onSubmit={onSubmitCode}>
            <p className="mb-3 text-xs text-ink-2">
              Enter the 6-digit code sent to <span className="font-medium text-ink">{email}</span>.
            </p>
            <label className="mb-1 block text-xxs uppercase tracking-wide text-muted" htmlFor="code">
              Code
            </label>
            {/* inputMode="numeric" brings up the digit keyboard on phones;
                the onChange strips any non-digit characters as they type */}
            <input
              id="code"
              ref={codeInput}
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              className="mb-3 w-full rounded border border-hairline bg-white px-2 py-1.5 text-center font-mono text-lg tracking-[0.4em]"
              placeholder="••••••"
            />
            <button
              type="submit"
              disabled={busy || code.length !== 6}
              className="mb-2 w-full rounded bg-ink px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {busy ? "Checking…" : "Sign in"}
            </button>
            <div className="flex items-center justify-between text-xxs text-muted">
              {/* escape hatch back to stage 1, clearing stage-2 state */}
              <button
                type="button"
                onClick={() => {
                  setStage("email");
                  setCode("");
                  setError(null);
                  setNotice(null);
                }}
                className="underline underline-offset-2"
              >
                Different email
              </button>
              {/* disabled while the cooldown counter is still ticking down */}
              <button
                type="button"
                disabled={cooldown > 0 || busy}
                onClick={() => void sendCode()}
                className="underline underline-offset-2 disabled:no-underline disabled:opacity-60"
              >
                {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
              </button>
            </div>
          </form>
        )}

        {notice ? <p className="mt-3 text-xs text-status-good-text">{notice}</p> : null}
        {error ? <p className="mt-3 text-xs text-status-critical">{error}</p> : null}
      </div>
    </div>
  );
}
