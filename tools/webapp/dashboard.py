from __future__ import annotations

import copy
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from tools.data_call import run_with_timeout
from tools.providers import a_stock_data_provider as eastmoney

CACHE_SECONDS = 15 * 60
HISTORY_DAYS = 60
DASHBOARD_DB = Path(__file__).resolve().parent / "data" / "market_dashboard.db"

_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
_DB_LOCK = threading.Lock()
_DB_READY_PATH: Path | None = None
_EASTMONEY_LOCK = threading.Lock()

# ponytail: one worker per source is enough for the 60-day cache; use a job queue only if concurrent users need independent syncs.
_SYNC_LOCK = threading.RLock()
_INDUSTRY_SYNC: dict[str, Any] = {"active": False, "completed": 0, "total": 0, "failed": 0, "error": "", "finished_at": 0.0}
_TURNOVER_SYNC: dict[str, Any] = {"active": False, "completed": 0, "total": 0, "failed": 0, "error": "", "finished_at": 0.0}


def _pick(df: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    lowered = {str(col).replace(" ", "").lower(): col for col in df.columns}
    for name in names:
        match = lowered.get(name.replace(" ", "").lower())
        if match is not None:
            return match
    return None


def _number(value: Any) -> float | None:
    try:
        value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        return None if pd.isna(value) else float(value)
    except Exception:
        return None


def _panel(status: str, source: str = "", rows: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"status": status, "source": source, "as_of": extra.pop("as_of", ""), "rows": rows or [], **extra}


def _open_db() -> sqlite3.Connection:
    global _DB_READY_PATH
    with _DB_LOCK:
        if _DB_READY_PATH != DASHBOARD_DB:
            DASHBOARD_DB.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(DASHBOARD_DB, timeout=5)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS industry_turnover (
                        trade_date TEXT NOT NULL,
                        board_code TEXT NOT NULL,
                        name TEXT NOT NULL,
                        amount REAL NOT NULL,
                        fetched_at REAL NOT NULL,
                        PRIMARY KEY (trade_date, board_code)
                    )"""
                )
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS market_turnover (
                        trade_date TEXT PRIMARY KEY,
                        sh_main REAL,
                        sh_star REAL,
                        sz_main REAL,
                        sz_chinext REAL,
                        total REAL,
                        fetched_at REAL NOT NULL
                    )"""
                )
                conn.commit()
            finally:
                conn.close()
            _DB_READY_PATH = DASHBOARD_DB
    conn = sqlite3.connect(DASHBOARD_DB, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _save_industry_rows(rows: list[tuple[str, str, str, float]]) -> None:
    if not rows:
        return
    conn = _open_db()
    try:
        conn.executemany(
            """INSERT INTO industry_turnover (trade_date, board_code, name, amount, fetched_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(trade_date, board_code) DO UPDATE SET
                 name = excluded.name, amount = excluded.amount, fetched_at = excluded.fetched_at""",
            [(day, code, name, amount, time.time()) for day, code, name, amount in rows],
        )
        conn.commit()
    finally:
        conn.close()


def _save_market_turnover(row: dict[str, Any]) -> None:
    conn = _open_db()
    try:
        conn.execute(
            """INSERT INTO market_turnover
               (trade_date, sh_main, sh_star, sz_main, sz_chinext, total, fetched_at)
               VALUES (:trade_date, :sh_main, :sh_star, :sz_main, :sz_chinext, :total, :fetched_at)
               ON CONFLICT(trade_date) DO UPDATE SET
                 sh_main = excluded.sh_main, sh_star = excluded.sh_star,
                 sz_main = excluded.sz_main, sz_chinext = excluded.sz_chinext,
                 total = excluded.total, fetched_at = excluded.fetched_at""",
            {**row, "fetched_at": time.time()},
        )
        conn.commit()
    finally:
        conn.close()


def _load_industry_rows(days: int) -> list[sqlite3.Row]:
    conn = _open_db()
    try:
        dates = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT trade_date FROM industry_turnover ORDER BY trade_date DESC LIMIT ?", (days,)
            )
        ]
        if not dates:
            return []
        marks = ",".join("?" for _ in dates)
        return conn.execute(
            f"SELECT trade_date, board_code, name, amount, fetched_at FROM industry_turnover WHERE trade_date IN ({marks})",
            dates,
        ).fetchall()
    finally:
        conn.close()


