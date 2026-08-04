#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def candidate_roots(script_path: Path | None = None) -> list[Path]:
    script_path = (script_path or Path(__file__)).resolve()
    candidates: list[Path] = []
    runtime_config = script_path.parents[1] / ".moda-companion-runtime.json"
    if runtime_config.is_file():
        try:
            configured_root = json.loads(runtime_config.read_text(encoding="utf-8")).get("moda_root")
            if configured_root:
                candidates.append(Path(configured_root).expanduser())
        except (OSError, ValueError, TypeError):
            pass
    explicit = os.environ.get("MODA_V4_ROOT", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend(
        [
            script_path.parents[2],
            Path.cwd(),
            Path("/var/minis/skills/moda-v4"),
            Path.home() / ".agents" / "skills" / "moda-v4",
            Path.home() / ".codex" / "skills" / "moda-v4",
            Path.home() / ".claude" / "skills" / "moda-v4",
        ]
    )
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def find_moda_root(explicit: str | Path | None = None) -> Path:
    candidates = [Path(explicit).expanduser().resolve()] if explicit else candidate_roots()
    for root in candidates:
        if (root / "tools" / "run_pipeline.py").is_file() and (root / "SKILL.md").is_file():
            return root
    locations = "\n".join(f"- {path}" for path in candidates)
    raise FileNotFoundError(
        "未找到 moda-v4。请设置 MODA_V4_ROOT，或将 moda-v4 安装到标准 Skills 目录。\n"
        f"已检查：\n{locations}"
    )


def _resolve_stock(root: Path, query: str) -> tuple[str, str]:
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from tools.stock_resolver import resolve_stock_input

    return resolve_stock_input(query)


def load_analysis(root: Path, code: str, name: str = "") -> dict[str, Any]:
    scorecard_path = root / "knowledge" / "research" / "scorecards" / f"{code}.json"
    report_path = root / "knowledge" / "research" / "scoring" / f"{code}.md"
    pipeline_path = root / "knowledge" / "research" / "pipeline" / f"{code}.json"
    if not scorecard_path.is_file() or not report_path.is_file() or not pipeline_path.is_file():
        raise FileNotFoundError(f"moda-v4 未生成完整结果：{code}")

    payload = json.loads(scorecard_path.read_text(encoding="utf-8"))
    card = payload.get("scorecard") or {}
    evidence = payload.get("evidence") or {}
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    return {
        "code": code,
        "name": name or evidence.get("name") or code,
        "research_score": card.get("research_score"),
        "action_rating": card.get("action_rating"),
        "action_rating_reason": card.get("action_rating_reason"),
        "coverage": card.get("coverage"),
        "unknown_maximum": card.get("unknown_maximum"),
        "signal": card.get("signal"),
        "hard_caps": card.get("hard_caps") or [],
        "pipeline": pipeline,
        "formal_report": report_path.read_text(encoding="utf-8"),
        "report_path": str(report_path),
        "scorecard_path": str(scorecard_path),
        "pipeline_path": str(pipeline_path),
    }


def analyze_a_share(
    query: str,
    refresh: bool = False,
    save: bool = False,
    *,
    moda_root: str | Path | None = None,
    run_pipeline: bool = True,
) -> dict[str, Any]:
    root = find_moda_root(moda_root)
    code, name = _resolve_stock(root, query)
    if run_pipeline:
        command = [sys.executable, str(root / "tools" / "run_pipeline.py"), "--stock", code, "--name", name]
        if refresh:
            command.append("--refresh")
        completed = subprocess.run(command, cwd=root, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"moda-v4 流水线运行失败，退出码 {completed.returncode}")

    result = load_analysis(root, code, name)
    if save:
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        from tools.export_skill_output import export

        result["saved_path"] = str(export(result["formal_report"], code, name))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run moda-v4 and return the stable Agent tool result")
    parser.add_argument("query", help="A-share name or six-digit code")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--moda-root")
    parser.add_argument("--report", action="store_true", help="Print the formal Markdown report instead of JSON")
    args = parser.parse_args()
    result = analyze_a_share(args.query, args.refresh, args.save, moda_root=args.moda_root)
    if args.report:
        print(result["formal_report"])
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
