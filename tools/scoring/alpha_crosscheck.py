from __future__ import annotations

from typing import Any


POSITIVE_SIGNALS = {"建仓", "加仓"}
NEGATIVE_SIGNALS = {"减仓", "清仓"}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _vote(direction: int, reasons: list[str], available: int) -> dict[str, Any]:
    return {
        "direction": direction,
        "label": "看多" if direction > 0 else "看空" if direction < 0 else "中性/不足",
        "available_checks": available,
        "reason": "；".join(reasons) if reasons else "可用证据不足",
    }


def _quant_screen(evidence: dict[str, Any]) -> dict[str, Any]:
    bullish = 0
    bearish = 0
    available = 0
    reasons: list[str] = []

    structure = evidence.get("ma_structure")
    if structure in {"bullish", "bearish", "mixed"}:
        available += 1
        if structure == "bullish":
            bullish += 1
            reasons.append("均线多头")
        elif structure == "bearish":
            bearish += 1
            reasons.append("均线空头")
        else:
            reasons.append("均线混合")

    momentum = _number(evidence.get("momentum_20d"))
    if momentum is not None:
        available += 1
        if momentum >= 0.03:
            bullish += 1
            reasons.append(f"20日动量 {momentum:.1%}")
        elif momentum <= -0.03:
            bearish += 1
            reasons.append(f"20日动量 {momentum:.1%}")
        else:
            reasons.append(f"20日动量中性 {momentum:.1%}")

    slope = _number(evidence.get("ma20_slope_5d"))
    if slope is not None:
        available += 1
        if slope >= 0.01:
            bullish += 1
            reasons.append(f"MA20斜率 {slope:.1%}")
        elif slope <= -0.01:
            bearish += 1
            reasons.append(f"MA20斜率 {slope:.1%}")
        else:
            reasons.append(f"MA20斜率平缓 {slope:.1%}")

    volume_ratio = _number(evidence.get("volume_ratio_20d"))
    if volume_ratio is not None:
        available += 1
        if volume_ratio >= 1.15 and momentum is not None and momentum > 0:
            bullish += 1
            reasons.append(f"上涨放量 {volume_ratio:.2f}倍")
        elif volume_ratio >= 1.15 and momentum is not None and momentum < 0:
            bearish += 1
            reasons.append(f"下跌放量 {volume_ratio:.2f}倍")
        else:
            reasons.append(f"量比 {volume_ratio:.2f}倍")

    direction = 1 if available >= 3 and bullish - bearish >= 2 else -1 if available >= 3 and bearish - bullish >= 2 else 0
    return {"method": "量化选股筛选", **_vote(direction, reasons, available)}


def _thesis_tracker(evidence: dict[str, Any]) -> dict[str, Any]:
    bullish = 0
    bearish = 0
    available = 0
    reasons: list[str] = []

    trend = evidence.get("alpha_trend")
    if trend in {"上升", "下降", "持平"}:
        available += 1
        if trend == "上升":
            bullish += 1
        elif trend == "下降":
            bearish += 1
        reasons.append(f"Alpha趋势{trend}")

    signal = evidence.get("technical_signal")
    if signal:
        available += 1
        if signal in POSITIVE_SIGNALS:
            bullish += 1
        elif signal in NEGATIVE_SIGNALS:
            bearish += 1
        reasons.append(f"信号{signal}")

    position = _number(evidence.get("technical_position"))
    if position is not None:
        available += 1
        if position <= 0.38:
            bullish += 1
            reasons.append(f"位置偏低 {position:.2f}")
        elif position >= 0.81:
            bearish += 1
            reasons.append(f"位置偏高 {position:.2f}")
        else:
            reasons.append(f"位置中性 {position:.2f}")

    overheat = evidence.get("technical_overheat")
    if overheat is not None:
        available += 1
        if overheat is True:
            bearish += 1
            reasons.append("技术过热")
        else:
            reasons.append("未见技术过热")

    direction = 1 if available >= 3 and bullish - bearish >= 2 else -1 if available >= 3 and bearish - bullish >= 2 else 0
    return {"method": "投资逻辑追踪", **_vote(direction, reasons, available)}


def evaluate(evidence: dict[str, Any], base_score: int = 0) -> dict[str, Any]:
    methods = [_quant_screen(evidence), _thesis_tracker(evidence)]
    available = [item for item in methods if item["available_checks"] >= 3]
    directions = [item["direction"] for item in available]
    if len(available) < 2:
        status = "部分覆盖" if available else "证据不足"
    elif directions == [1, 1]:
        status = "同向看多"
    elif directions == [-1, -1]:
        status = "同向看空"
    elif directions[0] != directions[1]:
        status = "方向分歧"
    else:
        status = "方向中性"

    return {
        "base_score": base_score,
        "final_score": base_score,
        "status": status,
        "methods": methods,
    }
