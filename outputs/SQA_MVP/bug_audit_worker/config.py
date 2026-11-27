"""配置仅引用凭据环境变量，不读取个人工具目录。"""
import copy
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo


class ConfigError(ValueError):
    pass


def canonical_url(value):
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if ":" in host: host = "[" + host + "]"
    port = parsed.port
    if port and not (parsed.scheme == "https" and port == 443 or parsed.scheme == "http" and port == 80):
        host += ":" + str(port)
    return urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip("/"), "", ""))


def load_env_files(paths, names):
    """仅显式文件、仅所需变量，原子载入当前进程；不执行 shell 或变量展开。"""
    require(len(paths) <= 5, "启动环境文件不能超过五份")
    reserved = {"PATH","HOME","USERPROFILE","CODEX_HOME","PYTHONPATH","PYTHONHOME","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","SYSTEMROOT","WINDIR","COMSPEC","TEMP","TMP","PATHEXT"}
    require(not {name.upper() for name in names} & reserved, "凭据不能复用系统环境变量")
    updates = {}
    for path in paths:
        try:
            with Path(path).open("rb") as handle: raw = handle.read(1024 * 1024 + 1)
            require(len(raw) <= 1024 * 1024, "启动环境文件过大")
            lines = raw.decode("utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            raise ConfigError("启动环境文件无法读取或不是 UTF-8") from None
        seen = set()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"): continue
            match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
            require(match is not None, "启动环境文件须使用 NAME=value 格式")
            key, value = match[1], match[2]
            if key not in names: continue
            require(key not in seen, "同一启动环境文件存在重复的凭据变量")
            seen.add(key)
            if value.startswith(("'", '"')):
                quoted = re.match(r"'(?:[^']*)'" if value[0] == "'" else r'"(?:[^"\\]|\\.)*"', value)
                require(quoted is not None, "启动环境文件的引号不完整")
                trailing = value[quoted.end():].strip()
                require(not trailing or trailing.startswith("#"), "启动环境文件的引号后存在无效内容")
                try: value = quoted[0][1:-1] if value[0] == "'" else json.loads(quoted[0])
                except ValueError: raise ConfigError("启动环境文件的转义无效") from None
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            require(len(value) <= 16384 and not any(c in value for c in "\r\n\x00"), "凭据值长度超限或包含控制字符")
            updates[key] = value
    os.environ.update(updates)


def require(condition, message):
    if not condition:
        raise ConfigError(message)


KEYS = {
    "worker": {"profile", "data_dir", "runs_dir", "allow_apply", "first_apply_date", "legacy_quiesced", "legacy_last_run_date"},
    "scope": {"project_id", "project_name", "team_ids", "team_names"},
    "tp": {"base_url", "context_id", "token_env"},
    "model": {"base_url", "api_style", "name", "key_env", "structured_mode", "max_input_chars", "max_output_tokens"},
    "feishu": {"base_url", "app_id", "secret_env", "chat_id", "chat_name", "bot_open_id", "mentions"},
    "transport": {"timeout", "max_bytes", "max_retries", "allow_loopback_http"},
    "limits": {"page_size", "max_pages", "batch_size", "max_model_calls", "max_features"},
}
DEFAULTS = {
    "worker": {"allow_apply": False, "first_apply_date": "", "legacy_quiesced": False, "legacy_last_run_date": ""},
    "model": {"structured_mode": "schema", "max_input_chars": 120000, "max_output_tokens": 4000},
    "transport": {"timeout": 30, "max_bytes": 8 * 1024 * 1024, "max_retries": 2, "allow_loopback_http": False},
    "limits": {"page_size": 200, "max_pages": 1000, "batch_size": 20, "max_model_calls": 500, "max_features": 10000},
}


@dataclass(frozen=True)
class Config:
    profile: str
    data_dir: Path
    runs_dir: Path
    data: dict = field(repr=False)

    def section(self, name):
        return copy.deepcopy(self.data[name])

    def secret(self, section):
        key = {"tp": "token_env", "model": "key_env", "feishu": "secret_env"}[section]
        value = os.environ.get(self.data[section][key], "")
        require(bool(value.strip()), section + " 凭据环境变量未配置")
        return value


def load_config(path):
    path = Path(path).resolve()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        raise ConfigError("配置文件无法读取或 TOML 格式无效") from None
    require(set(data) <= set(KEYS), "配置包含未知分组")
    for section, allowed in KEYS.items():
        block = data.setdefault(section, {})
        require(isinstance(block, dict) and set(block) <= allowed, section + " 包含未知配置项")
        for key, value in DEFAULTS.get(section, {}).items():
            block.setdefault(key, value)
    for section, keys in {
        "worker": ("profile", "data_dir", "runs_dir"), "tp": ("base_url", "context_id", "token_env"),
        "model": ("base_url", "api_style", "name", "key_env"),
        "feishu": ("base_url", "app_id", "secret_env", "chat_id", "chat_name", "bot_open_id"),
        "scope": ("project_name",),
    }.items():
        for key in keys:
            require(isinstance(data[section].get(key), str) and bool(data[section][key].strip()), section + "." + key + " 必须填写")
    require(bool(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", data["worker"]["profile"])), "profile 格式无效")
    require(type(data["scope"].get("project_id")) is int and data["scope"]["project_id"] > 0, "项目 ID 无效")
    ids = data["scope"].get("team_ids")
    names = data["scope"].get("team_names")
    require(isinstance(ids, list) and 1 <= len(ids) <= 50 and all(type(i) is int and i > 0 for i in ids)
            and len(ids) == len(set(ids)), "团队 ID 必须为非空、不重复的正整数数组")
    require(isinstance(names, list) and len(names) == len(ids) and all(isinstance(n, str) and n for n in names), "团队名称与 ID 必须逐项对应")
    for section, key in (("tp", "token_env"), ("model", "key_env"), ("feishu", "secret_env")):
        require(bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", data[section][key])), "凭据环境变量名无效")
    require(data["model"]["api_style"] in {"chat_completions", "responses"}, "模型协议无效")
    require(data["model"]["structured_mode"] in {"schema", "json"}, "结构化输出模式无效")
    for key in ("allow_apply", "legacy_quiesced"):
        require(type(data["worker"][key]) is bool, key + " 必须为布尔值")
    require(type(data["transport"]["allow_loopback_http"]) is bool, "loopback 配置必须为布尔值")
    for section, keys in (("transport", ("timeout", "max_bytes", "max_retries")),
                          ("limits", ("page_size", "max_pages", "batch_size", "max_model_calls", "max_features")),
                          ("model", ("max_input_chars", "max_output_tokens"))):
        for key in keys:
            value = data[section][key]
            require(type(value) is int and (value >= 0 if key == "max_retries" else value > 0), key + " 数值无效")
    require(data["transport"]["timeout"] <= 120 and data["transport"]["max_retries"] <= 5, "HTTP 超时或重试配置超限")
    require(data["limits"]["page_size"] <= 1000 and data["limits"]["batch_size"] <= 100, "分页或批次配置超限")
    for section in ("tp", "model", "feishu"):
        url = urlsplit(data[section]["base_url"])
        local = url.hostname in {"localhost", "127.0.0.1", "::1"}
        require(bool(url.hostname) and not url.username and not url.password and not url.query and not url.fragment
                and (url.scheme == "https" or (url.scheme == "http" and local and data["transport"]["allow_loopback_http"])), section + " URL 必须使用无凭据的 HTTPS 地址")
    mentions = data["feishu"].get("mentions", [])
    require(isinstance(mentions, list) and all(isinstance(m, dict) and set(m) == {"open_id", "name"}
            and all(isinstance(v, str) and v for v in m.values()) for m in mentions), "提及对象格式无效")
    data["feishu"]["mentions"] = mentions
    return Config(data["worker"]["profile"], (path.parent / data["worker"]["data_dir"]).resolve(),
                  (path.parent / data["worker"]["runs_dir"]).resolve(), data)


def doctor(config, online=False):
    issues = []
    if sys.version_info < (3, 11):
        issues.append("需要 Python 3.11 或更高版本")
    try:
        ZoneInfo("Asia/Shanghai")
    except Exception:
        issues.append("请安装 requirements-bug-audit-worker.txt 中的 tzdata")
    if online:
        for section in ("tp", "model", "feishu"):
            try: config.secret(section)
            except ConfigError as exc: issues.append(str(exc))
    return {"status": "blocked" if issues else "ready", "profile": config.profile, "issues": issues,
            "online": online, "timezone": "Asia/Shanghai"}
