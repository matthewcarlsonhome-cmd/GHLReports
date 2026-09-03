# Evosus × SSP — Dealer Launch Snapshot: Design & Build Specification

**Audience.** An engineering session (Codex or equivalent) building the GoHighLevel
Launch Snapshot, plus the SSP/Evosus team reviewing the design.
**Status of this document.** Current as of 2 September 2026. Supersedes the
"one snapshot vs fifty" discussion; the architecture section here is the decided
design, and §5 records the open team objection and the answer to it.

> **Note.** Every `src/…`, `email/…` and `docs/…` path below is relative to the
> **Evosus** repo — `github.com/matthewcarlsonhome-cmd/evosus`, branch
> `claude/ssp-evosus-lou-integration-gkbg1m`, which is where the connector code,
> templates and tests live. This copy sits in GHLReports alongside the other
> partnership documents; the canonical copy is `docs/LAUNCH-SNAPSHOT-DESIGN.md`
> in the Evosus repo.

---

## 1. Where we stand today

### Working
| Piece | State | Owner |
|---|---|---|
| LOU → GoHighLevel order flow | **Working.** Orders land, customer records are created, purchased products are recorded and linked to the customer. | Eric |
| Sample data pulled from Evosus | **Done**, with documentation defects found and reported back to Evosus | Eric |
| Signal layer (classification, cadence, tags, field values) | **Built and tested** — 37 tests passing in this repo | Matthew |
| 12 email + 8 SMS templates | **Built**, rendering with zero unresolved merge fields | Matthew |
| Ingest contract (incl. kits/bundles) | **Built** — `src/wave1/contract.js` | Matthew |

### Blocked or in flight
| Item | Detail |
|---|---|
| **LOU `orders-by-date` endpoint** | Not working. John's team is on it. Gates live firing, not the build. |
| **Clean sub-account** | Eric is rebuilding the test account into the Evosus template. **No GHL build starts until handover.** |
| **Bundle/kit sample order** | Requested from John. Needed to confirm packages arrive with component lines. |
| **UPC-level product data** | Arrives later this week. The design does not depend on it (see §4). |
| **Snapshot creation** | Agency-level action. Eric owns the template. |

### What this document adds
The previous design named a "product library" without saying where it lived, and
the placeholder answer — a maintained spreadsheet — was rejected. §4 replaces it
with product data pulled from LOU itself plus rules in code. **No spreadsheet, no
curated product table, no ongoing content maintenance is required to launch.**

---

## 2. The Evosus / LOU API

Base `https://louapi.evosus.com/api/v1/` · acceptance `https://lou-accp.evosus.com`
Auth: single header `APIKey: {key}`.
Envelope: `{ statusCode, status, messageCode, messageText, response }` — unwrap `response`.

Full audited reference: `docs/LOU-API.md`. This section covers only what the
snapshot depends on.

### 2.1 Endpoints in the critical path

| Endpoint | Gives us | Feeds |
|---|---|---|
| *orders-by-date* (interim, path unconfirmed) | order id, customer id, order type, order status for a given **created** date | the daily scan |
| `GET /orders/sales/{id}` | `orderTotal`, `date`, `orderStatus`, `customerInfo{...}`, `orderSKUs[{skuId, sku, description, quantity, price, subtotal, discount}]` | purchases, LTV, reorder clock |
| `GET /orders/work/{id}` | as above plus `taskType`, `scheduleDivision`, `scheduleTasks` | service signals, post-service review |
| `GET /customers/{id}` | `email`, `phoneNumber`, addresses, **`customerBalanceDue`**, **`customerPortalURL`** | contact identity, balance-due campaign |
| `GET /customers/{id}/skus` | `sku`, `skuDescription`, **`skuCategory`**, `quantity`, `subtotal`, `invoiceDate` | backfill and **dealer-supplied category** |
| `GET /product/catalog`, `GET /product/{sku}` | `sellPrice`, `onHand`, `available`, `clearance`, `discontinued`, **`pictures`** | **product name, image and price — see §4** |
| `GET /customers/{id}/transactions` | `source` (incl. `"Recurring Order"`), `total`, `status` | plan members, renewal |