def _load_market_turnover(dates: list[str]) -> dict[str, dict[str, Any]]:
    if not dates:
        return {}
    conn = _open_db()
    try:
        marks = ",".join("?" for _ in dates)
        rows = conn.execute(f"SELECT * FROM market_turnover WHERE trade_date IN ({marks})", dates).fetchall()
        return {str(row["trade_date"]): dict(row) for row in rows}
    finally:
        conn.close()


def _snapshot_date(value: Any) -> str:
    try:
        stamp = float(value)
        if stamp > 1_000_000_000_000:
            stamp /= 1000
        return datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone().date().isoformat()
    except Exception:
        return ""


def _fetch_industry_snapshot() -> tuple[list[dict[str, str]], list[tuple[str, str, str, float]], str]:
    params = {
        "pn": "1",
        "pz": "100",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:90 t:2 f:!50",
        "fields": "f12,f14,f3,f6,f124",
    }
    with _EASTMONEY_LOCK:
        payload = eastmoney._get_json(f"{eastmoney.PUSH2_URL}/clist/get", params=params, timeout=7)
    boards: list[dict[str, str]] = []
    rows: list[tuple[str, str, str, float]] = []
    for item in eastmoney._records(payload):
        code, name = str(item.get("f12", "")).strip(), str(item.get("f14", "")).strip()
        amount, trade_date = _number(item.get("f6")), _snapshot_date(item.get("f124"))
        if not code or not name:
            continue
        boards.append({"code": code, "name": name})
        if amount is not None and amount > 0 and trade_date:
            rows.append((trade_date, code, name, amount))
    if not boards:
        raise ValueError("东方财富行业列表为空")
    return boards, rows, max((row[0] for row in rows), default="")


def _fetch_industry_history(board: dict[str, str]) -> list[tuple[str, str, str, float]]:
    begin = (date.today() - timedelta(days=150)).strftime("%Y%m%d")
    params = {
        "secid": f"90.{board['code']}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": begin,
        "end": date.today().strftime("%Y%m%d"),
        "smplmt": "10000",
        "lmt": "1000000",
    }
    with _EASTMONEY_LOCK:
        payload = eastmoney._get_json(f"{eastmoney.PUSH2HIS_URL}/stock/kline/get", params=params, timeout=7)
    klines = payload.get("data", {}).get("klines", []) if isinstance(payload, dict) else []
    rows: list[tuple[str, str, str, float]] = []
    for item in klines:
        parts = str(item).split(",")
        if len(parts) < 7:
            continue
        amount = _number(parts[6])
        if amount is not None and amount > 0 and len(parts[0]) == 10:
            rows.append((parts[0], board["code"], board["name"], amount))
    return rows


def _sync_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    with _SYNC_LOCK:
        return {
            "active": bool(state["active"]),
            "completed": int(state["completed"]),
            "total": int(state["total"]),
            "failed": int(state["failed"]),
            "error": str(state["error"]),
        }


