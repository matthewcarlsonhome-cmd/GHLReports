// embed.ts — helpers for running the dashboard inside a GoHighLevel iframe.
//
// Embedding detection is UI-only (spec 9.1): hide the nav and offer a
// full-view link inside the GHL iframe. No auth logic branches on it.

// Are we rendered inside an iframe (or explicitly asked to act like it via
// ?embed)? Comparing window.self to window.top is the classic check; a
// cross-origin parent makes reading window.top throw, which itself proves we
// are framed — hence the try/catch returning true.
export function isEmbedded(): boolean {
  const inFrame = (() => {
    try {
      return window.self !== window.top;
    } catch {
      return true;
    }
  })();
  return inFrame || new URLSearchParams(window.location.search).has("embed");
}

// The same URL minus the ?embed marker — used for the "Open full view" link
// that breaks out of the iframe into a normal browser tab.
export function fullViewUrl(): string {
  const url = new URL(window.location.href);
  url.searchParams.delete("embed");
  return url.toString();
}

// Optional ?hint=email query param used to prefill the login email input.
export function emailHint(): string {
  const hint = new URLSearchParams(window.location.search).get("hint") ?? "";
  // The hint comes from GHL's {{user.email}} substitution: a convenience only,
  // never trusted — it just prefills the input. The regex is a loose
  // "looks like an email" shape check; real verification is the OTP itself.
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(hint) ? hint : "";
}
