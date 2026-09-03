# Claude-with-Chrome Build Plan — Evosus Launch Snapshot

**For:** a Claude session driving Chrome against a GoHighLevel sub-account.
**Supersedes:** `docs/CHROME-BUILD-SPEC.md`.
**Design source:** `docs/LAUNCH-SNAPSHOT-DESIGN.md` — read it if a decision here is
unclear. Where this plan and the design disagree, **this plan wins for build
actions**; where either disagrees with the code, **the code wins**.
**Revised:** 3 September 2026, after an external design review closed nine gaps
and surfaced two live defects in the connector (see §1.2).

> **Note.** Code paths below (`src/…`, `email/…`, `docs/…`) are relative to the
> **Evosus** repo — `github.com/matthewcarlsonhome-cmd/evosus`, branch
> `claude/ssp-evosus-lou-integration-gkbg1m`. This copy sits in GHLReports beside
> the other partnership documents; the canonical copies live in that repo.

---

## 1. Before you touch anything

### 1.1 What you are building

One `[SSP-CORE]` Launch Snapshot in a **test** sub-account: custom fields, tags,
custom values, pipelines, email and SMS templates, two forms, a calendar shell,
four funnel shells, and eight **draft** workflows.

The snapshot must contain **no** product knowledge, **no** manufacturer branching,
**no** real dealer data, **no** contacts, and **no** enabled workflows. A
connector outside GoHighLevel writes contact fields and tags; GHL only renders
messages from them.

### 1.2 Nine gaps closed since the last version — read these, they change the build

| # | Was ambiguous | Now decided |
|---|---|---|
| 1 | SMS copy not available to the builder | **Embedded verbatim in §7 of this plan.** No blocker. |
| 2 | Email HTML not available | Obtained by running `npm run email` in the repo — §8. If you cannot reach the repo, Phase F stops; do not invent bodies. |
| 3 | `email-ok` described as a send guard | **Not a GHL guard.** Connector-side segmentation only. Do not put it on the canvas. |
| 4 | `do-not-market` suppression not in the guards | **Folded into the send guard** as a compound condition — §9.1. |
| 5 | W4 "branch on rating" undefined | **Branch on the `service-recovery-open` tag.** There is no rating field on the contact. |
| 6 | Form tagging: native or workflow? | **Two utility workflows, U2 and U3** — §9.7, §9.8. GHL forms cannot do conditional tagging. |
| 7 | Calendar owner / timezone / availability | **Specified** — §10.3. |
| 8 | Internal task assignee | **The account's only user** (Matthew Carlson in the test build). |
| 9 | Agency snapshot | **Out of scope.** Eric owns it. Do not attempt it. |

**Two connector defects were found while resolving these and are already fixed in
the code** — they matter to you only because they explain the tag lifetimes you
will see in §9:
- `service-completed` was never applied by the connector, so W4 could never fire.
- `new-customer` expired after 7 days while W5 runs 45, so the guard would have
  killed the welcome series after the day-2 SMS.

### 1.3 Inputs you need from Matthew before starting

1. The exact test sub-account name (see §2 — he creates it from the agency prompt).
2. Confirmation Chrome is logged in with rights to manage settings, templates,
   forms, calendars, funnels, pipelines and workflows.
3. Repo access for the email HTML, or the files themselves.
4. Whether Eric's LOU pipe has written anything into this account yet (it should
   not have, in a fresh test account — but Phase 0 checks regardless).

---

## 2. The sub-account Matthew creates first

Matthew creates this at agency level before the session starts. It is a **test**
account and carries no dealer data. The account owner is Matthew.

| Setting | Value |
|---|---|
| Business name | `TEST — Evosus Launch Build` |
| Owner / first name | Matthew |
| Last name | Carlson |
| Email | `mcarlson@smallscreenproducer.com` |
| Phone | `608-284-7333` |
| Timezone | America/Chicago |
| Snapshot to apply | **None** — start blank |

---

## 3. Standing rules

**Never:**
1. Publish, enable, or set live any workflow. Every workflow stays in **Draft**.
2. Send a test email or SMS — to anyone, including yourself.
3. Import contacts, or create a contact record.
4. Delete or rename anything you did not create in this session.
5. Switch sub-accounts, or navigate to agency settings.
6. Change billing, domains, phone numbers, email sending, or user permissions.
7. Accept a browser prompt to publish, go live, purchase, upgrade, or connect billing.
8. Put any real dealer's name, logo, phone, or URL into a custom value.
9. Add an arbitration or traffic-controller workflow (§9.9).

