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
from tools.data_call import dataframe_empty, run_fallback_chain
OUTPUT_BASE = ROOT / "knowledge" / "research" / "business_data"
TYPE_NAMES = {"1": "按行业分类", "2": "按产品分类", "3": "按地区分类"}
OVERSEAS_TERMS = ("国外", "境外", "海外", "外销", "国际")


def _security_code(code: str) -> str:
    return ("SH" if code.startswith(("6", "9")) else "BJ" if code.startswith(("4", "8")) else "SZ") + code


def _normalize_business_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    aliases = {
        "REPORT_DATE": "REPORT_DATE", "报告期": "REPORT_DATE", "报告日期": "REPORT_DATE",
        "MAINOP_TYPE": "MAINOP_TYPE", "分类类型": "MAINOP_TYPE", "分类": "MAINOP_TYPE",
        "ITEM_NAME": "ITEM_NAME", "项目": "ITEM_NAME", "主营构成": "ITEM_NAME", "主营业务": "ITEM_NAME",
        "MAIN_BUSINESS_INCOME": "MAIN_BUSINESS_INCOME", "主营收入": "MAIN_BUSINESS_INCOME", "主营业务收入": "MAIN_BUSINESS_INCOME",
        "MBI_RATIO": "MBI_RATIO", "收入比例": "MBI_RATIO", "收入占比": "MBI_RATIO",
        "GROSS_RPOFIT_RATIO": "GROSS_RPOFIT_RATIO", "毛利率": "GROSS_RPOFIT_RATIO", "主营利润率": "GROSS_RPOFIT_RATIO",
    }
    renamed = frame.rename(columns={key: value for key, value in aliases.items() if key in frame.columns}).copy()
    if "MAINOP_TYPE" in renamed.columns:
        type_map = {"按行业分类": "1", "按产品分类": "2", "按地区分类": "3", "行业": "1", "产品": "2", "地区": "3"}
        renamed["MAINOP_TYPE"] = renamed["MAINOP_TYPE"].astype(str).map(lambda value: type_map.get(value, value))
    for column in ("MAIN_BUSINESS_INCOME", "MBI_RATIO", "GROSS_RPOFIT_RATIO"):
        if column in renamed.columns:
            renamed[column] = pd.to_numeric(renamed[column], errors="coerce")
    if "REPORT_DATE" in renamed.columns:
        renamed["REPORT_DATE"] = pd.to_datetime(renamed["REPORT_DATE"], errors="coerce")
    wanted = ["REPORT_DATE", "MAINOP_TYPE", "ITEM_NAME", "MAIN_BUSINESS_INCOME", "MBI_RATIO", "GROSS_RPOFIT_RATIO"]
    for column in wanted:
        if column not in renamed.columns:
            renamed[column] = pd.NA
    return renamed[wanted].dropna(subset=["REPORT_DATE", "ITEM_NAME"], how="any")


def fetch_business_data(code: str, timeout: float = 15) -> pd.DataFrame:
    url = "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax"

    def eastmoney() -> pd.DataFrame:
        response = requests.get(url, params={"code": _security_code(code)}, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        return _normalize_business_frame(pd.DataFrame(response.json().get("zygcfx", [])))

    def akshare() -> pd.DataFrame:
        import akshare as ak
        return _normalize_business_frame(ak.stock_zygc_em(symbol=_security_code(code)))

    result = run_fallback_chain("主营构成", [("东方财富/F10", eastmoney), ("AKShare/stock_zygc_em", akshare)], seconds=int(timeout), empty=dataframe_empty)
    frame = result.value if isinstance(result.value, pd.DataFrame) else pd.DataFrame()
    frame.attrs["fetch_state"] = result.fetch_state
    frame.attrs["source_chain"] = result.source_chain or []
    frame.attrs["fetch_error"] = result.error or None
    return frame


def build_structured(frame: pd.DataFrame) -> dict:
    if frame.empty or frame["REPORT_DATE"].dropna().empty:
        return {
            "fetch_state": frame.attrs.get("fetch_state", "empty"),
            "source_chain": frame.attrs.get("source_chain", []),
            "fetch_error": frame.attrs.get("fetch_error"),
        }
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
        "fetch_state": frame.attrs.get("fetch_state", "ok"),
        "source_chain": frame.attrs.get("source_chain", []),
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
        f"> 采集时间：{time.strftime('%Y-%m-%d %H:%M:%S')}  |  数据源：东方财富 F10 → AKShare stock_zygc_em",
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
                def fmt(value: object, percentage: bool = False) -> str:
                    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
                    if pd.isna(number):
                        return "需人工确认"
                    return f"{float(number):.2%}" if percentage else f"{float(number):,.0f}"
                lines.append(
                    f"| {str(row.get('ITEM_NAME', '')).replace('|', '/')} | "
                    f"{fmt(income)} | {fmt(ratio, percentage=True)} | {fmt(margin, percentage=True)} |"
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
