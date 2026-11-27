import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outputs" / "SQA_MVP"))
from bug_audit_worker.config import ConfigError
from bug_audit_worker.engine import Worker
from bug_audit_worker.journal import JournalError
from tests.bug_audit_worker_server import WorkerServer
from tests.test_bug_audit_worker_tp import WINDOW


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.remote = WorkerServer().__enter__()
        self.addCleanup(self.remote.__exit__, None, None, None)
        self.remote.config.data["model"]["api_style"] = "responses"
        self.worker = Worker(self.remote.config)

    def test_dry_run_has_no_external_writes_and_freezes_first_window(self):
        result = self.worker.run("dry_run", WINDOW)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(self.remote.counts[("POST", "/api/v1/Bugs/bulk")], 0)
        self.assertEqual(self.remote.counts[("POST", "/open-apis/im/v1/messages")], 0)
        later = {**WINDOW, "end": "2026-11-24T20:00:00+08:00"}
        replay = self.worker.run("dry_run", later)
        self.assertEqual(replay["run_id"], result["run_id"])
        self.assertEqual(replay["window"], result["window"])
        self.assertEqual(self.remote.counts[("POST", "/v1/responses")], 1)
        self.assertNotEqual(self.worker.audit.get_run(result["run_id"])["writeback_status"], "verified")

    def test_apply_complete_replay_and_reconcile_do_not_repeat_effects(self):
        result = self.worker.run("apply", WINDOW)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(self.remote.counts[("POST", "/api/v1/Bugs/bulk")], 1)
        self.assertEqual(self.remote.counts[("POST", "/open-apis/im/v1/messages")], 2)
        self.assertEqual(self.worker.audit.get_run(result["run_id"])["status"], "complete")
        posts = {k: v for k, v in self.remote.counts.items() if k[0] == "POST" and "auth" not in k[1]}
        self.assertEqual(self.worker.resume(result["run_id"])["exit_code"], 0)
        self.assertEqual(self.worker.reconcile(result["run_id"], online=True)["exit_code"], 0)
        self.assertEqual(posts, {k: v for k, v in self.remote.counts.items() if k[0] == "POST" and "auth" not in k[1]})

    def test_apply_gate_and_profile_cannot_bypass_same_day_delivery(self):
        cfg = self.remote.config
        cfg.data["worker"]["allow_apply"] = False
        with self.assertRaises(ConfigError): self.worker.run("apply", WINDOW)
        self.assertFalse(self.remote.requests)
        cfg.data["worker"]["allow_apply"] = True
        first = self.worker.run("apply", WINDOW)
        other = copy.deepcopy(cfg)
        object.__setattr__(other, "profile", "another")
        other.data["worker"]["profile"] = "another"
        with self.assertRaises(ConfigError): Worker(other).run("apply", WINDOW)
        self.assertEqual(self.remote.counts[("POST", "/open-apis/im/v1/messages")], 2)
        self.assertEqual(first["exit_code"], 0)

    def test_partial_file_resume_only_retries_failed_delivery(self):
        self.remote.fail_file_send = True
        first = self.worker.run("apply", WINDOW)
        self.assertEqual(first["exit_code"], 3)
        self.remote.fail_file_send = False
        resumed = self.worker.resume(first["run_id"])
        self.assertEqual(resumed["exit_code"], 0, resumed)
        self.assertEqual(self.remote.counts[("POST", "/api/v1/Bugs/bulk")], 1)
        self.assertEqual(self.remote.counts[("POST", "/open-apis/im/v1/messages")], 3)
        self.assertEqual(self.remote.counts[("POST", "/v1/responses")], 1)

    def test_unknown_summary_needs_verified_operator_receipt(self):
        self.remote.lose_summary_response = True
        first = self.worker.run("apply", WINDOW)
        self.assertEqual(first["exit_code"], 3)
        self.worker.resume(first["run_id"])
        self.assertEqual(self.remote.counts[("POST", "/open-apis/im/v1/messages")], 1)
        mid = next(iter(self.remote.messages))
        key = str(first["run_id"]) + ":feishu:summary"
        self.worker.reconcile(first["run_id"], online=True, receipts={key: mid})
        self.remote.lose_summary_response = False
        self.assertEqual(self.worker.resume(first["run_id"])["exit_code"], 0)
        self.assertEqual(self.remote.counts[("POST", "/open-apis/im/v1/messages")], 2)

    def test_checkpoint_tampering_and_changed_config_are_blocked(self):
        first = self.worker.run("dry_run", WINDOW)
        job = self.worker.journal.job(first["run_id"])
        path = self.remote.config.runs_dir / job["checkpoint"]["path"]
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(JournalError): self.worker.resume(first["run_id"])

    def test_legacy_receipt_blocks_same_day_apply(self):
        old = self.worker.audit.begin_run("legacy", WINDOW, {"project": "Synthetic", "teams": ["A"]}, "2026-11-10")
        self.worker.audit.record_notification(old["id"], "old-sent", {"schema_version": 1, "status": "sent"})
        with self.assertRaises(ConfigError): self.worker.run("apply", WINDOW)

    def test_legacy_display_labels_and_old_hash_cannot_bypass_day_gate(self):
        old = self.worker.audit.begin_run("legacy-labels", WINDOW, {"project": "1 Synthetic", "teams": ["2 A", "3 B"], "scope_hash": "f" * 64}, "v1")
        self.worker.audit.record_notification(old["id"], "sent", {"schema_version": 1, "status": "sent"})
        with self.assertRaises(ConfigError): self.worker.run("apply", WINDOW)
        self.assertFalse(self.remote.requests)

    def test_model_budget_survives_interrupted_review(self):
        self.remote.config.data["limits"]["max_model_calls"] = 1
        worker = Worker(self.remote.config)
        original = worker.model.review
        def crash(*args):
            original(*args)
            raise RuntimeError("模拟模型响应后进程中断")
        with patch.object(worker.model, "review", side_effect=crash):
            with self.assertRaises(RuntimeError): worker.run("dry_run", WINDOW)
        run_id = worker.audit.list_runs()[0]["id"]
        resumed = Worker(self.remote.config).resume(run_id)
        self.assertEqual(resumed["exit_code"], 3)
        self.assertEqual(self.remote.counts[("POST", "/v1/responses")], 1)

    def test_reconcile_during_audit_cannot_report_empty_fields_as_completed(self):
        with patch.object(self.worker.model,"review",side_effect=RuntimeError("模拟审计阶段中断")):
            with self.assertRaises(RuntimeError): self.worker.run("dry_run",WINDOW)
        run_id=self.worker.audit.list_runs()[0]["id"]
        result=self.worker.reconcile(run_id)
        self.assertEqual(result["counts"]["bugs"],1)
        self.assertEqual(result["counts"]["fields"],0)
        self.assertEqual(result["exit_code"],3)
        self.assertEqual(result["status"],"partial")

    def test_checkpoint_retains_validated_rule_citations_for_review(self):
        result=self.worker.run("dry_run",WINDOW)
        job=self.worker.journal.job(result["run_id"])
        state=json.loads((self.remote.config.runs_dir/job["checkpoint"]["path"]).read_text(encoding="utf-8"))
        checks={c["rule_id"]:c for c in state["findings"][0]["checks"]}
        self.assertEqual(checks["flow.state_consistency"]["evidence_refs"],["body"])
        self.assertEqual(checks["flow.state_consistency"]["status"],"pass")

    def test_line_endings_do_not_change_rule_or_execution_identity(self):
        import bug_audit_worker.engine as engine_module
        import bug_audit_worker.rules as rule_module
        source=self.remote.root/"module-copy"
        (source/"rules").mkdir(parents=True);(source/"prompts").mkdir()
        real=Path(engine_module.__file__).parent
        paths=[Path("rules/2026-11-10.md"),Path("prompts/semantic-review.md")]
        for path in paths:(source/path).write_bytes((real/path).read_text(encoding="utf-8").replace("\r\n","\n").encode("utf-8"))
        with patch.object(engine_module,"__file__",str(source/"engine.py")),patch.object(rule_module,"__file__",str(source/"rules.py")):
            first_rule=rule_module.rules_digest();first=Worker(self.remote.config).config_hash
            for path in paths:
                text=(source/path).read_bytes().decode("utf-8")
                (source/path).write_bytes(text.replace("\n","\r\n").encode("utf-8"))
            self.assertEqual(rule_module.rules_digest(),first_rule)
            self.assertEqual(Worker(self.remote.config).config_hash,first)

    def test_legacy_crlf_identity_recovers_same_run_without_duplicate_delivery(self):
        import hashlib
        import bug_audit_worker.engine as engine_module
        from bug_audit_worker.journal import digest
        self.remote.fail_file_send=True
        first=self.worker.run("apply",WINDOW)
        source=Path(engine_module.__file__).parent
        old_rule=hashlib.sha256((source/"rules/2026-11-10.md").read_text(encoding="utf-8").replace("\n","\r\n").encode()).hexdigest()
        old_prompt=hashlib.sha256((source/"prompts/semantic-review.md").read_text(encoding="utf-8").replace("\n","\r\n").encode()).hexdigest()
        old_hash=digest({"config":self.remote.config.data,"rules":old_rule,"prompt":old_prompt})
        with self.worker.store.connection(True) as db:
            row=db.execute("SELECT run_key FROM bug_audit_runs WHERE id=?",(first["run_id"],)).fetchone()
            old_key=row[0].rsplit(":",1)[0]+":"+old_rule[:24]
            db.execute("UPDATE bug_audit_runs SET run_key=? WHERE id=?",(old_key,first["run_id"]))
            db.execute("UPDATE bug_audit_worker_jobs SET config_hash=? WHERE run_id=?",(old_hash,first["run_id"]))
        self.remote.fail_file_send=False
        resumed=Worker(self.remote.config).resume(first["run_id"])
        self.assertEqual(resumed["run_id"],first["run_id"])
        self.assertEqual(resumed["exit_code"],0,resumed)
        self.assertEqual(self.remote.counts[("POST","/api/v1/Bugs/bulk")],1)
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/messages")],3)
        self.assertEqual(self.worker.journal.job(first["run_id"])["config_hash"],old_hash)
        self.assertEqual(Worker(self.remote.config).run("apply",WINDOW)["run_id"],first["run_id"])
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/messages")],3)

    def test_same_day_changed_rules_cannot_create_second_apply_delivery(self):
        import bug_audit_worker.engine as engine_module
        import bug_audit_worker.rules as rule_module
        self.worker.run("apply",WINDOW)
        source=self.remote.root/"changed-module"
        (source/"rules").mkdir(parents=True);(source/"prompts").mkdir()
        real=Path(engine_module.__file__).parent
        for relative in ("rules/2026-11-10.md","prompts/semantic-review.md"):
            (source/relative).write_text((real/relative).read_text(encoding="utf-8")+"\n明确不同的规则内容\n",encoding="utf-8")
        with patch.object(engine_module,"__file__",str(source/"engine.py")),patch.object(rule_module,"__file__",str(source/"rules.py")):
            with self.assertRaises(ConfigError):Worker(self.remote.config).run("apply",WINDOW)
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/messages")],2)

    def test_feature_lookup_rejects_unknown_or_cross_project_ids(self):
        from bug_audit_worker.tp import TPClient
        client = TPClient(self.remote.config)
        with self.assertRaises(ConfigError): client.feature_details([999], [])
        self.assertEqual(self.remote.counts[("GET", "/api/v2/Features")], 0)
        self.remote.features = [{"id": 25, "name": "Navigation", "description": "Route", "projectId": 2}]
        with self.assertRaises(ConfigError): client.feature_details([25], [{"id": 25, "projectId": 1}])
        self.remote.features[0]["projectId"] = 1
        self.assertEqual(client.feature_details([25], [{"id": 25, "projectId": 1}])[0]["description"], "Route")

    def test_apply_from_read_only_plan_creates_new_record_and_rechecks(self):
        first = self.worker.run("dry_run", WINDOW)
        self.remote.state = "Fixed"
        applied = self.worker.run("apply", source_run_id=first["run_id"])
        self.assertNotEqual(first["run_id"], applied["run_id"])
        self.assertEqual(first["window"], applied["window"])
        self.assertEqual(self.worker.audit.get_run(applied["run_id"])["items"][0]["snapshot"]["state"], "Fixed")
        self.assertEqual(self.remote.counts[("POST", "/v1/responses")], 2)

    def test_checkpoint_redacts_sensitive_evidence_without_breaking_preflight(self):
        self.remote.extra_body = " email: person@example.com; api_key=not-a-real-secret"
        result = self.worker.run("apply", WINDOW)
        self.assertEqual(result["exit_code"], 0, result)
        for path in self.remote.config.runs_dir.rglob("*.json"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("not-a-real-secret", text)
            self.assertNotIn("person@example.com", text)


    def test_equivalent_tp_url_cannot_duplicate_daily_apply(self):
        first = self.worker.run("apply", WINDOW)
        cfg = copy.deepcopy(self.remote.config)
        cfg.data["tp"]["base_url"] += "/"
        second = Worker(cfg).run("apply", WINDOW)
        self.assertEqual(second["run_id"], first["run_id"])
        self.assertEqual(self.remote.counts[("POST", "/open-apis/im/v1/messages")], 2)

    def test_old_scope_identity_requires_reconciliation_before_new_apply(self):
        run = self.worker.audit.begin_run("old-worker", WINDOW, {"project": "Synthetic", "teams": ["A", "B"], "scope_hash": "f" * 64}, "2026-11-10")
        self.worker.journal.save_job(run["id"], "old", "apply", WINDOW, "old-config", "complete", {})
        with self.assertRaises(ConfigError): self.worker.run("apply", WINDOW)
        self.assertFalse(self.remote.requests)

    def test_feature_evidence_change_before_write_blocks_stale_plan(self):
        self.remote.features = [{"id": 25, "name": "Navigation", "projectId": 1, "description": "Covers navigation reset"}]
        original = self.remote.response
        calls = 0
        def intercept(method, path, query, body, headers):
            nonlocal calls
            if path.startswith("/api/v2/Context/"):
                self.remote.intercept = None
                try: status, value = original(method, path, query, body, headers)
                finally: self.remote.intercept = intercept
                for field in value["customFields"]["items"]:
                    if field["name"] == "Bug Standard Review of Testing": field["value"] += "\nFeature is missing"
                return status, value
            if path == "/v1/responses":
                calls += 1
                self.remote.intercept = None
                try: status, value = original(method, path, query, body, headers)
                finally: self.remote.intercept = intercept
                answer = json.loads(value["output"][0]["content"][0]["text"])
                target = next(c for c in answer["checks"] if c["rule_id"] == "submission.feature")
                target.update(status="pending" if calls==1 else "fail", reason="Feature存在但未关联", evidence_refs=["body", "feature:25"])
                value["output"][0]["content"][0]["text"] = json.dumps(answer)
                if calls == 2: self.remote.features[0]["description"] = "Unrelated music playback only"
                return status, value
        self.remote.intercept = intercept
        result = self.worker.run("apply", WINDOW)
        self.assertEqual(calls, 2)
        self.assertEqual(self.remote.counts[("POST", "/api/v1/Bugs/bulk")], 0)
        self.assertEqual(result["exit_code"], 3)


if __name__ == "__main__": unittest.main()
