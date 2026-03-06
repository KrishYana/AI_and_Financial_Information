from __future__ import annotations

import unittest

from tests.test_textual_support import import_penrs_tui


class FrontierTuiUnitTests(unittest.TestCase):
    def test_sanitize_ticker(self):
        mod = import_penrs_tui(force_reload=True)
        self.assertEqual(mod.sanitize_ticker("mrna"), "MRNA")
        self.assertEqual(mod.sanitize_ticker(" MRNA! "), "MRNA")
        self.assertEqual(mod.sanitize_ticker("TOOLONG"), "")
        self.assertEqual(mod.sanitize_ticker(""), "")

    def test_normalize_historical_date_defaults_and_validates(self):
        mod = import_penrs_tui(force_reload=True)

        result = mod.normalize_historical_date("")
        self.assertRegex(result, r"^\d{4}-\d{2}-\d{2}$")

        result = mod.normalize_historical_date("2026-03-01")
        self.assertEqual(result, "2026-03-01")

        with self.assertRaises(ValueError):
            mod.normalize_historical_date("2026-13-01")

    def test_worker_configs_cover_all_document_types(self):
        mod = import_penrs_tui(force_reload=True)
        from utils import DocumentType

        configured_types = {cfg["document_type"] for cfg in mod._WORKER_CONFIGS}
        all_types = set(DocumentType)
        self.assertEqual(configured_types, all_types)

    def test_worker_configs_have_required_fields(self):
        mod = import_penrs_tui(force_reload=True)
        required_keys = {"name", "weight", "signal_density", "rubric_id", "document_type"}

        for cfg in mod._WORKER_CONFIGS:
            for key in required_keys:
                self.assertIn(key, cfg, f"Worker config missing '{key}': {cfg.get('name', 'unknown')}")
            self.assertGreater(cfg["weight"], 0.0)
            self.assertGreater(cfg["signal_density"], 0.0)

    def test_generate_report_summary_basic_structure(self):
        mod = import_penrs_tui(force_reload=True)

        report = {
            "ticker": "TEST",
            "historical_date": "2026-01-01",
            "generated_at": "2026-01-01T00:00:00Z",
            "worker_results": [],
            "arbiter": {"contradictions": []},
            "master": {"final_score": 0.5, "model": "test_model"},
            "evidence": [],
            "report_path": "/tmp/test.json",
        }
        summary = mod.generate_report_summary(report)
        self.assertIn("PENRS BIOTECH INTELLIGENCE REPORT", summary)
        self.assertIn("TEST", summary)
        self.assertIn("0.5", summary)

    def test_greet_screen_invalid_date_does_not_push(self):
        mod = import_penrs_tui(force_reload=True)
        screen = mod.GreetScreen()

        dialogue = mod.Label("", id="spec_dialogue")
        ticker = mod.Input(id="ticker_input")
        historical_date = mod.Input(id="historical_date_input")

        ticker.value = "MRNA"
        historical_date.value = "2026-13-01"

        widgets = {
            "#spec_dialogue": dialogue,
            "#ticker_input": ticker,
            "#historical_date_input": historical_date,
        }

        def _query_one(selector, _widget_type=None):
            return widgets[selector]

        class _AppStub:
            def __init__(self):
                self.session = mod.SessionState()
                self.pushed = False

            def push_screen(self, _screen):
                self.pushed = True

        app = _AppStub()
        screen.query_one = _query_one
        screen.app = app

        screen.on_scout()

        self.assertFalse(app.pushed)
        self.assertIn("YYYY-MM-DD", dialogue.text)


if __name__ == "__main__":
    unittest.main()
