"""Email / domain validation.

We never send mail or open SMTP sessions (that gets IPs blacklisted and is
ethically grey). Instead: syntax -> disposable/free-mail check -> MX lookup.
That is enough to drop the bounce-prone addresses that hurt sender reputation.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache

from .extract import EMAIL_RE

FREE_MAIL = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com",
    "comcast.net", "att.net", "verizon.net", "msn.com", "live.com", "sbcglobal.net",
    "bellsouth.net", "me.com", "ymail.com", "protonmail.com",
}
DISPOSABLE = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "yopmail.com"}


@lru_cache(maxsize=4096)
def _has_mx(domain: str) -> bool | None:
    """True/False when DNS answers, None when we could not determine."""
    try:
        import dns.exception
        import dns.resolver
    except ImportError:  # dnspython missing -> unknown, never block the pipeline
        return None
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=4)
        return len(answers) > 0
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        # RFC 5321: fall back to A record when no MX exists
        try:
            dns.resolver.resolve(domain, "A", lifetime=4)
            return True
        except Exception:
            return False
    except (dns.exception.Timeout, dns.resolver.NoNameservers):
        return None
    except Exception:
        return None


async def has_mx(domain: str) -> bool | None:
    return await asyncio.to_thread(_has_mx, domain)


async def validate_email(email: str) -> dict:
    """Return {status: valid|risky|invalid, reason}."""
    if not EMAIL_RE.fullmatch(email):
        return {"status": "invalid", "reason": "Bad syntax"}
    domain = email.rsplit("@", 1)[1]
    if domain in DISPOSABLE:
        return {"status": "invalid", "reason": "Disposable domain"}
    mx = await has_mx(domain)
    if mx is False:
        return {"status": "invalid", "reason": "Domain has no mail server"}
    if domain in FREE_MAIL:
        return {"status": "risky", "reason": "Free-mail address (may be personal)"}
    if mx is None:
        return {"status": "risky", "reason": "MX lookup inconclusive"}
    return {"status": "valid", "reason": "Syntax + MX verified"}
