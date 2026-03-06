from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from rich.text import Text

from tests.test_textual_support import import_penrs_tui


class Spec4TuiIntegrationTests(unittest.TestCase):
    def _build_app_with_bound_widgets(self, mod):
        app = mod.PenrsAuditor()

        widgets = {
            "#master_score": mod.Label("Master Score: --", id="master_score"),
            "#contradictions": mod.Label("Contradictions: --", id="contradictions"),
            "#synthesis": mod.VerticalScroll(id="synthesis", classes="pane"),
            "#evidence_list": mod.ListView(id="evidence_list"),
            "#ground_truth": mod.RichLog(id="ground_truth", classes="pane", wrap=True),
        }

        def _query_one(selector, widget_type=None):
            widget = widgets[selector]
            if widget_type is not None and not isinstance(widget, widget_type):
                raise TypeError(f"Expected {widget_type}, got {type(widget)}")
            return widget

        app.query_one = _query_one
        return app, widgets

    def test_spec_4_4_directory_selection_updates_score_flag_and_evidence(self):
        mod = import_penrs_tui(force_reload=True)
        app, widgets = self._build_app_with_bound_widgets(mod)

        with tempfile.TemporaryDirectory(prefix="spec4_tui_") as tmp:
            prev_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                report_dir = Path("penrs_reports")
                report_dir.mkdir(parents=True, exist_ok=True)
                report_path = report_dir / "report.json"
                report_payload = {
                    "master": {"final_score": 0.73},
                    "arbiter": {
                        "contradictions": [
                            {"name": "Risk mismatch", "flagged": True},
                            {"name": "Benign", "flagged": False},
                        ]
                    },
                    "evidence": [
                        {"verbatim_quote": "quote one", "cache_key": "abc123"},
                        {"verbatim_quote": "", "cache_key": "skip"},
                    ],
                }
                report_path.write_text(json.dumps(report_payload), encoding="utf-8")

                event = mod.DirectoryTree.FileSelected(report_path)
                app.on_report_selected(event)

                self.assertEqual(widgets["#master_score"].text, "Master Score: 0.73")
                self.assertIn("FLAGGED", widgets["#contradictions"].text)
                self.assertIn("Risk mismatch", widgets["#contradictions"].text)
                self.assertTrue(widgets["#synthesis"].has_class("flagged"))
                self.assertEqual(len(widgets["#evidence_list"].items), 1)

                item = widgets["#evidence_list"].items[0]
                self.assertIsInstance(item, mod.EvidenceListItem)
                self.assertEqual(item.verbatim_quote, "quote one")
                self.assertEqual(item.cache_key, "abc123")
                self.assertEqual(widgets["#ground_truth"].entries, [])
            finally:
                os.chdir(prev_cwd)

    def test_spec_4_4_directory_selection_without_flagged_contradictions_clears_flag(self):
        mod = import_penrs_tui(force_reload=True)
        app, widgets = self._build_app_with_bound_widgets(mod)
        widgets["#synthesis"].add_class("flagged")

        with tempfile.TemporaryDirectory(prefix="spec4_tui_") as tmp:
            prev_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                report_dir = Path("penrs_reports")
                report_dir.mkdir(parents=True, exist_ok=True)
                report_path = report_dir / "report.json"
                report_payload = {
                    "master": {"final_score": 0.0},
                    "arbiter": {"contradictions": [{"name": "Consistent", "flagged": False}]},
                    "evidence": [],
                }
                report_path.write_text(json.dumps(report_payload), encoding="utf-8")

                app.on_report_selected(mod.DirectoryTree.FileSelected(report_path))

                self.assertEqual(widgets["#contradictions"].text, "Contradictions: none")
                self.assertFalse(widgets["#synthesis"].has_class("flagged"))
            finally:
                os.chdir(prev_cwd)

    def test_spec_4_4_evidence_selection_highlights_verbatim_quote_in_ground_truth(self):
        mod = import_penrs_tui(force_reload=True)
        app, widgets = self._build_app_with_bound_widgets(mod)

        with tempfile.TemporaryDirectory(prefix="spec4_cache_") as tmp:
            cache_dir = Path(tmp)
            mod.PENRS_CACHE_DIR = cache_dir
            doc_text = "alpha beta highlighted quote omega"
            quote = "highlighted quote"
            (cache_dir / "cachekey1.json").write_text(
                json.dumps({"payload": {"text": doc_text}}),
                encoding="utf-8",
            )

            item = mod.EvidenceListItem(verbatim_quote=quote, cache_key="cachekey1")
            app.on_evidence_selected(mod.ListView.Selected(item))

            self.assertEqual(len(widgets["#ground_truth"].entries), 1)
            rendered = widgets["#ground_truth"].entries[0]
            self.assertIsInstance(rendered, Text)
            self.assertEqual(rendered.plain, doc_text)
            self.assertEqual(len(rendered.spans), 1)

            span = rendered.spans[0]
            self.assertEqual(span.style, "bold black on #00FFFF")
            start = doc_text.find(quote)
            self.assertEqual((span.start, span.end), (start, start + len(quote)))

    def test_spec_4_4_evidence_selection_missing_cache_file_logs_message(self):
        mod = import_penrs_tui(force_reload=True)
        app, widgets = self._build_app_with_bound_widgets(mod)

        with tempfile.TemporaryDirectory(prefix="spec4_cache_") as tmp:
            mod.PENRS_CACHE_DIR = Path(tmp)
            item = mod.EvidenceListItem(verbatim_quote="missing", cache_key="does_not_exist")

            app.on_evidence_selected(mod.ListView.Selected(item))

            self.assertEqual(len(widgets["#ground_truth"].entries), 1)
            self.assertIn("[missing cache file]", widgets["#ground_truth"].entries[0])


if __name__ == "__main__":
    unittest.main()
