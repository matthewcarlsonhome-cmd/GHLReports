# Chrome Handoff Build Spec — GoHighLevel Master Sub-Account

**Purpose.** This is the executable spec for an agent driving a Chrome session
already logged into a GoHighLevel sub-account. It builds the Wave 1 Dealer
Growth Engine master account, then produces a gaps report.

> **Where the code lives.** Every `src/…` and `email/…` path in this document is
> relative to the **Evosus** repo — `github.com/matthewcarlsonhome-cmd/evosus`,
> branch `claude/ssp-evosus-lou-integration-gkbg1m`. This copy sits in GHLReports
> so it reads alongside the other partnership documents; the canonical copy is
> `docs/CHROME-BUILD-SPEC.md` in the Evosus repo.

**Read this first, in full, before the first click.** Half of this document is
about what *not* to do.

---

## Status — updated after Eric's 1 Sep update

Three things changed, and they change how this spec is run:

1. **Eric is rebuilding the sub-account.** The account we have been testing in is
   an old one full of stale data. Eric is standing up a clean one and shaping it
   into the Evosus template that every dealer gets cloned from. **Do not build
   into the current test account.** This spec runs against Eric's new account,
   after he hands it over.
2. **The data layer already partly exists.** Orders flow into MLH, customer
   records are created, and purchased products are recorded and linked to the
   customer. So Eric has already created objects and fields in that account.
   Phase A can no longer assume an empty slate — see **Phase 0**, which is new
   and mandatory.
3. **Ownership is settled.** Eric owns the pipe and the account shell. Everything
   downstream — workflows, templates, content automation — is Matthew's. Phase J
   (snapshot) is Eric's, not a gap.

