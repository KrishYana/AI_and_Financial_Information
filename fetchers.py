from __future__ import annotations

import asyncio
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from sec_filings import get_most_recent_filing, get_ticker_filings
from utils import PENRS_CACHE_DIR, _api_request, cache_key, cache_set

_env_path = Path(".env")
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_ALPHA_BASE_URL = "https://www.alphavantage.co/query"
_ALPHA_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
_SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "penrs-agent research@example.com")
_NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")

_alpha_lock = asyncio.Lock()
_last_alpha_call_ts = 0.0


def _parse_date_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, "", "None"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _truncate(text: str, limit: int = 400) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _na_result(error: str = "") -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if error:
        metadata["error"] = error
    return {"status": "not_available", "source_text": "", "metadata": metadata}


async def _alpha_request(params: dict[str, Any]) -> dict[str, Any]:
    global _last_alpha_call_ts

    if not _ALPHA_KEY:
        return {"error": "Missing ALPHA_VANTAGE_API_KEY"}

    payload = dict(params)
    payload["apikey"] = _ALPHA_KEY

    async with _alpha_lock:
        now = asyncio.get_running_loop().time()
        wait = 1.0 - (now - _last_alpha_call_ts)
        if wait > 0:
            await asyncio.sleep(wait)

        result = await _api_request(
            _ALPHA_BASE_URL,
            params=payload,
            api_name="alpha_vantage",
            timeout=30.0,
        )
        _last_alpha_call_ts = asyncio.get_running_loop().time()
        return result


async def fetch_earnings(ticker: str, historical_date: str) -> dict[str, Any]:
    try:
        data = await _alpha_request({"function": "EARNINGS", "symbol": ticker.upper()})
        if data.get("error"):
            return _na_result(str(data["error"]))

        earnings = data.get("quarterlyEarnings") or []
        cutoff = _parse_date_or_none(historical_date)

        rows: list[dict[str, Any]] = []
        for item in earnings:
            rep = item.get("reportedDate")
            rep_dt = _parse_date_or_none(rep)
            if cutoff and rep_dt and rep_dt > cutoff:
                continue
            rows.append(item)

        rows.sort(key=lambda r: r.get("reportedDate", ""), reverse=True)
        rows = rows[:8]

        if not rows:
            return _na_result("No earnings entries found before historical_date")

        lines = [
            f"Earnings Report: {ticker.upper()}",
            "========================",
        ]
        for item in rows:
            fiscal = item.get("fiscalDateEnding", "")
            quarter = ""
            fiscal_dt = _parse_date_or_none(fiscal)
            if fiscal_dt:
                quarter = f"Q{((fiscal_dt.month - 1) // 3) + 1} {fiscal_dt.year}"
            else:
                quarter = fiscal

            reported = item.get("reportedDate", "unknown")
            eps_reported = item.get("reportedEPS", "N/A")
            eps_est = item.get("estimatedEPS", "N/A")
            surprise_pct = item.get("surprisePercentage", "N/A")

            lines.extend(
                [
                    f"{quarter} (reported: {reported})",
                    f"  EPS Reported: {eps_reported}",
                    f"  EPS Estimated: {eps_est}",
                    f"  Surprise: {surprise_pct}%",
                    "",
                ]
            )

        source_text = "\n".join(lines).rstrip()
        return {
            "status": "available",
            "source_text": source_text,
            "metadata": {
                "ticker": ticker.upper(),
                "historical_date": historical_date,
                "quarters_included": len(rows),
                "cache_dir": str(PENRS_CACHE_DIR),
                "cache_key": cache_key("alpha_vantage", ticker.upper(), "earnings_call", historical_date),
            },
        }
    except Exception as exc:
        return _na_result(str(exc))


