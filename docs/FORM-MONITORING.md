# Form monitoring: which forms are live, where they live, and a weekly test

Date: 2026-09-22 · Owner: Matthew Carlson · Status: code on branch
`claude/lucid-gauss-vz1y98`; migrations 0011 and 0012 applied live.

Three questions, in order:

1. Is GHL form history really limited to 30 days? (No, see §1.)
2. Which forms are live, and on which page does each one live? (§2, §3)
3. How do we prove every week that a live form still lands submissions as
   contacts, without setting off the client's automations? (§4)

---

## 1. The "30-day limit" is GHL's default window, not a retention limit

**Source (primary):** GHL's published API spec,
[`GoHighLevel/highlevel-api-docs` → `apps/forms.json`](https://github.com/GoHighLevel/highlevel-api-docs/blob/main/apps/forms.json),
`GET /forms/submissions`:

- `startAt`: "Get submission by starting of this date. By default it will be
  same date of last month (YYYY-MM-DD)."
- `endAt`: "By default it will be current date (YYYY-MM-DD)."

`apps/surveys.json` says the same for `/surveys/submissions`.

**What went wrong.** The nightly per-form check (`fetchers._count_and_latest`,
now replaced) called `/forms/submissions?formId=…&limit=1` **without dates**,
so it only ever saw about the last month. The Aug 25 `SSPFormInventory`
workbook read that correctly as "we only see ~30 days", then drew the wrong
conclusion ("GoHighLevel reports only the last ~30 days"). Both of its
evidence points (last dates stop at 30 days, totals shrink as submissions age
out) are exactly what the default window produces.

**Scale of the error, live data 2026-09-22:** 669 forms and surveys, and not
one with a last submission older than 28 days. 592 were labeled `no_leads`
("never had a lead"), and many of them simply went quiet more than a month
ago.

**Fix (this branch):**

- `collector/form_history.py` always sends `startAt=2018-01-01` (GHL launched
  in 2018) and `endAt=tomorrow`. If GHL ever rejects a wide range (400/422),
  it steps down to 365 days, then to the default, and records which window it
  used. A narrowed window shows in coverage notes and in the report; it is
  never silent.
- The nightly now stores **all-time** totals and the real last-submission
  date.
- New status `dormant`: had submissions, but none in 30+ days (migration
  0011). `silent` keeps its meaning (went quiet in the last 30 days) and stays
  the only status that flags, so the digest gets no new noise.

**Still to confirm live:** that GHL honours a range this wide. GHL documents no
maximum, and the report checks it on every run. Each account row shows
`default_window_total` against `all_time_total` plus the oldest submission
date, and the log ends with a `30-DAY CHECK:` verdict line.

## 2. The report: `form-activity.csv` and friends

Run it on GitHub Actions or on Render:

- **GitHub Actions (preferred).** One-time setup: Settings → Secrets and
  variables → Actions. Add repository secrets `REPORTS_SUPABASE_URL`,
  `REPORTS_SUPABASE_SERVICE_ROLE_KEY` and `REPORTS_COLLECTOR_KEY` (the same
  values Render uses). Then either:
  - Actions → **Reports** → Run workflow (once the workflow is on the default
    branch), or
  - edit `.github/reports-request.txt` on any branch and push.

  The CSVs arrive as the run's `reports-<id>` artifact. The `REPORTS_` prefix
  is deliberate: the unprefixed names would also switch on the nightly
  collector workflow and double-run next to Render.
- **Render.** Set `COLLECTOR_ARGS="--form-activity --form-activity-crawl
  --account-usage"`, Trigger Run, and copy the CSV blocks between the
  `===== … BEGIN/END =====` markers in the log. Clear `COLLECTOR_ARGS` after.

Outputs:

| File | One row per | Key columns |
|---|---|---|
| `form-activity.csv` | form / survey / unlisted form id | `channel`, `status`, `last_submission`, `days_inactive`, `subs_7d/30d/90d/365d/all_time`, `live_page_url`, `still_on_page`, `site_embed_pages`, `hosted_form_url`, `monitoring`, `datadog_candidate`, `channel_evidence` |
| `form-pages.csv` | (form, page URL, source) | `hits`, `last_seen`, `still_embedded` |
| `form-accounts.csv` | subaccount | status counts, 90-day submissions per channel, `unlisted_form_ids`, `default_window_total` against `all_time_total`, `oldest_submission` |

Status buckets use calendar days since the newest real submission: `active`
≤7d, `quiet` 8–30d, `dormant` 31–365d, `stale` >365d, `never`, `unknown`. The
dashboard's nightly status uses business days for alerting. Both exclude form
checks (§4).

`channel` answers "is this a website form or an ads form?":

| Channel | How it's decided |
|---|---|
| Facebook lead ad | Form id receives submissions but isn't in Sites → Forms (Facebook/Instagram instant forms), attributed to Facebook or to no web page |
| Facebook ad form / Google ad form | SSP naming convention (`- FB Ad`, `- GA Ad`); otherwise a campaign subdomain page where most submissions carry Facebook (`fbclid`, `fbc`, facebook utm) or Google (`gclid`/`gbraid`/`wbraid`, paid google utm) click markers |
| Website form | Named `Website …`, or lives on the client's main site. Ad traffic to it is reported in `channel_evidence`, not relabeled |
| Standalone form link | Submitted through GHL's hosted `/widget/form/<id>` link |
| Landing page form | On another page with no clear ad source |

Only the label is kept, never the click ids themselves.

## 3. Where each form lives

GHL never stores where a form is embedded, so the report combines four
sources:

1. **Submission page URLs.** Every web submission carries
   `others.eventData.page.url`. The report reads the newest 100 submissions
   per form, all-time, strips query strings, and ranks pages by recency, then
   by volume. This is what finds most forms, including ones that went quiet
   long ago.
2. **Still there?** With `--form-activity-crawl`, each page seen in the last
   year is re-fetched and checked for the form's id: `still_on_page` = yes/NO.
   NO means the form was removed or replaced (the classic "silent form" cause).
3. **Site crawl.** For forms with no submission history, the account website
   (`subaccounts.tag_config.website`) is crawled for GHL embeds
   (`tools/find_embeds.py`), filling `site_embed_pages`.
4. **Unlisted form ids.** Form ids with submissions in the last 90 days that
   Sites → Forms doesn't list. Usually Facebook lead-ad forms, which live
   inside Facebook, not on a page. On 2026-09-22 this gap was large: Flohr
   logged 197 submissions in 28 days, but only 37 of them came through its
   listed forms, and Hamlin and McKinney had 0 through listed forms.

Known blind spots: embeds injected purely client-side (seen on a Wix site) are
invisible to the raw-HTML crawl. AAA Pools' stored website
(`learn.aaapools.com`) is a dead page; its forms are still found through
source 1.

## 4. Weekly synthetic check (Datadog) that lands in Contacts without running workflows

### 4.1 What to test

- Test forms with `datadog_candidate = yes`: lead-capture forms on a known
  live page with fewer than about 1 real submission a week, where silence
  alone can't tell "broken" from "slow".
- `optional` forms get enough real traffic that the nightly `FORM_WENT_SILENT`
  flag already catches a break within about 3 business days.
- Never test Facebook lead ads (not a web form), utility forms (Unsubscribe,
  A2P opt-in, templates), or forms with a captcha (Datadog can't solve one).
  For captcha forms, keep only step 1 below.

### 4.2 One reserved test identity

| Field | Value | Why |
|---|---|---|
| Email | `formcheck@smallscreenproducer.com` | **Must be a real mailbox or Google Group that archives mail.** Any auto-reply that slips past a guard lands there (a useful tripwire), and bounces would hurt the client's sending reputation. Same address in every subaccount, so GHL updates one test contact per account instead of creating a new one weekly. |
| First / last name | `SSP` / `Test` | The collector already drops any contact named with the whole word "Test" from every lead metric |
| Phone | Only if the form requires it: a number SSP controls | Never a random number; an unguarded workflow could text it |
| Message | "Automated weekly form check by SSP, please ignore" | Anyone who sees it knows what it is |

The collector reads the address from `FORM_CHECK_EMAIL`, defaulting to the
value above. Change it in both places or neither.

### 4.3 Keeping the client's workflows from firing

What GHL offers, as verified:

- The **Form Submitted** trigger's documented filter is "Form is…"
  ([help article](https://help.gohighlevel.com/support/solutions/articles/155000002550-workflow-trigger-form-submitted)).
  The article says the trigger "activates each time the form is submitted" and
  recommends conditions inside the workflow.
- I found no account-wide "exclude this contact from all workflows" setting.
- **DND** on a contact stops messages *to* that contact. It does not stop
  internal notifications to the client's staff or deal creation.

The reliable recipe, done once per guarded workflow in the GHL UI:

1. Make the **first action** an **If/Else**: *Contact email* **is**
   `formcheck@smallscreenproducer.com`.
   - **Yes** branch: optionally *Add tag* `ssp-formcheck`, then end. Nothing
     else runs.
   - **None** branch: the workflow's existing steps, unchanged.
2. Guard every workflow that the tested form can start:
   - its *Form Submitted* workflows,
   - any *Contact Created* workflow,
   - any tag-based workflow the form's own tag triggers.

   The API does not expose triggers, so this is a per-account pass in
   Automation → Workflows.
3. After the first test lands, open the test contact and set **DND: all
   channels**, as a second barrier.
4. Check Settings → Business Profile: **Allow duplicate contact** should be
   off (the default), so weekly checks update one contact.
5. Add the same If/Else to SSP's snapshot workflows, so new accounts arrive
   pre-guarded.

If your workflow builder offers a trigger filter on contact email or tag, it
is simpler than step 1. Confirm in the UI; I could not verify it from GHL's
documentation.

### 4.4 The Datadog browser test (one per form)

This pattern doesn't rely on scripting inside GHL's cross-origin iframe. The
Datadog documentation I could reach doesn't describe interacting with
elements inside a cross-origin iframe, so treat that as unproven. If a
recorded on-page test works in your account, submitting on the page is fine
too.

1. **Start URL = `live_page_url`** from the report.
   - Step: *Assert element present*, user locator
     `//iframe[contains(@src,'/widget/form/<FORM_ID>')]`.
   - For forms rendered inline on GHL funnel pages, use
     `//*[contains(@id,'<FORM_ID>')]` instead.

   This proves the form is still on the page.
2. **Navigate to `hosted_form_url`**
   (`https://api.leadconnectorhq.com/widget/form/<FORM_ID>`).
   - Type the test identity into the fields (user locators such as
     `//input[@name='email']`) and click Submit.
   - *Assert* the form's thank-you text, or the redirect URL.

   This is the same form on the same backend, with the same validation.
   Side benefit: form-check submissions record the hosted URL as their page,
   so they are easy to spot.

Test settings:

- **Frequency:** weekly (`tick_every` 604800). Schedule early Monday, before
  the 05:30 CT nightly.
- **Location:** one managed location. **Retries:** 1.
- **`blockedRequestPatterns`:** block the ad and analytics pixels so a test
  never counts as a conversion in the client's ad accounts:
  `connect.facebook.net`, `facebook.com/tr`, `googletagmanager.com`,
  `google-analytics.com`, `googleadservices.com`, `doubleclick.net`,
  `analytics.tiktok.com`, `bat.bing.com`.
- **Monitor:** notify SSP ops, not the AMs.

Build one test in the recorder, then clone it per form, changing only the two
URLs and the form id. The report's `live_page_url` and `hosted_form_url`
columns are the inputs.

### 4.5 The back-end half: did it land as a contact? (collector, read-only)

- The nightly reads each form's newest submissions and sets form checks
  (email = `FORM_CHECK_EMAIL`) aside:
  - They **never count as activity**, so a dead form stays silent even while
    it is tested weekly.
  - The newest check per form is kept together with its contact id, and
    `GET /contacts/{id}` confirms the contact exists.
- **`FORM_CHECK_MISSED`** (amber) fires when a form whose check landed in the
  last 30 days hasn't had one for 8 days (`form_check_stale_days`), or when a
  check arrived with no contact behind it. A test you retire stops flagging
  after 30 days on its own.
- The split of work: Datadog alerts when the page or form breaks on screen.
  The collector alerts when the page said "thanks" but the CRM got nothing.
  PITs never leave the Supabase Vault; Datadog needs no GHL access.

### 4.6 Scaling without editing every workflow

**Not through the API.** GHL's official spec
([`apps/workflows.json`](https://github.com/GoHighLevel/highlevel-api-docs/blob/main/apps/workflows.json))
has one workflows endpoint:
- `GET /workflows/` (scope `workflows.readonly`), which returns id, name,
  status, version and dates;
- nothing to create or edit workflows, triggers or steps;
- triggers can't even be read, so the API can't tell which workflows a form
  starts.

This tool is also read-only by rule.

What scales instead:

1. **Submit only where it's needed.** A form with real leads in the last 3
   business days is "working" (Forms tab, §5), and those leads already prove
   the whole path. No synthetic submission is needed, and no workflow is
   touched.
2. **Render-only checks for every other live form.** Use step 1 of §4.4 (the
   page still embeds the form), plus a check that `hosted_form_url` loads.
   - No submission means no workflows fire, so no GHL setup is needed.
   - This catches the common failures: embed removed, form deleted or
     unpublished, page broken.
3. **Synthetic submissions for a short list** of low-traffic forms that
   matter. Guard their workflows once in SSP's snapshot source account, then
   use GHL's Snapshot **Push Update** to send the edited workflows to linked
   accounts.
   - Caveat, per
     [GHL's help](https://help.gohighlevel.com/support/solutions/articles/48000982582-load-snapshots-into-existing-sub-account):
     a conflict can be resolved with **Override**, and an override "cannot
     be undone".
   - So push only to accounts whose copies of those workflows are unmodified,
     and hand-edit the rest.
4. **Check "Allow Re-entry".** Per
   [GHL's workflow settings](https://help.gohighlevel.com/support/solutions/articles/48001239875-workflow-settings-overview),
   a contact enters a workflow with re-entry off only once. The weekly test
   reuses one contact per account, so such workflows fire only on the first
   test. The setting isn't readable through the API; check it in the UI.

### 4.7 Rollout order

1. Create the `formcheck@smallscreenproducer.com` group or mailbox.
2. Run the report (§2) and pick the `datadog_candidate = yes` forms for the
   pilot accounts first.
3. Guard those forms' workflows (§4.3).
4. Record one Datadog test, clone it per form, and run each once by hand.
5. The next morning, check that the account's forms show a last form check
   and that `FORM_CHECK_MISSED` is absent.

## 5. The Forms tab (dashboard)

`/forms`, in the top navigation. One section per client account that uses
MLH:
- Accounts the team marked ads only, not in MLH or canceled
  (`subaccounts.mlh_status`), and accounts with no client users, are left
  out.
- The SSP parent is behind a toggle.
- "My accounts" / "All" work as on the portfolio.

Per form:

| Column | Meaning |
|---|---|
| Form, Form ID | GHL name and id; Facebook lead-ad forms appear as "Facebook lead ad form" with their id |
| Type | Website form, Google ad form, Facebook ad form, Facebook lead ad, Standalone form link, Landing page form (§2); "(survey)" for surveys |
| Status | ✓ working (real lead within 3 business days) · ⚠ went quiet (had leads, none since, under 30 days) · ◌ quiet 30+ days · ○ never used · + new |
| 30d / All time | Real submissions; weekly tests never count |
| Last submission / Days quiet | Newest real submission, **all-time**: a form quiet for 200 days shows 200 |
| Lives on | Page of the newest real submission |
| Weekly test | Last synthetic check (§4): ✓ landed, ⚠ overdue, ✗ no contact |

- Every form shows by default, however long it has been quiet. The status
  and type filters are optional.
- The account page's Forms & Surveys card shows the same columns for one
  account.
- The nightly collector writes this data (`form_health`). Everything is
  live-accurate from the first nightly run after the branch is deployed.
  Rows from before that run still reflect GHL's 30-day default: a form quiet
  longer than a month shows as "never used" with no date.
