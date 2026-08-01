from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.scoring.sentiment_engine import SentimentScorer
from tools.scoring.web_research import _search

OUTPUT_BASE = ROOT / "knowledge" / "research" / "stock_discussion"
UA = "moda-v4-discussion/1.0"
PROMOTION_TERMS = ("必涨", "稳赚", "翻倍", "内部消息", "老师带", "加群", "微信群", "VIP", "收费群", "主力建仓", "最后上车", "股神", "跟单")
RUMOR_TERMS = ("谣言", "辟谣", "澄清", "虚假", "操纵", "荐股骗局", "杀猪盘")


def _json_get(url: str, params: dict[str, Any] | None = None, timeout: float = 12) -> Any:
    response = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _symbol(code: str) -> str:
    return f"{'SH' if code.startswith('6') else 'BJ' if code.startswith(('4', '8')) else 'SZ'}{code}"


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value or "").replace("&nbsp;", " ").strip()


def _xueqiu(code: str, name: str, count: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in (name, _symbol(code), code):
        try:
            payload = _json_get("https://xueqiu.com/statuses/search.json", {"q": query, "count": count})
        except Exception:
            continue
        items = payload.get("statuses") or payload.get("list") or []
        for item in items:
            text = _strip_html(str(item.get("text") or item.get("description") or ""))
            key = str(item.get("id") or text[:100])
            if len(text) < 5 or key in seen:
                continue
            seen.add(key)
            user = item.get("user") if isinstance(item.get("user"), dict) else {}
            rows.append({
                "source": "xueqiu",
                "title": text[:80],
                "text": text,
                "snippet": text[:300],
                "url": f"https://xueqiu.com/S/{_symbol(code)}",
                "author": user.get("screen_name", ""),
                "likes": item.get("like_count"),
                "replies": item.get("reply_count"),
                "retweets": item.get("retweet_count"),
                "status": "结构化接口",
            })
    return rows[:count]


def _eastmoney(code: str, count: int = 20) -> list[dict[str, Any]]:
    urls = [
        f"https://guba.eastmoney.com/list,{code},99,f.html",
        f"https://guba.eastmoney.com/list,{code}.html",
    ]
    for url in urls:
        try:
            response = requests.get(url, headers={"User-Agent": UA}, timeout=12)
            response.raise_for_status()
            html = response.text
        except Exception:
            continue
        rows: list[dict[str, Any]] = []
        for match in re.finditer(r"<a[^>]+href=\"([^\"]+)\"[^>]*>([^<]{3,120})</a>", html, re.I):
            title = re.sub(r"\s+", " ", _strip_html(match.group(2)))
            href = urljoin("https://guba.eastmoney.com/", match.group(1))
            if not title or title in {row["title"] for row in rows}:
                continue
            if not any(token in href for token in ("/caifuhao.eastmoney.com/news/", "/news,")):
                continue
            rows.append({"source": "eastmoney", "title": title, "text": title, "snippet": title, "url": href, "author": "", "status": "结构化接口"})
            if len(rows) >= count:
                return rows
        if rows:
            return rows
    return []


def _search_fallback(code: str, name: str, timeout: float = 12) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    providers: list[str] = []
    query = f"{name} {code} 股票 讨论 看多 看空 风险"
    used, rows, search_errors = _search("auto", query, timeout)
    errors.extend(search_errors)
    if used != "none":
        providers.append(used)
    for row in rows[:10]:
        title = str(row.get("title") or "")
        snippet = str(row.get("snippet") or "")
        records.append({
            "source": used,
            "title": title,
            "text": f"{title}。{snippet}".strip("。"),
            "snippet": snippet,
            "url": row.get("url") or "",
            "author": "",
            "status": "网络命中（未核验）",
            "query": query,
            "provider": used,
        })
    return records, providers, errors


def _score(records: list[dict[str, Any]]) -> dict[str, Any]:
    scorer = SentimentScorer()
    scored: list[dict[str, Any]] = []
    promotions: set[str] = set()
    rumors: set[str] = set()
    for record in records:
        text = str(record.get("text") or record.get("snippet") or "")
        result = scorer.score_text(text)
        record["sentiment"] = result["label"]
        record["sentiment_score"] = result["score"]
        record["promotion_hits"] = [term for term in PROMOTION_TERMS if term in text]
        record["rumor_hits"] = [term for term in RUMOR_TERMS if term in text]
        promotions.update(record["promotion_hits"])
        rumors.update(record["rumor_hits"])
        scored.append(result)
    labels = [item.get("sentiment") for item in records]
    avg = sum(float(item.get("sentiment_score") or 0) for item in records) / len(records) if records else None
    sentiment = "看多" if avg is not None and avg > 0.1 else "看空" if avg is not None and avg < -0.1 else "中性" if avg is not None else None
    return {
        "discussion_sentiment": sentiment,
        "discussion_sentiment_score": round(avg, 3) if avg is not None else None,
        "discussion_positive_count": labels.count("看多"),
        "discussion_negative_count": labels.count("看空"),
        "discussion_neutral_count": labels.count("中性"),
        "discussion_promotion_hits": sorted(promotions),
        "discussion_rumor_hits": sorted(rumors),
    }


def collect(code: str, name: str, timeout: float = 12) -> dict[str, Any]:
    structured: list[dict[str, Any]] = []
    xueqiu = _xueqiu(code, name)
    eastmoney = _eastmoney(code)
    structured.extend(xueqiu)
    structured.extend(eastmoney)
    providers = [source for source, rows in (("xueqiu", xueqiu), ("eastmoney", eastmoney)) if rows]
    records = structured
    search_errors: list[str] = []
    if not records:
        records, search_providers, search_errors = _search_fallback(code, name, timeout)
        providers.extend(search_providers)
    summary = _score(records)
    only_no_results = bool(search_errors) and all(item.endswith(":no_results") for item in search_errors)
    source_status = "结构化接口" if structured else "网络命中（未核验）" if records else "已搜索未命中" if only_no_results else "搜索失败，需人工确认"
    return {
        "discussion_posts_total": len(records),
        "discussion_structured_count": len(structured),
        "discussion_search_count": len(records) - len(structured),
        "discussion_source_count": len(set(providers)),
        "discussion_source_status": source_status,
        "discussion_sources": providers,
        "discussion_partial": not structured or bool(search_errors),
        "discussion_records": records[:25],
        "discussion_search_errors": search_errors,
        **summary,
    }


def build_report(code: str, name: str, data: dict[str, Any]) -> str:
    lines = [
        f"# 个股讨论与情绪：{name}（{code}）",
        "",
        f"> 采集时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  来源：雪球/东方财富公开接口；失败后 SearXNG → DuckDuckGo MCP",
        "",
        f"<!-- moda_stock_discussion: {json.dumps(data, ensure_ascii=False)} -->",
        "",
        f"- 讨论条数：{data.get('discussion_posts_total', 0)}；结构化 {data.get('discussion_structured_count', 0)}；搜索补缺 {data.get('discussion_search_count', 0)}",
        f"- 来源状态：{data.get('discussion_source_status', '需人工确认')}；来源：{'、'.join(data.get('discussion_sources', [])) or '无'}",
        f"- 汇总情绪：{data.get('discussion_sentiment') or '需人工确认'}（{data.get('discussion_sentiment_score') if data.get('discussion_sentiment_score') is not None else '需人工确认'}）",
        f"- 看多/中性/看空：{data.get('discussion_positive_count', 0)} / {data.get('discussion_neutral_count', 0)} / {data.get('discussion_negative_count', 0)}",
        f"- 推广话术：{'、'.join(data.get('discussion_promotion_hits') or []) or '无'}",
        f"- 谣言/风险词：{'、'.join(data.get('discussion_rumor_hits') or []) or '无'}",
        "",
        "## 讨论明细",
        "",
        "| 来源 | 情绪 | 标题/摘要 | 状态 |",
        "|---|---|---|---|",
    ]
    for item in data.get("discussion_records", []):
        text = str(item.get("text") or item.get("snippet") or "").replace("|", "/").replace("\n", " ")[:180]
        lines.append(f"| {item.get('source', '-')} | {item.get('sentiment', '需人工确认')} | {text} | {item.get('status', '需人工确认')} |")
    if not data.get("discussion_records"):
        lines.append("| - | 需人工确认 | 未获得个股讨论 | 已搜索未命中或搜索失败 |")
    lines.extend(["", "说明：搜索摘要只作为未核验线索，不覆盖行情、财务和公告结构化证据；未命中不等于安全。", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect stock discussion without CloakBrowser")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    name = args.name or args.stock
    data = collect(args.stock.strip(), name)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{args.stock.strip()}.md"
    path.write_text(build_report(args.stock.strip(), name, data), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
