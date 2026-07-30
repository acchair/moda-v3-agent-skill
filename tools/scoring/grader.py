from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


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

REPORTS = ("finance_data", "tdx_analysis", "announcements")
SOURCE_LABELS = {
    "finance_data": "easy_tdx/TDX + easy_tdx/Sina",
    "tdx_analysis": "easy_tdx/TDX",
    "announcements": "easy_tdx/CNINFO + AKShare/CNINFO",
}
FACTOR_SOURCES = {
    "F1 产业趋势与资本开支": ("finance_data", "announcements"),
    "F2 股东与筹码": ("announcements",),
    "F3 生存能力与龙头": ("finance_data", "announcements"),
    "F4 利润兑现路径": ("finance_data", "announcements"),
    "F5 低位与困境反转": ("finance_data", "tdx_analysis"),
}


def _read_reports(code: str, directories: tuple[str, ...] = REPORTS, since: float = 0) -> tuple[str, list[str]]:
    texts: list[str] = []
    sources: list[str] = []
    for directory in directories:
        path = REPORT_ROOT / directory / f"{code}.md"
        if path.exists() and path.stat().st_mtime >= since - 1:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
            sources.append(directory)
    return "\n".join(texts).lower(), sources


def _score_factor(text: str, maximum: int, keywords: tuple[str, ...]) -> tuple[int, list[str]]:
    lines = [line.strip().lower() for line in text.splitlines()
             if line.strip() and not line.lstrip().startswith(("#", "- 说明", ">", "免责声明"))
             and not any(header in line for header in ("| 指标 |", "| 日期 |", "| 因子 |", "|---"))]
    negative = ("无数据", "暂无", "未提供", "获取失败", "失败", "需人工确认", "无同行估值")
    hits = [word for word in keywords if any(word in line and not any(mark in line for mark in negative) for line in lines)]
    # ponytail: keyword evidence is conservative; replace with source-specific parsers only when stable schemas exist.
    return min(maximum, len(hits) * 3), hits


def _extract_metrics(text: str) -> dict[str, float]:
    match = re.search(r"<!-- moda_metrics: (\{.*?\}) -->", text)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError):
        return {}


def _apply_metric_guard(factor: str, score: int, metrics: dict[str, float]) -> tuple[int, str]:
    if factor.startswith("F3") and metrics.get("debt_ratio", 0) > 0.7:
        return min(score, 6), "资产负债率高于70%，F3封顶6分"
    if factor.startswith("F3") and metrics.get("net_profit", 0) < 0:
        return min(score, 6), "归母净利润为负，F3封顶6分"
    if factor.startswith("F3") and metrics.get("cash_to_debt") is not None and metrics["cash_to_debt"] < 0.1:
        return min(score, 8), "货币资金覆盖负债不足10%，F3封顶8分"
    if factor.startswith("F4"):
        revenue_yoy, profit_yoy = metrics.get("revenue_yoy"), metrics.get("profit_yoy")
        if revenue_yoy is not None and profit_yoy is not None and revenue_yoy < 0 and profit_yoy < 0:
            return min(score, 6), "营收和归母净利润同比均下降，F4封顶6分"
        if metrics.get("operating_cashflow", 0) < 0:
            return min(score, 9), "经营现金流为负，F4封顶9分"
    if factor.startswith("F5"):
        pe, peer = metrics.get("pe_ttm"), metrics.get("peer_pe_ttm_median")
        if pe is not None and pe <= 0:
            return min(score, 4), "TTM PE为负或无效，F5封顶4分"
        if pe and peer and pe > peer * 1.5:
            return min(score, 6), "TTM PE高于同行中位数50%，F5封顶6分"
    return score, ""


def _has_control_reduction(text: str) -> bool:
    for match in re.finditer(r"(?:控股股东|实际控制人|实控人)[^\n。；]{0,35}减持|减持[^\n。；]{0,35}(?:控股股东|实际控制人|实控人)", text):
        context = match.group(0)
        if not re.search(r"未发生|不存在|无控股|无实际控制人", context):
            return True
    return False


def _rating(score: int, text: str, name: str = "", factor_scores: dict[str, int] | None = None) -> tuple[str, str]:
    normalized_name = name.upper().replace(" ", "")
    if normalized_name.startswith(("ST", "*ST")) or any(term in text for term in ("退市风险警示", "终止上市决定")):
        return "不碰", "ST/退市风险"
    if _has_control_reduction(text):
        return "学习仓", "控股股东或实控人减持"
    if factor_scores and (factor_scores.get("F1", 100) < 15 or factor_scores.get("F3", 100) < 8):
        return "学习仓", "F1低于15或F3低于8"
    if score >= 85:
        return "根", "评分达到 A 档"
    if score >= 70:
        return "矛", "评分达到 B 档"
    if score >= 55:
        return "学习仓", "评分达到 C 档"
    return "不碰", "证据不足或评分偏低"


def build_report(code: str, name: str, directories: tuple[str, ...] = REPORTS, since: float = 0) -> str:
    text, sources = _read_reports(code, directories, since)
    metrics = _extract_metrics(text)
    rows: list[str] = []
    factor_scores: dict[str, int] = {}
    total = 0
    for factor, (maximum, keywords) in FACTORS.items():
        score, hits = _score_factor(text, maximum, keywords)
        score, guard = _apply_metric_guard(factor, score, metrics)
        total += score
        status = "结构化校验" if guard else "有自动证据" if hits else "需人工确认"
        logic = guard or (f"关键词: {', '.join(hits)}" if hits else "无自动证据")
        factor_sources = ", ".join(SOURCE_LABELS[source] for source in FACTOR_SOURCES[factor] if source in sources) or "-"
        factor_scores[factor.split()[0]] = score
        rows.append(f"| {factor} | {score}/{maximum} | {logic} | {factor_sources} | {status} |")

    rating, reason = _rating(total, text, name, factor_scores)
    source_text = ", ".join(SOURCE_LABELS[source] for source in sources if source in SOURCE_LABELS) or "无可用报告"
    return "\n".join([
        f"# 五层评分: {name or code}({code})",
        "",
        f"- 总分: {total}/100",
        f"- 评级: {rating}",
        f"- 评级原因: {reason}",
        f"- 数据来源: {source_text}",
        "- 说明: 关键词建立基础分，结构化财务指标负责限制与实际数据矛盾的高分；未覆盖因子必须人工确认。",
        "",
        "| 因子 | 分数 | 判断逻辑 | 数据来源 | 状态 |",
        "|---|---:|---|---|---|",
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
    parser.add_argument("--sources", default=",".join(REPORTS))
    parser.add_argument("--since", type=float, default=0)
    args = parser.parse_args()
    code = args.stock.strip()
    if len(code) != 6 or not code.isdigit():
        parser.error("--stock must be a 6-digit A-share code")
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{code}.md"
    directories = tuple(source for source in args.sources.split(",") if source in REPORTS)
    path.write_text(build_report(code, args.name or code, directories, args.since), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
