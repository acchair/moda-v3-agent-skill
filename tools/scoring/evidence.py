from __future__ import annotations

import csv
import json
from pathlib import Path
import re
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "knowledge" / "research"
SOURCE_LABELS = {
    "finance_data": "easy_tdx/TDX + easy_tdx/Sina",
    "business_data": "EastMoney/F10",
    "tdx_analysis": "easy_tdx/TDX",
    "announcements": "easy_tdx/CNINFO + AKShare/CNINFO",
    "market_events": "EastMoney + Sina",
    "popularity": "EastMoney/stockrank",
    "supply_demand": "AKShare/futures",
    "congestion": "AKShare/legulegu",
    "social_sentiment": "公开社交热榜",
    "macro_policy": "AKShare/PBOC + gov.cn",
    "web_research": "SearXNG + DuckDuckGo MCP",
}
REPORTS = tuple(SOURCE_LABELS)
COMMENT_PATTERN = re.compile(r"<!--\s*(moda_[a-z_]+):\s*(\{.*?\})\s*-->", re.S)

TRACK_GROUPS = {
    "AI 算力与数据中心": ("算力", "数据中心", "服务器", "液冷", "光模块", "高速互联", "AI电源", "人工智能"),
    "半导体国产替代": ("半导体", "芯片", "光刻", "电子特气", "硅片", "封装", "国产替代", "功率器件"),
    "商业航天与军工": ("商业航天", "卫星", "火箭", "高温合金", "军工", "航空发动机"),
    "新能源与储能": ("储能", "电网", "新能源", "锂电", "光伏", "风电", "充电桩"),
    "资源与周期": ("锂矿", "铜矿", "金矿", "稀土", "有色", "煤炭", "航运", "涨价"),
    "机器人与先进制造": ("机器人", "工业母机", "数控", "核心零部件", "自动化", "专用设备"),
}
CAPEX_TERMS = ("资本开支", "扩产", "投产", "产能利用率", "新增订单", "在手订单", "设备投资", "产线建设")
LEADERSHIP_TERMS = ("全球龙头", "行业龙头", "国内龙头", "核心供应商", "市场第一", "市占率第一", "隐形冠军")
SPECIALIZED_TERMS = ("专精特新", "单项冠军", "制造业冠军", "小巨人")
CATALYST_TERMS = ("中标", "重大合同", "新增订单", "订单增长", "扩产", "投产", "涨价", "回购", "增持", "业绩预增", "扭亏")
PROMOTION_TEMPLATE_TERMS = ("必涨", "稳赚", "翻倍", "内部消息", "主力建仓", "最后上车")
PAID_GROUP_TERMS = ("加群", "微信群", "VIP", "直播间", "收费群")
PERSONA_TERMS = ("老师带", "股神", "跟单", "操盘手")
RUMOR_TERMS = ("谣言", "辟谣", "澄清", "虚假", "操纵", "荐股骗局", "杀猪盘")


def _read_report(code: str, directory: str, since: float = 0) -> str | None:
    path = REPORT_ROOT / directory / f"{code}.md"
    if not path.exists() or (since and path.stat().st_mtime < since - 1):
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def read_reports(code: str, directories: tuple[str, ...] = REPORTS, since: float = 0) -> dict[str, str]:
    reports: dict[str, str] = {}
    for directory in directories:
        text = _read_report(code, directory, since)
        if text is not None:
            reports[directory] = text
    return reports


def _set(evidence: dict[str, Any], key: str, value: Any, source: str, *, overwrite: bool = False) -> None:
    if value is None or value == "":
        return
    if overwrite or key not in evidence:
        evidence[key] = value
    source_map = evidence.setdefault("metric_sources", {})
    sources = source_map.setdefault(key, [])
    if source and source not in sources:
        sources.append(source)


