from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
RESEARCH_ROOT = ROOT / "knowledge" / "research"

REPORT_DIRS = {
    "finance_data": "基础行情",
    "tdx_analysis": "技术分析",
    "announcements": "公告互动",
    "scoring": "五层评分",
}


def _valid_code(code: str) -> str:
    value = str(code or "").strip()
    return value if re.fullmatch(r"\d{6}", value) else ""


def read_report(code: str, module: str) -> dict[str, Any]:
    code = _valid_code(code)
    path = RESEARCH_ROOT / module / f"{code}.md"
    if not code or module not in REPORT_DIRS or not path.exists():
        return {"module": module, "title": REPORT_DIRS.get(module, module), "exists": False, "content": ""}
    return {
        "module": module,
        "title": REPORT_DIRS[module],
        "exists": True,
        "path": str(path),
        "content": path.read_text(encoding="utf-8", errors="replace"),
    }


def read_reports(code: str) -> list[dict[str, Any]]:
    return [read_report(code, module) for module in REPORT_DIRS]


def extract_score_summary(code: str) -> dict[str, Any]:
    code = _valid_code(code)
    content = read_report(code, "scoring").get("content", "")
    summary: dict[str, Any] = {
        "code": code,
        "score": None,
        "rating": "",
        "signal": "",
        "reason": "",
        "sources": [],
        "factors": [],
        "status": "missing",
    }
    if not content:
        return summary

    score = re.search(r"^-\s*总分:\s*(\d+(?:\.\d+)?)\s*/\s*100", content, re.MULTILINE)
    rating = re.search(r"^-\s*评级:\s*(.+)$", content, re.MULTILINE)
    reason = re.search(r"^-\s*评级原因:\s*(.+)$", content, re.MULTILINE)
    sources = re.search(r"^-\s*数据来源:\s*(.+)$", content, re.MULTILINE)
    if score:
        summary["score"] = float(score.group(1))
    if rating:
        summary["rating"] = rating.group(1).strip()
        summary["signal"] = summary["rating"]
    if reason:
        summary["reason"] = reason.group(1).strip()
    if sources:
        text = sources.group(1).strip()
        summary["sources"] = [] if text == "无可用报告" else [item.strip() for item in text.split(",") if item.strip()]

    for line in content.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) not in (4, 5) or not re.match(r"^F[1-5]\b", cells[0]):
            continue
        factor_score = re.fullmatch(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", cells[1])
        summary["factors"].append(
            {
                "factor": cells[0],
                "score": float(factor_score.group(1)) if factor_score else None,
                "maximum": float(factor_score.group(2)) if factor_score else None,
                "evidence": cells[2],
                "source": cells[3] if len(cells) == 5 else "",
                "status": cells[4] if len(cells) == 5 else cells[3],
            }
        )
    summary["status"] = "ready"
    summary["coverage"] = round(sum(1 for factor in summary["factors"] if factor["score"] is not None) / 5, 2)
    return summary


def analyzed_codes() -> list[str]:
    directory = RESEARCH_ROOT / "scoring"
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("[0-9][0-9][0-9][0-9][0-9][0-9].md"))
