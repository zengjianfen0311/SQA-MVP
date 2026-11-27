#!/bin/sh
# 配置绝对路径作为第一参数；默认只读。环境变量 PYTHON 指定部署虚拟环境解释器。
set -eu
config=${1:?需要 Worker TOML 配置路径}
mode=${2:-dry_run}
python_bin=${PYTHON:-python3}
schedule_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
entry="$schedule_dir/../../bug_audit_worker_cli.py"
case "$mode" in
  dry_run) exec "$python_bin" -X utf8 "$entry" run --config "$config" --live ;;
  apply) exec "$python_bin" -X utf8 "$entry" run --config "$config" --live --apply ;;
  *) printf '%s\n' '模式须为 dry_run 或 apply' >&2; exit 2 ;;
esac
