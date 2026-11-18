"""Central configuration, read once from environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=value lines); real env vars always win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


_load_dotenv(BASE_DIR / ".env")

DB_PATH = Path(os.getenv("SIGNAL_DB_PATH", BASE_DIR / "data" / "signal.db"))

# Crawler behaviour
USER_AGENT = os.getenv(
    "SIGNAL_USER_AGENT",
    "SaaSquatchSignalBot/1.0 (+lead-enrichment; respects robots.txt)",
)
REQUEST_TIMEOUT = float(os.getenv("SIGNAL_TIMEOUT", "10"))
MAX_CONCURRENCY = int(os.getenv("SIGNAL_CONCURRENCY", "8"))
MAX_PAGES_PER_SITE = int(os.getenv("SIGNAL_MAX_PAGES", "4"))
MAX_RETRIES = int(os.getenv("SIGNAL_RETRIES", "2"))
CACHE_TTL_HOURS = int(os.getenv("SIGNAL_CACHE_TTL_HOURS", "72"))
MAX_LEADS_PER_JOB = int(os.getenv("SIGNAL_MAX_LEADS", "200"))

# Optional HTTP proxy pool for IP-restricted sites, comma separated.
PROXIES = [p.strip() for p in os.getenv("SIGNAL_PROXIES", "").split(",") if p.strip()]

# AI insights (optional - heuristics are used when no key is configured)
ANTHROPIC_MODEL = os.getenv("SIGNAL_MODEL", "claude-opus-5-5")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
    AI_PROVIDER = "anthropic"
elif GROQ_API_KEY:
    AI_PROVIDER = "groq"
else:
    AI_PROVIDER = None
AI_ENABLED = AI_PROVIDER is not None
AI_MODEL = {"anthropic": ANTHROPIC_MODEL, "groq": GROQ_MODEL}.get(AI_PROVIDER)
AI_MAX_LEADS = int(os.getenv("SIGNAL_AI_MAX_LEADS", "25"))
