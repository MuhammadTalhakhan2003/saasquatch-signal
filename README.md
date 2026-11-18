# SaaSquatch Signal

**A lead-intelligence layer for SaaSquatch. It turns a raw lead list into a ranked call sheet you can explain.**

SaaSquatch is good at *finding* companies. A list of 500 companies still leaves the hard part to the rep. Who is actually reachable? Who is a real fit? Who do you call first, and what do you say? Signal handles that step. It enriches each lead from its own website and verifies the contact data. Then it scores the lead on three explainable dimensions and writes an AI brief and an opener. The ranked list exports straight into HubSpot or Salesforce.

> Built in about 5 hours for the Caprae Capital AI-Readiness challenge. I chose **Quality First**: one feature, end-to-end, production-shaped.

---

## Why this feature

Caprae uses SaaSquatch as its **primary sourcing tool for acquisition targets**. It also sells the tool to SMB-focused sales teams. Both users have the same bottleneck:

| Pain today | What Signal does |
|---|---|
| Lists are full of dead sites, parked domains and duplicates | Canonical-domain **dedupe**, unreachable / parked **disqualification** |
| Bounced emails burn sender reputation | **Email validation**: syntax, junk filter, disposable/free-mail check, **MX lookup**. It never sends mail |
| "info@" is not a decision maker | Separates **named vs role inboxes** and extracts **owners / founders** from the site (+ AI) |
| Every lead looks the same in a table | **Explainable scores** with a "+30 Established 47 yrs" style reason list |
| Reps spend about 10 min researching each lead before writing | **AI brief** (summary, pain points, value-creation levers) plus a **personalised opener** |
| Data dies in a CSV | One-click **HubSpot / Salesforce** import files, filtered by tier and by verified emails |

### The scoring model (explainable by design)

| Sub-score | Answers | Top signals |
|---|---|---|
| **Reachability** | Can we reach a decision maker? | verified named email > verified role inbox, direct phone, LinkedIn, named owner, address |
| **Acquisition fit** | Is this a Caprae-style ETA target? | years in business (founding year / "since 1978" / JSON-LD), family/owner-operated, retirement/succession language, SMB headcount, recurring-revenue language (service agreements), B2B mix. **Penalties**: franchise/national language, toll-free national numbers, outside target industry or state |
| **Digital gap** | How much post-close AI/tech upside? | stale site (© year), no CRM / marketing automation, no analytics, no chat/booking, DIY builder. Lower when they already run modern tools (HubSpot, ServiceTitan…) |

**Priority** blends the three according to the **playbook**:

