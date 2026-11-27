"""无 Codex 依赖的 Bug 审计入口。"""
import json
import sys

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")

if sys.version_info < (3, 11):
    print(json.dumps({"status": "blocked", "exit_code": 2, "issues": ["需要 Python 3.11 或更高版本"]}, ensure_ascii=False))
    raise SystemExit(2)

from bug_audit_worker.cli import main

if __name__ == "__main__": raise SystemExit(main())
