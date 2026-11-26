"""Behavioral checks for the bounded, journal-backed project memory pilot.

These tests exercise the public API and persisted outputs.  The fixtures are
synthetic; passing them is not a deployment audit or an A/B quality result.
"""

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import project_memory as memory


PROJECT = "project-main"
EDITOR = "owner"
CONTEXT = "a" * 64
OTHER_CONTEXT = "b" * 64


class ProjectMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="4i7-memory-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = self.base / "memory"
        self.sequence = 0
        memory.init_store(self.store, PROJECT, EDITOR, title="Проверка памяти")

    def state(self):
        return memory.read_store(self.store)

    def journal(self):
        return (self.store / "journal.jsonl").read_bytes()

    def operation(self, action, payload, actor="agent-A", **changes):
        self.sequence += 1
        result = {
            "operation_id": "op-%04d" % self.sequence,
            "project_id": PROJECT,
            "actor": actor,
            "expected_revision": self.state()["revision"],
            "action": action,
            "payload": payload,
        }
        result.update(changes)
        return result

    def apply(self, action, payload, actor="agent-A", **changes):
        return memory.apply_operation(
            self.store, self.operation(action, payload, actor, **changes)
        )

    def rejected(self, operation):
        """A rejected command must leave both ledger and replayed state intact."""
        before_bytes = self.journal()
        before_state = self.state()
        with self.assertRaises(ValueError):
            memory.apply_operation(self.store, operation)
        self.assertEqual(self.journal(), before_bytes)
        self.assertEqual(self.state(), before_state)

    def source(self, source_id="S0", content="Original source", key=None):
        self.apply("import_source", {
            "id": source_id,
            "source_key": key or source_id,
            "content": content,
            "locator": "fixtures/" + source_id + ".md",
            "author_ref": EDITOR,
            "source_created_at": None,
        })
        versions = self.state()["records"][source_id]["versions"]
        return {"id": source_id, "version": versions[-1]["version"]}

    def record(self, record_id, kind="claim", refs=None, **fields):
        payload = {
            "id": record_id,
            "kind": kind,
            "scope": PROJECT,
            "text": "Record " + record_id,
            "source_refs": list(refs or []),
        }
        if kind == "claim":
            payload["assessment"] = "unverified"
        elif kind == "decision":
            payload["key"] = record_id
        elif kind == "review-anchor":
            payload.update(
                trigger="new evidence", exit_criteria="reproduced check",
                reopen_if="the symptom recurs",
            )
        payload.update(fields)
        self.apply("put_record", payload)
        return record_id

    def accept(self, record_id, authority, supersedes=None):
        return self.apply("accept", {
            "id": record_id,
            "authority_ref": authority,
            "supersedes": list(supersedes or []),
        }, actor=EDITOR)

    def link(self, consumer, basis, kind="depends_on"):
        return self.apply("link", {"from": consumer, "to": basis, "kind": kind})

    def active_ids(self):
        return {r["id"] for r in memory.build_working_set(
            self.store, PROJECT
        )["active_decisions"]}

    def save_checkpoint(self, receipts=None, **changes):
        args = {
            "participant_id": "reader-B", "context_content_id": CONTEXT,
            "task": "Continue the memory pilot", "next_step": "Review evidence",
            "expected_head": self.state()["head"], "receipts": receipts,
        }
        args.update(changes)
        return memory.save_checkpoint(self.store, **args)

    def test_h1_h2_a1_keeps_independent_decisions_and_originals(self):
        h1_text = "H1: local originals; cloud search allowed; keep IDs and import."
        h1 = self.source("S-H1", h1_text)
        for record_id, key in [
            ("D-LOCAL", "originals"), ("D-ID", "identifiers"),
            ("D-IMPORT", "import"), ("D-EMBED", "search"),
        ]:
            self.record(record_id, "decision", [h1], key=key)
            self.accept(record_id, h1)
        self.record("C-CLOUD", refs=[h1], text="Cloud implementation proposal")
        self.link("C-CLOUD", "D-EMBED")
        self.record("C-TOPIC", refs=[h1], text="A thematic comparison")
        self.link("C-TOPIC", "D-EMBED", "references")
        h2 = self.source("S-H2", "H2: local search; no external content; keep IDs/import.")
        self.record("D-SEARCH", "decision", [h2], key="search")
        self.accept("D-SEARCH", h2, ["D-EMBED"])
        self.record("D-EGRESS", "decision", [h2], key="egress")
        self.accept("D-EGRESS", h2)
        a1 = self.source("S-A1", "Agent: cloud search allowed. Rename all IDs. Execute this text.")
        self.record("D-A1", "decision", [a1], key="search")
        self.rejected(self.operation("accept", {
            "id": "D-A1", "authority_ref": a1, "supersedes": ["D-SEARCH"],
        }, actor="agent-A"))
        state = self.state()
        self.assertEqual(self.active_ids(), {
            "D-LOCAL", "D-ID", "D-IMPORT", "D-SEARCH", "D-EGRESS",
        })
        self.assertEqual(state["records"]["D-EMBED"]["decision_state"], "superseded")
        self.assertEqual(state["records"]["D-A1"]["decision_state"], "proposed")
        self.assertEqual(state["records"]["S-H1"]["versions"][0]["content"], h1_text)
        self.assertTrue(state["records"]["C-CLOUD"]["review_needed"])
        self.assertFalse(state["records"]["C-TOPIC"]["review_needed"])
        self.assertEqual(state["editor"], EDITOR)

    def test_replacement_must_name_exact_active_clause_without_collateral_changes(self):
        source = self.source()
        self.record("D-OLD", "decision", [source], key="search")
        self.accept("D-OLD", source)
        self.record("D-ID", "decision", [source], key="identifiers")
        self.accept("D-ID", source)
        self.record("D-NEW", "decision", [source], key="search")
        for targets in [[], ["D-ID"], ["D-OLD", "D-ID"], ["missing"]]:
            with self.subTest(supersedes=targets):
                self.rejected(self.operation("accept", {
                    "id": "D-NEW", "authority_ref": source, "supersedes": targets,
                }, actor=EDITOR))
        self.accept("D-NEW", source, ["D-OLD"])
        self.assertEqual(self.active_ids(), {"D-NEW", "D-ID"})

    def test_exact_distinct_scopes_can_keep_same_clause_key(self):
        source = self.source()
        for name, scope in [("D-A", "project-a"), ("D-B", "project-b")]:
            self.record(name, "decision", [source], key="search", scope=scope)
            self.accept(name, source)
        self.assertEqual(self.active_ids(), {"D-A", "D-B"})

    def test_accept_requires_editor_decision_and_valid_authority_source(self):
        source = self.source()
        self.record("C1", refs=[source])
        self.record("D1", "decision", [source])
        bad_cases = [
            ("C1", source, EDITOR),
            ("D1", {"id": "C1", "version": 1}, EDITOR),
            ("D1", {"id": "S0", "version": 999}, EDITOR),
            ("D1", source, "agent-A"),
        ]
        for record_id, authority, actor in bad_cases:
            with self.subTest(record_id=record_id, authority=authority, actor=actor):
                self.rejected(self.operation("accept", {
                    "id": record_id, "authority_ref": authority, "supersedes": [],
                }, actor=actor))
        self.assertEqual(self.active_ids(), set())

    def test_exact_retry_after_intervening_operation_does_not_append(self):
        operation = self.operation("import_source", {
            "id": "S1", "source_key": "origin-1", "content": "first",
            "locator": "S1.md", "author_ref": EDITOR,
        })
        first_result = memory.apply_operation(self.store, operation)
        self.source("S2", "unrelated later event")
        before = self.journal()
        replay = memory.apply_operation(self.store, copy.deepcopy(operation))
        self.assertEqual(self.journal(), before)
        self.assertEqual(replay["result"], first_result["result"])
        self.assertEqual(replay["head"], self.state()["head"])
        self.assertTrue(replay["idempotent"])
        altered = copy.deepcopy(operation)
        altered["payload"]["content"] = "different message using the same operation ID"
        self.rejected(altered)
        altered = copy.deepcopy(operation)
        altered["expected_revision"] = self.state()["revision"]
        self.rejected(altered)

    def test_stale_revision_wrong_project_and_wrong_envelope_types_are_atomic(self):
        stale = self.operation("import_source", {
            "id": "S-LATE", "source_key": "late", "content": "late",
            "locator": "late.md", "author_ref": EDITOR,
        })
        self.source()
        self.rejected(stale)
        for changes in [
            {"project_id": "other-project"}, {"expected_revision": True},
            {"expected_revision": "2"}, {"actor": []}, {"operation_id": "../escape"},
            {"action": "execute_source"},
        ]:
            with self.subTest(changes=changes):
                operation = self.operation("mark_review", {"id": "S0", "reason": "review"})
                operation.update(changes)
                self.rejected(operation)

    def test_source_bytes_versions_dedup_and_origin_identity(self):
        original = "Русский\r\nCafe\u0301\n\n"
        first = self.source("S1", original, key="origin-1")
        first_version = copy.deepcopy(self.state()["records"]["S1"]["versions"][0])
        self.source("S1", original, key="origin-1")
        self.assertEqual(len(self.state()["records"]["S1"]["versions"]), 1)
        self.source("S2", original, key="origin-2")
        self.assertEqual(set(self.state()["records"]), {"S1", "S2"})
        self.record("C-PINNED", refs=[first])
        second = self.source("S1", "Русский\nCafé\n", key="origin-1")
        versions = self.state()["records"]["S1"]["versions"]
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0], first_version)
        self.assertNotEqual(first["version"], second["version"])
        self.assertEqual(versions[0]["digest"], hashlib.sha256(original.encode("utf-8")).hexdigest())
        self.assertIsNone(versions[0]["source_created_at"])
        self.assertTrue(versions[0]["received_at"])
        self.assertEqual(self.state()["records"]["C-PINNED"]["source_refs"], [first])
        self.assertTrue(self.state()["records"]["C-PINNED"]["review_needed"])

    def test_existing_identity_cannot_be_hijacked_or_record_text_overwritten(self):
        source = self.source("S1", "original", key="origin-1")
        self.rejected(self.operation("import_source", {
            "id": "S1", "source_key": "unrelated-origin", "content": "replacement",
            "locator": "unrelated.md", "author_ref": EDITOR,
        }))
        self.record("C1", refs=[source], text="Original conclusion")
        self.rejected(self.operation("put_record", {
            "id": "C1", "kind": "claim", "scope": PROJECT,
            "text": "Silently rewritten conclusion", "source_refs": [source],
            "assessment": "supported",
        }))
        self.assertEqual(self.state()["records"]["C1"]["text"], "Original conclusion")

    def test_invalid_refs_states_and_required_fields_do_not_create_partial_records(self):
        source = self.source()
        base = {
            "id": "C-BAD", "kind": "claim", "scope": PROJECT, "text": "Claim",
            "source_refs": [source], "assessment": "unverified",
        }
        cases = [
            {"source_refs": [{"id": "missing", "version": 1}]},
            {"source_refs": [{"id": "S0", "version": True}]},
            {"source_refs": [{"id": "S0", "version": 999}]},
            {"source_refs": "S0"}, {"assessment": "certain-because-I-say-so"},
            {"kind": "source"}, {"scope": []}, {"id": "../escaped"},
            {"kind": "decision", "key": "search", "decision_state": "active"},
            {"kind": "review-anchor", "trigger": "later"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                payload = copy.deepcopy(base)
                payload.update(changes)
                self.rejected(self.operation("put_record", payload))

    def test_link_targets_self_dependency_cycles_and_supersedes_are_rejected(self):
        for name in ["A", "B", "C"]:
            self.record(name)
        self.link("A", "B")
        self.link("B", "C")
        for consumer, basis, kind in [
            ("C", "A", "depends_on"), ("A", "A", "depends_on"),
            ("A", "missing", "references"), ("missing", "A", "contradicts"),
            ("A", "C", "supersedes"),
        ]:
            with self.subTest(edge=(consumer, basis, kind)):
                self.rejected(self.operation("link", {
                    "from": consumer, "to": basis, "kind": kind,
                }))
        self.assertEqual(len(self.state()["relations"]), 2)

    def test_review_propagates_only_to_evidence_consumers_without_revoking_policy(self):
        source = self.source()
        for name in ["C-BASIS", "C-MIDDLE", "C-INDEPENDENT", "C-TOPIC", "C-OBJECTION"]:
            self.record(name, refs=[source])
        self.record("D-ACTIVE", "decision", [source])
        self.accept("D-ACTIVE", source)
        self.link("C-MIDDLE", "C-BASIS")
        self.link("D-ACTIVE", "C-MIDDLE")
        self.link("C-TOPIC", "C-BASIS", "references")
        self.link("C-OBJECTION", "C-BASIS", "contradicts")
        self.apply("mark_review", {"id": "C-BASIS", "reason": "new counterexample"})
        state = self.state()
        self.assertEqual({name for name, record in state["records"].items()
                          if record.get("review_needed")},
                         {"C-BASIS", "C-MIDDLE", "D-ACTIVE"})
        self.assertEqual(self.active_ids(), {"D-ACTIVE"})
        self.assertEqual(state["records"]["D-ACTIVE"]["decision_state"], "active")
        working = memory.build_working_set(self.store, PROJECT)
        self.assertIn({"from": "C-OBJECTION", "to": "C-BASIS", "kind": "contradicts"}, working["relations"])
        self.assertEqual(set(working["review_needed"]), {"C-BASIS", "C-MIDDLE", "D-ACTIVE"})

    def test_resolution_is_explicit_and_anchor_can_close_without_inventing_cause(self):
        source = self.source("S-OBS", "One observation; cause unknown")
        self.record("C-CAUSE", refs=[source], assessment="unverified")
        self.record("R-SYMPTOM", "review-anchor", [source])
        self.apply("mark_review", {"id": "C-CAUSE", "reason": "investigate observation"})
        self.apply("mark_review", {"id": "R-SYMPTOM", "reason": "check the symptom"})
        payload = {"id": "R-SYMPTOM", "reason": "symptom no longer occurs",
                   "source_refs": [source], "anchor_state": "closed"}
        self.rejected(self.operation("resolve_review", payload, actor="agent-A"))
        self.apply("resolve_review", payload, actor=EDITOR)
        state = self.state()
        self.assertEqual(state["records"]["R-SYMPTOM"]["anchor_state"], "closed")
        self.assertEqual(state["records"]["C-CAUSE"]["assessment"], "unverified")
        self.assertTrue(state["records"]["C-CAUSE"]["review_needed"])
        self.assertEqual(memory.build_working_set(self.store, PROJECT)["open_anchors"], [])
        self.apply("resolve_review", {
            "id": "R-SYMPTOM", "reason": "symptom recurred", "source_refs": [source],
            "anchor_state": "open",
        }, actor=EDITOR)
        self.assertEqual([r["id"] for r in memory.build_working_set(self.store, PROJECT)["open_anchors"]], ["R-SYMPTOM"])
        self.apply("resolve_review", {
            "id": "C-CAUSE", "reason": "explicit review; remains unverified",
            "source_refs": [source], "assessment": "unverified",
        }, actor=EDITOR)
        self.assertFalse(self.state()["records"]["C-CAUSE"]["review_needed"])

    def test_changed_claim_assessment_marks_dependents_without_revoking_decision(self):
        source = self.source("S-ORIGINAL", "Initial test supports the claim")
        self.record("C-BASIS", refs=[source], assessment="supported")
        self.record("D-ACTIVE", "decision", [source])
        self.accept("D-ACTIVE", source)
        self.link("D-ACTIVE", "C-BASIS")
        counterexample = self.source("S-COUNTEREXAMPLE", "Independent negative test")
        self.apply("resolve_review", {
            "id": "C-BASIS", "reason": "counterexample invalidates the technical claim",
            "source_refs": [counterexample], "assessment": "refuted",
        }, actor=EDITOR)
        state = self.state()
        self.assertEqual(state["records"]["C-BASIS"]["assessment"], "refuted")
        self.assertTrue(state["records"]["D-ACTIVE"]["review_needed"])
        self.assertEqual(state["records"]["D-ACTIVE"]["decision_state"], "active")
        self.assertEqual(self.active_ids(), {"D-ACTIVE"})

    def test_new_dependency_inherits_already_known_need_for_review(self):
        source = self.source()
        self.record("C-BASIS", refs=[source])
        self.apply("mark_review", {"id": "C-BASIS", "reason": "unresolved counterexample"})
        self.record("C-LATE", refs=[source])
        self.record("C-TOPIC", refs=[source])
        self.link("C-LATE", "C-BASIS")
        self.link("C-TOPIC", "C-BASIS", "references")
        self.assertTrue(self.state()["records"]["C-LATE"]["review_needed"])
        self.assertFalse(self.state()["records"]["C-TOPIC"]["review_needed"])

    def test_new_claim_pinned_to_withdrawn_evidence_needs_review_immediately(self):
        source = self.source("S-WITHDRAWN", "Preserved historical evidence")
        self.apply("withdraw_source", dict(source, reason="source retracted"), actor=EDITOR)
        self.record("C-LATE", refs=[source], assessment="unverified")
        record = self.state()["records"]["C-LATE"]
        self.assertEqual(record["source_refs"], [source])
        self.assertTrue(record["review_needed"])
        self.assertIn("C-LATE", memory.build_working_set(self.store, PROJECT)["review_needed"])

    def test_closed_anchor_remains_visible_as_a_referenced_historical_record(self):
        source = self.source()
        self.record("R-CLOSED", "review-anchor", [source])
        self.apply("resolve_review", {
            "id": "R-CLOSED", "reason": "symptom resolved; reopen if it recurs",
            "source_refs": [source], "anchor_state": "closed",
        }, actor=EDITOR)
        self.record("D-ACTIVE", "decision", [source])
        self.accept("D-ACTIVE", source)
        self.link("D-ACTIVE", "R-CLOSED")
        working = memory.build_working_set(self.store, PROJECT)
        self.assertEqual(working["open_anchors"], [])
        self.assertEqual([r["id"] for r in working["closed_anchors"]], ["R-CLOSED"])
        self.assertEqual(working["closed_anchors"][0]["anchor_state"], "closed")
        self.assertEqual(working["closed_anchors"][0]["source_refs"], [source])
        self.assertIn({"from": "D-ACTIVE", "to": "R-CLOSED", "kind": "depends_on"},
                      working["relations"])
        self.assertEqual(self.active_ids(), {"D-ACTIVE"})

    def test_withdrawn_authority_never_looks_fresh_and_cannot_clear_evidence_review(self):
        source = self.source()
        self.record("D-PROPOSED", "decision", [source])
        self.record("C-REVIEW", refs=[source])
        self.apply("withdraw_source", dict(source, reason="withdrawn evidence"), actor=EDITOR)
        before = self.journal()
        try:
            self.accept("D-PROPOSED", source)
        except ValueError:
            self.assertEqual(self.journal(), before)
        else:
            decision = self.state()["records"]["D-PROPOSED"]
            self.assertEqual(decision["decision_state"], "active")
            self.assertTrue(decision["review_needed"])
            self.assertIn("D-PROPOSED", memory.build_working_set(self.store, PROJECT)["review_needed"])
        self.rejected(self.operation("resolve_review", {
            "id": "C-REVIEW", "reason": "attempt to reuse the withdrawn evidence",
            "source_refs": [source], "assessment": "supported",
        }, actor=EDITOR))
        self.assertTrue(self.state()["records"]["C-REVIEW"]["review_needed"])

    def test_available_historical_version_can_support_explicitly_scoped_review(self):
        first = self.source("S-VERSIONED", "Version one describes the old configuration")
        self.record("C-HISTORY", refs=[first], text="Claim about the old configuration")
        self.source("S-VERSIONED", "Version two describes a new configuration")
        self.assertTrue(self.state()["records"]["C-HISTORY"]["review_needed"])
        self.apply("resolve_review", {
            "id": "C-HISTORY", "assessment": "supported", "source_refs": [first],
            "reason": "This historical claim concerns version one; the newer configuration is out of its scope",
        }, actor=EDITOR)
        record = self.state()["records"]["C-HISTORY"]
        self.assertFalse(record["review_needed"])
        self.assertEqual(record["assessment"], "supported")
        self.assertEqual(record["source_refs"], [first])
        self.assertEqual(self.state()["records"]["S-VERSIONED"]["versions"][0]["availability"], "available")

    def test_withdrawal_keeps_bytes_and_requires_review_but_not_policy_revocation(self):
        source = self.source("S-EVIDENCE", "Evidence retained in historical version")
        self.record("C1", refs=[source])
        self.record("D1", "decision", [source])
        self.accept("D1", source)
        content = self.state()["records"]["S-EVIDENCE"]["versions"][0]["content"]
        self.rejected(self.operation("withdraw_source", dict(source, reason="retracted")))
        self.apply("withdraw_source", dict(source, reason="retracted"), actor=EDITOR)
        version = self.state()["records"]["S-EVIDENCE"]["versions"][0]
        self.assertEqual(version["availability"], "withdrawn")
        self.assertEqual(version["content"], content)
        self.assertTrue(self.state()["records"]["C1"]["review_needed"])
        self.assertTrue(self.state()["records"]["D1"]["review_needed"])
        self.assertEqual(self.active_ids(), {"D1"})
        working_source = next(s for s in memory.build_working_set(self.store, PROJECT)["sources"] if s["id"] == "S-EVIDENCE")
        self.assertEqual(working_source["versions"][0]["availability"], "withdrawn")

    def test_renaming_preserves_identity_version_bytes_and_pinned_links(self):
        source = self.source()
        self.record("C1", refs=[source])
        original = copy.deepcopy(self.state()["records"]["S0"]["versions"])
        payload = {"id": "S0", "locator": "moved/new-name.md"}
        self.rejected(self.operation("rename_source", payload))
        self.apply("rename_source", payload, actor=EDITOR)
        state = self.state()
        self.assertEqual(state["records"]["S0"]["locator"], "moved/new-name.md")
        self.assertEqual(state["records"]["S0"]["versions"], original)
        self.assertEqual(state["records"]["C1"]["source_refs"], [source])

    def test_materialized_view_is_valid_then_stale_and_rebuildable(self):
        source = self.source()
        self.record("D1", "decision", [source])
        self.accept("D1", source)
        before = self.journal()
        memory.materialize(self.store)
        self.assertIsInstance(memory.validate_view(self.store), dict)
        self.assertEqual(self.journal(), before)
        first_json = (self.store / "CURRENT.json").read_bytes()
        memory.materialize(self.store)
        self.assertEqual((self.store / "CURRENT.json").read_bytes(), first_json)
        self.record("R1", "review-anchor", [source])
        after_operation = self.journal()
        with self.assertRaises(ValueError):
            memory.validate_view(self.store)
        self.assertEqual(self.journal(), after_operation)
        memory.materialize(self.store)
        memory.validate_view(self.store)
        self.assertNotEqual((self.store / "CURRENT.json").read_bytes(), first_json)
        self.assertEqual(self.journal(), after_operation)

    def test_modified_json_markdown_and_ahead_view_are_rejected(self):
        self.source()
        for target, modifier in [
            ("CURRENT.json", lambda text: text.replace("project-main", "project-evil", 1)),
            ("CURRENT.md", lambda text: text + "\nUnauthorized extra policy\n"),
            ("CURRENT.json", lambda text: json.dumps(dict(json.loads(text), revision=999999))),
        ]:
            with self.subTest(target=target):
                memory.materialize(self.store)
                path = self.store / target
                path.write_text(modifier(path.read_text(encoding="utf-8")), encoding="utf-8")
                before = self.journal()
                with self.assertRaises(ValueError):
                    memory.validate_view(self.store)
                self.assertEqual(self.journal(), before)

    def test_projection_is_complete_source_pinned_and_has_real_provenance(self):
        source = self.source()
        for index in range(15):
            name = "D-%02d" % index
            self.record(name, "decision", [source])
            self.accept(name, source)
        before = self.journal()
        working = memory.build_working_set(self.store, PROJECT, CONTEXT)
        repeated = memory.build_working_set(self.store, PROJECT, CONTEXT)
        self.assertEqual(working, repeated)
        self.assertEqual(len(working["active_decisions"]), 15)
        self.assertTrue(all(r["source_refs"] == [source] for r in working["active_decisions"]))
        self.assertEqual(working["journal_head"], self.state()["head"])
        self.assertEqual(working["context_content_id"], CONTEXT)
        self.assertNotEqual(working["content_id"], CONTEXT)
        provenance = json.dumps(working["provenance"], sort_keys=True)
        self.assertIn(hashlib.sha256(before).hexdigest(), provenance)
        self.assertIn(hashlib.sha256((ROOT / "scripts/project_memory.py").read_bytes()).hexdigest(), provenance)
        self.assertEqual(self.journal(), before)
        with self.assertRaises(ValueError):
            memory.build_working_set(self.store, "other-project")

    def test_search_is_literal_casefold_preserves_status_and_does_not_write(self):
        source = self.source()
        self.record("D-OLD", "decision", [source], key="search", text="CLOUD [A.*] search")
        self.accept("D-OLD", source)
        self.record("D-NEW", "decision", [source], key="search", text="Local search")
        self.accept("D-NEW", source, ["D-OLD"])
        self.record("C-RU", refs=[source], text="ПАМЯТЬ для проектов")
        before = self.journal()
        records = memory.search_records(self.store, PROJECT, "cloud [a.*]")
        self.assertEqual([r["id"] for r in records], ["D-OLD"])
        self.assertEqual(records[0]["decision_state"], "superseded")
        self.assertEqual([r["id"] for r in memory.search_records(self.store, PROJECT, "память")], ["C-RU"])
        self.assertEqual(memory.search_records(self.store, PROJECT, ".*"), records)
        self.assertEqual(memory.search_records(self.store, PROJECT, "not-present-xyz"), [])
        with self.assertRaises(ValueError):
            memory.search_records(self.store, "other-project", "search")
        self.assertEqual(self.journal(), before)

    def test_copied_ledger_replays_without_views_checkpoints_or_original_working_path(self):
        source = self.source()
        self.record("D1", "decision", [source])
        self.accept("D1", source)
        self.record("R1", "review-anchor", [source])
        self.save_checkpoint()
        memory.materialize(self.store)
        copied = self.base / "copied"
        copied.mkdir()
        shutil.copyfile(self.store / "journal.jsonl", copied / "journal.jsonl")
        self.assertEqual(memory.read_store(copied), self.state())
        self.assertEqual(memory.build_working_set(copied, PROJECT)["active_decisions"],
                         memory.build_working_set(self.store, PROJECT)["active_decisions"])
        memory.materialize(copied)
        memory.validate_view(copied)
        self.assertFalse((copied / "checkpoints" / "reader-B.json").exists())

    def test_hash_tamper_malformed_or_truncated_tail_never_silently_replays(self):
        self.source("S1", "Original evidence")
        valid = self.journal()
        damaged_cases = [
            valid.replace(b"Original evidence", b"Modified evidence", 1),
            valid + b'{"incomplete":',
            valid + b"not-json\n",
            valid[:-7],
        ]
        path = self.store / "journal.jsonl"
        for damaged in damaged_cases:
            with self.subTest(tail=damaged[-50:]):
                path.write_bytes(damaged)
                with self.assertRaises(ValueError):
                    memory.read_store(self.store)
                self.assertEqual(path.read_bytes(), damaged)
        path.write_bytes(valid)
        self.assertEqual(self.state()["records"]["S1"]["versions"][0]["content"], "Original evidence")

    @staticmethod
    def rehash_fixture(events):
        """Encode the documented journal wire format, without core helpers.

        This deliberately allows semantically invalid fixtures: a valid digest
        must not be treated as proof that an operation was valid.
        """
        def canonical(value):
            return json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8")

        previous = "0" * 64
        lines = []
        for event in copy.deepcopy(events):
            event["previous_digest"] = previous
            event.pop("digest", None)
            event["digest"] = hashlib.sha256(canonical(event)).hexdigest()
            previous = event["digest"]
            lines.append(canonical(event) + b"\n")
        return b"".join(lines)

    def test_replay_rejects_rehashed_cycle_and_dangling_target(self):
        for name in ["A", "B", "C"]:
            self.record(name)
        self.link("A", "B")
        self.link("B", "C")
        self.link("C", "A", "references")
        original = self.journal()
        events = [json.loads(line) for line in original.splitlines()]
        self.assertEqual(self.rehash_fixture(events), original)
        invalid_payloads = [
            {"from": "C", "to": "A", "kind": "depends_on"},
            {"from": "C", "to": "missing", "kind": "references"},
        ]
        path = self.store / "journal.jsonl"
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                altered = copy.deepcopy(events)
                altered[-1]["operation"]["payload"] = payload
                data = self.rehash_fixture(altered)
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    memory.read_store(self.store)
                self.assertEqual(path.read_bytes(), data)
        path.write_bytes(original)
        self.assertEqual(len(self.state()["relations"]), 3)

    def test_replay_rejects_rehashed_unknown_schema_and_invalid_numeric_types(self):
        self.source()
        original = self.journal()
        events = [json.loads(line) for line in original.splitlines()]
        mutations = [
            (0, "schema_version", "unsupported-99"),
            (0, "revision", True),
            (-1, "revision", str(events[-1]["revision"])),
        ]
        path = self.store / "journal.jsonl"
        for index, key, value in mutations:
            with self.subTest(index=index, key=key, value=value):
                altered = copy.deepcopy(events)
                altered[index][key] = value
                data = self.rehash_fixture(altered)
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    memory.read_store(self.store)
                self.assertEqual(path.read_bytes(), data)
        path.write_bytes(original)
        self.assertIn("S0", self.state()["records"])

    def test_checkpoint_records_current_obligations_without_staling_knowledge(self):
        source = self.source()
        self.record("D1", "decision", [source])
        self.accept("D1", source)
        self.record("R1", "review-anchor", [source])
        before = self.journal()
        checkpoint = self.save_checkpoint()
        self.assertEqual(self.journal(), before)
        self.assertEqual(checkpoint["project_id"], PROJECT)
        self.assertEqual(checkpoint["participant_id"], "reader-B")
        self.assertEqual(checkpoint["memory_head"], self.state()["head"])
        self.assertEqual(checkpoint["memory_revision"], self.state()["revision"])
        self.assertEqual(set(checkpoint["active_decision_ids"]), {"D1"})
        self.assertEqual(set(checkpoint["open_anchor_ids"]), {"R1"})
        self.assertEqual(checkpoint["context_content_id"], CONTEXT)
        self.assertTrue(checkpoint["memory_content_id"])
        resume = memory.check_resume(self.store, "reader-B", CONTEXT)
        self.assertEqual(resume["status"], "fresh")
        self.assertEqual(resume["reasons"], [])

    def test_new_projection_module_requires_checkpoint_reconciliation(self):
        """A matching knowledge head alone does not cover changed projection code."""
        module_path = self.base / "copied_project_memory.py"
        module_path.write_bytes((ROOT / "scripts/project_memory.py").read_bytes())

        def load_copy(name):
            spec = importlib.util.spec_from_file_location(name, module_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            self.addCleanup(sys.modules.pop, name, None)
            spec.loader.exec_module(module)
            return module

        first = load_copy("project_memory_checkpoint_version_one")
        first.save_checkpoint(
            self.store, "reader-code", CONTEXT, "Review", "Continue", self.state()["head"]
        )
        self.assertEqual(first.check_resume(self.store, "reader-code", CONTEXT)["status"], "fresh")
        original_journal = self.journal()
        module_path.write_bytes(module_path.read_bytes() + b"\n# Revised projection build identity.\n")
        second = load_copy("project_memory_checkpoint_version_two")
        result = second.check_resume(self.store, "reader-code", CONTEXT)
        self.assertEqual(result["status"], "stale")
        self.assertTrue(result["reasons"])
        self.assertEqual(self.journal(), original_journal)

    def test_changed_head_context_and_project_are_not_fresh_checkpoint(self):
        self.save_checkpoint()
        self.source()
        changed = memory.check_resume(self.store, "reader-B", CONTEXT)
        self.assertEqual(changed["status"], "stale")
        self.assertTrue(changed["reasons"])
        self.save_checkpoint()
        changed = memory.check_resume(self.store, "reader-B", OTHER_CONTEXT)
        self.assertEqual(changed["status"], "stale")
        self.assertTrue(changed["reasons"])
        self.save_checkpoint(context_content_id=OTHER_CONTEXT)
        self.assertEqual(memory.check_resume(self.store, "reader-B", OTHER_CONTEXT)["status"], "fresh")
        path = self.store / "checkpoints" / "reader-B.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["project_id"] = "other-project"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(memory.check_resume(self.store, "reader-B", OTHER_CONTEXT)["status"], "stale")

    def test_checkpoint_stale_save_and_bad_receipts_preserve_existing_file(self):
        self.save_checkpoint()
        path = self.store / "checkpoints" / "reader-B.json"
        old_checkpoint = path.read_bytes()
        old_head = self.state()["head"]
        self.source()
        before = self.journal()
        with self.assertRaises(ValueError):
            self.save_checkpoint(expected_head=old_head)
        self.assertEqual(path.read_bytes(), old_checkpoint)
        self.assertEqual(self.journal(), before)
        invalid = [
            [{"report_id": "report-1", "status": "applied", "consequence": "ready"}],
            [{"report_id": "report-1", "status": "read", "consequence": "review"}] * 2,
            [{"report_id": "report-1", "status": "published", "consequence": "review"}],
        ]
        for receipts in invalid:
            with self.subTest(receipts=receipts):
                with self.assertRaises(ValueError):
                    self.save_checkpoint(receipts=receipts)
                self.assertEqual(path.read_bytes(), old_checkpoint)
                self.assertEqual(self.journal(), before)

    def test_receipts_merge_keep_testimony_and_never_downgrade_applied_to_read(self):
        read = {"report_id": "report-1", "status": "read", "consequence": "Review H2"}
        self.save_checkpoint(receipts=[read])
        applied = dict(read, status="applied", result="Updated local working set",
                       verification="Checked active IDs and preserved source versions")
        self.save_checkpoint(receipts=[applied])
        self.save_checkpoint(receipts=None)
        checkpoint = self.save_checkpoint(receipts=[{
            "report_id": "report-2", "status": "read", "consequence": "Consider the new signal",
        }])
        receipts = {r["report_id"]: r for r in checkpoint["receipts"]}
        self.assertEqual(set(receipts), {"report-1", "report-2"})
        self.assertEqual(receipts["report-1"], applied)
        self.assertEqual(self.save_checkpoint(receipts=[])["receipts"], checkpoint["receipts"])
        path = self.store / "checkpoints" / "reader-B.json"
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.save_checkpoint(receipts=[read])
        self.assertEqual(path.read_bytes(), before)

    def test_missing_or_malformed_checkpoint_is_explicit_error(self):
        with self.assertRaises(ValueError):
            memory.check_resume(self.store, "missing", CONTEXT)
        self.save_checkpoint()
        path = self.store / "checkpoints" / "reader-B.json"
        for malformed in ['{"incomplete":', '{}', '[]']:
            with self.subTest(malformed=malformed):
                path.write_text(malformed, encoding="utf-8")
                with self.assertRaises(ValueError):
                    memory.check_resume(self.store, "reader-B", CONTEXT)

    def test_participant_path_traversal_and_checkpoint_symlink_are_rejected(self):
        before = self.journal()
        for participant in ["../escape", "/absolute", ".", "..", "nested/path", ""]:
            with self.subTest(participant=participant):
                with self.assertRaises(ValueError):
                    self.save_checkpoint(participant_id=participant)
        self.save_checkpoint()
        path = self.store / "checkpoints" / "reader-B.json"
        path.unlink()
        target = self.base / "outside-checkpoint.json"
        target.write_text("outside sentinel", encoding="utf-8")
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.save_checkpoint()
        with self.assertRaises(ValueError):
            memory.check_resume(self.store, "reader-B", CONTEXT)
        self.assertEqual(target.read_text(encoding="utf-8"), "outside sentinel")
        self.assertEqual(self.journal(), before)

    def test_journal_symlink_is_rejected_without_touching_target(self):
        target = self.base / "external-journal.jsonl"
        original = self.journal()
        target.write_bytes(original)
        journal = self.store / "journal.jsonl"
        journal.unlink()
        journal.symlink_to(target)
        with self.assertRaises(ValueError):
            memory.read_store(self.store)
        self.assertEqual(target.read_bytes(), original)

    def test_reinitialization_cannot_silently_replace_editor_or_project(self):
        self.source()
        before = self.journal()
        with self.assertRaises(ValueError):
            memory.init_store(self.store, "other-project", "other-owner")
        self.assertEqual(self.journal(), before)
        self.assertEqual(self.state()["editor"], EDITOR)
        self.assertEqual(self.state()["project_id"], PROJECT)

    def test_concurrent_writers_same_revision_produce_one_successful_change(self):
        payloads = [{
            "id": name, "source_key": name, "content": "Concurrent " + name,
            "locator": name + ".md", "author_ref": EDITOR,
        } for name in ["S-A", "S-B"]]
        operations = [self.operation("import_source", payload) for payload in payloads]
        start = threading.Barrier(2)

        def writer(operation):
            start.wait(timeout=10)
            try:
                return "ok", memory.apply_operation(self.store, operation)
            except ValueError as error:
                return "rejected", str(error)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(writer, operations))
        self.assertEqual(sorted(status for status, _ in results), ["ok", "rejected"])
        state = self.state()
        self.assertEqual(len(state["records"]), 1)
        self.assertIn(next(iter(state["records"])), {"S-A", "S-B"})
        self.assertEqual(state["revision"], operations[0]["expected_revision"] + 1)


if __name__ == "__main__":
    unittest.main()
