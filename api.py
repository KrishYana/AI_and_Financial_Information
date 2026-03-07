"""
api.py — FastAPI backend for PENRS pipeline.

Usage:
    python api.py
    → serves on http://localhost:8000

Endpoints:
    POST /api/analyze   { "ticker": "MRNA", "date_from": "2025-12-01", "date_to": "2026-03-01" }
    GET  /api/reports   List all saved reports
    GET  /api/reports/{filename}   Fetch a specific report
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=True)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import everything from run_pipeline (it handles notebook loading)
from run_pipeline import _build_workers, run_penrs

REPORTS_DIR = Path("penrs_reports")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="PENRS API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    ticker: str
    date_from: str = "2025-12-01"
    date_to: str = "2026-03-01"


class AnalyzeResponse(BaseModel):
    ticker: str
    date_from: str
    date_to: str
    final_score: float
    available_workers: int
    total_workers: int
    worker_scores: list[dict[str, Any]]
    contradictions: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    report_path: str


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    """Run the full PENRS pipeline for a given ticker and date range."""
    ticker = req.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    workers = _build_workers(ticker)

    report = await run_penrs(
        ticker=ticker,
        date_from=req.date_from,
        date_to=req.date_to,
        workers=workers,
        report_dir=str(REPORTS_DIR),
    )

    master = report.get("master", {})
    arbiter = report.get("arbiter", {})

    return AnalyzeResponse(
        ticker=ticker,
        date_from=req.date_from,
        date_to=req.date_to,
        final_score=master.get("final_score", 0.0),
        available_workers=master.get("available_worker_count", 0),
        total_workers=master.get("total_worker_count", 0),
        worker_scores=arbiter.get("worker_scores", []),
        contradictions=arbiter.get("contradictions", []),
        evidence=report.get("evidence", []),
        report_path=report.get("report_path", ""),
    )


@app.get("/api/reports")
async def list_reports():
    """List all saved PENRS reports."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(REPORTS_DIR.glob("*.json"), reverse=True)
    reports = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reports.append({
                "filename": f.name,
                "ticker": data.get("ticker"),
                "date_from": data.get("date_from"),
                "date_to": data.get("date_to"),
                "generated_at": data.get("generated_at"),
                "final_score": data.get("master", {}).get("final_score"),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return {"reports": reports}


@app.get("/api/debug/env")
async def debug_env():
    """Temporary: check if env vars are loaded (shows first/last 4 chars only)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return {
        "ANTHROPIC_API_KEY_set": bool(key),
        "key_preview": f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "(too short or empty)",
        "key_length": len(key),
    }


@app.get("/api/reports/{filename}")
async def get_report(filename: str):
    """Fetch a specific saved report by filename."""
    path = REPORTS_DIR / filename
    if not path.exists() or not path.name.endswith(".json"):
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
