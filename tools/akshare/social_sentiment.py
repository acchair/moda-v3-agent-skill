from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from typing import Callable
from urllib.parse import quote

import requests


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_BASE = ROOT / "knowledge" / "research" / "social_sentiment"
CACHE_BASE = ROOT / "knowledge" / "research" / "pipeline" / "cache" / "social_hot"
CACHE_TTL = 300
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
PROMOTION_TERMS = ("必涨", "稳赚", "翻倍", "内部消息", "老师带", "加群", "主力建仓", "最后上车", "股神", "跟单")
RUMOR_TERMS = ("谣言", "辟谣", "澄清", "虚假", "操纵", "荐股骗局", "杀猪盘")

DISCUSSION_SCRIPT = ROOT / "tools" / "scoring" / "stock_discussion.py"


def _json(url: str, timeout: float = 10) -> dict:
    response = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _weibo() -> list[dict]:
    rows = (_json("https://weibo.com/ajax/side/hotSearch").get("data") or {}).get("realtime") or []
    return [{"rank": i, "title": row.get("word", ""), "url": f"https://s.weibo.com/weibo?q={quote(row.get('word', ''))}"} for i, row in enumerate(rows[:50], 1) if row.get("word")]


def _zhihu() -> list[dict]:
    rows = _json("https://www.zhihu.com/api/v3/feed/topstory/hot-list-web?limit=50&desktop=true").get("data") or []
    output = []
    for i, row in enumerate(rows[:50], 1):
        target = row.get("target") or {}
        title = (target.get("title_area") or {}).get("text") or target.get("title") or ""
        if title:
            output.append({"rank": i, "title": title, "url": ((target.get("link") or {}).get("url") or "")})
    return output


def _baidu() -> list[dict]:
    cards = (_json("https://top.baidu.com/api/board?platform=wise&tab=realtime").get("data") or {}).get("cards") or []
    rows = (cards[0] or {}).get("content") or [] if cards else []
    if rows and isinstance(rows[0], dict) and isinstance(rows[0].get("content"), list):
        rows = rows[0]["content"]
    return [{"rank": i, "title": row.get("word") or row.get("query") or "", "url": row.get("url") or ""} for i, row in enumerate(rows[:50], 1) if row.get("word") or row.get("query")]


def _douyin() -> list[dict]:
    rows = (_json("https://www.douyin.com/aweme/v1/web/hot/search/list/").get("data") or {}).get("word_list") or []
    return [{"rank": i, "title": row.get("word", ""), "url": f"https://www.douyin.com/search/{quote(row.get('word', ''))}"} for i, row in enumerate(rows[:50], 1) if row.get("word")]


def _toutiao() -> list[dict]:
    rows = _json("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc").get("data") or []
    return [{"rank": i, "title": row.get("Title") or row.get("title") or "", "url": ""} for i, row in enumerate(rows[:50], 1) if row.get("Title") or row.get("title")]


def _bilibili() -> list[dict]:
    rows = _json("https://s.search.bilibili.com/main/hotword?limit=50").get("list") or []
    return [{"rank": i, "title": row.get("keyword") or row.get("show_name") or "", "url": ""} for i, row in enumerate(rows[:50], 1) if row.get("keyword") or row.get("show_name")]


FETCHERS: dict[str, Callable[[], list[dict]]] = {
    "weibo": _weibo,
    "zhihu": _zhihu,
    "baidu": _baidu,
    "douyin": _douyin,
    "toutiao": _toutiao,
    "bilibili": _bilibili,
}


def _cached(platform: str, fetcher: Callable[[], list[dict]]) -> tuple[list[dict], str]:
    CACHE_BASE.mkdir(parents=True, exist_ok=True)
    path = CACHE_BASE / f"{platform}.json"
    if path.exists() and time.time() - path.stat().st_mtime <= CACHE_TTL:
        try:
            return json.loads(path.read_text(encoding="utf-8")), "cache"
        except (json.JSONDecodeError, OSError):
            pass
    rows = fetcher()
    if rows:
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows, "live"


def _aliases(name: str, code: str) -> list[str]:
    values = [name.strip(), code]
    compact = name.strip()
    for suffix in ("股份有限公司", "有限责任公司", "股份", "集团"):
        if compact.endswith(suffix) and len(compact.removesuffix(suffix)) >= 3:
            values.append(compact.removesuffix(suffix))
    return list(dict.fromkeys(value for value in values if len(value) >= 3))


