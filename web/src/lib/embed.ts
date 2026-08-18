// Embedding detection is UI-only (spec 9.1): hide the nav and offer a
// full-view link inside the GHL iframe. No auth logic branches on it.

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

export function fullViewUrl(): string {
  const url = new URL(window.location.href);
  url.searchParams.delete("embed");
  return url.toString();
}

export function emailHint(): string {
  const hint = new URLSearchParams(window.location.search).get("hint") ?? "";
  // The hint comes from GHL's {{user.email}} substitution: a convenience only,
  // never trusted — it just prefills the input.
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(hint) ? hint : "";
}
