from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.data_call import dataframe_empty, run_direct_then_proxy, run_with_timeout
from tools.data_patch import apply_data_patches
from tools.efinance.provider import search_stock

LOCAL_SECTOR_FALLBACKS = {
    "锂电池": [
        ("300750", "宁德时代"), ("002466", "天齐锂业"), ("002460", "赣锋锂业"),
        ("002812", "恩捷股份"), ("300014", "亿纬锂能"), ("002709", "天赐材料"),
        ("300568", "星源材质"), ("603799", "华友钴业"),
    ],
    "半导体": [
        ("688981", "中芯国际"), ("603501", "韦尔股份"), ("603290", "斯达半导"),
        ("688012", "中微公司"), ("688008", "澜起科技"), ("300782", "卓胜微"),
        ("002371", "北方华创"), ("688256", "寒武纪"),
    ],
    "半导体设备": [
        ("002371", "北方华创"), ("688012", "中微公司"), ("688072", "拓荆科技"),
        ("688120", "华海清科"), ("300604", "长川科技"), ("688596", "正帆科技"),
    ],
    "机器人": [
        ("300024", "机器人"), ("002747", "埃斯顿"), ("002031", "巨轮智能"),
        ("688017", "绿的谐波"), ("002698", "博实股份"), ("300124", "汇川技术"),
        ("603728", "鸣志电器"), ("002896", "中大力德"),
    ],
    "储能": [
        ("300750", "宁德时代"), ("300274", "阳光电源"), ("300014", "亿纬锂能"),
        ("002335", "科华数据"), ("002518", "科士达"), ("688063", "派能科技"),
    ],
    "人工智能": [
        ("688256", "寒武纪"), ("002230", "科大讯飞"), ("300308", "中际旭创"),
        ("300502", "新易盛"), ("603019", "中科曙光"), ("000977", "浪潮信息"),
    ],
}


def _normalize_code(value: Any) -> str:
    m = re.search(r"(\d{6})", str(value or ""))
    return m.group(1) if m else str(value or "").strip()


def _pick(row: pd.Series, names: list[str]) -> Any:
    for name in names:
        if name in row.index:
            return row.get(name)
    return ""


def _records(df: pd.DataFrame, limit: int, source: str = "akshare") -> list[dict[str, str]]:
    if df is None or df.empty:
        return []
    hits: list[dict[str, str]] = []
    for _, row in df.head(limit).iterrows():
        code = _normalize_code(_pick(row, ["代码", "stock_code", "成分券代码", "code"]))
        name = str(_pick(row, ["名称", "股票简称", "stock_name", "成分券名称", "name"]) or "")
        if code and len(code) == 6:
            hits.append({"code": code, "name": name or code, "source": source})
    return hits


def _local_sector_fallback(query: str, limit: int) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    sectors: list[dict[str, str]] = []
    stocks: list[dict[str, str]] = []
    for key, values in LOCAL_SECTOR_FALLBACKS.items():
        if query in key or key in query:
            sectors.append({"name": key, "kind": "local_fallback", "source": "本地兜底"})
            stocks.extend({"code": code, "name": name, "source": "本地兜底"} for code, name in values[:limit])
    return sectors, stocks[:limit]


def _friendly_source_note(result: dict[str, Any], note: str) -> None:
    if note not in result["notes"]:
        result["notes"].append(note)


