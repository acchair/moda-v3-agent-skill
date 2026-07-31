from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.scoring.evidence import REPORTS, REPORT_ROOT, SOURCE_LABELS, build_evidence, read_reports
from tools.scoring.institutional_checks import evaluate as evaluate_institutional_methods
from tools.scoring.model import FactorResult, Scorecard, SubfactorResult, score_evidence


OUTPUT_BASE = REPORT_ROOT / "scoring"
SCORECARD_BASE = REPORT_ROOT / "scorecards"


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _progress_bar(value: float, maximum: float, width: int = 20) -> str:
    if width <= 0:
        return ""
    ratio = 0.0 if maximum <= 0 else max(0.0, min(float(value) / float(maximum), 1.0))
    filled = min(width, int(ratio * width + 0.5))
    return "█" * filled + "░" * (width - filled)


def _factor_status(factor: FactorResult) -> str:
    statuses = {item.status for item in factor.subfactors}
    if statuses == {"已验证"}:
        return "已验证"
    if all(item.status == "需人工确认" for item in factor.subfactors):
        return "需人工确认"
    return "部分覆盖"


def _factor_summary(factor: FactorResult) -> str:
    ranked = sorted(factor.subfactors, key=lambda item: (item.score / item.maximum, item.score), reverse=True)
    positives = [item for item in ranked if item.score > 0]
    missing = [item.label for item in factor.subfactors if item.status == "需人工确认"]
    if positives:
        summary = f"{positives[0].label}是当前主要得分项"
    else:
        summary = "没有已验证的正向得分项"
    if missing:
        summary += f"；{missing[0]}需人工确认"
    return summary


def _source_text(item: SubfactorResult) -> str:
    return "、".join(f"[{source}]" for source in item.sources) if item.sources else "需人工确认"


def _sleep_checks(card: Scorecard) -> list[tuple[str, str, str]]:
    factors = {factor.key: factor for factor in card.factors}
    subfactors = {item.key: item for factor in card.factors for item in factor.subfactors}

    financial = subfactors["financial_safety"]
    financing_status = "通过" if financial.score >= 3.75 else "不通过" if financial.status != "需人工确认" else "需人工确认"
    announcement_status = "通过" if subfactors["business_match"].score >= 3 and subfactors["realization"].score >= 2 else "需人工确认"
    finance_status = "通过" if financial.score >= 3.75 else "不通过" if financial.status != "需人工确认" else "需人工确认"
    shareholder = subfactors["controller_action"]
    shareholder_status = "通过" if shareholder.score >= 4 else "不通过" if shareholder.status != "需人工确认" else "需人工确认"
    industry_status = "通过" if factors["F1"].score >= 20 else "不通过" if factors["F1"].score < 15 else "需人工确认"
    return [
        ("不融资也能拿", financing_status, financial.reason),
        ("不靠单一公告续命", announcement_status, "检查主营匹配和订单/产能兑现是否同时成立"),
        ("财务不容易暴雷", finance_status, financial.reason),
        ("股东不持续伤害小股东", shareholder_status, shareholder.reason),
        ("产业逻辑至少 1-3 年不证伪", industry_status, f"F1 得分 {_fmt(factors['F1'].score)}/30"),
        ("跌 20% 后仍能持有", "需人工确认", "需要结合基本面证据与个人风险承受能力判断"),
    ]