def _clear_payload_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _start_industry_sync(boards: list[dict[str, str]], force: bool = False) -> dict[str, Any]:
    with _SYNC_LOCK:
        if _INDUSTRY_SYNC["active"]:
            return _sync_snapshot(_INDUSTRY_SYNC)
        if not force and _INDUSTRY_SYNC["finished_at"] and time.time() - _INDUSTRY_SYNC["finished_at"] < CACHE_SECONDS:
            return _sync_snapshot(_INDUSTRY_SYNC)
        _INDUSTRY_SYNC.update({"active": True, "completed": 0, "total": len(boards), "failed": 0, "error": ""})

    def worker() -> None:
        errors: list[str] = []
        try:
            for board in boards:
                try:
                    _save_industry_rows(_fetch_industry_history(board))
                    _clear_payload_cache()
                except Exception as exc:
                    errors.append(f"{board['name']}: {type(exc).__name__}")
                    with _SYNC_LOCK:
                        _INDUSTRY_SYNC["failed"] += 1
                finally:
                    with _SYNC_LOCK:
                        _INDUSTRY_SYNC["completed"] += 1
        finally:
            with _SYNC_LOCK:
                _INDUSTRY_SYNC.update({"active": False, "error": "; ".join(errors[:3]), "finished_at": time.time()})
            _clear_payload_cache()

    threading.Thread(target=worker, name="dashboard-industry-sync", daemon=True).start()
    return _sync_snapshot(_INDUSTRY_SYNC)


def _industry_matrix(records: list[sqlite3.Row], days: int) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    dates = sorted({str(row["trade_date"]) for row in records})[-days:]
    if not dates:
        return [], [], []
    by_date: dict[str, list[sqlite3.Row]] = {day: [] for day in dates}
    for row in records:
        if row["trade_date"] in by_date:
            by_date[str(row["trade_date"])].append(row)
    totals = {day: sum(float(row["amount"]) for row in rows) for day, rows in by_date.items()}
    values: dict[str, dict[str, dict[str, Any]]] = {}
    names: dict[str, str] = {}
    for day, day_rows in by_date.items():
        for rank, row in enumerate(sorted(day_rows, key=lambda item: float(item["amount"]), reverse=True), start=1):
            code, amount = str(row["board_code"]), float(row["amount"])
            names[code] = str(row["name"])
            values.setdefault(code, {})[day] = {
                "amount": amount,
                "ratio": round(amount / totals[day] * 100, 4) if totals[day] else None,
                "rank": rank,
            }
    rows: list[dict[str, Any]] = []
    warming: list[dict[str, Any]] = []
    for code, per_day in values.items():
        cells = [per_day.get(day) for day in dates]
        latest = next((value for value in reversed(cells) if value is not None), None)
        previous = next((cells[index] for index in range(len(cells) - 2, -1, -1) if cells[index] is not None), None)
        ratio_change = None
        if latest and previous and latest["ratio"] is not None and previous["ratio"] is not None:
            ratio_change = round(latest["ratio"] - previous["ratio"], 4)
        row = {
            "code": code,
            "name": names.get(code, code),
            "values": cells,
            "current_ratio": latest["ratio"] if latest else None,
            "ratio_change": ratio_change,
            "rank": latest["rank"] if latest else None,
        }
        rows.append(row)
        last_ten = cells[-10:]
        if len(last_ten) == 10 and all(cell and cell["ratio"] is not None for cell in last_ten):
            previous_average = sum(float(cell["ratio"]) for cell in last_ten[:5]) / 5
            recent_average = sum(float(cell["ratio"]) for cell in last_ten[5:]) / 5
            warming.append(
                {
                    "code": code,
                    "name": names.get(code, code),
                    "recent_average": round(recent_average, 4),
                    "previous_average": round(previous_average, 4),
                    "warming_change": round(recent_average - previous_average, 4),
                }
            )
    rows.sort(key=lambda row: (row["current_ratio"] is None, -(row["current_ratio"] or 0)))
    warming.sort(key=lambda row: row["warming_change"], reverse=True)
    return dates, rows, warming


