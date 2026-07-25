from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

import pandas as pd


def _empty_history() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"])


def _load_easy_tdx():
    import easy_tdx
    from easy_tdx import Adjust, MacClient, Market, Period

    return easy_tdx, MacClient, Market, Period, Adjust


@lru_cache(maxsize=1)
def _best_host() -> str:
    """Select a host without racing on easy_tdx's shared config file."""
    from easy_tdx.config import get_best_host, get_mac_hosts, get_port
    from easy_tdx.transport.sync import ping_mac_all

    ranked = ping_mac_all(get_mac_hosts(), get_port(), 1.5)
    return ranked[0][0] if ranked else get_best_host()


def _client(timeout: float):
    _, MacClient, _, _, _ = _load_easy_tdx()
    from easy_tdx.config import get_port

    return MacClient(_best_host(), get_port(), timeout=timeout)


def _market(code: str):
    _, _, Market, _, _ = _load_easy_tdx()
    code = str(code).strip()
    if code.startswith(("6", "9")):
        return Market.SH
    if code.startswith(("4", "8")):
        return Market.BJ
    return Market.SZ


def _period(value: str):
    _, _, _, Period, _ = _load_easy_tdx()
    mapping = {
        "daily": Period.DAILY,
        "day": Period.DAILY,
        "weekly": Period.WEEKLY,
        "week": Period.WEEKLY,
        "monthly": Period.MONTHLY,
        "month": Period.MONTHLY,
        "quarterly": Period.QUARTERLY,
        "quarter": Period.QUARTERLY,
        "1": Period.MIN_1,
        "5": Period.MIN_5,
        "15": Period.MIN_15,
        "30": Period.MIN_30,
        "60": Period.MIN_60,
    }
    return mapping.get(str(value).lower(), Period.DAILY)


def _adjust(value: str | None):
    _, _, _, _, Adjust = _load_easy_tdx()
    mapping = {
        None: Adjust.NONE,
        "": Adjust.NONE,
        "none": Adjust.NONE,
        "qfq": Adjust.QFQ,
        "hfq": Adjust.HFQ,
    }
    return mapping.get(str(value).lower() if value is not None else None, Adjust.NONE)


def _to_number(value: Any) -> Any:
    if value is None:
        return None
    out = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(out):
        return value
    return float(out)