### 2.2 Constraints that shape the design

- **No webhooks.** Nightly polling. Push is a drop-in replacement later.
- **Orders are listed by *created* date, not completed.** Campaigns must trigger on
  completion, so the connector keeps an **open-order ledger** and re-polls status
  until an order reaches a completed state. Observed statuses: `Pending`, `Complete`.
- **No documented status enum.** `COMPLETED_STATUSES` in `src/wave1/contract.js`.
- **No pagination, unpublished rate limits.** Tight date windows, backoff, honour `Retry-After`.
- **No customer-list endpoint.** Onboarding does a one-time CSV import.
- **No estimates endpoint.** Quote Rescue is built and switched off.
- **Dates are US strings** (`"2/13/24"`); `customerBalanceDue` is a formatted string
  (`"-$2,219.89"`) while order money is numeric. Parse both. IDs can be 18 digits — keep as strings.

### 2.3 The 7-day backfill guard
Onboarding imports up to 24 months of history. Without a guard, every imported
customer looks new and fires a welcome series. Any purchase older than
`NEW_CUSTOMER_DAYS = 7` at import time never sets `new-customer`.

---

## 3. Architecture

Three layers. Only the first is a snapshot.

```
  OUTSIDE GOHIGHLEVEL                    INSIDE GOHIGHLEVEL
  ───────────────────                    ──────────────────
  LOU  ──orders──▶  Connector  ──API──▶  Dealer sub-account
                        │                   contact fields
                        │                   tags
   LOU product catalog ─┘                     │
   + category rules in code                   ▼
                                          Workflow (identical everywhere)
                                              │
                                              ▼
                                          Email / SMS
```

**The only thing that crosses the boundary is field values and tags on a contact.**
No manufacturer name is ever stored in a template, a workflow, or the snapshot.
By the time an email sends, the product name and image are already on the contact.

**Money-ask arbitration happens in the connector, not in GHL.** Only one
revenue-ask tag is ever applied to a contact at a time — priority
`balance-due (1) > season-invite (2) > reorder-due (3)`, and none at all while
`service-recovery-open` is set. This is why the snapshot needs no traffic-controller
workflow, and it is the single most important thing for the builder not to
"helpfully" re-add inside GoHighLevel.

---

## 4. Product intelligence — no spreadsheet

Earlier drafts proposed a maintained Google Sheet as the product library. That is
withdrawn. Everything the campaigns need is either already in LOU or derivable.

### 4.1 Where each piece actually comes from

| What an email needs | Source | Maintained by |
|---|---|---|
| Product name shown to the customer | `orderSKUs[].description`, or `GET /product/{sku}` | **LOU — nobody** |
| Product image | `GET /product/catalog` → `pictures` | **LOU — nobody** |
| Price | `orderSKUs[].price` / `sellPrice` | **LOU — nobody** |
| SKU / UPC | `orderSKUs[].sku`, `skuId` | **LOU — nobody** |
| In stock / discontinued | `onHand`, `available`, `discontinued` | **LOU — nobody** |
| Category | `skuCategory` from `/customers/{id}/skus`, else the name classifier | LOU, with a code fallback |
| How often it runs out | Observed repeat cadence, blended with a category default | **Derived — nobody** |
| Category copy (11 blocks) | `src/wave1/content.js` | Code, reviewed in a PR |

**Net: zero rows of curated product data are required to launch.** The dealer's own
LOU catalog is the product library, and it is already maintained by the dealer as
a condition of running their business.

