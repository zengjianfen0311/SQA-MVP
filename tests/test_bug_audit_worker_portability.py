import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outputs/SQA_MVP"))
from bug_audit_worker.config import ConfigError
from bug_audit_worker.engine import Worker
from tests.bug_audit_worker_server import WorkerServer
from tests.test_bug_audit_worker_tp import WINDOW


class PortabilityTests(unittest.TestCase):
    def test_clean_copy_empty_home_demo_and_reconcile_from_other_cwd(self):
        with tempfile.TemporaryDirectory(prefix="中文 portable ") as tmp:
            root = Path(tmp)
            copied = root / "仓库 副本" / "outputs/SQA_MVP"
            shutil.copytree(ROOT / "outputs/SQA_MVP", copied, ignore=shutil.ignore_patterns("__pycache__", "*.sqlite3", "*.log"))
            home = root / "empty home"; home.mkdir()
            cwd = root / "另一个 cwd"; cwd.mkdir()
            env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
            env.update(HOME=str(home), USERPROFILE=str(home), PYTHONUTF8="1", PATH=str(Path(sys.executable).parent))
            entry = copied / "bug_audit_worker_cli.py"
            def call(*args):
                result = subprocess.run([sys.executable, "-X", "utf8", str(entry), *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return json.loads(result.stdout)
            self.assertEqual(call("doctor")["status"], "ready")
            data = root / "演示 目录"
            result = call("demo", "--data-dir", str(data))
            self.assertEqual(result["mode"], "demo")
            self.assertEqual(result["counts"]["verified"], 0)
            check = call("reconcile", "--config", str(data / "worker-demo.toml"), "--run-id", str(result["run_id"]))
            self.assertEqual(check["status"], "planned")
            self.assertFalse(any(home.iterdir()))

    def test_demo_does_not_connect_or_read_real_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 真正执行入口，只拦截边界：任何网络连接或取凭据都会使子进程失败。
            script = "import socket,sys; from bug_audit_worker.config import Config; from bug_audit_worker.cli import main; socket.socket.connect=lambda *a: (_ for _ in ()).throw(RuntimeError('network forbidden')); Config.secret=lambda *a: (_ for _ in ()).throw(RuntimeError('secret forbidden')); raise SystemExit(main(['demo','--data-dir',sys.argv[1]]))"
            result = subprocess.run([sys.executable, "-X", "utf8", "-c", script, str(Path(tmp) / "demo")], cwd=ROOT / "outputs/SQA_MVP", capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(json.loads(result.stdout)["counts"]["verified"], 0)

    def test_first_apply_date_and_old_executor_day_gate_before_network(self):
        with WorkerServer() as remote:
            remote.config.data["worker"]["first_apply_date"] = "2026-11-25"
            with self.assertRaises(ConfigError): Worker(remote.config).run("apply", WINDOW)
            remote.config.data["worker"]["first_apply_date"] = "2026-11-24"
            remote.config.data["worker"]["legacy_last_run_date"] = "2026-11-24"
            with self.assertRaises(ConfigError): Worker(remote.config).run("apply", WINDOW)
            self.assertFalse(remote.requests)

    def test_deployment_launcher_refuses_implicit_apply(self):
        path = ROOT / "outputs/SQA_MVP/bug_audit_worker/schedule/run-worker.ps1"
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell: self.skipTest("此主机没有 PowerShell")
        with WorkerServer() as remote:
            result = subprocess.run([shell, "-NoProfile", "-File", str(path), "-ConfigPath", str(remote.root / "worker.toml"), "-Python", sys.executable], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(json.loads(result.stdout)["mode"], "dry_run")
            self.assertEqual(remote.counts[("POST", "/api/v1/Bugs/bulk")], 0)
            self.assertEqual(remote.counts[("POST", "/open-apis/im/v1/messages")], 0)


if __name__ == "__main__": unittest.main()