def normalize_kline(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty_history()
    out = pd.DataFrame()
    if "datetime" in df.columns:
        out["date"] = pd.to_datetime(df["datetime"], errors="coerce")
    elif "date" in df.columns:
        out["date"] = pd.to_datetime(df["date"], errors="coerce")
    for src, dst in {
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "vol": "volume",
        "volume": "volume",
        "amount": "amount",
        "turnover": "turnover",
    }.items():
        if src in df.columns and dst not in out.columns:
            out[dst] = pd.to_numeric(df[src], errors="coerce")
    if "pct_chg" not in out.columns and "close" in out.columns:
        out["pct_chg"] = out["close"].pct_change() * 100
    if {"date", "close"}.issubset(out.columns):
        out = out.dropna(subset=["date", "close"], how="any")
    return out if not out.empty else _empty_history()


def fetch_kline(
    code: str,
    period: str = "daily",
    count: int = 800,
    adjust: str | None = "qfq",
    timeout: float = 3.0,
) -> pd.DataFrame:
    with _client(timeout) as client:
        df = client.get_stock_kline(
            _market(code),
            str(code).strip(),
            _period(period),
            count=count,
            adjust=_adjust(adjust),
        )
    return normalize_kline(df)


def fetch_kline_daily(code: str, count: int = 800, adjust: str | None = "qfq") -> pd.DataFrame:
    return fetch_kline(code, period="daily", count=count, adjust=adjust)


def fetch_realtime_quote(code: str, timeout: float = 3.0) -> dict[str, Any]:
    with _client(timeout) as client:
        df = client.get_stock_quotes([(_market(code), str(code).strip())])
    if df is None or df.empty:
        return {}
    row = df.iloc[0]
    close = _to_number(row.get("close"))
    pre_close = _to_number(row.get("pre_close"))
    change = None
    pct = None
    if isinstance(close, float) and isinstance(pre_close, float) and pre_close:
        change = close - pre_close
        pct = change / pre_close * 100
    return {
        "source": "easy_tdx",
        "最新价": close,
        "涨跌幅": pct,
        "涨跌额": change,
        "换手率": _to_number(row.get("turnover")),
        "量比": _to_number(row.get("vol_ratio")),
        "市盈率-动态": _to_number(row.get("pe_dynamic")),
        "市盈率-TTM": _to_number(row.get("pe_ttm")),
        "市净率": _to_number(row.get("pb")),
        "总市值": _to_number(row.get("total_market_cap_ab")),
        "流通市值": _to_number(row.get("circulating_market_cap")),
        "成交量": _to_number(row.get("vol")),
        "成交额": _to_number(row.get("amount")),
        "主力净额": _to_number(row.get("main_net_amount")),
    }


def fetch_announcements(code: str, count: int = 30, timeout: float = 8.0) -> pd.DataFrame:
    from easy_tdx.cninfo import CninfoClient

    return CninfoClient(timeout=timeout).get_announcements(str(code).strip(), count=count)


def fetch_financial_report(
    code: str,
    report_type: str = "lrb",
    num: int = 8,
    timeout: float = 8.0,
) -> pd.DataFrame:
    from easy_tdx.sina import SinaClient

    return SinaClient(timeout=timeout).get_financial_report(str(code).strip(), report_type, num=num)


def fetch_tick_chart(code: str, date: int | None = None, timeout: float = 3.0) -> pd.DataFrame:
    with _client(timeout) as client:
        return client.get_tick_chart(_market(code), str(code).strip(), date=date)


def fetch_transactions(code: str, count: int = 2000, date: int | None = None, timeout: float = 3.0) -> pd.DataFrame:
    with _client(timeout) as client:
        return client.get_transactions(_market(code), str(code).strip(), count=count, date=date)


def fetch_capital_flow(code: str, timeout: float = 3.0) -> pd.DataFrame:
    with _client(timeout) as client:
        return client.get_capital_flow(_market(code), str(code).strip())


def fetch_company_info(code: str, timeout: float = 3.0) -> pd.DataFrame:
    with _client(timeout) as client:
        return client.get_symbol_info(_market(code), str(code).strip())


def fetch_belong_boards(code: str, timeout: float = 3.0) -> pd.DataFrame:
    with _client(timeout) as client:
        return client.get_belong_board(_market(code), str(code).strip())


def fetch_board_list(timeout: float = 3.0) -> pd.DataFrame:
    with _client(timeout) as client:
        return client.get_board_list()


def fetch_board_members(board_symbol: str, timeout: float = 3.0) -> pd.DataFrame:
    with _client(timeout) as client:
        return client.get_board_members(board_symbol)


def fetch_board_ranking(timeout: float = 3.0) -> pd.DataFrame:
    with _client(timeout) as client:
        return client.get_board_ranking()


def available() -> dict[str, Any]:
    try:
        easy_tdx, *_ = _load_easy_tdx()
        return {"ok": True, "version": getattr(easy_tdx, "__version__", "")}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@lru_cache(maxsize=1)
def _health_check_cached(bucket: int) -> dict[str, Any]:
    started = time.perf_counter()
    info = available()
    if not info.get("ok"):
        return {"ok": False, "status": "missing", **info}
    checks: dict[str, Any] = {}
    try:
        checks["kline"] = len(fetch_kline_daily("000001", count=5)) > 0
    except Exception as exc:
        checks["kline"] = False
        checks["kline_error"] = f"{type(exc).__name__}: {exc}"
    try:
        checks["quote"] = bool(fetch_realtime_quote("600519"))
    except Exception as exc:
        checks["quote"] = False
        checks["quote_error"] = f"{type(exc).__name__}: {exc}"
    try:
        checks["cninfo"] = not fetch_announcements("000001", count=3).empty
    except Exception as exc:
        checks["cninfo"] = False
        checks["cninfo_error"] = f"{type(exc).__name__}: {exc}"
    try:
        checks["sina"] = not fetch_financial_report("600519", "lrb", num=2).empty
    except Exception as exc:
        checks["sina"] = False
        checks["sina_error"] = f"{type(exc).__name__}: {exc}"
    ok = any(checks.get(key) for key in ["kline", "quote", "cninfo", "sina"])
    return {
        "ok": ok,
        "status": "ok" if ok else "unavailable",
        "version": info.get("version", ""),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "checks": checks,
    }


def health_check(cache_seconds: int = 60) -> dict[str, Any]:
    bucket = int(time.time() // max(1, cache_seconds))
    return _health_check_cached(bucket)
