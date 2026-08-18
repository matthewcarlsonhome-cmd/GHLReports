import { FormEvent, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { emailHint } from "../lib/embed";
import { supabase } from "../lib/supabase";

const RESEND_COOLDOWN_S = 30;

// Email-code sign-in only, for pre-provisioned staff accounts (spec v3 9.1):
// public sign-up is disabled in Supabase Auth, the domain trigger is the
// backstop, and this flow works identically standalone and in any iframe.
export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: { pathname: string; search: string } } | null)?.from;
  const destination = from ? `${from.pathname}${from.search ?? ""}` : "/";

  const [email, setEmail] = useState(emailHint());
  const [code, setCode] = useState("");
  const [stage, setStage] = useState<"email" | "code">("email");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const codeInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  useEffect(() => {
    if (stage === "code") codeInput.current?.focus();
  }, [stage]);

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

  async function onSubmitEmail(event: FormEvent) {
    event.preventDefault();
    await sendCode();
  }

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