def _sector_snapshot_fallback() -> dict[str, Any] | None:
    from tools.providers.easy_tdx_provider import fetch_board_ranking

    result = run_with_timeout(
        "dashboard sector snapshot",
        fetch_board_ranking,
        seconds=8,
        source="easy_tdx",
        retries=0,
        empty=lambda value: value is None or value.empty,
    )
    if not result.ok or result.value is None:
        return None
    frame = result.value.copy()
    name_col, amount_col = _pick(frame, ["name", "行业", "板块名称"]), _pick(frame, ["amount", "成交额", "成交额(元)"])
    change_col = _pick(frame, ["change_pct", "行业-涨跌幅", "涨跌幅"])
    if not name_col or not amount_col:
        return None
    frame["_amount"] = pd.to_numeric(frame[amount_col], errors="coerce")
    frame = frame.dropna(subset=["_amount"])
    total = float(frame["_amount"].sum())
    if frame.empty or total <= 0:
        return None
    rows = []
    for rank, (_, item) in enumerate(frame.sort_values("_amount", ascending=False).iterrows(), start=1):
        amount = float(item["_amount"])
        rows.append(
            {
                "code": str(item.get("code", "")),
                "name": str(item[name_col]),
                "values": [],
                "amount": amount,
                "current_ratio": round(amount / total * 100, 4),
                "ratio_change": None,
                "rank": rank,
                "change_pct": _number(item.get(change_col)) if change_col else None,
            }
        )
    return _panel(
        "partial",
        "easy_tdx 行业排行（当前快照降级）",
        rows,
        dates=[],
        warming=[],
        note="东方财富行业日线暂不可用，以下为 easy_tdx 当前快照；占比仅按该接口返回行业集合计算，不代表历史热力矩阵。",
    )


def _sector_panel(days: int = 20, refresh: bool = False) -> dict[str, Any]:
    records = _load_industry_rows(days)
    boards: list[dict[str, str]] = []
    snapshot_error = ""
    try:
        boards, snapshot_rows, _ = _fetch_industry_snapshot()
        _save_industry_rows(snapshot_rows)
        if snapshot_rows:
            records = _load_industry_rows(days)
    except Exception as exc:
        snapshot_error = f"{type(exc).__name__}: {exc}"
    sync = _start_industry_sync(boards, force=refresh) if boards else _sync_snapshot(_INDUSTRY_SYNC)
    dates, rows, warming = _industry_matrix(records, days)
    if not rows:
        fallback = _sector_snapshot_fallback()
        if fallback:
            fallback["sync"] = sync
            fallback["error"] = snapshot_error or sync["error"]
            return fallback
        return _panel(
            "syncing" if sync["active"] else "unavailable",
            "东方财富公开行业板块日线",
            error=snapshot_error or sync["error"] or "行业历史数据尚未写入本地缓存",
            dates=[],
            warming=[],
            sync=sync,
            note="仅展示已取得的真实交易日；缺失日期不会补值。",
        )
    complete = len(dates) >= days and not sync["failed"]
    status = "syncing" if sync["active"] else "live" if not snapshot_error and complete else "partial"
    return _panel(
        status,
        "东方财富公开行业板块日线（PUSH2HIS）",
        rows,
        as_of=dates[-1],
        dates=dates,
        warming=warming[:10],
        sync=sync,
        error=snapshot_error or sync["error"],
        note="占比 = 行业板块成交额 / 当日已取得全部行业板块成交额；颜色按当日横截面排名，红高绿低。",
    )


def _fetch_index(ak: Any, symbol: str) -> pd.DataFrame:
    for name, kwargs in (("stock_zh_index_daily_em", {"symbol": symbol}), ("stock_zh_index_daily", {"symbol": symbol})):
        fn = getattr(ak, name, None)
        if not fn:
            continue
        try:
            value = fn(**kwargs)
            if value is not None and not value.empty:
                return value
        except Exception:
            continue
    return pd.DataFrame()


def _row_value(frame: pd.DataFrame, row_labels: list[str], col_labels: list[str]) -> float | None:
    row_col = _pick(frame, ["项目", "指标", "单日情况", "证券类别", "类别"])
    value_col = _pick(frame, col_labels)
    if not row_col or not value_col:
        return None
    labels = frame[row_col].astype(str).str.replace(" ", "", regex=False)
    match = pd.Series(False, index=frame.index)
    for label in row_labels:
        match |= labels.str.contains(label.replace(" ", ""), regex=False)
    if not match.any():
        return None
    return _number(frame.loc[match, value_col].iloc[0])


