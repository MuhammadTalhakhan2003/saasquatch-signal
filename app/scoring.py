"""Explainable lead scoring.

Three sub-scores (0-100), each built from human-readable reasons so a rep
can see *why* a lead ranks where it does - black-box scores don't get used.

- reachability    : can we actually reach a decision maker? (data quality)
- acquisition_fit : does this look like a Caprae-style target? (ETA thesis:
                    established, owner-operated, SMB, recurring, B2B)
- digital_gap     : how much AI/tech value-creation upside post-close?

`priority` blends them according to the selected playbook.
"""
from __future__ import annotations

from .extract import CURRENT_YEAR

PLAYBOOKS = {
    # Caprae's own use: sourcing acquisition targets
    "acquisition": {"reachability": 0.30, "acquisition_fit": 0.50, "digital_gap": 0.20},
    # Selling AI / software services into SMBs (SaaSquatch customers' use)
    "sales": {"reachability": 0.50, "acquisition_fit": 0.15, "digital_gap": 0.35},
}

DEFAULT_ICP = {
    "playbook": "acquisition",
    "industries": [],          # keywords; empty = any industry
    "min_years": 10,
    "min_employees": 5,
    "max_employees": 250,
    "states": [],              # e.g. ["TX", "FL"]; empty = anywhere
}


def _clamp(x: float) -> int:
    return int(max(0, min(100, round(x))))