**Always:**
- **Check before you create.** GHL does not enforce name uniqueness. Read the list
  view first; if the object exists, reuse it and log "already present". A re-run
  without this produces 27 duplicate fields.
- **Verify by reading back.** Reopen the object, or re-read the list view. "I
  clicked Save" is not verification.
- **Prefix free-text names** `[SSP-CORE]`. **Never prefix** field keys, tag names,
  or custom value names — the connector writes by key.
- **Log as you go** (§12). If the session dies, the log is the deliverable.
- **Screenshot each phase boundary.**

**Stop and ask when:**
- A generated field key differs from the `key` column in §5.
- An object exists with the right name but different contents or type.
- GHL rejects a tag containing `:`.
- The same interaction fails three times.
- The UI does not expose an object type or setting this plan requires.
- Anything would cost money, send a message, or publish.

---

## 4. Phase 0 — inventory (mandatory, before any write)

A fresh test account should be empty. Verify rather than assume; Eric's pipe may
have been pointed here.

1. Confirm the sub-account name reads `TEST — Evosus Launch Build`. **If not, stop.**
2. Record the location/account ID if visible.
3. Inventory and log: custom fields (name, **key**, type, object) · custom objects
   and any Contact association · tags · custom values · pipelines · email and SMS
   templates · forms · calendars · funnels · workflows · any contact created by
   the pipe and which fields it populated.

Reconcile against §5 into three lists:
- **Same meaning, same key** → reuse, do not create.
- **Same meaning, different key** → **STOP and ask.** Connector contract decision.
- **Same key, different meaning** → **STOP and ask.** Collision.
- **Absent** → create per §5.

**Report how purchased products are linked to the contact** (custom object with
association / flat fields / notes / opportunities / not found). Put it first in
the gaps report.

---

## 5. Phase A — 27 Contact custom fields

`Settings → Custom Fields → Add Field`. Object: **Contact**.

Create field 1, then **confirm the generated key matches `lou_customer_id` exactly**
before creating the other 26. If GHL derives a different format (e.g.
`contact.lou_customer_id`, or a random suffix), **stop and report** — the
connector writes by key.

| # | key | Name | Type |
|---|---|---|---|
| 1 | `lou_customer_id` | LOU Customer ID | Text |
| 2 | `lou_portal_url` | LOU Portal URL | Text |
| 3 | `lou_balance_due` | LOU Balance Due | Monetary |
| 4 | `first_purchase_date` | First Purchase Date | Date |
| 5 | `last_purchase_date` | Last Purchase Date | Date |
| 6 | `ltv_total` | LTV Total | Monetary |
| 7 | `purchase_count` | Purchase Count | Number |
| 8 | `lifecycle_stage` | Lifecycle Stage | Text |
| 9 | `value_tier` | Value Tier | Text |
| 10 | `reorder_due_date` | Reorder Due Date | Date |
| 11 | `reorder_category` | Reorder Category | Text |
| 12 | `next_offer_product` | Next Offer Product | Text |
| 13 | `next_offer_sku` | Next Offer SKU | Text |
| 14 | `next_offer_url` | Next Offer URL | Text |
| 15 | `next_offer_price` | Next Offer Price | Monetary |
| 16 | `next_offer_code` | Next Offer Code | Text |
| 17 | `next_offer_image` | Next Offer Image | Text |
| 18 | `last_service_date` | Last Service Date | Date |
| 19 | `last_service_type` | Last Service Type | Text |
| 20 | `season_service_type` | Season Service Type | Text |
| 21 | `major_purchase_product` | Major Purchase Product | Text |
| 22 | `major_purchase_date` | Major Purchase Date | Date |
| 23 | `major_purchase_amount` | Major Purchase Amount | Monetary |
| 24 | `category_headline` | Category Headline | Text |
| 25 | `category_body` | Category Body | **Multi-line Text** |
| 26 | `category_tip` | Category Tip | Text |
| 27 | `category_image` | Category Image | Text |

**Verify:** 27 present · `category_body` is multi-line · fields 3, 6, 15, 23 are
Monetary · all on Contact · all keys exact.

