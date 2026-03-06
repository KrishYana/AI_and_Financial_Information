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
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Load notebook code (same approach as run_pipeline.py)
# ---------------------------------------------------------------------------

def _exec_notebook(nb_path: str) -> dict:
    raw = json.loads(Path(nb_path).read_text(encoding="utf-8"))
    ns: dict = {}
    for cell in raw.get("cells", []):
        if cell.get("cell_type") == "code":
            source = "".join(cell.get("source", []))
            try:
                exec(compile(source, nb_path, "exec"), ns)
            except Exception as exc:
                print(f"  [skip cell] {exc}", file=sys.stderr)
    return ns


print("Loading worker_nodes.ipynb …")
_worker_ns = _exec_notebook("worker_nodes.ipynb")
PENRSWorker = _worker_ns["PENRSWorker"]
DocumentType = _worker_ns["DocumentType"]

print("Loading orchestrator.ipynb …")
_orch_ns = _exec_notebook("orchestrator.ipynb")
run_penrs = _orch_ns["run_penrs"]

_PENRSReport = _orch_ns.get("PENRSReport")
if _PENRSReport is not None:
    _PENRSReport.model_rebuild(_types_namespace={"Any": Any})

# ---------------------------------------------------------------------------
# Import pipeline components from run_pipeline.py
# ---------------------------------------------------------------------------

from run_pipeline import (
    _build_workers,
    _real_document_fetcher,
    _real_llm_invoker,
)

REPORTS_DIR = Path("penrs_reports")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="PENRS API", version="1.0.0")

# Allow Lovable (and any frontend) to call this API
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