### 4.2 Cadence is observed, not curated
`intervalFor()` blends the customer's own repeat interval with a category default:
`interval = observed × 0.7 + default × 0.3`, and the nudge fires at
`last_purchase + interval × 0.8`. Category defaults (days): sanitizer 45,
balancer 75, specialty 60, salt 120, filtration 240, test 180. A dealer whose
customers actually reorder every 60 days converges on 60 without anyone editing
anything.

### 4.3 Category is preferred over brand, deliberately
`skuCategory` comes from the dealer's own item master, so it reflects how *that
dealer* files the product. Where it is absent, `src/wave1/classify.js` reads the
product name. Manufacturer is captured as a `brand:*` tag for segmentation, but
**no campaign branches on it.**

### 4.4 If per-product overrides are ever wanted
Optional, not required. A small override table keyed by SKU/UPC holding only a
better image, a hand-set interval, or brand-approved copy — anything absent falls
back to §4.1. It belongs in the connector's datastore (the repo, or Supabase),
version-controlled and code-reviewed. **Do not build this for wave 1.**

### 4.5 Kits and bundles
Dealers sell a hot tub with a cover, steps and a chemical starter kit as one
package; LOU sends several lines. The contract carries `kitId` and `lineRole`
(`kit-parent` | `kit-component`). Whichever line carries the price keeps it and
the rest go to zero, so a bundle counts once in LTV; if no parent is marked, the
priciest line is treated as the parent. **Components still contribute their
category signals** — a spa package containing chemicals must start that customer's
chemical clock. See `resolveKits()` in `src/wave1/compute.js`.

---

## 5. The per-manufacturer snapshot question

**The team's position.** Some of the team believe we need one snapshot per
manufacturer — apply the Latham snapshot to dealers carrying Latham, the Solenis
snapshot to dealers carrying Solenis, and so on, up to ~50. The concern behind it
is real and worth stating plainly: *generic automation that does not know a
manufacturer's specifics will send wrong or embarrassing messages, and the dealer
will blame us.*

**Why that structure does not deliver it.**

1. **A snapshot carries no product knowledge.** Whatever we learn about Latham has
   to be written down once by a person either way. Fifty snapshots does not
   produce that content; it only chooses where it is filed.
2. **Cloned copies drift.** Content filed into 50 snapshots has already been cloned
   into every sub-account onboarded before the next edit. A March change to Latham's
   warranty language never reaches dealers onboarded in February, silently, with no
   single place to audit correctness. This is a brand-accuracy failure, which is
   the very thing the objection is trying to prevent.
3. **Snapshots stack; they do not merge.** A customer who buys a Doughboy pool, a
   Latham liner and Solenis chemicals — an ordinary customer for any full-line
   dealer — sits in a sub-account with three snapshots applied. Three welcome
   series, three review requests, three reorder workflows, none aware of the
   others. Arbitration would have to live somewhere that is not a snapshot, which
   concedes the architecture anyway.
4. **Maintenance.** 50 snapshots × ~12 templates ≈ 600 assets; one footer change is
   50 edits; each dealer needs 8–12 snapshots applied; ~1,000 applications to track
   at 100 dealers.

**The way around it.** Manufacturer specificity is delivered per *contact*, not per
*snapshot*: the connector writes the product's real name, real image and real
timing onto the contact record, and one template renders it. The same template
produces "Running low on BioGuard Burnout 73?" for one customer and "Running low
on Spa 56 Chlorinating Granules?" for another. Depth of brand knowledge and number
of snapshots are independent variables.

**What we owe the objection.** Two things that genuinely need a person:
- **Timing intricacies per product line** — a registration step, a seasonal install
  window, a measurement lead time. These become fields, and apply to every dealer at once.
- **Brand-approved language** for the handful of products that warrant it (§4.4).

**If a manufacturer truly needs its own path**, build that one exception when a real
dealer needs it. Do not pre-build fifty for a case no dealer has raised.

---

## 6. The Launch Snapshot — object specification

