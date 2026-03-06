from __future__ import annotations

import inspect
from pathlib import Path
import unittest

from tests.test_textual_support import import_penrs_tui


class Spec4TuiUnitTests(unittest.TestCase):
    def test_spec_4_1_framework_initialization(self):
        mod = import_penrs_tui(force_reload=True)

        self.assertTrue(hasattr(mod, "PenrsAuditor"))
        self.assertEqual(mod.PenrsAuditor.CSS_PATH, "penrs_tui.tcss")
        self.assertEqual(mod.PenrsAuditor.BINDINGS, [("q", "quit", "Quit")])

    def test_spec_4_2_css_design_system_rules_present(self):
        css_path = Path("penrs_tui.tcss")
        css = css_path.read_text(encoding="utf-8")

        required_rules = [
            "Screen {",
            "background: #101418;",
            "DirectoryTree, VerticalScroll, RichLog {",
            "border: double #00FFFF;",
            "padding: 1;",
            "DirectoryTree:focus, VerticalScroll:focus, RichLog:focus {",
            ".flagged {",
            "border: double #FF003C;",
            "RichLog {",
            "color: #39FF14;",
        ]

        for rule in required_rules:
            self.assertIn(rule, css)

    def test_spec_4_3_compose_contains_required_layout_widgets(self):
        mod = import_penrs_tui(force_reload=True)
        app = mod.PenrsAuditor()

        nodes = list(app.compose())
        compose_src = inspect.getsource(mod.PenrsAuditor.compose)

        headers = [node for node in nodes if isinstance(node, mod.Header)]
        ledgers = [
            node
            for node in nodes
            if isinstance(node, mod.DirectoryTree)
            and node.id == "ledger"
            and node.path == "./penrs_reports"
            and node.has_class("pane")
        ]
        synth_nodes = [
            node
            for node in nodes
            if isinstance(node, mod.VerticalScroll) and node.id == "synthesis" and node.has_class("pane")
        ]
        master_labels = [node for node in nodes if isinstance(node, mod.Label) and node.id == "master_score"]
        contradictions_labels = [node for node in nodes if isinstance(node, mod.Label) and node.id == "contradictions"]
        evidence_lists = [node for node in nodes if isinstance(node, mod.ListView) and node.id == "evidence_list"]
        ground_truth_logs = [
            node
            for node in nodes
            if isinstance(node, mod.RichLog) and node.id == "ground_truth" and node.has_class("pane") and node.wrap
        ]

        self.assertEqual(len(headers), 1)
        self.assertEqual(len(ledgers), 1)
        self.assertIn('with VerticalScroll(id="synthesis", classes="pane")', compose_src)
        self.assertLessEqual(len(synth_nodes), 1)
        self.assertEqual(len(master_labels), 1)
        self.assertEqual(len(contradictions_labels), 1)
        self.assertEqual(len(evidence_lists), 1)
        self.assertEqual(len(ground_truth_logs), 1)

    def test_extract_raw_text_prefers_textual_keys_and_serializes_structures(self):
        mod = import_penrs_tui(force_reload=True)
        app = mod.PenrsAuditor()

        self.assertEqual(app._extract_raw_text("raw"), "raw")
        self.assertEqual(app._extract_raw_text({"text": "t", "content": "c"}), "t")
        self.assertEqual(app._extract_raw_text({"raw_text": "rt"}), "rt")
        self.assertIn('"k": "v"', app._extract_raw_text({"k": "v"}))
        self.assertIn('"x"', app._extract_raw_text(["x"]))
        self.assertEqual(app._extract_raw_text(42), "42")


if __name__ == "__main__":
    unittest.main()
