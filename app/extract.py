"""HTML -> structured signals.

Pure functions (no network) so they are fast to unit-test. Each extractor is
defensive: real SMB websites are messy (builders, obfuscated emails, JSON-LD
with odd shapes), so every parser degrades gracefully instead of raising.
"""
from __future__ import annotations

import json
import re
from datetime import date
from html import unescape
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

CURRENT_YEAR = date.today().year

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")
# "john [at] acme [dot] com", "john(at)acme.com"
OBFUSCATED_EMAIL_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*[\[\(\{]\s*at\s*[\]\)\}]\s*([A-Za-z0-9\-]+)"
    r"\s*(?:[\[\(\{]\s*dot\s*[\]\)\}]|\.)\s*([A-Za-z]{2,24})",
    re.I,
)
PHONE_RE = re.compile(
    r"(?:\+?1[\s.\-]?)?\(?\b([2-9]\d{2})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})\b"
)
YEAR_FOUNDED_RE = re.compile(
    r"(?:since|founded(?:\s+in)?|established(?:\s+in)?|est\.?|serving\s+\w+(?:\s+\w+)?\s+since)\s*(?:in\s*)?((?:18|19|20)\d{2})",
    re.I,
)
YEARS_IN_BUSINESS_RE = re.compile(r"\b(\d{2,3})\+?\s+years?\s+(?:of\s+)?(?:experience|in business|serving)", re.I)
COPYRIGHT_RE = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", re.I)
EMPLOYEE_RE = re.compile(r"\b(\d{1,4})\+?\s+(?:employees|team members|staff|professionals|technicians)", re.I)

JUNK_EMAIL_PARTS = (
    "example.", "sentry", "wixpress", "domain.com", "email.com", "yourdomain",
    "@2x", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", "godaddy", "squarespace",
)
ROLE_PREFIXES = {
    "info", "contact", "hello", "sales", "support", "admin", "office", "team",
    "service", "help", "enquiries", "inquiries", "billing", "hr", "jobs",
    "careers", "marketing", "noreply", "no-reply", "webmaster", "mail",
}

SOCIAL_PATTERNS = {
    "linkedin": re.compile(r"linkedin\.com/(?:company|in)/[^/?#\"'\s]+", re.I),
    "facebook": re.compile(r"facebook\.com/(?!sharer|share|dialog|plugins|tr\b)[^/?#\"'\s]+", re.I),
    "twitter": re.compile(r"(?:twitter|x)\.com/(?!share|intent|home)[A-Za-z0-9_]{2,}", re.I),
    "instagram": re.compile(r"instagram\.com/(?!p/|explore)[A-Za-z0-9_.]{2,}", re.I),
    "youtube": re.compile(r"youtube\.com/(?:c/|channel/|user/|@)[^/?#\"'\s]+", re.I),
}

# fingerprint -> (technology, category)
TECH_FINGERPRINTS = [
    ("wp-content", "WordPress", "cms"),
    ("cdn.shopify.com", "Shopify", "ecommerce"),
    ("static.wixstatic.com", "Wix", "cms"),
    ("squarespace.com", "Squarespace", "cms"),
    ("webflow.com", "Webflow", "cms"),
    ("godaddy", "GoDaddy Builder", "cms"),
    ("woocommerce", "WooCommerce", "ecommerce"),
    ("js.hs-scripts.com", "HubSpot", "crm"),
    ("hs-analytics", "HubSpot", "crm"),
    ("salesforce", "Salesforce", "crm"),
    ("pardot", "Pardot", "marketing"),
    ("marketo", "Marketo", "marketing"),
    ("klaviyo", "Klaviyo", "marketing"),
    ("mailchimp", "Mailchimp", "marketing"),
    ("googletagmanager.com", "Google Tag Manager", "analytics"),
    ("google-analytics.com", "Google Analytics", "analytics"),
    ("gtag(", "Google Analytics", "analytics"),
    ("connect.facebook.net", "Meta Pixel", "analytics"),
    ("intercom", "Intercom", "chat"),
    ("drift.com", "Drift", "chat"),
    ("tawk.to", "Tawk.to", "chat"),
    ("zendesk", "Zendesk", "support"),
    ("calendly.com", "Calendly", "scheduling"),
    ("servicetitan", "ServiceTitan", "vertical-saas"),
    ("housecallpro", "Housecall Pro", "vertical-saas"),
    ("jobber", "Jobber", "vertical-saas"),
    ("stripe.com", "Stripe", "payments"),
    ("react", "React", "frontend"),
    ("jquery", "jQuery", "frontend"),
]