async def fetch_form4(ticker: str, historical_date: str) -> dict[str, Any]:
    try:
        filing = get_most_recent_filing(ticker.upper(), "4", historical_date)
        if filing and filing.get("text"):
            text = str(filing.get("text", ""))
            return {
                "status": "available",
                "source_text": text,
                "metadata": {
                    "source": "sec_edgar",
                    "ticker": ticker.upper(),
                    "form_type": "4",
                    "filing_date": filing.get("filing_date"),
                    "accession_number": filing.get("accession_number"),
                    "text_length": len(text),
                },
            }

        data = await _alpha_request({"function": "INSIDER_TRANSACTIONS", "symbol": ticker.upper()})
        if data.get("error"):
            return _na_result(str(data["error"]))

        records = data.get("data") or data.get("insiderTransactions") or []
        cutoff = _parse_date_or_none(historical_date)
        filtered: list[dict[str, Any]] = []
        for rec in records:
            tx_date = rec.get("transactionDate") or rec.get("filingDate")
            tx_dt = _parse_date_or_none(tx_date)
            if cutoff and tx_dt and tx_dt > cutoff:
                continue
            filtered.append(rec)

        filtered.sort(
            key=lambda r: (r.get("transactionDate") or r.get("filingDate") or ""),
            reverse=True,
        )
        filtered = filtered[:20]

        if not filtered:
            return _na_result("No Form 4 filing or insider transactions found")

        lines = [
            f"Form 4 / Insider Transactions: {ticker.upper()}",
            "=========================================",
        ]
        for i, rec in enumerate(filtered, start=1):
            lines.extend(
                [
                    f"[{i}] {rec.get('name', 'Unknown insider')}",
                    f"    Date: {rec.get('transactionDate') or rec.get('filingDate') or 'N/A'}",
                    f"    Type: {rec.get('transactionType', 'N/A')}",
                    f"    Shares: {rec.get('shares', rec.get('securitiesTransacted', 'N/A'))}",
                    f"    Price: {rec.get('price', 'N/A')}",
                    "",
                ]
            )

        source_text = "\n".join(lines).rstrip()
        return {
            "status": "available",
            "source_text": source_text,
            "metadata": {
                "source": "alpha_vantage_fallback",
                "ticker": ticker.upper(),
                "records_included": len(filtered),
            },
        }
    except Exception as exc:
        return _na_result(str(exc))


async def fetch_news_sentiment(ticker: str, historical_date: str) -> dict[str, Any]:
    try:
        params: dict[str, Any] = {
            "function": "NEWS_SENTIMENT",
            "tickers": ticker.upper(),
            "limit": 50,
        }
        if historical_date:
            params["time_to"] = historical_date.replace("-", "") + "T2359"

        data = await _alpha_request(params)
        if data.get("error"):
            return _na_result(str(data["error"]))

        feed = data.get("feed") or []
        if not feed:
            return _na_result("No news sentiment feed found")

        feed = feed[:20]
        lines = [
            f"News Sentiment: {ticker.upper()}",
            "========================",
        ]
        for i, article in enumerate(feed, start=1):
            title = article.get("title", "Untitled")
            published = article.get("time_published", "")
            if published and len(published) >= 15:
                published = f"{published[:4]}-{published[4:6]}-{published[6:8]}T{published[9:11]}:{published[11:13]}:{published[13:15]}"
            source = article.get("source", "Unknown")
            overall_label = article.get("overall_sentiment_label", "N/A")
            overall_score = article.get("overall_sentiment_score", "N/A")
            summary = _truncate(article.get("summary", ""), 420)

            ticker_label = "N/A"
            ticker_score = "N/A"
            for sentiment_item in article.get("ticker_sentiment", []):
                if sentiment_item.get("ticker", "").upper() == ticker.upper():
                    ticker_label = sentiment_item.get("ticker_sentiment_label", "N/A")
                    ticker_score = sentiment_item.get("ticker_sentiment_score", "N/A")
                    break

            lines.extend(
                [
                    f"[{i}] \"{title}\"",
                    f"    Published: {published or 'N/A'}",
                    f"    Source: {source}",
                    f"    Overall Sentiment: {overall_label} ({overall_score})",
                    f"    {ticker.upper()} Sentiment: {ticker_label} ({ticker_score})",
                    f"    Summary: {summary}",
                    "",
                ]
            )

        source_text = "\n".join(lines).rstrip()
        return {
            "status": "available",
            "source_text": source_text,
            "metadata": {
                "ticker": ticker.upper(),
                "historical_date": historical_date,
                "articles_included": len(feed),
            },
        }
    except Exception as exc:
        return _na_result(str(exc))