def _fetch_market_turnover(ak: Any, trade_date: str) -> dict[str, Any]:
    compact_date = trade_date.replace("-", "")
    sh = ak.stock_sse_deal_daily(date=compact_date)
    sz = ak.stock_szse_summary(date=compact_date)
    sh_main = _row_value(sh, ["成交金额"], ["主板A股", "主板A", "主板"])
    sh_star = _row_value(sh, ["成交金额"], ["科创板"])
    sz_main = _row_value(sz, ["主板A股"], ["成交金额", "成交额"])
    sz_chinext = _row_value(sz, ["创业板A股"], ["成交金额", "成交额"])
    values = [sh_main, sh_star, sz_main, sz_chinext]
    total = (sh_main + sh_star) * 1e8 + sz_main + sz_chinext if all(value is not None for value in values) else None
    return {
        "trade_date": trade_date,
        "sh_main": None if sh_main is None else sh_main * 1e8,
        "sh_star": None if sh_star is None else sh_star * 1e8,
        "sz_main": sz_main,
        "sz_chinext": sz_chinext,
        "total": total,
    }


def _start_turnover_sync(dates: list[str], force: bool = False) -> dict[str, Any]:
    missing = [day for day in sorted(set(dates), reverse=True) if _load_market_turnover([day]).get(day, {}).get("total") is None]
    if not missing:
        return _sync_snapshot(_TURNOVER_SYNC)
    with _SYNC_LOCK:
        if _TURNOVER_SYNC["active"]:
            return _sync_snapshot(_TURNOVER_SYNC)
        if not force and _TURNOVER_SYNC["finished_at"] and time.time() - _TURNOVER_SYNC["finished_at"] < CACHE_SECONDS:
            return _sync_snapshot(_TURNOVER_SYNC)
        _TURNOVER_SYNC.update({"active": True, "completed": 0, "total": len(missing), "failed": 0, "error": ""})

    def worker() -> None:
        errors: list[str] = []
        try:
            import akshare as ak

            for trade_date in missing:
                try:
                    _save_market_turnover(_fetch_market_turnover(ak, trade_date))
                    _clear_payload_cache()
                except Exception as exc:
                    errors.append(f"{trade_date}: {type(exc).__name__}")
                    with _SYNC_LOCK:
                        _TURNOVER_SYNC["failed"] += 1
                finally:
                    with _SYNC_LOCK:
                        _TURNOVER_SYNC["completed"] += 1
        finally:
            with _SYNC_LOCK:
                _TURNOVER_SYNC.update({"active": False, "error": "; ".join(errors[:3]), "finished_at": time.time()})
            _clear_payload_cache()

    threading.Thread(target=worker, name="dashboard-turnover-sync", daemon=True).start()
    return _sync_snapshot(_TURNOVER_SYNC)