def _framework_conclusion(card: Scorecard, evidence: dict[str, Any]) -> list[tuple[str, str]]:
    subfactors = {item.key: item for factor in card.factors for item in factor.subfactors}
    factors = {factor.key: factor for factor in card.factors}
    adjustments = {item.key: item for item in card.adjustments}
    name = str(evidence.get("name") or evidence.get("code") or "该标的")
    track = subfactors["era_track"]
    upstream = subfactors["upstream"]
    supply = subfactors["supply_gap"]
    chokepoint = subfactors["chokepoint"]
    capex = subfactors["capex_wave"]
    realization = subfactors["realization"]
    price = subfactors["price_position"]
    financial = subfactors["financial_safety"]
    background = subfactors["background"]
    institutional = adjustments["institutional_direction"]
    technical = adjustments["technical_structure"]
    sentiment = adjustments["sentiment"]
    weakest = min(card.factors, key=lambda factor: factor.score / factor.maximum)
    logic_status = "基本成立" if factors["F1"].score >= 20 and factors["F3"].score >= 12 else "尚未完全成立"
    prosperity = evidence.get("industry_prosperity_status", "需人工确认")
    return [
        ("1. 一句话逻辑", f"{logic_status}。{name}的核心依据是{track.reason}，但必须以已验证证据为准。"),
        ("2. 产业位置", f"{upstream.reason}；供需判断为{supply.reason}；行业景气交叉验证为{prosperity}。赛道成立不等于位置合适。"),
        ("3. 国产替代与兑现", f"{chokepoint.reason}；{capex.reason}；{realization.reason}。订单和利润没有共同兑现时，题材不能单独支撑评级。"),
        ("4. 位置与市场态度", f"{price.reason}；机构方向 {institutional.score:g}/2，技术结构 {technical.score:g}/4，情绪/拥挤度 {sentiment.score:g}/2。热榜只代表关注，不代表利好。"),
        ("5. 安全边际", f"{financial.reason}；{background.reason}。当前最大短板是 {weakest.key} {weakest.label}（{_fmt(weakest.score)}/{_fmt(weakest.maximum)}）。"),
        ("6. 最终判断", f"归入“{card.rating}”，{card.rating_reason}。证伪条件是产业需求、订单或资本开支连续两个报告期恶化，或现金流、审计和股东行为明显转坏。"),
    ]


def _technical_analysis(evidence: dict[str, Any]) -> list[str]:
    indicators = evidence.get("technical_indicators") if isinstance(evidence.get("technical_indicators"), dict) else {}
    chan = evidence.get("chan_structure") if isinstance(evidence.get("chan_structure"), dict) else {}

    def value(key: str, field: str = "value", suffix: str = "") -> str:
        item = indicators.get(key, {})
        raw = item.get(field) if isinstance(item, dict) else None
        return f"{raw}{suffix}" if raw is not None else "需人工确认"

    chan_reading = "需人工确认"
    if chan.get("status") == "可分析":
        chan_reading = f"{chan.get('latest_direction', '方向未定')}；{chan.get('relation', '中枢未形成')}"
    rows = [
        ("缠论（结构）", chan_reading, "结构偏多" if chan.get("latest_direction") == "向上" else "结构偏空" if chan.get("latest_direction") == "向下" else "方向未定"),
        ("OBV", value("obv"), indicators.get("obv", {}).get("state", "需人工确认")),
        ("30日BIAS", value("bias30", suffix="%"), indicators.get("bias30", {}).get("state", "需人工确认")),
        ("MACD", value("macd"), indicators.get("macd", {}).get("state", "需人工确认")),
        ("BOLL", f"位置 {value('boll')}", indicators.get("boll", {}).get("state", "需人工确认")),
        ("ATR", value("atr", "pct", "%"), indicators.get("atr", {}).get("state", "需人工确认")),
        ("DMI", f"ADX {value('dmi', 'adx')}", indicators.get("dmi", {}).get("state", "需人工确认")),
        ("RSI", value("rsi"), indicators.get("rsi", {}).get("state", "需人工确认")),
        ("WR", value("wr"), indicators.get("wr", {}).get("state", "需人工确认")),
    ]
    current_price = chan.get("current_price", "需人工确认")
    support = chan.get("support", "需人工确认")
    resistance = chan.get("resistance", "需人工确认")
    structure_score = evidence.get("technical_structure_score", "需人工确认")
    structure_reason = evidence.get("technical_structure_reason", "技术证据不足，需人工确认")
    lines = [
        "## 技术分析（easy-tdx 日 K）",
        "",
        (
            f"- 当前价格：{current_price}；支撑位：{support}；压力位：{resistance}。"
        ),
        f"- 综合判断：技术结构 {structure_score}/4；{structure_reason}；交易信号 {evidence.get('technical_signal', '需人工确认')}。",
        "- 缠论说明：识别日线分型、笔和最近三笔重叠区间，不替代完整多级别缠论递归。",
        "",
        "| 指标 | 当前读数 | 当前评价 |",
        "|---|---|---|",
    ]
    lines.extend(f"| {indicator} | {reading} | {comment} |" for indicator, reading, comment in rows)
    return lines


