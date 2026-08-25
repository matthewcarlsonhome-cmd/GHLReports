"""HTTP client for the GHL API v2 (services.leadconnectorhq.com).

Read-only by design: only GET and the documented search POSTs are issued.
Token bucket at 8 req/s per token; 429 honors Retry-After (else 2^n capped
30 s) and repeated 429s halve the rate for the rest of the location.
The Authorization header must never leak into exceptions, logs, coverage
notes, or --probe output; sanitize() enforces that.

How this fits in:
    This is the bottom layer of the collector. fetchers.py creates one
    GHLClient per API token and funnels every network call through
    GHLClient.request(); everything above (fetchers, metrics, flags) can
    then pretend "the network already happened" and just work with dicts.

Key ideas to understand this file:
  * Rate limit / token bucket: the API only allows so many requests per
    second. A token bucket is a small counter that refills continuously at
    a fixed rate (here 8 "tokens" per second, one token = one request).
    Each request spends a token; when the bucket is empty we sleep until
    one accrues. This smooths our traffic instead of firing requests as
    fast as Python can.
  * HTTP 429 ("Too Many Requests"): the server telling us to slow down.
    We honor its Retry-After header (the server's suggested wait, in
    seconds) when present; otherwise we use exponential backoff — wait
    2, 4, 8... seconds between attempts, capped at 30.
  * Bearer token: the secret API key, sent on every request as an
    `Authorization: Bearer <token>` header. If that string ever appeared
    in an exception message it could end up in logs, tracebacks, or the
    dashboard — so every error string is passed through sanitize() first.
"""

from __future__ import annotations

import time

import requests

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"

# Scope hint per path prefix, used in PermissionError messages so a 401/403
# points straight at the Private Integration checkbox to fix.
SCOPE_HINTS = [
    ("/locations", "locations.readonly"),
    ("/users", "users.readonly"),
    ("/opportunities/pipelines", "opportunities.readonly"),
    ("/opportunities", "opportunities.readonly"),
    ("/contacts", "contacts.readonly"),
    ("/conversations/search", "conversations.readonly"),
    ("/conversations", "conversations/message.readonly"),
    ("/calendars/events", "calendars/events.readonly"),
    ("/calendars", "calendars.readonly"),
    ("/blogs/site", "blogs/list.readonly"),
    ("/blogs/posts", "blogs/post.readonly"),
    ("/social-media-posting", "socialplanner/post.readonly"),
]

# The only POST endpoints we will ever hit. GHL implements a few *search*
# operations as POST (the filter body is too rich for a query string), but
# they are still read-only. Anything else POSTed would be a write — refused.
ALLOWED_POST_PATHS = ("/contacts/search", "/social-media-posting/")


def scope_hint(path: str) -> str:
    """Best-effort guess at the OAuth scope a path needs, for 401/403 messages.

    Matches the first SCOPE_HINTS prefix (list order matters: more specific
    prefixes like /opportunities/pipelines come before /opportunities).
    """
    for prefix, scope in SCOPE_HINTS:
        if path.startswith(prefix):
            return scope
    return "unknown scope"


class GHLError(Exception):
    """Base error. Message is already sanitized (no token)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GHLAuthError(GHLError):
    """401/403 — token missing the scope, or token invalid/revoked."""


class GHLHttpError(GHLError):
    """Non-auth HTTP failure that survived retries."""


class TokenBucket:
    """Client-side rate limiter (see "token bucket" in the module docstring).

    `tokens` is the current spendable balance; it refills at `rate` per
    second, capped at `capacity` so a long idle stretch cannot bank a huge
    burst. time.monotonic() is used instead of time.time() because it never
    jumps backward (wall clocks can, e.g. on NTP corrections).
    """

    def __init__(self, rate_per_s: float, capacity: float | None = None):
        """Start with a full bucket; capacity defaults to one second's worth."""
        self.rate = float(rate_per_s)
        self.capacity = capacity if capacity is not None else float(rate_per_s)
        self.tokens = self.capacity
        self.updated = time.monotonic()

    def acquire(self) -> None:
        """Block until one request's worth of budget is available, then spend it."""
        while True:
            now = time.monotonic()
            # Refill first: credit tokens for the time elapsed since the last
            # check, never exceeding capacity.
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            # Not enough budget yet — sleep roughly until a whole token will
            # have accrued (floor of 10 ms so we never spin in a tight loop).
            time.sleep(max((1.0 - self.tokens) / self.rate, 0.01))

    def halve(self) -> None:
        """Permanently slow this bucket down (floored at 1 req/s).

        Called after repeated 429s: the server has told us twice that we are
        too fast, so we stay slower for the rest of this location's run.
        """
        self.rate = max(self.rate / 2.0, 1.0)