**Blocked upstream:** the LOU `orders-by-date` endpoint is not working yet
(John's team is on it). That gates *live* firing, not this build. Everything in
this spec can be built and verified against sample data.

> **A note on names.** "MLH" in Eric's updates is the GoHighLevel instance. This
> document says GoHighLevel throughout; they are the same system.

---

## 0. Preflight — do all of this before any write

Take a screenshot at each step. If any check fails, **stop and report**; do not
proceed on assumption.

| # | Check | Pass condition | If it fails |
|---|---|---|---|
| P1 | Read the sub-account name in the top-left switcher | Matches the name Matthew gave for this session | STOP. Wrong account is the one unrecoverable mistake here. |
| P2 | Navigate to Contacts | Zero contacts, **or** only records Eric's sample-data load created | STOP and ask if there are contacts that look like a real dealer's customers. |
| P3 | Navigate to Automation → Workflows | Empty, or only items already prefixed `[SSP-CORE]` | STOP if unrelated workflows exist. |
| P2b | Confirm this is Eric's **new** account, not the old test account | Eric has confirmed handover | STOP. Building into the old account wastes the work — it is being discarded. |
| P4 | Navigate to Settings → Business Profile | Confirm this is the intended master, not a client | STOP if it carries a real dealer's name. |
| P5 | Check Settings → Phone Numbers and Email Services | Note whether sending is configured | Record in the gaps report. Do not configure. |
| P6 | Confirm plan supports Custom Objects (Settings → Objects, or Custom Objects in the left nav) | Present | Record as a gap; Wave 1 does not require them. |

Record the answers to P5 and P6 — they belong in the gaps report regardless.

---

## 1. Standing rules

**Never, under any circumstances:**

1. Publish, enable, or set live any workflow. Every workflow is left in **Draft**.
2. Send any test email or SMS, to anyone, including yourself.
3. Delete or rename an object you did not create in this session.
4. Modify another sub-account, or switch accounts.
5. Change billing, domains, phone numbers, email sending, or user permissions.
6. Import contacts.
7. Accept a browser prompt to "publish", "go live", "purchase", or "upgrade".

**Always:**

- **Check before you create.** Every object type has a list view. Read it first;
  if an object with the target name exists, skip it and note "already present"
  rather than creating a duplicate. GHL does not enforce uniqueness — a re-run
  without this check produces 27 duplicate fields.
- **Verify by reading back.** After creating a batch, return to the list view and
  compare against the manifest. "I clicked Save" is not verification.
- **Prefix everything** `[SSP-CORE]` where a name field allows it (workflows,
  templates, funnels, forms). Custom fields, tags, and custom values use the
  exact keys in this spec, unprefixed, because code depends on them.
- **Log as you go.** Maintain the build log in §8 continuously, not at the end.
  If the session dies, the log is the deliverable.
- **Screenshot each phase boundary** for the gaps report.

**Stop and ask when:**

- The same interaction fails 3 times.
- The UI does not match this spec (GHL ships changes frequently).
- An object exists with the right name but different contents.
- Anything would cost money or send a message.
- You are more than ~45 minutes into a single phase.

---

## 2. Build order and realistic expectations

Phases are ordered by dependency **and** by automation reliability, so that if
the session ends early, the most valuable and least-recoverable work is done.

| Phase | Objects | Automation risk | Expect |
|---|---|---|---|
| 0 | Inventory Eric's build | Low | Complete — and it gates everything below |
| A | 27 custom fields | Low | Complete, minus whatever Eric already created |
| B | 19 tags | Low | Complete |
| C | 22 custom values | Low | Complete |
| D | 2 pipelines | Low | Complete |
| E | 8 SMS templates | Low–Med | Complete |
| F | 12 email templates | Medium | Likely complete; verify HTML survived |
| G | 2 forms + 1 calendar | Medium | Partial |
| H | 4 funnel pages | **High** | Shells only |
| I | 6 workflows | **High** | 2–3 of 6 |
| J | Snapshot / template | **Eric's** | Eric is building the Evosus template this clones from. Not our action. |

**Be honest in the report about where you stopped.** A truthful "3 of 6
workflows, here is exactly where I left off" is worth far more than a claim of
completion that Matthew discovers is wrong on Monday.

---

## 2b. Phase 0 — Inventory Eric's build (mandatory, before any write)

Eric's pipe already creates customer records and records purchased products
against them. That means objects and fields exist in this account that this spec
did not create. **Creating a second set alongside them is the most likely way to
wreck this build** — two "Customer ID" fields, one populated by the pipe and one
read by the workflows, fails silently and looks like a content bug for days.

Produce a written inventory before creating anything:

| Read | Record |
|---|---|
| Settings → Custom Fields (all objects) | Every field: name, **key**, type, which object |
| Settings → Custom Objects / Objects | Every custom object, its fields, and its **association to Contact** |
| Settings → Tags | Every existing tag |
| Settings → Custom Values | Every existing value |
| A sample contact created by the pipe | Which fields are actually populated, and with what |

Then reconcile against §3's 27 fields and produce three lists:

- **Same meaning, same key** → do not create. Use Eric's.
- **Same meaning, different key** → **STOP and ask.** This is a code decision:
  either the connector changes to write Eric's key, or Eric's field is renamed.
  Do not create a duplicate to paper over it.
- **Not present** → create per §3.

**How products are stored is the question that matters most.** "Individual
products purchased are recorded and linked to the customer record" can mean a
custom object with an association, or repeating fields on the contact. Find out
which, record the exact structure, and put it at the top of the gaps report.
The signal layer currently writes flat contact fields; if Eric has modelled
purchases as a related object, that is a genuine architecture question for
Matthew and Eric, not something to resolve by clicking.

---

## 3. Phase A — Custom fields (27)

**Path:** Settings → Custom Fields → Add Field. Object: **Contact**.

For each: set the **Name** exactly as given (GHL derives the key from the name;
after creating the first one, confirm the generated key matches the `key` column
— if GHL produces a different key format, **stop and report**, because the
connector writes by key).

| key | Name | Type |
|---|---|---|
| lou_customer_id | LOU Customer ID | Text |
| lou_portal_url | LOU Portal URL | Text |
| lou_balance_due | LOU Balance Due | Monetary |
| first_purchase_date | First Purchase Date | Date |
| last_purchase_date | Last Purchase Date | Date |
| ltv_total | LTV Total | Monetary |
| purchase_count | Purchase Count | Number |
| lifecycle_stage | Lifecycle Stage | Text |
| value_tier | Value Tier | Text |
| reorder_due_date | Reorder Due Date | Date |
| reorder_category | Reorder Category | Text |
| next_offer_product | Next Offer Product | Text |
| next_offer_sku | Next Offer SKU | Text |
| next_offer_url | Next Offer URL | Text |
| next_offer_price | Next Offer Price | Monetary |
| next_offer_code | Next Offer Code | Text |
| next_offer_image | Next Offer Image | Text |
| last_service_date | Last Service Date | Date |
| last_service_type | Last Service Type | Text |
| season_service_type | Season Service Type | Text |
| major_purchase_product | Major Purchase Product | Text |
| major_purchase_date | Major Purchase Date | Date |
| major_purchase_amount | Major Purchase Amount | Monetary |
| category_headline | Category Headline | Text |
| category_body | Category Body | Multi-line Text |
| category_tip | Category Tip | Text |
| category_image | Category Image | Text |

Canonical source: `src/wave1/writer.js` → `WAVE1_FIELDS`.

**Verify:** list view shows 27 fields; spot-check that `category_body` is
multi-line and the three Monetary fields are Monetary.

---

## 4. Phase B — Tags (19)

**Path:** Settings → Tags → Add Tag.

```
lou-import          email-ok            sms-consent         new-customer
balance-due         season-invite       reorder-due         season-booked
service-recovery-open                   chemical-buyer      service-customer
big-ticket-buyer    plan-member         owns:pool           owns:hot-tub
owns:spa            do-not-market       money-ask-active    offer-ready
```

Lowercase exactly as written, including the colons. If GHL rejects `:`, **stop
and report** — the connector's tag names would need changing, which is a code
decision, not a UI workaround.

---

## 5. Phase C — Custom values (22)

**Path:** Settings → Custom Values → Add Custom Value.

Use these placeholder values verbatim. They are deliberately obvious so that
anything unconfigured is visible in a test send rather than silently blank.

| Name | Placeholder value |
|---|---|
| dealer_name | `[[DEALER NAME]]` |
| dealer_city | `[[CITY]]` |
| dealer_phone | `[[PHONE]]` |
| dealer_email | `[[EMAIL]]` |
| dealer_address | `[[STREET, CITY, ST ZIP]]` |
| logo_url | `https://placehold.co/160x40?text=LOGO` |
| brand_color | `#0F6F86` |
| booking_url | `[[BOOKING URL]]` |
| review_url | `[[GOOGLE REVIEW URL]]` |
| review_gateway_url | `[[REVIEW GATEWAY PAGE URL]]` |
| shop_url | `[[SHOP URL]]` |
| water_test_url | `[[WATER TEST BOOKING URL]]` |
| preference_center_url | `[[PREFERENCE CENTER URL]]` |
| offer_reorder | `10% off` |
| offer_service_plan | `[[SERVICE PLAN OFFER]]` |
| offer_seasonal | `[[SEASONAL OFFER]]` |
| season_opening_start | `04-01` |
| season_opening_end | `05-31` |
| season_closing_start | `09-15` |
| season_closing_end | `10-31` |
| notify_email_sales | `[[SALES EMAIL]]` |
| notify_email_service | `[[SERVICE EMAIL]]` |

---

## 6. Phase D — Pipelines (2)

**Path:** Opportunities → Pipelines → Create.

1. **`[SSP-CORE] Purchases — Attributed`** — one stage: `Attributed`
2. **`[SSP-CORE] Seasonal Services`** — stages in order:
   `Invited` → `Requested` → `Scheduled` → `Completed`

Do not enable any pipeline automation prompts.

---

## 7. Phase E–F — Templates

### SMS (8)
**Path:** Marketing → Templates (or Snippets) → SMS → New.
Name each `[SSP-CORE] S1 …` through `S8`. Source: `email/dist/sms.txt` in the
repo. Paste text exactly, including the merge braces.

### Email (12)
**Path:** Marketing → Emails → Templates → New → **Blank / Code / Import HTML**.

Source files: `email/dist/E1-*.html` … `E12-*.html`.

Procedure per template:
1. Create a new template, name it `[SSP-CORE] E1 Welcome` (etc.).
2. Choose the **code / custom HTML** option, not the drag-drop builder.
3. Paste the entire file contents.
4. Set the **subject line** from the table below.
5. Save, then **reopen and confirm** the HTML was not rewritten — specifically
   that `{{contact.…}}` braces survived and the `<table>` structure is intact.

| ID | Subject |
|---|---|
| E1 | Welcome to {{custom_values.dealer_name}}, {{contact.first_name}} |
| E2 | The 5 things that keep {{custom_values.dealer_city}} pools trouble-free |
| E3 | Three things people come back for |
| E4 | How did we do? |
| E5 | Running low on {{contact.next_offer_product}}? |
| E6 | A little something on your {{contact.next_offer_product}} |
| E7 | A quick note about your account |
| E8 | Anything we can help with? |
| E9 | Time to get your {{contact.season_service_type}} on the schedule |
| E10 | Last of the {{contact.season_service_type}} slots |
| E11 | What that visit would have cost on a plan |
| E12 | How did we do on the {{contact.last_service_type}}? |

**If the editor mangles the HTML** (strips tables, escapes braces): stop after
the first template, report it, and do not burn time on the other eleven. That is
a tooling finding worth more than a partial paste job.

---

## 8. Phase G–H — Forms, calendar, pages

Build in this order and stop when reliability drops.

1. **Form: `[SSP-CORE] Seasonal Booking`** — fields: service type (dropdown:
   Opening / Closing), preferred week (date), address (text), notes (textarea).
   On submit: add tag `season-booked`.
2. **Form: `[SSP-CORE] Service Feedback`** — rating (radio 1–5), comments
   (textarea). Used by the unhappy path of the review gateway.
3. **Calendar: `[SSP-CORE] Seasonal Service`** — 60-minute slots, no payment,
   no confirmation SMS configured.
4. **Funnels** (Sites → Funnels): create four funnels, one step each, named
   `[SSP-CORE] Review Gateway`, `Seasonal Booking`, `Quick Reorder`,
   `Preference Center`. **Create the shell only** — a single blank step with the
   name set. Do not attempt to compose page layouts in the drag-drop builder;
   note them in the gaps report as needing manual design.

---

## 9. Phase I — Workflows (6)

Build in this exact order. It is easiest-first on purpose, so partial completion
still leaves working automation.

Each workflow: **Automation → Workflows → Create → Start from Scratch**, name
`[SSP-CORE] W3 Balance-Due Recovery` etc., **leave in Draft**.

Full step-by-step definitions: the Wave 1 Build Specification, §8. Summary:

| Order | Workflow | Trigger (Contact Tag Added) | Steps |
|---|---|---|---|
| 1 | W3 Balance-Due Recovery | `balance-due` | E7 → wait 3d → S5 → wait 6d → E8 → wait 8d → internal task |
| 2 | W1 Reorder Reminder | `reorder-due` | S2 → wait 1d → E5 → wait 3d → S3 → wait 4d → E6 → wait 6d → S4 |
| 3 | W2 Seasonal Booking | `season-invite` | E9 → wait 5d → S7 → wait 7d → E10 → wait 5d → S8 |
| 4 | W4 Post-Service Review | `service-completed` | S6 or E12 → wait 3d → E12 → wait 11d → E11 |
| 5 | W5 New Customer Welcome | `new-customer` | E1 → wait 2d → S1 → wait 5d → E2 → wait 14d → E3 → wait 24d → E4 |
| 6 | U1 Internal Alerts | `service-recovery-open` | internal notification + task |

**Guard pattern, on every send step:** precede it with an If/Else on
"Contact has tag {trigger tag}"; the false branch ends the workflow. This is what
makes a customer who has already acted stop receiving messages. Without it the
whole design fails silently.

**SMS steps:** wrap in an If/Else on "Contact has tag `sms-consent`"; false
branch skips the step and continues.

**Workflow settings for every one:** Allow re-entry **off** (exception: W4, which
allows re-entry after 30 days). Quiet hours 8am–8pm if the setting is available.

---

## 10. Phase J — Snapshot / Evosus template

**Not our action.** Eric is building this account out as the Evosus template that
every dealer is cloned from. Snapshot creation is an agency-level action anyway
(Agency Settings → Snapshots → Create from sub-account), and cannot be done from
a sub-account.

What this phase *does* require from us: the gaps report must tell Eric exactly
what is safe to snapshot and what is not — specifically, that every workflow is
in Draft by design, and every `[[BRACKETED]]` custom value is a per-dealer blank
that must stay a blank in the template. If a placeholder gets filled with the
first dealer's real details before the snapshot is taken, all 100 dealers inherit
them.

---

## 11. Build log (maintain continuously)

| Phase | Object | Action | Verified | Notes |
|---|---|---|---|---|
| A | … | created / skipped-exists / failed | yes/no | … |

---

## 12. Gaps report — required output

Produce all nine sections. Do not omit one because it is empty; say "none".

**1. Built and verified.** Object type, count, how verification was done.

**2. Built but unverified.** Anything created that could not be read back, and why.

**3. Partially built.** Exact stopping point, and the precise next click to resume.

**4. Not built.** Split by reason: (a) automation limitation, (b) needs a decision,
(c) needs agency access, (d) ran out of session.

**5. Placeholders left in.** Every `[[BRACKETED]]` value, every stub image URL,
every page that is a shell — with what real content it needs.

**6. Discrepancies found.** Anywhere the GHL UI did not match this spec, anywhere
an object already existed, anywhere a generated field key differed from the
expected key. **This section is the most valuable one** — it is how the spec gets
corrected for dealers 2 through 100.

**7. Decisions needed from Matthew.** Concrete questions with options, not
open-ended ones.

**8. Eric's data layer, as found.** The Phase 0 inventory: every object, field and
key the pipe created, how purchased products are linked to the contact, and every
place our field keys and Eric's disagree. Eric needs this back — it is the other
half of the contract in `src/wave1/contract.js`.

**9. Template safety.** What must NOT be filled in before Eric takes the snapshot:
every `[[BRACKETED]]` custom value, and the fact that all workflows are
deliberately in Draft. A placeholder filled with dealer one's details propagates
to every dealer cloned afterward.

Close with a one-paragraph plain summary: what a person would now see if they
opened this sub-account, and what remains before it could be snapshotted.

---

## 13. Source of truth

| Thing | Where |
|---|---|
| Field list, types, keys | `src/wave1/writer.js` → `WAVE1_FIELDS` |
| Tag list | `src/wave1/compute.js` → `MANAGED_TAGS` |
| Email HTML | `email/dist/*.html` (regenerate with `npm run email`) |
| SMS copy | `email/dist/sms.txt` |
| Workflow step detail | Wave 1 Build Specification, §8 |
| Category copy | `src/wave1/content.js` |
| Kit / bundle handling | `src/wave1/contract.js` (`kitId`, `lineRole`) and `resolveKits()` in `compute.js` |

If this spec and the code disagree, **the code wins** — it is what the connector
actually writes.
