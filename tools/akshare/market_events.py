from __future__ import annotations

import argparse
from io import StringIO
import json
from pathlib import Path
import re
import sys
import time
from typing import Callable

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.providers import a_stock_data_provider as provider


OUTPUT_BASE = ROOT / "knowledge" / "research" / "market_events"
STATE_TERMS = ("国有", "国资", "人民政府", "财政", "国务院", "中央汇金", "国有资本", "国资委")
INSTITUTION_TERMS = STATE_TERMS + ("社保", "基金", "保险", "证券", "银行", "中央结算", "投资管理", "资产管理")
INDUSTRIAL_TERMS = ("产业投资", "产业资本", "控股集团", "实业集团")


def _safe_fetch(function: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    try:
        frame = function()
        return frame if frame is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def fetch_top_holders(code: str, timeout: float = 15) -> list[dict]:
    url = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_StockHolder/stockid/{code}.phtml"
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    for table in pd.read_html(StringIO(response.text)):
        raw = table.iloc[:, :5].copy()
        first = raw.iloc[:, 0].astype(str)
        header_rows = raw.index[first.eq("编号")].tolist()
        if not header_rows:
            continue
        header = header_rows[0]
        rows: list[dict] = []
        for _, row in raw.iloc[header + 1:].iterrows():
            if not str(row.iloc[0]).strip().isdigit():
                break
            ratio_text = str(row.iloc[3]).replace("↑", "").replace("↓", "")
            ratio = pd.to_numeric(pd.Series([ratio_text]), errors="coerce").iloc[0]
            if pd.isna(ratio):
                continue
            rows.append({"rank": int(row.iloc[0]), "name": str(row.iloc[1]).strip(), "ratio": float(ratio)})
        if rows:
            return rows[:10]
    return []


def fetch_pledge(code: str, timeout: float = 15) -> pd.DataFrame:
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "NOTICE_DATE",
        "sortTypes": "-1",
        "pageSize": 100,
        "pageNumber": 1,
        "reportName": "RPTA_APP_ACCUMDETAILS",
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{code}")',
        "source": "WEB",
        "client": "WEB",
    }
    response = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    result = response.json().get("result") or {}
    return pd.DataFrame(result.get("data") or [])


def _holder_metrics(holders: list[dict]) -> dict:
    if not holders:
        return {}
    top1 = holders[0]
    names = [item["name"] for item in holders]
    first_name = top1["name"]
    if any(term in first_name for term in STATE_TERMS):
        background, background_reason = 1.0, f"第一大股东 {first_name} 具有明确国资背景"
    elif any(term in first_name for term in INDUSTRIAL_TERMS):
        background, background_reason = 0.6, f"第一大股东 {first_name} 具有产业资本特征"
    else:
        background, background_reason = 0.0, f"第一大股东 {first_name} 未识别出国资或强产业资本背景"
    quality_count = sum(any(term in name for term in INSTITUTION_TERMS) for name in names)
    return {
        "top_holders": holders,
        "top1_holder_pct": top1["ratio"],
        "background_quality": background,
        "background_reason": background_reason,
        "background_partial": background not in (0.0, 1.0),
        "top10_quality": quality_count / len(names),
        "top10_quality_reason": f"前十大股东中识别到 {quality_count}/{len(names)} 个国资或长期机构",
        "top10_partial": len(names) < 10,
    }