Everything below is built in **one sub-account**, then snapshotted. Naming rule:
prefix everything that has a free-text name with `[SSP-CORE]`. **Custom fields,
tags and custom values use the exact keys below, unprefixed — the connector writes
by key and a renamed key breaks silently.**

### 6.1 Custom fields — 27, object: Contact

`Settings → Custom Fields → Add Field`. Confirm the generated key matches the
`key` column on the first one; if GHL derives a different key format, **stop** —
the connector writes by key.

| # | key | Field name | Type | Written by | Used by |
|---|---|---|---|---|---|
| 1 | `lou_customer_id` | LOU Customer ID | Text | connector | matching, reporting |
| 2 | `lou_portal_url` | LOU Portal URL | Text | connector | E1, E7 |
| 3 | `lou_balance_due` | LOU Balance Due | Monetary | connector | E7, E8, S5 |
| 4 | `first_purchase_date` | First Purchase Date | Date | connector | segmentation |
| 5 | `last_purchase_date` | Last Purchase Date | Date | connector | cadence |
| 6 | `ltv_total` | LTV Total | Monetary | connector | value tiering |
| 7 | `purchase_count` | Purchase Count | Number | connector | segmentation |
| 8 | `lifecycle_stage` | Lifecycle Stage | Text | connector | reporting |
| 9 | `value_tier` | Value Tier | Text | connector | offer selection |
| 10 | `reorder_due_date` | Reorder Due Date | Date | connector | W1 trigger basis |
| 11 | `reorder_category` | Reorder Category | Text | connector | E5, E6 |
| 12 | `next_offer_product` | Next Offer Product | Text | connector | **E5, E6, S2–S4 subject + body** |
| 13 | `next_offer_sku` | Next Offer SKU | Text | connector | reorder link |
| 14 | `next_offer_url` | Next Offer URL | Text | connector | E5, E6 CTA |
| 15 | `next_offer_price` | Next Offer Price | Monetary | connector | E5 product card |
| 16 | `next_offer_code` | Next Offer Code | Text | connector | E6 incentive |
| 17 | `next_offer_image` | Next Offer Image | Text | connector | E5, E6 product card |
| 18 | `last_service_date` | Last Service Date | Date | connector | W4 |
| 19 | `last_service_type` | Last Service Type | Text | connector | **E12 subject** |
| 20 | `season_service_type` | Season Service Type | Text | connector | **E9, E10 subject** |
| 21 | `major_purchase_product` | Major Purchase Product | Text | connector | E1, E2 |
| 22 | `major_purchase_date` | Major Purchase Date | Date | connector | W5 |
| 23 | `major_purchase_amount` | Major Purchase Amount | Monetary | connector | segmentation |
| 24 | `category_headline` | Category Headline | Text | connector | E2 |
| 25 | `category_body` | Category Body | **Multi-line Text** | connector | E2 |
| 26 | `category_tip` | Category Tip | Text | connector | E2, S1 |
| 27 | `category_image` | Category Image | Text | connector | E2 |

Fields 24–27 are the mechanism that keeps conditional logic out of the email
builder: the connector resolves the category's copy and writes it as plain text,
so every template is unconditional. Canonical list: `WAVE1_FIELDS` in
`src/wave1/writer.js`.

### 6.2 Tags — 19

`Settings → Tags`. Lowercase exactly as written, colons included. If GHL rejects
`:` in a tag name, **stop and report** — renaming is a code change, not a UI
workaround.

