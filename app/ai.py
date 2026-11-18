"""AI insight layer.

With an Anthropic key: Claude reads the crawled text and returns a strict
JSON brief (summary, pain points, decision makers, personalised opener).
Without a key: deterministic templates built from the extracted signals,
so the product still works offline / at zero cost.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from .config import AI_PROVIDER, ANTHROPIC_MODEL, GROQ_API_KEY, GROQ_MODEL

log = logging.getLogger("signal.ai")

INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2 sentences: what the company does and for whom"},
        "industry": {"type": "string"},
        "business_model": {"type": "string", "description": "e.g. recurring service contracts, project-based, retail"},
        "pain_points": {"type": "array", "items": {"type": "string"}, "description": "max 3, specific to this business"},
        "ai_opportunities": {"type": "array", "items": {"type": "string"}, "description": "max 3 concrete AI/automation levers"},
        "decision_makers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "title": {"type": "string"}},
                "required": ["name", "title"],
                "additionalProperties": False,
            },
        },
        "opener": {"type": "string", "description": "personalised first line of a cold email, <= 40 words"},
        "red_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "industry", "business_model", "pain_points", "ai_opportunities",
                 "decision_makers", "opener", "red_flags"],
    "additionalProperties": False,
}

SYSTEM = (
    "You are a lead-research analyst at a private-equity firm that acquires and modernises "
    "established, owner-operated small businesses and helps them adopt AI after the deal. "
    "You read a company's website text and produce a short, factual brief for an outreach rep. "
    "Only state facts supported by the text; when something is unknown, say so or leave lists empty. "
    "Decision makers must be people explicitly named on the site. The opener must reference a concrete "
    "detail from the site, sound human, avoid flattery and never claim facts not in the text."
)

_client = None
_sem = asyncio.Semaphore(4)


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.AsyncAnthropic()
    return _client


def _context(lead: dict, playbook: str) -> dict:
    p = lead.get("profile", {})
    return {
        "domain": lead["domain"],
        "company_name": lead.get("company_name"),
        "title": p.get("title"),
        "meta_description": p.get("description"),
        "founded_year": p.get("founded_year"),
        "detected_tech": list(p.get("tech", {}).keys()),
        "outreach_goal": "acquisition conversation with the owner" if playbook == "acquisition"
        else "selling AI / automation services",
        "website_text": p.get("text_sample", "")[:4500],
    }


def _normalize(data: dict, source: str) -> dict | None:
    """Coerce model output to the schema shape; reject if the core fields are missing."""
    if not isinstance(data, dict) or not data.get("summary") or not data.get("opener"):
        return None
    out = {}
    for key, spec in INSIGHT_SCHEMA["properties"].items():
        val = data.get(key)
        if spec["type"] == "array":
            val = val if isinstance(val, list) else []
            if key == "decision_makers":
                val = [{"name": str(d.get("name", "")).strip(), "title": str(d.get("title", "")).strip()}
                       for d in val if isinstance(d, dict) and d.get("name")]
            else:
                val = [str(x) for x in val][:3]
        else:
            val = str(val or "")
        out[key] = val
    out["source"] = source
    return out


async def ai_insights(lead: dict, playbook: str) -> dict | None:
    if AI_PROVIDER == "anthropic":
        return await claude_insights(lead, playbook)
    if AI_PROVIDER == "groq":
        return await groq_insights(lead, playbook)
    return None


async def groq_insights(lead: dict, playbook: str) -> dict | None:
    """Groq (OpenAI-compatible chat completions) in JSON mode."""
    schema_hint = json.dumps(INSIGHT_SCHEMA["properties"], indent=None)
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0.3,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM + " Respond with a single JSON object only, with exactly "
             "these keys and types: " + schema_hint},
            {"role": "user", "content": json.dumps(_context(lead, playbook))},
        ],
    }
    for attempt in range(3):
        try:
            async with _sem, httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                    json=payload,
                )
            if r.status_code == 429 or r.status_code >= 500:
                retry = r.headers.get("retry-after", "")
                await asyncio.sleep(min(float(retry) if retry.replace(".", "").isdigit() else 2 ** attempt, 10))
                continue
            if r.status_code != 200:
                log.warning("Groq error %s on %s: %s", r.status_code, lead["domain"], r.text[:200])
                return None
            content = r.json()["choices"][0]["message"]["content"]
            return _normalize(json.loads(content), "groq")
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
            log.warning("Groq call failed for %s: %s", lead["domain"], e)
            return None
    log.warning("Groq rate-limited on %s - using heuristic insight", lead["domain"])
    return None


async def claude_insights(lead: dict, playbook: str) -> dict | None:
    context = _context(lead, playbook)
    try:
        import anthropic

        async with _sem:
            resp = await _get_client().beta.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=4000,
                system=SYSTEM,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": INSIGHT_SCHEMA}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": json.dumps(context)}],
            )
        if resp.stop_reason in ("refusal", "max_tokens"):
            log.warning("Claude stop_reason=%s for %s", resp.stop_reason, lead["domain"])
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return _normalize(json.loads(text), "claude")
    except anthropic.RateLimitError:
        log.warning("Rate limited on %s - using heuristic insight", lead["domain"])
    except anthropic.APIStatusError as e:
        log.warning("Claude API error %s on %s", e.status_code, lead["domain"])
    except (anthropic.APIConnectionError, json.JSONDecodeError) as e:
        log.warning("Claude call failed for %s: %s", lead["domain"], e)
    return None


def heuristic_insights(lead: dict, playbook: str) -> dict:
    p = lead.get("profile", {})
    name = lead.get("company_name") or lead["domain"]
    possessive = name + ("'" if name.endswith("s") else "'s")
    desc = p.get("description") or p.get("title") or ""
    founded = p.get("founded_year")
    tech = p.get("tech", {})
    cats = set(tech.values())

    pains, opps = [], []
    if "crm" not in cats:
        pains.append("Leads and customer follow-ups likely tracked manually (no CRM detected)")
        opps.append("CRM + automated follow-up sequences to recover lost quotes")
    if "chat" not in cats and "scheduling" not in cats:
        pains.append("No online booking or chat - after-hours inquiries are lost")
        opps.append("AI receptionist / chat that books jobs 24/7")
    if p.get("copyright_year") and p["copyright_year"] < 2024:
        pains.append(f"Website last refreshed ~{p['copyright_year']}")
        opps.append("Modern site + local SEO to grow inbound demand")
    if p.get("recurring_hits"):
        opps.append("Predictive renewal / churn alerts on service contracts")

    if playbook == "acquisition":
        hook = f"{possessive} {CURRENT_AGE(founded)}track record" if founded else f"what you've built at {name}"
        opener = (f"I've been reading about {hook} - we partner with owners of established businesses "
                  f"on succession and growth, and wanted to ask a few questions about your plans.")
    else:
        opener = (f"Noticed {name} doesn't offer online booking yet - we help businesses like yours "
                  f"capture after-hours inquiries automatically. Worth a quick look?")
    return {
        "summary": (desc[:220] + ("..." if len(desc) > 220 else "")) or f"{name} - no description found on site.",
        "industry": lead.get("input", {}).get("industry") or "Unknown",
        "business_model": "Recurring service contracts" if p.get("recurring_hits", 0) >= 2 else "Unknown",
        "pain_points": pains[:3],
        "ai_opportunities": opps[:3],
        "decision_makers": [],
        "opener": opener,
        "red_flags": [],
        "source": "heuristic",
    }


def CURRENT_AGE(founded: int | None) -> str:  # noqa: N802 - tiny template helper
    from .extract import CURRENT_YEAR

    return f"{CURRENT_YEAR - founded}-year " if founded else ""