def collect(code: str) -> tuple[dict, dict[str, pd.DataFrame]]:
    frames = {
        "holder_num": _safe_fetch(lambda: provider.holder_num_change(code)),
        "lockup": _safe_fetch(lambda: provider.lockup_expiry(code)),
        "concepts": _safe_fetch(lambda: provider.concept_blocks(code)),
        "research": _safe_fetch(lambda: provider.research_reports(code, max_pages=1)),
        "pledge": _safe_fetch(lambda: fetch_pledge(code)),
    }
    try:
        holders = fetch_top_holders(code)
    except Exception:
        holders = []
    structured = _holder_metrics(holders)

    holder_num = frames["holder_num"]
    if not holder_num.empty:
        row = holder_num.iloc[0]
        ratio = pd.to_numeric(pd.Series([row.get("HOLDER_NUM_RATIO")]), errors="coerce").iloc[0]
        if pd.notna(ratio):
            structured["holder_count_change_pct"] = float(ratio)
        current = pd.to_numeric(pd.Series([row.get("HOLDER_NUM")]), errors="coerce").iloc[0]
        if pd.notna(current):
            structured["holder_count"] = int(current)

    lockup = frames["lockup"]
    if not lockup.empty:
        ratios = pd.to_numeric(lockup.get("FREE_RATIO", lockup.get("TOTAL_RATIO")), errors="coerce")
        structured["unlock_ratio"] = float(ratios.fillna(0).sum())
    else:
        structured["unlock_ratio"] = 0.0

    pledge = frames["pledge"]
    if not pledge.empty:
        active = pledge[~pledge.get("UNFREEZE_STATE", pd.Series(index=pledge.index, dtype=str)).astype(str).str.contains("已解押", na=False)]
        ratios = pd.to_numeric(active.get("PF_TSR"), errors="coerce") if not active.empty else pd.Series(dtype=float)
        structured["pledge_ratio"] = float(ratios.fillna(0).sum()) if not ratios.empty else 0.0
    else:
        structured["pledge_ratio"] = 0.0

    concepts = frames["concepts"]
    if not concepts.empty:
        name_column = "f14" if "f14" in concepts.columns else concepts.columns[-1]
        structured["concepts"] = concepts[name_column].dropna().astype(str).drop_duplicates().head(30).tolist()

    research = frames["research"]
    if not research.empty and "title" in research.columns:
        titles = research["title"].dropna().astype(str).head(20).tolist()
        structured["research_titles"] = titles
        research_text = " ".join(titles)
        leadership_terms = ("龙头", "核心供应商", "行业领先", "国产替代", "全球领先")
        hits = [term for term in leadership_terms if term in research_text]
        if hits:
            structured["leadership_strength"] = min(1.0, 0.5 + len(hits) * 0.2)
            structured["leadership_reason"] = "研报标题出现：" + "、".join(hits)
            structured["leadership_partial"] = True
    return structured, frames


def _frame_table(frame: pd.DataFrame, columns: list[str], limit: int = 8) -> list[str]:
    existing = [column for column in columns if column in frame.columns]
    if frame.empty or not existing:
        return ["需人工确认：无数据。"]
    lines = ["| " + " | ".join(existing) + " |", "|" + "|".join(["---"] * len(existing)) + "|"]
    for _, row in frame[existing].head(limit).iterrows():
        values = [str(row.get(column, "")).replace("|", "/")[:80] for column in existing]
        lines.append("| " + " | ".join(values) + " |")
    return lines


def build_report(code: str, name: str, structured: dict, frames: dict[str, pd.DataFrame]) -> str:
    lines = [
        f"# 股东与市场事件：{name or code}（{code}）",
        "",
        f"> 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}  |  数据源：EastMoney + Sina",
        "",
        f"<!-- moda_market_events: {json.dumps(structured, ensure_ascii=False)} -->",
        "",
        "## 前十大股东",
        "",
        "| 排名 | 股东 | 持股比例 |",
        "|---:|---|---:|",
    ]
    for item in structured.get("top_holders", []):
        lines.append(f"| {item['rank']} | {item['name']} | {item['ratio']:.2f}% |")
    if not structured.get("top_holders"):
        lines.append("| - | 需人工确认 | - |")
    lines += [
        "",
        "## 股东户数变化",
        "",
        *_frame_table(frames["holder_num"], ["END_DATE", "HOLDER_NUM", "PRE_HOLDER_NUM", "HOLDER_NUM_RATIO"]),
        "",
        "## 未来解禁",
        "",
        *_frame_table(frames["lockup"], ["FREE_DATE", "CURRENT_FREE_SHARES", "FREE_RATIO", "FREE_SHARES_TYPE"]),
        "",
        "## 股权质押",
        "",
        *_frame_table(frames["pledge"], ["NOTICE_DATE", "HOLDER_NAME", "PF_TSR", "UNFREEZE_STATE"]),
        "",
        "## 概念与研报",
        "",
        "- 概念：" + ("、".join(structured.get("concepts", [])) if structured.get("concepts") else "需人工确认"),
        "- 近期研报：" + ("；".join(structured.get("research_titles", [])[:5]) if structured.get("research_titles") else "需人工确认"),
        "",
        "## 免责声明",
        "",
        "本报告基于公开股东和市场事件数据，仅供研究参考，不构成投资建议。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect shareholder and market-event evidence")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    code = args.stock.strip()
    if len(code) != 6 or not code.isdigit():
        parser.error("--stock must be a 6-digit A-share code")
    structured, frames = collect(code)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{code}.md"
    path.write_text(build_report(code, args.name or code, structured, frames), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