OWNER_SIGNALS = [
    (re.compile(r"family[\s-]owned", re.I), "Family-owned"),
    (re.compile(r"owner[\s-]operated", re.I), "Owner-operated"),
    (re.compile(r"locally owned", re.I), "Locally owned"),
    (re.compile(r"(?:second|third|fourth|2nd|3rd|4th)[\s-]generation", re.I), "Multi-generation business"),
    (re.compile(r"\bfounder\b|\bour founder\b", re.I), "Founder-led"),
    (re.compile(r"veteran[\s-]owned", re.I), "Veteran-owned"),
]
SUCCESSION_SIGNALS = [
    (re.compile(r"retir(?:e|ing|ement)", re.I), "Mentions retirement"),
    (re.compile(r"succession", re.I), "Mentions succession"),
    (re.compile(r"business for sale|selling (?:the|our) business", re.I), "Business for sale"),
]
GROWTH_SIGNALS = [
    (re.compile(r"\b(?:we'?re hiring|now hiring|join our team|careers)\b", re.I), "Hiring"),
    (re.compile(r"\b(?:new location|now open|expanding|expansion)\b", re.I), "Expanding"),
    (re.compile(r"\b(?:award|best of|top rated|5[- ]star)\b", re.I), "Reputation / awards"),
]
B2B_SIGNALS = re.compile(
    r"\b(?:commercial|industrial|b2b|wholesale|distribut\w+|manufactur\w+|contract(?:s|ing)?|"
    r"enterprise|municipal|government|facilit(?:y|ies))\b",
    re.I,
)
RECURRING_SIGNALS = re.compile(
    r"\b(?:maintenance (?:plan|agreement|contract)s?|service (?:plan|agreement|contract)s?|"
    r"subscription|membership|monthly plan|recurring|managed services?|retainer)\b",
    re.I,
)

ENTERPRISE_SIGNALS = re.compile(
    r"\b(?:franchis(?:e|ing|ee)s?|locations nationwide|nationwide network|publicly traded|nyse|nasdaq|"
    r"subsidiary of|a division of|fortune 500|\d{3,}\+?\s+locations|in all 50 states)\b",
    re.I,
)

CONTACT_PAGE_HINTS =("contact", "about", "team", "leadership", "our-story", "company", "staff", "who-we-are")


def normalize_domain(value: str) -> str | None:
    """'https://www.Acme.com/about?x=1' -> 'acme.com'. Returns None if unusable."""
    if not value:
        return None
    value = value.strip().lower()
    if "@" in value and "/" not in value:
        value = value.split("@", 1)[1]
    if not re.match(r"^[a-z]+://", value):
        value = "http://" + value
    host = urlparse(value).hostname or ""
    host = host.removeprefix("www.").strip(".")
    if "." not in host or " " in host:
        return None
    return host


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def _decode_cf_email(encoded: str) -> str | None:
    """Cloudflare email protection: hex string XOR-ed with its first byte."""
    try:
        key = int(encoded[:2], 16)
        return "".join(chr(int(encoded[i:i + 2], 16) ^ key) for i in range(2, len(encoded), 2))
    except ValueError:
        return None


def extract_emails(html: str, text: str) -> set[str]:
    found: set[str] = set()
    raw = unescape(html)
    found.update(m.group(0) for m in EMAIL_RE.finditer(raw))
    for m in re.finditer(r'mailto:([^"\'?>\s]+)', raw, re.I):
        found.add(m.group(1))
    for m in OBFUSCATED_EMAIL_RE.finditer(text):
        found.add(f"{m.group(1)}@{m.group(2)}.{m.group(3)}")
    for m in re.finditer(r'data-cfemail="([0-9a-fA-F]+)"', html):
        decoded = _decode_cf_email(m.group(1))
        if decoded:
            found.add(decoded)
    cleaned = set()
    for e in found:
        e = e.strip().strip(".").lower()
        if any(j in e for j in JUNK_EMAIL_PARTS) or len(e) > 80:
            continue
        if EMAIL_RE.fullmatch(e):
            cleaned.add(e)
    return cleaned


def extract_phones(text: str) -> set[str]:
    phones = set()
    for m in PHONE_RE.finditer(text):
        area, mid, last = m.groups()
        if mid == "555" or len(set(area + mid + last)) <= 2:
            continue
        phones.add(f"({area}) {mid}-{last}")
    return phones