---

## 6. Phase B — 19 tags · Phase C — 22 custom values · Phase D — 2 pipelines

### Tags — `Settings → Tags`. Lowercase, unprefixed, colons intact.

```
lou-import        email-ok            sms-consent        new-customer
balance-due       season-invite       reorder-due        season-booked
service-recovery-open                 service-completed  chemical-buyer
service-customer  big-ticket-buyer    plan-member        owns:pool
owns:hot-tub      owns:spa            do-not-market      money-ask-active
```

**Verify:** 19 present · the three `owns:*` tags kept their colons · no
near-duplicates differing only by case, spacing, or a missing colon.

### Custom values — `Settings → Custom Values`. Placeholders stay bracketed.

| Name | Value |
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

**Do not** substitute Matthew's real email or phone here. They identify the
account owner (§2), not the dealer. These 22 values are the dealer intake form and
must ship blank.

### Pipelines — `Opportunities → Pipelines`. Decline automation prompts.

1. `[SSP-CORE] Purchases - Attributed` — one stage: `Attributed`
2. `[SSP-CORE] Seasonal Services` — `Invited` → `Requested` → `Scheduled` → `Completed`

---

## 7. Phase E — 8 SMS templates

`Marketing → Templates → SMS`. Copy is embedded below — paste **verbatim**,
including every `{{ }}`. Name each template exactly as the heading.

**`[SSP-CORE] S1`** (W5 Day 2)
```
{{custom_values.dealer_name}}: hi {{contact.first_name}}, just checking in after your order - anything you need a hand with? Reply here anytime. Text STOP to opt out.
```

**`[SSP-CORE] S2`** (W1 Day 0)
```
{{custom_values.dealer_name}}: hi {{contact.first_name}}, you're probably getting low on {{contact.next_offer_product}}. Reorder in one tap: {{contact.next_offer_url}}
```

**`[SSP-CORE] S3`** (W1 Day 4)
```
{{custom_values.dealer_name}}: still time to restock your {{contact.next_offer_product}} before the weekend - {{contact.next_offer_url}}
```

**`[SSP-CORE] S4`** (W1 Day 14)
```
{{custom_values.dealer_name}}: last reminder on your {{contact.next_offer_product}}, promise. {{contact.next_offer_url}}
```

**`[SSP-CORE] S5`** (W3 Day 3)
```
{{custom_values.dealer_name}}: friendly reminder, your account shows {{contact.lou_balance_due}}. Pay anytime here: {{contact.lou_portal_url}}
```

**`[SSP-CORE] S6`** (W4 Day 1)
```
{{custom_values.dealer_name}}: how did we do on the {{contact.last_service_type}}, {{contact.first_name}}? One tap: {{custom_values.review_gateway_url}}
```

**`[SSP-CORE] S7`** (W2 Day 5)
```
{{custom_values.dealer_name}}: {{contact.season_service_type}} slots are filling up. Pick your week: {{custom_values.booking_url}}
```

**`[SSP-CORE] S8`** (W2 Day 17)
```
{{custom_values.dealer_name}}: last few {{contact.season_service_type}} appointments left - {{custom_values.booking_url}}
```

**Verify:** 8 present · reopen each and confirm the braces survived · **no SMS sent**.

---

## 8. Phase F — 12 email templates

Source: `email/dist/E1-*.html` … `E12-*.html`. Regenerate with `npm run email`
from the repo root. **If you cannot obtain these files, mark Phase F not built —
do not recreate the bodies visually and do not use placeholder HTML.**

`Marketing → Emails → Templates → New → **Blank / Custom HTML / Import HTML**`.
**Do not use the drag-and-drop builder** — it rewrites tables and can escape merge
braces.

Procedure: build **E1 first**, save, reopen, and confirm three things — the
subject is right, every `{{ }}` survived unescaped, and the `<table>` structure is
intact. **If E1 is mangled, stop and report. Do not paste the other eleven.**