def _margin_rows() -> tuple[list[dict[str, Any]], str, str, list[str]]:
    import akshare as ak

    sh = ak.macro_china_market_margin_sh()
    sz = ak.macro_china_market_margin_sz()
    sh_date = _pick(sh, ["日期", "信用交易日期", "date"])
    sz_date = _pick(sz, ["日期", "信用交易日期", "date"])
    sh_buy = _pick(sh, ["融资买入额", "RZMRE", "financing_buy"])
    sz_buy = _pick(sz, ["融资买入额", "RZMRE", "financing_buy"])
    if not all([sh_date, sz_date, sh_buy, sz_buy]):
        raise ValueError("融资融券字段缺失")
    left = pd.DataFrame({"date": pd.to_datetime(sh[sh_date], errors="coerce"), "margin_buy_sh": pd.to_numeric(sh[sh_buy], errors="coerce")})
    right = pd.DataFrame({"date": pd.to_datetime(sz[sz_date], errors="coerce"), "margin_buy_sz": pd.to_numeric(sz[sz_buy], errors="coerce")})
    margin = pd.merge(left.dropna(), right.dropna(), on="date", how="inner")
    margin["margin_buy"] = margin["margin_buy_sh"] + margin["margin_buy_sz"]

    index = _fetch_index(ak, "sh000001")
    index_date, close_col = _pick(index, ["日期", "date"]), _pick(index, ["收盘", "close"])
    if not index_date or not close_col:
        raise ValueError("上证指数字段缺失")
    index_frame = pd.DataFrame(
        {"date": pd.to_datetime(index[index_date], errors="coerce"), "sh_index": pd.to_numeric(index[close_col], errors="coerce")}
    )
    merged = pd.merge(margin[["date", "margin_buy"]], index_frame, on="date", how="inner").dropna(subset=["date", "margin_buy", "sh_index"])
    date_keys = [day.strftime("%Y-%m-%d") for day in merged["date"].sort_values().tail(HISTORY_DAYS)]
    turnover = _load_market_turnover(date_keys)
    rows = []
    for _, row in merged.sort_values("date").iterrows():
        trade_date = row["date"].strftime("%Y-%m-%d")
        market_turnover = _number(turnover.get(trade_date, {}).get("total"))
        rows.append(
            {
                "date": trade_date,
                "margin_buy": _number(row["margin_buy"]),
                "market_turnover": market_turnover,
                "ratio": None if not market_turnover else round(float(row["margin_buy"]) / market_turnover * 100, 4),
                "sh_index": _number(row["sh_index"]),
            }
        )
    source = "AKShare 融资买入 + 上交所/深交所官方日成交额 + 上证指数日线"
    note = "融资分子为沪深两市融资买入额之和；分母为上交所主板A股、科创板与深交所主板A股、创业板A股成交额之和，不含北交所。"
    return rows, source, note, date_keys


def _margin_panel(days: int, refresh: bool = False) -> dict[str, Any]:
    result = run_with_timeout("dashboard margin/index", _margin_rows, seconds=20, source="AKShare", retries=0)
    if not result.ok or not result.value:
        return _panel("unavailable", "AKShare", error=result.error, note="融资、指数或官方成交额数据不可用。", sync=_sync_snapshot(_TURNOVER_SYNC))
    rows, source, note, dates = result.value
    sync = _start_turnover_sync(dates, force=refresh)
    rows = rows[-days:]
    if not rows:
        return _panel("unavailable", source, error="没有可对齐的交易日", note=note, sync=sync)
    complete = all(row["ratio"] is not None for row in rows)
    status = "syncing" if sync["active"] else "live" if complete and not sync["failed"] else "partial"
    return _panel(status, source, rows, as_of=rows[-1]["date"], note=note, sync=sync, methodology="沪深 A 股口径；成交额统一按元存储。", error=sync["error"])


def _overall_status(panels: dict[str, dict[str, Any]]) -> str:
    statuses = {panel["status"] for panel in panels.values()}
    if "syncing" in statuses:
        return "syncing"
    if statuses == {"live"}:
        return "live"
    if statuses == {"unavailable"}:
        return "unavailable"
    return "partial"


def get_market_dashboard(days: int = 20, refresh: bool = False) -> dict[str, Any]:
    days = days if days in (20, 30, 60) else 20
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(days)
    if cached and not refresh and now - cached[0] < CACHE_SECONDS:
        payload = copy.deepcopy(cached[1])
        payload["cache_age_seconds"] = round(now - cached[0])
        if payload["status"] != "syncing":
            payload["status"] = "cache"
        for panel in payload["panels"].values():
            if panel["status"] in {"live", "partial"}:
                panel["status"] = "cache"
        return payload

    sector = _sector_panel(days=days, refresh=refresh)
    margin = _margin_panel(days=days, refresh=refresh)
    panels = {"sector_amount_ratio": sector, "margin_index": margin}
    payload = {
        "as_of": max((panel.get("as_of", "") for panel in panels.values()), default=""),
        "status": _overall_status(panels),
        "cache_age_seconds": 0,
        "sources": sorted({panel["source"] for panel in panels.values() if panel.get("source")}),
        "syncing": any(panel.get("sync", {}).get("active") for panel in panels.values()),
        "panels": panels,
    }
    with _CACHE_LOCK:
        _CACHE[days] = (now, copy.deepcopy(payload))
    return payload