def collect(code: str, name: str) -> dict:
    results: dict[str, dict] = {}

    def one(item: tuple[str, Callable[[], list[dict]]]) -> tuple[str, dict]:
        platform, fetcher = item
        try:
            rows, mode = _cached(platform, fetcher)
            return platform, {"ok": bool(rows), "mode": mode, "items": rows, "error": "" if rows else "empty response"}
        except Exception as exc:
            return platform, {"ok": False, "mode": "failed", "items": [], "error": f"{type(exc).__name__}: {str(exc)[:100]}"}

    with ThreadPoolExecutor(max_workers=len(FETCHERS)) as executor:
        for platform, result in executor.map(one, FETCHERS.items()):
            results[platform] = result

    aliases = _aliases(name, code)
    mentions: dict[str, list[dict]] = {}
    for platform, result in results.items():
        mentions[platform] = [row for row in result["items"] if any(alias in row.get("title", "") for alias in aliases)]
    hits = sum(len(rows) for rows in mentions.values())
    platform_hits = sum(bool(rows) for rows in mentions.values())
    checked = sum(result["ok"] for result in results.values())
    matched_text = " ".join(row.get("title", "") for rows in mentions.values() for row in rows)
    promotion_hits = [term for term in PROMOTION_TERMS if term in matched_text]
    rumor_hits = [term for term in RUMOR_TERMS if term in matched_text]
    rank_weight = sum(max(0.0, (51 - float(row.get("rank", 50))) / 50) for rows in mentions.values() for row in rows)
    social_heat = min(1.0, (platform_hits / 3) * 0.6 + min(0.4, rank_weight * 0.12)) if checked >= 3 else None
    discussion = _collect_discussion(code, name)
    return {
        "social_platforms_checked": checked,
        "social_platforms_total": len(FETCHERS),
        "social_hot_hits": hits,
        "social_platform_hits": platform_hits,
        "social_heat": round(social_heat, 4) if social_heat is not None else None,
        "social_mentions": mentions,
        "promotional_keyword_hits": promotion_hits,
        "rumor_keyword_hits": rumor_hits,
        "social_aliases": aliases,
        "social_platform_status": {key: {"ok": value["ok"], "mode": value["mode"], "error": value["error"]} for key, value in results.items()},
        "social_partial": checked < len(FETCHERS),
        **discussion,
    }


def _collect_discussion(code: str, name: str) -> dict:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("moda_stock_discussion", DISCUSSION_SCRIPT)
        if spec is None or spec.loader is None:
            raise ImportError("discussion module unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.collect(code, name)
    except Exception as exc:
        return {
            "discussion_posts_total": 0,
            "discussion_structured_count": 0,
            "discussion_search_count": 0,
            "discussion_source_count": 0,
            "discussion_source_status": "搜索失败，需人工确认",
            "discussion_sources": [],
            "discussion_partial": True,
            "discussion_records": [],
            "discussion_search_errors": [f"{type(exc).__name__}: {str(exc)[:120]}"],
            "discussion_sentiment": None,
            "discussion_sentiment_score": None,
            "discussion_positive_count": 0,
            "discussion_negative_count": 0,
            "discussion_neutral_count": 0,
            "discussion_promotion_hits": [],
            "discussion_rumor_hits": [],
        }


def build_report(code: str, name: str, data: dict) -> str:
    lines = [
        f"# 社交热榜与异常推广风险：{name or code}（{code}）",
        "",
        f"> 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}  |  数据源：微博/知乎/百度/抖音/头条/B站公开热榜",
        "",
        f"<!-- moda_social_sentiment: {json.dumps(data, ensure_ascii=False)} -->",
        "",
        f"- 可用平台：{data['social_platforms_checked']} / {data['social_platforms_total']}",
        f"- 命中：{data['social_hot_hits']} 条，覆盖 {data['social_platform_hits']} 个平台",
        f"- 社交热度：{data['social_heat'] if data['social_heat'] is not None else '需人工确认'}",
        f"- 推广话术命中：{'、'.join(data['promotional_keyword_hits']) or '无'}；个股讨论：{'、'.join(data.get('discussion_promotion_hits') or []) or '无'}",
        f"- 谣言/风险词命中：{'、'.join(data['rumor_keyword_hits']) or '无'}",
        f"- 个股讨论：{data.get('discussion_posts_total', 0)} 条；情绪 {data.get('discussion_sentiment') or '需人工确认'}；来源 {data.get('discussion_source_status', '需人工确认')}",
        "",
        "## 命中明细",
        "",
        "| 平台 | 排名 | 标题 |",
        "|---|---:|---|",
    ]
    for platform, rows in data["social_mentions"].items():
        for row in rows:
            lines.append(f"| {platform} | {row.get('rank', '-')} | {str(row.get('title', '')).replace('|', '/')} |")
    if not data["social_hot_hits"]:
        lines.append("| - | - | 当前可用热榜未命中 |")
    lines += [
        "",
        "说明：热榜只证明关注度。异常推广风险必须与基本面、K 线、公告澄清等独立证据交叉验证，未命中不等于安全。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect social hot-list and promotion-risk evidence")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    code = args.stock.strip()
    data = collect(code, args.name or code)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{code}.md"
    path.write_text(build_report(code, args.name or code, data), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
