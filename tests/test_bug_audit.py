import sys
import json
import subprocess
import tempfile
import unittest
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outputs" / "SQA_MVP"))

from store import Problem, Store
from bug_audit import BugAudit
from app import PilotServer


class BugAuditStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.audit = BugAudit(self.store)
        self.window = {
            "start": "2026-11-24T00:00:00+08:00",
            "end": "2026-11-24T19:30:00+08:00",
        }
        self.run = self.audit.begin_run(
            "2026-11-24:scope-a:v1", self.window, {"project": "Suunto work"}, "v1"
        )

    def test_window_normalizes_to_shanghai_and_excludes_cutoff(self):
        result = self.audit.record_evidence(self.run["id"], "evidence-1", self.payload({
            "scope_status": "complete",
            "items": [
                self.history_bug("start", "2026-11-23T16:00:00Z", "New", "Open"),
                self.history_bug("edge", "2026-11-24T10:30:00Z", "New", "Open"),
                self.history_bug("cutoff", "2026-11-24T11:30:00+00:00", "New", "Open"),
            ],
        }))
        ids = {item["bug_id"] for item in self.audit.get_run(self.run["id"])["items"]}
        self.assertEqual(ids, {"start", "edge"})
        self.assertEqual(result["candidate_count"], 2)

    def test_scope_summary_rejects_credentials_and_local_paths(self):
        with self.assertRaises(Problem):
            self.audit.begin_run("unsafe", self.window, {"project": "Suunto work", "token": "secret"}, "v1")
        with self.assertRaises(Problem):
            self.audit.begin_run("unsafe-path", self.window, {"project": "Suunto work", "path": "C:/private"}, "v1")

    def test_fractional_cutoff_preserves_membership_and_evidence_timestamp(self):
        run = self.audit.begin_run("fractional", {**self.window, "end": "2026-11-24T19:30:00.500+08:00"}, {}, "v1")
        self.audit.record_evidence(run["id"], "fractional-evidence", self.payload({"scope_status": "complete", "items": [
            self.history_bug("inside", "2026-11-24T19:30:00.250+08:00", "New", "Open"),
            self.history_bug("cutoff", "2026-11-24T19:30:00.500+08:00", "New", "Open"),
        ]}))
        detail = self.audit.get_run(run["id"])
        self.assertEqual([item["bug_id"] for item in detail["items"]], ["inside"])
        self.assertEqual(detail["window"]["end"], "2026-11-24T19:30:00.500000+08:00")
        self.assertEqual(detail["items"][0]["history"][0]["changed_at"], "2026-11-24T19:30:00.250000+08:00")

    def test_one_history_can_preserve_both_state_and_severity_changes(self):
        item = self.history_bug("two-fields", "2026-11-24T08:00:00+08:00", "New", "Open")
        item["history"].append({**item["history"][0], "field": "Severity", "old_value": "Major", "new_value": "Critical"})
        payload = self.payload({"scope_status": "complete", "items": [item]})
        self.audit.record_evidence(self.run["id"], "both-fields", payload)
        self.audit.record_evidence(self.run["id"], "both-fields-replayed", payload)
        detail = self.audit.get_run(self.run["id"])
        self.assertEqual(len(detail["items"]), 1)
        self.assertEqual({(h["field"], h["old_value"], h["new_value"]) for h in detail["items"][0]["history"]},
                         {("State", "New", "Open"), ("Severity", "Major", "Critical")})

    def test_daily_window_must_start_at_shanghai_midnight(self):
        with self.assertRaises(Problem):
            self.audit.begin_run("midday", {**self.window, "start": "2026-11-24T12:00:00+08:00"},
                                 {"project": "Suunto work"}, "v1")

    def test_replay_is_idempotent_for_bug_history_and_event(self):
        payload = self.payload({"scope_status": "complete", "items": [self.history_bug("42", "2026-11-24T08:00:00+08:00", "New", "Open")]})
        first = self.audit.record_evidence(self.run["id"], "evidence-same", payload)
        second = self.audit.record_evidence(self.run["id"], "evidence-same", payload)
        self.assertEqual(first, second)
        self.assertEqual(first["candidate_count"], 1)
        self.assertEqual(len(self.audit.get_run(self.run["id"])["items"]), 1)

    def test_candidate_requires_real_state_or_severity_change(self):
        records = [
            self.history_bug("add", "2026-11-24T08:00:00+08:00", None, "New", kind="Add"),
            self.history_bug("other", "2026-11-24T08:00:00+08:00", "a", "b", field="Developer"),
            self.history_bug("same", "2026-11-24T08:00:00+08:00", "New", "New"),
            self.history_bug("state", "2026-11-24T08:00:00+08:00", "New", "Open"),
            self.history_bug("severity", "2026-11-24T08:00:00+08:00", "Major", "Critical", field="Severity"),
        ]
        self.audit.record_evidence(self.run["id"], "evidence-filter", self.payload({"scope_status": "complete", "items": records}))
        items = self.audit.get_run(self.run["id"])["items"]
        self.assertEqual({row["bug_id"] for row in items}, {"state", "severity"})

    def test_same_run_is_resumed_and_partial_evidence_is_visible(self):
        resumed = self.audit.begin_run(
            "2026-11-24:scope-a:v1", self.window, {"project": "Suunto work"}, "v1"
        )
        self.assertEqual(resumed["id"], self.run["id"])
        self.audit.record_evidence(self.run["id"], "partial", self.payload({"scope_status": "partial", "items": []}))
        detail = self.audit.get_run(self.run["id"])
        self.assertEqual(detail["collection_status"], "partial")

    def test_same_day_retry_preserves_the_original_cutoff(self):
        resumed = self.audit.begin_run("2026-11-24:scope-a:v1",
                                      {**self.window, "end": "2026-11-24T20:00:00+08:00"},
                                      {"project": "Suunto work"}, "v1")
        self.assertEqual(resumed["id"], self.run["id"])
        self.assertEqual(resumed["window"]["end"], "2026-11-24T19:30:00+08:00")
        result = self.audit.record_evidence(resumed["id"], "e-after-original-cutoff", self.payload({
            "scope_status": "complete", "items": [self.history_bug("late", "2026-11-24T19:45:00+08:00", "New", "Open")]
        }))
        self.assertEqual(result["candidate_count"], 0)

    def test_failed_scope_collection_is_not_downgraded_to_partial(self):
        self.audit.record_evidence(self.run["id"], "evidence-failed", self.payload({
            "scope_status": "failed", "items": []
        }))
        self.assertEqual(self.audit.get_run(self.run["id"])["collection_status"], "failed")
        result = self.audit.complete_run(self.run["id"], "complete-failed", self.payload({}))
        self.assertEqual(result["status"], "failed")

    def test_missing_history_completeness_and_missing_field_findings_remain_partial(self):
        self.audit.record_evidence(self.run["id"], "evidence-no-history-status", self.payload({
            "scope_status": "complete", "items": [{**self.history_bug("8", "2026-11-24T08:00:00+08:00", "New", "Open"), "evidence": {}}]
        }))
        self.assertEqual(self.audit.get_run(self.run["id"])["collection_status"], "partial")
        self.audit.record_findings(self.run["id"], "findings-incomplete", self.payload({"items": [{
            "bug_id": "8", "fields": [{"name": "Bug Standard Review of Testing", "original_value": "Pending",
                                         "proposed_value": None, "ready": False, "reason": "evidence missing"}]
        }]}))
        self.assertEqual(self.audit.get_run(self.run["id"])["audit_status"], "partial")

    def test_field_readiness_and_preflight_conflict_are_independent(self):
        self.audit.record_evidence(self.run["id"], "evidence-fields", self.payload({
            "scope_status": "complete", "items": [self.history_bug("7", "2026-11-24T08:00:00+08:00", "New", "Open")]
        }))
        self.audit.record_findings(self.run["id"], "findings-fields", self.payload({"items": [{
            "bug_id": "7", "fields": [
                {"name": "Bug Standard Review of Testing", "original_value": "Pending", "proposed_value": "Pass", "ready": True},
                {"name": "Bug Standard Review of Development", "original_value": "Pending", "proposed_value": None, "ready": False, "reason": "missing evidence"},
                {"name": "Bug quality audit", "original_value": "Pending", "proposed_value": "Pass", "ready": True},
            ]
        }]}))
        result = self.audit.record_writeback(self.run["id"], "writeback-fields", self.payload({"items": [{
            "bug_id": "7", "fields": [
                {"name": "Bug Standard Review of Testing", "preflight_value": "Changed", "status": "written", "readback_value": "Pass"},
                {"name": "Bug quality audit", "preflight_value": "Pending", "status": "unknown", "readback_value": None},
            ]
        }]}))
        fields = {field["name"]: field for field in self.audit.get_run(self.run["id"])["items"][0]["fields"]}
        self.assertEqual(fields["Bug Standard Review of Testing"]["status"], "conflict")
        self.assertEqual(fields["Bug Standard Review of Development"]["status"], "pending")
        self.assertEqual(fields["Bug quality audit"]["status"], "unknown")
        self.assertEqual(result["conflict_count"], 1)

    def test_pending_field_is_a_complete_audit_and_unneeded_writeback_is_not_required(self):
        self.audit.record_evidence(self.run["id"], "evidence-applicable", self.payload({
            "scope_status": "complete", "items": [self.history_bug("10", "2026-11-24T08:00:00+08:00", "New", "Open")]
        }))
        self.audit.record_findings(self.run["id"], "findings-applicable", self.payload({"items": [{
            "bug_id": "10", "fields": [
                {"name": "Bug Standard Review of Testing", "original_value": "Pending", "proposed_value": "Pass", "ready": True},
                {"name": "Bug Standard Review of Development", "original_value": "Pending", "proposed_value": None, "ready": False, "reason": "evidence missing"},
                {"name": "Bug quality audit", "original_value": "Pending", "proposed_value": None, "ready": False, "reason": "evidence missing"},
            ]
        }]}))
        self.assertEqual(self.audit.get_run(self.run["id"])["audit_status"], "complete")
        self.audit.record_writeback(self.run["id"], "writeback-applicable", self.payload({"items": [{
            "bug_id": "10", "fields": [{"name": "Bug Standard Review of Testing", "preflight_value": "Pending",
                                            "status": "written", "readback_value": "Pass"}]
        }]}))
        self.assertEqual(self.audit.get_run(self.run["id"])["writeback_status"], "verified")

    def test_empty_writeback_can_complete_as_not_required(self):
        self.audit.record_evidence(self.run["id"], "evidence-empty", self.payload({"scope_status": "complete", "items": []}))
        self.audit.record_findings(self.run["id"], "findings-empty", self.payload({"items": []}))
        self.audit.record_writeback(self.run["id"], "writeback-empty", self.payload({"items": []}))
        self.assertEqual(self.audit.get_run(self.run["id"])["writeback_status"], "not_required")
        self.audit.record_notification(self.run["id"], "notification-empty", self.payload({"status": "not_required"}))
        result = self.audit.complete_run(self.run["id"], "complete-empty", self.payload({}))
        self.assertEqual(result["status"], "complete")

    def prepare_ready_fields(self):
        self.audit.record_evidence(self.run["id"], "e-ready", self.payload({
            "scope_status": "complete", "items": [self.history_bug("12", "2026-11-24T08:00:00+08:00", "New", "Open")]
        }))
        findings = self.payload({"items": [{"bug_id": "12", "fields": [
            {"name": name, "original_value": "Pending", "proposed_value": "Pass", "ready": True}
            for name in ("Bug Standard Review of Testing", "Bug Standard Review of Development", "Bug quality audit")
        ]}]})
        self.audit.record_findings(self.run["id"], "f-ready", findings)
        self.audit.record_writeback(self.run["id"], "w-one", self.payload({"items": [{"bug_id": "12", "fields": [
            {"name": "Bug Standard Review of Testing", "preflight_value": "Pending", "status": "written", "readback_value": "Pass"}
        ]}]}))
        return findings

    def test_ready_fields_without_receipts_keep_writeback_partial(self):
        self.prepare_ready_fields()
        self.assertEqual(self.audit.get_run(self.run["id"])["writeback_status"], "partial")

    def test_multiselect_order_is_irrelevant_but_audit_text_order_is_significant(self):
        self.audit.record_evidence(self.run["id"], "e-order", self.payload({"scope_status": "complete", "items": [
            self.history_bug("order", "2026-11-24T08:00:00+08:00", "New", "Open")
        ]}))
        names = ("Bug Standard Review of Testing", "Bug Standard Review of Development", "Bug quality audit")
        self.audit.record_findings(self.run["id"], "f-order", self.payload({"items": [{"bug_id": "order", "fields": [
            {"name": name, "original_value": "A,B", "proposed_value": "C,D", "ready": True} for name in names
        ]}]}))
        self.audit.record_writeback(self.run["id"], "w-order", self.payload({"items": [{"bug_id": "order", "fields": [
            {"name": name, "preflight_value": "B,A" if name != "Bug quality audit" else "A,B",
             "status": "written", "readback_value": "D,C"} for name in names
        ]}]}))
        fields = {f["name"]: f for f in self.audit.get_run(self.run["id"])["items"][0]["fields"]}
        self.assertEqual(fields["Bug Standard Review of Testing"]["status"], "verified")
        self.assertEqual(fields["Bug Standard Review of Development"]["status"], "verified")
        self.assertEqual(fields["Bug quality audit"]["status"], "failed")
        self.assertEqual(fields["Bug Standard Review of Testing"]["readback_value"], "D,C")

    def test_pending_conclusion_prevents_whole_run_completion(self):
        findings = self.prepare_ready_fields()
        for field in findings["items"][0]["fields"][1:]:
            field.update(proposed_value=None, ready=False, reason="evidence missing")
        self.audit.record_findings(self.run["id"], "f-pending", findings)
        self.audit.record_notification(self.run["id"], "n-pending", self.payload({"status": "not_required"}))
        result = self.audit.complete_run(self.run["id"], "finish-pending", self.payload({}))
        self.assertEqual(result["audit_status"], "complete")
        self.assertEqual(result["writeback_status"], "verified")
        self.assertEqual(result["status"], "partial")

    def test_new_findings_event_preserves_verified_field_receipt(self):
        findings = self.prepare_ready_fields()
        self.audit.record_findings(self.run["id"], "f-ready-again", findings)
        field = next(f for f in self.audit.get_run(self.run["id"])["items"][0]["fields"]
                     if f["name"] == "Bug Standard Review of Testing")
        self.assertEqual(field["status"], "verified")
        self.assertEqual(field["readback_value"], "Pass")

    def test_findings_cannot_replace_a_conclusion_with_recorded_side_effect(self):
        findings = self.prepare_ready_fields()
        findings["items"][0]["fields"][0]["proposed_value"] = "Fail"
        with self.assertRaises(Problem):
            self.audit.record_findings(self.run["id"], "f-changed-after-write", findings)
        detail = self.audit.get_run(self.run["id"])
        field = next(f for f in detail["items"][0]["fields"] if f["name"] == "Bug Standard Review of Testing")
        self.assertEqual(field["proposed_value"], "Pass")
        self.assertNotIn("f-changed-after-write", [e["event_id"] for e in detail["events"]])

    def test_notification_receipts_append_without_storing_unapproved_payload(self):
        self.audit.record_notification(self.run["id"], "notification-partial", self.payload({
            "status": "partial", "target": "Synthetic target", "message_id": "SIM-1",
            "attachment_status": "mismatch", "raw_comment": "must not persist",
        }))
        self.audit.record_notification(self.run["id"], "notification-verified", self.payload({
            "status": "verified", "target": "Synthetic target", "message_id": "SIM-1",
            "idempotency_key": "SIM-IDEM-1", "attachment_status": "verified",
            "summary_hash": "0123456789abcdef0123456789abcdef",
        }))
        detail = self.audit.get_run(self.run["id"])
        self.assertEqual(detail["notification_status"], "verified")
        self.assertEqual(len([event for event in detail["events"] if event["stage"] == "notification"]), 2)
        self.assertNotIn("must not persist", json.dumps(detail))

    def test_completed_run_reopens_and_reused_complete_event_reconciles_latest_stages(self):
        self.audit.record_evidence(self.run["id"], "e-empty", self.payload({"scope_status": "complete", "items": []}))
        self.audit.record_findings(self.run["id"], "f-empty", self.payload({"items": []}))
        self.audit.record_writeback(self.run["id"], "w-empty", self.payload({"items": []}))
        self.audit.record_notification(self.run["id"], "n-partial", self.payload({"status": "partial"}))
        self.assertEqual(self.audit.complete_run(self.run["id"], "finish", self.payload({}))["status"], "partial")
        self.audit.record_notification(self.run["id"], "n-recovered", self.payload({"status": "not_required"}))
        self.assertEqual(self.audit.get_run(self.run["id"])["status"], "running")
        self.assertEqual(self.audit.complete_run(self.run["id"], "finish", self.payload({}))["status"], "complete")
        self.audit.record_notification(self.run["id"], "n-correction", self.payload({"status": "failed"}))
        self.assertEqual(self.audit.get_run(self.run["id"])["status"], "running")
        self.assertEqual(self.audit.complete_run(self.run["id"], "finish", self.payload({}))["status"], "failed")

    def test_new_candidate_cannot_reuse_previous_completed_audit_status(self):
        self.audit.record_evidence(self.run["id"], "e-empty", self.payload({"scope_status": "complete", "items": []}))
        self.audit.record_findings(self.run["id"], "f-empty", self.payload({"items": []}))
        self.audit.record_writeback(self.run["id"], "w-empty", self.payload({"items": []}))
        self.audit.record_notification(self.run["id"], "n-empty", self.payload({"status": "not_required"}))
        self.assertEqual(self.audit.complete_run(self.run["id"], "finish", self.payload({}))["status"], "complete")
        self.audit.record_evidence(self.run["id"], "e-corrected", self.payload({"scope_status": "complete", "items": [
            self.history_bug("new", "2026-11-24T08:00:00+08:00", "New", "Open")
        ]}))
        self.assertEqual(self.audit.get_run(self.run["id"])["audit_status"], "partial")
        self.assertEqual(self.audit.complete_run(self.run["id"], "finish", self.payload({}))["status"], "partial")

    def test_verified_notification_requires_receipt_and_attachment_verification(self):
        with self.assertRaises(Problem):
            self.audit.record_notification(self.run["id"], "notification-unverified", self.payload({
                "status": "verified", "target": "AQS",
            }))

    def test_pending_field_requires_a_reason(self):
        self.audit.record_evidence(self.run["id"], "evidence-pending-reason", self.payload({
            "scope_status": "complete", "items": [self.history_bug("11", "2026-11-24T08:00:00+08:00", "New", "Open")]
        }))
        with self.assertRaises(Problem):
            self.audit.record_findings(self.run["id"], "findings-missing-reason", self.payload({"items": [{
                "bug_id": "11", "fields": [{"name": "Bug Standard Review of Testing", "original_value": "Pending",
                                               "proposed_value": None, "ready": False}]
            }]}))

    @staticmethod
    def payload(data):
        return {"schema_version": 1, **data}

    @staticmethod
    def history_bug(bug_id, timestamp, old_value, new_value, field="State", kind="Change"):
        return {
            "bug_id": bug_id,
            "snapshot": {"title": "simulated"},
            "history": [{
                "id": "h-" + bug_id,
                "kind": kind,
                "changed_at": timestamp,
                "field": field,
                "old_value": old_value,
                "new_value": new_value,
            }],
            "evidence": {"history": "complete", "comments": "not_requested"},
        }


