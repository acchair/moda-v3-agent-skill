from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from tools.scoring.alpha_crosscheck import evaluate as evaluate_alpha_crosscheck


RATING_ORDER = ("不碰", "学习仓", "矛", "根")


@dataclass(frozen=True)
class SubfactorResult:
    key: str
    label: str
    score: float
    maximum: float
    status: str
    reason: str
    sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorResult:
    key: str
    label: str
    score: float
    maximum: float
    subfactors: tuple[SubfactorResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "score": self.score,
            "maximum": self.maximum,
            "subfactors": [item.to_dict() for item in self.subfactors],
        }


@dataclass(frozen=True)
class AdjustmentResult:
    key: str
    label: str
    score: float
    minimum: float
    maximum: float
    status: str
    reason: str
    sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Scorecard:
    factors: tuple[FactorResult, ...]
    adjustments: tuple[AdjustmentResult, ...]
    base_score: float
    adjustment_score: float
    final_score: float
    rating: str
    rating_reason: str
    signal: str
    hard_caps: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "factors": [item.to_dict() for item in self.factors],
            "adjustments": [item.to_dict() for item in self.adjustments],
            "base_score": self.base_score,
            "adjustment_score": self.adjustment_score,
            "final_score": self.final_score,
            "rating": self.rating,
            "rating_reason": self.rating_reason,
            "signal": self.signal,
            "hard_caps": list(self.hard_caps),
        }


