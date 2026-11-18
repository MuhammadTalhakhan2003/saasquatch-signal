"""CSV exports shaped for the tools sales teams already use."""
from __future__ import annotations

import csv
import io


def _best_email(lead: dict) -> str:
    good = [e for e in lead.get("emails", []) if e["status"] != "invalid"]
    return good[0]["email"] if good else ""


def _contact(lead: dict) -> tuple[str, str, str]:
    c = (lead.get("contacts") or [{}])[0]
    parts = (c.get("name") or "").split(" ", 1)
    return parts[0] if parts else "", parts[1] if len(parts) > 1 else "", c.get("title", "")


def _reasons(lead: dict, n: int = 4) -> str:
    rs = sorted(lead["scores"]["reasons"], key=lambda r: -abs(r["points"]))
    return "; ".join(r["text"] for r in rs[:n] if r["points"])


def to_csv(leads: list[dict], fmt: str = "standard") -> str:
    buf = io.StringIO()
    if fmt == "hubspot":
        cols = ["Company name", "Company Domain Name", "First Name", "Last Name", "Job Title", "Email",
                "Phone Number", "City", "State/Region", "Industry", "Year Founded", "LinkedIn Company Page",
                "Lead Status", "Lead Score", "Lead Tier", "Score Notes", "Outreach Opener"]
    elif fmt == "salesforce":
        cols = ["Company", "Website", "FirstName", "LastName", "Title", "Email", "Phone", "City", "State",
                "Industry", "Rating", "Description", "LeadSource"]
    else:
        cols = ["tier", "priority", "reachability", "acquisition_fit", "digital_gap", "company", "domain",
                "contact_name", "contact_title", "email", "email_status", "all_emails", "phone", "linkedin",
                "founded_year", "employees", "city", "state", "industry", "tech_stack", "owner_signals",
                "why", "summary", "opener", "crawl_status"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for l in leads:
        s, p, ins = l["scores"], l.get("profile", {}), l.get("insights", {})
        first, last, title = _contact(l)
        email = _best_email(l)
        jl, inp = p.get("json_ld", {}), l.get("input", {})
        city = jl.get("city") or inp.get("city", "")
        state = jl.get("state") or inp.get("state", "")
        industry = ins.get("industry") if ins.get("industry") not in (None, "Unknown") else inp.get("industry", "")
        phone = (l.get("phones") or [""])[0]
        linkedin = p.get("socials", {}).get("linkedin", "")
        if fmt == "hubspot":
            w.writerow({
                "Company name": l["company_name"], "Company Domain Name": l["domain"], "First Name": first,
                "Last Name": last, "Job Title": title, "Email": email, "Phone Number": phone, "City": city,
                "State/Region": state, "Industry": industry, "Year Founded": p.get("founded_year") or "",
                "LinkedIn Company Page": linkedin, "Lead Status": "NEW", "Lead Score": s["priority"],
                "Lead Tier": s["tier"], "Score Notes": _reasons(l), "Outreach Opener": ins.get("opener", ""),
            })
        elif fmt == "salesforce":
            w.writerow({
                "Company": l["company_name"], "Website": l["domain"], "FirstName": first,
                "LastName": last or l["company_name"], "Title": title, "Email": email, "Phone": phone,
                "City": city, "State": state, "Industry": industry,
                "Rating": {"A": "Hot", "B": "Warm"}.get(s["tier"], "Cold"),
                "Description": f"Score {s['priority']} - {_reasons(l)}\n\n{ins.get('summary', '')}",
                "LeadSource": "SaaSquatch Signal",
            })
        else:
            emails = l.get("emails", [])
            w.writerow({
                "tier": s["tier"], "priority": s["priority"], "reachability": s["reachability"],
                "acquisition_fit": s["acquisition_fit"], "digital_gap": s["digital_gap"],
                "company": l["company_name"], "domain": l["domain"],
                "contact_name": f"{first} {last}".strip(), "contact_title": title, "email": email,
                "email_status": emails[0]["status"] if emails else "",
                "all_emails": " | ".join(e["email"] for e in emails), "phone": phone, "linkedin": linkedin,
                "founded_year": p.get("founded_year") or "", "employees": p.get("employees") or inp.get("employees", ""),
                "city": city, "state": state, "industry": industry,
                "tech_stack": ", ".join(p.get("tech", {}).keys()),
                "owner_signals": ", ".join(p.get("owner_signals", [])), "why": _reasons(l),
                "summary": ins.get("summary", ""), "opener": ins.get("opener", ""),
                "crawl_status": l.get("crawl", {}).get("status", ""),
            })
    return buf.getvalue()
