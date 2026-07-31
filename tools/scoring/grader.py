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


def _one_line_conclusion(card: Scorecard) -> str:
    strongest = max(card.factors, key=lambda factor: factor.score / factor.maximum)
    weakest = min(card.factors, key=lambda factor: factor.score / factor.maximum)
    return (
        f"当前最强项是 {strongest.key} {strongest.label}（{_fmt(strongest.score)}/{_fmt(strongest.maximum)}），"
        f"最大短板是 {weakest.key} {weakest.label}（{_fmt(weakest.score)}/{_fmt(weakest.maximum)}）。"
        f"综合评级为“{card.rating}”，原因：{card.rating_reason}。"
    )


def _framework_conclusion(card: Scorecard, evidence: dict[str, Any]) -> list[str]:
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
    alpha = adjustments["alpha"]
    sentiment = adjustments["sentiment"]
    weakest = min(card.factors, key=lambda factor: factor.score / factor.maximum)
    logic_status = "基本成立" if factors["F1"].score >= 20 and factors["F3"].score >= 12 else "尚未完全成立"
    return [
        f"1. 一句话逻辑：{logic_status}。{name}的核心依据是{track.reason}，但必须以已验证证据为准。",
        f"2. 产业位置：{upstream.reason}；供需判断为{supply.reason}。赛道成立不等于位置合适。",
        f"3. 国产替代与兑现：{chokepoint.reason}；{capex.reason}；{realization.reason}。订单和利润没有共同兑现时，题材不能单独支撑评级。",
        f"4. 位置与市场态度：{price.reason}；Alpha 修正 {alpha.score:+g}，情绪/拥挤度修正 {sentiment.score:+g}。热榜只代表关注，不代表利好。",
        f"5. 安全边际：{financial.reason}；{background.reason}。当前最大短板是 {weakest.key} {weakest.label}（{_fmt(weakest.score)}/{_fmt(weakest.maximum)}）。",
        f"6. 结论：归入“{card.rating}”，{card.rating_reason}。证伪条件是产业需求、订单或资本开支连续两个报告期恶化，或现金流、审计和股东行为明显转坏。",
    ]


def render_report(code: str, name: str, evidence: dict[str, Any], card: Scorecard, requested_modules: tuple[str, ...]) -> str:
    lines = [
        f"总分：{_fmt(card.final_score)}/100｜评级：{card.rating}｜技术信号：{card.signal}",
        "",
        f"# {name or code}（{code}）五层诊断",
        "",
        f"<!-- moda_scorecard: {json.dumps(card.to_dict(), ensure_ascii=False)} -->",
        "",
        "## 一句话结论",
        "",
        _one_line_conclusion(card),
        "",
        "## 五层评分卡",
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
            "",
            "| 子因子 | 得分 | 判断依据 | 来源 | 状态 |",
            "|---|---:|---|---|---|",
        ]
        for item in factor.subfactors:
            reason = item.reason.replace("|", "/")
            lines.append(f"| {item.label} | {_fmt(item.score)}/{_fmt(item.maximum)} | {reason} | {_source_text(item)} | {item.status} |")

    lines += [
        "",
        "## 修正项",
        "",
        f"基础分：{_fmt(card.base_score)}/100｜修正合计：{card.adjustment_score:+g}｜综合分：{_fmt(card.final_score)}/100",
        "",
        "| 修正项 | 分值 | 依据 | 来源 | 状态 |",
        "|---|---:|---|---|---|",
    ]
    for item in card.adjustments:
        sources = "、".join(f"[{source}]" for source in item.sources) if item.sources else "需人工确认"
        lines.append(f"| {item.label} | {item.score:+g} | {item.reason} | {sources} | {item.status} |")

    lines += [
        "",
        "## 舆情、社交热榜与异常推广风险",
        "",
        f"- 个股关注热度：{evidence.get('attention_heat', '需人工确认')}（EastMoney 人气排名归一化）",
        f"- 市场拥挤度：{evidence.get('market_congestion', '需人工确认')}；数据日期 {evidence.get('market_congestion_date', '需人工确认')}；{'有效' if evidence.get('market_congestion_fresh') is True else '过期或缺失，不计分'}",
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
        "> hot-money 当前方法索引实际列出 18 项。仅“量化筛选”和“投资逻辑追踪”可在双重冲突时将 Alpha 向 0 收缩 1 分；其余方法不计分，也不改变 Hard Cap。",
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

    lines += [
        "",
        "## 最终结论",
        "",
    ]
    lines.extend(_framework_conclusion(card, evidence))
    lines += ["", "免责声明：本分析仅供研究参考，不构成投资建议。"]
    return "\n".join(lines) + "\n"


def build_report(code: str, name: str, directories: tuple[str, ...] = REPORTS, since: float = 0,
                 requested_modules: tuple[str, ...] | None = None) -> tuple[str, Scorecard, dict[str, Any]]:
    reports = read_reports(code, directories, since)
    evidence = build_evidence(code, name, reports)
    card = score_evidence(evidence)
    return render_report(code, name, evidence, card, requested_modules or directories), card, evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="moda-v4 structured five-factor scorer")
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
