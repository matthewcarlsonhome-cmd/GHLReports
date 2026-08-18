# Reference implementation (not present)

The build spec calls for `ghl_am_brief.py` and `test_mock.py` from
`C:\Users\matth\Desktop\SSP\GHL\ghl-am-brief\` to be copied here as read-only
reference. That path is on Matthew's local machine and was not reachable from
the remote build environment, so the Coverage tracker, GHL client with
retries, weekend wait rule, whole-word exclusion matcher, gate logic, and
mock harness were implemented directly from the spec (see `VERIFICATION.md`,
"Deviations from spec").

To add the reference for future maintainers:

    cp "C:\Users\matth\Desktop\SSP\GHL\ghl-am-brief\ghl_am_brief.py" collector/reference/
    cp "C:\Users\matth\Desktop\SSP\GHL\ghl-am-brief\test_mock.py"   collector/reference/
    git add collector/reference/ && git commit -m "Add ghl-am-brief reference implementation"

If behavior in the reference differs from this collector (especially the
weekend rule or gate logic), the reference is the prior art — reconcile and
note the outcome in VERIFICATION.md.
