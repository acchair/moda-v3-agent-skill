from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

import akshare as ak
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.daily_cache import load_daily_json, shanghai_now


OUTPUT_BASE = ROOT / "knowledge" / "research" / "congestion"
CACHE_PATH = ROOT / "knowledge" / "research" / "pipeline" / "cache" / "market_congestion_daily.json"


def _fetch_latest(max_age_days: int, now: datetime) -> dict:
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
    age_days = (now.date() - data_date).days
    return {
        "source": "AKShare/乐咕市场拥挤度",
        "source_date": data_date.isoformat(),
        "market_congestion": round(float(latest["congestion"]), 4),
        "market_congestion_date": data_date.isoformat(),
        "market_congestion_age_days": age_days,
        "market_congestion_fresh": 0 <= age_days <= max_age_days,
        "market_congestion_max_age_days": max_age_days,
    }


def collect(
    max_age_days: int = 10,
    *,
    refresh: bool = False,
    cache_path: Path = CACHE_PATH,
    now: datetime | None = None,
) -> dict:
    checked_at = shanghai_now(now)
    record = load_daily_json(
        cache_path,
        lambda: _fetch_latest(max_age_days, checked_at),
        force_refresh=refresh,
        now=checked_at,
    )
    payload = dict(record.get("payload") or {})
    source_date = payload.get("market_congestion_date")
    if source_date:
        try:
            age_days = (checked_at.date() - datetime.fromisoformat(str(source_date)).date()).days
        except ValueError:
            age_days = None
    else:
        age_days = None
    fresh = bool(
        record.get("usable")
        and age_days is not None
        and 0 <= age_days <= max_age_days
    )
    payload.update({
        "market_congestion_age_days": age_days,
        "market_congestion_fresh": fresh,
        "market_congestion_max_age_days": max_age_days,
        "market_congestion_checked_date": record.get("checked_date"),
        "market_congestion_checked_at": record.get("checked_at"),
        "market_congestion_cache_hit": record.get("cache_hit", False),
        "market_congestion_cache_status": record.get("status"),
        "market_congestion_error": record.get("error"),
    })
    return payload


def build_report(data: dict) -> str:
    freshness = "有效" if data.get("market_congestion_fresh") else "过期或不可用，仅展示不计分"
    congestion = data.get("market_congestion")
    congestion_text = f"{congestion:.4f}" if isinstance(congestion, (int, float)) else "需人工确认"
    age = data.get("market_congestion_age_days")
    age_text = f"距今 {age} 天" if age is not None else "源日期不可用"
    return "\n".join([
        "# A 股市场拥挤度",
        "",
        f"> 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}  |  数据源：AKShare/乐咕乐股",
        "",
        f"<!-- moda_congestion: {json.dumps(data, ensure_ascii=False)} -->",
        "",
        f"- 今日检查：{data.get('market_congestion_checked_date', '需人工确认')}（{'命中当日缓存' if data.get('market_congestion_cache_hit') else '本次刷新'}）",
        f"- 拥挤度：{congestion_text}",
        f"- 源数据日期：{data.get('market_congestion_date', '需人工确认')}",
        f"- 新鲜度：{freshness}（{age_text}）",
        f"- 缓存状态：{data.get('market_congestion_cache_status', '需人工确认')}"
        + (f"；失败原因：{data['market_congestion_error']}" if data.get("market_congestion_error") else ""),
        "",
        "过期数据不得触发情绪修正或高位过热 Hard Cap。",
        "",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect A-share market congestion")
    parser.add_argument("--stock", required=True, help="Used only to keep report names consistent")
    parser.add_argument("--name", default="")
    parser.add_argument("--max-age-days", type=int, default=10)
    parser.add_argument("--refresh", action="store_true", help="Force today's shared cache to refresh")
    args = parser.parse_args()
    code = args.stock.strip()
    data = collect(max_age_days=args.max_age_days, refresh=args.refresh)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{code}.md"
    path.write_text(build_report(data), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
