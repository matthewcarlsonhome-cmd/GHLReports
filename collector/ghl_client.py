"""HTTP client for the GHL API v2 (services.leadconnectorhq.com).

Read-only by design: only GET and the documented search POSTs are issued.
Token bucket at 8 req/s per token; 429 honors Retry-After (else 2^n capped
30 s) and repeated 429s halve the rate for the rest of the location.
The Authorization header must never leak into exceptions, logs, coverage
notes, or --probe output; sanitize() enforces that.
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
    ("/invoices", "invoices.readonly"),
    ("/blogs/site", "blogs/list.readonly"),
    ("/blogs/posts", "blogs/post.readonly"),
    ("/social-media-posting", "socialplanner/post.readonly"),
]

ALLOWED_POST_PATHS = ("/contacts/search", "/social-media-posting/")


def scope_hint(path: str) -> str:
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
    def __init__(self, rate_per_s: float, capacity: float | None = None):
        self.rate = float(rate_per_s)
        self.capacity = capacity if capacity is not None else float(rate_per_s)
        self.tokens = self.capacity
        self.updated = time.monotonic()

    def acquire(self) -> None:
        while True:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            time.sleep(max((1.0 - self.tokens) / self.rate, 0.01))

    def halve(self) -> None:
        self.rate = max(self.rate / 2.0, 1.0)


class GHLClient:
    def __init__(self, token: str, rate_per_s: float = 8.0, timeout: float = 30.0):
        self._token = token
        self.timeout = timeout
        self.bucket = TokenBucket(rate_per_s)
        self.session = requests.Session()
        self.requests_made = 0
        self.rate_limited = 0
        self._saw_429 = False

    # -- sanitization ---------------------------------------------------

    def sanitize(self, text: str) -> str:
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
        """
        method = method.upper()
        if method not in ("GET", "POST"):
            raise GHLError(f"refusing non-read method {method}")
        if method == "POST" and not any(path.startswith(p) for p in ALLOWED_POST_PATHS):
            raise GHLError(f"refusing POST to {path}: not a documented search endpoint")

        url = BASE_URL + path
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
                if attempt < max_attempts:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise GHLHttpError(self.sanitize(f"{method} {path}: {type(exc).__name__}: {exc}")) from None
            self.requests_made += 1

            if resp.status_code == 429:
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
                raise GHLAuthError(
                    self.sanitize(
                        f"{method} {path}: HTTP {resp.status_code} — token lacks `{scope_hint(path)}` or is invalid"
                    ),
                    resp.status_code,
                )

            if resp.status_code >= 500:
                if attempt < max_attempts:
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise GHLHttpError(self.sanitize(f"{method} {path}: HTTP {resp.status_code}"), resp.status_code)

            if resp.status_code >= 400:
                body = self.sanitize(resp.text[:300])
                raise GHLHttpError(f"{method} {path}: HTTP {resp.status_code}: {body}", resp.status_code)

            try:
                return resp.json() if resp.text else {}
            except ValueError:
                raise GHLHttpError(self.sanitize(f"{method} {path}: non-JSON response"), resp.status_code) from None

    def try_request(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None
    ) -> tuple[int | None, dict | None, str | None]:
        """Probe helper: returns (status, data, error) instead of raising."""
        try:
            data = self.request(method, path, params=params, json_body=json_body)
            return 200, data, None
        except GHLError as exc:
            return exc.status, None, str(exc)
