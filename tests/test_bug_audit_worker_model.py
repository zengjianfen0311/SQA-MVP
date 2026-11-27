import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"outputs"/"SQA_MVP"))
from bug_audit_worker.config import load_config
from bug_audit_worker.model import ModelClient
from bug_audit_worker.transport import HTTPClient
from bug_audit_worker.rules import semantic_rules
from tests.test_bug_audit_worker_config import EXAMPLE
from tests.test_bug_audit_worker_rules import item

class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name)/"config.toml";p.write_text(EXAMPLE,encoding="utf-8");self.cfg=load_config(p)
        self.record=item();self.record["body"]="Firmware 1.0; email: me@example.com; api_key=secret123"
        self.record["comments"]=[{"id":"20","at":"2026-11-24T12:00:00+08:00","text":"test the scenario"}]
        self.answer={"schema_version":1,"checks":[{"rule_id":rid,"status":"pass","reason":"资料已核对","evidence_refs":["body"]} for rid in semantic_rules(self.record)],
                     "coverage":{"body":True,"comment_ids":["20"],"field_names":["Bug Category","REG issue"]}}
        self.requests=[];owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers["Content-Length"])));owner.requests.append((self.path,body))
                text=json.dumps(owner.answer)
                payload={"choices":[{"finish_reason":"stop","message":{"content":text}}]} if "chat" in self.path else {"status":"completed","output":[{"type":"reasoning","summary":[]},{"type":"message","content":[{"type":"output_text","text":text}]}]}
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(payload).encode())
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
        self.http=HTTPClient("http://127.0.0.1:"+str(self.server.server_port)+"/v1",allow_loopback_http=True)
        self.env=patch.dict("os.environ",{"TEST_MODEL_KEY":"configured-secret"});self.env.start();self.addCleanup(self.env.stop)

    def stop(self):self.server.shutdown();self.server.server_close();self.thread.join(3)
    def review(self):return ModelClient(self.cfg,self.http).review(self.record,{}, {"items":[],"details":[],"status":"complete"})

    def test_both_protocols_use_no_tools_and_business_data_is_redacted(self):
        # Feature pass needs explicit matching Feature evidence; use not_applicable here.
        self.answer["checks"][4]["status"]="not_applicable"
        for style in ("chat_completions","responses"):
            self.cfg.data["model"]["api_style"]=style
            checks=self.review()
            self.assertTrue(all(c["status"] in {"pass","not_applicable"} for c in checks))
            path,payload=self.requests[-1]
            self.assertEqual(path,"/v1/chat/completions" if style=="chat_completions" else "/v1/responses")
            self.assertNotIn("tools",payload)
            self.assertNotIn("previous_response_id",payload)
            text=json.dumps(payload)
            self.assertNotIn("me@example.com",text)
            self.assertNotIn("secret123",text)

    def test_uncovered_comments_forged_citations_and_invalid_json_cannot_pass(self):
        self.answer["coverage"]["comment_ids"]=[]
        self.assertTrue(all(c["status"]=="pending" for c in self.review()))
        self.answer["coverage"]["comment_ids"]=["20"]
        self.answer["checks"][0]["evidence_refs"]=["comment:nonexistent"]
        self.assertEqual(self.review()[0]["status"],"pending")
        self.answer="not a result object"
        self.assertTrue(all(c["status"]=="pending" for c in self.review()))

    def test_budget_does_not_silently_truncate_or_make_request(self):
        self.cfg.data["model"]["max_input_chars"]=30
        checks=self.review()
        self.assertTrue(all(c["status"]=="pending" for c in checks))
        self.assertEqual(self.requests,[])

    def test_feature_failure_requires_known_feature_details(self):
        feature=next(c for c in self.answer["checks"] if c["rule_id"]=="submission.feature")
        feature.update(status="fail",reason="功能归属存在但未关联",evidence_refs=["body","feature:99"])
        features={"items":[{"id":99,"name":"Synthetic feature"}],"details":[],"status":"complete"}
        client=ModelClient(self.cfg,self.http)
        checks=client.review(self.record,{},features)
        result=next(c for c in checks if c["rule_id"]=="submission.feature")
        self.assertEqual(result["status"],"pending")
        self.assertEqual(result["requested_feature_ids"],[99])
        features["details"]=[{"id":99,"name":"Synthetic feature","description":"Applicable feature evidence"}]
        result=next(c for c in client.review(self.record,{},features) if c["rule_id"]=="submission.feature")
        self.assertEqual(result["status"],"fail")

    def test_required_state_explanation_cannot_be_skipped_by_model(self):
        for state in ("Invalid", "Duplicate", "Wont fix", "Later", "Reproduce", "Needs info"):
            with self.subTest(state=state):
                self.record["snapshot"]["state"] = state
                for result in self.answer["checks"]:
                    if result["rule_id"] in {"flow.explanation", "flow.keywords", "submission.feature", "submission.app_platform", "submission.app_version"}:
                        result.update(status="not_applicable", evidence_refs=[])
                checks = {c["rule_id"]: c for c in self.review()}
                self.assertEqual(checks["flow.explanation"]["status"], "pending")
                if state in {"Invalid", "Reproduce", "Needs info"}:
                    self.assertEqual(checks["flow.keywords"]["status"], "pending")

    def test_probe_distinguishes_valid_pending_semantics_from_protocol_failure(self):
        self.answer = {"schema_version": 1, "checks": [{"rule_id": rid, "status": "pending", "reason": "合成资料不足", "evidence_refs": []}
                       for rid in ("flow.explanation", "flow.keywords", "flow.state_consistency")],
                       "coverage": {"body": True, "comment_ids": [], "field_names": []}}
        result = ModelClient(self.cfg, self.http).probe()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["semantic_status"], "pending")
        self.answer["coverage"]["comment_ids"] = ["forged"]
        self.assertEqual(ModelClient(self.cfg, self.http).probe()["status"], "blocked")

    def test_large_or_partial_feature_index_only_blocks_feature_check(self):
        for status in ("complete", "partial"):
            with self.subTest(status=status):
                features = {"items": [{"id":i+1,"name":"Navigation "*30} for i in range(1000)], "details":[], "status":status}
                checks = ModelClient(self.cfg,self.http).review(self.record,{},features)
                by_id = {c["rule_id"]:c for c in checks}
                self.assertEqual(by_id["flow.state_consistency"]["status"], "pass")
                self.assertEqual(by_id["submission.feature"]["status"], "pending")
                sent = json.loads(self.requests[-1][1]["messages"][-1]["content"])
                self.assertEqual([c["id"] for c in sent["comments"]], ["20"])
                self.assertEqual(sent["features"]["items"], [])

    def test_old_bug_does_not_send_unrelated_feature_index(self):
        self.record["created_at"]="2025-01-01T00:00:00+08:00"
        self.answer["checks"]=[c for c in self.answer["checks"] if c["rule_id"].startswith("flow.")]
        features={"items":[{"id":i+1,"name":"unrelated "*30} for i in range(1000)],"details":[],"status":"complete"}
        checks=ModelClient(self.cfg,self.http).review(self.record,{},features)
        self.assertTrue(all(c["status"]=="pass" for c in checks))
        self.assertEqual(len(self.requests),1)

    def test_linked_feature_uses_verified_description_without_full_catalog(self):
        self.record["feature_id"]=25
        feature=next(c for c in self.answer["checks"] if c["rule_id"]=="submission.feature")
        feature["evidence_refs"]=["body","feature:25"]
        features={"items":[{"id":i+1,"name":"unrelated "*30} for i in range(1000)],
                  "details":[{"id":25,"name":"Navigation","description":"Covers navigation reset","projectId":1}],"status":"partial"}
        checks=ModelClient(self.cfg,self.http).review(self.record,{},features)
        self.assertEqual(next(c for c in checks if c["rule_id"]=="submission.feature")["status"],"pass")
        sent=json.loads(self.requests[-1][1]["messages"][-1]["content"])
        self.assertEqual([f["id"] for f in sent["features"]["items"]],[25])
        features["details"][0]["projectId"]=999
        checks=ModelClient(self.cfg,self.http).review(self.record,{},features)
        self.assertEqual(next(c for c in checks if c["rule_id"]=="submission.feature")["status"],"pending")

    def test_empty_feature_description_cannot_prove_applicability(self):
        feature=next(c for c in self.answer["checks"] if c["rule_id"]=="submission.feature")
        feature.update(evidence_refs=["body","feature:25"])
        features={"items":[{"id":25,"name":"Navigation"}],"details":[{"id":25,"name":"Navigation","description":"  "}],"status":"complete"}
        checks=ModelClient(self.cfg,self.http).review(self.record,{},features)
        self.assertEqual(next(c for c in checks if c["rule_id"]=="submission.feature")["status"],"pending")

    def test_pending_feature_with_known_citation_requests_missing_description(self):
        feature=next(c for c in self.answer["checks"] if c["rule_id"]=="submission.feature")
        feature.update(status="pending",reason="需要候选描述才能判断",evidence_refs=["body","feature:99"])
        checks=ModelClient(self.cfg,self.http).review(self.record,{}, {"items":[{"id":99,"name":"Tides"}],"details":[],"status":"complete"})
        result=next(c for c in checks if c["rule_id"]=="submission.feature")
        self.assertEqual(result["requested_feature_ids"],[99])

if __name__=="__main__":unittest.main()
