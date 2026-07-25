"""
efinance fallback data provider.

The functions here normalize efinance output into the column names used by
the existing moda v3 scripts.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.data_patch import apply_data_patches


KLT_MAP = {"daily": 101, "weekly": 102, "monthly": 103, "5": 5, "15": 15, "30": 30, "60": 60}
FQT_MAP = {None: 0, "": 0, "none": 0, "qfq": 1, "hfq": 2}


def _load_efinance():
    apply_data_patches(verbose=False)
    import efinance as ef

    return ef


def _digits(value: Any) -> str:
    m = re.search(r"(\d{6})", str(value or ""))
    return m.group(1) if m else str(value or "").strip()


def _empty_history() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"])


def _find_column(df: pd.DataFrame, candidates: list[str], contains: list[str] | None = None) -> str | None:
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    if contains:
        for col in df.columns:
            text = str(col).lower()
            if any(piece.lower() in text for piece in contains):
                return col
    return None


def normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty_history()

    col_specs = {
        "date": (["日期", "date", "交易日期"], ["date", "日期"]),
        "open": (["开盘", "open"], ["open", "开盘"]),
        "high": (["最高", "high"], ["high", "最高"]),
        "low": (["最低", "low"], ["low", "最低"]),
        "close": (["收盘", "close"], ["close", "收盘"]),
        "volume": (["成交量", "volume"], ["volume", "成交量"]),
        "amount": (["成交额", "amount"], ["amount", "成交额"]),
        "pct_chg": (["涨跌幅", "pct_chg"], ["pct", "涨跌幅"]),
        "turnover": (["换手率", "turnover"], ["turnover", "换手率"]),
        "code": (["股票代码", "代码", "code"], ["code", "代码"]),
        "name": (["股票名称", "名称", "name"], ["name", "名称"]),
    }

    out = pd.DataFrame()
    for target, (exact, fuzzy) in col_specs.items():
        col = _find_column(df, exact, fuzzy)
        if col is not None:
            out[target] = df[col]

    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if {"date", "close"}.issubset(out.columns):
        out = out.dropna(subset=["date", "close"], how="any")
    return out if not out.empty else _empty_history()


def fetch_quote_history(code: str, period: str = "daily", adjust: str | None = "qfq") -> pd.DataFrame:
    """Fetch A-share K-line data from efinance."""
    try:
        ef = _load_efinance()
        df = ef.stock.get_quote_history(
            stock_codes=code,
            klt=KLT_MAP.get(str(period), 101),
            fqt=FQT_MAP.get(adjust, 0),
            suppress_error=True,
        )
        if isinstance(df, dict):
            df = df.get(code) or next(iter(df.values()), pd.DataFrame())
        return normalize_history(df)
    except Exception as exc:
        print(f"  [efinance-kline] failed: {exc}")
        return _empty_history()


def fetch_realtime_quotes(code: str) -> dict[str, Any]:
    """Fetch realtime quote for one A-share code from efinance."""
    try:
        ef = _load_efinance()
        try:
            df = ef.stock.get_latest_quote(stock_codes=code)
        except Exception:
            df = ef.stock.get_realtime_quotes()
        if df is None or df.empty:
            return {}

        code_col = _find_column(df, ["股票代码", "代码", "code"], ["代码", "code"])
        name_col = _find_column(df, ["股票名称", "名称", "name"], ["名称", "name"])
        if code_col:
            df = df[df[code_col].map(_digits) == code]
        if df.empty:
            return {}
        row = df.iloc[0]

        def pick(names: list[str], fuzzy: list[str]) -> Any:
            col = _find_column(df, names, fuzzy)
            return row.get(col) if col else None

        return {
            "source": "efinance",
            "name": str(row.get(name_col, "")) if name_col else "",
            "最新价": pick(["最新价"], ["最新"]),
            "涨跌幅": pick(["涨跌幅"], ["涨跌幅"]),
            "涨跌额": pick(["涨跌额"], ["涨跌额"]),
            "换手率": pick(["换手率"], ["换手率"]),
            "量比": pick(["量比"], ["量比"]),
            "市盈率-动态": pick(["市盈率-动态", "动态市盈率"], ["市盈率"]),
            "市净率": pick(["市净率"], ["市净率"]),
            "总市值": pick(["总市值"], ["总市值"]),
            "流通市值": pick(["流通市值"], ["流通市值"]),
        }
    except Exception as exc:
        print(f"  [efinance-quote] failed: {exc}")
        return {}


def search_stock(keyword: str, limit: int = 20) -> list[dict[str, str]]:
    """Search stocks by code or name using realtime quote table."""
    keyword = str(keyword or "").strip()
    if not keyword:
        return []
    try:
        ef = _load_efinance()
        df = ef.stock.get_realtime_quotes()
        if df is None or df.empty:
            return []
        code_col = _find_column(df, ["股票代码", "代码", "code"], ["代码", "code"])
        name_col = _find_column(df, ["股票名称", "名称", "name"], ["名称", "name"])
        if not code_col or not name_col:
            return []
        mask = df[code_col].astype(str).str.contains(keyword, na=False, regex=False) | df[name_col].astype(str).str.contains(keyword, na=False, regex=False)
        hits = []
        for _, row in df[mask].head(limit).iterrows():
            code = _digits(row.get(code_col))
            if code:
                hits.append({"code": code, "name": str(row.get(name_col, "")) or code, "source": "efinance"})
        return hits
    except Exception as exc:
        print(f"  [efinance-search] failed: {exc}")
        return []
