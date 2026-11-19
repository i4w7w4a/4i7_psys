"""Regression tests for deterministic context packaging."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/context_pack.py"


def invoke(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class ContextPackTests(unittest.TestCase):
    def test_default_does_not_promote_unapproved(self):
        result = invoke("--node", "P7")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("KN-20261113-001", result.stdout)
        self.assertIn("Отобранные записи", result.stdout)

    def test_research_mode_shows_source_and_unapproved_label(self):
        result = invoke("--node", "P7", "--include-candidates")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("KN-20261113-001", result.stdout)
        self.assertIn("НЕ УТВЕРЖДЕНО КАК ПРАВИЛО", result.stdout)
        self.assertIn("Намерение автора", result.stdout)

    def test_focus_side_is_addressable(self):
        result = invoke("--node", "P3.left", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["requested_nodes"], ["P3.left"])
        self.assertEqual([p["node_id"] for p in data["pillars"]], ["P3"])
        self.assertEqual(data["knowledge_records"], [])

    def test_global_knowledge_has_explicit_status(self):
        result = invoke("--node", "P3", "--include-candidates", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual([r["id"] for r in data["knowledge_records"]], ["KN-20261113-001", "KN-20261114-001"])
        self.assertEqual(data["knowledge_records"][0]["status"], "captured")
        self.assertIn("trusted instructions", data["warning"])

    def test_foundation_candidate_is_not_a_rule(self):
        default = invoke("--node", "P1", "--format", "json")
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertNotIn("KN-20261114-001", default.stdout)
        research = invoke("--node", "P1", "--include-candidates", "--format", "json")
        self.assertEqual(research.returncode, 0, research.stderr)
        records = json.loads(research.stdout)["knowledge_records"]
        candidate = next(r for r in records if r["id"] == "KN-20261114-001")
        self.assertEqual(candidate["status"], "under_review")
        self.assertIsNone(candidate["decision_ref"])

    def test_invalid_node_rejected(self):
        result = invoke("--node", "P8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown focus node", result.stderr)

    def test_invalid_record_limit_rejected(self):
        result = invoke("--max-records", "0")
        self.assertNotEqual(result.returncode, 0)

    def test_unmatched_query(self):
        result = invoke("--query", "несуществующий_термин_123", "--include-candidates", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["knowledge_records"], [])


if __name__ == "__main__":
    unittest.main()
