import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"outputs"/"SQA_MVP"))
from bug_audit import BugAudit
from store import Store
from bug_audit_worker.journal import Journal
from bug_audit_worker.tp import TPClient,FIELDS
from bug_audit_worker.writeback import Writeback
from tests.bug_audit_worker_server import WorkerServer
from tests.test_bug_audit_worker_tp import WINDOW

class WritebackTests(unittest.TestCase):
    def setUp(self):
        self.remote=WorkerServer().__enter__();self.addCleanup(self.remote.__exit__,None,None,None)
        self.store=Store(self.remote.config.data_dir);self.audit=BugAudit(self.store);self.j=Journal(self.store)
        self.tp=TPClient(self.remote.config);self.bundle=self.tp.collect(WINDOW)
        self.run=self.audit.begin_run("writeback",WINDOW,{},"v1")
        self.findings=[{"bug_id":"1","fields":[{"name":n,"original_value":None,"proposed_value":"无异常" if n==FIELDS[2] else "OK","ready":True,"reason":""} for n in FIELDS]}]
        self.audit.record_evidence(self.run["id"],"e",{"schema_version":1,"scope_status":"complete","items":self.bundle["items"]})
        self.audit.record_findings(self.run["id"],"f",{"schema_version":1,"items":self.findings})
        self.plan={"bundle":self.bundle,"findings":self.findings}
        self.writer=Writeback(self.tp,self.j,self.audit)

    def test_success_and_lost_response_are_read_back_and_not_rewritten(self):
        self.remote.lose_tp_response=True
        self.writer.execute(self.run["id"],self.plan)
        self.assertEqual(self.remote.counts[("POST","/api/v1/Bugs/bulk")],1)
        self.assertEqual({f["status"] for f in self.audit.get_run(self.run["id"])["items"][0]["fields"]},{"verified"})
        self.writer.execute(self.run["id"],self.plan)
        self.assertEqual(self.remote.counts[("POST","/api/v1/Bugs/bulk")],1)

    def test_conflicted_field_is_excluded_but_other_fields_are_independent(self):
        self.remote.fields[FIELDS[0]]="Changed by reviewer"
        self.writer.execute(self.run["id"],self.plan)
        self.assertEqual(self.remote.fields[FIELDS[0]],"Changed by reviewer")
        fields={f["name"]:f["status"] for f in self.audit.get_run(self.run["id"])["items"][0]["fields"]}
        self.assertEqual(fields[FIELDS[0]],"conflict")
        self.assertEqual(fields[FIELDS[1]],"verified")
        payload=next(body for method,path,q,body,h in self.remote.requests if method=="POST" and path=="/api/v1/Bugs/bulk")
        self.assertNotIn(FIELDS[0].encode(),payload)

    def test_receipt_committed_before_ledger_failure_recovers_without_post(self):
        with patch.object(self.audit,"record_writeback",side_effect=RuntimeError("injected interruption")):
            with self.assertRaises(RuntimeError):self.writer.execute(self.run["id"],self.plan)
        self.assertEqual(self.j.intents(self.run["id"])[0]["status"],"verified")
        self.writer.execute(self.run["id"],self.plan)
        self.assertEqual(self.remote.counts[("POST","/api/v1/Bugs/bulk")],1)
        self.assertEqual(self.audit.get_run(self.run["id"])["writeback_status"],"verified")

    def test_business_evidence_change_prevents_all_writes(self):
        self.remote.state="Fixed"
        self.writer.execute(self.run["id"],self.plan)
        self.assertEqual(self.remote.counts[("POST","/api/v1/Bugs/bulk")],0)
        self.assertEqual(self.audit.get_run(self.run["id"])["writeback_status"],"partial")

if __name__=="__main__":unittest.main()
