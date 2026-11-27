import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outputs" / "SQA_MVP"))
from bug_audit_worker.history import select_history
from bug_audit_worker.tp import TPClient
from bug_audit_worker.transport import HTTPClient
from tests.test_bug_audit_worker_config import EXAMPLE
from bug_audit_worker.config import load_config

WINDOW = {"start":"2026-11-24T00:00:00+08:00", "end":"2026-11-24T19:30:00.500+08:00"}

class HistoryTests(unittest.TestCase):
    def test_history_baseline_both_fields_and_fractional_cutoff(self):
        bugs = [{"id": 1}, {"id": 2}]
        histories = [
            {"id":1,"sourceEntityId":1,"date":"2026-11-23T12:00:00+08:00","modification":"Add","stateName":"New","severityName":"Major"},
            {"id":2,"sourceEntityId":1,"date":"2026-11-24T19:30:00.250+08:00","modification":"Update","changes":"EntityState,Severity","stateName":"Open","severityName":"Critical"},
            {"id":3,"sourceEntityId":1,"date":WINDOW["end"],"modification":"Update","changes":"EntityState","stateName":"Fixed","severityName":"Critical"},
            {"id":4,"sourceEntityId":2,"date":"2026-11-24T12:00:00+08:00","modification":"Add","stateName":"New","severityName":"Normal"},
        ]
        items, unresolved = select_history(bugs, histories, WINDOW)
        self.assertEqual([i["bug_id"] for i in items], ["1"])
        self.assertEqual({(h["id"], h["field"]) for h in items[0]["history"]}, {("2","State"),("2","Severity")})
        self.assertEqual(unresolved, [])

    def test_missing_old_value_is_unresolved_not_excluded_as_no_change(self):
        items, unresolved = select_history([{"id":1}], [{"id":2,"sourceEntityId":1,"date":"2026-11-24T12:00:00+08:00","modification":"Update","changes":"EntityState","stateName":"Open"}], WINDOW)
        self.assertEqual(items, [])
        self.assertEqual(unresolved[0]["bug_id"], "1")

class TPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.calls = []
        calls = self.calls
        response = lambda p,q: self.respond(p,q)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                parsed = urlsplit(self.path); q = {k:v[0] for k,v in parse_qs(parsed.query).items()}
                calls.append((parsed.path,q))
                payload = response(parsed.path,q)
                raw = json.dumps(payload).encode(); self.send_response(200); self.end_headers(); self.wfile.write(raw)
        self.server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.stop)
        path = Path(self.tmp.name)/"cfg.toml";path.write_text(EXAMPLE,encoding="utf-8")
        self.cfg = load_config(path)
        self.http = HTTPClient("http://127.0.0.1:"+str(self.server.server_port),allow_loopback_http=True)
        self.client = TPClient(self.cfg,self.http)
        self.env = patch.dict("os.environ",{"TEST_TP_TOKEN":"fake-token"});self.env.start();self.addCleanup(self.env.stop)

    def stop(self): self.server.shutdown();self.server.server_close();self.thread.join(3)

    def respond(self,path,q):
        if path.endswith("/Projects"): return {"items":[{"id":1,"name":"Synthetic"}],"next":None}
        if path.endswith("/Teams"): return {"items":[{"id":2,"name":"A"},{"id":3,"name":"B"}],"next":None}
        if "/Context/" in path:
            return {"customFields":{"items":[{"name":n,"value":"OK\nNo need\nRelations is wrong"} for n in ("Bug Standard Review of Testing","Bug Standard Review of Development","Bug quality audit")]}}
        if path.endswith("/Bugs"):
            if "inboundRelations" in q["select"]:
                return {"items":[{"id":1,"inbound":[],"outbound":[],"inboundCount":0,"outboundCount":0}],"next":None}
            return {"items":[{"id":1,"name":"Synthetic bug","description":"Firmware 1.0","createDate":"2026-11-23T12:00:00+08:00","projectId":1,"assignedTeamIds":[2],"stateName":"Open","severityName":"Major","featureId":None,"customValues":{"customFields":[]}}],"next":None}
        if path.endswith("/BugHistories"):
            return {"items":[{"id":10,"sourceEntityId":1,"date":"2026-11-23T12:00:00+08:00","createDate":"2026-11-23T12:00:00+08:00","modification":"Add","stateName":"New","severityName":"Major"},{"id":11,"sourceEntityId":1,"date":"2026-11-24T12:00:00+08:00","modification":"Update","changes":"EntityState","stateName":"Open","severityName":"Major"}],"next":None}
        if path.endswith("/Comments"): return {"items":[{"id":20,"generalId":1,"createDate":"2026-11-24T12:00:00+08:00","description":"retest this scenario"}],"next":None}
        if path.endswith("/Features"): return {"items":[],"next":None}
        raise AssertionError(path)

    def test_native_collection_scope_and_evidence_are_bounded(self):
        bundle = self.client.collect(WINDOW)
        self.assertEqual(bundle["completeness"], "complete")
        self.assertEqual(bundle["candidate_count"],1)
        self.assertEqual(bundle["items"][0]["bug_id"],"1")
        self.assertEqual(bundle["items"][0]["comments"][0]["id"],"20")
        query = next(q for p,q in self.calls if p.endswith("/Bugs") and "modifyDate" in q["where"])
        self.assertIn("assignedTeams",query["where"])
        self.assertIn("project.id == 1",query["where"])
        self.assertFalse(any("2026-01-01" in q.get("where","") for p,q in self.calls))
        self.assertEqual({q["access_token"] for p,q in self.calls},{"fake-token"})

    def test_repeated_page_cannot_report_complete(self):
        self.cfg.data["limits"]["page_size"] = 1
        self.cfg.data["limits"]["max_pages"] = 3
        rows,audit = self.client.rows("Bugs","id in [1]","{id}")
        self.assertEqual(audit["status"],"complete")
        original = self.respond
        self.respond = lambda p,q: {"items":[{"id":1}],"next":"?skip=1"}
        rows,audit = self.client.rows("Bugs","id in [1]","{id}")
        self.assertEqual(audit["status"],"partial")
        self.assertEqual(len(rows),1)

if __name__ == "__main__": unittest.main()