def _industry_prosperity_analysis(evidence: dict[str, Any]) -> list[str]:
    mapping = evidence.get("industry_mapping") if isinstance(evidence.get("industry_mapping"), dict) else {}
    financial = evidence.get("industry_financial_signal") if isinstance(evidence.get("industry_financial_signal"), dict) else {}
    supply = evidence.get("industry_supply_signal") if isinstance(evidence.get("industry_supply_signal"), dict) else {}
    market = evidence.get("industry_market_signal") if isinstance(evidence.get("industry_market_signal"), dict) else {}
    conflicts = evidence.get("industry_prosperity_conflicts") if isinstance(evidence.get("industry_prosperity_conflicts"), list) else []

    def pct(value: Any) -> str:
        try:
            return f"{float(value):.2%}"
        except (TypeError, ValueError):
            return "需人工确认"

    def ratio(value: Any) -> str:
        try:
            return f"{float(value):.2f}x"
        except (TypeError, ValueError):
            return "需人工确认"
    return [
        "## 行业景气度交叉验证",
        "",
        f"- 行业映射：{mapping.get('matched_token', '需人工确认')} → {mapping.get('sw_second_name', '需人工确认')} → {mapping.get('sw_first_name', '需人工确认')}（{mapping.get('status', '不可用')}）",
        f"- 综合状态：{evidence.get('industry_prosperity_status', '不可用')}；覆盖：{evidence.get('industry_prosperity_coverage', '不可用')}；报告期：{evidence.get('industry_prosperity_period', '需人工确认')}。本项只交叉验证，不独立加分。",
        "",
        "| 层面 | 状态 | 当前判断 |",
        "|---|---|---|",
        f"| 财务确认 | {financial.get('status', '不可用')} | 可用 {financial.get('available_metrics', 0)}/6；当期正向 {financial.get('current_positive', 0)}；边际正向 {financial.get('delta_positive', 0)} |",
        f"| 供需先行 | {supply.get('status', '不可用')} | 商品 {supply.get('commodity') or '未匹配'}；证据 {supply.get('evidence_count') or 0} 类；PPI 同比 {supply.get('ppi_yoy', '需人工确认')} |",
        f"| 市场验证 | {market.get('status', '不可用')} | 20日相对沪深300 {pct(market.get('relative_to_csi300_20d'))}；成交活跃比 {ratio(market.get('turnover_activity_ratio'))} |",
        "",
        "- 冲突检查：" + ("；".join(conflicts) if conflicts else "未发现已覆盖指标之间的明确冲突。"),
        "- 来源边界：乐咕为 B 级聚合数据；雪球文章仅作 C 级方法线索，均不能单独确认产业景气。",
    ]


