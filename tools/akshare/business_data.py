from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUTPUT_BASE = ROOT / "knowledge" / "research" / "business_data"
TYPE_NAMES = {"1": "按行业分类", "2": "按产品分类", "3": "按地区分类"}
OVERSEAS_TERMS = ("国外", "境外", "海外", "外销", "国际")


def _security_code(code: str) -> str:
    return ("SH" if code.startswith(("6", "9")) else "BJ" if code.startswith(("4", "8")) else "SZ") + code


def fetch_business_data(code: str, timeout: float = 15) -> pd.DataFrame:
    url = "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax"
    response = requests.get(url, params={"code": _security_code(code)}, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    frame = pd.DataFrame(response.json().get("zygcfx", []))
    if frame.empty:
        return frame
    keep = ["REPORT_DATE", "MAINOP_TYPE", "ITEM_NAME", "MAIN_BUSINESS_INCOME", "MBI_RATIO", "GROSS_RPOFIT_RATIO"]
    frame = frame[[column for column in keep if column in frame.columns]].copy()
    frame["REPORT_DATE"] = pd.to_datetime(frame["REPORT_DATE"], errors="coerce")
    for column in ("MAIN_BUSINESS_INCOME", "MBI_RATIO", "GROSS_RPOFIT_RATIO"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def build_structured(frame: pd.DataFrame) -> dict:
    if frame.empty or frame["REPORT_DATE"].dropna().empty:
        return {}
    latest_date = frame["REPORT_DATE"].max()
    latest = frame[frame["REPORT_DATE"].eq(latest_date)].copy()
    latest = latest.sort_values("MBI_RATIO", ascending=False)
    business_rows = latest[latest["MAINOP_TYPE"].isin(("1", "2"))]
    business_items = business_rows["ITEM_NAME"].dropna().astype(str).head(20).tolist()
    breakdown: list[dict] = []
    for _, row in business_rows.head(30).iterrows():
        ratio = row.get("MBI_RATIO")
        margin = row.get("GROSS_RPOFIT_RATIO")
        breakdown.append({
            "category": TYPE_NAMES.get(str(row.get("MAINOP_TYPE")), str(row.get("MAINOP_TYPE"))),
            "item": str(row.get("ITEM_NAME") or ""),
            "revenue_ratio": None if pd.isna(ratio) else round(float(ratio), 6),
            "gross_margin": None if pd.isna(margin) else round(float(margin), 6),
        })
    region_rows = latest[latest["MAINOP_TYPE"].eq("3")]
    result = {
        "business_report_date": latest_date.strftime("%Y-%m-%d"),
        "business_items": business_items,
        "business_breakdown": breakdown,
        "main_business": "、".join(business_items[:8]),
    }
    if not region_rows.empty:
        valid_regions = region_rows[region_rows["MBI_RATIO"].notna()]
        if not valid_regions.empty:
            overseas_rows = valid_regions[valid_regions["ITEM_NAME"].astype(str).map(lambda value: any(term in value for term in OVERSEAS_TERMS))]
            result["overseas_revenue_ratio"] = round(float(overseas_rows["MBI_RATIO"].sum() * 100), 4)
    return result


def build_report(code: str, name: str, frame: pd.DataFrame) -> str:
    structured = build_structured(frame)
    lines = [
        f"# 主营构成报告：{name or code}（{code}）",
        "",
        f"> 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}  |  数据源：EastMoney/F10",
        "",
        f"<!-- moda_business: {json.dumps(structured, ensure_ascii=False)} -->",
        "",
    ]
    if frame.empty:
        lines += ["需人工确认：未取得主营构成数据。", ""]
    else:
        latest_date = frame["REPORT_DATE"].max()
        latest = frame[frame["REPORT_DATE"].eq(latest_date)].sort_values(["MAINOP_TYPE", "MBI_RATIO"], ascending=[True, False])
        for type_code, title in TYPE_NAMES.items():
            rows = latest[latest["MAINOP_TYPE"].eq(type_code)]
            lines += [f"## {title}", ""]
            if rows.empty:
                lines += ["需人工确认：无数据。", ""]
                continue
            lines += ["| 项目 | 收入 | 收入占比 | 毛利率 |", "|---|---:|---:|---:|"]
            for _, row in rows.head(12).iterrows():
                income = row.get("MAIN_BUSINESS_INCOME")
                ratio = row.get("MBI_RATIO")
                margin = row.get("GROSS_RPOFIT_RATIO")
                lines.append(
                    f"| {str(row.get('ITEM_NAME', '')).replace('|', '/')} | "
                    f"{income:,.0f} | {ratio:.2%} | {margin:.2%} |"
                )
            lines.append("")
    lines += ["## 免责声明", "", "本报告基于公开主营构成数据，仅供研究参考，不构成投资建议。"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect structured business composition")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    code = args.stock.strip()
    if len(code) != 6 or not code.isdigit():
        parser.error("--stock must be a 6-digit A-share code")
    frame = fetch_business_data(code)
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_BASE / f"{code}.md"
    path.write_text(build_report(code, args.name or code, frame), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