def _extract_comments(report: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for _, raw in COMMENT_PATTERN.findall(report):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            payloads.append(value)
    return payloads


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _derive_legacy_fields(evidence: dict[str, Any], reports: dict[str, str]) -> None:
    finance = reports.get("finance_data", "")
    source = SOURCE_LABELS["finance_data"]
    match = re.search(r"\|\s*行业\s*\|\s*([^|\n]+)", finance)
    if match:
        _set(evidence, "industry", match.group(1).strip(), source)

    tdx = reports.get("tdx_analysis", "")
    if tdx:
        source = SOURCE_LABELS["tdx_analysis"]
        match = re.search(r"当前评分\*\*:\s*([+-]?\d+(?:\.\d+)?)", tdx)
        if match:
            _set(evidence, "alpha_score", float(match.group(1)), source)
        match = re.search(r"位置=([0-9.]+)", tdx)
        if match:
            _set(evidence, "technical_position", float(match.group(1)), source)
        for label in ("清仓", "减仓", "加仓", "建仓"):
            if re.search(rf"\*\*[^\n]*{label}\*\*:\s*[^\n]*触发", tdx):
                _set(evidence, "technical_signal", label, source)
                break
        if "technical_signal" not in evidence:
            _set(evidence, "technical_signal", "中性/无触发", source)

    announcements = reports.get("announcements", "")
    if announcements:
        source = SOURCE_LABELS["announcements"]
        titles = re.findall(r"\|\s*\d{4}-\d{2}-\d{2}\s*\|[^|]*\|\s*\[([^]]+)]", announcements)
        if titles:
            _set(evidence, "announcement_titles", titles, source)
        growth_matches = re.findall(r"(?:订单|新增订单)[^\n。]{0,40}?(?:同比(?:增幅)?|增长)[^\d]{0,8}([0-9]+(?:\.[0-9]+)?)%", announcements)
        if growth_matches:
            _set(evidence, "order_growth", max(float(value) for value in growth_matches), source)


def _chain_match(evidence: dict[str, Any]) -> None:
    path = ROOT / "tools" / "scoring" / "chains.yaml"
    if not path.exists():
        return
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    industry_text = str(evidence.get("industry", "")).lower()
    primary_parts = [
        industry_text,
        str(evidence.get("main_business", "")),
        " ".join(str(item) for item in evidence.get("business_items", [])[:30]),
    ]
    context = " ".join(primary_parts).lower()
    concept_context = " ".join(str(item) for item in evidence.get("concepts", [])[:30]).lower()
    if not context.strip():
        return
    best: tuple[float, str, str] | None = None
    for chain in raw.get("chains", []):
        chain_name = str(chain.get("name", ""))
        aliases = [str(item).lower() for item in chain.get("aliases", [])]
        primary_aliases = [alias for alias in aliases if alias and alias in context]
        alias_score = min(2.0, 0.5 * max(map(len, primary_aliases))) if primary_aliases else 0.25 if any(alias and alias in concept_context for alias in aliases) else 0
        for stage in ("upstream", "midstream", "downstream"):
            data = chain.get(stage, {}) or {}
            industries = [str(item).lower() for item in data.get("industries", [])]
            keywords = [str(item).lower() for item in data.get("keywords", [])]
            score = alias_score
            score += 2 * sum(item and item in industry_text for item in industries)
            score += sum(item and item in context for item in keywords)
            score += 0.25 * sum(item and item in concept_context and item not in context for item in keywords)
            candidate = (score, chain_name, stage)
            if score > 0 and (best is None or candidate[0] > best[0]):
                best = candidate
    if best:
        source = "chains.yaml"
        _set(evidence, "chain_stage", best[2], source)
        _set(evidence, "chain_name", best[1], source)
        _set(evidence, "business_chain_match", min(1.0, best[0] / 3), source)
        evidence["chain_partial"] = best[0] < 3
        evidence["business_match_partial"] = best[0] < 3
        evidence["business_match_reason"] = f"匹配 {best[1]}，位置为 {best[2]}"


def _chokepoint_match(evidence: dict[str, Any]) -> None:
    path = ROOT / "tools" / "scoring" / "chokepoint_segments.csv"
    if not path.exists():
        return
    name = str(evidence.get("name", ""))
    context = " ".join([
        str(evidence.get("industry", "")),
        str(evidence.get("main_business", "")),
        " ".join(str(item) for item in evidence.get("business_items", [])[:30]),
    ])
    best: tuple[float, str, bool] | None = None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            stocks = str(row.get("key_stocks", ""))
            segment = str(row.get("segment_name", ""))
            exact = bool(name and name in stocks)
            contextual = bool(segment and segment in context)
            if not exact and not contextual:
                continue
            score = _float(row.get("chokepoint_score")) or 0
            candidate = (score, segment, not exact)
            if best is None or candidate[0] > best[0]:
                best = candidate
    if best:
        _set(evidence, "chokepoint_score", best[0], "chokepoint_segments.csv")
        evidence["chokepoint_segment"] = best[1]
        evidence["chokepoint_partial"] = best[2]


def _derive_framework_fields(evidence: dict[str, Any], reports: dict[str, str]) -> None:
    structured_context = " ".join([
        str(evidence.get("industry", "")),
        str(evidence.get("main_business", "")),
        " ".join(str(item) for item in evidence.get("business_items", [])[:30]),
        " ".join(str(item) for item in evidence.get("concepts", [])[:30]),
    ])
    full_context = structured_context + " " + " ".join(reports.values())
    matched_tracks = [label for label, terms in TRACK_GROUPS.items() if any(term.lower() in structured_context.lower() for term in terms)]
    if matched_tracks:
        strength = 1.0 if len(matched_tracks) >= 2 else 0.8
        _set(evidence, "track_strength", strength, "行业/主营结构化匹配")
        evidence["track_reason"] = "匹配赛道：" + "、".join(matched_tracks)
        evidence["track_partial"] = len(matched_tracks) == 1

    order_growth = _float(evidence.get("order_growth"))
    capex_hits = [term for term in CAPEX_TERMS if term in full_context]
    if order_growth is not None and order_growth > 0:
        _set(evidence, "capex_strength", 1.0, evidence.get("metric_sources", {}).get("order_growth", ["公告"])[0])
        evidence["capex_reason"] = f"订单增长 {order_growth:.2f}% 并出现资本开支兑现证据"
    elif capex_hits:
        _set(evidence, "capex_strength", 0.5, "公告/主营文本")
        evidence["capex_reason"] = "发现资本开支相关证据：" + "、".join(capex_hits[:3])
        evidence["capex_partial"] = True

    titles = [str(item) for item in evidence.get("announcement_titles", [])]
    title_text = " ".join(titles)
    reduction = re.search(r"(?:控股股东|实际控制人|实控人)[^。；\n]{0,35}减持|减持[^。；\n]{0,35}(?:控股股东|实际控制人|实控人)", title_text)
    increase = re.search(r"(?:控股股东|实际控制人|实控人)[^。；\n]{0,35}增持|增持[^。；\n]{0,35}(?:控股股东|实际控制人|实控人)", title_text)
    if reduction:
        _set(evidence, "controller_action", "reduction", SOURCE_LABELS["announcements"], overwrite=True)
    elif increase:
        _set(evidence, "controller_action", "increase", SOURCE_LABELS["announcements"], overwrite=True)

    if "st_risk" not in evidence:
        normalized_name = str(evidence.get("name", "")).upper().replace(" ", "")
        _set(evidence, "st_risk", normalized_name.startswith(("ST", "*ST")), "证券简称")
    audit_terms = ("非标准审计", "保留意见", "无法表示意见", "否定意见", "退市风险警示")
    if any(term in title_text for term in audit_terms):
        _set(evidence, "audit_risk", True, SOURCE_LABELS["announcements"])
    elif evidence.get("announcement_lookback_days"):
        _set(evidence, "audit_risk", False, SOURCE_LABELS["announcements"])

    leadership_hits = [term for term in LEADERSHIP_TERMS if term in full_context]
    if leadership_hits:
        _set(evidence, "leadership_strength", min(1.0, 0.5 + 0.25 * len(leadership_hits)), "公告/主营/研报")
        evidence["leadership_reason"] = "发现行业地位证据：" + "、".join(leadership_hits[:3])
        evidence["leadership_partial"] = True
    specialized_hits = [term for term in SPECIALIZED_TERMS if term in full_context]
    if specialized_hits:
        _set(evidence, "specialized_strength", min(1.0, 0.5 + 0.25 * len(specialized_hits)), "公告/主营/概念")
        evidence["specialized_reason"] = "发现标签：" + "、".join(specialized_hits[:3])
        evidence["specialized_partial"] = True

    catalyst_hits = {term for term in CATALYST_TERMS if term in title_text}
    if title_text:
        _set(evidence, "verified_catalyst_count", len(catalyst_hits), SOURCE_LABELS["announcements"])

    _chain_match(evidence)
    _chokepoint_match(evidence)

    web_supply = evidence.get("web_supply_validation") or {}
    current_supply_count = _float(evidence.get("supply_evidence_count"))
    if web_supply.get("status") == "已验证" and (current_supply_count is None or current_supply_count < 2):
        _set(evidence, "supply_evidence_count", web_supply.get("evidence_count"), SOURCE_LABELS["web_research"], overwrite=True)
        _set(evidence, "supply_tightening", web_supply.get("tightening"), SOURCE_LABELS["web_research"], overwrite=True)
        evidence["supply_web_fallback"] = True

    web_chokepoint = evidence.get("web_chokepoint_validation") or {}
    if web_chokepoint.get("status") == "已验证" and ("chokepoint_score" not in evidence or evidence.get("chokepoint_partial") is True):
        _set(evidence, "chokepoint_score", web_chokepoint.get("score"), SOURCE_LABELS["web_research"], overwrite=True)
        evidence["chokepoint_partial"] = False
        evidence["chokepoint_web_fallback"] = True

    promotion_hits = set(str(item) for item in evidence.get("promotional_keyword_hits", []))
    rumor_hits = set(str(item) for item in evidence.get("rumor_keyword_hits", []))
    announcement_rumors = {term for term in RUMOR_TERMS if term in title_text}
    platform_hits = int(_float(evidence.get("social_platform_hits")) or 0)
    attention = _float(evidence.get("attention_heat"))
    social_heat = _float(evidence.get("social_heat"))
    combined_heat = max(value for value in (attention, social_heat) if value is not None) if any(value is not None for value in (attention, social_heat)) else None
    price = _float(evidence.get("price_percentile_3y"))
    profit = _float(evidence.get("net_profit"))
    profit_yoy = _float(evidence.get("profit_yoy"))
    revenue_yoy = _float(evidence.get("revenue_yoy"))
    financial_negative = any(value is not None and value < 0 for value in (profit, profit_yoy, revenue_yoy))
    fundamental_gap = combined_heat is not None and combined_heat >= 0.75 and financial_negative
    if combined_heat is None:
        fundamental_reason = "缺少热度或财务交叉证据"
    elif combined_heat < 0.75:
        fundamental_reason = f"热度 {combined_heat:.2f} 未达 0.75 阈值" + ("，虽有负向财务证据" if financial_negative else "")
    else:
        fundamental_reason = f"热度 {combined_heat:.2f} 与负向财务证据{'并存' if financial_negative else '未同时出现'}"
    kline_overlap = combined_heat is not None and combined_heat >= 0.75 and price is not None and price >= 0.80 and evidence.get("technical_overheat") is True
    kline_reason = "缺少热度、价格分位或技术信号"
    if combined_heat is not None and price is not None:
        kline_reason = f"热度 {combined_heat:.2f}、价格分位 {price:.1%}、技术过热={evidence.get('technical_overheat')}"
    checks = [
        {"signal": "大量账号/平台同步推荐", "hit": platform_hits >= 3, "evidence": f"热榜命中 {platform_hits} 个平台" if platform_hits else "当前热榜未形成跨平台命中"},
        {"signal": "推荐话术模板化", "hit": len(promotion_hits & set(PROMOTION_TEMPLATE_TERMS)) >= 2, "evidence": "、".join(sorted(promotion_hits & set(PROMOTION_TEMPLATE_TERMS))) or "未命中两类模板话术"},
        {"signal": "付费社群/VIP 引流", "hit": bool(promotion_hits & set(PAID_GROUP_TERMS)), "evidence": "、".join(sorted(promotion_hits & set(PAID_GROUP_TERMS))) or "未命中付费引流词"},
        {"signal": "基本面与热度脱节", "hit": fundamental_gap, "evidence": fundamental_reason},
        {"signal": "K 线异常配合", "hit": kline_overlap, "evidence": kline_reason},
        {"signal": "老师/股神人设推广", "hit": bool(promotion_hits & set(PERSONA_TERMS)), "evidence": "、".join(sorted(promotion_hits & set(PERSONA_TERMS))) or "未命中人设推广词"},
        {"signal": "跨平台联动推广", "hit": platform_hits >= 3 and bool(promotion_hits), "evidence": f"{platform_hits} 个平台且出现推广词" if platform_hits else "未形成跨平台推广证据"},
        {"signal": "虚假研报/谣言/澄清", "hit": bool(rumor_hits or announcement_rumors), "evidence": "、".join(sorted(rumor_hits | announcement_rumors)) or "未命中公开谣言或澄清证据"},
    ]
    signal_count = sum(bool(item["hit"]) for item in checks)
    independent_categories = sum((bool(platform_hits or promotion_hits), any(value is not None for value in (profit, profit_yoy, revenue_yoy, price)), bool(announcement_rumors)))
    if evidence.get("social_platforms_checked") is not None:
        _set(evidence, "trap_signal_count", signal_count, SOURCE_LABELS["social_sentiment"])
        _set(evidence, "trap_checks", checks, SOURCE_LABELS["social_sentiment"])
        _set(evidence, "trap_independent_categories", independent_categories, SOURCE_LABELS["social_sentiment"])
        if signal_count >= 4 and independent_categories >= 2:
            risk = "高"
        elif signal_count >= 2:
            risk = "注意"
        elif int(_float(evidence.get("social_platforms_checked")) or 0) < int(_float(evidence.get("social_platforms_total")) or 0):
            risk = "未见高风险信号（部分覆盖）"
        else:
            risk = "低"
        _set(evidence, "trap_risk_level", risk, SOURCE_LABELS["social_sentiment"])


def build_evidence(code: str, name: str, reports: dict[str, str]) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "code": code,
        "name": name or code,
        "metric_sources": {},
        "completed_modules": list(reports),
    }
    for directory, report in reports.items():
        source = SOURCE_LABELS.get(directory, directory)
        for payload in _extract_comments(report):
            for key, value in payload.items():
                _set(evidence, key, value, source, overwrite=True)
    _derive_legacy_fields(evidence, reports)
    _derive_framework_fields(evidence, reports)
    return evidence
