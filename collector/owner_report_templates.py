"""Escaped owner email variants from the same safe snapshot the dashboard reads."""
from datetime import timedelta
from html import escape
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .owner_reports import number, report_url, safe_label, stamp

ROOT = Path(__file__).parent / "templates"


def display(value, suffix=""):
    n = number(value)
    return f"{n:g}{suffix}" if n is not None else "Unavailable"


def duration(value):
    n=number(value)
    if n is None: return "Unavailable"
    return f"{n:g} minutes" if n<60 else f"{n/60:.1f} hours" if n<1440 else f"{n/1440:.1f} days"


def render(snapshot, origin, test=False):
    """Return flat strings. Input must be a persisted, safe owner snapshot."""
    d=snapshot["data"]; m=d["metrics"]; q=d["queue"]; state=d["state"]
    if state not in ("attention","healthy","limited"):
        raise ValueError("Unsupported report state")
    start,end=stamp(d["period_start"]),stamp(d["period_end"])
    tz=ZoneInfo(d["timezone"])
    period=f"{start.astimezone(tz):%b %d, %Y} – {end.astimezone(tz)-timedelta(seconds=1):%b %d, %Y}"
    headline={"attention":"Your team's next steps","healthy":"No follow-up issues found in the selected topics",
              "limited":"Some results need another check"}[state]
    intro={"attention":"Review the period results below, then address the current follow-up priorities.",
           "healthy":"Keep your team's follow-up routine in place and check the dashboard for new activity.",
           "limited":"Review the available items below. Missing figures need another check before judging performance."}[state]
    colors={"attention":("#faf0da","#a86f0a","#795006"),"healthy":("#eaf5ef","#0c7a3c","#086031"),"limited":("#edf3fa","#466e95","#335578")}[state]
    actions=d.get("actions",[])[:3]
    period_kind="Weekly" if d["period_kind"]=="week" else "Monthly" if d["period_kind"]=="month" else "Current"
    suffix=f"{len(d['actions'])} priorities" if state=="attention" else "no issues found" if state=="healthy" else "some data unavailable"
    subject=f"{safe_label(d['account_name'],75)}: {period_kind.lower()} lead follow-up — {suffix}"
    if test: subject="[TEST — SAMPLE DATA] "+subject
    fields={
      "subject":subject[:150], "preheader":headline, "account_name":safe_label(d["account_name"]),
      "report_label":period_kind+" lead follow-up","period_label":period+" · "+d["timezone"],"greeting":"Hello,",
      "status_background":colors[0],"status_accent":colors[1],"status_text_color":colors[2],
      "status_label":{"attention":"FOLLOW-UP NEEDED","healthy":"NO ISSUES FOUND","limited":"SOME DATA UNAVAILABLE"}[state],
      "headline":headline,"introduction":intro,"scorecard_title":"Reporting period results",
      "metric_1_label":"New leads","metric_1_value":display(m["new_leads"]),
      "metric_1_detail":"No comparable prior period." if not d.get("comparison") else
        f"{display(d['comparison']['previous'])} in the prior period · "+(display(d["comparison"]["change"],"% change") if d["comparison"]["change"] is not None else "No comparable percentage."),
      "metric_2_label":"Follow-up recorded","metric_2_value":f"{display(m['contacted'])} of {display(m['eligible'])}" if m["eligible"] is not None else "Unavailable",
      "metric_2_detail":f"{display(m['completion_pct'],'%')} by period end. No follow-up: {display(m['uncontacted'])}." if m["eligible"] is not None else "Complete response history is not available.",
      "metric_3_label":"Typical first human response" if m["speed_human"] else "Typical first outbound response",
      "metric_3_value":duration(m["speed_median"]),
      "metric_3_detail":f"90th percentile: {duration(m['speed_p90'])} · {display(m['speed_samples'])} measured responses.",
      "period_note":"Eligible leads were at least 24 hours old at cutoff. Follow-up recorded does not mean contacted within 24 hours or reached successfully.",
      "current_as_of":stamp(d["current_as_of"]).astimezone(tz).strftime("%b %d, %Y %I:%M %p %Z") if d.get("current_as_of") else "Unavailable",
      "current_queue_text":f"{display(q['uncontacted'])} recent leads overdue · {display(q['waiting'])} waiting conversations · oldest wait: {display(q['oldest_wait_hours'],' hours')} · {display(q['unassigned'])} unassigned leads.",
      "actions_heading":"What needs correction" if actions else "Next step",
      "more_issues_text":f"{max(0,len(d['actions'])-3)} additional priorities in the dashboard." if len(d["actions"])>3 else "",
      "data_note":safe_label(d["data_note"],2000),
      "report_url":report_url(origin,d["location_id"],snapshot["id"],snapshot["period_start"]),
      "cta_label":"View your account report","link_note":"Sign in to view this saved report. You can open the latest report from the dashboard.",
      "footer_text":"You receive this weekly summary as the designated report owner. Contact your SSP account manager to change the recipient or pause reports. The dashboard requires sign-in.",
      "report_reference":f"Report {snapshot['id']} · {d['template_version']}",
      "report_state":state,"improvement_text":"","action_count":str(len(actions)),
    }
    topics=d.get("topics",["lead_flow","follow_up","speed","waiting","ownership"])
    for i,topic in enumerate(("lead_flow","follow_up","speed"),1):
        if topic not in topics:
            fields[f"metric_{i}_value"]="Not included"
            fields[f"metric_{i}_detail"]="Not selected for this report."
    queue=[]
    if "follow_up" in topics: queue.append(f"{display(q['uncontacted'])} recent leads overdue")
    if "waiting" in topics: queue.append(f"{display(q['waiting'])} waiting conversations; oldest {display(q['oldest_wait_hours'],' hours')}")
    if "ownership" in topics: queue.append(f"{display(q['unassigned'])} unassigned leads")
    fields['current_queue_text']=' · '.join(queue) if queue else 'Current queue topics are not selected.'
    if d.get('comparison',{}):
        change=d['comparison']['change']
        fields['metric_1_detail']=f"{display(d['comparison']['previous'])} {d['comparison'].get('label','in the prior period')} · "+(f"{change:+g}% change" if change is not None else "No comparable percentage.")
    html_parts=[]; text_parts=[]
    for i,action in enumerate(actions,1):
        title=safe_label(action["title"],200); detail=safe_label(action["detail"],1000); step=safe_label(action["next_step"],1000)
        fields.update({f"action_{i}_title":title,f"action_{i}_detail":detail,f"action_{i}_next_step":step})
        html_parts.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:14px;border:1px solid #e3eae9;"><tr><td style="padding:16px;"><h3 style="margin:0;font-size:17px;line-height:24px;">{i}. {escape(title)}</h3><p style="font-size:15px;line-height:23px;">{escape(detail)}</p><p style="padding:12px;background-color:#f4f7f6;font-size:15px;line-height:23px;"><b>Next step:</b> {escape(step)}</p></td></tr></table>')
        text_parts.append(f"{i}. {title}\n{detail}\nNext step: {step}")
    if not actions:
        text_parts.append(intro)
        html_parts.append(f'<p style="font-size:16px;line-height:24px;">{escape(intro)}</p>')
    for i in range(len(actions)+1,4):
        fields.update({f"action_{i}_{key}":"" for key in ("title","detail","next_step")})
    html_slots={"actions_html":"".join(html_parts),"improvement_html":"",
      "preview_banner":'<tr><td style="padding:12px;color:#ffffff;background:#1d2b32;">TEST — FICTIONAL DATA</td></tr>' if test else ""}
    fields["actions_text"]="\n\n".join(text_parts)
    def replace(match, html=False):
        key=match[1]
        if html and key in html_slots: return html_slots[key]
        value=fields[key]  # Unknown tokens fail rather than leaking placeholders.
        return escape(value,quote=True) if html else value
    fields["body_html"]=re.sub(r"%%([a-z0-9_]+)%%",lambda m:replace(m,True),(ROOT/"owner-weekly.html").read_text(encoding="utf-8"))
    fields["body_text"]=re.sub(r"%%([a-z0-9_]+)%%",replace,(ROOT/"owner-weekly.txt").read_text(encoding="utf-8"))
    if len(fields["body_html"].encode())>60000: raise ValueError("Report exceeds email size limit")
    return fields
