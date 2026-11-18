"""Job orchestration: parse -> dedupe -> crawl -> validate -> score -> AI -> persist."""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import uuid

from . import db
from .ai import ai_insights, heuristic_insights
from .config import AI_ENABLED, AI_MAX_LEADS, MAX_CONCURRENCY, MAX_LEADS_PER_JOB
from .crawler import crawl_company, make_client
from .extract import classify_email, find_people, normalize_domain
from .scoring import DEFAULT_ICP, score_lead
from .validate import validate_email

log = logging.getLogger("signal.pipeline")

# Header aliases so a SaaSquatch / Apollo / ZoomInfo / hand-made CSV all "just work".
ALIASES = {
    "company_name": ("company", "company name", "name", "business", "business name", "organization", "account"),
    "website": ("website", "domain", "url", "site", "web", "company website", "homepage"),
    "industry": ("industry", "category", "sector", "vertical"),
    "city": ("city", "town", "location"),
    "state": ("state", "region", "province"),
    "employees": ("employees", "employee count", "headcount", "size", "company size"),
    "email": ("email", "e-mail", "contact email"),
    "phone": ("phone", "telephone", "phone number"),
}


def parse_input(raw: str) -> list[dict]:
    """Accept a CSV (with any common headers) or one domain/URL per line."""
    raw = raw.strip().lstrip("﻿")
    if not raw:
        return []
    first = raw.splitlines()[0].lower()
    looks_csv = "," in first and any(a in first for alias in ALIASES.values() for a in alias)
    rows: list[dict] = []
    if looks_csv:
        reader = csv.DictReader(io.StringIO(raw))
        header_map = {}
        for h in reader.fieldnames or []:
            key = h.strip().lower()
            for field, aliases in ALIASES.items():
                if key in aliases and field not in header_map.values():
                    header_map[h] = field
                    break
        for r in reader:
            rows.append({header_map[h]: (v or "").strip() for h, v in r.items() if h in header_map})
    else:
        for line in raw.splitlines():
            token = re.split(r"[\s,;\t]+", line.strip())[0] if line.strip() else ""
            if token:
                rows.append({"website": token})
    return rows


def dedupe(rows: list[dict]) -> tuple[list[dict], int, list[dict]]:
    """Canonicalise domains and drop duplicates. Returns (unique, dup_count, invalid_rows)."""
    seen: dict[str, dict] = {}
    invalid, dups = [], 0
    for r in rows:
        domain = normalize_domain(r.get("website") or r.get("email", ""))
        if not domain:
            invalid.append(r)
            continue
        if domain in seen:
            dups += 1
            # keep the richest record: fill blanks from the duplicate
            for k, v in r.items():
                if v and not seen[domain].get(k):
                    seen[domain][k] = v
            continue
        seen[domain] = {**r, "domain": domain}
    return list(seen.values()), dups, invalid


async def enrich_one(client, row: dict, icp: dict) -> dict:
    domain = row["domain"]
    crawl = await crawl_company(client, domain)
    profile = crawl["profile"]

    emails = set(profile.get("emails", set()))
    if row.get("email"):
        emails.add(row["email"].lower())
    validations = await asyncio.gather(*(validate_email(e) for e in emails))
    email_rows = []
    for e, v in zip(emails, validations):
        email_rows.append({**classify_email(e, domain), **v})
    # verified + personal + on-domain first
    email_rows.sort(key=lambda e: (e["status"] != "valid", e["type"] != "personal", not e["domain_match"]))

    phones = sorted(profile.get("phones", set()))
    if row.get("phone") and row["phone"] not in phones:
        phones.insert(0, row["phone"])

    name = (row.get("company_name") or profile.get("site_name")
            or (profile.get("title", "").split("|")[0].split(" - ")[0].strip()) or domain)

    lead = {
        "domain": domain,
        "company_name": name[:120],
        "input": {k: v for k, v in row.items() if k != "domain"},
        "crawl": crawl["crawl"],
        "profile": {k: (sorted(v) if isinstance(v, set) else v) for k, v in profile.items()},
        "emails": email_rows,
        "phones": phones[:5],
        "contacts": find_people(profile.get("text_sample", "")),
    }
    lead["scores"] = score_lead(lead, icp)
    return lead


async def _add_insights(lead: dict, icp: dict, use_ai: bool) -> dict:
    insight = await ai_insights(lead, icp["playbook"]) if use_ai else None
    fallback = heuristic_insights(lead, icp["playbook"])
    if insight:
        # keep the model's facts, but never show empty sections when heuristics have something
        for key in ("pain_points", "ai_opportunities"):
            if not insight.get(key):
                insight[key] = fallback[key]
    lead["insights"] = insight or fallback
    # AI-found decision makers enrich contacts (deduped by name) -> rescore
    known = {c["name"].lower() for c in lead["contacts"]}
    for dm in lead["insights"].get("decision_makers", []):
        if dm.get("name") and dm["name"].lower() not in known:
            lead["contacts"].append({**dm, "source": "ai"})
            known.add(dm["name"].lower())
    lead["scores"] = score_lead(lead, icp)
    return lead


async def run_job(job_id: str, rows: list[dict], icp: dict) -> None:
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    leads: list[dict] = []
    async with make_client() as client:

        async def worker(row):
            async with sem:
                try:
                    lead = await enrich_one(client, row, icp)
                except Exception as e:  # one bad site must never kill the batch
                    log.exception("enrich failed for %s", row["domain"])
                    lead = {
                        "domain": row["domain"], "company_name": row.get("company_name") or row["domain"],
                        "input": row, "crawl": {"status": "error", "error": str(e)[:200]},
                        "profile": {}, "emails": [], "phones": [], "contacts": [],
                    }
                    lead["scores"] = score_lead(lead, icp)
                lead["insights"] = heuristic_insights(lead, icp["playbook"])
                db.save_lead(job_id, lead)
                db.increment_done(job_id)
                leads.append(lead)

        await asyncio.gather(*(worker(r) for r in rows))

    # Spend AI budget only on the leads that matter: top-N reachable by priority.
    # only fully-readable sites: AI on a bot wall / JS shell just produces noise
    ranked = sorted((l for l in leads if not l["scores"]["disqualified"]
                     and l.get("crawl", {}).get("status") == "ok"),
                    key=lambda l: l["scores"]["priority"], reverse=True)
    if AI_ENABLED and ranked:
        db.update_job(job_id, status="ai")
        await asyncio.gather(*(_add_insights(l, icp, True) for l in ranked[:AI_MAX_LEADS]))
        for l in ranked[:AI_MAX_LEADS]:
            db.save_lead(job_id, l)
    db.update_job(job_id, status="done")


def start_job(raw: str, icp: dict) -> dict:
    icp = {**DEFAULT_ICP, **{k: v for k, v in (icp or {}).items() if v not in (None, "")}}
    rows = parse_input(raw)
    unique, dups, invalid = dedupe(rows)
    unique = unique[:MAX_LEADS_PER_JOB]
    job_id = uuid.uuid4().hex[:12]
    db.create_job(job_id, len(unique), dups, icp)
    asyncio.get_running_loop().create_task(run_job(job_id, unique, icp))
    return {"job_id": job_id, "total": len(unique), "duplicates": dups, "invalid": len(invalid)}
