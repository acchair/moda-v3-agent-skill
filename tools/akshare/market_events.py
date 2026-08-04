from __future__ import annotations

import argparse
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import json
from pathlib import Path
import re
import sys
import time
from typing import Callable

import akshare as ak
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
NOMINEE_HOLDER_TERMS = (
    "HKSCC NOMINEES LIMITED",
    "香港中央结算（代理人）有限公司",
    "香港中央结算代理人有限公司",
    "香港中央结算(代理人)有限公司",
)


def _safe_fetch(function: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    return _fetch_result(function)[0]


def _fetch_result(function: Callable[[], pd.DataFrame]) -> tuple[pd.DataFrame, bool, str]:
    try:
        frame = function()
        return (frame if frame is not None else pd.DataFrame()), True, ""
    except Exception as exc:
        return pd.DataFrame(), False, f"{type(exc).__name__}: {exc}"


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


def _completed_quarters(reference: date | None = None, count: int = 6) -> list[str]:
    current = reference or date.today()
    year = current.year
    quarter = (current.month - 1) // 3
    if quarter == 0:
        year -= 1
        quarter = 4
    values: list[str] = []
    for _ in range(count):
        values.append(f"{year}{quarter}")
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
    return values


def fetch_fund_holding(code: str) -> pd.DataFrame:
    for quarter in _completed_quarters():
        frame = ak.stock_institute_hold_detail(stock=code, quarter=quarter)
        if frame is not None and not frame.empty:
            result = frame.copy()
            result["报告季度"] = quarter
            return result
    return pd.DataFrame()


def _holder_metrics(holders: list[dict]) -> dict:
    if not holders:
        return {}
    top1 = holders[0]
    names = [item["name"] for item in holders]
    first_name = top1["name"]
    nominee = any(term.upper() in first_name.upper() for term in NOMINEE_HOLDER_TERMS)
    effective_holder = next(
        (item for item in holders if not any(term.upper() in str(item["name"]).upper() for term in NOMINEE_HOLDER_TERMS)),
        None,
    )
    effective_name = str(effective_holder["name"]) if effective_holder else ""
    if nominee and effective_holder is None:
        background = None
        background_reason = f"第一大股东 {first_name} 为名义持有人，真实控制人待核验"
    elif nominee:
        # Nominee custody is not evidence of weak control. Use the first
        # disclosed non-nominee holder when it carries a recognizable
        # shareholder-quality signal; otherwise keep the factor unknown.
        if any(term in effective_name for term in STATE_TERMS):
            background, background_reason = 1.0, f"名义持有人 {first_name} 之外，披露的主要股东 {effective_name} 具有国资背景"
        elif any(term in effective_name for term in INDUSTRIAL_TERMS):
            background, background_reason = 0.6, f"名义持有人 {first_name} 之外，披露的主要股东 {effective_name} 具有产业资本特征"
        else:
            background = None
            background_reason = f"第一大股东 {first_name} 为名义持有人，实际控制人与控股股东性质需结合公司公告核验（当前参考 {effective_name}）"
    elif any(term in first_name for term in STATE_TERMS):
        background, background_reason = 1.0, f"第一大股东 {first_name} 具有明确国资背景"
    elif any(term in first_name for term in INDUSTRIAL_TERMS):
        background, background_reason = 0.6, f"第一大股东 {first_name} 具有产业资本特征"
    else:
        background = None
        background_reason = f"第一大股东 {first_name} 未识别出国资或强产业资本背景，背景质量需人工确认"
    quality_count = sum(any(term in name for term in INSTITUTION_TERMS) for name in names)
    result = {
        "top_holders": holders,
        "top1_holder_pct": top1["ratio"],
        "background_reason": background_reason,
        "background_nominee_holder": nominee,
        "effective_holder_name": effective_name,
        "background_partial": background is not None and background not in (0.0, 1.0),
        "top10_quality": quality_count / len(names),
        "top10_quality_reason": f"前十大股东中识别到 {quality_count}/{len(names)} 个国资或长期机构",
        "top10_partial": len(names) < 10,
    }
    if background is not None:
        result["background_quality"] = background
    return result


def _security_name(frames: dict[str, pd.DataFrame]) -> str:
    aliases = ("SECURITY_NAME_ABBR", "SECURITY_NAME", "股票简称", "证券简称", "stock_name")
    for frame in frames.values():
        if frame.empty:
            continue
        for column in aliases:
            if column not in frame.columns:
                continue
            values = frame[column].dropna().astype(str).str.strip()
            value = next((item for item in values if item and item.lower() != "nan"), "")
            if value:
                return value
    return ""


def collect(code: str) -> tuple[dict, dict[str, pd.DataFrame]]:
    fetchers = {
        "holder_num": lambda: provider.holder_num_change(code),
        "lockup": lambda: provider.lockup_expiry(code),
        "concepts": lambda: provider.concept_blocks(code),
        "research": lambda: provider.research_reports(code, max_pages=1),
        "pledge": lambda: fetch_pledge(code),
        "fund_holding": lambda: fetch_fund_holding(code),
        "top_holders": lambda: fetch_top_holders(code),
    }
    frames: dict[str, pd.DataFrame] = {}
    fetch_status: dict[str, dict] = {}
    # These endpoints are independent and mostly network-bound. Running them
    # together prevents one slow provider from consuming the whole collector
    # timeout and preserves per-endpoint status for downstream scoring.
    with ThreadPoolExecutor(max_workers=len(fetchers)) as executor:
        futures = {key: executor.submit(_fetch_result, fetcher) for key, fetcher in fetchers.items()}
        for key, future in futures.items():
            try:
                result = future.result()
            except Exception as exc:
                result = (pd.DataFrame(), False, f"{type(exc).__name__}: {exc}")
            if key == "top_holders":
                holders, ok, error = result
                fetch_status[key] = {"ok": ok, "error": error}
            else:
                frame, ok, error = result
                frames[key] = frame
                fetch_status[key] = {"ok": ok, "error": error}
    if "top_holders" not in fetch_status:
        holders = []
    structured = _holder_metrics(holders)
    structured["market_event_fetch_status"] = fetch_status
    structured["pledge_fetch_ok"] = fetch_status["pledge"]["ok"]
    structured["unlock_fetch_ok"] = fetch_status["lockup"]["ok"]
    security_name = _security_name(frames)
    if security_name:
        structured["security_name"] = security_name

    fund_holding = frames["fund_holding"]
    if not fund_holding.empty:
        ratios = pd.to_numeric(fund_holding.get("持股比例"), errors="coerce")
        changes = pd.to_numeric(fund_holding.get("持股比例增幅"), errors="coerce")
        structured["fund_holding_quarter"] = str(fund_holding.iloc[0].get("报告季度") or "")
        structured["fund_holding_institutions"] = int(len(fund_holding))
        if ratios.notna().any():
            structured["fund_holding_ratio"] = float(ratios.fillna(0).sum())
        if changes.notna().any():
            structured["fund_holding_change_pct"] = float(changes.fillna(0).sum())
            change = structured["fund_holding_change_pct"]
            base_quality = float(structured.get("top10_quality") or 0)
            fund_quality = 1.0 if change > 0 else 0.5 if change == 0 else 0.0
            structured["top10_quality"] = min(1.0, base_quality * 0.75 + fund_quality * 0.25)
        if "top10_quality_reason" in structured:
            change = structured.get("fund_holding_change_pct")
            change_text = f"；基金持股比例变化 {change:.2f} 个百分点" if change is not None else "；基金持仓变化需人工确认"
            structured["top10_quality_reason"] += change_text

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
    if fetch_status["lockup"]["ok"]:
        if lockup.empty:
            structured["unlock_ratio"] = 0.0
        else:
            ratio_column = next((column for column in ("FREE_RATIO", "TOTAL_RATIO") if column in lockup.columns), None)
            if ratio_column is not None:
                ratios = pd.to_numeric(lockup[ratio_column], errors="coerce")
                if ratios.notna().any():
                    structured["unlock_ratio"] = float(ratios.fillna(0).sum())

    pledge = frames["pledge"]
    if fetch_status["pledge"]["ok"]:
        if pledge.empty:
            structured["pledge_ratio"] = 0.0
        else:
            active = pledge[~pledge.get("UNFREEZE_STATE", pd.Series(index=pledge.index, dtype=str)).astype(str).str.contains("已解押", na=False)]
            if active.empty:
                structured["pledge_ratio"] = 0.0
            elif "PF_TSR" in active.columns:
                ratios = pd.to_numeric(active["PF_TSR"], errors="coerce")
                if ratios.notna().any():
                    structured["pledge_ratio"] = float(ratios.fillna(0).sum())

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
            # Research titles are discovery clues only. The scoring evidence
            # layer must corroborate leadership with sector-appropriate facts.
            structured["leadership_clues"] = hits
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
        f"- 接口状态：{'成功' if structured.get('unlock_fetch_ok') else '失败，需人工确认'}",
        "",
        *_frame_table(frames["lockup"], ["FREE_DATE", "CURRENT_FREE_SHARES", "FREE_RATIO", "FREE_SHARES_TYPE"]),
        "",
        "## 股权质押",
        "",
        f"- 接口状态：{'成功' if structured.get('pledge_fetch_ok') else '失败，需人工确认'}",
        "",
        *_frame_table(frames["pledge"], ["NOTICE_DATE", "HOLDER_NAME", "PF_TSR", "UNFREEZE_STATE"]),
        "",
        "## 基金持仓季度变化",
        "",
        *_frame_table(frames["fund_holding"], ["报告季度", "持股机构简称", "持股比例", "持股比例增幅", "占流通股比例"]),
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