def render_report(code: str, name: str, evidence: dict[str, Any], card: Scorecard, requested_modules: tuple[str, ...]) -> str:
    adjustments = {item.key: item for item in card.adjustments}
    lines = [
        f"# {name or code}（{code}）六层诊断",
        "",
        f"<!-- moda_scorecard: {json.dumps(card.to_dict(), ensure_ascii=False)} -->",
        "",
        "## 综合得分",
        "",
        "```text",
        f"  {_fmt(card.final_score)} / 100  [{_progress_bar(card.final_score, 100)}]",
        f"  评级：{card.rating}  |  技术信号：{card.signal}",
        (
            f"  F6修正：机构方向 {adjustments['institutional_direction'].score:g}/2  |  "
            f"技术结构 {adjustments['technical_structure'].score:g}/4  |  "
            f"情绪/拥挤度 {adjustments['sentiment'].score:g}/2  |  "
            f"风口催化 {adjustments['catalyst'].score:g}/2"
        ),
        "```",
        "",
        "## 一句话结论与最终判断",
        "",
    ]
    for title, body in _framework_conclusion(card, evidence):
        lines += [f"**{title}**", "", body, ""]
    lines.extend(_technical_analysis(evidence))
    lines += [""]
    lines.extend(_industry_prosperity_analysis(evidence))
    lines += [
        "",
        "## 六层图形概览",
        "",
        "```text",
    ]
    for factor in card.factors:
        lines.append(
            f"{factor.key} [{_progress_bar(factor.score, factor.maximum)}] "
            f"{_fmt(factor.score):>5}/{_fmt(factor.maximum):<3}  {factor.label}"
        )
    lines += [
        "```",
        "",
        "## 六层评分卡",
        "",
        "| 因子 | 得分 | 核心判断 | 状态 |",
        "|---|---:|---|---|",
    ]
    for factor in card.factors:
        lines.append(f"| {factor.key} {factor.label} | {_fmt(factor.score)}/{_fmt(factor.maximum)} | {_factor_summary(factor)} | {_factor_status(factor)} |")

    for factor in card.factors:
        lines += [
            "",
            f"## {factor.key} {factor.label}（{_fmt(factor.score)}/{_fmt(factor.maximum)}）",
        ]
        if factor.key == "F6":
            lines += [
                "",
                f"非修正基础分：{_fmt(card.base_score)}/90｜F6 修正项：{_fmt(card.adjustment_score)}/10｜综合分：{_fmt(card.final_score)}/100",
                "",
                "> F6 是独立的第六层，已计入综合分，不再二次加分。",
            ]
        lines += [
            "",
            "| 子因子 | 得分 | 判断依据 | 来源 | 状态 |",
            "|---|---:|---|---|---|",
        ]
        for item in factor.subfactors:
            reason = item.reason.replace("|", "/")
            lines.append(f"| {item.label} | {_fmt(item.score)}/{_fmt(item.maximum)} | {reason} | {_source_text(item)} | {item.status} |")

    lines += [
        "",
        "## 舆情、社交热榜与异常推广风险",
        "",
        f"- 个股关注热度：{evidence.get('attention_heat', '需人工确认')}（EastMoney 人气排名归一化）",
        f"- 市场拥挤度：{evidence.get('market_congestion', '需人工确认')}；今日检查 {evidence.get('market_congestion_checked_date', '需人工确认')}；源数据日期 {evidence.get('market_congestion_date', '需人工确认')}；{'有效' if evidence.get('market_congestion_fresh') is True else '过期或缺失，不计分'}",
        f"- 社交热榜：命中 {evidence.get('social_hot_hits', '需人工确认')} 条，覆盖 {evidence.get('social_platform_hits', '需人工确认')} 个平台",
        f"- 异常推广风险：{evidence.get('trap_risk_level', '需人工确认')}；命中 {evidence.get('trap_signal_count', '需人工确认')}/8",
        "",
        "| 异常推广信号 | 结果 | 证据 |",
        "|---|---|---|",
    ]
    for item in evidence.get("trap_checks", []):
        lines.append(f"| {item['signal']} | {'命中' if item['hit'] else '未命中'} | {item['evidence']} |")
    if not evidence.get("trap_checks"):
        lines.append("| 8 类信号 | 需人工确认 | 社交热榜或交叉证据未完成 |")

    lines += [
        "",
        "## Hard Cap 检查",
        "",
        "| 条件 | 本次结果 | 对评级的影响 |",
        "|---|---|---|",
    ]
    for item in card.hard_caps:
        lines.append(f"| {item['condition']} | {item['result']} | {item['cap']} |")

    lines += [
        "",
        "## 机构方法交叉验证",
        "",
        "> hot-money 当前方法索引实际列出 18 项。本评分只使用“量化选股筛选”和“投资逻辑追踪”判断机构方向，合计 2 分；技术结构由 easy-tdx 独立评分，其他方法不计分，也不改变 Hard Cap。",
        "",
        "| 方法 | 适用价值 | 本次状态 | 说明 |",
        "|---|---|---|---|",
    ]
    for item in evaluate_institutional_methods(evidence, card):
        lines.append(f"| {item['method']} | {item['usefulness']} | {item['status']} | {item['reason']} |")

    lines += ["", "## 睡得着检查", ""]
    for label, status, reason in _sleep_checks(card):
        lines.append(f"- {label}：{status}。{reason}")

    lines += [
        "",
        "## 动态纠错触发器",
        "",
        "- 产业证伪：行业需求、供需方向、订单或下游资本开支连续两个报告期恶化。",
        "- 公司证伪：营收与利润同时转负、经营现金流持续为负，或出现非标审计和重大持续经营风险。",
        "- 估值过热：三年价格分位超过 80%，且市场拥挤度达到 80% 以上；或 TTM PE 超过同行中位数 50%。",
        "- 股东恶化：控股股东或实控人减持、质押比例明显上升，或未来半年解禁比例超过 10%。",
        "- 同链高切低：同产业链出现 F1/F3 不弱、但 F5 得分高出 4 分以上的标的时重新比较。",
        "",
        "## 数据覆盖与待确认",
        "",
    ]
    completed = evidence.get("completed_modules", [])
    missing = [module for module in requested_modules if module not in completed]
    source_names = [f"{module} [{SOURCE_LABELS.get(module, module)}]" for module in completed]
    lines.append("- 已完成模块：" + ("、".join(source_names) if source_names else "无"))
    lines.append("- 失败或缺失模块：" + ("、".join(missing) if missing else "无"))
    missing_items = [item.label for factor in card.factors for item in factor.subfactors if item.status == "需人工确认"]
    lines.append("- 需人工确认：" + ("、".join(dict.fromkeys(missing_items)) if missing_items else "无"))

    lines += ["", "免责声明：本分析仅供研究参考，不构成投资建议。"]
    return "\n".join(lines) + "\n"


