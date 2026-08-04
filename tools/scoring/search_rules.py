from __future__ import annotations

import re
from typing import Any


RULES: dict[str, dict[str, Any]] = {
    "F1.era_track": {"queries": ["{name} {context} 未来三年 CAGR 渗透率 市场规模", "{context} 行业增速 渗透率 产业趋势"], "positive": ("增长", "CAGR", "渗透率", "产业趋势", "技术革命"), "negative": ("负增长", "衰退", "萎缩", "成熟期")},
    "F1.upstream": {"queries": ["{name} 主营 产品 产业链 上游 材料 设备 核心零部件"], "positive": ("上游", "原材料", "核心设备", "关键零部件", "卖铲子"), "negative": ("下游应用", "终端应用", "整机")},
    "F1.supply_gap": {"queries": ["{name} {context} 供需 CR3 库存 订单 扩产周期", "{context} 供给集中度 产能 建设周期 供不应求"], "positive": ("供不应求", "库存下降", "去库存", "订单增长", "产能利用率提升", "扩产周期"), "negative": ("供过于求", "库存上升", "产能过剩", "订单下降")},
    "F1.chokepoint": {"queries": ["{name} 国产替代 进口依赖 唯一供应商 不可替代 卡脖子"], "positive": ("国产替代", "进口依赖", "不可替代", "唯一供应商", "自主可控", "卡脖子"), "negative": ("可替代", "替代路线", "竞争激烈")},
    "F1.capex_wave": {"queries": ["{name} {context} 资本开支 同比 扩产 投资 订单", "{context} 龙头 资本开支 出货量 同比"], "positive": ("资本开支增长", "投资增长", "扩产", "新增产能", "订单增长", "出货量增长"), "negative": ("削减资本开支", "投资下降", "停止扩产", "订单下降")},
    "F2.controller_action": {"queries": ["{code} {name} 控股股东 实际控制人 增持 减持"], "positive": ("增持",), "negative": ("减持", "清仓式减持")},
    "F2.top1_ratio": {"queries": ["{code} {name} 第一大股东 持股比例"], "positive": ("第一大股东", "持股比例"), "negative": ()},
    "F2.holder_trend": {"queries": ["{code} {name} 股东户数 环比 下降 增加"], "positive": ("股东户数下降", "股东人数下降", "筹码集中"), "negative": ("股东户数增加", "股东人数增加", "筹码分散")},
    "F2.top10_quality": {"queries": ["{code} {name} 前十大股东 国资 产业资本 基金 持仓变化"], "positive": ("国资", "产业资本", "社保", "基金增持", "保险", "长期机构"), "negative": ("基金减持", "机构退出")},
    "F2.pledge_unlock": {"queries": ["{code} {name} 股权质押比例 未来解禁比例"], "positive": ("无质押", "零质押", "无解禁"), "negative": ("高比例质押", "大额解禁", "质押风险")},
    "F3.background": {"queries": ["{name} 控股股东 实际控制人 国资 央企 产业资本 背景"], "positive": ("央企", "国资", "国有控股", "产业资本", "产业龙头", "战略股东"), "negative": ("无实际控制人", "股权分散", "资金占用")},
    "F3.leadership": {"queries": ["{name} 市场份额 市占率 排名 销量 出货量 用户数 客户覆盖 核心供应商"], "positive": ("市场份额", "市占率", "排名", "销量", "出货量", "用户数", "核心供应商", "客户覆盖", "技术领先", "专利", "牌照", "标准"), "negative": ("市占率低", "边缘供应商", "客户流失")},
    "F3.financial_safety": {"queries": ["{code} {name} 净现金 短期债务 经营现金流 应收账款 资产负债率"], "positive": ("净现金为正", "现金流为正", "低负债", "现金充足", "短债覆盖"), "negative": ("现金流为负", "高负债", "债务逾期", "应收账款异常", "流动性风险")},
    "F3.survival_risk": {"queries": ["{code} {name} ST 退市 审计意见 商誉减值 持续经营风险"], "positive": ("标准无保留意见", "无退市风险", "未触发风险警示"), "negative": ("退市风险警示", "保留意见", "无法表示意见", "否定意见", "商誉减值", "持续经营重大不确定性")},
    "F3.specialized": {"queries": ["{name} 专精特新 小巨人 制造业单项冠军"], "positive": ("专精特新", "小巨人", "制造业单项冠军", "单项冠军"), "negative": ()},
    "F4.business_match": {"queries": ["{name} 主营业务 收入占比 {context} 产业链"], "positive": ("主营业务", "收入占比", "产业链", "核心产品"), "negative": ("占比较低", "尚未形成收入")},
    "F4.profit_position": {"queries": ["{name} 产业链 上游 议价权 毛利率 核心环节"], "positive": ("上游", "议价权", "高毛利", "核心环节", "定价权"), "negative": ("价格竞争", "低毛利", "下游应用")},
    "F4.overseas": {"queries": ["{code} {name} 海外收入占比 出口收入"], "positive": ("海外收入", "出口收入", "境外收入"), "negative": ("无海外收入",)},
    "F4.realization": {"queries": ["{name} 营收同比 净利润同比 在手订单 产能利用率"], "positive": ("营收增长", "利润增长", "订单增长", "产能利用率提升", "投产"), "negative": ("营收下降", "利润下降", "订单下降", "产能闲置")},
    "F5.price_position": {"queries": ["{name} 产品价格 历史高点 周期底部 库存", "{context} 产品价格 历史分位"], "positive": ("周期底部", "历史低位", "价格低位", "库存下降"), "negative": ("历史高位", "价格见顶", "库存高位")},
    "F5.valuation": {"queries": ["{code} {name} PE历史分位 PB历史中位数 估值分位"], "positive": ("估值低位", "低于历史中位数", "历史低分位", "低估"), "negative": ("估值高位", "历史高分位", "高估")},
    "F5.coldness": {"queries": ["{name} 行业冰点 市场关注度 机构覆盖 冷门"], "positive": ("行业冰点", "低关注", "冷门", "机构覆盖少", "无人问津"), "negative": ("热门股", "高关注", "拥挤")},
    "F5.inflection": {"queries": ["{name} 营收增长 利润下降 现金流改善 库存下降 订单恢复 困境反转"], "positive": ("困境反转", "现金流改善", "订单恢复", "库存下降", "扭亏", "利润改善"), "negative": ("持续恶化", "现金流恶化", "订单下降")},
    "F5.expectation_gap": {"queries": ["{name} 预期差 低关注 订单改善 产业趋势"], "positive": ("预期差", "低关注", "尚未充分定价", "订单改善", "业绩超预期"), "negative": ("充分定价", "一致预期过高", "交易拥挤")},
}