async def fetch_price_history(ticker: str, historical_date: str) -> dict[str, Any]:
    try:
        data = await _alpha_request(
            {
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker.upper(),
                "outputsize": "compact",
            }
        )
        if data.get("error"):
            return _na_result(str(data["error"]))

        series = data.get("Time Series (Daily)") or {}
        if not series:
            return _na_result("No daily time series found")

        cutoff = _parse_date_or_none(historical_date)
        rows: list[tuple[str, dict[str, Any]]] = []
        for date_str, entry in series.items():
            row_dt = _parse_date_or_none(date_str)
            if cutoff and row_dt and row_dt > cutoff:
                continue
            rows.append((date_str, entry))

        rows.sort(key=lambda x: x[0], reverse=True)
        rows = rows[:30]
        if not rows:
            return _na_result("No price history rows before historical_date")

        lines = [
            f"Price History: {ticker.upper()}",
            "========================",
            "Date       Open     High     Low      Close    Volume",
        ]

        highs: list[float] = []
        lows: list[float] = []
        vols: list[float] = []
        closes: list[float] = []

        for date_str, entry in rows:
            o = _safe_float(entry.get("1. open"))
            h = _safe_float(entry.get("2. high"))
            l = _safe_float(entry.get("3. low"))
            c = _safe_float(entry.get("4. close"))
            v = _safe_float(entry.get("5. volume"))

            if h is not None:
                highs.append(h)
            if l is not None:
                lows.append(l)
            if v is not None:
                vols.append(v)
            if c is not None:
                closes.append(c)

            lines.append(
                f"{date_str} {o if o is not None else 0:7.2f} {h if h is not None else 0:8.2f} "
                f"{l if l is not None else 0:8.2f} {c if c is not None else 8:8.2f} {int(v) if v is not None else 0:11,}"
            )

        high_30 = max(highs) if highs else 0.0
        low_30 = min(lows) if lows else 0.0
        avg_vol = (sum(vols) / len(vols)) if vols else 0.0
        pct_change = 0.0
        if len(closes) >= 2 and closes[-1] != 0:
            pct_change = ((closes[0] - closes[-1]) / closes[-1]) * 100.0

        lines.extend(
            [
                "",
                "Summary (last 30 trading days)",
                f"  30-day high: {high_30:.2f}",
                f"  30-day low: {low_30:.2f}",
                f"  Avg volume: {avg_vol:,.0f}",
                f"  Price change %: {pct_change:.2f}%",
            ]
        )

        source_text = "\n".join(lines)
        return {
            "status": "available",
            "source_text": source_text,
            "metadata": {
                "ticker": ticker.upper(),
                "historical_date": historical_date,
                "days_included": len(rows),
            },
        }
    except Exception as exc:
        return _na_result(str(exc))


async def _fetch_sec_form(ticker: str, historical_date: str, form_type: str) -> dict[str, Any]:
    try:
        filing = get_most_recent_filing(ticker.upper(), form_type, historical_date)
        if not filing:
            return _na_result(f"No {form_type} filing found before {historical_date}")

        text = str(filing.get("text", "") or "")
        if not text.strip():
            return _na_result(f"{form_type} filing text is empty")

        return {
            "status": "available",
            "source_text": text,
            "metadata": {
                "source": "sec_edgar",
                "ticker": ticker.upper(),
                "form_type": form_type,
                "filing_date": filing.get("filing_date"),
                "accession_number": filing.get("accession_number"),
                "text_length": len(text),
            },
        }
    except Exception as exc:
        return _na_result(str(exc))


async def fetch_sec_10k(ticker: str, historical_date: str) -> dict[str, Any]:
    return await _fetch_sec_form(ticker, historical_date, "10-K")


async def fetch_sec_10q(ticker: str, historical_date: str) -> dict[str, Any]:
    return await _fetch_sec_form(ticker, historical_date, "10-Q")


async def fetch_sec_8k(ticker: str, historical_date: str) -> dict[str, Any]:
    return await _fetch_sec_form(ticker, historical_date, "8-K")


