from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "SKILL.md"
ANALYZE_TOOL = ROOT / "scripts" / "analyze_a_share.py"
MEMORY_TOOL = ROOT / "scripts" / "memory.py"


def _load_analysis_module():
    spec = importlib.util.spec_from_file_location("moda_companion_analysis", ANALYZE_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 moda-companion 分析工具")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_agent():
    try:
        from agents import Agent, function_tool
    except ImportError as exc:
        raise RuntimeError("请先安装 OpenAI Agents SDK：pip install openai-agents") from exc

    analysis_module = _load_analysis_module()
    memory_spec = importlib.util.spec_from_file_location("moda_companion_memory", MEMORY_TOOL)
    if memory_spec is None or memory_spec.loader is None:
        raise RuntimeError("无法加载 moda-companion 记忆工具")
    memory_module = importlib.util.module_from_spec(memory_spec)
    memory_spec.loader.exec_module(memory_module)

    @function_tool
    def analyze_a_share(query: str, refresh: bool = False, save: bool = False) -> dict[str, Any]:
        """Run the authoritative moda-v4 A-share pipeline and return its formal report and scorecard summary."""
        return analysis_module.analyze_a_share(query, refresh, save)

    @function_tool
    def remember_preference(key: str, value: str) -> dict[str, Any]:
        """Store a reusable non-sensitive user preference or public research note."""
        return memory_module.remember(key, value)

    @function_tool
    def forget_preference(key: str) -> dict[str, bool]:
        """Delete a previously stored Moda Companion memory by key."""
        return {"removed": memory_module.forget(key)}

    instructions = SKILL.read_text(encoding="utf-8")
    return Agent(
        name="莫大 Agent",
        instructions=instructions,
        tools=[analyze_a_share, remember_preference, forget_preference],
    )


agent = build_agent()
