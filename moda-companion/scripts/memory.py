#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENSITIVE_KEYWORDS = {
    "password", "passwd", "密码", "token", "cookie", "api_key", "apikey", "secret",
    "private_key", "私钥", "身份证", "手机号", "phone", "email", "账户", "账号",
    "持仓数量", "成本价", "交易记录", "银行卡", "database_url",
}
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def default_memory_path() -> Path:
    configured = os.environ.get("MODA_COMPANION_MEMORY", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".moda-companion" / "memory.json"


def _reject_sensitive(key: str, value: Any) -> None:
    normalized = key.strip().lower()
    text = json.dumps(value, ensure_ascii=False)
    if any(word in normalized for word in SENSITIVE_KEYWORDS):
        raise ValueError("拒绝保存敏感字段")
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise ValueError("内容疑似包含密钥、Token 或私钥，已拒绝保存")


def load_memory(path: Path | None = None) -> dict[str, Any]:
    target = path or default_memory_path()
    if not target.exists():
        return {"version": 1, "entries": {}}
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        raise ValueError("记忆文件格式无效")
    return payload


def remember(key: str, value: Any, tags: list[str] | None = None, path: Path | None = None) -> dict[str, Any]:
    _reject_sensitive(key, value)
    target = path or default_memory_path()
    payload = load_memory(target)
    entry = {
        "value": value,
        "tags": sorted(set(tags or [])),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["entries"][key] = entry
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return entry


def forget(key: str, path: Path | None = None) -> bool:
    target = path or default_memory_path()
    payload = load_memory(target)
    removed = payload["entries"].pop(key, None) is not None
    if removed:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Store only non-sensitive Moda Companion memories")
    parser.add_argument("--path", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    save_parser = subparsers.add_parser("remember")
    save_parser.add_argument("key")
    save_parser.add_argument("value")
    save_parser.add_argument("--tag", action="append", default=[])
    forget_parser = subparsers.add_parser("forget")
    forget_parser.add_argument("key")
    subparsers.add_parser("list")
    args = parser.parse_args()

    if args.command == "remember":
        result: Any = remember(args.key, args.value, args.tag, args.path)
    elif args.command == "forget":
        result = {"removed": forget(args.key, args.path)}
    else:
        result = load_memory(args.path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
