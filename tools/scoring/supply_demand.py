from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import time

import akshare as ak
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.data_call import dataframe_empty, run_fallback_chain
MAP_PATH = Path(__file__).with_name("commodity_futures_map.csv")
OUTPUT_BASE = ROOT / "knowledge" / "research" / "supply_demand"


def match_mapping(context: str) -> dict | None:
    normalized = context.lower()
    with MAP_PATH.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    matches = []
    for row in rows:
        terms = [term.strip() for term in row["stock_keyword"].split("|") if term.strip()]
        hit_terms = [term for term in terms if term.lower() in normalized]
        if hit_terms:
            matches.append((len(max(hit_terms, key=len)), float(row.get("weight") or 0), row, hit_terms))
    if not matches:
        return None
    _, _, row, hit_terms = max(matches, key=lambda item: (item[0], item[1]))
    return {**row, "matched_terms": hit_terms}


def _change(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 2 or clean.iloc[0] == 0:
        return None
    return float(clean.iloc[-1] / clean.iloc[0] - 1)


def _latest_value(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.iloc[-1]) if not clean.empty else None


def _normalize_spot(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    aliases = {"日期": "date", "交易日期": "date", "spot_price": "spot_price", "现货价": "spot_price", "价格": "spot_price", "基差": "dom_basis_rate", "基差率": "dom_basis_rate"}
    frame = frame.rename(columns={key: value for key, value in aliases.items() if key in frame.columns}).copy()
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("spot_price", "dom_basis_rate"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _normalize_inventory(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.rename(columns={"日期": "date", "统计日期": "date", "库存量": "库存", "库存": "库存"}).copy()
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    if "库存" in frame.columns:
        frame["库存"] = pd.to_numeric(frame["库存"], errors="coerce")
    return frame


def _future_spot_fallback(mapping: dict) -> pd.DataFrame:
    """Return only a directly mapped futures snapshot; never invent a history."""
    frame = ak.futures_zh_spot(symbol=mapping["futures_symbol"], market=mapping["exchange"])
    frame = _normalize_spot(frame)
    if "spot_price" not in frame.columns:
        price_column = next((item for item in ("最新价", "最新", "收盘价") if item in frame.columns), None)
        if price_column:
            frame["spot_price"] = pd.to_numeric(frame[price_column], errors="coerce")
    return frame


def _future_inventory_fallback(mapping: dict) -> pd.DataFrame:
    return _normalize_inventory(ak.futures_inventory_99(symbol=mapping["inventory_symbol"]))


def collect(context: str, lookback_days: int = 30) -> dict:
    mapping = match_mapping(context)
    if not mapping:
        return {
            "supply_mapping_found": False,
            "fetch_state": "empty",
            "source_chain": [],
            "supply_reason": "主营、行业和概念未匹配商品期货映射，不强行评分",
        }

    start = (date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    spot_result = run_fallback_chain(
        "商品现货/基差",
        [
            ("AKShare/现货库存主接口", lambda: _normalize_spot(ak.futures_spot_price_daily(start_day=start, end_day=end, vars_list=[mapping["spot_var"]]))),
            ("AKShare/对应期货快照", lambda: _future_spot_fallback(mapping)),
        ],
        seconds=20,
        empty=dataframe_empty,
    )
    inventory_result = run_fallback_chain(
        "商品库存",
        [
            ("AKShare/库存主接口", lambda: _normalize_inventory(ak.futures_inventory_em(symbol=mapping["inventory_symbol"]))),
            ("AKShare/库存等价接口", lambda: _future_inventory_fallback(mapping)),
        ],
        seconds=20,
        empty=dataframe_empty,
    )
    spot = spot_result.value if isinstance(spot_result.value, pd.DataFrame) else pd.DataFrame()
    inventory = inventory_result.value if isinstance(inventory_result.value, pd.DataFrame) else pd.DataFrame()
    errors = [item for item in (spot_result.error, inventory_result.error) if item]
    fetch_status = {
        "spot": {"fetch_state": spot_result.fetch_state, "source": spot_result.source, "source_chain": spot_result.source_chain or [], "error": spot_result.error or None},
        "inventory": {"fetch_state": inventory_result.fetch_state, "source": inventory_result.source, "source_chain": inventory_result.source_chain or [], "error": inventory_result.error or None},
    }

    evidence: list[dict] = []
    if not spot.empty and "spot_price" in spot:
        spot_change = _change(spot["spot_price"])
        if spot_change is not None:
            evidence.append({"category": "spot_price", "value": round(spot_change, 4), "tightening": spot_change >= 0.03,
                             "threshold": 0.03, "window_days": lookback_days})
    if not spot.empty and "dom_basis_rate" in spot:
        latest_basis = _latest_value(spot["dom_basis_rate"])
        if latest_basis is not None:
            # Positive basis/backwardation is the tightening signal; a negative
            # basis is a loosening signal. Keep the threshold explicit.
            evidence.append({"category": "basis", "value": round(latest_basis, 4), "tightening": latest_basis >= 0.01,
                             "threshold": 0.01, "window_days": lookback_days})
    if not inventory.empty and "库存" in inventory:
        recent_inventory = inventory.tail(max(10, min(len(inventory), 30)))
        inventory_change = _change(recent_inventory["库存"])
        if inventory_change is not None:
            evidence.append({"category": "inventory", "value": round(inventory_change, 4), "tightening": inventory_change <= -0.03,
                             "threshold": -0.03, "window_days": min(lookback_days, 30)})

    positive = sum(item["tightening"] for item in evidence)
    categories = {item["category"] for item in evidence}
    negative = len(evidence) - positive
    if len(categories) >= 2 and positive >= 2 and negative == 0:
        tightening: bool | None = True
        signal_status = "tightening"
    elif len(categories) >= 2 and negative >= 2 and positive == 0:
        tightening = False
        signal_status = "loosening"
    elif len(categories) >= 2 and positive and negative:
        tightening = None
        signal_status = "conflict"
    else:
        tightening = None
        signal_status = "insufficient"
    return {
        "supply_mapping_found": True,
        "supply_commodity": mapping["commodity"],
        "supply_chain_name": mapping["chain_name"],
        "supply_matched_terms": mapping["matched_terms"],
        "supply_evidence_count": len(evidence),
        "supply_positive_count": positive,
        "supply_negative_count": negative,
        "supply_category_count": len(categories),
        "supply_signal_status": signal_status,
        "supply_tightening": tightening,
        "supply_evidence": evidence,
        "supply_errors": errors,
        "fetch_state": "failed" if all(item["fetch_state"] == "failed" for item in fetch_status.values()) else "fallback_ok" if any(item["fetch_state"] == "fallback_ok" for item in fetch_status.values()) else "ok" if evidence else "empty",
        "source_chain": {key: value["source_chain"] for key, value in fetch_status.items()},
        "supply_fetch_status": fetch_status,
        "supply_reason": "现货、基差和库存接口返回字段不足或不可用，供需证据类别不可用" if not evidence else None,
    }


def build_report(code: str, name: str, data: dict) -> str:
    lines = [
        f"# 商品供需证据：{name or code}（{code}）",
        "",
        f"> 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}  |  数据源：AKShare 现货/基差/库存",
        "",
        f"<!-- moda_supply_demand: {json.dumps(data, ensure_ascii=False)} -->",
        "",
    ]
    if not data.get("supply_mapping_found"):
        lines.append(f"- {data['supply_reason']}")
    else:
        lines += [
            f"- 匹配商品：{data['supply_commodity']}（命中：{'、'.join(data['supply_matched_terms'])}）",
            f"- 独立证据：{data['supply_evidence_count']} 类，其中趋紧 {data['supply_positive_count']} 类",
            f"- 供给趋紧结论：{data['supply_tightening'] if data['supply_tightening'] is not None else '方向不一致'}",
            "",
            "| 证据类别 | 数值 | 是否趋紧 |",
            "|---|---:|---|",
        ]
        for item in data["supply_evidence"]:
            lines.append(f"| {item['category']} | {item['value']:.4f} | {'是' if item['tightening'] else '否'} |")
    lines += ["", "至少两类独立证据同向，才确认供需趋紧。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect commodity supply-demand evidence")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--context", default="")
    args = parser.parse_args()
    code = args.stock.strip()
    data = collect(" ".join([args.name, args.context]))
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{code}.md"
    path.write_text(build_report(code, args.name or code, data), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