def extract_socials(html: str) -> dict[str, str]:
    socials = {}
    for name, pattern in SOCIAL_PATTERNS.items():
        m = pattern.search(html)
        if m:
            socials[name] = "https://" + m.group(0).rstrip("/")
    return socials


def extract_tech(html: str) -> dict[str, str]:
    lower = html.lower()
    return {tech: cat for needle, tech, cat in TECH_FINGERPRINTS if needle in lower}


def extract_json_ld(soup: BeautifulSoup) -> dict:
    """Pull Organization / LocalBusiness facts from schema.org JSON-LD."""
    out: dict = {}
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = item.get("@type", "")
            kind = " ".join(kind) if isinstance(kind, list) else str(kind)
            if not re.search(r"Organization|Business|Store|Service|Contractor|Company|Corporation", kind, re.I):
                continue
            out.setdefault("schema_type", kind)
            for key in ("name", "telephone", "email", "foundingDate", "numberOfEmployees", "description"):
                if item.get(key) and key not in out:
                    val = item[key]
                    if isinstance(val, dict):
                        val = val.get("value") or val.get("minValue") or ""
                    out[key] = str(val)
            addr = item.get("address")
            if isinstance(addr, dict) and "address" not in out:
                parts = [addr.get(k, "") for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
                out["address"] = ", ".join(p for p in parts if p)
                out["city"] = addr.get("addressLocality", "")
                out["state"] = addr.get("addressRegion", "")
    return out


def discover_links(html: str, base_url: str) -> list[str]:
    """Same-domain pages likely to hold contact / leadership info, best first."""
    soup = BeautifulSoup(html, "html.parser")
    base_host = normalize_domain(base_url)
    scored: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if normalize_domain(url) != base_host:
            continue
        haystack = (urlparse(url).path + " " + a.get_text(" ")).lower()
        for rank, hint in enumerate(CONTACT_PAGE_HINTS):
            if hint in haystack:
                scored[url] = min(scored.get(url, 99), rank)
                break
    return [u for u, _ in sorted(scored.items(), key=lambda kv: kv[1])]


def _first_year(regex: re.Pattern, text: str, lo: int, hi: int) -> int | None:
    years = [int(m.group(1)) for m in regex.finditer(text)]
    years = [y for y in years if lo <= y <= hi]
    return min(years) if years else None


def extract_page(html: str, url: str) -> dict:
    """Extract every signal we care about from one HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    description = (meta.get("content") or "").strip() if meta else ""
    og_name = soup.find("meta", attrs={"property": "og:site_name"})
    json_ld = extract_json_ld(soup)
    text = _visible_text(soup)  # NB: mutates soup, so run after JSON-LD

    founded = _first_year(YEAR_FOUNDED_RE, text, 1850, CURRENT_YEAR)
    if not founded and json_ld.get("foundingDate"):
        m = re.search(r"(18|19|20)\d{2}", json_ld["foundingDate"])
        founded = int(m.group(0)) if m else None
    if not founded:
        m = YEARS_IN_BUSINESS_RE.search(text)
        if m and 3 <= int(m.group(1)) <= 150:
            founded = CURRENT_YEAR - int(m.group(1))

    employees = None
    m = EMPLOYEE_RE.search(text)
    if m:
        employees = int(m.group(1))
    elif json_ld.get("numberOfEmployees", "").isdigit():
        employees = int(json_ld["numberOfEmployees"])

    return {
        "url": url,
        "title": title[:200],
        "description": description[:500] or json_ld.get("description", "")[:500],
        "site_name": (og_name.get("content") if og_name else "") or json_ld.get("name", ""),
        "emails": extract_emails(html, text) | ({json_ld["email"].lower()} if json_ld.get("email") else set()),
        "phones": extract_phones(text) | extract_phones(json_ld.get("telephone", "")),
        "socials": extract_socials(html),
        "tech": extract_tech(html),
        "json_ld": json_ld,
        "founded_year": founded,
        "copyright_year": _first_year(COPYRIGHT_RE, text, 1995, CURRENT_YEAR + 1),
        "employees": employees,
        "owner_signals": {label for rx, label in OWNER_SIGNALS if rx.search(text)},
        "succession_signals": {label for rx, label in SUCCESSION_SIGNALS if rx.search(text)},
        "growth_signals": {label for rx, label in GROWTH_SIGNALS if rx.search(text)},
        "b2b_hits": len(B2B_SIGNALS.findall(text)),
        "recurring_hits": len(RECURRING_SIGNALS.findall(text)),
        "enterprise_hits": len(ENTERPRISE_SIGNALS.findall(text)),
        "has_contact_form": bool(soup.find("form")) or "contact form" in text.lower(),
        "text_sample": text[:2500],
        "word_count": len(text.split()),
    }


def merge_pages(pages: list[dict]) -> dict:
    """Combine per-page extractions into one company profile."""
    if not pages:
        return {}
    merged = {
        "emails": set(), "phones": set(), "socials": {}, "tech": {}, "json_ld": {},
        "owner_signals": set(), "succession_signals": set(), "growth_signals": set(),
        "b2b_hits": 0, "recurring_hits": 0, "enterprise_hits": 0, "has_contact_form": False,
        "pages_crawled": [p["url"] for p in pages],
    }
    home = pages[0]
    for key in ("title", "description", "site_name"):
        merged[key] = home.get(key) or next((p[key] for p in pages if p.get(key)), "")
    for p in pages:
        merged["emails"] |= p["emails"]
        merged["phones"] |= p["phones"]
        merged["owner_signals"] |= p["owner_signals"]
        merged["succession_signals"] |= p["succession_signals"]
        merged["growth_signals"] |= p["growth_signals"]
        merged["b2b_hits"] += p["b2b_hits"]
        merged["recurring_hits"] += p["recurring_hits"]
        merged["enterprise_hits"] += p.get("enterprise_hits", 0)
        merged["has_contact_form"] |= p["has_contact_form"]
        for k, v in p["socials"].items():
            merged["socials"].setdefault(k, v)
        merged["tech"].update(p["tech"])
        for k, v in p["json_ld"].items():
            merged["json_ld"].setdefault(k, v)
    founded = [p["founded_year"] for p in pages if p.get("founded_year")]
    merged["founded_year"] = min(founded) if founded else None
    cy = [p["copyright_year"] for p in pages if p.get("copyright_year")]
    merged["copyright_year"] = max(cy) if cy else None
    emp = [p["employees"] for p in pages if p.get("employees")]
    merged["employees"] = max(emp) if emp else None
    merged["text_sample"] = " ".join(p["text_sample"] for p in pages)[:5000]
    merged["word_count"] = sum(p.get("word_count", 0) for p in pages)
    return merged


DECISION_TITLES = (
    r"Owner|Co-Owner|Founder|Co-Founder|President|CEO|Chief Executive Officer|"
    r"Managing Partner|Managing Director|Principal|General Manager|Operations Manager|COO|CFO"
)
_NAME = r"([A-Z][a-z]+(?:\s[A-Z]\.)?\s[A-Z][a-zA-Z'\-]+)"
PERSON_PATTERNS = [
    re.compile(_NAME + r"\s*[,\-–|:]\s*(?:the\s+)?(" + DECISION_TITLES + r")\b"),
    re.compile(r"\b(" + DECISION_TITLES + r")\s*[:\-–,]\s*" + _NAME),
    re.compile(r"\b(?i:owned|founded|run|led)\s+by\s+" + _NAME),
]
NOT_NAMES = {"Contact Us", "About Us", "Our Team", "Read More", "Learn More", "Privacy Policy", "Terms Of"}


def find_people(text: str) -> list[dict]:
    """Best-effort decision-maker extraction ('Jane Doe, Owner')."""
    people: dict[str, str] = {}
    for i, rx in enumerate(PERSON_PATTERNS):
        for m in rx.finditer(text):
            if i == 0:
                name, title = m.group(1), m.group(2)
            elif i == 1:
                title, name = m.group(1), m.group(2)
            else:
                name, title = m.group(1), "Owner"
            if name in NOT_NAMES or any(w in name for w in ("Our", "The", "Your", "Call", "Email")):
                continue
            people.setdefault(name, title)
    return [{"name": n, "title": t, "source": "website"} for n, t in list(people.items())[:5]]


def classify_email(email: str, company_domain: str) -> dict:
    local, _, dom = email.partition("@")
    return {
        "email": email,
        "type": "role" if local.split(".")[0].split("+")[0] in ROLE_PREFIXES else "personal",
        "domain_match": dom == company_domain or dom.endswith("." + company_domain),
    }
