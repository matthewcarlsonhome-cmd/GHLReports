# Owner report email review assets

October 2, 2026. Synthetic examples only; no messages sent.

Read [the implementation and workflow specification](../OWNER-WEEKLY-REPORT-SPEC.md) first.

## Previews

- [Follow-up needed](preview-attention.html)
- [No issues found](preview-healthy.html)
- [Incomplete data](preview-limited.html)
- [Monthly review — delivery deferred](preview-monthly.html)

Each preview has a matching plain-text file. Preview button destinations are harmless example URLs, not working report links. These are browser-reviewed designs, not proof of GHL/Gmail/Outlook delivery compatibility.

Review evidence: the attention design was visually inspected at desktop and
390px browser viewport settings. All four variants were checked at the mobile
setting with no horizontal overflow or unresolved placeholders; the monthly
variant was also visually inspected. Actual email-client rendering and GHL
delivery remain untested. No email was sent.

## Template sources

owner-weekly-template.html is a portable, inline-styled design source. owner-weekly-template.txt carries the same content contract. The HTML includes responsive width and a 600px Outlook table fallback. It requires backend rendering or verified GHL field mapping before use.

All %%field%% tokens are application placeholders, **not GHL merge syntax**. Do not upload and send the source unchanged. Use the workflow's observed custom-value picker and record the exact mapping. No external recipient email belongs in these templates.

Scalar tokens are HTML-escaped before insertion, including greetings, account labels, periods, metrics, notes and titles. report_url is server-constructed and restricted to the approved report origin and account. status colors come from an enum, never user-supplied CSS.

Controlled HTML slots:
- preview_banner: synthetic previews only; empty in live reports.
- actions_html: 0–3 safe action blocks rendered by trusted code from escaped fields.
- improvement_html: an optional safe block only when improvement is supported.

The plain-text source uses actions_text and improvement_text for equivalent content. Do not assume GHL creates multipart alternatives automatically.

Template contract:
- Identity: subject, preheader, account_name, report_label, period_label, greeting.
- Status: status_background, status_accent, status_text_color, status_label, headline, introduction.
- Scorecard: scorecard_title; metric_1/2/3_label, metric_1/2/3_value, metric_1/2/3_detail; period_note.
- Queue: current_as_of, current_queue_text.
- Actions: actions_heading, actions_html/actions_text, more_issues_text, improvement_html/improvement_text.
- Footer: data_note, report_url, cta_label, link_note, footer_text, report_reference.

In production replace preview link_note with: “Sign in to view this report. You can open the latest report from the dashboard.”
Replace report_reference with a safe report reference and template revision, not a credential.

## Review checklist

Verify 390px and desktop widths, long labels, escaped special characters, 0/1/3 actions, sample counts, unavailable figures, and period-versus-current wording. Approved live inbox tests must check Gmail, Outlook, GHL HTML handling, fixed recipient routing and exact report links. A healthy template must not be selected when required data is missing.

No company logo is fabricated; the header uses the existing SSP name and report palette.