def _known(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _number(value: Any) -> float | None:
    if not _known(value) or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounded(value: float, minimum: float, maximum: float) -> float:
    return round(min(maximum, max(minimum, value)), 2)


def _sources(evidence: dict[str, Any], keys: Iterable[str]) -> tuple[str, ...]:
    source_map = evidence.get("metric_sources", {})
    found: list[str] = []
    for key in keys:
        for source in source_map.get(key, []):
            if source and source not in found:
                found.append(source)
    return tuple(found)


def _subfactor(
    evidence: dict[str, Any], key: str, label: str, score: float, maximum: float,
    reason: str, metric_keys: Iterable[str], *, partial: bool = False,
) -> SubfactorResult:
    sources = _sources(evidence, metric_keys)
    if not sources:
        status = "需人工确认"
        score = 0
    else:
        status = "部分覆盖" if partial else "已验证"
    return SubfactorResult(key, label, _bounded(score, 0, maximum), maximum, status, reason, sources)


def _missing(key: str, label: str, maximum: float, reason: str) -> SubfactorResult:
    return SubfactorResult(key, label, 0, maximum, "需人工确认", reason)


def _factor(key: str, label: str, maximum: float, items: list[SubfactorResult]) -> FactorResult:
    return FactorResult(key, label, round(sum(item.score for item in items), 2), maximum, tuple(items))


def _score_f1(evidence: dict[str, Any]) -> FactorResult:
    items: list[SubfactorResult] = []

    track = _number(evidence.get("track_strength"))
    if track is None:
        items.append(_missing("era_track", "大时代赛道", 10, "缺少可核验的行业、主营或产业标签"))
    else:
        items.append(_subfactor(
            evidence, "era_track", "大时代赛道", track * 10, 10,
            evidence.get("track_reason", "按行业、主营和产业标签匹配"),
            ("track_strength",), partial=evidence.get("track_partial", False),
        ))

    stage = evidence.get("chain_stage")
    stage_scores = {"upstream": 7, "midstream": 4, "downstream": 1}
    if stage not in stage_scores:
        items.append(_missing("upstream", "上游/卖铲子", 7, "未识别出可靠的产业链位置"))
    else:
        stage_score = stage_scores[stage] * (0.5 if evidence.get("chain_partial", False) else 1.0)
        items.append(_subfactor(
            evidence, "upstream", "上游/卖铲子", stage_score, 7,
            f"产业链位置：{evidence.get('chain_name', '未命名产业链')} / {stage}",
            ("chain_stage",), partial=evidence.get("chain_partial", False),
        ))

    supply_count = _number(evidence.get("supply_evidence_count"))
    supply_tightening = evidence.get("supply_tightening")
    if supply_count is None:
        items.append(_missing("supply_gap", "供需失衡", 5, "缺少价格、基差、库存或仓单证据"))
    else:
        if supply_count >= 2 and supply_tightening is True:
            score, reason, partial = 5, "至少两类证据共同指向供给趋紧", False
        elif supply_count >= 2 and supply_tightening is False:
            score, reason, partial = 0, "至少两类证据未显示供给趋紧", False
        elif supply_count >= 2:
            score, reason, partial = 2, "证据覆盖充分，但方向尚不一致", False
        else:
            score, reason, partial = 1, "只有一类供需证据，不能确认供需缺口", True
        items.append(_subfactor(
            evidence, "supply_gap", "供需失衡", score, 5, reason,
            ("supply_evidence_count", "supply_tightening"), partial=partial,
        ))

    choke = _number(evidence.get("chokepoint_score"))
    if choke is None:
        items.append(_missing("chokepoint", "卡脖子/国产替代", 4, "未匹配到精确卡脖子环节或标的"))
    else:
        score = 4 if choke >= 80 else 3 if choke >= 65 else 2 if choke >= 50 else 0
        items.append(_subfactor(
            evidence, "chokepoint", "卡脖子/国产替代", score, 4,
            f"卡脖子数据库评分 {choke:g}", ("chokepoint_score",),
            partial=evidence.get("chokepoint_partial", False),
        ))

    capex = _number(evidence.get("capex_strength"))
    if capex is None:
        items.append(_missing("capex_wave", "资本开支浪潮", 4, "缺少资本开支、订单或扩产证据"))
    else:
        items.append(_subfactor(
            evidence, "capex_wave", "资本开支浪潮", capex * 4, 4,
            evidence.get("capex_reason", "按订单、扩产和资本开支证据判断"),
            ("capex_strength",), partial=evidence.get("capex_partial", False),
        ))
    return _factor("F1", "产业趋势与资本开支", 30, items)


def _score_f2(evidence: dict[str, Any]) -> FactorResult:
    items: list[SubfactorResult] = []
    action = evidence.get("controller_action")
    action_scores = {"increase": 5, "stable": 4, "reduction": 0}
    action_reasons = {"increase": "控股股东或实控人有增持证据", "stable": "已核验期间未发生减持", "reduction": "控股股东或实控人存在减持"}
    if action not in action_scores:
        items.append(_missing("controller_action", "第一大股东增减持", 5, "未完成控股股东或实控人增减持核验"))
    else:
        items.append(_subfactor(evidence, "controller_action", "第一大股东增减持", action_scores[action], 5, action_reasons[action], ("controller_action",)))

    top1 = _number(evidence.get("top1_holder_pct"))
    if top1 is None:
        items.append(_missing("top1_ratio", "Top1 持股比例", 3, "缺少第一大股东持股比例"))
    else:
        score = 3 if 20 <= top1 <= 40 else 2 if 10 <= top1 <= 55 else 1
        items.append(_subfactor(evidence, "top1_ratio", "Top1 持股比例", score, 3, f"第一大股东持股 {top1:.2f}%", ("top1_holder_pct",)))

    holder_trend = _number(evidence.get("holder_count_change_pct"))
    if holder_trend is None:
        items.append(_missing("holder_trend", "股东户数趋势", 3, "缺少可比期间股东户数变化"))
    else:
        score = 3 if holder_trend <= -5 else 2 if holder_trend < 0 else 1 if holder_trend == 0 else 0
        items.append(_subfactor(evidence, "holder_trend", "股东户数趋势", score, 3, f"股东户数变化 {holder_trend:.2f}%", ("holder_count_change_pct",)))

    quality = _number(evidence.get("top10_quality"))
    if quality is None:
        items.append(_missing("top10_quality", "前十大股东质量", 2, "缺少前十大股东名单或性质判断"))
    else:
        items.append(_subfactor(evidence, "top10_quality", "前十大股东质量", quality * 2, 2, evidence.get("top10_quality_reason", "按国资、产业资本和长期机构占比判断"), ("top10_quality",), partial=evidence.get("top10_partial", False)))

    pledge = _number(evidence.get("pledge_ratio"))
    unlock = _number(evidence.get("unlock_ratio"))
    if pledge is None and unlock is None:
        items.append(_missing("pledge_unlock", "质押/解禁风险", 2, "质押和未来解禁数据均缺失"))
    else:
        known_count = int(pledge is not None) + int(unlock is not None)
        pledge_ok = pledge is None or pledge <= 10
        unlock_ok = unlock is None or unlock <= 5
        score = 2 if known_count == 2 and pledge_ok and unlock_ok else 1 if pledge_ok and unlock_ok else 0
        reason = f"质押比例 {pledge if pledge is not None else '待确认'}%；未来解禁比例 {unlock if unlock is not None else '待确认'}%"
        items.append(_subfactor(evidence, "pledge_unlock", "质押/解禁风险", score, 2, reason, ("pledge_ratio", "unlock_ratio"), partial=known_count < 2))
    return _factor("F2", "股东与筹码", 15, items)


def _score_f3(evidence: dict[str, Any]) -> FactorResult:
    items: list[SubfactorResult] = []
    background = _number(evidence.get("background_quality"))
    if background is None:
        items.append(_missing("background", "好爹/产业背景", 5, "缺少控股股东和实控人背景资料"))
    else:
        items.append(_subfactor(evidence, "background", "好爹/产业背景", background * 5, 5, evidence.get("background_reason", "按国资、央企或强产业资本背景判断"), ("background_quality",), partial=evidence.get("background_partial", False)))

    leadership = _number(evidence.get("leadership_strength"))
    if leadership is None:
        items.append(_missing("leadership", "龙头/核心供应商", 5, "缺少行业地位或核心供应商证据"))
    else:
        items.append(_subfactor(evidence, "leadership", "龙头/核心供应商", leadership * 5, 5, evidence.get("leadership_reason", "按行业地位证据判断"), ("leadership_strength",), partial=evidence.get("leadership_partial", False)))

    checks = (
        ("net_profit", lambda value: value > 0, "归母净利润为正"),
        ("operating_cashflow", lambda value: value > 0, "经营现金流为正"),
        ("debt_ratio", lambda value: value <= 0.70, "资产负债率不高于70%"),
        ("cash_to_debt", lambda value: value >= 0.10, "货币资金覆盖负债不低于10%"),
    )
    known_checks, passed, details, used_keys = 0, 0, [], []
    for key, predicate, description in checks:
        value = _number(evidence.get(key))
        if value is None:
            continue
        known_checks += 1
        used_keys.append(key)
        ok = predicate(value)
        passed += int(ok)
        details.append(f"{description}{'通过' if ok else '不通过'}")
    if not known_checks:
        items.append(_missing("financial_safety", "财务安全", 5, "缺少利润、现金流、负债和现金覆盖数据"))
    else:
        items.append(_subfactor(evidence, "financial_safety", "财务安全", passed * 1.25, 5, "；".join(details), used_keys, partial=known_checks < len(checks)))

    st_risk = evidence.get("st_risk")
    audit_risk = evidence.get("audit_risk")
    if st_risk is None and audit_risk is None:
        items.append(_missing("survival_risk", "退市/审计/商誉风险", 3, "缺少 ST、审计或重大风险核验"))
    else:
        score = 0 if st_risk is True else 2
        if audit_risk is False:
            score += 1
        if audit_risk is True:
            score = 0
        items.append(_subfactor(evidence, "survival_risk", "退市/审计/商誉风险", score, 3, f"ST/退市风险：{st_risk}；审计重大风险：{audit_risk}", ("st_risk", "audit_risk"), partial=audit_risk is None))

    special = _number(evidence.get("specialized_strength"))
    if special is None:
        items.append(_missing("specialized", "专精特新/单项冠军", 2, "未发现可核验的专精特新或单项冠军证据"))
    else:
        items.append(_subfactor(evidence, "specialized", "专精特新/单项冠军", special * 2, 2, evidence.get("specialized_reason", "按公开标签判断"), ("specialized_strength",), partial=evidence.get("specialized_partial", False)))
    return _factor("F3", "生存能力与龙头", 20, items)


def _score_f4(evidence: dict[str, Any]) -> FactorResult:
    items: list[SubfactorResult] = []
    match = _number(evidence.get("business_chain_match"))
    if match is None:
        items.append(_missing("business_match", "主营匹配产业链", 4, "缺少主营构成或产业链匹配结果"))
    else:
        items.append(_subfactor(evidence, "business_match", "主营匹配产业链", match * 4, 4, evidence.get("business_match_reason", "按主营构成和产业链匹配判断"), ("business_chain_match",), partial=evidence.get("business_match_partial", False)))

    stage = evidence.get("chain_stage")
    stage_scores = {"upstream": 4, "midstream": 2, "downstream": 1}
    if stage not in stage_scores:
        items.append(_missing("profit_position", "利润分配位置", 4, "未识别产业链利润位置"))
    else:
        stage_score = stage_scores[stage] * (0.5 if evidence.get("chain_partial", False) else 1.0)
        items.append(_subfactor(evidence, "profit_position", "利润分配位置", stage_score, 4, f"产业链位置为 {stage}", ("chain_stage",), partial=evidence.get("chain_partial", False)))

    overseas = _number(evidence.get("overseas_revenue_ratio"))
    if overseas is None:
        items.append(_missing("overseas", "出口/海外收入", 3, "缺少按地区披露的海外收入比例"))
    else:
        score = 3 if overseas >= 30 else 2 if overseas >= 10 else 1 if overseas > 0 else 0
        items.append(_subfactor(evidence, "overseas", "出口/海外收入", score, 3, f"海外收入占比 {overseas:.2f}%", ("overseas_revenue_ratio",)))

    realization_score, details, keys = 0.0, [], []
    for key, label in (("revenue_yoy", "营收同比"), ("profit_yoy", "归母净利润同比")):
        value = _number(evidence.get(key))
        if value is not None:
            keys.append(key)
            details.append(f"{label} {value * 100:.2f}%")
            realization_score += 1 if value > 0 else 0
    order_growth = _number(evidence.get("order_growth"))
    if order_growth is not None:
        keys.append("order_growth")
        details.append(f"订单增长 {order_growth:.2f}%")
        realization_score += 2 if order_growth > 0 else 0
    if not keys:
        items.append(_missing("realization", "订单/产能兑现", 4, "缺少营收、利润、订单或产能兑现数据"))
    else:
        items.append(_subfactor(evidence, "realization", "订单/产能兑现", realization_score, 4, "；".join(details), keys, partial=len(keys) < 3))
    return _factor("F4", "利润兑现路径", 15, items)


def _score_f5(evidence: dict[str, Any]) -> FactorResult:
    items: list[SubfactorResult] = []
    price = _number(evidence.get("price_percentile_3y"))
    if price is None:
        items.append(_missing("price_position", "价格分位", 5, "缺少至少三年的可比价格序列"))
    else:
        score = 5 if price <= 0.20 else 4 if price <= 0.35 else 2.5 if price <= 0.50 else 1 if price <= 0.70 else 0
        items.append(_subfactor(evidence, "price_position", "价格分位", score, 5, f"三年价格分位 {price:.1%}", ("price_percentile_3y",)))

    pe = _number(evidence.get("pe_ttm"))
    peer = _number(evidence.get("peer_pe_ttm_median"))
    pb = _number(evidence.get("pb"))
    if pe is None and pb is None:
        items.append(_missing("valuation", "PE/PB 相对位置", 4, "PE、PB 和同行估值均缺失"))
    else:
        score, details, keys = 0.0, [], []
        if pe is not None:
            keys.append("pe_ttm")
            details.append(f"TTM PE {pe:.2f}")
            if pe > 0 and peer and peer > 0:
                keys.append("peer_pe_ttm_median")
                ratio = pe / peer
                score += 2 if ratio <= 0.75 else 1 if ratio <= 1 else 0
                details.append(f"同行中位数 {peer:.2f}")
        if pb is not None:
            keys.append("pb")
            score += 2 if 0 < pb < 2 else 1 if 0 < pb < 3 else 0
            details.append(f"PB {pb:.2f}")
        items.append(_subfactor(evidence, "valuation", "PE/PB 相对位置", score, 4, "；".join(details), keys, partial=peer is None or pe is None or pb is None))

    attention_heat = _number(evidence.get("attention_heat"))
    social_heat = _number(evidence.get("social_heat"))
    heat_values = [value for value in (attention_heat, social_heat) if value is not None]
    heat = max(heat_values) if heat_values else None
    if heat is None:
        items.append(_missing("coldness", "行业冰点/市场冷落", 4, "缺少个股关注度、人气排名或行业冷落证据"))
    else:
        score = 4 if heat <= 0.20 else 3 if heat <= 0.40 else 1 if heat <= 0.60 else 0
        items.append(_subfactor(evidence, "coldness", "行业冰点/市场冷落", score, 4, f"关注热度归一值 {heat:.2f}，越低越冷", ("attention_heat",), partial=evidence.get("attention_partial", False)))

    inflection_score, details, keys = 0.0, [], []
    for key, label in (("revenue_yoy", "营收同比"), ("profit_yoy", "利润同比"), ("revenue_yoy_delta", "营收同比改善"), ("profit_yoy_delta", "利润同比改善")):
        value = _number(evidence.get(key))
        if value is not None:
            keys.append(key)
            details.append(f"{label} {value * 100:.2f}%")
            inflection_score += 1 if value > 0 else 0
    if not keys:
        items.append(_missing("inflection", "业绩拐点", 4, "缺少营收、利润及其趋势数据"))
    else:
        items.append(_subfactor(evidence, "inflection", "业绩拐点", inflection_score, 4, "；".join(details), keys, partial=len(keys) < 4))

    gap_score, gap_details, gap_keys = 0.0, [], []
    track = _number(evidence.get("track_strength"))
    if heat is not None and track is not None:
        gap_keys.extend(("attention_heat", "track_strength"))
        if heat <= 0.40 and track >= 0.70:
            gap_score += 1
            gap_details.append("关注度偏低但产业逻辑较强")
    order_growth = _number(evidence.get("order_growth"))
    supply_tightening = evidence.get("supply_tightening")
    if order_growth is not None:
        gap_keys.append("order_growth")
        if order_growth > 0:
            gap_score += 1
            gap_details.append("订单已经改善")
    elif supply_tightening is not None:
        gap_keys.append("supply_tightening")
        if supply_tightening:
            gap_score += 1
            gap_details.append("供需证据开始改善")
    revenue_delta = _number(evidence.get("revenue_yoy_delta"))
    profit_delta = _number(evidence.get("profit_yoy_delta"))
    if revenue_delta is not None or profit_delta is not None:
        gap_keys.extend(key for key, value in (("revenue_yoy_delta", revenue_delta), ("profit_yoy_delta", profit_delta)) if value is not None)
        if (revenue_delta or 0) > 0 or (profit_delta or 0) > 0:
            gap_score += 1
            gap_details.append("财务同比趋势边际改善")
    if not gap_keys:
        items.append(_missing("expectation_gap", "预期差", 3, "缺少关注度与产业、订单或财务拐点的交叉证据"))
    else:
        items.append(_subfactor(evidence, "expectation_gap", "预期差", gap_score, 3, "；".join(gap_details) or "交叉证据未形成正向预期差", gap_keys, partial=len(set(gap_keys)) < 3))
    return _factor("F5", "低位与困境反转", 20, items)


def _adjustments(evidence: dict[str, Any], factors: tuple[FactorResult, ...]) -> tuple[AdjustmentResult, ...]:
    alpha = _number(evidence.get("alpha_score"))
    if alpha is None:
        alpha_result = AdjustmentResult("alpha", "Alpha/技术结构", 0, -3, 3, "需人工确认", "缺少本次 TDX Alpha 评分")
    else:
        base_score = 3 if alpha >= 0.40 else 2 if alpha >= 0.20 else 1 if alpha > 0.05 else -3 if alpha <= -0.45 else -2 if alpha <= -0.20 else -1 if alpha < -0.05 else 0
        crosscheck = evaluate_alpha_crosscheck(evidence, base_score)
        evidence["alpha_crosscheck"] = crosscheck
        source_map = evidence.setdefault("metric_sources", {})
        source_map["alpha_crosscheck"] = ["机构方法/量化选股筛选", "机构方法/投资逻辑追踪"]
        method_text = "；".join(f"{item['method']}={item['label']}（{item['reason']}）" for item in crosscheck["methods"])
        status = "已验证" if crosscheck["status"] in {"同向确认", "基础中性"} else "部分覆盖"
        reason = f"TDX Alpha {alpha:.4f}，原始修正 {base_score:+d}；机构复核：{crosscheck['status']}；{method_text}"
        alpha_result = AdjustmentResult(
            "alpha", "Alpha/技术结构", crosscheck["final_score"], -3, 3, status, reason,
            _sources(evidence, ("alpha_score", "alpha_crosscheck")),
        )

    price = _number(evidence.get("price_percentile_3y"))
    attention_heat = _number(evidence.get("attention_heat"))
    social_heat = _number(evidence.get("social_heat"))
    heat_values = [value for value in (attention_heat, social_heat) if value is not None]
    heat = max(heat_values) if heat_values else None
    congestion = _number(evidence.get("market_congestion"))
    congestion_fresh = evidence.get("market_congestion_fresh") is True
    trap_risk = evidence.get("trap_risk_level")
    factor_map = {factor.key: factor.score for factor in factors}
    sentiment_keys: list[str] = []
    if price is not None:
        sentiment_keys.append("price_percentile_3y")
    if attention_heat is not None:
        sentiment_keys.append("attention_heat")
    if social_heat is not None:
        sentiment_keys.append("social_heat")
    if congestion is not None and congestion_fresh:
        sentiment_keys.append("market_congestion")
    social_checked = _number(evidence.get("social_platforms_checked"))
    social_total = _number(evidence.get("social_platforms_total"))
    social_complete = social_checked is not None and social_total is not None and social_checked >= social_total
    trap_complete = evidence.get("trap_risk_level") in {"低", "注意", "高"}
    if not sentiment_keys:
        sentiment_result = AdjustmentResult("sentiment", "情绪/拥挤度", 0, -3, 3, "需人工确认", "缺少价格位置、个股热度和市场拥挤度证据")
    else:
        score, reason = 0, "情绪证据未形成明确修正"
        if trap_risk == "高":
            sentiment_keys.append("trap_risk_level")
            score, reason = -3, "至少两类独立证据形成高异常推广风险"
        elif price is not None and price > 0.80 and ((heat is not None and heat >= 0.80) or (congestion is not None and congestion_fresh and congestion >= 0.80)):
            score, reason = -3, "高位叠加个股过热或市场高拥挤"
        elif price is not None and price <= 0.35 and heat is not None and heat <= 0.35 and factor_map.get("F1", 0) >= 15:
            score, reason = 2, "低位冷门且产业逻辑未破"
        fully_covered = len(sentiment_keys) >= 3 and social_complete and trap_complete
        sentiment_result = AdjustmentResult("sentiment", "情绪/拥挤度", score, -3, 3, "已验证" if fully_covered else "部分覆盖", reason, _sources(evidence, sentiment_keys))

    catalysts = _number(evidence.get("verified_catalyst_count"))
    if catalysts is None:
        catalyst_result = AdjustmentResult("catalyst", "风口催化", 0, -2, 2, "需人工确认", "缺少公告或研报中的可验证催化")
    else:
        score = min(2, max(0, int(catalysts)))
        catalyst_result = AdjustmentResult("catalyst", "风口催化", score, -2, 2, "已验证", f"发现 {int(catalysts)} 项可验证催化", _sources(evidence, ("verified_catalyst_count",)))
    return alpha_result, sentiment_result, catalyst_result


def _base_rating(score: float) -> str:
    if score >= 85:
        return "根"
    if score >= 70:
        return "矛"
    if score >= 55:
        return "学习仓"
    return "不碰"


def _cap_rating(rating: str, cap: str) -> str:
    return RATING_ORDER[min(RATING_ORDER.index(rating), RATING_ORDER.index(cap))]


def score_evidence(evidence: dict[str, Any]) -> Scorecard:
    factors = (
        _score_f1(evidence),
        _score_f2(evidence),
        _score_f3(evidence),
        _score_f4(evidence),
        _score_f5(evidence),
    )
    base_score = round(sum(factor.score for factor in factors), 2)
    adjustments = _adjustments(evidence, factors)
    adjustment_score = round(sum(item.score for item in adjustments), 2)
    final_score = _bounded(base_score + adjustment_score, 0, 100)
    rating = _base_rating(final_score)
    factor_map = {factor.key: factor.score for factor in factors}
    caps: list[dict[str, str]] = []

    if evidence.get("st_risk") is True:
        caps.append({"condition": "ST 或退市风险", "result": "已触发", "cap": "不碰"})
        rating = "不碰"
    else:
        caps.append({"condition": "ST 或退市风险", "result": "未触发" if evidence.get("st_risk") is False else "需人工确认", "cap": "无" if evidence.get("st_risk") is False else "需人工确认"})

    controller_action = evidence.get("controller_action")
    if controller_action == "reduction":
        caps.append({"condition": "控股股东或实控人减持", "result": "已触发", "cap": "学习仓"})
        rating = _cap_rating(rating, "学习仓")
    else:
        caps.append({"condition": "控股股东或实控人减持", "result": "未触发" if controller_action in ("increase", "stable") else "需人工确认", "cap": "无" if controller_action in ("increase", "stable") else "需人工确认"})

    floor_triggered = factor_map["F1"] < 15 or factor_map["F3"] < 8
    caps.append({"condition": "F1 < 15 或 F3 < 8", "result": "已触发" if floor_triggered else "未触发", "cap": "学习仓" if floor_triggered else "无"})
    if floor_triggered:
        rating = _cap_rating(rating, "学习仓")

    price = _number(evidence.get("price_percentile_3y"))
    congestion = _number(evidence.get("market_congestion"))
    congestion_fresh = evidence.get("market_congestion_fresh") is True
    hot_cap = price is not None and price > 0.80 and congestion is not None and congestion_fresh and congestion >= 0.80
    hot_known_safe = price is not None and price <= 0.80 or (price is not None and congestion is not None and congestion_fresh)
    caps.append({"condition": "价格高位且市场拥挤过热", "result": "已触发" if hot_cap else "未触发" if hot_known_safe else "需人工确认", "cap": "矛" if hot_cap else "无" if hot_known_safe else "需人工确认"})
    if hot_cap:
        rating = _cap_rating(rating, "矛")

    triggered = [item for item in caps if item["result"] == "已触发"]
    rating_reason = "；".join(f"{item['condition']}，评级最高为{item['cap']}" for item in triggered) if triggered else f"综合分达到{rating}档且未触发评级上限"
    return Scorecard(
        factors=factors,
        adjustments=adjustments,
        base_score=base_score,
        adjustment_score=adjustment_score,
        final_score=final_score,
        rating=rating,
        rating_reason=rating_reason,
        signal=str(evidence.get("technical_signal") or "需人工确认"),
        hard_caps=tuple(caps),
    )
