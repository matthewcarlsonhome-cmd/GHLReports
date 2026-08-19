"""Tag/pixel monitoring service (docs/FORMS-INTEGRATION.md Phase 2).

Separate from the collector on purpose: this service needs a headless
Chromium browser (Playwright), which is a heavyweight dependency the nightly
collector should never carry. It reads NO GHL data and holds NO GHL tokens —
its only inputs are each client's public website (from subaccounts.tag_config)
and its only output is rows in the tag_checks table.
"""
