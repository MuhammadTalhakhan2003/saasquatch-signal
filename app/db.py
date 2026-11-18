"""SQLite persistence: page cache (crawl dedup across jobs) + jobs + leads.

SQLite in WAL mode is plenty for a single-node deployment; the schema is
plain SQL so it ports to Postgres (e.g. Cloud SQL / Neon) without changes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import CACHE_TTL_HOURS, DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS page_cache (
    url TEXT PRIMARY KEY,
    status INTEGER,
    html TEXT,
    fetched_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at REAL,
    status TEXT,
    total INTEGER,
    done INTEGER,
    duplicates INTEGER DEFAULT 0,
    icp TEXT
);
CREATE TABLE IF NOT EXISTS leads (
    job_id TEXT,
    domain TEXT,
    data TEXT,
    priority_score REAL,
    PRIMARY KEY (job_id, domain)
);
CREATE INDEX IF NOT EXISTS idx_leads_job ON leads(job_id, priority_score DESC);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


_conn = _connect()
_conn.executescript(SCHEMA)


@contextmanager
def tx():
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise


# ---------- page cache ----------
def cache_get(url: str) -> tuple[int, str] | None:
    with tx() as c:
        row = c.execute("SELECT status, html, fetched_at FROM page_cache WHERE url=?", (url,)).fetchone()
    if row and time.time() - row["fetched_at"] < CACHE_TTL_HOURS * 3600:
        return row["status"], row["html"]
    return None


def cache_put(url: str, status: int, html: str) -> None:
    with tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO page_cache(url, status, html, fetched_at) VALUES (?,?,?,?)",
            (url, status, html[:600_000], time.time()),
        )


# ---------- jobs ----------
def create_job(job_id: str, total: int, duplicates: int, icp: dict) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO jobs(id, created_at, status, total, done, duplicates, icp) VALUES (?,?,?,?,?,?,?)",
            (job_id, time.time(), "running", total, 0, duplicates, json.dumps(icp)),
        )


def update_job(job_id: str, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def increment_done(job_id: str) -> None:
    with tx() as c:
        c.execute("UPDATE jobs SET done = done + 1 WHERE id=?", (job_id,))


def get_job(job_id: str) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    job = dict(row)
    job["icp"] = json.loads(job["icp"] or "{}")
    return job


def list_jobs(limit: int = 20) -> list[dict]:
    with tx() as c:
        rows = c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------- leads ----------
def save_lead(job_id: str, lead: dict) -> None:
    with tx() as c:
        c.execute(
            "INSERT OR REPLACE INTO leads(job_id, domain, data, priority_score) VALUES (?,?,?,?)",
            (job_id, lead["domain"], json.dumps(lead, default=list), lead["scores"]["priority"]),
        )


def get_leads(job_id: str) -> list[dict]:
    with tx() as c:
        rows = c.execute(
            "SELECT data FROM leads WHERE job_id=? ORDER BY priority_score DESC", (job_id,)
        ).fetchall()
    return [json.loads(r["data"]) for r in rows]


def get_lead(job_id: str, domain: str) -> dict | None:
    with tx() as c:
        row = c.execute("SELECT data FROM leads WHERE job_id=? AND domain=?", (job_id, domain)).fetchone()
    return json.loads(row["data"]) if row else None