| Tag | Applied when | Removed when | Role |
|---|---|---|---|
| `lou-import` | contact synced from LOU | never | provenance |
| `email-ok` | contact has an email | email removed | send guard |
| `sms-consent` | `smsConsent === true` | consent withdrawn | **SMS guard — every SMS step checks this** |
| `new-customer` | first purchase ≤ 7 days ago | after 45 days | W5 trigger |
| `balance-due` | balance > $1 **and wins arbitration** | balance cleared | W3 trigger |
| `season-invite` | in season window **and wins arbitration** | booked or window closes | W2 trigger |
| `reorder-due` | reorder date reached **and wins arbitration** | category repurchased | W1 trigger |
| `season-booked` | booking form submitted | next season | suppression |
| `service-recovery-open` | rating 1–3 | staff closes it | **suppresses all money asks** |
| `service-completed` | work order completes | 35 days | W4 trigger |
| `chemical-buyer` | bought a consumable | reconciled | segmentation |
| `service-customer` | any work order | reconciled | segmentation |
| `big-ticket-buyer` | ≥ $5,000 or big-ticket category | reconciled | segmentation |
| `plan-member` | recurring order / service plan | plan ends | suppression |
| `owns:pool` | pool, liner or pool cover purchase | reconciled | seasonal targeting |
| `owns:hot-tub` | hot tub, or a spa-context cover | reconciled | seasonal targeting |
| `owns:spa` | sauna / cold plunge | reconciled | seasonal targeting |
| `do-not-market` | manual, or unsubscribe | manual | hard suppression |
| `money-ask-active` | any revenue-ask tag set | none set | observability |

**Tag reconciliation is what lets workflows exit.** On every sync the connector
removes managed tags that are no longer earned. A workflow's guard step
(§6.6) re-checks the trigger tag before each send, so a customer who has already
reordered stops receiving the sequence. Canonical list: `MANAGED_TAGS` in
`src/wave1/compute.js`.

### 6.3 Custom values — 22

`Settings → Custom Values`. Use these placeholders **verbatim**; they are
deliberately conspicuous so an unconfigured value is obvious in a test send rather
than rendering blank.

| Name | Placeholder |
|---|---|
| `dealer_name` | `[[DEALER NAME]]` |
| `dealer_city` | `[[CITY]]` |
| `dealer_phone` | `[[PHONE]]` |
| `dealer_email` | `[[EMAIL]]` |
| `dealer_address` | `[[STREET, CITY, ST ZIP]]` |
| `logo_url` | `https://placehold.co/160x40?text=LOGO` |
| `brand_color` | `#0F6F86` |
| `booking_url` | `[[BOOKING URL]]` |
| `review_url` | `[[GOOGLE REVIEW URL]]` |
| `review_gateway_url` | `[[REVIEW GATEWAY PAGE URL]]` |
| `shop_url` | `[[SHOP URL]]` |
| `water_test_url` | `[[WATER TEST BOOKING URL]]` |
| `preference_center_url` | `[[PREFERENCE CENTER URL]]` |
| `offer_reorder` | `10% off` |
| `offer_service_plan` | `[[SERVICE PLAN OFFER]]` |
| `offer_seasonal` | `[[SEASONAL OFFER]]` |
| `season_opening_start` | `04-01` |
| `season_opening_end` | `05-31` |
| `season_closing_start` | `09-15` |
| `season_closing_end` | `10-31` |
| `notify_email_sales` | `[[SALES EMAIL]]` |
| `notify_email_service` | `[[SERVICE EMAIL]]` |

**These 22 values are also the dealer onboarding intake form.** Every `[[BRACKETED]]`
entry is a question for the dealer at signup. **They must remain bracketed in the
snapshot** — a placeholder filled with dealer one's details propagates to all 100.

### 6.4 Pipelines — 2

1. `[SSP-CORE] Purchases — Attributed` · one stage: `Attributed`
   The connector creates a won opportunity when a purchase follows a campaign send,
   which is the attribution record for the monthly dealer report.
2. `[SSP-CORE] Seasonal Services` · `Invited → Requested → Scheduled → Completed`

Decline any pipeline automation prompts.

### 6.5 Templates — 12 email, 8 SMS

Email: `Marketing → Emails → Templates → New → Blank / Custom HTML`. Paste the
built file, **do not use the drag-drop builder** — it rewrites the table structure
and can escape merge braces. Source files: `email/dist/E1-*.html` … `E12-*.html`
(regenerate with `npm run email`). After saving, **reopen and confirm** the
`{{contact.…}}` braces survived and the `<table>` structure is intact. If the
editor mangles the first template, stop and report rather than pasting eleven more.

