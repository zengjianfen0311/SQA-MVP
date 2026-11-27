import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "outputs/SQA_MVP/bug_audit_worker_cli.py"


class CLITests(unittest.TestCase):
    def call(self, *args):
        return subprocess.run([sys.executable, "-X", "utf8", str(ENTRY), *args], capture_output=True, encoding="utf-8")

    def test_doctor_default_does_not_need_secrets(self):
        result = self.call("doctor")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["status"], "ready")

    def test_live_required_and_apply_is_explicit(self):
        result = self.call("run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("live", result.stdout)

    def test_demo_offline_and_existing_business_directory_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "演示 数据"
            result = self.call("demo", "--data-dir", str(dest))
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            value = json.loads(result.stdout)
            self.assertEqual(value["mode"], "demo")
            self.assertTrue((dest / "pilot.sqlite3").exists())
            again = self.call("demo", "--data-dir", str(dest))
            self.assertEqual(again.returncode, 0, again.stdout)
            other = Path(tmp) / "business"; other.mkdir(); (other / "untouched").write_text("keep")
            self.assertEqual(self.call("demo", "--data-dir", str(other)).returncode, 2)
            self.assertEqual((other / "untouched").read_text(), "keep")

    def test_json_stdout_is_utf8_even_under_windows_legacy_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "PYTHONIOENCODING": "gbk"}
            result = subprocess.run([sys.executable, str(ENTRY), "demo", "--data-dir", str(Path(tmp) / "演示")], capture_output=True, env=env)
            self.assertEqual(result.returncode, 0)
            value = json.loads(result.stdout.decode("utf-8"))
            self.assertIn("合成演示", value["notice"])

    def test_offline_commands_do_not_load_env_files(self):
        result=self.call("doctor","--env-file","does-not-exist.env")
        self.assertEqual(result.returncode,2)
        self.assertIn("--live",result.stdout)

    def test_online_doctor_uses_explicit_env_files_without_external_business_writes(self):
        from tests.bug_audit_worker_server import WorkerServer
        with WorkerServer() as remote:
            first=remote.root/"tp.env";second=remote.root/"company.env"
            first.write_text("TEST_TP_TOKEN=synthetic-token\nPATH=do-not-replace\n",encoding="utf-8")
            second.write_text("TEST_MODEL_KEY=synthetic-model-key\nTEST_FEISHU_SECRET=synthetic-feishu-secret\n",encoding="utf-8")
            env={k:v for k,v in os.environ.items() if k not in {"TEST_TP_TOKEN","TEST_MODEL_KEY","TEST_FEISHU_SECRET"}}
            result=subprocess.run([sys.executable,"-X","utf8",str(ENTRY),"doctor","--config",str(remote.root/"worker.toml"),"--live","--env-file",str(first),"--env-file",str(second)],env=env,capture_output=True,text=True,encoding="utf-8",timeout=20)
            self.assertEqual(result.returncode,0,result.stderr+result.stdout)
            self.assertEqual(json.loads(result.stdout)["status"],"ready")
            self.assertEqual(remote.counts[("POST","/api/v1/Bugs/bulk")],0)
            self.assertEqual(remote.counts[("POST","/open-apis/im/v1/messages")],0)
            self.assertEqual(remote.counts[("POST","/open-apis/im/v1/files")],0)
