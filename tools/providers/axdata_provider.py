"""Optional AxData enrichment without making AxData a runtime dependency."""

from __future__ import annotations

import importlib
import inspect
import os
from typing import Any


ENDPOINTS = {
    "finance": "stock_finance_summary_tdx",
    "profit_cashflow": "stock_profit_cashflow_summary_tdx",
    "balance": "stock_balance_summary_tdx",
    "valuation": "stock_valuation_metrics_tdx",
    "valuation_series": "stock_valuation_series_tdx",
    "valuation_band": "stock_valuation_band_tdx",
    "share_capital": "stock_share_capital_tdx",
    "shareholder_changes": "stock_shareholder_change_plans_tdx",
}


def _module() -> Any | None:
    if os.getenv("MODA_AXDATA", "").lower() not in {"1", "true", "yes"}:
        return None
    for name in ("axdata", "AxData"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


def available() -> bool:
    return _module() is not None


def fetch(kind: str, code: str, **kwargs: Any) -> Any | None:
    """Call a named AxData endpoint only when explicitly enabled."""
    module = _module()
    function_name = ENDPOINTS.get(kind)
    if module is None or not function_name:
        return None
    fn = getattr(module, function_name, None)
    if not callable(fn):
        return None
    params = {"symbol": code, "code": code, **kwargs}
    try:
        signature = inspect.signature(fn)
        accepted = {name for name in signature.parameters if name != "self"}
        call_kwargs = {name: value for name, value in params.items() if name in accepted}
        if not call_kwargs and signature.parameters:
            return fn(code)
        return fn(**call_kwargs)
    except (TypeError, ValueError, ImportError):
        return None
