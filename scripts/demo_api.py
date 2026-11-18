"""API walkthrough: submit the sample list, wait, print the ranked call sheet, save a HubSpot CSV.

Usage (server running on :8000):
    python scripts/demo_api.py
    python scripts/demo_api.py --playbook sales --industries hvac,plumbing
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def call(base, path, body=None):
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        raw = r.read().decode()
    return json.loads(raw) if r.headers.get_content_type() == "application/json" else raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--file", default=str(ROOT / "data" / "sample_leads.csv"))
    ap.add_argument("--playbook", default="acquisition", choices=["acquisition", "sales"])
    ap.add_argument("--industries", default="")
    args = ap.parse_args()

    icp = {"playbook": args.playbook, "industries": [i for i in args.industries.split(",") if i]}
    job = call(args.base, "/api/jobs", {"leads": Path(args.file).read_text(encoding="utf-8"), "icp": icp})
    print(f"Job {job['job_id']}: {job['total']} unique leads, {job['duplicates']} duplicates merged, "
          f"{job['invalid']} invalid rows skipped")

    t0 = time.time()
    while True:
        data = call(args.base, f"/api/jobs/{job['job_id']}")
        j = data["job"]
        print(f"\r  {j['status']:<8} {j['done']}/{j['total']}  ({time.time() - t0:.0f}s)", end="", flush=True)
        if j["status"] == "done":
            break
        time.sleep(1.5)
    print("\n")

    print(f"{'Tier':<5}{'Score':<7}{'Company':<45}{'Best email':<34}Top reason")
    for lead in data["leads"]:
        s = lead["scores"]
        email = next((e["email"] for e in lead["emails"] if e["status"] != "invalid"), "-")
        top = max(s["reasons"], key=lambda r: abs(r["points"]), default={"text": ""})["text"]
        print(f"{s['tier']:<5}{s['priority']:<7}{lead['company_name'][:43]:<45}{email[:32]:<34}{top}")

    csv_text = call(args.base, f"/api/jobs/{job['job_id']}/export?fmt=hubspot&tiers=A,B")
    out = ROOT / "exports" / f"hubspot_{job['job_id']}.csv"
    out.parent.mkdir(exist_ok=True)
    out.write_text(csv_text, encoding="utf-8")
    print(f"\nHubSpot-ready CSV (tiers A+B): {out}")


if __name__ == "__main__":
    main()