async def fetch_clinical_trials(ticker: str, historical_date: str) -> dict[str, Any]:
    try:
        company_query = ticker.upper()
        try:
            filings_df = get_ticker_filings(ticker.upper())
            if not filings_df.empty and "company_name" in filings_df.columns:
                first_name = str(filings_df.iloc[0].get("company_name", "")).strip()
                if first_name:
                    company_query = first_name
        except Exception:
            pass

        params = {
            "query.spons": company_query,
            "pageSize": 20,
            "format": "json",
        }

        headers = {"User-Agent": _SEC_USER_AGENT}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get("https://clinicaltrials.gov/api/v2/studies", params=params, headers=headers)
            if resp.status_code >= 400:
                return _na_result(f"ClinicalTrials.gov HTTP {resp.status_code}")
            data = resp.json()

        studies = data.get("studies") or []
        cutoff = _parse_date_or_none(historical_date)
        filtered: list[dict[str, Any]] = []
        for study in studies:
            status_mod = study.get("protocolSection", {}).get("statusModule", {})
            verified_date = status_mod.get("statusVerifiedDate")
            if cutoff and verified_date:
                vd = _parse_date_or_none(str(verified_date).split("T")[0])
                if vd and vd > cutoff:
                    continue
            filtered.append(study)

        filtered = filtered[:20]
        if not filtered:
            return _na_result("No clinical trials found")

        lines = [
            f"Clinical Trials: {ticker.upper()}",
            "========================",
        ]

        for i, study in enumerate(filtered, start=1):
            ident = study.get("protocolSection", {}).get("identificationModule", {})
            status_mod = study.get("protocolSection", {}).get("statusModule", {})
            design_mod = study.get("protocolSection", {}).get("designModule", {})
            sponsor_mod = study.get("protocolSection", {}).get("sponsorCollaboratorsModule", {})

            nct = ident.get("nctId", "N/A")
            title = ident.get("briefTitle") or ident.get("officialTitle") or "Untitled"
            overall_status = status_mod.get("overallStatus", "N/A")

            phases_raw = design_mod.get("phases") or []
            if isinstance(phases_raw, list):
                phases = ", ".join(phases_raw) if phases_raw else "N/A"
            else:
                phases = str(phases_raw)

            sponsor_name = sponsor_mod.get("leadSponsor", {}).get("name", "N/A")

            lines.extend(
                [
                    f"[{i}] {nct}",
                    f"    Title: \"{title}\"",
                    f"    Status: {overall_status}",
                    f"    Phase: {phases}",
                    f"    Sponsor: {sponsor_name}",
                    "",
                ]
            )

        source_text = "\n".join(lines).rstrip()
        return {
            "status": "available",
            "source_text": source_text,
            "metadata": {
                "ticker": ticker.upper(),
                "company_query": company_query,
                "historical_date": historical_date,
                "trials_included": len(filtered),
            },
        }
    except Exception as exc:
        return _na_result(str(exc))


def _parse_pubmed_articles(xml_text: str) -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []
    root = ET.fromstring(xml_text)

    for article in root.findall(".//PubmedArticle"):
        pmid = ""
        title = ""
        abstract = ""

        pmid_el = article.find(".//PMID")
        if pmid_el is not None and pmid_el.text:
            pmid = pmid_el.text.strip()

        title_el = article.find(".//ArticleTitle")
        if title_el is not None:
            title = "".join(title_el.itertext()).strip()

        abstract_parts: list[str] = []
        for abs_el in article.findall(".//Abstract/AbstractText"):
            label = abs_el.attrib.get("Label")
            text = "".join(abs_el.itertext()).strip()
            if text:
                abstract_parts.append(f"{label}: {text}" if label else text)

        abstract = " ".join(abstract_parts).strip()
        articles.append({"pmid": pmid, "title": title, "abstract": abstract})

    return articles


