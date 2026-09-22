# Who actually uses MLH? Ads delivery vs CRM use

Date: 2026-09-22. Written for the GHL team and the AMs. Numbers come from the
dashboard's stored data for that date unless marked otherwise.

## Why an account with no MLH users still shows leads, deals and replies

A subaccount does two different jobs, and the dashboard used to see them as
one:

1. **Lead router, for every client.** SSP's standard account setup (the
   snapshot) includes:
   - pipelines named `<Service> - Ads Pipelines 📢`, present in 21 of 29
     active accounts;
   - published workflows. When a Facebook/Instagram lead ad or a Google ad
     landing form delivers a lead, the workflows reply to the lead instantly,
     send the lead's details to the client's staff by email or text, and file
     a deal in the matching Ads Pipeline.

   All of this runs whether or not anyone at the client ever logs in. The
   client works the lead from that email or text.
2. **CRM, only where the client adopted it.** Client staff reply from MLH,
   move and win deals, book appointments, and call from the app.

So "data flowing into the account" proves job 1 is working. Only human
actions prove job 2.

**Hamlin, the case that raised this:**
- 35 leads in 28 days, all Facebook or "Social media". 31 got a reply within
  one minute (automation) and 4 got none.
- 235 open deals, 222 of them stale, none won or lost last week, no
  appointments.
- Users: Taylor Hamlin (client) and Lisa Hoffman (SSP).
- 18 workflows, 9 published.
- Pipelines: ⛔️ Forms, 📊 Sales Pipeline, Inground Pool - Ads Pipelines 📢,
  Pool Renovation - Ads Pipelines 📢.

That is job 1 only. Lisa confirmed Hamlin was never onboarded to MLH.

**Verified vs inferred:** the pattern (instant replies, weekly deals in Ads
Pipelines, no human activity, accounts with zero users still running
published workflows) is in the data. What each workflow actually does
(trigger, who gets notified) is not exposed by the API. Opening one "Ads"
workflow in Hamlin or G&S confirms it in a minute.

## A measurement bug this exposed

The dashboard's "human first touch" counted any message sent under a user's
name as human. Workflow auto-replies are sent under a user's name. Across the
book, most "human" first replies arrive within one minute of the lead, which
no person does consistently:

| Account | "Human" first replies within 1 min (28d) |
|---|---|
| Exquisite | 214 of 230 |
| Central Jersey | 153 of 176 |
| Liverpool | 45 of 46 |
| Hamlin | 31 of 31 |
| Texas Swim | 30 of 30 |
| Aqua Leisure | 23 of 23 |

**Effect:** speed-to-lead looks near-instant everywhere, and "leads with no
human touch" is under-counted. That hides exactly the accounts where nobody
is working leads.

**Proposed fix, not yet applied:** a message counts as human only if its
source isn't an automation source and it was sent more than 60 seconds after
the lead's last message. It will move the speed-to-lead numbers and the
alerts built on them, so it waits for a go-ahead. The account-usage report
below already applies it and prints each account's raw message-source mix,
so the rule can be checked on live data first.

## The usage tiers and the filter

| Tier | Rule (last 28 days) | What happens |
|---|---|---|
| **No client logins** | No client-staff users on the account (GHL marks users `account` vs `agency`) | **Live now:** out of the AM digest; hidden in the portfolio behind "Show N with no client users". Still collected. |
| **Ads + automation only** | Leads or deals arrive, but no client-staff replies, no deals won or lost, no client-staff calls | Candidates for "ads delivery" handling: keep lead-flow alerts (a broken Facebook→GHL link still stops the client's leads), drop CRM-work alerts (stale pipeline, uncontacted leads, reply speed). Waiting on the report plus AM confirmation. |
| **Light use** | Some human activity, below the thresholds | Normal alerts |
| **Working in MLH** | ≥5 client-staff replies, or ≥2 deals won/lost, or ≥3 client-staff calls | Normal alerts |
| **Idle** | Nothing arrives, nothing happens | Check whether the client churned |

The 7 accounts with no users at all as of 2026-09-22: All American, Backyard
Oasis, Beachfront, G&S, Luke Gell, Pla-Mor, Pristine. Six of them are also on
the "missing from the updated client list — churned?" list. G&S still
receives about 6 Facebook leads a week, so its ads are live.

## Getting the per-account evidence

`python -m collector.main --account-usage` (or the **Reports** GitHub
workflow, see docs/FORM-MONITORING.md §2) writes `account-usage.csv`. It has
one row per account with:

- the tier and plain-language evidence;
- client vs SSP users, with client user names;
- leads, deals created (in Ads Pipelines vs other), deals won and lost, open
  deals and the share assigned;
- published workflow names;
- outbound messages split into automated, instant auto-reply, client staff,
  SSP staff and unattributed, plus client-staff calls and appointments;
- `message_source_mix`, the raw evidence for the rule above.

It is read-only and holds no contact names, message text, emails or phones.
