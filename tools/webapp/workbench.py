from __future__ import annotations

import math
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.providers.easy_tdx_provider import fetch_realtime_quote
from tools.webapp import chain_db, dashboard, reports

DATA_DIR = Path(__file__).resolve().parent / "data"
STATE_DB = DATA_DIR / "workbench.db"
CHAIN_DB = chain_db.DEFAULT_DB
POOL_STATES = {"watch", "core", "ignore"}
QUOTE_TTL_SECONDS = 60
_QUOTE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_QUOTE_LOCK = threading.Lock()


def ensure_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(STATE_DB)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pool_entries (
                code TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK(state IN ('watch', 'core', 'ignore')),
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def _state_rows() -> dict[str, dict[str, Any]]:
    ensure_db()
    with closing(sqlite3.connect(STATE_DB)) as connection:
        connection.row_factory = sqlite3.Row
        return {row["code"]: dict(row) for row in connection.execute("SELECT * FROM pool_entries")}


def _chain_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(CHAIN_DB)
    connection.row_factory = sqlite3.Row
    return connection


def company_exists(code: str) -> bool:
    if not CHAIN_DB.exists():
        return False
    with closing(_chain_connection()) as connection:
        return connection.execute("SELECT 1 FROM companies WHERE code6 = ? LIMIT 1", (code,)).fetchone() is not None


def put_pool_entry(code: str, state: str, note: str = "") -> dict[str, Any]:
    code, state, note = str(code).strip(), str(state).strip(), str(note or "").strip()
    if len(code) != 6 or not code.isdigit() or not company_exists(code):
        raise ValueError("stock code not found")
    if state not in POOL_STATES:
        raise ValueError("state must be watch, core, or ignore")
    if len(note) > 500:
        raise ValueError("note must be 500 characters or fewer")
    ensure_db()
    now = datetime.now().isoformat(timespec="seconds")
    with closing(sqlite3.connect(STATE_DB)) as connection:
        connection.execute(
            """
            INSERT INTO pool_entries(code, state, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET state=excluded.state, note=excluded.note, updated_at=excluded.updated_at
            """,
            (code, state, note, now, now),
        )
        connection.commit()
    return {"code": code, "state": state, "note": note, "updated_at": now}


def delete_pool_entry(code: str) -> None:
    ensure_db()
    with closing(sqlite3.connect(STATE_DB)) as connection:
        connection.execute("DELETE FROM pool_entries WHERE code = ?", (str(code).strip(),))
        connection.commit()


def _company_rows(codes: list[str]) -> dict[str, dict[str, Any]]:
    if not codes or not CHAIN_DB.exists():
        return {}
    placeholders = ",".join("?" for _ in codes)
    sql = f"""
        SELECT c.code6 AS code, c.name, c.full_name, c.market_value,
               COALESCE(c.sw_industry_lv3, c.sw_industry_lv2, c.sw_industry_lv1, c.akshare_industry, c.ckg_industry_name, '') AS industry
        FROM companies c WHERE c.code6 IN ({placeholders})
    """
    with closing(_chain_connection()) as connection:
        return {row["code"]: dict(row) for row in connection.execute(sql, codes)}


def search_companies(query: str, limit: int = 20) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query or not CHAIN_DB.exists():
        return {"query": query, "type": "unknown", "stocks": [], "sectors": [], "notes": []}
    like = f"%{query}%"
    limit = max(1, min(int(limit or 20), 50))
    sql = """
        SELECT c.code6 AS code, c.name, c.full_name, c.market_value,
               COALESCE(c.sw_industry_lv3, c.sw_industry_lv2, c.sw_industry_lv1, c.akshare_industry, c.ckg_industry_name, '') AS industry
        FROM companies c
        WHERE c.code6 = ? OR c.name LIKE ? OR c.full_name LIKE ?
           OR c.sw_industry_lv1 LIKE ? OR c.sw_industry_lv2 LIKE ? OR c.sw_industry_lv3 LIKE ?
           OR c.akshare_industry LIKE ? OR c.ckg_industry_name LIKE ?
           OR EXISTS (SELECT 1 FROM company_industry ci WHERE ci.company_id = c.id AND ci.industry_name LIKE ?)
           OR EXISTS (SELECT 1 FROM company_product cp WHERE cp.company_id = c.id AND cp.product_name LIKE ?)
        ORDER BY CASE WHEN c.code6 = ? THEN 0 WHEN c.name = ? THEN 1 WHEN c.name LIKE ? THEN 2 ELSE 3 END,
                 c.market_value DESC
        LIMIT ?
    """
    params = (query, *([like] * 9), query, query, f"{query}%", limit)
    with closing(_chain_connection()) as connection:
        stocks = [{**dict(row), "source": "CKG"} for row in connection.execute(sql, params)]
    return {
        "query": query,
        "type": "stock_search" if stocks else "unknown",
        "stocks": stocks,
        "sectors": [],
        "notes": [],
    }


def _all_company_industries() -> list[str]:
    if not CHAIN_DB.exists():
        return []
    sql = """
        SELECT DISTINCT industry FROM (
            SELECT COALESCE(sw_industry_lv3, sw_industry_lv2, sw_industry_lv1, akshare_industry, ckg_industry_name, '') AS industry
            FROM companies
        ) WHERE industry <> '' ORDER BY industry
    """
    with closing(_chain_connection()) as connection:
        return [row["industry"] for row in connection.execute(sql)]


def get_pool(query: str = "", industry: str = "", state: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
    states = _state_rows()
    codes = set(reports.analyzed_codes()) | {code for code, item in states.items() if item["state"] in {"watch", "core"}}
    codes -= {code for code, item in states.items() if item["state"] == "ignore"}
    companies = _company_rows(sorted(codes))
    items = []
    for code in sorted(codes):
        company = companies.get(code, {"code": code, "name": code, "full_name": "", "industry": "", "market_value": None})
        explicit = states.get(code, {})
        summary = reports.extract_score_summary(code)
        item = {
            **company,
            "state": explicit.get("state") or "watch",
            "note": explicit.get("note", ""),
            "origin": "manual" if explicit else "report",
            "analysis_status": "ready" if summary["status"] == "ready" else "never",
            "summary": summary,
        }
        haystack = f"{code} {item['name']} {item['full_name']} {item['industry']}".lower()
        if query and query.lower() not in haystack:
            continue
        if industry and item["industry"] != industry:
            continue
        if state and item["state"] != state:
            continue
        items.append(item)
    items.sort(key=lambda item: (item["state"] != "core", -(item["summary"].get("score") or -1), item["code"]))
    total = len(items)
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    page = items[offset : offset + limit]
    return {
        "total": total,
        "items": page,
        "industries": _all_company_industries(),
        "counts": {
            "core": sum(item["state"] == "core" for item in items),
            "watch": sum(item["state"] == "watch" for item in items),
            "analyzed": sum(item["analysis_status"] == "ready" for item in items),
        },
    }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _fetch_quote(code: str) -> dict[str, Any]:
    try:
        raw = fetch_realtime_quote(code)
        if not raw:
            return {"code": code, "status": "unavailable", "error": "empty quote"}
        return {
            "code": code,
            "status": "live",
            "source": raw.get("source", "easy_tdx"),
            "price": _finite(raw.get("最新价")),
            "change_pct": _finite(raw.get("涨跌幅")),
            "change": _finite(raw.get("涨跌额")),
            "turnover": _finite(raw.get("换手率")),
            "pe_ttm": _finite(raw.get("市盈率-TTM")),
            "pb": _finite(raw.get("市净率")),
            "market_value": _finite(raw.get("总市值")),
            "amount": _finite(raw.get("成交额")),
            "as_of": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as exc:
        return {"code": code, "status": "unavailable", "source": "easy_tdx", "error": f"{type(exc).__name__}: {exc}"}


def get_quotes(codes: list[str], refresh: bool = False) -> dict[str, Any]:
    cleaned = list(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
    if len(cleaned) > 50 or any(len(code) != 6 or not code.isdigit() for code in cleaned):
        raise ValueError("codes must contain at most 50 six-digit A-share codes")
    now, results, missing = time.time(), {}, []
    with _QUOTE_LOCK:
        for code in cleaned:
            cached = _QUOTE_CACHE.get(code)
            if cached and not refresh and now - cached[0] < QUOTE_TTL_SECONDS:
                results[code] = {**cached[1], "status": "cache" if cached[1].get("status") == "live" else cached[1].get("status")}
            else:
                missing.append(code)
    if missing:
        with ThreadPoolExecutor(max_workers=min(6, len(missing))) as executor:
            futures = {executor.submit(_fetch_quote, code): code for code in missing}
            for future in as_completed(futures):
                payload = future.result()
                code = futures[future]
                results[code] = payload
                with _QUOTE_LOCK:
                    _QUOTE_CACHE[code] = (time.time(), payload)
    ordered = [results[code] for code in cleaned]
    return {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "source": "easy_tdx",
        "requested": len(cleaned),
        "received": sum(item["status"] in {"live", "cache"} for item in ordered),
        "quotes": ordered,
    }


def _industry_candidates(name: str, limit: int = 3) -> list[dict[str, Any]]:
    if not CHAIN_DB.exists():
        return []
    sql = """
        SELECT DISTINCT c.code6 AS code, c.name, c.market_value, ci.source AS relation_source
        FROM company_industry ci
        JOIN companies c ON c.id = ci.company_id
        WHERE ci.industry_name = ?
        ORDER BY c.market_value IS NULL, c.market_value DESC, c.code6
        LIMIT ?
    """
    fallback = """
        SELECT c.code6 AS code, c.name, c.market_value, c.source AS relation_source
        FROM companies c
        WHERE ? IN (c.sw_industry_lv1, c.sw_industry_lv2, c.sw_industry_lv3, c.akshare_industry, c.ckg_industry_name)
        ORDER BY c.market_value IS NULL, c.market_value DESC, c.code6
        LIMIT ?
    """
    with closing(_chain_connection()) as connection:
        rows = [dict(row) for row in connection.execute(sql, (name, limit))]
        return rows or [dict(row) for row in connection.execute(fallback, (name, limit))]


def build_discovery(market_payload: dict[str, Any], limit: int = 30) -> dict[str, Any]:
    sector = market_payload.get("panels", {}).get("sector_amount_ratio", {})
    warming = [row for row in sector.get("warming", []) if (_finite(row.get("warming_change")) or 0) > 0]
    states = _state_rows()
    candidates = []
    for industry_rank, industry in enumerate(warming, start=1):
        for company in _industry_candidates(str(industry.get("name", "")), 3):
            code = company["code"]
            summary = reports.extract_score_summary(code)
            explicit = states.get(code, {})
            candidates.append(
                {
                    **company,
                    "industry": industry.get("name", ""),
                    "industry_rank": industry_rank,
                    "warming_change": industry.get("warming_change"),
                    "recent_average": industry.get("recent_average"),
                    "previous_average": industry.get("previous_average"),
                    "pool_state": explicit.get("state", ""),
                    "analysis_status": summary["status"],
                    "summary": summary,
                    "evidence": [
                        {"label": "行业升温", "source": sector.get("source", ""), "status": sector.get("status", "")},
                        {"label": "精确行业关系", "source": company.get("relation_source", ""), "status": "ready"},
                    ],
                }
            )
    candidates.sort(key=lambda item: (item["industry_rank"], -(item.get("market_value") or 0), item["code"]))
    candidates = [item for item in candidates if item.get("pool_state") != "ignore"][: max(1, min(int(limit), 50))]
    quotes = get_quotes([item["code"] for item in candidates]) if candidates else {"quotes": []}
    quote_map = {item["code"]: item for item in quotes["quotes"]}
    for candidate in candidates:
        candidate["quote"] = quote_map.get(candidate["code"], {"status": "unavailable"})
    return {
        "status": "live" if candidates and sector.get("status") in {"live", "cache"} else "partial" if candidates else "unavailable",
        "as_of": sector.get("as_of", ""),
        "source": sector.get("source", ""),
        "methodology": "近5日行业成交额占比均值相对前5日升温；行业关系必须精确匹配。",
        "candidates": candidates,
    }


def get_discovery(refresh: bool = False, limit: int = 30) -> dict[str, Any]:
    return build_discovery(dashboard.get_market_dashboard(days=20, refresh=refresh), limit)


def _percentile(values: list[float], current: float) -> float | None:
    clean = sorted(value for value in values if math.isfinite(value))
    if len(clean) < 2:
        return None
    return round(sum(value <= current for value in clean) / len(clean) * 100, 1)


def _factor(identifier: str, name: str, weight: int, raw: float | None, score: float | None, logic: str, source: str, as_of: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "name": name,
        "weight": weight,
        "raw_value": None if raw is None else round(raw, 4),
        "score": score,
        "logic": logic,
        "source": source,
        "as_of": as_of,
        "status": "ready" if score is not None else "missing",
    }


def build_market_pressure(market_payload: dict[str, Any]) -> dict[str, Any]:
    panels = market_payload.get("panels", {})
    sector, margin = panels.get("sector_amount_ratio", {}), panels.get("margin_index", {})
    margin_rows = [row for row in margin.get("rows", []) if _finite(row.get("ratio")) is not None and _finite(row.get("sh_index")) is not None]
    ratio_values = [_finite(row["ratio"]) for row in margin_rows]
    margin_raw = ratio_values[-1] if ratio_values else None
    margin_score = _percentile([value for value in ratio_values if value is not None], margin_raw) if margin_raw is not None else None

    index_values = [_finite(row["sh_index"]) for row in margin_rows]
    returns = [((index_values[index] / index_values[index - 5]) - 1) * 100 for index in range(5, len(index_values)) if index_values[index - 5]]
    index_raw = returns[-1] if returns else None
    index_score = _percentile([-value for value in returns], -index_raw) if index_raw is not None else None

    sector_rows, dates = sector.get("rows", []), sector.get("dates", [])
    concentration_history, breadth_history = [], []
    for date_index in range(len(dates)):
        ratios = [
            _finite(row.get("values", [])[date_index].get("ratio"))
            for row in sector_rows
            if len(row.get("values", [])) > date_index and row["values"][date_index]
        ]
        ratios = sorted((value for value in ratios if value is not None), reverse=True)
        if ratios:
            concentration_history.append(sum(ratios[:10]))
        if date_index >= 9:
            changes = []
            for row in sector_rows:
                cells = row.get("values", [])[date_index - 9 : date_index + 1]
                if len(cells) == 10 and all(cell and _finite(cell.get("ratio")) is not None for cell in cells):
                    changes.append(sum(float(cell["ratio"]) for cell in cells[5:]) / 5 - sum(float(cell["ratio"]) for cell in cells[:5]) / 5)
            if changes:
                breadth_history.append(sum(value > 0 for value in changes) / len(changes) * 100)
    concentration_raw = concentration_history[-1] if concentration_history else None
    concentration_score = _percentile(concentration_history, concentration_raw) if concentration_raw is not None else None
    breadth_raw = breadth_history[-1] if breadth_history else None
    breadth_score = _percentile([-value for value in breadth_history], -breadth_raw) if breadth_raw is not None else None

    factors = [
        _factor("margin", "融资热度", 30, margin_raw, margin_score, "融资买入额/沪深A股成交额的历史百分位", margin.get("source", ""), margin.get("as_of", "")),
        _factor("index", "指数弱势", 30, index_raw, index_score, "上证指数5日跌幅在近60日样本中的弱势百分位", margin.get("source", ""), margin.get("as_of", "")),
        _factor("concentration", "行业集中度", 25, concentration_raw, concentration_score, "成交额占比前10行业合计值的历史百分位", sector.get("source", ""), sector.get("as_of", "")),
        _factor("breadth", "广度弱势", 15, breadth_raw, breadth_score, "近5日占比升温行业比例的反向历史百分位", sector.get("source", ""), sector.get("as_of", "")),
    ]
    available_weight = sum(factor["weight"] for factor in factors if factor["score"] is not None)
    total = None
    if available_weight >= 70:
        total = round(sum(factor["score"] * factor["weight"] for factor in factors if factor["score"] is not None) / available_weight, 1)
    status = "live" if available_weight == 100 else "partial" if available_weight >= 70 else "unavailable"
    trend = [
        {"date": row.get("date", ""), "margin_ratio": _finite(row.get("ratio")), "sh_index": _finite(row.get("sh_index"))}
        for row in margin_rows[-20:]
    ]
    return {
        "status": status,
        "score": total,
        "available_weight": available_weight,
        "as_of": market_payload.get("as_of", ""),
        "factors": factors,
        "trend": trend,
        "sources": market_payload.get("sources", []),
        "note": "压力分数越高表示融资、指数、集中度和广度约束越强，不构成投资建议。",
    }


def get_market_pressure(days: int = 60, refresh: bool = False) -> dict[str, Any]:
    return build_market_pressure(dashboard.get_market_dashboard(days=days, refresh=refresh))