async def fetch_biomedical_evidence(ticker: str, historical_date: str) -> dict[str, Any]:
    try:
        ticker_upper = ticker.upper()

        # openFDA adverse events
        fda_params = {
            "search": f"patient.drug.medicinalproduct:{ticker_upper}",
            "limit": 10,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            fda_resp = await client.get("https://api.fda.gov/drug/event.json", params=fda_params)
            if fda_resp.status_code >= 400:
                fda_data: dict[str, Any] = {"error": f"HTTP {fda_resp.status_code}"}
            else:
                fda_data = fda_resp.json()

        # PubMed esearch + efetch
        maxdate = historical_date.replace("-", "/") if historical_date else ""
        esearch_params = {
            "db": "pubmed",
            "term": ticker_upper,
            "retmode": "json",
            "retmax": 5,
            "datetype": "pdat",
            "maxdate": maxdate,
        }
        if _NCBI_API_KEY:
            esearch_params["api_key"] = _NCBI_API_KEY

        esearch = await _api_request(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params=esearch_params,
            api_name="pubmed_esearch",
            timeout=30.0,
        )

        pmids = (esearch.get("esearchresult") or {}).get("idlist") or []
        pubmed_articles: list[dict[str, str]] = []
        if pmids:
            efetch_params = {
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "xml",
                "rettype": "abstract",
            }
            if _NCBI_API_KEY:
                efetch_params["api_key"] = _NCBI_API_KEY

            async with httpx.AsyncClient(timeout=30.0) as client:
                efetch_resp = await client.get(
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                    params=efetch_params,
                )
                if efetch_resp.status_code < 400:
                    pubmed_articles = _parse_pubmed_articles(efetch_resp.text)

        lines = [
            f"Biomedical Evidence: {ticker_upper}",
            "=============================",
            "",
            "--- Adverse Events (openFDA) ---",
        ]

        total_events = ((fda_data.get("meta") or {}).get("results") or {}).get("total")
        if total_events is None:
            total_events = 0
        lines.append(f"Total events found: {total_events}")
        lines.append("")

        fda_results = fda_data.get("results") or []
        for i, event in enumerate(fda_results[:10], start=1):
            receipt = event.get("receiptdate", "")
            if receipt and len(receipt) == 8:
                receipt = f"{receipt[:4]}-{receipt[4:6]}-{receipt[6:8]}"
            safety = "SERIOUS" if event.get("serious") == "1" else "NON-SERIOUS"

            drugs: list[str] = []
            for drug in (event.get("patient") or {}).get("drug", []):
                med = (drug.get("medicinalproduct") or "").strip()
                if med:
                    drugs.append(med)

            reactions: list[str] = []
            for rxn in (event.get("patient") or {}).get("reaction", []):
                term = (rxn.get("reactionmeddrapt") or "").strip()
                if term:
                    reactions.append(term)

            patient = event.get("patient") or {}
            age = patient.get("patientonsetage", "unknown")
            sex_code = str(patient.get("patientsex", "unknown"))
            sex_map = {"1": "male", "2": "female", "0": "unknown", "unknown": "unknown"}
            sex = sex_map.get(sex_code, "unknown")

            report_no = event.get("safetyreportid", "unknown")
            lines.extend(
                [
                    f"[{i}] Report #{report_no} ({receipt or 'unknown date'}) {safety}",
                    f"    Drugs: {', '.join(drugs) if drugs else 'Unknown'}",
                    f"    Reactions: {', '.join(reactions[:4]) if reactions else 'Unknown'}",
                    f"    Patient: Age {age}, Sex {sex}",
                    "",
                ]
            )

        lines.extend(["--- Publications (PubMed) ---", ""])

        if not pubmed_articles:
            lines.append("No publications found.")
        else:
            for i, article in enumerate(pubmed_articles[:5], start=1):
                lines.extend(
                    [
                        f"[{i}] PMID {article.get('pmid', 'N/A')}",
                        f"    Title: \"{article.get('title', 'Untitled')}\"",
                        f"    Abstract: {_truncate(article.get('abstract', ''), 600)}",
                        "",
                    ]
                )

        source_text = "\n".join(lines).rstrip()
        metadata = {
            "ticker": ticker_upper,
            "historical_date": historical_date,
            "openfda_event_count": len(fda_results),
            "pubmed_article_count": len(pubmed_articles),
        }

        cache_payload = {
            "source_text": source_text,
            "metadata": metadata,
        }
        cache_set("biomedical", ticker_upper, "biomedical_evidence", historical_date, cache_payload)

        return {
            "status": "available",
            "source_text": source_text,
            "metadata": metadata,
        }
    except Exception as exc:
        return _na_result(str(exc))


FETCHER_MAP: dict[str, Callable] = {
    "earnings_call": fetch_earnings,
    "form_4": fetch_form4,
    "news_sentiment": fetch_news_sentiment,
    "price_history": fetch_price_history,
    "sec_10k": fetch_sec_10k,
    "sec_10q": fetch_sec_10q,
    "sec_8k": fetch_sec_8k,
    "clinical_trials": fetch_clinical_trials,
    "biomedical_evidence": fetch_biomedical_evidence,
}


async def fetch_document(document_type: str, ticker: str, historical_date: str) -> dict[str, Any]:
    fetcher = FETCHER_MAP.get(document_type)
    if fetcher is None:
        return {"status": "not_available", "source_text": "", "metadata": {"error": f"Unknown type: {document_type}"}}
    try:
        return await fetcher(ticker, historical_date)
    except Exception as exc:
        return {"status": "not_available", "source_text": "", "metadata": {"error": str(exc)}}