| ID | Template name | Subject |
|---|---|---|
| E1 | `[SSP-CORE] E1 Welcome` | `Welcome to {{custom_values.dealer_name}}, {{contact.first_name}}` |
| E2 | `[SSP-CORE] E2 Category Education` | `The 5 things that keep {{custom_values.dealer_city}} pools trouble-free` |
| E3 | `[SSP-CORE] E3 Return Reasons` | `Three things people come back for` |
| E4 | `[SSP-CORE] E4 Review Request` | `How did we do?` |
| E5 | `[SSP-CORE] E5 Reorder Product` | `Running low on {{contact.next_offer_product}}?` |
| E6 | `[SSP-CORE] E6 Reorder Incentive` | `A little something on your {{contact.next_offer_product}}` |
| E7 | `[SSP-CORE] E7 Balance Note` | `A quick note about your account` |
| E8 | `[SSP-CORE] E8 Balance Help` | `Anything we can help with?` |
| E9 | `[SSP-CORE] E9 Seasonal Invite` | `Time to get your {{contact.season_service_type}} on the schedule` |
| E10 | `[SSP-CORE] E10 Seasonal Last Slots` | `Last of the {{contact.season_service_type}} slots` |
| E11 | `[SSP-CORE] E11 Service Plan` | `What that visit would have cost on a plan` |
| E12 | `[SSP-CORE] E12 Service Review` | `How did we do on the {{contact.last_service_type}}?` |

**Verify:** 12 present · names and subjects exact · braces intact on reopen ·
**no email sent**.

---

## 9. Phase I — 8 workflows

`Automation → Workflows → Create → Start from Scratch`. **Every workflow stays in
Draft.** Re-entry **off** except W4 (30 days). Quiet hours 8am–8pm where offered.

**Build order: U2, U3, W3, W1, W2, W4, W5, U1.** U2 and U3 are small and
everything downstream depends on them — without U3 nothing ever sets
`service-recovery-open`, which makes both the W4 branch and U1 dead.

### 9.1 The send guard — on every send step in W1–W5

One If/Else with a **compound** condition:

> **Contact has tag `{trigger tag}` AND Contact does not have tag `do-not-market`**

- **True** → continue to the send.
- **False** → **End workflow.**

This is what stops someone who has already reordered, paid, or booked, and it is
where the hard suppression lives. Use one combined condition, not two stacked
branches. Do **not** add an `email-ok` check.

### 9.2 The SMS consent guard — wrapping every SMS step

If/Else on **Contact has tag `sms-consent`**.
- **True** → send the SMS.
- **False** → **skip the step and continue** (do not end the workflow).

### 9.3 `[SSP-CORE] W3 Balance-Due Recovery` — trigger tag added `balance-due`
guard → E7 → wait 3d → guard → consent → S5 → wait 6d → guard → E8 → wait 8d →
guard → internal task "Call about outstanding balance", assignee: the account's
only user.

### 9.4 `[SSP-CORE] W1 Reorder Reminder` — trigger tag added `reorder-due`
guard → consent → S2 → wait 1d → guard → E5 → wait 3d → guard → consent → S3 →
wait 4d → guard → E6 → wait 6d → guard → consent → S4.

### 9.5 `[SSP-CORE] W2 Seasonal Booking` — trigger tag added `season-invite`
guard → E9 → wait 5d → guard → consent → S7 → wait 7d → guard → E10 → wait 5d →
guard → consent → S8.

### 9.6 `[SSP-CORE] W4 Post-Service Review` — trigger tag added `service-completed`
Re-entry: **allowed after 30 days**.
guard → consent → S6 → wait 1d → guard → E12 → wait 3d →
**If/Else: Contact has tag `service-recovery-open` → True: End workflow** (the
customer is unhappy; a plan upsell would be tone-deaf) **→ False:** wait 11d →
guard → E11.

### 9.7 `[SSP-CORE] U2 Seasonal Booking Handler`
Trigger: **Form Submitted → `[SSP-CORE] Seasonal Booking`**. Action: add tag
`season-booked`. No guards, no waits, no sends.

### 9.8 `[SSP-CORE] U3 Service Feedback Handler`
Trigger: **Form Submitted → `[SSP-CORE] Service Feedback`**.
If/Else on the form's Rating field **is one of `1`, `2`, `3`** → True: add tag
`service-recovery-open`. False: end. No sends.

If the rating field is not available as a workflow condition, **stop and report** —
this is the single dependency the post-service design rests on.

