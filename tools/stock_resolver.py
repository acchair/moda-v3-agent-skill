"""Fast A-share stock code/name resolution.

The resolver keeps a tiny JSON index next to the pipeline caches.  Code input
is accepted directly; Chinese names are resolved locally first and fall back
to efinance only on a cache miss.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "knowledge" / "research" / "pipeline" / "cache" / "stock_name_index.json"
_INDEX: dict[str, dict[str, str]] | None = None


def normalize_stock_text(value: Any) -> str:
    """Normalize names while preserving Chinese characters."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = re.sub(r"[\s()（）\[\]【】._-]+", "", text)
    return text


def _valid_code(value: Any) -> str | None:
    match = re.fullmatch(r"\d{6}", str(value or "").strip())
    return match.group(0) if match else None


def _read_index() -> dict[str, dict[str, str]]:
    if CACHE_PATH.exists():
        try:
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return {str(code): dict(item) for code, item in payload.items() if _valid_code(code) and isinstance(item, dict)}
        except (OSError, ValueError, TypeError):
            pass
    return {}


def _write_index(index: dict[str, dict[str, str]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def _seed_from_reports(index: dict[str, dict[str, str]]) -> None:
    """Best-effort seed from already collected finance reports."""
    report_dir = ROOT / "knowledge" / "research" / "finance_data"
    if not report_dir.exists():
        return
    pattern = re.compile(r"股票名称\s*[：:]\s*([^|\n]+)|\|\s*简称\s*\|\s*([^|\n]+)")
    for path in report_dir.glob("*.md"):
        code = _valid_code(path.stem)
        if not code or code in index:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = pattern.search(text)
        name = next((part.strip() for part in match.groups() if part and part.strip()), "") if match else ""
        if name and name not in {"代码", "简称"}:
            index[code] = {"code": code, "name": name, "source": "local_report"}


def load_index() -> dict[str, dict[str, str]]:
    global _INDEX
    if _INDEX is None:
        _INDEX = _read_index()
        before = len(_INDEX)
        _seed_from_reports(_INDEX)
        if len(_INDEX) != before:
            try:
                _write_index(_INDEX)
            except OSError:
                pass
    return _INDEX


def _remember(rows: list[dict[str, Any]]) -> None:
    index = load_index()
    changed = False
    for row in rows:
        code = _valid_code(row.get("code"))
        name = str(row.get("name") or "").strip()
        if code and name:
            item = {"code": code, "name": name, "source": str(row.get("source") or "efinance")}
            if index.get(code) != item:
                index[code] = item
                changed = True
    if changed:
        try:
            _write_index(index)
        except OSError:
            pass


def lookup_local(keyword: str, limit: int = 20) -> list[dict[str, str]]:
    query = normalize_stock_text(keyword)
    if not query:
        return []
    rows = list(load_index().values())
    exact = [row for row in rows if normalize_stock_text(row.get("code")) == query or normalize_stock_text(row.get("name")) == query]
    if exact:
        return exact[:limit]
    return [row for row in rows if query in normalize_stock_text(row.get("name")) or query in normalize_stock_text(row.get("code"))][:limit]


def resolve_stock_input(value: str, name: str = "") -> tuple[str, str]:
    """Return ``(code, display_name)`` for a six-digit code or Chinese name."""
    raw = str(value or "").strip()
    code = _valid_code(raw)
    if code:
        if name.strip():
            return code, name.strip()
        hits = lookup_local(code, limit=1)
        return code, (hits[0].get("name") or code) if hits else code

    hits = lookup_local(raw)
    if not hits:
        try:
            from tools.efinance.provider import search_stock

            hits = search_stock(raw, limit=20)
            _remember(hits)
        except Exception:
            hits = []
    if not hits:
        raise ValueError(f"未找到股票：{raw}")
    exact = [row for row in hits if normalize_stock_text(row.get("name")) == normalize_stock_text(raw)]
    candidates = exact or hits
    if len(candidates) > 1 and not exact:
        labels = "、".join(f"{row.get('name', '')}({row.get('code', '')})" for row in candidates[:8])
        raise ValueError(f"股票名称不唯一，请改用代码：{labels}")
    selected = candidates[0]
    return str(selected["code"]), str(selected.get("name") or raw)


def clear_memory_cache() -> None:
    global _INDEX
    _INDEX = None
