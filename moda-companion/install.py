#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

COMPANION_ROOT = Path(__file__).resolve().parent
MODA_ROOT = COMPANION_ROOT.parent
IGNORED_NAMES = {
    ".git", ".env", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "knowledge", "output", "skills", "tests", "moda-companion",
}


def _ignore_runtime_files(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if (
            name in IGNORED_NAMES
            or name.startswith("test_")
            or name.endswith((".pyc", ".pyo", ".log", ".bak"))
        ):
            ignored.add(name)
    return ignored


def _copy_tree(source: Path, target: Path, *, force: bool) -> Path:
    if source.resolve() == target.resolve():
        return target
    if target.exists():
        if not force:
            raise FileExistsError(f"目标已存在：{target}。确认更新时使用 --force")
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=_ignore_runtime_files)
    return target


def find_moda_source(explicit: Path | None = None) -> Path:
    candidates = [
        explicit,
        Path(os.environ["MODA_V4_ROOT"]) if os.environ.get("MODA_V4_ROOT") else None,
        MODA_ROOT,
        COMPANION_ROOT.parent / "moda-v4",
        Path("/var/minis/skills/moda-v4"),
        Path.home() / ".agents" / "skills" / "moda-v4",
        Path.home() / ".codex" / "skills" / "moda-v4",
        Path.home() / ".claude" / "skills" / "moda-v4",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        root = candidate.expanduser().resolve()
        if (root / "SKILL.md").is_file() and (root / "tools" / "run_pipeline.py").is_file():
            return root
    raise FileNotFoundError("未找到 moda-v4 源目录；请使用 --moda-root 或设置 MODA_V4_ROOT")


def _copy_moda_skill(source: Path, target: Path, *, force: bool) -> Path:
    source = source.resolve()
    if source == target.resolve():
        return target
    if target.exists():
        if (target / "SKILL.md").is_file() and (target / "tools" / "run_pipeline.py").is_file() and not force:
            return target
        if not force:
            raise FileExistsError(f"目标已存在但不是完整 moda-v4：{target}。确认替换时使用 --force")
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for filename in ("SKILL.md", "work.md", "requirements.txt", ".env.example"):
        source_file = source / filename
        if source_file.exists():
            shutil.copy2(source_file, target / filename)
    shutil.copytree(source / "tools", target / "tools", ignore=_ignore_runtime_files)
    if (source / "agents").exists():
        shutil.copytree(source / "agents", target / "agents", ignore=_ignore_runtime_files)
    (target / "knowledge" / "research").mkdir(parents=True)
    (target / "knowledge" / "research" / ".gitkeep").write_text("", encoding="utf-8")
    return target


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_runtime_config(companion_skill: Path, moda_skill: Path) -> Path:
    path = companion_skill / ".moda-companion-runtime.json"
    path.write_text(
        json.dumps({"version": 1, "moda_root": str(moda_skill.resolve())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def render_codex_agent(companion_skill: Path, moda_skill: Path) -> str:
    return "\n".join(
        [
            'name = "moda_companion"',
            'description = "受莫大公开方法启发的研究与陪伴 Agent；具体 A 股必须调用 moda-v4。"',
            'developer_instructions = """',
            "Read the enabled moda-companion skill before responding. You are inspired by public Moda materials, not the real person.",
            "For a specific A-share, run the companion analyze_a_share.py tool, preserve the complete moda-v4 formal report, then add the defined interpretation.",
            "Never alter research_score, action_rating, evidence coverage, source status, or Hard Caps. Never store secrets or private portfolio details.",
            '"""',
            "",
            "[[skills.config]]",
            f"path = {_toml_string(str(companion_skill / 'SKILL.md'))}",
            "enabled = true",
            "",
            "[[skills.config]]",
            f"path = {_toml_string(str(moda_skill / 'SKILL.md'))}",
            "enabled = true",
            "",
        ]
    )


def install_codex(home: Path, *, force: bool, moda_root: Path | None = None) -> dict[str, str]:
    skills_dir = home / ".agents" / "skills"
    companion_skill = _copy_tree(COMPANION_ROOT, skills_dir / "moda-companion", force=force)
    moda_skill = find_moda_source(moda_root)
    runtime_config = write_runtime_config(companion_skill, moda_skill)
    agent_path = home / ".codex" / "agents" / "moda-companion.toml"
    if agent_path.exists() and not force:
        raise FileExistsError(f"Agent 配置已存在：{agent_path}。确认更新时使用 --force")
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text(render_codex_agent(companion_skill, moda_skill), encoding="utf-8")
    return {
        "companion_skill": str(companion_skill),
        "moda_skill": str(moda_skill),
        "runtime_config": str(runtime_config),
        "agent": str(agent_path),
    }


def render_claude_agent() -> str:
    return """---
name: moda-companion
description: 受莫大公开方法启发的研究与陪伴 Agent。分析具体 A 股时调用 moda-v4，保留正式报告后追加人格解读。
tools: Read, Grep, Glob, Bash
skills:
  - moda-companion
  - moda-v4
memory: user
---

读取并严格遵守 moda-companion Skill。你不是莫大本人。

具体 A 股必须运行 moda-companion/scripts/analyze_a_share.py，完整保留 moda-v4 正式报告，再追加规定格式的人格解读。不得修改研究分、行动评级、覆盖率、来源状态或 Hard Cap。长期记忆只保存非敏感摘要，不保存账户、持仓、成本、交易记录、密码、Token、Cookie 或密钥。
"""


def install_claude(home: Path, *, force: bool, moda_root: Path | None = None) -> dict[str, str]:
    skills_dir = home / ".claude" / "skills"
    companion_skill = _copy_tree(COMPANION_ROOT, skills_dir / "moda-companion", force=force)
    moda_skill = _copy_moda_skill(find_moda_source(moda_root), skills_dir / "moda-v4", force=force)
    runtime_config = write_runtime_config(companion_skill, moda_skill)
    agent_path = home / ".claude" / "agents" / "moda-companion.md"
    if agent_path.exists() and not force:
        raise FileExistsError(f"Agent 配置已存在：{agent_path}。确认更新时使用 --force")
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text(render_claude_agent(), encoding="utf-8")
    return {
        "companion_skill": str(companion_skill),
        "moda_skill": str(moda_skill),
        "runtime_config": str(runtime_config),
        "agent": str(agent_path),
    }


def install_openminis(
    skills_dir: Path,
    memory_dir: Path,
    *,
    force: bool,
    moda_root: Path | None = None,
) -> dict[str, Any]:
    companion_skill = _copy_tree(COMPANION_ROOT, skills_dir / "moda-companion", force=force)
    moda_skill = _copy_moda_skill(find_moda_source(moda_root), skills_dir / "moda-v4", force=force)
    runtime_config = write_runtime_config(companion_skill, moda_skill)

    import importlib.util

    soul_module_path = COMPANION_ROOT / "adapters" / "openminis" / "install_soul.py"
    spec = importlib.util.spec_from_file_location("moda_companion_soul_installer", soul_module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 OpenMinis SOUL 安装器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    soul_result = module.install_soul(
        memory_dir,
        companion_skill / "adapters" / "openminis" / "SOUL.md",
        force=force,
    )
    return {
        "companion_skill": str(companion_skill),
        "moda_skill": str(moda_skill),
        "runtime_config": str(runtime_config),
        "soul": soul_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Moda Companion without replacing the original moda-v4 Skill")
    parser.add_argument("platform", choices=["codex", "claude", "openminis"])
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--moda-root", type=Path)
    parser.add_argument("--skills-dir", type=Path, default=Path("/var/minis/skills"))
    parser.add_argument("--memory-dir", type=Path, default=Path("/var/minis/memory"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.platform == "codex":
        result: Any = install_codex(args.home.expanduser(), force=args.force, moda_root=args.moda_root)
    elif args.platform == "claude":
        result = install_claude(args.home.expanduser(), force=args.force, moda_root=args.moda_root)
    else:
        result = install_openminis(
            args.skills_dir,
            args.memory_dir,
            force=args.force,
            moda_root=args.moda_root,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