class BugAuditCliTests(unittest.TestCase):
    def test_begin_works_without_optional_timezone_database(self):
        script = '''
import json, sys, tempfile, zoneinfo
sys.path.insert(0, sys.argv[1])
zoneinfo.reset_tzpath([])
zoneinfo.ZoneInfo.clear_cache()
sys.modules["tzdata"] = None
from store import Store
from bug_audit import BugAudit
with tempfile.TemporaryDirectory() as data_dir:
    run = BugAudit(Store(data_dir)).begin_run("portable:v1", {
        "start": "2026-11-23T16:00:00Z", "end": "2026-11-24T11:30:00Z"
    }, {}, "v1")
    print(json.dumps(run["window"]))
'''
        result = subprocess.run([sys.executable, "-S", "-c", script, str(ROOT / "outputs" / "SQA_MVP")],
                                text=True, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"start": "2026-11-24T00:00:00+08:00", "end": "2026-11-24T19:30:00+08:00"})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "data"
        self.input_file = self.root / "input.json"

    def invoke(self, command, payload, *extra):
        self.input_file.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "outputs" / "SQA_MVP" / "bug_audit_cli.py"),
             "--data-dir", str(self.data_dir), command, "--input", str(self.input_file), *extra],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )

    def test_cli_begin_and_replay_evidence_without_external_access(self):
        started = self.invoke("begin", {
            "schema_version": 1, "run_key": "2026-11-24:scope:v1",
            "window": {"start": "2026-11-24T00:00:00+08:00", "end": "2026-11-24T19:30:00+08:00"},
            "scope": {"project": "simulated"}, "rule_version": "v1",
        })
        self.assertEqual(started.returncode, 0, started.stderr)
        run_id = json.loads(started.stdout)["id"]
        payload = {"schema_version": 1, "scope_status": "complete", "items": []}
        first = self.invoke("record-evidence", payload, "--run-id", str(run_id), "--event-id", "e1")
        replay = self.invoke("record-evidence", payload, "--run-id", str(run_id), "--event-id", "e1")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(json.loads(first.stdout), json.loads(replay.stdout))

    def test_cli_rejects_bad_schema_and_out_of_scope_field_without_echoing_payload(self):
        bad_schema = self.invoke("record-evidence", {"schema_version": 2, "secret": "do-not-echo"},
                                 "--run-id", "1", "--event-id", "e2")
        self.assertNotEqual(bad_schema.returncode, 0)
        self.assertNotIn("do-not-echo", bad_schema.stderr + bad_schema.stdout)
        started = self.invoke("begin", {
            "schema_version": 1, "run_key": "2026-11-24:scope:v1",
            "window": {"start": "2026-11-24T00:00:00+08:00", "end": "2026-11-24T19:30:00+08:00"},
            "scope": {}, "rule_version": "v1",
        })
        run_id = json.loads(started.stdout)["id"]
        self.invoke("record-evidence", {"schema_version": 1, "scope_status": "complete", "items": [{
            "bug_id": "1", "history": [{"id": "h1", "kind": "Change", "changed_at": "2026-11-24T08:00:00+08:00",
                                         "field": "State", "old_value": "New", "new_value": "Open"}],
            "evidence": {"history": "complete"},
        }]}, "--run-id", str(run_id), "--event-id", "evidence-cli-test")
        bad_field = self.invoke("record-findings", {"schema_version": 1, "items": [{
            "bug_id": "1", "fields": [{"name": "State", "original_value": "Open", "proposed_value": "Closed", "ready": True}]
        }]}, "--run-id", str(run_id), "--event-id", "e3")
        self.assertNotEqual(bad_field.returncode, 0)
        self.assertNotIn("Closed", bad_field.stderr + bad_field.stdout)

    def test_full_offline_stage_replay_keeps_one_run_and_no_external_side_effect(self):
        fixture_dir = ROOT / "tests" / "fixtures" / "bug_audit"

        def load(name):
            return json.loads((fixture_dir / (name + ".json")).read_text(encoding="utf-8"))

        first_begin = self.invoke("begin", load("begin"))
        run_id = json.loads(first_begin.stdout)["id"]
        stages = [
            ("record-evidence", "evidence", "evidence-1"),
            ("record-findings", "findings", "findings-1"),
            ("record-writeback", "writeback", "writeback-1"),
            ("record-notification", "notification", "notification-1"),
            ("complete", "complete", "complete-1"),
        ]
        for command, fixture, event_id in stages:
            first = self.invoke(command, load(fixture), "--run-id", str(run_id), "--event-id", event_id)
            replay = self.invoke(command, load(fixture), "--run-id", str(run_id), "--event-id", event_id)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(json.loads(first.stdout), json.loads(replay.stdout))

        audit = BugAudit(Store(self.data_dir))
        detail = audit.get_run(run_id)
        self.assertEqual(detail["status"], "partial")
        self.assertEqual(len(detail["items"]), 1)
        self.assertEqual(len(detail["items"][0]["fields"]), 3)
        field_status = {field["name"]: field["status"] for field in detail["items"][0]["fields"]}
        self.assertEqual(field_status["Bug Standard Review of Development"], "pending")
        self.assertEqual(field_status["Bug quality audit"], "unknown")
        with Store(self.data_dir).connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bug_audit_runs").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bug_audit_events").fetchone()[0], 5)


class BugAuditApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.owner = self.store.add_user(None, {"name": "audit-owner", "password": "password-12345", "role": "owner"})
        self.reviewer = self.store.add_user(self.owner, {"name": "audit-reviewer", "password": "password-12345", "role": "reviewer"})
        self.member = self.store.add_user(self.owner, {"name": "audit-member", "password": "password-12345", "role": "member"})
        self.server = PilotServer(0, self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin = self.server.origin

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, user=None):
        headers = {"Host": self.server.host}
        if user:
            token, _ = self.server.mint(self.store.get_user(user["id"]))
            headers["Cookie"] = "sqa_session=" + token
        try:
            with urlopen(Request(self.origin + path, headers=headers), timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_bug_audit_routes_require_review_role_and_return_redacted_projection(self):
        audit = BugAudit(self.store)
        run = audit.begin_run("api-run", {"start": "2026-11-24T00:00:00+08:00", "end": "2026-11-24T19:30:00+08:00"},
                              {"project": "simulated"}, "v1")
        audit.record_evidence(run["id"], "evidence", {"schema_version": 1, "scope_status": "complete", "items": [{
            "bug_id": "9", "history": [{"id": "h9", "kind": "Change", "changed_at": "2026-11-24T08:00:00+08:00",
                                         "field": "State", "old_value": "New", "new_value": "Open"}],
            "snapshot": {"title": "safe title"}, "evidence": {"history": "complete", "comments": "complete",
                                                                  "raw_comment": "private comment text"},
        }]})
        self.assertEqual(self.request("/api/bug-audit/runs")[0], 401)
        self.assertEqual(self.request("/api/bug-audit/runs", self.member)[0], 403)
        status, runs = self.request("/api/bug-audit/runs?limit=1", self.reviewer)
        self.assertEqual(status, 200)
        self.assertEqual(runs[0]["id"], run["id"])
        status, detail = self.request("/api/bug-audit/runs/" + str(run["id"]), self.owner)
        self.assertEqual(status, 200)
        self.assertNotIn("private comment text", json.dumps(detail))
        self.assertEqual(self.request("/api/bug-audit/runs/not-an-id", self.reviewer)[0], 404)
        self.assertEqual(self.request("/api/bug-audit/runs?limit=999", self.reviewer)[0], 400)

    def test_bug_audit_http_surface_has_no_write_route(self):
        account = self.store.get_user(self.owner["id"])
        token, csrf = self.server.mint(account)
        headers = {
            "Host": self.server.host, "Cookie": "sqa_session=" + token,
            "Content-Type": "application/json", "Origin": self.origin,
            "X-SQA-Request": "pilot", "X-SQA-CSRF": csrf,
        }
        request = Request(self.origin + "/api/bug-audit/runs", data=b"{}", headers=headers, method="POST")
        try:
            with urlopen(request, timeout=3) as response:
                status = response.status
        except HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 404)

    def test_complete_cli_run_and_replay_are_visible_through_read_only_api(self):
        fixture_dir = ROOT / "tests" / "fixtures" / "bug_audit"
        payloads = {name: json.loads((fixture_dir / (name + ".json")).read_text(encoding="utf-8"))
                    for name in ("begin", "evidence", "findings", "notification", "complete")}
        payloads["evidence"]["items"][0]["evidence"]["comments"] = "complete"
        for field in payloads["findings"]["items"][0]["fields"]:
            field.update(proposed_value="Pass", ready=True, reason="Synthetic complete evidence")
        payloads["writeback"] = {"schema_version": 1, "items": [{"bug_id": "SIM-101", "fields": [
            {"name": name, "preflight_value": "Pending", "status": "written", "readback_value": "Pass"}
            for name in ("Bug Standard Review of Testing", "Bug Standard Review of Development", "Bug quality audit")
        ]}]}
        payloads["notification"].update(status="verified", attachment_status="verified")

        def invoke(command, payload_name, run_id=None):
            input_file = Path(self.temp.name) / (payload_name + ".json")
            input_file.write_text(json.dumps(payloads[payload_name]), encoding="utf-8")
            args = [sys.executable, "-X", "utf8", str(ROOT / "outputs" / "SQA_MVP" / "bug_audit_cli.py"),
                    "--data-dir", self.temp.name, command, "--input", str(input_file)]
            if run_id is not None:
                args.extend(["--run-id", str(run_id), "--event-id", payload_name + "-success"])
            result = subprocess.run(args, text=True, capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        run_id = invoke("begin", "begin")["id"]
        for command, payload_name in (("record-evidence", "evidence"), ("record-findings", "findings"),
                                      ("record-writeback", "writeback"), ("record-notification", "notification"),
                                      ("complete", "complete")):
            self.assertEqual(invoke(command, payload_name, run_id), invoke(command, payload_name, run_id))
        self.assertEqual(invoke("begin", "begin")["id"], run_id)
        status, detail = self.request("/api/bug-audit/runs/" + str(run_id), self.reviewer)
        self.assertEqual(status, 200)
        self.assertEqual(detail["status"], "complete")
        self.assertEqual([detail[key] for key in ("collection_status", "audit_status", "writeback_status", "notification_status")],
                         ["complete", "complete", "verified", "verified"])
        self.assertEqual(len(detail["items"]), 1)
        self.assertEqual(len(detail["items"][0]["history"]), 1)
        self.assertEqual({field["name"]: field["status"] for field in detail["items"][0]["fields"]}, {
            "Bug Standard Review of Testing": "verified",
            "Bug Standard Review of Development": "verified",
            "Bug quality audit": "verified",
        })
        status, runs = self.request("/api/bug-audit/runs", self.owner)
        self.assertEqual(status, 200)
        self.assertEqual([(run["id"], run["status"]) for run in runs], [(run_id, "complete")])
        self.assertEqual(len(detail["events"]), 5)


class BugAuditPageTests(unittest.TestCase):
    def test_bug_page_exposes_snapshot_and_read_only_daily_run_details(self):
        source = (ROOT / "outputs" / "SQA_MVP" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-bug-audit-view="snapshot"', source)
        self.assertIn('data-bug-audit-view="runs"', source)
        self.assertIn('api("bug-audit/runs")', source)
        self.assertIn('api("bug-audit/runs/" + encodeURIComponent(id))', source)
        self.assertIn("History</th>", source)
        self.assertNotIn('api("bug-audit/write', source)


if __name__ == "__main__":
    unittest.main()