def search(query: str, limit: int = 30) -> dict[str, Any]:
    query = str(query or "").strip()
    result: dict[str, Any] = {"query": query, "type": "unknown", "stocks": [], "sectors": [], "notes": []}
    if not query:
        return result

    if re.fullmatch(r"\d{6}", query):
        result["type"] = "stock"
        result["stocks"] = [{"code": query, "name": query, "source": "input"}]
        return result

    stock_search_res = run_with_timeout(
        "efinance search_stock",
        lambda: search_stock(query, limit=limit),
        seconds=8,
        source="efinance",
        retries=0,
        empty=lambda value: not value,
    )
    if stock_search_res.ok and stock_search_res.value:
        result["stocks"].extend(stock_search_res.value)
    if result["stocks"]:
        result["type"] = "stock_search"

    apply_data_patches(verbose=False)
    try:
        import akshare as ak

        boards_res = run_direct_then_proxy(
            "stock_board_concept_name_em",
            ak.stock_board_concept_name_em,
            hook_domains=["push2.eastmoney.com"],
            seconds=12,
            empty=dataframe_empty,
            proxy_reason="concept board fallback",
        )
        if boards_res.ok and boards_res.value is not None:
            boards = boards_res.value
            name_col = "板块名称" if "板块名称" in boards.columns else boards.columns[0]
            matched = boards[boards[name_col].astype(str).str.contains(query, na=False, regex=False)]
            for _, board in matched.head(5).iterrows():
                board_name = str(board.get(name_col, ""))
                result["sectors"].append({"name": board_name, "kind": "concept", "source": boards_res.source})
                cons_res = run_direct_then_proxy(
                    f"stock_board_concept_cons_em {board_name}",
                    lambda board_name=board_name: ak.stock_board_concept_cons_em(symbol=board_name),
                    hook_domains=["push2.eastmoney.com"],
                    seconds=12,
                    empty=dataframe_empty,
                    proxy_reason="concept constituents fallback",
                )
                if cons_res.ok and cons_res.value is not None:
                    result["stocks"].extend(_records(cons_res.value, limit, source=cons_res.source))
                else:
                    _friendly_source_note(result, f"概念板块 {board_name} 成分股接口暂不可用，已保留可用结果。")
        else:
            _friendly_source_note(result, "外部概念板块接口暂不可用，已尝试使用本地常用板块兜底。")

        sw_res = run_with_timeout(
            "stock_industry_clf_hist_sw",
            ak.stock_industry_clf_hist_sw,
            seconds=12,
            source="akshare-direct/swsresearch",
            retries=0,
            empty=dataframe_empty,
        )
        if sw_res.ok and sw_res.value is not None:
            sw = sw_res.value
            text_cols = [c for c in ["industry_name", "industry_l1", "industry_l2", "industry_l3", "stock_name"] if c in sw.columns]
            if text_cols:
                mask = pd.Series(False, index=sw.index)
                for col in text_cols:
                    mask = mask | sw[col].astype(str).str.contains(query, na=False, regex=False)
                matched_sw = sw[mask].drop_duplicates(subset=["stock_code"]).head(limit)
                if not matched_sw.empty:
                    result["sectors"].append({"name": query, "kind": "sw_industry", "source": "akshare-direct"})
                    result["stocks"].extend(_records(matched_sw, limit, source="akshare-direct"))
        else:
            _friendly_source_note(result, "申万行业接口暂不可用，已跳过该来源。")
    except Exception:
        _friendly_source_note(result, "AKShare 暂不可用，已优先展示备用来源。")

    local_sectors, local_stocks = _local_sector_fallback(query, limit)
    if local_sectors and not result["sectors"]:
        result["sectors"].extend(local_sectors)
    if local_stocks:
        existing_codes = {stock.get("code") for stock in result["stocks"]}
        result["stocks"].extend(stock for stock in local_stocks if stock.get("code") not in existing_codes)
    if local_stocks and any(stock.get("source") == "本地兜底" for stock in result["stocks"]):
        _friendly_source_note(result, "外部板块接口暂不可用，已使用本地常用板块候选池。")

    dedup: dict[str, dict[str, str]] = {}
    for stock in result["stocks"]:
        code = stock.get("code", "")
        if code and code not in dedup:
            dedup[code] = stock
    result["stocks"] = list(dedup.values())[:limit]
    if result["sectors"]:
        result["type"] = "sector"
    elif result["stocks"] and result["type"] == "unknown":
        result["type"] = "stock_search"
    return result
