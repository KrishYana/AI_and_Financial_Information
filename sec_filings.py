"""
sec_filings.py — SEC EDGAR filing downloader and local parquet cache.

Downloads 10-K, 10-Q, 8-K, and Form 4 filings for a given ticker from SEC EDGAR,
strips HTML, and stores as a local parquet file. Subsequent queries hit local cache.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

SEC_CACHE_DIR = Path(os.getenv("SEC_FILINGS_CACHE_DIR", ".sec_filings_cache")).resolve()
SEC_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "penrs-agent research@example.com")
_SEC_HEADERS = {"User-Agent": _SEC_USER_AGENT}

FORM_TYPES = {"10-K", "10-Q", "8-K", "4"}
_DEFAULT_FROM_YEAR = 2020
_REQUEST_DELAY = 0.35  # ~3 req/sec, well under SEC's 10 req/sec limit


def _strip_html(html: str) -> str:
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_cik(ticker: str) -> tuple[str, str]:
    """Resolve ticker symbol to (zero-padded CIK, company name)."""
    r = httpx.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=_SEC_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    for entry in r.json().values():
        if entry.get("ticker") == ticker.upper():
            return str(entry["cik_str"]).zfill(10), entry.get("title", "")
    raise ValueError(f"Ticker '{ticker}' not found in SEC company tickers")


def _fetch_filing_text(cik: str, accession: str, primary_doc: str) -> str:
    acc_no_dashes = accession.replace("-", "")
    cik_int = str(int(cik))
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_no_dashes}/{primary_doc}"
    try:
        r = httpx.get(url, headers=_SEC_HEADERS, timeout=30)
        if r.status_code != 200:
            return f"[HTTP {r.status_code}]"
        if primary_doc.endswith((".htm", ".html", ".xml")):
            return _strip_html(r.text)
        return r.text
    except Exception as exc:
        return f"[Error: {exc}]"


def download_ticker_filings(
    ticker: str,
    from_year: int = _DEFAULT_FROM_YEAR,
    form_types: set[str] | None = None,
    progress_callback: Any = None,
) -> Path:
    """Download all SEC filings for a ticker from from_year+ and save as parquet."""
    ticker = ticker.upper()
    form_types = form_types or FORM_TYPES
    parquet_path = SEC_CACHE_DIR / f"{ticker}_filings.parquet"

    cik, company_name = get_cik(ticker)
    if callable(progress_callback):
        progress_callback(f"SEC EDGAR: {ticker} → CIK {cik} ({company_name})")

    r = httpx.get(
        f"https://data.sec.gov/submissions/CIK{cik}.json",
        headers=_SEC_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    subs = r.json()
    recent = subs.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    rows: list[dict[str, Any]] = []
    for i in range(len(forms)):
        if forms[i] not in form_types:
            continue
        if dates[i] < f"{from_year}-01-01":
            continue
        rows.append({
            "ticker": ticker,
            "cik": cik,
            "company_name": company_name,
            "form_type": forms[i],
            "filing_date": dates[i],
            "accession_number": accessions[i],
            "primary_document": primary_docs[i],
            "description": descriptions[i] if i < len(descriptions) else "",
        })

    if callable(progress_callback):
        progress_callback(f"SEC EDGAR: Downloading {len(rows)} filings for {ticker}")

    for j, row in enumerate(rows):
        if callable(progress_callback) and j % 20 == 0:
            progress_callback(f"SEC EDGAR: [{j + 1}/{len(rows)}] {row['form_type']} {row['filing_date']}")
        row["text"] = _fetch_filing_text(cik, row["accession_number"], row["primary_document"])
        row["text_length"] = len(row["text"])
        time.sleep(_REQUEST_DELAY)

    df = pd.DataFrame(rows)
    df.to_parquet(parquet_path, engine="pyarrow")

    if callable(progress_callback):
        size_mb = parquet_path.stat().st_size / 1024 / 1024
        progress_callback(f"SEC EDGAR: Saved {parquet_path.name} ({size_mb:.1f} MB, {len(rows)} filings)")

    return parquet_path


def get_ticker_filings(ticker: str) -> pd.DataFrame:
    """Load cached filings for a ticker. Downloads if not cached."""
    ticker = ticker.upper()
    parquet_path = SEC_CACHE_DIR / f"{ticker}_filings.parquet"
    if not parquet_path.exists():
        download_ticker_filings(ticker)
    return pd.read_parquet(parquet_path)


def get_most_recent_filing(
    ticker: str,
    form_type: str,
    before_date: str,
) -> dict[str, Any] | None:
    """Get the most recent filing of a given type before a date.

    Returns dict with keys: ticker, form_type, filing_date, text, text_length, etc.
    Returns None if no matching filing found.
    """
    df = get_ticker_filings(ticker)
    subset = df[
        (df["form_type"] == form_type) & (df["filing_date"] <= before_date)
    ].sort_values("filing_date", ascending=False)

    if len(subset) == 0:
        return None

    row = subset.iloc[0]
    return row.to_dict()