def build_report(code: str, name: str, directories: tuple[str, ...] = REPORTS, since: float = 0,
                 requested_modules: tuple[str, ...] | None = None) -> tuple[str, Scorecard, dict[str, Any]]:
    reports = read_reports(code, directories, since)
    evidence = build_evidence(code, name, reports)
    card = score_evidence(evidence)
    return render_report(code, name, evidence, card, requested_modules or directories), card, evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="moda-v4 structured six-factor scorer")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--sources", default=",".join(REPORTS))
    parser.add_argument("--requested-sources", default="")
    parser.add_argument("--since", type=float, default=0)
    args = parser.parse_args()
    code = args.stock.strip()
    if len(code) != 6 or not code.isdigit():
        parser.error("--stock must be a 6-digit A-share code")
    directories = tuple(source for source in args.sources.split(",") if source in REPORTS)
    requested = tuple(source for source in args.requested_sources.split(",") if source in REPORTS) or directories
    report, card, evidence = build_report(code, args.name or code, directories, args.since, requested)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    SCORECARD_BASE.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_BASE / f"{code}.md"
    scorecard_path = SCORECARD_BASE / f"{code}.json"
    report_path.write_text(report, encoding="utf-8")
    scorecard_path.write_text(json.dumps({"evidence": evidence, "scorecard": card.to_dict()}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)


if __name__ == "__main__":
    main()
