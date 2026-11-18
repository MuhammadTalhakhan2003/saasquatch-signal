import csv
import io

from app.export import to_csv
from app.extract import CURRENT_YEAR
from app.pipeline import dedupe, parse_input
from app.scoring import DEFAULT_ICP, score_lead


def make_lead(**profile):
    base = {
        "tech": {}, "owner_signals": [], "succession_signals": [], "recurring_hits": 0,
        "b2b_hits": 0, "enterprise_hits": 0, "socials": {}, "json_ld": {}, "has_contact_form": True,
    }
    base.update(profile)
    return {
        "domain": "acme.com", "company_name": "Acme", "input": {}, "crawl": {"status": "ok"},
        "profile": base, "emails": [], "phones": [], "contacts": [],
    }


def test_ideal_target_beats_young_franchise():
    ideal = make_lead(founded_year=CURRENT_YEAR - 35, owner_signals=["Family-owned"],
                      succession_signals=["Mentions retirement"], recurring_hits=3, employees=30,
                      copyright_year=CURRENT_YEAR - 4)
    ideal["emails"] = [{"email": "bob@acme.com", "type": "personal", "domain_match": True, "status": "valid"}]
    ideal["phones"] = ["(214) 867-5309"]
    ideal["contacts"] = [{"name": "Bob Smith", "title": "Owner"}]

    franchise = make_lead(founded_year=CURRENT_YEAR - 3, enterprise_hits=4,
                          tech={"HubSpot": "crm", "Google Analytics": "analytics", "Intercom": "chat"})

    s1, s2 = score_lead(ideal, DEFAULT_ICP), score_lead(franchise, DEFAULT_ICP)
    assert s1["tier"] == "A"
    assert s2["tier"] == "C"
    assert s1["priority"] > s2["priority"] + 30
    assert all("text" in r for r in s1["reasons"])  # explainable


def test_dead_site_is_disqualified():
    lead = make_lead()
    lead["crawl"] = {"status": "parked"}
    s = score_lead(lead, DEFAULT_ICP)
    assert s["tier"] == "X" and s["disqualified"]


def test_partial_data_does_not_inflate_digital_gap():
    lead = make_lead()
    lead["crawl"] = {"status": "blocked"}
    assert score_lead(lead, DEFAULT_ICP)["digital_gap"] == 0


def test_industry_filter_penalises_mismatch():
    lead = make_lead(title="Bob's Bakery")
    icp = {**DEFAULT_ICP, "industries": ["hvac"]}
    assert score_lead(lead, icp)["acquisition_fit"] < score_lead(lead, DEFAULT_ICP)["acquisition_fit"]


def test_parse_csv_with_header_aliases_and_dedupe():
    raw = "Company Name,Website,Industry\nAcme,https://www.acme.com/,HVAC\nAcme Dup,ACME.com,\nBad,not a site,\n"
    rows, dups, invalid = dedupe(parse_input(raw))
    assert len(rows) == 1 and dups == 1 and len(invalid) == 1
    assert rows[0]["domain"] == "acme.com" and rows[0]["industry"] == "HVAC"


def test_parse_plain_list():
    rows = parse_input("acme.com\nhttps://foo.org/page\n\n")
    assert [r["website"] for r in rows] == ["acme.com", "https://foo.org/page"]


def test_hubspot_export_columns():
    lead = make_lead(founded_year=1990)
    lead["contacts"] = [{"name": "Bob Smith", "title": "Owner"}]
    lead["scores"] = score_lead(lead, DEFAULT_ICP)
    lead["insights"] = {"opener": "Hi Bob", "industry": "HVAC", "summary": "x"}
    out = list(csv.DictReader(io.StringIO(to_csv([lead], "hubspot"))))
    assert out[0]["First Name"] == "Bob" and out[0]["Last Name"] == "Smith"
    assert out[0]["Year Founded"] == "1990" and out[0]["Outreach Opener"] == "Hi Bob"