| ID | Workflow · step | Subject |
|---|---|---|
| E1 | W5 · Day 0 | Welcome to `{{custom_values.dealer_name}}`, `{{contact.first_name}}` |
| E2 | W5 · Day 7 | The 5 things that keep `{{custom_values.dealer_city}}` pools trouble-free |
| E3 | W5 · Day 21 | Three things people come back for |
| E4 | W5 Day 45 / W4 | How did we do? |
| E5 | W1 · Day 1 | Running low on `{{contact.next_offer_product}}`? |
| E6 | W1 · Day 8 | A little something on your `{{contact.next_offer_product}}` |
| E7 | W3 · Day 0 | A quick note about your account |
| E8 | W3 · Day 9 | Anything we can help with? |
| E9 | W2 · Day 0 | Time to get your `{{contact.season_service_type}}` on the schedule |
| E10 | W2 · Day 12 | Last of the `{{contact.season_service_type}}` slots |
| E11 | W4 · Day 14 | What that visit would have cost on a plan |
| E12 | W4 · Day 1 and 4 | How did we do on the `{{contact.last_service_type}}`? |

SMS: `Marketing → Templates → SMS`. Name `[SSP-CORE] S1` … `S8`. Source:
`email/dist/sms.txt`. Paste verbatim including merge braces.

**Merge-field contract.** Every `{{contact.*}}` a template uses must be a field the
connector writes. A test in `tests/wave1.test.js` enforces this and fails the build
otherwise — it has already caught one production defect (`next_offer_image`
merged by the E5/E6 product card but never written, which would have rendered a
broken image for every reorder recipient). **If you add a merge field to a template,
add the field to `WAVE1_FIELDS` in the same change.**

### 6.6 Workflows — 6

`Automation → Workflows → Create → Start from Scratch`. **Every workflow is left in
Draft.** Build in this order — easiest first, so partial completion still leaves
working automation.

Settings for all: re-entry **off** (exception: W4, re-entry after 30 days);
quiet hours 8am–8pm where available.

| Order | Name | Trigger (Contact Tag Added) | Steps |
|---|---|---|---|
| 1 | `[SSP-CORE] W3 Balance-Due Recovery` | `balance-due` | E7 → wait 3d → S5 → wait 6d → E8 → wait 8d → internal task |
| 2 | `[SSP-CORE] W1 Reorder Reminder` | `reorder-due` | S2 → wait 1d → E5 → wait 3d → S3 → wait 4d → E6 → wait 6d → S4 |
| 3 | `[SSP-CORE] W2 Seasonal Booking` | `season-invite` | E9 → wait 5d → S7 → wait 7d → E10 → wait 5d → S8 |
| 4 | `[SSP-CORE] W4 Post-Service Review` | `service-completed` | S6 → wait 1d → E12 → wait 3d → branch on rating → wait 11d → E11 |
| 5 | `[SSP-CORE] W5 New Customer Welcome` | `new-customer` | E1 → wait 2d → S1 → wait 5d → E2 → wait 14d → E3 → wait 24d → E4 |
| 6 | `[SSP-CORE] U1 Internal Alerts` | `service-recovery-open` | internal notification to `{{custom_values.notify_email_service}}` + task |

**Two guard patterns, on every workflow. These are not optional — without them the
design fails silently.**

1. **Trigger-tag guard.** Precede *every* send step with If/Else on
   "Contact has tag `{trigger tag}`"; the false branch **ends** the workflow. This
   is what makes a customer who has already reordered, paid, or booked stop
   receiving the sequence. The connector removes the tag; the guard notices.
2. **SMS consent guard.** Wrap every SMS step in If/Else on "Contact has tag
   `sms-consent`"; the false branch **skips the step and continues** (it does not
   end the workflow).

