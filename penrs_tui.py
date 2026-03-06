from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from utils import PENRS_CACHE_DIR, DocumentType, cache_key, cache_set


@dataclass
class SessionState:
    username: str = ""
    ticker: str = ""
    historical_date: str = ""
    active_report_path: Path | None = None


class EvidenceListItem(ListItem):
    """List row that carries evidence metadata needed for cache lookup/highlight."""

    def __init__(self, verbatim_quote: str, cache_key_value: str | None = None, **kwargs: Any) -> None:
        if cache_key_value is None:
            cache_key_value = kwargs.get("cache_key")
        if not isinstance(cache_key_value, str):
            raise TypeError("cache_key must be provided")
        super().__init__(Label(verbatim_quote))
        self.verbatim_quote = verbatim_quote
        self.cache_key = cache_key_value


class AuditBehavior:
    def _update_dialogue(self, text: str) -> None:
        query_method = getattr(self, "query", None)
        if query_method is None:
            return
        try:
            matches = query_method("#spec_dialogue")
            if not matches:
                return
            first = matches.first() if hasattr(matches, "first") else matches[0]
            first.update(text)
        except Exception:
            return

    def _load_report_from_path(self, report_path: Path) -> None:
        master_score = self.query_one("#master_score", Label)
        contradictions_label = self.query_one("#contradictions", Label)
        synthesis = self.query_one("#synthesis", VerticalScroll)
        evidence_list = self.query_one("#evidence_list", ListView)
        ground_truth = self.query_one("#ground_truth", RichLog)

        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            master_score.update("Master Score: [parse error]")
            contradictions_label.update(f"Contradictions: [parse error] {exc}")
            synthesis.remove_class("flagged")
            evidence_list.clear()
            ground_truth.clear()
            self._update_dialogue("Spec: Could not parse that report, partner.")
            return

        master = report.get("master", {}) if isinstance(report, dict) else {}
        arbiter = report.get("arbiter", {}) if isinstance(report, dict) else {}
        evidence = report.get("evidence")
        if not isinstance(evidence, list):
            evidence = master.get("evidence", []) if isinstance(master, dict) else []

        final_score = master.get("final_score") if isinstance(master, dict) else None
        master_score.update(f"Master Score: {final_score}")

        contradictions = arbiter.get("contradictions", []) if isinstance(arbiter, dict) else []
        flagged = [
            item for item in contradictions if isinstance(item, dict) and bool(item.get("flagged"))
        ]
        if flagged:
            synthesis.add_class("flagged")
        else:
            synthesis.remove_class("flagged")

        if flagged:
            names = ", ".join(str(item.get("name", "Unknown")) for item in flagged)
            contradictions_label.update(f"Contradictions: FLAGGED ({names})")
        else:
            contradictions_label.update("Contradictions: none")

        evidence_list.clear()
        for node in evidence:
            if not isinstance(node, dict):
                continue
            quote = node.get("verbatim_quote")
            cache_key_value = node.get("cache_key")
            if isinstance(quote, str) and quote and isinstance(cache_key_value, str) and cache_key_value:
                evidence_list.append(
                    EvidenceListItem(verbatim_quote=quote, cache_key_value=cache_key_value)
                )

        ground_truth.clear()
        self._update_dialogue(
            "Spec: Pick an evidence quote and I'll pull the raw source text for you."
        )

    @on(DirectoryTree.FileSelected)
    def on_report_selected(self, event: DirectoryTree.FileSelected) -> None:
        report_path = Path(event.path)
        if report_path.suffix.lower() != ".json":
            return

        report_root = Path("./penrs_reports").resolve()
        resolved = report_path.resolve()
        if resolved != report_root and report_root not in resolved.parents:
            return

        self._load_report_from_path(resolved)

    @on(ListView.Selected, "#evidence_list")
    def on_evidence_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if item is None or not isinstance(item, EvidenceListItem):
            return

        ground_truth = self.query_one("#ground_truth", RichLog)
        ground_truth.clear()

        cache_file = PENRS_CACHE_DIR / f"{item.cache_key}.json"
        if not cache_file.exists():
            ground_truth.write(f"[missing cache file] {cache_file}")
            self._update_dialogue("Spec: Cache came up empty for that quote.")
            return

        try:
            cache_record = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            ground_truth.write(f"[cache parse error] {exc}")
            return

        payload = cache_record.get("payload", cache_record) if isinstance(cache_record, dict) else cache_record
        raw_document = self._extract_raw_text(payload)
        text = Text(raw_document)

        start = raw_document.find(item.verbatim_quote)
        if start != -1:
            end = start + len(item.verbatim_quote)
            text.stylize("bold black on #00FFFF", start, end)

        ground_truth.write(text)
        self._update_dialogue(
            "Spec: Highlighted the exact quote in source. That's your audit trail, partner."
        )

    def _extract_raw_text(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            for key in ("text", "raw_text", "content", "document", "body"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
            return json.dumps(payload, indent=2, ensure_ascii=False)
        if isinstance(payload, list):
            return json.dumps(payload, indent=2, ensure_ascii=False)
        return str(payload)


class PenrsAuditor(App, AuditBehavior):
    """Legacy auditor-only app retained for spec4 compatibility."""

    CSS_PATH = "penrs_tui.tcss"
    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DirectoryTree("./penrs_reports", id="ledger", classes="pane")
            with Vertical():
                with VerticalScroll(id="synthesis", classes="pane"):
                    yield Label("Master Score: --", id="master_score")
                    yield Label("Contradictions: --", id="contradictions")
                    yield ListView(id="evidence_list")
                yield RichLog(id="ground_truth", classes="pane", wrap=True)


class DnaSpinner(Static):
    _FRAMES = (
        "()  ()  ()  ()\n ||\\/||\\/||\\/||\n ||/\\||/\\||/\\||\n()  ()  ()  ()",
        " ()  ()  ()  ()\n||\\/||\\/||\\/|| \n||/\\||/\\||/\\|| \n ()  ()  ()  ()",
        "()   ()   ()   ()\n |\\/||\\/||\\/||\\/|\n |/\\||/\\||/\\||/\\|\n()   ()   ()   ()",
        " ()   ()   ()   ()\n|\\/||\\/||\\/||\\/| \n|/\\||/\\||/\\||/\\| \n ()   ()   ()   ()",
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(self._FRAMES[0], **kwargs)
        self._idx = 0

    def on_mount(self) -> None:
        self.set_interval(0.25, self._tick)

    def _tick(self) -> None:
        self._idx = (self._idx + 1) % len(self._FRAMES)
        self.update(self._FRAMES[self._idx])

    @staticmethod
    def frame_at(index: int) -> str:
        frames = DnaSpinner._FRAMES
        return frames[index % len(frames)]


class AuthScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Label("PENRS BIOTECH FRONTIER", id="auth_title")
        yield DnaSpinner(id="dna_spinner")
        yield Label("Spoofed Access Console", id="auth_subtitle")
        yield Input(placeholder="Trail Name", id="username")
        yield Button("ENTER", id="enter_button")

    @on(Button.Pressed, "#enter_button")
    def on_enter(self) -> None:
        username = self.query_one("#username", Input).value.strip() or "Trailblazer"
        app = self.app
        app.session.username = username
        app.push_screen(GreetScreen())


class GreetScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Label("[SPEC] Hey there, this is Spec. Now what do you want to look into in biotech so I can give you my rec, partner ;)", id="spec_dialogue")
        yield Input(placeholder="Ticker (e.g., MRNA)", id="ticker_input")
        yield Input(placeholder="Historical Date YYYY-MM-DD (optional, defaults to today)", id="historical_date_input")
        yield Button("SCOUT TICKER", id="scout_button")

    @on(Input.Changed, "#ticker_input, #historical_date_input")
    def on_force_uppercase(self, event: Input.Changed) -> None:
        upper_value = event.value.upper()
        if event.input.value != upper_value:
            event.input.value = upper_value

    @on(Button.Pressed, "#scout_button")
    def on_scout(self) -> None:
        ticker_raw = self.query_one("#ticker_input", Input).value
        ticker = sanitize_ticker(ticker_raw)
        dialogue = self.query_one("#spec_dialogue", Label)
        if not ticker:
            dialogue.update("[SPEC] Need a real ticker symbol, partner. 1-5 letters.")
            return

        date_str = self.query_one("#historical_date_input", Input).value.strip()
        try:
            historical_date = normalize_historical_date(date_str)
        except ValueError:
            dialogue.update("[SPEC] Use dates like YYYY-MM-DD, partner.")
            return

        app = self.app
        app.session.ticker = ticker
        app.session.historical_date = historical_date
        app.push_screen(ProgressScreen())


class ProgressScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Label("[SPEC] Saddling up the data feeds...", id="spec_dialogue")
        yield Label("Status: Initializing", id="progress_status")

    def on_mount(self) -> None:
        self.run_worker(self._run_analysis(), exclusive=True)

    async def _run_analysis(self) -> None:
        status = self.query_one("#progress_status", Label)
        dialogue = self.query_one("#spec_dialogue", Label)
        app = self.app

        try:
            ticker = app.session.ticker

            def on_progress(message: str) -> None:
                status.update(f"Status: {message}")
                dialogue.update(f"[SPEC] {ticker}: {message}")

            on_progress("Booting frontier analysis")
            report_path = await run_frontier_analysis(
                ticker=ticker,
                historical_date=app.session.historical_date,
                progress_callback=on_progress,
            )
            app.session.active_report_path = report_path
            dialogue.update("[SPEC] Report is ready. Let's audit the trail.")
            app.push_screen(AuditScreen())
        except Exception as exc:  # pragma: no cover - UI failure path
            dialogue.update(f"[SPEC] Hit a snag: {exc}")
            status.update("Status: Failed")


class AuditScreen(Screen, AuditBehavior):
    BINDINGS = [("q", "app.quit", "Quit"), ("n", "new_analysis", "New")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DirectoryTree("./penrs_reports", id="ledger", classes="pane")
            with Vertical():
                with VerticalScroll(id="synthesis", classes="pane"):
                    yield Label("[SPEC] Select a report from the ledger to begin.", id="spec_dialogue")
                    yield Label("Master Score: --", id="master_score")
                    yield Label("Contradictions: --", id="contradictions")
                    yield RichLog(id="report_summary", wrap=True)
                    yield ListView(id="evidence_list")
                yield RichLog(id="ground_truth", classes="pane", wrap=True)
        with Horizontal():
            yield Button("NEW ANALYSIS", id="new_analysis_btn")
        yield Footer()

    def on_mount(self) -> None:
        active = self.app.session.active_report_path
        if active and active.exists():
            self._load_report_from_path(active)

    def action_new_analysis(self) -> None:
        self.app.push_screen(GreetScreen())

    @on(Button.Pressed, "#new_analysis_btn")
    def on_new_analysis_button(self) -> None:
        self.action_new_analysis()

    def _load_report_from_path(self, report_path: Path) -> None:
        super()._load_report_from_path(report_path)
        try:
            summary_log = self.query_one("#report_summary", RichLog)
        except Exception:
            return

        summary_log.clear()
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary_log.write("[summary unavailable: report parse error]")
            return

        summary_path = report_path.with_suffix(".txt")
        summary_text = ""
        if summary_path.exists():
            try:
                summary_text = summary_path.read_text(encoding="utf-8")
            except OSError:
                summary_text = ""
        if not summary_text:
            summary_text = generate_report_summary(report)
        summary_log.write(summary_text)


class PenrsFrontierApp(App):
    CSS_PATH = "penrs_tui.tcss"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self.session = SessionState()

    def on_mount(self) -> None:
        self.push_screen(AuthScreen())


def sanitize_ticker(value: str) -> str:
    ticker = re.sub(r"[^A-Za-z]", "", (value or "")).upper()
    if 1 <= len(ticker) <= 5:
        return ticker
    return ""


def normalize_historical_date(date_str: str) -> str:
    """Return a validated YYYY-MM-DD date string. Defaults to today if empty."""
    if not date_str.strip():
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")


_WORKER_CONFIGS: list[dict[str, Any]] = [
    {"name": "Earnings Analyst", "weight": 1.0, "signal_density": 0.85, "rubric_id": "earnings_call", "document_type": DocumentType.EARNINGS_CALL},
    {"name": "Insider Tracker", "weight": 0.9, "signal_density": 0.7, "rubric_id": "form_4", "document_type": DocumentType.FORM_4},
    {"name": "News Sentinel", "weight": 1.0, "signal_density": 0.9, "rubric_id": "news_sentiment", "document_type": DocumentType.NEWS_SENTIMENT},
    {"name": "Price Technician", "weight": 0.8, "signal_density": 0.75, "rubric_id": "price_history", "document_type": DocumentType.PRICE_HISTORY},
    {"name": "Annual Report Analyst", "weight": 1.0, "signal_density": 0.8, "rubric_id": "sec_10k", "document_type": DocumentType.SEC_10K},
    {"name": "Quarterly Report Analyst", "weight": 0.9, "signal_density": 0.8, "rubric_id": "sec_10q", "document_type": DocumentType.SEC_10Q},
    {"name": "Current Events Analyst", "weight": 0.7, "signal_density": 0.65, "rubric_id": "sec_8k", "document_type": DocumentType.SEC_8K},
    {"name": "Clinical Pipeline Scout", "weight": 1.0, "signal_density": 0.85, "rubric_id": "clinical_trials", "document_type": DocumentType.CLINICAL_TRIALS},
    {"name": "Biomedical Evidence Scout", "weight": 0.9, "signal_density": 0.8, "rubric_id": "biomedical_evidence", "document_type": DocumentType.BIOMEDICAL_EVIDENCE},
]


def _load_env() -> None:
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _create_llm_invoker() -> Any:
    _load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return lambda prompt, *, system="": "{}"

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    def invoke(prompt: str, *, system: str = "Respond only in valid JSON.") -> str:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    return invoke


def _make_fetcher_document_fetcher(fetcher_module: Any) -> Any:
    async def document_fetcher(ticker: str, document_type: DocumentType, date_range: dict | None = None) -> dict[str, Any]:
        date_to = (date_range or {}).get("to", "")
        if not date_to:
            date_to = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result = await fetcher_module.fetch_document(document_type.value, ticker, date_to)
        source_text = result.get("source_text", "")
        if result.get("status") == "available" and source_text:
            cache_set(
                api=document_type.value,
                ticker=ticker,
                doc_type=document_type.value,
                date=date_to,
                payload={"text": source_text},
            )
            return {"status": "available", "data": source_text}
        return {"status": "not_released", "data": {"apis_attempted": [document_type.value]}}
    return document_fetcher


def generate_report_summary(report_payload: dict[str, Any]) -> str:
    divider = "=" * 64
    lines: list[str] = [
        divider,
        "  PENRS BIOTECH INTELLIGENCE REPORT",
        divider,
        f"  Ticker:     {report_payload.get('ticker', '--')}",
        f"  As-Of Date: {report_payload.get('historical_date', '--')}",
        f"  Generated:  {report_payload.get('generated_at', '--')}",
        f"  Model:      {report_payload.get('master', {}).get('model', '--')}",
        divider,
        "",
        f"  MASTER SCORE: {report_payload.get('master', {}).get('final_score', '--')}",
        "  ----------------",
    ]

    worker_results = report_payload.get("worker_results", [])
    if not isinstance(worker_results, list):
        worker_results = []
    master = report_payload.get("master", {})
    available = (
        master.get("available_worker_count", len(worker_results))
        if isinstance(master, dict)
        else len(worker_results)
    )
    total = (
        master.get("total_worker_count", len(worker_results))
        if isinstance(master, dict)
        else len(worker_results)
    )
    lines.append(f"  Workers Available: {available}/{total}")
    lines.extend(["", "  WORKER RESULTS:"])

    for idx, worker_result in enumerate(worker_results, start=1):
        if not isinstance(worker_result, dict):
            continue
        worker = worker_result.get("worker", {})
        result = worker_result.get("result", {})
        name = worker.get("name", "Unknown") if isinstance(worker, dict) else "Unknown"
        document_type = worker_result.get("document_type", "unknown")
        score = result.get("score", "--") if isinstance(result, dict) else "--"
        weight = worker.get("weight", "--") if isinstance(worker, dict) else "--"
        thesis = result.get("thesis", "--") if isinstance(result, dict) else "--"
        lines.append(f"  {idx}. {name} [{document_type}]")
        lines.append(f"     Score: {score} | Weight: {weight}")
        lines.append(f"     Thesis: {thesis}")
        evidence_nodes = result.get("evidence_nodes", []) if isinstance(result, dict) else []
        if isinstance(evidence_nodes, list) and evidence_nodes:
            first = evidence_nodes[0]
            if isinstance(first, dict):
                quote = first.get("verbatim_quote")
                reasoning = first.get("reasoning")
                if quote:
                    lines.append(f'     Evidence: "{quote}" ({reasoning})')
        lines.append("")

    lines.append("  CONTRADICTION FLAGS:")
    arbiter = report_payload.get("arbiter", {})
    contradictions = arbiter.get("contradictions", []) if isinstance(arbiter, dict) else []
    if isinstance(contradictions, list) and contradictions:
        for item in contradictions:
            if not isinstance(item, dict):
                continue
            prefix = "[!!]" if item.get("flagged") else "[  ]"
            name = item.get("name", "Unknown")
            severity = item.get("severity", "Unknown")
            evidence = item.get("evidence", "")
            if evidence:
                lines.append(f'  {prefix} {name} ({severity}) - "{evidence}"')
            else:
                lines.append(f"  {prefix} {name} ({severity}) - not detected")
    else:
        lines.append("  [  ] none")

    lines.extend(["", "  EVIDENCE TRAIL:"])
    evidence = report_payload.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []
    if evidence:
        for node in evidence:
            if not isinstance(node, dict):
                continue
            quote = node.get("verbatim_quote", "")
            ckey = node.get("cache_key", "")
            reasoning = node.get("reasoning", "")
            lines.append(f'  - "{quote}" [cache: {ckey}]')
            lines.append(f"    Reasoning: {reasoning}")
    else:
        lines.append("  - none")

    report_path_str = report_payload.get("report_path", "")
    summary_path_str = ""
    if isinstance(report_path_str, str) and report_path_str:
        summary_path_str = str(Path(report_path_str).with_suffix(".txt"))

    lines.extend(
        [
            "",
            divider,
            f"  Report saved to: {report_path_str or '--'}",
            f"  Summary saved to: {summary_path_str or '--'}",
            divider,
        ]
    )
    return "\n".join(lines)


async def run_frontier_analysis(
    ticker: str,
    historical_date: str,
    progress_callback: Any = None,
) -> Path:
    # Lazy imports from notebooks to avoid circular dependencies at module load
    from worker_nodes import PENRSWorker
    from orchestrator import ArbiterAgent, MasterAgent, run_all_workers, _evaluate_with_arbiter
    import fetchers

    if callable(progress_callback):
        progress_callback("Initializing LLM and document fetchers")

    llm_invoker = _create_llm_invoker()
    doc_fetcher = _make_fetcher_document_fetcher(fetchers)

    workers = []
    for cfg in _WORKER_CONFIGS:
        workers.append(
            PENRSWorker(
                name=cfg["name"],
                weight=cfg["weight"],
                signal_density=cfg["signal_density"],
                rubric_id=cfg["rubric_id"],
                document_type=cfg["document_type"],
                llm_invoker=llm_invoker,
                document_fetcher=doc_fetcher,
            )
        )

    if callable(progress_callback):
        progress_callback(f"Running {len(workers)} workers for {ticker}")

    worker_results = await run_all_workers(
        workers, ticker, historical_date, historical_date,
    )

    if callable(progress_callback):
        available_count = sum(1 for r in worker_results if r.get("status") == "available")
        progress_callback(f"Workers complete: {available_count}/{len(workers)} available")

    if callable(progress_callback):
        progress_callback("Running arbiter evaluation")
    arbiter = ArbiterAgent()
    arbiter_result = _evaluate_with_arbiter(arbiter, worker_results)

    if callable(progress_callback):
        progress_callback("Running master synthesis")
    master_agent = MasterAgent()
    master_result = master_agent.synthesize(
        ticker=ticker,
        date_from=historical_date,
        date_to=historical_date,
        worker_results=worker_results,
        arbiter_result=arbiter_result,
    )

    now = datetime.now(timezone.utc)
    report_dir = Path("penrs_reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = (
        report_dir
        / f"{ticker}_asof_{historical_date}_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    ).resolve()

    report_payload = {
        "ticker": ticker,
        "historical_date": historical_date,
        "generated_at": now.isoformat(),
        "worker_results": worker_results,
        "arbiter": arbiter_result,
        "master": master_result,
        "evidence": master_result.get("evidence", []),
        "report_path": str(report_path),
    }

    if callable(progress_callback):
        progress_callback("Writing JSON report")
    report_path.write_text(json.dumps(report_payload, indent=2, ensure_ascii=True), encoding="utf-8")
    summary_path = report_path.with_suffix(".txt")
    summary_text = generate_report_summary(report_payload)
    if callable(progress_callback):
        progress_callback("Writing text summary")
    summary_path.write_text(summary_text, encoding="utf-8")
    if callable(progress_callback):
        progress_callback("Completed analysis")
    return report_path


if __name__ == "__main__":
    if "--auditor" in sys.argv:
        PenrsAuditor().run()
    else:
        PenrsFrontierApp().run()
