"""独立命令入口：默认离线，在线读取和真实写入显式选择。"""
import argparse
import json
import sys
from pathlib import Path
from .config import ConfigError, doctor, load_config, load_env_files


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"): sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Bug 审计独立 Worker（Python 3.11+）")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "run", "resume", "reconcile"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", default=str(Path(__file__).parent / "config.example.toml"))
        sub.add_argument("--live", action="store_true")
        sub.add_argument("--env-file", action="append", default=[], help="仅 --live：显式加载凭据环境文件，可多次指定；不自动搜索")
        if name in {"run", "resume"}: sub.add_argument("--apply", action="store_true")
        if name == "run": sub.add_argument("--from-run", type=int, help="保留只读计划窗口；创建独立 apply 并重新取证审计")
        if name in {"resume", "reconcile"}: sub.add_argument("--run-id", type=int, required=True)
        if name == "reconcile": sub.add_argument("--receipts", help="operation_key 到 message_id 的 JSON 文件；只读在线核验")
    sub = commands.add_parser("demo")
    sub.add_argument("--data-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            from .demo import run_demo
            result = run_demo(args.data_dir)
        else:
            if args.command in {"run", "resume"} and not args.live:
                raise ConfigError("run/resume 必须显式提供 --live；演示请使用 demo")
            cfg = load_config(args.config)
            if args.env_file:
                if not args.live: raise ConfigError("--env-file 仅配合 --live 使用；离线命令不读取凭据")
                names={cfg.data[section][key] for section,key in (("tp","token_env"),("model","key_env"),("feishu","secret_env"))}
                load_env_files(args.env_file,names)
            health = doctor(cfg, online=args.live)
            if health["status"] != "ready": result = {**health, "exit_code": 2}
            elif args.command == "doctor":
                if args.live:
                    from .tp import TPClient
                    from .model import ModelClient
                    from .feishu import FeishuClient
                    checks = {"tp": TPClient(cfg).metadata(), "model": ModelClient(cfg).probe(), "feishu": FeishuClient(cfg).probe()}
                    ok = all(v["status"] in {"ready", "complete"} for v in checks.values())
                    result = {**health, "checks": checks, "status": "ready" if ok else "blocked", "exit_code": 0 if ok else 2}
                else: result = {**health, "exit_code": 0}
            else:
                from .engine import Worker
                worker = Worker(cfg)
                if args.command == "run": result = worker.run("apply" if args.apply else "dry_run", source_run_id=args.from_run)
                elif args.command == "resume":
                    job = worker.journal.job(args.run_id)
                    if (job["mode"] == "apply") != args.apply: raise ConfigError("恢复模式必须与原运行一致；apply 恢复需要 --apply")
                    result = worker.resume(args.run_id)
                else:
                    receipts = json.loads(Path(args.receipts).read_text(encoding="utf-8")) if args.receipts else None
                    if receipts is not None and (not isinstance(receipts, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in receipts.items())):
                        raise ConfigError("人工回执格式应为 operation_key 到 message_id 的对象")
                    result = worker.reconcile(args.run_id, online=args.live, receipts=receipts)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return result.get("exit_code", 0)
    except (ConfigError, ValueError) as exc:
        # 仅受控配置错误可显示原文；任意 JSON/网络异常不输出含业务资料的异常文本。
        message = str(exc) if isinstance(exc, ConfigError) else "输入文件格式无效"
        print(json.dumps({"status": "blocked", "exit_code": 2, "issues": [message]}, ensure_ascii=False)); return 2
    except Exception as exc:
        from .journal import JournalError
        message = str(exc) if isinstance(exc, JournalError) else "执行失败，请检查运行阶段与配置；异常类型：" + type(exc).__name__
        print(json.dumps({"status": "failed", "exit_code": 4, "issues": [message]}, ensure_ascii=False)); return 4


if __name__ == "__main__": raise SystemExit(main())