**Do not add an arbitration or traffic-controller workflow.** Arbitration already
happened in the connector — a contact can only ever carry one revenue-ask tag.
Adding a second layer in GHL will produce contradictory behaviour.

### 6.7 Supporting objects

| Object | Spec |
|---|---|
| Form `[SSP-CORE] Seasonal Booking` | service type (dropdown: Opening / Closing), preferred week (date), address, notes. On submit: add tag `season-booked`. |
| Form `[SSP-CORE] Service Feedback` | rating (radio 1–5), comments. Rating 1–3 adds `service-recovery-open`. |
| Calendar `[SSP-CORE] Seasonal Service` | 60-minute slots, no payment, no confirmation SMS configured |
| Funnels (4) | `Review Gateway`, `Seasonal Booking`, `Quick Reorder`, `Preference Center` — **shells only**, one blank step each. Page design is a separate content task. |

---

## 7. Build sequence

Ordered by dependency **and** by automation reliability, so an interrupted session
still leaves the most valuable work done.

| Phase | Work | Risk | Expect |
|---|---|---|---|
| 0 | **Inventory what Eric's pipe already created** | Low | Complete — gates everything below |
| A | 27 custom fields (§6.1) | Low | Complete, minus anything Eric already made |
| B | 19 tags (§6.2) | Low | Complete |
| C | 22 custom values (§6.3) | Low | Complete |
| D | 2 pipelines (§6.4) | Low | Complete |
| E | 8 SMS templates | Low–Med | Complete |
| F | 12 email templates | Medium | Verify HTML survived the editor |
| G | 2 forms + 1 calendar | Medium | Partial |
| H | 4 funnel shells | High | Shells only |
| I | 6 workflows (§6.6) | **High** | Realistically 2–3 of 6; the canvas is the wall |
| J | Snapshot | **Eric's** | Agency-level; not this session's action |

### Phase 0 is mandatory and comes before any write

Eric's pipe already creates customer records and links purchased products. Objects
and fields therefore exist that this spec did not create. **Creating a second set
alongside them is the most likely way to wreck the build** — two "Customer ID"
fields, one written by the pipe and one read by the workflows, fails silently and
looks like a content bug for days.

Record, before creating anything:

- Every custom field: name, **key**, type, object.
- Every custom object, its fields, and its **association to Contact**.
- Existing tags and custom values.
- A sample contact created by the pipe — which fields are populated, and with what.

Then reconcile against §6.1 into three lists:
- **Same meaning, same key** → use Eric's, do not create.
- **Same meaning, different key** → **STOP and ask.** Either the connector changes
  to write Eric's key or the field is renamed. Do not paper over it with a duplicate.
- **Not present** → create per §6.1.

**Report how products are linked to the contact.** "Products recorded and linked"
could mean a custom object with an association, or repeating contact fields. The
signal layer currently writes flat contact fields; if Eric modelled purchases as a
related object, that is an architecture decision for Matthew and Eric, not
something to resolve by clicking. Put it at the top of the gaps report.

### Standing rules

**Never:** publish or enable any workflow (all stay Draft) · send a test email or SMS
to anyone including yourself · delete or rename an object you did not create ·
switch sub-accounts · change billing, domains, phone numbers, email sending or user
permissions · import contacts · accept a prompt to publish, go live, purchase or
upgrade.

**Always:** **check before you create** — GHL does not enforce name uniqueness, so a
re-run without a list-view check produces 27 duplicate fields · **verify by reading
back** ("I clicked Save" is not verification) · prefix free-text names `[SSP-CORE]`
· keep the build log current as you go, not at the end.

**Stop and ask when:** the same interaction fails 3 times · the UI does not match
this spec · an object exists with the right name but different contents · anything
would cost money or send a message · a generated field key differs from §6.1.

---

## 8. Acceptance criteria

The build is done when all of these are true and have been **read back**, not
merely saved:

