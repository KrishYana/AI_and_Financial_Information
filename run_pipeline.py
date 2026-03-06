"""
run_pipeline.py — End-to-end PENRS pipeline runner.

Usage:
    python run_pipeline.py                          # defaults: MRNA, last 3 months
    python run_pipeline.py --ticker BIIB
    python run_pipeline.py --ticker MRNA --date-from 2025-10-01 --date-to 2026-01-01
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Imports from project notebooks (executed as plain Python via importnb or
# by pulling classes directly).  We use runpy-style exec to load notebook
# cells into a namespace so we can instantiate PENRSWorker / run_penrs.
# ---------------------------------------------------------------------------

def _exec_notebook(nb_path: str) -> dict:
    """Execute all code cells of a .ipynb and return the resulting namespace."""
    raw = json.loads(Path(nb_path).read_text(encoding="utf-8"))
    ns: dict = {}
    for cell in raw.get("cells", []):
        if cell.get("cell_type") == "code":
            source = "".join(cell.get("source", []))
            try:
                exec(compile(source, nb_path, "exec"), ns)
            except Exception as exc:
                # Skip cells that fail (e.g. interactive widgets)
                print(f"  [skip cell] {exc}", file=sys.stderr)
    return ns


print("Loading worker_nodes.ipynb …")
_worker_ns = _exec_notebook("worker_nodes.ipynb")
PENRSWorker = _worker_ns["PENRSWorker"]
DocumentType = _worker_ns["DocumentType"]

print("Loading orchestrator.ipynb …")
_orch_ns = _exec_notebook("orchestrator.ipynb")
run_penrs = _orch_ns["run_penrs"]

# Fix Pydantic forward-ref resolution after notebook exec
_PENRSReport = _orch_ns.get("PENRSReport")
if _PENRSReport is not None:
    from typing import Any
    _PENRSReport.model_rebuild(_types_namespace={"Any": Any})

# ---------------------------------------------------------------------------
# Real document fetcher — calls Alpha Vantage / SEC / openFDA / PubMed
# via the MCP server functions directly (no MCP transport needed).
# ---------------------------------------------------------------------------

from penrs_mcp_server import (
    fetch_alpha_vantage,
    fetch_openfda,
    fetch_pubmed,
)


async def _real_document_fetcher(
    ticker: str,
    document_type: DocumentType,
    date_range: dict[str, str] | None = None,
) -> dict:
    """Route to the correct API fetcher based on document_type."""
    date_from = (date_range or {}).get("from")

    if document_type in (
        DocumentType.EARNINGS_CALL,
        DocumentType.NEWS_SENTIMENT,
        DocumentType.PRICE_HISTORY,
        DocumentType.FORM_4,
    ):
        # Map document types to Alpha Vantage function names
        av_functions = {
            DocumentType.EARNINGS_CALL: "EARNINGS",
            DocumentType.NEWS_SENTIMENT: "NEWS_SENTIMENT",
            DocumentType.PRICE_HISTORY: "TIME_SERIES_MONTHLY",
            DocumentType.FORM_4: "INSIDER_TRANSACTIONS",
        }
        function_name = av_functions[document_type]
        data = await fetch_alpha_vantage(ticker=ticker, function=function_name, date=date_from)
        if "error" in data or "Error Message" in data or "Note" in data:
            return {"status": "not_released", "data": data}
        return {"status": "available", "data": data}

    if document_type == DocumentType.BIOMEDICAL_EVIDENCE:
        fda_data = await fetch_openfda(ticker=ticker, limit=5)
        pubmed_data = await fetch_pubmed(term=ticker, retmax=5)
        combined = {"openfda": fda_data, "pubmed": pubmed_data}
        has_error = all("error" in v for v in [fda_data, pubmed_data] if isinstance(v, dict))
        if has_error:
            return {"status": "not_released", "data": combined}
        return {"status": "available", "data": combined}

    if document_type == DocumentType.CLINICAL_TRIALS:
        pubmed_data = await fetch_pubmed(term=f"{ticker} clinical trial", retmax=5)
        if isinstance(pubmed_data, dict) and "error" in pubmed_data:
            return {"status": "not_released", "data": pubmed_data}
        return {"status": "available", "data": pubmed_data}

    # SEC filings — skip in MVP (need CIK + accession number lookup)
    if document_type in (DocumentType.SEC_10K, DocumentType.SEC_10Q, DocumentType.SEC_8K):
        return {
            "status": "not_released",
            "data": {"note": "SEC filing lookup not wired in MVP runner"},
        }

    return {"status": "not_released", "data": {}}


# ---------------------------------------------------------------------------
# Real LLM invoker — Anthropic Messages API
# ---------------------------------------------------------------------------

import anthropic

_client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY from env


async def _real_llm_invoker(prompt: str, *, system: str = "Respond only in valid JSON.") -> str:
    """Call Claude via the Anthropic Messages API."""
    # Run the synchronous SDK call in a thread to keep the event loop free
    def _call():
        response = _client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    return await asyncio.to_thread(_call)


# ---------------------------------------------------------------------------
# Worker factory
# ---------------------------------------------------------------------------

def _build_workers(ticker: str) -> list:
    """Create a set of workers covering the main document types."""
    worker_configs = [
        {
            "name": "Earnings Analyst",
            "weight": 1.0,
            "signal_density": 0.8,
            "rubric_id": "earnings",
            "document_type": DocumentType.EARNINGS_CALL,
        },
        {
            "name": "Sentiment Analyst",
            "weight": 0.7,
            "signal_density": 0.6,
            "rubric_id": "sentiment",
            "document_type": DocumentType.NEWS_SENTIMENT,
        },
        {
            "name": "Price Action Analyst",
            "weight": 0.8,
            "signal_density": 0.7,
            "rubric_id": "price_action",
            "document_type": DocumentType.PRICE_HISTORY,
        },
        {
            "name": "Biomedical Analyst",
            "weight": 0.6,
            "signal_density": 0.5,
            "rubric_id": "biomedical",
            "document_type": DocumentType.BIOMEDICAL_EVIDENCE,
        },
    ]

    workers = []
    for cfg in worker_configs:
        workers.append(
            PENRSWorker(
                name=cfg["name"],
                weight=cfg["weight"],
                signal_density=cfg["signal_density"],
                rubric_id=cfg["rubric_id"],
                document_type=cfg["document_type"],
                document_fetcher=_real_document_fetcher,
                llm_invoker=_real_llm_invoker,
                max_context_chars=12000,
            )
        )
    return workers


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(ticker: str, date_from: str, date_to: str) -> None:
    print(f"\n{'='*60}")
    print(f"  PENRS Pipeline — {ticker}")
    print(f"  Date range: {date_from} -> {date_to}")
    print(f"{'='*60}\n")

    workers = _build_workers(ticker)
    print(f"Spawning {len(workers)} workers: {[w.name for w in workers]}\n")

    report = await run_penrs(
        ticker=ticker,
        date_from=date_from,
        date_to=date_to,
        workers=workers,
        report_dir="penrs_reports",
    )

    # Print summary
    master = report.get("master", {})
    arbiter = report.get("arbiter", {})
    evidence = report.get("evidence", [])

    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Final Score:      {master.get('final_score', 'N/A')}")
    print(f"  Workers Available: {master.get('available_worker_count', 0)}/{master.get('total_worker_count', 0)}")
    print(f"  Evidence Nodes:   {len(evidence)}")

    contradictions = arbiter.get("contradictions", [])
    flagged = [c for c in contradictions if c.get("flagged")]
    if flagged:
        print(f"\n  FLAGGED CONTRADICTIONS:")
        for c in flagged:
            print(f"    - {c['name']} (severity: {c['severity']}, evidence: {c.get('evidence')})")
    else:
        print(f"  Contradictions:   None flagged")

    report_path = report.get("report_path", "unknown")
    print(f"\n  Report saved to: {report_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run PENRS pipeline end-to-end")
    parser.add_argument("--ticker", default="MRNA", help="Stock ticker (default: MRNA)")
    parser.add_argument("--date-from", default="2025-12-01", help="Start date (default: 2025-12-01)")
    parser.add_argument("--date-to", default="2026-03-01", help="End date (default: 2026-03-01)")
    args = parser.parse_args()

    asyncio.run(main(args.ticker, args.date_from, args.date_to))
