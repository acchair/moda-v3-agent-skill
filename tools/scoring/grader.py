from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "knowledge" / "research"
OUTPUT_BASE = REPORT_ROOT / "scoring"

FACTORS = {
    "F1 产业趋势与资本开支": (30, ("行业", "政策", "产能", "资本开支", "供需", "订单")),
    "F2 股东与筹码": (15, ("股东", "增持", "减持", "质押", "解禁", "户数")),
    "F3 生存能力与龙头": (20, ("营收", "净利润", "现金", "负债", "龙头", "审计")),
    "F4 利润兑现路径": (15, ("订单", "产能", "主营", "收入", "利润", "公告")),
    "F5 低位与困境反转": (20, ("市盈率", "市净率", "低位", "估值", "反转", "价格")),
}

REPORTS = ("finance_data", "finance_deep", "tdx_analysis", "announcements")


def _read_reports(code: str) -> tuple[str, list[str]]:
    texts: list[str] = []
    sources: list[str] = []
    for directory in REPORTS:
        path = REPORT_ROOT / directory / f"{code}.md"
        if path.exists():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
            sources.append(directory)
    return "\n".join(texts).lower(), sources


def _score_factor(text: str, maximum: int, keywords: tuple[str, ...]) -> tuple[int, list[str]]:
    hits = [word for word in keywords if word in text]
    # ponytail: keyword evidence is conservative; replace with source-specific parsers only when stable schemas exist.
    return min(maximum, len(hits) * 3), hits


def _rating(score: int, text: str) -> tuple[str, str]:
    if "st" in text or "退市" in text:
        return "不碰", "ST/退市风险"
    if "减持" in text:
        return "学习仓", "控股股东或实控人减持"
    if score >= 85:
        return "根", "评分达到 A 档"
    if score >= 70:
        return "矛", "评分达到 B 档"
    if score >= 55:
        return "学习仓", "评分达到 C 档"
    return "不碰", "证据不足或评分偏低"


def build_report(code: str, name: str) -> str:
    text, sources = _read_reports(code)
    rows: list[str] = []
    total = 0
    for factor, (maximum, keywords) in FACTORS.items():
        score, hits = _score_factor(text, maximum, keywords)
        total += score
        status = "有自动证据" if hits else "需人工确认"
        rows.append(f"| {factor} | {score}/{maximum} | {', '.join(hits) or '-'} | {status} |")

    rating, reason = _rating(total, text)
    source_text = ", ".join(sources) if sources else "无可用报告"
    return "\n".join([
        f"# 五层评分: {name or code}({code})",
        "",
        f"- 总分: {total}/100",
        f"- 评级: {rating}",
        f"- 评级原因: {reason}",
        f"- 数据来源: {source_text}",
        "- 说明: 自动分数仅统计报告中的可验证关键词，未覆盖的因子必须人工确认。",
        "",
        "| 因子 | 分数 | 自动证据 | 状态 |",
        "|---|---:|---|---|",
        *rows,
        "",
        "## 动态纠错触发器",
        "",
        "- 产业证伪: 行业需求、政策或资本开支逻辑恶化。",
        "- 公司证伪: 财务、订单、审计或公告出现重大负面变化。",
        "- 估值过热: 价格和估值显著偏离基本面。",
        "- 股东恶化: 减持、质押或解禁压力上升。",
    ]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="moda-v3 five-factor scorer")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    code = args.stock.strip()
    if len(code) != 6 or not code.isdigit():
        parser.error("--stock must be a 6-digit A-share code")
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{code}.md"
    path.write_text(build_report(code, args.name or code), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