def score_lead(lead: dict, icp: dict) -> dict:
    icp = {**DEFAULT_ICP, **(icp or {})}
    p = lead.get("profile", {})
    reasons: list[dict] = []

    def add(bucket: str, pts: float, text: str):
        reasons.append({"bucket": bucket, "points": pts, "text": text})
        return pts

    # ------------------------------------------------------------ reachability
    r = 0.0
    emails = lead.get("emails", [])
    valid_personal = [e for e in emails if e["status"] == "valid" and e["type"] == "personal" and e["domain_match"]]
    valid_role = [e for e in emails if e["status"] == "valid" and e["type"] == "role"]
    if valid_personal:
        r += add("reachability", 40, f"Verified named email ({valid_personal[0]['email']})")
    elif valid_role:
        r += add("reachability", 25, f"Verified company inbox ({valid_role[0]['email']})")
    elif emails:
        r += add("reachability", 10, "Email found but unverified")
    else:
        add("reachability", 0, "No email found")
    if lead.get("phones"):
        r += add("reachability", 20, "Direct phone number")
    if p.get("socials", {}).get("linkedin"):
        r += add("reachability", 15, "LinkedIn company page")
    if lead.get("contacts"):
        r += add("reachability", 15, f"Named decision maker: {lead['contacts'][0]['name']}")
    if p.get("has_contact_form"):
        r += add("reachability", 5, "Contact form available")
    if p.get("json_ld", {}).get("address") or lead.get("input", {}).get("city"):
        r += add("reachability", 5, "Physical address known")

    # --------------------------------------------------------- acquisition fit
    a = 0.0
    founded = p.get("founded_year")
    if founded:
        age = CURRENT_YEAR - founded
        if age >= max(icp["min_years"], 20):
            a += add("acquisition_fit", 30, f"Established {age} yrs (since {founded}) - durable cash flows")
        elif age >= icp["min_years"]:
            a += add("acquisition_fit", 22, f"In business {age} yrs (since {founded})")
        elif age >= 5:
            a += add("acquisition_fit", 8, f"Only {age} yrs old - below ICP minimum")
        else:
            a += add("acquisition_fit", -10, f"Young company ({age} yrs) - higher risk")
    else:
        a += add("acquisition_fit", 8, "Founding year unknown")

    owner = p.get("owner_signals", [])
    if owner:
        a += add("acquisition_fit", 18, "Owner signals: " + ", ".join(sorted(owner)))
    succ = p.get("succession_signals", [])
    if succ:
        a += add("acquisition_fit", 15, "Succession signals: " + ", ".join(sorted(succ)))

    emp = p.get("employees") or lead.get("input", {}).get("employees")
    try:
        emp = int(emp) if emp else None
    except (TypeError, ValueError):
        emp = None
    if emp:
        if icp["min_employees"] <= emp <= icp["max_employees"]:
            a += add("acquisition_fit", 12, f"~{emp} employees - in SMB sweet spot")
        else:
            a += add("acquisition_fit", -5, f"~{emp} employees - outside target size")

    if p.get("recurring_hits", 0) >= 2:
        a += add("acquisition_fit", 12, "Recurring revenue signals (service plans / contracts)")
    elif p.get("recurring_hits", 0) == 1:
        a += add("acquisition_fit", 6, "Some recurring revenue language")
    if p.get("b2b_hits", 0) >= 3:
        a += add("acquisition_fit", 8, "B2B / commercial customer base")
    ent = p.get("enterprise_hits", 0)
    if ent >= 3:
        a += add("acquisition_fit", -25, "Franchise / national enterprise - not an owner-led SMB")
    elif ent >= 1:
        a += add("acquisition_fit", -12, "Franchise / multi-location language")
    if any(ph.startswith(("(800)", "(888)", "(877)", "(866)", "(855)", "(844)", "(833)")) for ph in lead.get("phones", [])[:1]):
        a += add("acquisition_fit", -6, "Toll-free national number - likely larger operator")

    hay = " ".join(
        str(x) for x in (p.get("title"), p.get("description"), lead.get("input", {}).get("industry"))
    ).lower()
    if icp["industries"]:
        hits = [kw for kw in icp["industries"] if kw.lower() in hay]
        if hits:
            a += add("acquisition_fit", 10, "Matches target industry: " + ", ".join(hits))
        else:
            a += add("acquisition_fit", -15, "Outside target industries")
    state = (p.get("json_ld", {}).get("state") or lead.get("input", {}).get("state") or "").upper()
    if icp["states"] and state:
        if state in [s.upper() for s in icp["states"]]:
            a += add("acquisition_fit", 5, f"In target geography ({state})")
        else:
            a += add("acquisition_fit", -10, f"Outside target geography ({state})")

    # ------------------------------------------------------------ digital gap
    g = 0.0
    tech = p.get("tech", {})
    cats = set(tech.values())
    cy = p.get("copyright_year")
    crawl_status = lead.get("crawl", {}).get("status")
    if crawl_status != "ok":
        # We can't see the stack, so don't invent upside from missing data.
        add("digital_gap", 0, "Digital maturity unknown (site not fully readable)")
    else:
        if cy and CURRENT_YEAR - cy >= 2:
            g += add("digital_gap", 25, f"Website not updated since ~{cy}")
        if "crm" not in cats and "marketing" not in cats:
            g += add("digital_gap", 25, "No CRM / marketing automation detected")
        if "analytics" not in cats:
            g += add("digital_gap", 15, "No web analytics")
        if "chat" not in cats and "scheduling" not in cats:
            g += add("digital_gap", 15, "No chat or online booking")
        if tech.keys() & {"Wix", "GoDaddy Builder"}:
            g += add("digital_gap", 10, "DIY site builder")
        if not tech:
            g += add("digital_gap", 10, "Minimal web stack")
        modern = [k for k, v in tech.items() if v in {"crm", "vertical-saas"}]
        if modern:
            g += add("digital_gap", -10, "Already on modern ops software: " + ", ".join(modern))

    sub = {"reachability": _clamp(r), "acquisition_fit": _clamp(a), "digital_gap": _clamp(g)}
    weights = PLAYBOOKS.get(icp["playbook"], PLAYBOOKS["acquisition"])
    priority = sum(sub[k] * w for k, w in weights.items())

    status = lead.get("crawl", {}).get("status")
    disqualified = None
    if status in ("unreachable", "parked"):
        disqualified = "Website unreachable" if status == "unreachable" else "Parked / for-sale domain"
        priority *= 0.3
    elif status in ("blocked", "thin", "error"):
        why = {"blocked": "Site behind bot-protection", "thin": "JS-rendered / near-empty site",
               "error": "Crawl error"}[status]
        reasons.append({"bucket": "data", "points": 0, "text": f"{why} - partial data, verify manually"})
        priority *= 0.85

    priority = _clamp(priority)
    if disqualified:
        tier = "X"
    elif priority >= 60:
        tier = "A"
    elif priority >= 40:
        tier = "B"
    else:
        tier = "C"
    return {**sub, "priority": priority, "tier": tier, "disqualified": disqualified, "reasons": reasons}