### 9.9 Do not build an arbitration workflow
Arbitration already happened in the connector: a contact can only ever carry one
revenue-ask tag (`balance-due` > `season-invite` > `reorder-due`, and none while
`service-recovery-open` is set). A second layer inside GHL will contradict it.
If you find yourself wanting one, log it as a question instead.

**Verify each workflow:** exists · **Draft** · correct trigger · re-entry setting ·
every send preceded by the compound guard whose false branch **ends** the workflow ·
every SMS wrapped in the consent guard whose false branch **continues** · W4's
recovery branch present · no arbitration workflow anywhere.

---

## 10. Phase G–H — forms, calendar, funnel shells

### 10.1 Form `[SSP-CORE] Seasonal Booking`
Service type (dropdown: `Opening`, `Closing`) · Preferred week (date) · Address ·
Notes (multi-line). Tagging is handled by U2, not form settings.

### 10.2 Form `[SSP-CORE] Service Feedback`
Rating (radio `1`–`5`) · Comments (multi-line). Tagging is handled by U3.
The rating field must be readable by a workflow condition — confirm while building U3.

### 10.3 Calendar `[SSP-CORE] Seasonal Service`
60-minute slots · no payment · no confirmation SMS · **owner:** the account's only
user · **timezone:** America/Chicago · **availability:** Mon–Fri 08:00–16:00.
These are snapshot defaults, overridden per dealer at onboarding.

### 10.4 Funnels — shells only
`[SSP-CORE] Review Gateway`, `[SSP-CORE] Seasonal Booking`,
`[SSP-CORE] Quick Reorder`, `[SSP-CORE] Preference Center`. One blank step each.
**Do not design pages, do not publish, do not touch domains or SEO settings.**

---

## 11. Realistic expectations

| Phase | Work | Risk | Expect |
|---|---|---|---|
| 0 | Inventory | Low | Complete — gates everything |
| A | 27 fields | Low | Complete |
| B–D | Tags, values, pipelines | Low | Complete |
| E | 8 SMS | Low | Complete — copy is in §7 |
| F | 12 emails | Medium | Complete if the repo files are reachable |
| G | 2 forms + calendar | Medium | Likely complete |
| H | 4 funnel shells | High | Shells only |
| I | 8 workflows | **High** | U2/U3 and the linear ones; W4's branch is the hardest. 4–5 of 8 is a good session. |
| J | Snapshot | — | **Not yours.** Eric, at agency level. |

A truthful "5 of 8 workflows, here is exactly where I stopped" is worth far more
than a claim of completion that fails on Monday.

---

## 12. Build log — keep it current, not at the end

| Time | Phase | Object | Action | Verified how | Notes |
|---|---|---|---|---|---|
| | | | created / reused / failed | | |

---

## 13. Required output — the gaps report

Nine sections. If one is empty, write "none".

1. **Built and verified** — type, count, how read back.
2. **Built but unverified** — and why.
3. **Partially built** — exact stopping point and the precise next click.
4. **Not built** — by reason: automation limit / needs a decision / needs agency
   access / missing source files / out of session.
5. **Placeholders left in** — every `[[BRACKETED]]` value and every shell.
6. **Discrepancies** — objects that already existed, keys GHL generated
   differently, UI paths that differed, behaviour you had to infer. **Most
   valuable section** — it is how this plan gets corrected for dealers 2–100.
7. **Decisions needed from Matthew** — concrete, with options.
8. **Eric's data layer as found** — the Phase 0 inventory, especially how
   purchases link to the contact, and every key mismatch. Eric needs this back.
9. **Template safety** — confirm all workflows Draft, no message sent, no contact
   created, all dealer values still bracketed, funnels are shells.

Close with one plain paragraph: what a person would see opening this sub-account,
and what remains before Eric could snapshot it.

---

## 14. Definition of done

1. Phase 0 complete and reconciled.
2. 27 fields, exact keys and types.
3. 19 tags, exact names, colons intact.
4. 22 custom values, placeholders **still bracketed**.
5. 2 pipelines with exact stages.
6. 8 SMS templates, braces verified on reopen.
7. 12 email templates as custom HTML, braces verified on reopen.
8. Forms, calendar and funnel shells built or explicitly listed as partial.
9. Every workflow built is Draft and carries the required guards.
10. No arbitration workflow exists.
11. No message sent, no contact created, no publish action taken.
12. Nine-section gaps report delivered.
