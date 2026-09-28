"""GHL account health collector: the nightly, read-only job behind the dashboard.

Start with main.py (the run sequence and every CLI mode). The layers, bottom
up: ghl_client.py (HTTP, rate limits, token redaction) -> fetchers.py (one
function per endpoint, PII stripped here) -> metrics.py (pure arithmetic) ->
flags.py (thresholds). main.py ties them together and saves results through
store.py (Supabase); form_history.py and lead_channels.py serve the form
checks. digest.py and automation.py are the two outbound paths (Monday email,
GHL webhook). docs/ARCHITECTURE.md has the full picture and
docs/SECURITY-SPEC.md the security rules and open items.
"""