- **Acquisition sourcing** (Caprae's own use): 30% reachability, 50% fit, 20% gap
- **Sales prospecting** (SaaSquatch customers selling AI/software into SMBs): 50% reachability, 15% fit, 35% gap

Tiers: **A ≥ 60** (call first), **B ≥ 40**, **C**, and **✕** (dead / parked).

Missing data never creates fake upside. If a site is bot-protected or JS-only, the digital-gap score is **not** inflated. The lead is flagged "partial data, verify manually".

---

## Product / UX decisions

- **Two steps, one button.** (1) Paste or upload leads. Any SaaSquatch / Apollo / ZoomInfo CSV works because header aliases are auto-mapped, and so does a plain list of URLs. (2) Pick a playbook and an optional ICP. Then click run.
- **Results stream in** while the crawl runs (progress bar, new rows flash). Nobody stares at a spinner.
- **KPI strip** answers "was this list any good?": Tier A count, verified-email rate, named decision makers, and leads removed as dead or parked.
- **Table built for triage.** It shows the tier badge, the score with three micro-bars (one per sub-score), the decision maker, the best contact with its verification badge, the founding year, and the 3–4 signals that matter.
- **Drawer per lead.** Here the rep acts: copy the opener or "Draft email", read the brief, and see *why this score*. Every point is listed with its sign.
- **Filters that match how reps work**: tier chips, "verified email only", "has decision maker", and free-text search across company and person names.
- **Export menu** with CRM-native column names. It defaults to Tier A+B, with an option for verified emails only.
- **Design**: Caprae gold on charcoal for the brand. Colour is used only to carry meaning (green = verified/good, amber = risky/verify, red = invalid/disqualified). Inter font, light and dark mode, responsive down to phone width.
- The ICP is remembered between sessions (localStorage), and recent runs can be reopened from the sidebar.

---

## Architecture

```
 Browser (vanilla JS SPA, no build step)
   │  POST /api/jobs (CSV/text + ICP)     GET /api/jobs/{id} (poll 1.2s)     GET /export
   ▼
 FastAPI (uvicorn, async)
   │
   ├─ pipeline.parse_input ─ header-alias CSV / plain list
   ├─ pipeline.dedupe ────── canonical domain (scheme/www/path/case-insensitive), merges richer fields
   ├─ asyncio job (semaphore = 8 domains in parallel)
   │     ├─ crawler.crawl_company
   │     │     • robots.txt honoured            • apex ↔ www and https → http fallback
   │     │     • retries + exp. backoff + jitter on 429/5xx, honours Retry-After
   │     │     • optional proxy rotation on 403/429 (SIGNAL_PROXIES)
   │     │     • CAPTCHA / bot-wall *detected and flagged*, never bypassed
   │     │     • home page + up to 3 best "contact/about/team" links, 300 ms politeness delay per host
   │     ├─ extract.*  emails (mailto, "[at] [dot]", Cloudflare-protected), phones, socials,
   │     │             JSON-LD Organization/LocalBusiness, founding year, © year, headcount,
   │     │             owner / succession / growth / recurring / enterprise signals, 30 tech fingerprints
   │     ├─ validate.* syntax → junk/disposable → MX (dnspython, LRU-cached, run in thread pool)
   │     └─ scoring.score_lead → 3 sub-scores + reasons + tier
   ├─ ai.ai_insights → Groq | Claude (top-N leads only) → strict JSON schema → re-score with AI-found decision makers
   └─ export.to_csv  → standard | HubSpot | Salesforce
   │
 SQLite (WAL)  page_cache (72h TTL) · jobs · leads (JSON + indexed priority)
```

### Exact stack

| Layer | Technology |
|---|---|
| Language | Python 3.13 |
| API | FastAPI 0.162, Pydantic v2, Uvicorn (ASGI) |
| HTTP crawling | httpx 0.28 (async, connection pooling, keep-alive) |
| Parsing | BeautifulSoup 4 (`html.parser`), regex extractors, schema.org JSON-LD |
| Validation | dnspython (MX / A fallback per RFC 5321) |
| AI | Pluggable: **Groq** (`openai/gpt-oss-120b`, JSON mode, retry on 429) or **Anthropic** (`claude-opus-5-5`, structured outputs). Output is schema-normalised; heuristics fill gaps |
| Storage | **SQLite** in WAL mode (stdlib `sqlite3`). The schema is plain SQL and ports to Postgres (Cloud SQL / Neon) unchanged |
| Front end | Vanilla HTML/CSS/JS served by FastAPI `StaticFiles`. No framework, no build step |
| Tests | pytest (15 tests: extractors, scoring, dedupe, CSV parsing, HubSpot export) |
| Container | Docker (`python:3.13-slim`) |

### Caching and performance

- **Page cache**: every 200-OK page is stored in SQLite for 72h (configurable). Re-scoring a list with a different ICP, or overlapping lists from different reps, costs almost no network.
- **MX cache**: an in-process LRU of 4,096 domains. Thousands of leads share a few hundred mail domains.
- **Bounded concurrency**: 8 domains at once, and only one request at a time per domain. That is fast overall (the 11-row sample, 9 unique, finishes in **about 10–12 s**, and the slowest part is retrying the dead and bot-protected domains) and gentle on small-business hosting.
- **AI spend control**: Claude runs only on the **top-N non-disqualified leads** (default 25) after heuristic ranking, at `effort: low`. Every other lead gets a free template brief.
- **Fault isolation**: one broken site can never fail the batch. Each lead is saved as soon as it finishes, so the UI fills in progressively.

### Hosting and deployment (recommended: GCP Cloud Run)

- **Serverless container on Google Cloud Run** (`min-instances=0`, so it costs nothing when idle). The same Dockerfile also runs on Render, Fly.io or AWS App Runner.
- The static UI is served by the same container. Put Cloud CDN in front if needed.
- Deploy:
  ```bash
  gcloud run deploy saasquatch-signal --source . --region us-central1 \
    --allow-unauthenticated --memory 512Mi --concurrency 40 \
    --set-secrets GROQ_API_KEY=groq-key:latest
  ```
- CI/CD: on a push to `main`, run GitHub Actions `pytest`, then `gcloud run deploy --source .` (Cloud Build builds the image).
- **Scaling path**: SQLite → Cloud SQL Postgres (no schema changes). The in-process asyncio job → Cloud Tasks / Pub/Sub workers once lists exceed a few thousand rows. Add a Playwright worker pool for JS-rendered sites.

---

## Ethics and compliance

- robots.txt is honoured, and the bot identifies itself in its User-Agent.
- There is a politeness delay per host and at most 4 pages per site.
- CAPTCHAs and bot walls are **detected and reported, never solved or evaded**.
- No SMTP probing and no email is ever sent. Validation is DNS-only.
- Only publicly published business contact data is collected. Personal addresses are labelled as such so that reps can follow CAN-SPAM and GDPR rules.

---

## Setup

```bash
git clone <this repo> && cd saasquatch-signal
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# optional AI briefs/openers (otherwise heuristic mode, $0): copy .env.example to .env and set
# GROQ_API_KEY=...        (default model openai/gpt-oss-120b)   or   ANTHROPIC_API_KEY=...

uvicorn app.main:app --reload --port 8000
# open http://localhost:8000  →  "Try it with the sample list"
```

Docker:

```bash
docker build -t signal . && docker run -p 8080:8080 --env-file .env signal
```

Tests:

```bash
pytest -q
```

### API demo (no UI)

```bash
python scripts/demo_api.py                       # acquisition playbook on data/sample_leads.csv
python scripts/demo_api.py --playbook sales --industries hvac,plumbing
```

Interactive OpenAPI docs: `http://localhost:8000/docs`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | version, AI on/off, defaults |
| `POST` | `/api/jobs` | `{leads: "<csv or lines>", icp: {...}}` → `{job_id, total, duplicates, invalid}` |
| `POST` | `/api/jobs/upload` | multipart CSV upload |
| `GET` | `/api/jobs/{id}` | job progress + ranked leads |
| `GET` | `/api/jobs/{id}/export?fmt=hubspot\|salesforce\|standard&tiers=A,B&verified_only=true` | CSV download |

### Dataset

`data/sample_leads.csv` is 11 rows of real regional home-services businesses. It deliberately includes two duplicate rows (different URL formats) and one dead domain, so that dedupe and disqualification are visible in the demo. Every value is pulled live from public websites at run time.

---

## Results on the sample list (cold run, heuristic mode)

- 11 rows → **9 unique** (2 duplicates merged) → **1 dead domain removed** automatically
- 2 sites flagged as "partial data" (one bot-protected, one JS-only) instead of being given misleading scores
- The national franchise (Roto-Rooter) is pushed **below** the family-run regional operators despite its 90-year history, which is what an ETA buyer wants
- Founding year found for **6/6** readable sites, a direct phone for **6/6**, and a LinkedIn company page for **5/6**

## Known limitations / next steps

1. JS-rendered sites: add an optional headless-browser (Playwright) fallback worker.
2. Decision-maker coverage: add LinkedIn / state Secretary-of-State registry lookups (owner name, entity age), which would be a strong acquisition signal.
3. Email pattern inference (`first@`, `first.last@`) from known names, marked as "inferred".
4. Native HubSpot/Salesforce API push and webhooks instead of CSV.
5. A feedback loop: reps mark leads as good/bad, and the scoring weights are learned per user.

## Time log (~5h)

| | |
|---|---|
| 0:45 | Studied SaaSquatch, its users (Caprae deal team plus SMB sales teams) and the gaps in its workflow |
| 1:30 | Crawler and extractors, with tests |
| 0:45 | Validation, scoring model and dedupe |
| 0:30 | Claude integration with structured outputs and heuristic fallback |
| 1:00 | UI/UX |
| 0:30 | Exports, docs and live verification |
