"""FastAPI entrypoint: JSON API + static single-page UI."""
from __future__ import annotations

import logging

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, db
from .config import AI_ENABLED, AI_MODEL, AI_PROVIDER, BASE_DIR, MAX_LEADS_PER_JOB
from .export import to_csv
from .pipeline import start_job
from .scoring import DEFAULT_ICP, PLAYBOOKS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="SaaSquatch Signal", version=__version__,
              description="Lead enrichment, validation and acquisition-fit scoring API.")
STATIC = BASE_DIR / "static"


class ICP(BaseModel):
    playbook: str = Field("acquisition", description="acquisition | sales")
    industries: list[str] = []
    min_years: int = 10
    min_employees: int = 5
    max_employees: int = 250
    states: list[str] = []


class JobRequest(BaseModel):
    leads: str = Field(..., description="CSV text or one domain per line")
    icp: ICP = ICP()


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "ai_enabled": AI_ENABLED,
            "provider": AI_PROVIDER, "model": AI_MODEL, "max_leads": MAX_LEADS_PER_JOB,
            "playbooks": list(PLAYBOOKS), "default_icp": DEFAULT_ICP}


@app.post("/api/jobs")
async def create_job(req: JobRequest):
    result = start_job(req.leads, req.icp.model_dump())
    if result["total"] == 0:
        raise HTTPException(400, "No valid domains found in input.")
    return result


@app.post("/api/jobs/upload")
async def create_job_upload(file: UploadFile = File(...), icp: str = Form("{}")):
    raw = (await file.read()).decode("utf-8", errors="ignore")
    result = start_job(raw, ICP.model_validate_json(icp).model_dump())
    if result["total"] == 0:
        raise HTTPException(400, "No valid domains found in file.")
    return result


@app.get("/api/jobs")
def jobs():
    return db.list_jobs()


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = db.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    leads = db.get_leads(job_id)
    for l in leads:  # trim heavy fields for the table view
        l.get("profile", {}).pop("text_sample", None)
    return {"job": j, "leads": leads}


@app.get("/api/jobs/{job_id}/export", response_class=PlainTextResponse)
def export(job_id: str, fmt: str = "standard", tiers: str = "A,B,C", verified_only: bool = False):
    if not db.get_job(job_id):
        raise HTTPException(404, "Job not found")
    wanted = set(tiers.upper().split(","))
    leads = [l for l in db.get_leads(job_id) if l["scores"]["tier"] in wanted]
    if verified_only:
        leads = [l for l in leads if any(e["status"] == "valid" for e in l.get("emails", []))]
    return PlainTextResponse(
        to_csv(leads, fmt), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="signal_{job_id}_{fmt}.csv"'},
    )


@app.get("/api/sample", response_class=PlainTextResponse)
def sample():
    return (BASE_DIR / "data" / "sample_leads.csv").read_text(encoding="utf-8")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
