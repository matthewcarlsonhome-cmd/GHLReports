"""Admin and report tools that sit next to the nightly collector.

None of these run on the nightly schedule. Each is either a small CLI a
person runs by hand (`python -m collector.tools.<name>`) or a report mode
that collector/main.py exposes (--form-urls, --form-activity,
--account-usage) so Render and GitHub Actions can run it.

  pit.py                  set, rotate, delete or list GHL tokens in Vault
  find_client_contact.py  find a client's contact id in SSP's own account
  form_urls.py            form -> page URLs, from submission data
  form_activity.py        every form: channel, last real lead, where it lives
  account_usage.py        is the client's team working leads in MLH
  find_embeds.py          crawl client websites for GHL form embeds

All of them are read-only against GHL. The ones that talk to Supabase need
the same .env as the collector (service role key plus COLLECTOR_KEY). Their
CSV output can hold client staff names and text typed by site visitors:
keep it out of git and out of public places (docs/SECURITY-SPEC.md).
"""
