from __future__ import annotations

import argparse
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_BASE = ROOT / "knowledge" / "research" / "web_research"
USER_AGENT = "moda-v4-research/1.0"
MAX_FETCH_BYTES = 600_000
MAX_PAGES = 12
AUTHORITY_DOMAINS = (
    "gov.cn", "cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn",
    "stats.gov.cn", "miit.gov.cn", "ndrc.gov.cn", "customs.gov.cn",
)
SUPPLY_CATEGORIES = {
    "price": ("价格", "涨价", "降价", "报价", "基差"),
    "inventory": ("库存", "仓单", "去库存"),
    "orders": ("订单", "在手订单", "交付", "排产", "交期"),
    "capacity": ("产能", "产能利用率", "扩产", "供不应求", "供过于求", "紧缺", "产能过剩"),
}
TIGHTENING_TERMS = ("供不应求", "紧缺", "缺货", "涨价", "库存下降", "去库存", "订单增长", "排产饱满", "交期延长", "产能利用率提升")
LOOSENING_TERMS = ("供过于求", "库存上升", "降价", "产能过剩", "订单下降", "需求下滑", "开工率下降")
COMPANY_RELATION_TERMS = ("产品", "设备", "业务", "供应商", "客户", "产业化", "量产")
REPLACEMENT_TERMS = ("国产替代", "进口替代", "自主可控", "国产化")
DEPENDENCY_TERMS = ("进口依赖", "卡脖子", "受制于人", "海外垄断", "国外垄断", "关键核心技术", "国产化率")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            value = re.sub(r"\s+", " ", data).strip()
            if value:
                self.parts.append(value)


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _is_authority(domain: str) -> bool:
    return any(domain == suffix or domain.endswith("." + suffix) for suffix in AUTHORITY_DOMAINS) or domain.endswith(".org.cn")


def _safe_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
        return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)
    except (OSError, ValueError):
        return False


