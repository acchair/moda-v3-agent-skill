from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time

import akshare as ak
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_BASE = ROOT / "knowledge" / "research" / "macro_policy"
GOV_POLICY_URL = "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json"
SUPPORT_TERMS = ("支持", "促进", "鼓励", "发展", "行动方案", "规划", "补贴", "试点", "扩内需")
TIGHTEN_TERMS = ("整治", "规范", "限制", "禁止", "处罚", "监管", "风险提示")


def _latest_lpr() -> dict:
    frame = ak.macro_china_lpr()
    if frame is None or frame.empty:
        return {}
    clean = frame.copy()
    clean["TRADE_DATE"] = pd.to_datetime(clean["TRADE_DATE"], errors="coerce")
    clean = clean.dropna(subset=["TRADE_DATE"]).sort_values("TRADE_DATE")
    latest = clean.iloc[-1]
    previous = clean.iloc[-2] if len(clean) > 1 else latest
    return {
        "lpr_date": latest["TRADE_DATE"].date().isoformat(),
        "lpr_1y": float(latest["LPR1Y"]),
        "lpr_5y": float(latest["LPR5Y"]),
        "lpr_1y_change": round(float(latest["LPR1Y"] - previous["LPR1Y"]), 4),
        "lpr_5y_change": round(float(latest["LPR5Y"] - previous["LPR5Y"]), 4),
    }


def _latest_pmi() -> dict:
    frame = ak.macro_china_pmi()
    if frame is None or frame.empty:
        return {}
    clean = frame.copy()
    clean["_date"] = pd.to_datetime(clean["月份"].astype(str).str.replace("月份", "", regex=False), format="%Y年%m", errors="coerce")
    clean = clean.dropna(subset=["_date"]).sort_values("_date")
    latest = clean.iloc[-1]
    return {
        "pmi_date": latest["_date"].date().isoformat(),
        "manufacturing_pmi": float(latest["制造业-指数"]),
        "non_manufacturing_pmi": float(latest["非制造业-指数"]),
    }


def _policy_titles(industry: str, timeout: float = 12) -> tuple[list[dict], list[dict]]:
    response = requests.get(GOV_POLICY_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    response.raise_for_status()
    rows = response.json()
    unique = [
        {
            "title": str(item.get("TITLE") or "").strip(),
            "url": str(item.get("URL") or ""),
            "date": str(item.get("DOCRELPUBTIME") or ""),
        }
        for item in rows
        if str(item.get("TITLE") or "").strip()
    ]
    keywords = [word for word in re.split(r"[\s,/，、;；]+", industry) if len(word) >= 2 and word not in ("其他", "综合")]
    relevant = [item for item in unique if any(word in item["title"] for word in keywords)]
    return unique[:30], relevant[:10]


def collect(industry: str) -> dict:
    data: dict = {"macro_industry": industry, "macro_errors": []}
    try:
        data.update(_latest_lpr())
    except Exception as exc:
        data["macro_errors"].append(f"lpr:{type(exc).__name__}")
    try:
        data.update(_latest_pmi())
    except Exception as exc:
        data["macro_errors"].append(f"pmi:{type(exc).__name__}")
    try:
        latest, relevant = _policy_titles(industry)
        data["official_policy_titles"] = latest
        data["relevant_policy_titles"] = relevant
        data["policy_evidence_count"] = len(relevant)
        text = " ".join(item["title"] for item in relevant)
        positive = sum(term in text for term in SUPPORT_TERMS)
        negative = sum(term in text for term in TIGHTEN_TERMS)
        data["policy_direction"] = "支持" if positive > negative else "收紧" if negative > positive else "中性/无直接命中"
    except Exception as exc:
        data["macro_errors"].append(f"policy:{type(exc).__name__}")
    return data


def build_report(code: str, name: str, data: dict) -> str:
    lines = [
        f"# 宏观与政策背景：{name or code}（{code}）",
        "",
        f"> 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}  |  数据源：AKShare/中国人民银行口径 + 中国政府网",
        "",
        f"<!-- moda_macro_policy: {json.dumps(data, ensure_ascii=False)} -->",
        "",
        f"- 行业上下文：{data.get('macro_industry') or '需人工确认'}",
        f"- LPR：1 年 {data.get('lpr_1y', '需人工确认')}%，5 年 {data.get('lpr_5y', '需人工确认')}%（{data.get('lpr_date', '需人工确认')}）",
        f"- PMI：制造业 {data.get('manufacturing_pmi', '需人工确认')}，非制造业 {data.get('non_manufacturing_pmi', '需人工确认')}（{data.get('pmi_date', '需人工确认')}）",
        f"- 相关政策：{data.get('policy_evidence_count', '需人工确认')} 条，方向 {data.get('policy_direction', '需人工确认')}",
        "",
        "## 相关政策标题",
        "",
    ]
    relevant = data.get("relevant_policy_titles") or []
    lines.extend(f"- {item['title']}" for item in relevant)
    if not relevant:
        lines.append("- 当前政府网最新政策页未命中该行业关键词，不据此推断政策利空。")
    lines += ["", "宏观与政策是背景证据，不因单条标题直接改变个股评分。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect macro and official policy context")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--industry", default="综合")
    args = parser.parse_args()
    code = args.stock.strip()
    data = collect(args.industry)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{code}.md"
    path.write_text(build_report(code, args.name or code, data), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
