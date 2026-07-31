from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import time

import akshare as ak
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_BASE = ROOT / "knowledge" / "research" / "congestion"


def collect(max_age_days: int = 10) -> dict:
    frame = ak.stock_a_congestion_lg()
    if frame is None or frame.empty:
        raise ValueError("market congestion data is empty")
    clean = frame.copy()
    clean["date"] = pd.to_datetime(clean["date"], errors="coerce")
    clean["congestion"] = pd.to_numeric(clean["congestion"], errors="coerce")
    clean = clean.dropna(subset=["date", "congestion"]).sort_values("date")
    if clean.empty:
        raise ValueError("market congestion data has no usable rows")
    latest = clean.iloc[-1]
    data_date = latest["date"].date()
    age_days = (date.today() - data_date).days
    return {
        "market_congestion": round(float(latest["congestion"]), 4),
        "market_congestion_date": data_date.isoformat(),
        "market_congestion_age_days": age_days,
        "market_congestion_fresh": 0 <= age_days <= max_age_days,
        "market_congestion_max_age_days": max_age_days,
    }


def build_report(data: dict) -> str:
    freshness = "有效" if data["market_congestion_fresh"] else "过期，仅展示不计分"
    return "\n".join([
        "# A 股市场拥挤度",
        "",
        f"> 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}  |  数据源：AKShare/乐咕乐股",
        "",
        f"<!-- moda_congestion: {json.dumps(data, ensure_ascii=False)} -->",
        "",
        f"- 拥挤度：{data['market_congestion']:.4f}",
        f"- 数据日期：{data['market_congestion_date']}",
        f"- 新鲜度：{freshness}（距今 {data['market_congestion_age_days']} 天）",
        "",
        "过期数据不得触发情绪修正或高位过热 Hard Cap。",
        "",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect A-share market congestion")
    parser.add_argument("--stock", required=True, help="Used only to keep report names consistent")
    parser.add_argument("--name", default="")
    parser.add_argument("--max-age-days", type=int, default=10)
    args = parser.parse_args()
    code = args.stock.strip()
    data = collect(max_age_days=args.max_age_days)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{code}.md"
    path.write_text(build_report(data), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
