from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path
import time

import akshare as ak
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
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


def collect(context: str, lookback_days: int = 30) -> dict:
    mapping = match_mapping(context)
    if not mapping:
        return {
            "supply_mapping_found": False,
            "supply_reason": "主营、行业和概念未匹配商品期货映射，不强行评分",
        }

    start = (date.today() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    spot = pd.DataFrame()
    inventory = pd.DataFrame()
    errors: list[str] = []
    try:
        spot = ak.futures_spot_price_daily(start_day=start, end_day=end, vars_list=[mapping["spot_var"]])
    except Exception as exc:
        errors.append(f"spot:{type(exc).__name__}")
    try:
        inventory = ak.futures_inventory_em(symbol=mapping["inventory_symbol"])
    except Exception as exc:
        errors.append(f"inventory:{type(exc).__name__}")

    evidence: list[dict] = []
    if not spot.empty and "spot_price" in spot:
        spot_change = _change(spot["spot_price"])
        if spot_change is not None:
            evidence.append({"category": "spot_price", "value": round(spot_change, 4), "tightening": spot_change >= 0.03})
    if not spot.empty and "dom_basis_rate" in spot:
        basis = pd.to_numeric(spot["dom_basis_rate"], errors="coerce").dropna()
        if not basis.empty:
            latest_basis = float(basis.iloc[-1])
            evidence.append({"category": "basis", "value": round(latest_basis, 4), "tightening": latest_basis <= -0.01})
    if not inventory.empty and "库存" in inventory:
        recent_inventory = inventory.tail(max(10, min(len(inventory), 30)))
        inventory_change = _change(recent_inventory["库存"])
        if inventory_change is not None:
            evidence.append({"category": "inventory", "value": round(inventory_change, 4), "tightening": inventory_change <= -0.03})

    positive = sum(item["tightening"] for item in evidence)
    if len(evidence) >= 2 and positive >= 2:
        tightening: bool | None = True
    elif len(evidence) >= 2 and positive == 0:
        tightening = False
    else:
        tightening = None
    return {
        "supply_mapping_found": True,
        "supply_commodity": mapping["commodity"],
        "supply_chain_name": mapping["chain_name"],
        "supply_matched_terms": mapping["matched_terms"],
        "supply_evidence_count": len(evidence),
        "supply_positive_count": positive,
        "supply_tightening": tightening,
        "supply_evidence": evidence,
        "supply_errors": errors,
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
