import csv
import io
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"outputs"/"SQA_MVP"))
from store import Store
from bug_audit import BugAudit
from bug_audit_worker.journal import Journal
from bug_audit_worker.feishu import FeishuClient
from bug_audit_worker.reports import report_run
from tests.bug_audit_worker_server import WorkerServer
from tests.test_bug_audit_worker_tp import WINDOW

class FeishuTests(unittest.TestCase):
    def setUp(self):
        self.remote=WorkerServer().__enter__();self.addCleanup(self.remote.__exit__,None,None,None)
        self.store=Store(self.remote.config.data_dir);self.audit=BugAudit(self.store);self.j=Journal(self.store)
        self.run=self.audit.begin_run("feishu",WINDOW,{},"v1")
        self.report={"markdown":"固件 Bug 审计：合成验证，1单，3字段。","csv_bytes":"Bug,状态\n1,完成\n".encode("utf-8-sig"),"filename":"Bug审计明细.csv","summary":{"candidate_count":1}}
        self.client=FeishuClient(self.remote.config)

    def test_verified_delivery_has_real_mentions_download_and_no_replay_send(self):
        result=self.client.notify(self.run["id"],self.report,self.j)
        self.assertEqual(result["status"],"verified")
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/messages")],2)
        self.assertEqual(self.remote.csv,self.report["csv_bytes"])
        message=next(m for m in self.remote.messages.values() if m["msg_type"]=="post")
        self.assertEqual(message["mentions"][0]["id"],"configured-user")
        self.client.notify(self.run["id"],self.report,self.j)
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/messages")],2)
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/files")],1)

    def test_message_scoped_file_key_uses_download_hash_without_resending(self):
        self.client.notify(self.run["id"],self.report,self.j)
        message=next(m for m in self.remote.messages.values() if m["msg_type"]=="file")
        message["body"]["content"]=json.dumps({"file_key":"file_message_scoped","file_name":self.report["filename"]})
        result=self.client.recover(self.run["id"],self.j)
        self.assertEqual(result["status"],"verified")
        path="/open-apis/im/v1/messages/"+message["message_id"]+"/resources/file_message_scoped"
        self.assertEqual(self.remote.counts[("GET",path)],1)
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/messages")],2)
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/files")],1)

    def test_message_scoped_file_key_still_requires_filename_and_exact_bytes(self):
        self.client.notify(self.run["id"],self.report,self.j)
        message=next(m for m in self.remote.messages.values() if m["msg_type"]=="file")
        for filename,data in (("wrong.csv",self.report["csv_bytes"]),(self.report["filename"],b"different bytes")):
            with self.subTest(filename=filename):
                message["body"]["content"]=json.dumps({"file_key":"file_message_scoped","file_name":filename})
                self.remote.csv=data
                self.assertEqual(self.client.check_delivery(self.run["id"],self.j)["status"],"partial")

    def test_missing_membership_permission_blocks_notification_without_sending(self):
        self.remote.members_allowed=False
        result=self.client.notify(self.run["id"],self.report,self.j)
        self.assertEqual(result["status"],"blocked")
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/messages")],0)

    def test_unknown_summary_is_not_resent_and_operator_receipt_must_be_verified(self):
        self.remote.lose_summary_response=True
        result=self.client.notify(self.run["id"],self.report,self.j)
        self.assertEqual(result["status"],"unknown")
        self.client.notify(self.run["id"],self.report,self.j)
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/messages")],1)
        key=str(self.run["id"])+":feishu:summary"
        self.client.recover(self.run["id"],self.j,{key:next(iter(self.remote.messages))})
        self.remote.lose_summary_response=False
        result=self.client.notify(self.run["id"],self.report,self.j)
        self.assertEqual(result["status"],"verified")
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/messages")],2)

    def test_wrong_sender_cannot_be_verified_as_delivery(self):
        self.client.notify(self.run["id"],self.report,self.j)
        message=next(iter(self.remote.messages.values()));message["sender"]["id"]="another-app"
        result=self.client.check_delivery(self.run["id"],self.j)
        self.assertNotEqual(result["status"],"verified")

    def test_certain_file_failure_can_retry_only_failed_part(self):
        self.remote.fail_file_send=True
        self.assertEqual(self.client.notify(self.run["id"],self.report,self.j)["status"],"partial")
        self.remote.fail_file_send=False
        self.assertEqual(self.client.notify(self.run["id"],self.report,self.j,retry_failed=True)["status"],"verified")
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/messages")],3)

    def test_plain_replay_keeps_certain_failure_retryable(self):
        self.remote.fail_file_send=True
        self.client.notify(self.run["id"],self.report,self.j)
        self.client.notify(self.run["id"],self.report,self.j)
        intent=next(i for i in self.j.intents(self.run["id"]) if i["operation_key"].endswith(":file"))
        self.assertEqual(intent["status"],"failed")
        self.assertTrue(intent["receipt"]["safe_retry"])
        self.remote.fail_file_send=False
        self.assertEqual(self.client.notify(self.run["id"],self.report,self.j,retry_failed=True)["status"],"verified")
        self.assertEqual(self.remote.counts[("POST","/open-apis/im/v1/files")],1)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM bug_audit_worker_intent_events WHERE status='failed'").fetchone()[0],1)

    def test_report_csv_neutralizes_formulas_and_masks_business_contact_data(self):
        bundle={"window":WINDOW,"candidate_count":1,"completeness":"complete","items":[{"bug_id":"1","snapshot":{"title":"=HYPERLINK(\"bad\") user@example.com"}}]}
        findings=[{"bug_id":"1","fields":[{"name":"Bug quality audit","original_value":"@unsafe","proposed_value":"无异常","ready":True,"reason":""}]}]
        report=report_run(bundle,findings,[])
        rows=list(csv.reader(io.StringIO(report["csv_bytes"].decode("utf-8-sig"))))
        self.assertTrue(any(cell.startswith("'=HYPERLINK") for row in rows for cell in row))
        self.assertNotIn("user@example.com",report["csv_bytes"].decode("utf-8-sig"))

if __name__=="__main__":unittest.main()