1. 27 custom fields exist with the exact keys in §6.1; `category_body` is multi-line;
   the four Monetary fields are Monetary.
2. 19 tags exist, lowercase, colons intact.
3. 22 custom values exist and **still contain their `[[BRACKETED]]` placeholders**.
4. 2 pipelines exist with the stages in §6.4.
5. 12 email templates saved as custom HTML, reopened, merge braces intact.
6. 8 SMS templates saved with merge braces intact.
7. Every workflow built is in **Draft**, has the trigger-tag guard before each send,
   and wraps SMS steps in the consent guard.
8. No arbitration/traffic-controller workflow exists.
9. A gaps report (§9) has been produced.

---

## 9. Required output — gaps report

Nine sections. Do not omit one because it is empty; say "none".

1. **Built and verified** — object type, count, how verified.
2. **Built but unverified** — what could not be read back, and why.
3. **Partially built** — exact stopping point and the precise next click to resume.
4. **Not built** — split by reason: automation limitation / needs a decision /
   needs agency access / ran out of session.
5. **Placeholders left in** — every `[[BRACKETED]]` value and every shell page,
   with what real content it needs.
6. **Discrepancies** — anywhere the GHL UI did not match this spec, anywhere an
   object already existed, anywhere a generated key differed. **This is the most
   valuable section** — it is how the spec gets corrected for dealers 2 through 100.
7. **Decisions needed from Matthew** — concrete questions with options.
8. **Eric's data layer as found** — the Phase 0 inventory, including how purchased
   products are linked to the contact, and every key mismatch. Eric needs this back.
9. **Template safety** — confirmation that all workflows are Draft by design and all
   dealer values are still bracketed, so the snapshot is safe for Eric to take.

Close with one plain paragraph: what a person would see opening this sub-account,
and what remains before it could be snapshotted.

---

## 10. Open decisions

| # | Question | Who | Blocking |
|---|---|---|---|
| 1 | Does the product lookup + cadence step run inside Eric's existing connection, or as a second step after it? Decides who writes it. | Eric + Matthew | Connector work, not the GHL build |
| 2 | Are purchases modelled as a custom object with a Contact association, or flat contact fields? | Eric | Phase 0 answers it; may change the writer |
| 3 | Do LOU bundles expose component lines, or only the package parent? If only the parent, a package sale cannot start a chemical clock. | Evosus (John) | The flagship replenishment use case |
| 4 | Exact path and parameter names for the interim orders-by-date endpoint | Evosus (John) | Live firing |
| 5 | Does the dealer have ecommerce? Decides whether reorder CTAs deep-link to a cart or to a call/booking. | Dealer, at onboarding | E5/E6 CTA behaviour |
| 6 | Three real dealer offers and a voice check on the 12 emails | Pam | Content quality, not structure |

---

## 11. Source of truth

Where this document and the code disagree, **the code wins** — it is what the
connector actually writes.

| Thing | Where |
|---|---|
| Field list, keys, types | `src/wave1/writer.js` → `WAVE1_FIELDS` |
| Tag list | `src/wave1/compute.js` → `MANAGED_TAGS` |
| Arbitration priority | `src/wave1/compute.js` → `MONEY_ASK_PRIORITY`, `arbitrate()` |
| Cadence maths | `src/wave1/compute.js` → `intervalFor()`, `REORDER_LEAD_FACTOR` |
| Category / brand classification | `src/wave1/classify.js` |
| Category copy blocks | `src/wave1/content.js` |
| Kit handling | `src/wave1/contract.js`, `resolveKits()` in `compute.js` |
| Ingest contract for Eric | `src/wave1/contract.js` |
| Email HTML | `email/dist/*.html` — regenerate with `npm run email` |
| SMS copy | `email/dist/sms.txt` |
| LOU API audit | `docs/LOU-API.md` |
| Chrome-driven build runbook | `docs/CHROME-BUILD-SPEC.md` |
