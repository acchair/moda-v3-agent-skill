#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_NAME = ".moda-companion-soul.json"
DEFAULT_LINUX_MEMORY_DIR = Path("/var/minis/memory")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_memory_dir(value: str | Path | None = None) -> Path:
    if value:
        return Path(value).expanduser()
    configured = os.environ.get("MINIS_MEMORY_DIR", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_LINUX_MEMORY_DIR


def load_state(memory_dir: Path) -> dict[str, Any] | None:
    path = memory_dir / STATE_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def install_soul(memory_dir: Path, template: Path, *, force: bool = False) -> dict[str, Any]:
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / "SOUL.md"
    state_path = memory_dir / STATE_NAME
    state = load_state(memory_dir)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if state:
        expected_hash = state.get("installed_hash")
        if target.exists() and expected_hash and sha256(target) != expected_hash and not force:
            raise RuntimeError("当前 SOUL.md 已被用户修改；如确认覆盖，请追加 --force")
        backup_path = state.get("backup_path")
        original_existed = bool(state.get("original_existed"))
    else:
        original_existed = target.exists()
        backup_path = None
        if original_existed:
            backup = memory_dir / f"SOUL.before-moda-companion.{now}.md"
            shutil.copy2(target, backup)
            backup_path = str(backup)

    if target.exists() and force and state and sha256(target) != state.get("installed_hash"):
        snapshot = memory_dir / f"SOUL.before-moda-companion-force.{now}.md"
        shutil.copy2(target, snapshot)

    temporary = memory_dir / "SOUL.md.moda-companion.tmp"
    shutil.copy2(template, temporary)
    temporary.replace(target)
    new_state = {
        "version": 1,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "installed_hash": sha256(target),
        "template_path": str(template),
        "original_existed": original_existed,
        "backup_path": backup_path,
    }
    state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"soul_path": str(target), "backup_path": backup_path, "state_path": str(state_path)}


def restore_soul(memory_dir: Path, *, force: bool = False) -> dict[str, Any]:
    target = memory_dir / "SOUL.md"
    state_path = memory_dir / STATE_NAME
    state = load_state(memory_dir)
    if not state:
        raise FileNotFoundError("没有找到莫大 Agent 的 SOUL 安装状态")
    if target.exists() and sha256(target) != state.get("installed_hash") and not force:
        raise RuntimeError("当前 SOUL.md 已被用户修改；如确认恢复备份，请追加 --force")

    backup_path = Path(state["backup_path"]) if state.get("backup_path") else None
    if state.get("original_existed"):
        if not backup_path or not backup_path.exists():
            raise FileNotFoundError("原 SOUL.md 备份不存在，已停止恢复")
        shutil.copy2(backup_path, target)
    elif target.exists():
        target.unlink()
    state_path.unlink()
    return {"restored": True, "soul_path": str(target), "backup_path": str(backup_path) if backup_path else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Install or restore the Moda Companion OpenMinis SOUL.md")
    parser.add_argument("action", choices=["install", "restore"])
    parser.add_argument("--memory-dir", type=Path)
    parser.add_argument("--template", type=Path, default=Path(__file__).with_name("SOUL.md"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    memory_dir = resolve_memory_dir(args.memory_dir)
    result = install_soul(memory_dir, args.template.resolve(), force=args.force) if args.action == "install" else restore_soul(memory_dir, force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