LEADERSHIP_SEARCH_DIMENSIONS = {
    "市场份额/排名": (
        r"(市场份额|市场占有率|市占率|行业排名)[^。；\n]{0,35}(第一|首位|排名|领先|第[一二三四五六七八九十\d]+|[1-9]\d?(?:\.\d+)?%)",
        r"(?:排名|位列|份额)[^。；\n]{0,28}(?:第一|首位|前[三五十\d]+|第[一二三四五六七八九十\d]+|[1-9]\d?(?:\.\d+)?%)",
    ),
    "销量/出货/规模": (
        r"(销量|出货量|装机量|用户数|保有量|产量|资产规模|储量)[^。；\n]{0,35}(同比|达到|超过|领先|第一|排名|万|亿|%)",
    ),
    "客户/核心供应关系": (
        r"(核心|关键|主要|指定)供应商[^。；\n]{0,35}(供货|配套|量产|定点|客户|供应链|订单)",
        r"(量产供货|客户定点|进入|配套)[^。；\n]{0,35}(供应链|头部客户|核心客户|主机厂|订单|客户)",
    ),
    "技术/专利壁垒": (
        r"(核心技术|发明专利|核心专利|自主可控|不可替代|技术领先|首创|独家|唯一|核心工艺)[^。；\n]{0,35}(领先|第一|数量|标准|认证|产品|量产|应用|客户|工艺)",
    ),
    "牌照/批件/标准资质": (
        r"(牌照|批件|注册证|国家标准|行业标准|标准制定|认证|资质|行业准入)[^。；\n]{0,35}(取得|获得|覆盖|牵头|参与|核心|领先|第一|产品|业务)",
    ),
    "渠道/区域/资源覆盖": (
        r"(全球|全国|国内|海外|区域)[^。；\n]{0,35}(客户|渠道|网点|门店|用户|基地|覆盖|产能|储量)",
    ),
}