def _response_payload(response: requests.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        payloads: list[dict[str, Any]] = []
        text = response.content.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    payloads.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    continue
        return next((payload for payload in reversed(payloads) if "result" in payload), payloads[-1] if payloads else {})
    try:
        return response.json()
    except ValueError:
        return {}


def _searxng_search(base_url: str, query: str, timeout: float) -> list[dict[str, Any]]:
    response = requests.get(
        base_url.rstrip("/") + "/search",
        params={"q": query, "format": "json", "language": "zh-CN", "safesearch": 1},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    rows = response.json().get("results", [])
    return [
        {
            "title": str(row.get("title") or "").strip(),
            "url": str(row.get("url") or "").strip(),
            "snippet": str(row.get("content") or "").strip(),
            "date": str(row.get("publishedDate") or "").strip(),
            "engine": ",".join(row.get("engines") or [str(row.get("engine") or "")]),
        }
        for row in rows[:8]
        if row.get("url")
    ]


def _parse_ddg_text(text: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        r"(?:^|\n)(?:##\s*)?\d+\.\s*(.*?)\n\s*(?:\*\*)?URL:(?:\*\*)?\s*(\S+)"
        r"(?:\n\s*(?:\*\*)?Summary:(?:\*\*)?\s*(.*?))?(?=\n\s*(?:##\s*)?\d+\.|\Z)",
        re.S,
    )
    return [
        {"title": title.strip(), "url": url.strip(), "snippet": (snippet or "").strip(), "date": "", "engine": "DuckDuckGo"}
        for title, url, snippet in pattern.findall(text)
    ]


def _ddg_mcp_search(url: str, query: str, timeout: float) -> list[dict[str, Any]]:
    session = requests.Session()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    initialize = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "moda-v4", "version": "1.0"}},
    }
    response = session.post(url, json=initialize, headers=headers, timeout=timeout)
    response.raise_for_status()
    session_id = response.headers.get("Mcp-Session-Id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    session.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers, timeout=timeout).raise_for_status()
    call = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "search", "arguments": {"query": query, "max_results": 8, "region": "cn-zh"}},
    }
    response = session.post(url, json=call, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = _response_payload(response)
    blocks = payload.get("result", {}).get("content", [])
    text = "\n".join(str(block.get("text") or "") for block in blocks if block.get("type") == "text")
    return _parse_ddg_text(text)


def _search(provider: str, query: str, timeout: float) -> tuple[str, list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    searxng = os.getenv("SEARXNG_URL", "").strip()
    ddg = os.getenv("DDG_MCP_URL", "").strip()
    if provider in {"auto", "searxng"} and searxng:
        try:
            rows = _searxng_search(searxng, query, timeout)
            if rows:
                return "searxng", rows, errors
            errors.append("searxng:no_results")
        except Exception as exc:
            errors.append(f"searxng:{type(exc).__name__}")
    if provider in {"auto", "duckduckgo"} and ddg:
        try:
            rows = _ddg_mcp_search(ddg, query, timeout)
            if rows:
                return "duckduckgo", rows, errors
            errors.append("duckduckgo:no_results")
        except Exception as exc:
            errors.append(f"duckduckgo:{type(exc).__name__}")
    if not searxng and not ddg:
        errors.append("search_backend_not_configured")
    return "none", [], errors


def _fetch_page(url: str, timeout: float) -> tuple[str, str]:
    current = url
    try:
        for _ in range(4):
            if not _safe_public_url(current):
                return "unsafe_url", ""
            response = requests.get(current, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=False, stream=True)
            if response.is_redirect:
                current = urljoin(current, response.headers.get("location", ""))
                continue
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(32_768):
                total += len(chunk)
                if total > MAX_FETCH_BYTES:
                    break
                chunks.append(chunk)
            encoding = response.encoding or "utf-8"
            html = b"".join(chunks).decode(encoding, errors="replace")
            parser = _TextExtractor()
            parser.feed(html)
            return "ok", " ".join(parser.parts)[:120_000]
        return "too_many_redirects", ""
    except Exception as exc:
        return type(exc).__name__, ""


def _classify(record: dict[str, Any], name: str) -> dict[str, Any]:
    text = str(record.get("content", ""))
    categories = [category for category, terms in SUPPLY_CATEGORIES.items() if any(term in text for term in terms)]
    tightening = any(term in text for term in TIGHTENING_TERMS)
    loosening = any(term in text for term in LOOSENING_TERMS)
    company_relation = bool(name and name in text and any(term in text for term in COMPANY_RELATION_TERMS) and any(term in text for term in REPLACEMENT_TERMS))
    industry_dependency = any(term in text for term in DEPENDENCY_TERMS) and any(term in text for term in REPLACEMENT_TERMS)
    domain = _domain(record.get("url", ""))
    return {
        **record,
        "domain": domain,
        "source_tier": "A" if _is_authority(domain) else "B",
        "supply_categories": categories,
        "supply_direction": "tightening" if tightening and not loosening else "loosening" if loosening and not tightening else "conflict" if tightening and loosening else "unknown",
        "company_product_relation": company_relation,
        "industry_dependency": industry_dependency,
    }


def _validate_supply(records: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [row for row in records if row.get("fetch_status") == "ok" and row.get("supply_categories") and row.get("supply_direction") in {"tightening", "loosening"}]
    domains = {row["domain"] for row in usable}
    categories = {category for row in usable for category in row["supply_categories"]}
    has_authority = any(row["source_tier"] == "A" for row in usable)
    directions = {row["supply_direction"] for row in usable}
    confirmed = len(domains) >= 2 and len(categories) >= 2 and has_authority and len(directions) == 1
    return {
        "status": "已验证" if confirmed else "证据冲突" if len(directions) > 1 else "需人工确认",
        "evidence_count": len(usable),
        "domain_count": len(domains),
        "categories": sorted(categories),
        "has_authority": has_authority,
        "tightening": next(iter(directions)) == "tightening" if confirmed else None,
        "reason": "两个不同域名、两类证据且含权威来源同向" if confirmed else "未满足双域名、双类别、权威来源和同向要求",
    }


def _validate_chokepoint(records: list[dict[str, Any]]) -> dict[str, Any]:
    company_rows = [row for row in records if row.get("fetch_status") == "ok" and row.get("company_product_relation")]
    industry_rows = [row for row in records if row.get("fetch_status") == "ok" and row.get("industry_dependency")]
    domains = {row["domain"] for row in company_rows + industry_rows}
    has_authority = any(row["source_tier"] == "A" for row in company_rows + industry_rows)
    confirmed = bool(company_rows and industry_rows and len(domains) >= 2 and has_authority)
    return {
        "status": "已验证" if confirmed else "需人工确认",
        "company_evidence_count": len(company_rows),
        "industry_evidence_count": len(industry_rows),
        "domain_count": len(domains),
        "has_authority": has_authority,
        "score": 80 if confirmed else None,
        "reason": "公司产品关系与行业进口依赖由不同来源交叉确认" if confirmed else "缺少公司产品关系、行业依赖、独立域名或权威来源",
    }


def collect(code: str, name: str, context: str, provider: str | None = None, timeout: float = 12) -> dict[str, Any]:
    selected = (provider or os.getenv("MODA_SEARCH_PROVIDER", "auto")).strip().lower()
    if selected not in {"auto", "searxng", "duckduckgo", "off"}:
        selected = "off"
    if selected == "off":
        return {"web_research_status": "disabled", "web_research_provider": "off", "queries": [], "results": [], "errors": []}

    queries = [
        f"{name} {context} 供不应求 订单 产能 库存",
        f"{name} {context} 国产替代 核心供应商 进口依赖",
        f"site:cninfo.com.cn {name} 订单 产能 国产替代",
        f"site:gov.cn {context} 国产替代 进口依赖 供需",
    ]
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    used_providers: list[str] = []
    seen: set[str] = set()
    for query in queries:
        used, rows, query_errors = _search(selected, query, timeout)
        errors.extend(f"{query[:24]}:{error}" for error in query_errors)
        if used != "none" and used not in used_providers:
            used_providers.append(used)
        for row in rows:
            url = row.get("url", "")
            if not url or url in seen or len(results) >= MAX_PAGES:
                continue
            seen.add(url)
            fetch_status, content = _fetch_page(url, timeout)
            classified = _classify({**row, "query": query, "fetch_status": fetch_status, "content": content}, name)
            classified.pop("content", None)
            results.append(classified)

    supply = _validate_supply(results)
    chokepoint = _validate_chokepoint(results)
    status = "completed" if results else "unavailable"
    return {
        "web_research_status": status,
        "web_research_provider": ",".join(used_providers) or "none",
        "queries": queries,
        "results": results,
        "errors": errors,
        "web_supply_validation": supply,
        "web_chokepoint_validation": chokepoint,
    }


def build_report(code: str, name: str, data: dict[str, Any]) -> str:
    lines = [
        f"# 搜索交叉验证：{name or code}（{code}）",
        "",
        f"> 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}  |  后端：{data.get('web_research_provider', 'none')}",
        "",
        f"<!-- moda_web_research: {json.dumps(data, ensure_ascii=False)} -->",
        "",
        f"- 运行状态：{data.get('web_research_status')}",
        f"- 供需验证：{data.get('web_supply_validation', {}).get('status', '需人工确认')}；{data.get('web_supply_validation', {}).get('reason', '搜索未运行')}",
        f"- 国产替代验证：{data.get('web_chokepoint_validation', {}).get('status', '需人工确认')}；{data.get('web_chokepoint_validation', {}).get('reason', '搜索未运行')}",
        "",
        "| 来源等级 | 标题 | 域名 | 正文 | 查询词 |",
        "|---|---|---|---|---|",
    ]
    for row in data.get("results", []):
        title = str(row.get("title", "")).replace("|", "/")
        query = str(row.get("query", "")).replace("|", "/")
        lines.append(f"| {row.get('source_tier', 'B')} | [{title}]({row.get('url', '')}) | {row.get('domain', '')} | {row.get('fetch_status', '')} | {query} |")
    if not data.get("results"):
        lines.append("| - | 无可核验结果 | - | - | - |")
    lines += ["", "搜索摘要只用于发现线索；正文未成功读取或未通过交叉验证时不得计分。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate supply and chokepoint evidence with optional web search")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--context", default="")
    parser.add_argument("--provider", default=None)
    args = parser.parse_args()
    code = args.stock.strip()
    data = collect(code, args.name or code, args.context, args.provider)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{code}.md"
    path.write_text(build_report(code, args.name or code, data), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