class GHLClient:
    """Thin wrapper around requests.Session that adds auth headers, rate
    limiting, retries, and secret sanitization. One instance per API token
    (each GHL location gets its own token and thus its own rate budget).
    """

    def __init__(self, token: str, rate_per_s: float = 8.0, timeout: float = 30.0):
        """Store the token privately and set up the session + rate bucket.

        requests_made / rate_limited are simple counters for run reports;
        _saw_429 remembers whether we've been throttled once already (the
        second 429 triggers TokenBucket.halve()).
        """
        self._token = token
        self.timeout = timeout
        self.bucket = TokenBucket(rate_per_s)
        self.session = requests.Session()
        self.requests_made = 0
        self.rate_limited = 0
        self._saw_429 = False

    # -- sanitization ---------------------------------------------------

    def sanitize(self, text: str) -> str:
        """Replace the bearer token with ***TOKEN*** anywhere it appears.

        Every string that can leave this module inside an exception passes
        through here, so the secret can never reach logs or reports.
        """
        if self._token and self._token in text:
            text = text.replace(self._token, "***TOKEN***")
        return text

    # -- core request ---------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        max_attempts: int = 4,
    ) -> dict:
        """Issue one API call with rate limiting and retries; returns parsed JSON.

        Raises GHLAuthError on 401/403 and GHLHttpError on anything else
        that survives retries. Never raises with the token in the message.

        The retry ladder, in order of checks below: network errors and 5xx
        retry with exponential backoff; 429 waits (Retry-After if given) and
        gets two extra attempts; 401/403 and other 4xx never retry — they
        would fail identically every time.
        """
        # Enforce the read-only contract before anything touches the network.
        method = method.upper()
        if method not in ("GET", "POST"):
            raise GHLError(f"refusing non-read method {method}")
        if method == "POST" and not any(path.startswith(p) for p in ALLOWED_POST_PATHS):
            raise GHLError(f"refusing POST to {path}: not a documented search endpoint")

        url = BASE_URL + path
        # "Version" is GHL's API-date header (their flavor of API versioning);
        # without it many endpoints reject the call outright.
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Version": API_VERSION,
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        attempt = 0
        while True:
            attempt += 1
            self.bucket.acquire()
            try:
                resp = self.session.request(
                    method, url, params=params, json=json_body, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                # Network-level failure (DNS, timeout, connection reset):
                # retry with exponential backoff, then give up sanitized.
                if attempt < max_attempts:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise GHLHttpError(self.sanitize(f"{method} {path}: {type(exc).__name__}: {exc}")) from None
            self.requests_made += 1

            if resp.status_code == 429:
                # Rate limited. The first 429 just waits; a second one means
                # our budget estimate is wrong, so halve the bucket's rate
                # for the rest of this location.
                self.rate_limited += 1
                if self._saw_429:
                    self.bucket.halve()
                self._saw_429 = True
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
                except ValueError:
                    delay = min(2 ** attempt, 30)
                time.sleep(min(delay, 30))
                if attempt < max_attempts + 2:  # 429s get extra patience
                    continue
                raise GHLHttpError(self.sanitize(f"{method} {path}: rate limited after retries"), 429)

            if resp.status_code in (401, 403):
                # Auth failures never retry — the token won't fix itself.
                # GHL's own message comes first when it says something more
                # specific than "unauthorized" (e.g. "Location is not
                # active", which is an account state, not a token problem);
                # scope_hint() remains the fallback pointer at the likely
                # missing scope.
                api_message = ""
                try:
                    api_message = str((resp.json() or {}).get("message") or "").strip()
                except ValueError:
                    pass
                if api_message and api_message.lower() != "unauthorized":
                    detail = f"API says: {api_message!r}"
                else:
                    detail = f"token lacks `{scope_hint(path)}` or is invalid"
                raise GHLAuthError(
                    self.sanitize(
                        f"{method} {path}: HTTP {resp.status_code} — {detail}"
                    ),
                    resp.status_code,
                )

            if resp.status_code >= 500:
                # Server-side error: usually transient, so retry with backoff.
                if attempt < max_attempts:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise GHLHttpError(self.sanitize(f"{method} {path}: HTTP {resp.status_code}"), resp.status_code)

            if resp.status_code >= 400:
                # Remaining 4xx (bad request, not found, ...): our fault, not
                # transient — fail immediately with a truncated response body.
                body = self.sanitize(resp.text[:300])
                raise GHLHttpError(f"{method} {path}: HTTP {resp.status_code}: {body}", resp.status_code)

            # Success. An empty body is treated as an empty dict; anything
            # else must parse as JSON.
            try:
                return resp.json() if resp.text else {}
            except ValueError:
                raise GHLHttpError(self.sanitize(f"{method} {path}: non-JSON response"), resp.status_code) from None

    def try_request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None
    ) -> tuple[int | None, dict | None, str | None]:
        """Probe helper: returns (status, data, error) instead of raising.

        Used by --probe to test many endpoints and report which succeed,
        without one failure aborting the whole sweep. Exactly one of `data`
        or `error` is non-None.
        """
        try:
            data = self.request(method, path, params=params, json_body=json_body)
            return 200, data, None
        except GHLError as exc:
            return exc.status, None, str(exc)
