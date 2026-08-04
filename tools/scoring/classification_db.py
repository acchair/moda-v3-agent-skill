from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "tools" / "scoring" / "专精特新_行业龙头_核心供应商_A股名单_完整版.csv"
SOURCE_LABEL = "专精特新_行业龙头_核心供应商_A股名单_完整版.csv"
CATEGORY_ALIASES = {
    "specialized": "专精特新",
    "leadership": "行业龙头",
    "core_supplier": "核心供应商",
}


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() else text


def _normalize_name(value: Any) -> str:
    return "".join(str(value or "").split()).strip().lower()


def _categories(value: Any) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(value or "").replace("、", "/").split("/")
        if item.strip()
    )


@lru_cache(maxsize=1)
def _load_rows() -> tuple[dict[str, str], ...]:
    if not CSV_PATH.exists():
        return ()
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


def lookup(code: Any, name: str = "") -> dict[str, Any]:
    normalized_code = normalize_code(code)
    normalized_name = _normalize_name(name)
    matched: dict[str, str] | None = None
    match_by = ""

    for row in _load_rows():
        row_code = normalize_code(row.get("证券代码"))
        row_name = _normalize_name(row.get("证券简称"))
        if normalized_code and row_code == normalized_code:
            matched = row
            match_by = "证券代码"
            break
        if not matched and normalized_name and row_name == normalized_name:
            matched = row
            match_by = "证券简称"

    if not matched:
        return {
            "found": False,
            "match_by": "",
            "code": normalized_code,
            "name": name or "",
            "categories": (),
            "row": None,
            "source": SOURCE_LABEL,
        }

    categories = _categories(matched.get("分类"))
    return {
        "found": True,
        "match_by": match_by,
        "code": normalize_code(matched.get("证券代码")),
        "name": matched.get("证券简称", ""),
        "industry": matched.get("行业名称", ""),
        "categories": categories,
        "row": matched,
        "source": SOURCE_LABEL,
    }


def has_category(result: dict[str, Any], category: str) -> bool:
    label = CATEGORY_ALIASES.get(category, category)
    return label in set(result.get("categories") or ())


def clear_cache() -> None:
    _load_rows.cache_clear()