PERCENT_PATTERN = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
YEAR_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*年")


def queries_for(key: str, name: str, code: str, context: str) -> list[str]:
    rule = RULES.get(key, {})
    short_context = " ".join(context.split()[:12])
    return [template.format(name=name, code=code, context=short_context).strip() for template in rule.get("queries", [])]


def _near_percent(text: str, terms: tuple[str, ...]) -> float | None:
    for term in terms:
        for match in re.finditer(re.escape(term), text, re.I):
            window = text[max(0, match.start() - 24):match.end() + 40]
            number = PERCENT_PATTERN.search(window)
            if number:
                return float(number.group(1))
    return None


def _numeric_ratio(key: str, text: str) -> tuple[float | None, list[str]]:
    signals: list[str] = []
    if key == "F1.era_track":
        cagr = _near_percent(text, ("CAGR", "复合增长率", "行业增速"))
        penetration = _near_percent(text, ("渗透率",))
        parts = []
        if cagr is not None:
            parts.append(1.0 if cagr > 30 else 0.8 if cagr >= 20 else 0.5 if cagr >= 10 else 0.0)
            signals.append(f"CAGR={cagr:g}%")
        if penetration is not None:
            parts.append(1.0 if 5 <= penetration <= 20 else 0.6 if penetration <= 50 else 0.2)
            signals.append(f"渗透率={penetration:g}%")
        return (sum(parts) / len(parts), signals) if parts else (None, signals)
    if key == "F1.supply_gap":
        cr3 = _near_percent(text, ("CR3", "前三家", "市场集中度"))
        years = next((float(match.group(1)) for match in YEAR_PATTERN.finditer(text) if "扩产" in text[max(0, match.start() - 20):match.end() + 20]), None)
        parts = []
        if cr3 is not None:
            parts.append(1.0 if cr3 > 70 else 0.67 if cr3 >= 50 else 0.33 if cr3 >= 30 else 0.0)
            signals.append(f"CR3={cr3:g}%")
        if years is not None:
            parts.append(1.0 if years > 3 else 0.67 if years >= 1 else 0.33)
            signals.append(f"扩产周期={years:g}年")
        return (sum(parts) / len(parts), signals) if parts else (None, signals)
    if key == "F1.capex_wave":
        value = _near_percent(text, ("资本开支", "CAPEX", "固定资产投资", "设备投资"))
        if value is not None:
            signals.append(f"CAPEX同比={value:g}%")
            return (1.0 if value > 30 else 0.75 if value >= 10 else 0.5 if value > 0 else 0.0), signals
    if key in {"F2.top1_ratio", "F2.holder_trend", "F2.pledge_unlock", "F4.overseas"}:
        terms = {
            "F2.top1_ratio": ("第一大股东", "持股比例"),
            "F2.holder_trend": ("股东户数", "股东人数"),
            "F2.pledge_unlock": ("质押比例", "解禁比例"),
            "F4.overseas": ("海外收入", "出口收入", "境外收入"),
        }[key]
        value = _near_percent(text, terms)
        if value is not None:
            signals.append(f"比例={value:g}%")
            if key == "F2.top1_ratio":
                return (1.0 if 20 <= abs(value) <= 40 else 0.67 if 10 <= abs(value) <= 55 else 0.33), signals
            if key == "F2.holder_trend":
                falling = any(term in text for term in ("下降", "减少", "环比降"))
                change = -abs(value) if falling else abs(value)
                return (1.0 if change <= -20 else 0.67 if change <= -5 else 0.33 if change < 0 else 0.0), signals
            if key == "F2.pledge_unlock":
                return (1.0 if value <= 5 else 0.5 if value <= 10 else 0.0), signals
            return (1.0 if value >= 30 else 0.67 if value >= 10 else 0.33 if value > 0 else 0.0), signals
    if key == "F5.valuation":
        value = _near_percent(text, ("PE历史分位", "PB历史分位", "估值分位"))
        if value is not None:
            signals.append(f"估值分位={value:g}%")
            return (1.0 if value <= 20 else 0.6 if value <= 50 else 0.3 if value <= 80 else 0.0), signals
    return None, signals


