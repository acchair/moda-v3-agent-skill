"""
Unified data-source request patches.

Call apply_data_patches() before importing akshare, efinance, or yfinance.
The paid akshare-proxy-patch is installed lazily only for known slow endpoints.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
AKSHARE_TOOLS = ROOT / "tools" / "akshare"

AKSHARE_PROXY_HOOK_DOMAINS = [
    "fund.eastmoney.com",
    "push2.eastmoney.com",
    "push2his.eastmoney.com",
    "datacenter-web.eastmoney.com",
    "emweb.securities.eastmoney.com",
]

_PATCHED = False
_PROXY_ENABLED = False
_PROXY_DOMAINS: set[str] = set()
_STATUS: dict[str, Any] = {
    "akshare_proxy_patch": "not_applied",
    "local_anti_rate_limit": "not_applied",
    "yfinance_patch": "not_applied",
    "auth_ip": "",
    "token_masked": "",
    "hook_domains": [],
    "available_hook_domains": AKSHARE_PROXY_HOOK_DOMAINS,
    "proxy_health": {"status": "not_enabled", "detail": "paid proxy is enabled lazily"},
    "errors": [],
}


def _mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return f"{token[:2]}***{token[-2:]}"
    return f"{token[:4]}***{token[-4:]}"


def _proxy_config() -> tuple[str, str, str]:
    auth_ip = os.environ.get("AKSHARE_PROXY_AUTH_IP", "").strip()
    auth_token = os.environ.get("AKSHARE_PROXY_AUTH_TOKEN", "").strip()
    proxy_cookie = os.environ.get("AKSHARE_PROXY_COOKIE", "").strip()
    _STATUS["auth_ip"] = auth_ip
    _STATUS["token_masked"] = _mask_token(auth_token)
    return auth_ip, auth_token, proxy_cookie


def apply_data_patches(verbose: bool = True, enable_proxy: bool = False) -> dict[str, Any]:
    """Apply free/local patches once. Paid proxy stays lazy by default."""
    global _PATCHED
    if _PATCHED and not enable_proxy:
        return dict(_STATUS)

    _proxy_config()

    if not _PATCHED:
        try:
            if str(AKSHARE_TOOLS) not in sys.path:
                sys.path.insert(0, str(AKSHARE_TOOLS))
            from anti_rate_limit import apply_patch as apply_local_patch

            apply_local_patch()
            _STATUS["local_anti_rate_limit"] = "applied"
        except Exception as exc:
            _STATUS["local_anti_rate_limit"] = "failed"
            _STATUS["errors"].append(f"local_anti_rate_limit: {exc}")

        _PATCHED = True

    if enable_proxy:
        ensure_akshare_proxy_patch(AKSHARE_PROXY_HOOK_DOMAINS, reason="explicit enable")

    if verbose:
        print(
            "  [data-patch] "
            f"akshare_proxy={_STATUS['akshare_proxy_patch']} | "
            f"local={_STATUS['local_anti_rate_limit']} | "
            f"yfinance={_STATUS['yfinance_patch']}"
        )
    return dict(_STATUS)


def patch_status() -> dict[str, Any]:
    return dict(_STATUS)


def ensure_akshare_proxy_patch(hook_domains: list[str] | tuple[str, ...] | None = None, reason: str = "") -> dict[str, Any]:
    """Install the paid proxy patch only when a slow endpoint needs it."""
    global _PROXY_ENABLED, _PROXY_DOMAINS
    apply_data_patches(verbose=False)
    domains = set(hook_domains or AKSHARE_PROXY_HOOK_DOMAINS)
    domains = {d for d in domains if d}
    if _PROXY_ENABLED and domains.issubset(_PROXY_DOMAINS):
        return dict(_STATUS)

    auth_ip, auth_token, proxy_cookie = _proxy_config()
    if not auth_ip or not auth_token:
        _STATUS["akshare_proxy_patch"] = "skipped: proxy credentials not set"
        return dict(_STATUS)

    try:
        import akshare_proxy_patch

        _PROXY_DOMAINS.update(domains)
        akshare_proxy_patch.install_patch(
            auth_ip=auth_ip,
            auth_token=auth_token,
            retry=int(os.environ.get("AKSHARE_PROXY_RETRY", "5")),
            hook_domains=sorted(_PROXY_DOMAINS),
            timeout=int(os.environ.get("AKSHARE_PROXY_TIMEOUT", "8")),
            cookie=proxy_cookie,
        )
        _PROXY_ENABLED = True
        _STATUS["akshare_proxy_patch"] = "applied_lazy"
        _STATUS["hook_domains"] = sorted(_PROXY_DOMAINS)
        _STATUS["proxy_health"] = {"status": "enabled", "detail": reason or "enabled on demand"}
        print(f"  [data-patch] paid proxy enabled for {', '.join(sorted(domains))} ({reason or 'on demand'})")
    except Exception as exc:
        _STATUS["akshare_proxy_patch"] = "failed"
        _STATUS["errors"].append(f"akshare_proxy_patch: {exc}")
    return dict(_STATUS)


def ensure_yfinance_proxy_patch(reason: str = "") -> dict[str, Any]:
    """Install paid yfinance proxy only if a Yahoo request explicitly needs it."""
    apply_data_patches(verbose=False)
    auth_ip, auth_token, _ = _proxy_config()
    if not auth_ip or not auth_token:
        _STATUS["yfinance_patch"] = "skipped: proxy credentials not set"
        return dict(_STATUS)
    try:
        import akshare_proxy_patch

        akshare_proxy_patch.install_yfinance_patch(
            auth_ip=auth_ip,
            auth_token=auth_token,
            retry=int(os.environ.get("YFINANCE_PROXY_RETRY", "5")),
        )
        _STATUS["yfinance_patch"] = "applied_lazy"
        print(f"  [data-patch] paid yfinance proxy enabled ({reason or 'on demand'})")
    except Exception as exc:
        _STATUS["yfinance_patch"] = "failed"
        _STATUS["errors"].append(f"yfinance_patch: {exc}")
    return dict(_STATUS)


def proxy_health_check(verbose: bool = True) -> dict[str, Any]:
    """Probe one tiny Eastmoney endpoint after patches are installed."""
    if not _PROXY_ENABLED:
        result = {"status": "not_enabled", "detail": "paid proxy has not been used in this process"}
        _STATUS["proxy_health"] = result
        return result
    started = time.perf_counter()
    try:
        import requests

        url = "https://push2.eastmoney.com/api/qt/stock/get"
        resp = requests.get(
            url,
            params={"secid": "0.000001", "fields": "f57"},
            timeout=int(os.environ.get("AKSHARE_PROXY_HEALTH_TIMEOUT", "8")),
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        if resp.status_code == 200 and "000001" in resp.text:
            result = {"status": "ok", "detail": "Eastmoney proxy probe ok", "elapsed_ms": elapsed_ms}
        else:
            result = {
                "status": "warning",
                "detail": f"HTTP {resp.status_code}, body={resp.text[:120]}",
                "elapsed_ms": elapsed_ms,
            }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        result = {"status": "failed", "detail": f"{type(exc).__name__}: {exc}", "elapsed_ms": elapsed_ms}
    if verbose:
        print(f"  [proxy-health] {result['status']} ({result.get('elapsed_ms')}ms): {result['detail']}")
    _STATUS["proxy_health"] = result
    return result