def _leadership_search_ratio(text: str) -> tuple[float | None, list[str]]:
    dimensions = [
        label for label, patterns in LEADERSHIP_SEARCH_DIMENSIONS.items()
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)
    ]
    if not dimensions:
        return None, []
    ratio = 1.0 if len(dimensions) >= 3 else 0.75 if len(dimensions) == 2 else 0.4
    return ratio, dimensions


def evaluate(key: str, maximum: float, rows: list[dict[str, Any]]) -> dict[str, Any]:
    rule = RULES[key]
    ranked = []
    all_signals: list[str] = []
    directions: list[int] = []
    for index, row in enumerate(rows):
        text = " ".join(str(row.get(field) or "") for field in ("title", "snippet", "content_excerpt"))
        positives = [term for term in rule.get("positive", ()) if term.lower() in text.lower()]
        negatives = [term for term in rule.get("negative", ()) if term.lower() in text.lower()]
        numeric_ratio, numeric_signals = _numeric_ratio(key, text)
        leadership_ratio, leadership_signals = _leadership_search_ratio(text) if key == "F3.leadership" else (None, [])
        if leadership_signals:
            numeric_signals.extend(leadership_signals)
        all_signals.extend(numeric_signals)
        if key == "F3.leadership":
            direction = -1 if negatives and not leadership_signals else 1 if leadership_signals else 0
        else:
            direction = 1 if len(positives) > len(negatives) else -1 if len(negatives) > len(positives) else 0
        if numeric_ratio is not None:
            direction = 1 if numeric_ratio > 0 else -1
        directions.append(direction)
        generic_ratio = 1.0 if len(positives) >= 3 else 0.75 if len(positives) == 2 else 0.5 if len(positives) == 1 else 0.0
        ratio = (
            leadership_ratio if key == "F3.leadership" and leadership_ratio is not None
            else numeric_ratio if numeric_ratio is not None
            else 0.0 if direction < 0 else generic_ratio
        )
        ranked.append((direction, ratio, index, positives, negatives, row))
    positive_count = sum(direction > 0 for direction in directions)
    negative_count = sum(direction < 0 for direction in directions)
    conflict = positive_count > 0 and negative_count > 0
    winning_direction = 1 if positive_count > negative_count else -1 if negative_count > positive_count else next((item[0] for item in ranked if item[0]), 0)
    winner = next((item for item in ranked if item[0] == winning_direction), ranked[0] if ranked else None)
    if not winner or winning_direction == 0:
        return {"status": "已搜索未命中", "score": 0.0, "reason": "搜索结果未命中可量化判断词", "signals": [], "conflict": conflict}
    score = round(maximum * winner[1], 2)
    matched = winner[3] if winning_direction > 0 else winner[4]
    reason = ("正向" if winning_direction > 0 else "负向") + "网络线索：" + "、".join((matched + all_signals)[:6])
    if conflict:
        reason += f"；存在冲突，正向 {positive_count} 条、负向 {negative_count} 条，按多数及排名处理"
    hard_cap_signals: dict[str, bool] = {}
    combined = " ".join(str(row.get("title") or "") + " " + str(row.get("snippet") or "") for row in rows)
    if key == "F2.controller_action" and any(term in combined for term in ("控股股东减持", "实际控制人减持", "实控人减持")):
        hard_cap_signals["controller_reduction"] = True
    if key == "F3.survival_risk" and any(term in combined for term in ("退市风险警示", "终止上市", "*ST")):
        hard_cap_signals["st_risk"] = True
    return {
        "status": "网络命中（未核验）",
        "score": score,
        "reason": reason,
        "signals": list(dict.fromkeys((matched + all_signals)[:8])),
        "conflict": conflict,
        "hard_cap_signals": hard_cap_signals,
    }
